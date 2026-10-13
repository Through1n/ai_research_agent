"""版式阅读与翻译呈现：原生坐标渲染（双栏分栏）、中英同步滚动（按比例联动）、
原位排版 PDF（内置 CJK 字体 china-s）、引用双向跳转 HTML、划词选区 JS。

对外函数签名保持不变（供 ui/page_reading.py 调用）：
    native_page_html(doc, page_num)          -> (html, pw, ph)
    bilingual_html(pairs)                    -> html
    in_place_pdf(doc, page_num, zh_text, out)-> out_path
    citation_jump_html(forward_ctx, backward_ctx) -> html

本轮修复：
  A1 双栏 PDF 文字块坐标重叠 -> detect_columns() 按 x 分布找中缝，分栏容器渲染 + max-width 限宽
  A2 同步滚动不同步          -> 按比例（scrollTop/(scrollHeight-clientHeight)）同步 + isSyncing + rAF
  A3 划词翻译选区未清空      -> SELECTION_JS：捕获即 clearSelection + hash 去重 + 长度<2 忽略
  A4 原位 PDF 中文乱码       -> pymupdf/fitz 兼容 Font("china-s") + insert_textbox + 负值回退
本轮（考生7）新增（模块三：正文引用标记 ↔ References 双向无损跳转）：
  T1 真实 References 索引 build_reference_index()（优先复用 services.writing
     .extract_references_from_pdf，失败降级 services.parsing.split_reference_entries）
  T1 双向无损跳转 citation_links_html()：正文 [n] → <a href="#ref-n">；参考文献
     条目 <div id="ref-n"> + 「↩ 回到正文」按钮循环切换第 k/m 处引用
  T2 native_page_html(..., highlight=标题) 让标题树点击后可在原生渲染里高亮定位
  T3 protect_math()/restore_math()/math_safe_translate()：翻译前把 $..$、\(..\)、
     LaTeX 命令、key=value 数值占位，翻译后原样还原（公式与数值不被翻译）

本轮（考生 S3，Round9）修复（均有实测证据，见报告）：
  D1 作者-年份引用不可点：_render_cite 增加 kind="paren" 分支——(Vaswani et al., 2017)
     里的年份数字不是文献编号，旧逐数字匹配一个都匹配不上（BERT 31/31 标记全死）；
  D2 反向循环游标分母偏小：citation_links_html 的 stats 增加 per_ref 权威计数
     （含 [a,b]/[a-b]/作者-年份 形态），页面不再用 regex 重扫漏计；
  D6 翻译干等：新增 stream_translate_to_chinese()/_stream_restore() 流式翻译
     （公式占位 + 流式还原，失败/限流文案不当译文）；
  D7 抗幻觉：build_reference_index 的 meta 增加 numbered 标记——参考文献本身不带
     [n] 编号时如实告知用户「编号为按出现顺序生成的索引号」；
  代码块保护：protect_math 先占位 ```code``` / `inline`，再占位公式数值。
"""
import importlib
import re
import time
import threading

try:  # PyMuPDF >= 1.24 推荐 import pymupdf；老版本只有 fitz。二者 API 一致
    import pymupdf as fitz
except Exception:  # pragma: no cover - 老版本兜底
    try:
        import fitz  # type: ignore  # noqa: F401
    except Exception:  # pragma: no cover
        fitz = None  # 环境缺失时所有函数在 try/except 内降级，不冒泡

# ---------------- 常量 ----------------
_MIN_COL_BLOCKS = 4          # 少于该文本块数不判定分栏
_MIN_GUTTER = 12.0           # 中缝最小宽度（pt），小于此值视为单栏
_COL_BINS = 140              # x 方向覆盖率剖面的采样桶数
_LOW_COVER_RATIO = 0.25      # 低于搜索区中位覆盖率的该比例 → 视为中缝
_CJK_FONTNAME = "china-s"    # PyMuPDF 内置简体中文（Droid Sans Fallback）

# ---------------- 翻译结果 TTL 缓存（默认路径 memoization） ----------------
# 针对划词/原位等「同一段可能被反复翻译」的场景：相同原文在 TTL 内直接命中缓存，
# 避免重复打满翻译限流。仅对默认翻译器（services.writing.translate_to_chinese，确定性 prompt）
# 生效；传入自定义 call 时不缓存，避免跨 translator 串结果。不缓存空结果 / 以「【LLM」开头的
# 降级文案，确保限流/不可用期间的伪响应不会被冻结成"译文"。
_TRANSLATE_CACHE_TTL = 600.0
_TRANSLATE_CACHE_MAX = 512
_translate_cache = {}
_translate_cache_lock = threading.Lock()


def _translate_cache_get(text):
    now = time.monotonic()
    item = _translate_cache.get(text)
    if item is not None and item[1] > now:
        return item[0]
    _translate_cache.pop(text, None)
    return None


def _translate_cache_put(text, value):
    now = time.monotonic()
    with _translate_cache_lock:
        if len(_translate_cache) >= _TRANSLATE_CACHE_MAX:
            _translate_cache.pop(next(iter(_translate_cache)), None)
        _translate_cache[text] = (value, now + _TRANSLATE_CACHE_TTL)


# =====================================================================
# A1：双栏检测 + 分栏渲染
# =====================================================================
def _text_blocks_xy(page):
    """page.get_text('blocks') -> [(x0,y0,x1,y1)]，只保留文本块(type==0)且带内容。"""
    out = []
    try:
        raw = page.get_text("blocks") or []
    except Exception:
        return out
    for b in raw:
        try:
            if len(b) >= 7 and int(b[6]) != 0:      # 0=文本块，1=图片块
                continue
            txt = str(b[4] or "") if len(b) > 4 else ""
            if not txt.strip():
                continue
            x0, y0, x1, y1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
            if x1 <= x0 or y1 <= y0:
                continue
            out.append((x0, y0, x1, y1))
        except Exception:
            continue
    return out


def detect_columns(page):
    """双栏判定：在页面中段做 x 方向「覆盖率剖面」，找最宽的低覆盖带（中缝）。

    相比“必须有一段完全空白”的朴素做法，覆盖率剖面能容忍通栏标题 / 通栏图注 /
    超宽行内公式：它们只在少数 y 上贡献覆盖，中缝仍是明显的低谷。
    判定条件：中缝宽度 >= _MIN_GUTTER 且中缝两侧各有 >= 2 个文本块。

    返回 dict:
        n_cols : 1 或 2
        gap    : (gap_x0, gap_x1) 或 None
        bounds : [(x0,x1), ...] 各栏的横向边界
        pw     : 页宽
        n_blocks / left / right : 调试信息
    """
    try:
        pw = float(page.rect.width)
    except Exception:
        pw = 595.0
    blocks = _text_blocks_xy(page)
    res = {"n_cols": 1, "gap": None, "bounds": [(0.0, pw)], "pw": pw,
           "n_blocks": len(blocks), "left": 0, "right": 0}
    if len(blocks) < _MIN_COL_BLOCKS or pw <= 0:
        return res
    bin_w = pw / float(_COL_BINS)
    if bin_w <= 0:
        return res
    cov = [0.0] * _COL_BINS
    for x0, y0, x1, y1 in blocks:
        h = max(0.0, y1 - y0)
        i0 = max(0, int(x0 / bin_w))
        i1 = min(_COL_BINS - 1, int(x1 / bin_w))
        for i in range(i0, i1 + 1):
            cov[i] += h
    s = max(0, int(pw * 0.28 / bin_w))
    e = min(_COL_BINS - 1, int(pw * 0.72 / bin_w))
    if e <= s:
        return res
    region = sorted(cov[s:e + 1])
    med = region[len(region) // 2]
    if med <= 0:
        return res
    thr = med * _LOW_COVER_RATIO
    best_n, best_seg = 0, None
    i = s
    while i <= e:
        if cov[i] <= thr:
            j = i
            while j + 1 <= e and cov[j + 1] <= thr:
                j += 1
            if (j - i + 1) > best_n:
                best_n, best_seg = (j - i + 1), (i, j)
            i = j + 1
        else:
            i += 1
    if not best_seg or best_n * bin_w < _MIN_GUTTER:
        return res
    g0 = best_seg[0] * bin_w
    g1 = (best_seg[1] + 1) * bin_w
    left = [b for b in blocks if b[2] <= g0 + 1.0]
    right = [b for b in blocks if b[0] >= g1 - 1.0]
    res["left"], res["right"] = len(left), len(right)
    if len(left) >= 2 and len(right) >= 2:
        res["n_cols"] = 2
        res["gap"] = (g0, g1)
        res["bounds"] = [(min(b[0] for b in left), g0), (g1, max(b[2] for b in right))]
    return res


def _dict_block_bbox(block):
    """从 dict block 计算 (x0,y0,x1,y1)；取不到返回 None。"""
    xs0, ys0, xs1, ys1 = [], [], [], []
    for line in block.get("lines", []) or []:
        for span in line.get("spans", []) or []:
            bb = span.get("bbox") or [0, 0, 0, 0]
            try:
                xs0.append(float(bb[0])); ys0.append(float(bb[1]))
                xs1.append(float(bb[2])); ys1.append(float(bb[3]))
            except Exception:
                continue
    if not xs0:
        return None
    return min(xs0), min(ys0), max(xs1), max(ys1)


def _assign_column(x0, x1, info):
    """判断块属于哪一栏：0/1 为左右栏，-1 表示跨栏（整页宽）。"""
    if info.get("n_cols") != 2:
        return 0
    bounds = info.get("bounds") or [(0.0, info.get("pw", 595.0))]
    gap = info.get("gap")
    if gap and x0 < gap[0] and x1 > gap[1]:
        return -1                      # 跨过中缝：标题/通栏摘要/图注
    if len(bounds) < 2:
        return 0
    l0, l1 = bounds[0]
    r0, r1 = bounds[1]
    # 与哪一栏重叠更多就归哪一栏
    ov_l = max(0.0, min(x1, l1) - max(x0, l0))
    ov_r = max(0.0, min(x1, r1) - max(x0, r0))
    return 0 if ov_l >= ov_r else 1


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _block_html(block, rel_x, y, max_w, highlight=None):
    """把一个 dict block 渲染成绝对定位的 .blk（内部行用常规流，避免块内重叠）。

    T2：highlight 为标题文本时，命中的 span 加 class="mk"（黄色高亮），
    供「标题树点击 → 原生渲染定位并高亮该标题」使用；highlight 为空则行为不变。
    """
    hl = str(highlight or "").strip().lower()
    lines_html = []
    for line in block.get("lines", []) or []:
        spans = [s for s in (line.get("spans", []) or []) if str(s.get("text", "")).strip()]
        if not spans:
            continue
        fs = 10.0
        for s in spans:
            try:
                fs = max(fs, float(s.get("size", 10) or 10))
            except Exception:
                pass
        ly0, ly1 = None, None
        for s in spans:
            bb = s.get("bbox") or [0, 0, 0, 0]
            try:
                a, b = float(bb[1]), float(bb[3])
            except Exception:
                continue
            ly0 = a if ly0 is None else min(ly0, a)
            ly1 = b if ly1 is None else max(ly1, b)
        lh = max(fs * 1.28, (ly1 - ly0) if (ly0 is not None and ly1 is not None) else 0.0)
        inner = []
        for s in spans:
            color = s.get("color", 0) or 0
            rgb = "#%06x" % (int(color) & 0xFFFFFF) if color else "#000"
            try:
                sfs = float(s.get("size", fs) or fs)
            except Exception:
                sfs = fs
            flags = int(s.get("flags", 0) or 0)
            style = ""
            if flags & 16:      # 粗体
                style += "font-weight:700;"
            if flags & 1:       # 斜体
                style += "font-style:italic;"
            s_text = str(s.get("text", ""))
            mk = " mk" if (hl and hl in s_text.lower()) else ""
            inner.append(
                '<span class="sp%s" style="font-size:%.1fpx;color:%s;%s">%s</span>'
                % (mk, sfs, rgb, style, _esc(s_text)))
        lines_html.append('<div class="ln" style="font-size:%.1fpx;line-height:%.2fpx;">%s</div>'
                          % (fs, lh, "".join(inner)))
    if not lines_html:
        return None
    return ('<div class="blk" style="left:%.1fpx;top:%.1fpx;max-width:%.1fpx;">%s</div>'
            % (rel_x, y, max(12.0, max_w), "".join(lines_html)))


# ---------- 共用 JS：iframe 高度自适应（Streamlit components.html） ----------
_HEIGHT_JS = """
<script>
(function(){
  function post(h){
    try{
      if (window.parent && window.parent !== window){
        window.parent.postMessage({isStreamlitMessage:true, type:'streamlit:setFrameHeight', height:h}, '*');
      }
    }catch(e){}
  }
  function report(){
    try{
      var d = document.documentElement, b = document.body;
      var h = Math.max(d ? d.scrollHeight : 0, b ? b.scrollHeight : 0,
                       d ? d.offsetHeight : 0, b ? b.offsetHeight : 0);
      post(Math.min(Math.max(h + 24, 180), 6000));
    }catch(e){}
  }
  try{
    window.addEventListener('load', function(){ report(); setTimeout(report, 260); });
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      setTimeout(report, 60);
    }
    if (window.ResizeObserver){
      var ro = new ResizeObserver(function(){ report(); });
      ro.observe(document.body);
    }
  }catch(e){}
})();
</script>
"""


# ---------- 共用 JS：A3 划词选区（捕获即清空 + hash 去重 + 长度<2 忽略） ----------
SELECTION_JS = """
<style>
.ai4s-panel{position:fixed;right:14px;bottom:14px;max-width:330px;z-index:99;
background:#ffffff;border:1px solid #d6e0ef;border-radius:10px;padding:10px 12px;
box-shadow:0 4px 16px rgba(23,58,99,.16);font-family:'Microsoft YaHei',sans-serif;display:none;}
.ai4s-panel .ai4s-t{font-size:12.5px;font-weight:700;color:#173a63;margin-bottom:6px;}
.ai4s-panel .ai4s-x{font-size:12px;color:#333;line-height:1.65;max-height:96px;overflow:auto;
background:#f7fafd;border:1px solid #e6edf7;border-radius:6px;padding:6px 8px;white-space:pre-wrap;}
.ai4s-panel .ai4s-b{margin-top:8px;display:flex;gap:8px;}
.ai4s-panel .ai4s-b button{border:1px solid #3b6fb6;background:#eef4fc;color:#173a63;
border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer;}
.ai4s-panel .ai4s-b button:hover{background:#dbe8fa;}
.ai4s-panel .ai4s-h{margin-top:6px;font-size:11px;color:#7a869a;}
</style>
<script>
(function(){
  var MIN_LEN = 2, DEDUP_MS = 2500, AUTO_HIDE_MS = 15000;
  var lastHash = null, lastAt = 0, hideTimer = 0;
  function h32(s){
    var h = 5381;
    for (var i = 0; i < s.length; i++){ h = ((h << 5) + h + s.charCodeAt(i)) >>> 0; }
    return 'h' + h;
  }
  function esc(s){
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  function getSel(){
    try{
      return (window.getSelection && window.getSelection()) ||
             (document.getSelection && document.getSelection()) || null;
    }catch(e){ return null; }
  }
  /* 关键：选区清空（removeAllRanges 优先，empty() 兜底，blur 防止输入控件残留） */
  function clearSelection(){
    var s = getSel();
    if (s){
      try{ if (s.removeAllRanges) s.removeAllRanges(); }catch(e){}
      try{ if (s.empty) s.empty(); }catch(e){}
    }
    try{ var d = document.getSelection && document.getSelection(); if (d && d.empty) d.empty(); }catch(e){}
    try{ var ae = document.activeElement; if (ae && ae.blur && ae.tagName !== 'INPUT' && ae.tagName !== 'TEXTAREA') ae.blur(); }catch(e){}
  }
  function copyText(txt){
    var ok = false;
    try{
      if (navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(txt); ok = true;
      }
    }catch(e){}
    if (!ok){
      try{
        var ta = document.createElement('textarea');
        ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta); ok = true;
      }catch(e){}
    }
    return ok;
  }
  function panel(){
    var p = document.getElementById('ai4s-sel-panel');
    if (p) return p;
    try{
      p = document.createElement('div'); p.id = 'ai4s-sel-panel'; p.className = 'ai4s-panel';
      document.body.appendChild(p);
    }catch(e){ p = null; }
    return p;
  }
  function show(txt){
    var p = panel(); if (!p) return;
    p.innerHTML = '<div class="ai4s-t">已捕获 ' + txt.length + ' 字（选区已清空）</div>' +
      '<div class="ai4s-x">' + esc(txt) + '</div>' +
      '<div class="ai4s-b"><button id="ai4s-copy">复制</button>' +
      '<button id="ai4s-close">关闭</button></div>' +
      '<div class="ai4s-h">可到下方「选中/粘贴原文片段」按 Ctrl+V 粘贴后翻译</div>';
    p.style.display = 'block';
    var cp = document.getElementById('ai4s-copy');
    if (cp) cp.onclick = function(){ copyText(txt); cp.textContent = '已复制'; };
    var cl = document.getElementById('ai4s-close');
    if (cl) cl.onclick = function(){ p.style.display = 'none'; };
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function(){ try{ p.style.display = 'none'; }catch(e){} }, AUTO_HIDE_MS);
    copyText(txt);
  }
  function publish(txt){
    /* 尽力把选中文本回传给宿主页面（Streamlit 无自定义组件通道时静默失败，不影响主流程） */
    try{
      if (window.parent && window.parent !== window){
        window.parent.postMessage({type:'ai4s:selection', text:txt}, '*');
      }
    }catch(e){}
    try{
      if (window.parent && window.parent.document){
        var el = window.parent.document.getElementById('ai4s_sel_sink');
        if (el){ el.setAttribute('data-v', txt); el.textContent = txt; }
      }
    }catch(e){}
  }
  function onSelect(){
    var s = getSel(); if (!s) return;
    var txt = '';
    try{ txt = String(s.toString() || ''); }catch(e){ return; }
    txt = txt.replace(/\\s+/g, ' ').trim();
    if (txt.length < MIN_LEN) return;                 /* 选区过短（误触/单击）直接忽略 */
    var h = h32(txt), now = (new Date()).getTime();
    if (h === lastHash && (now - lastAt) < DEDUP_MS) return;  /* 同一段文本不重复触发 */
    lastHash = h; lastAt = now;
    try{ show(txt); }catch(e){}
    try{ publish(txt); }catch(e){}
    clearSelection();                                  /* 捕获后立即清空，避免残留重复触发 */
  }
  try{
    document.addEventListener('mouseup', onSelect);
    document.addEventListener('touchend', function(){ setTimeout(onSelect, 30); });
    document.addEventListener('dblclick', function(){ setTimeout(onSelect, 30); });
    window.addEventListener('scroll', function(){
      var s = getSel(); if (s && String(s.toString() || '').trim()) { try{ clearSelection(); }catch(e){} }
    }, true);
  }catch(e){}
  window.ai4sClearSelection = clearSelection;
})();
</script>
"""


def native_page_html(doc, page_num, highlight=None):
    """文本块+坐标→HTML：双栏 PDF 按栏分组进独立容器，块宽限制在栏宽内（A1）。

    返回 (html, pw, ph) —— 与旧签名一致；highlight 为 T2 新增的可选标题高亮词。
    """
    page = doc[page_num - 1]
    pw, ph = float(page.rect.width), float(page.rect.height)
    info = detect_columns(page)
    n_cols = info["n_cols"]
    bounds = info["bounds"]
    try:
        raw = page.get_text("dict")["blocks"] or []
    except Exception:
        raw = []
    layers = {0: [], 1: [], -1: []}
    for block in raw:
        try:
            if block.get("type") != 0:
                continue
            bb = _dict_block_bbox(block)
            if bb is None:
                continue
            x0, y0, x1, _y1 = bb
            ci = _assign_column(x0, x1, info)
            if ci == -1:
                rel_x, max_w = x0, pw - x0 - 6.0
                css_layer = -1
            else:
                c0, c1 = bounds[ci] if ci < len(bounds) else (0.0, pw)
                rel_x = x0 - c0
                max_w = (c1 - c0) - rel_x - 2.0
                css_layer = ci
                if max_w < 12.0:                     # 块起点已越出栏右边界：夹回栏内
                    rel_x = 0.0
                    max_w = max(12.0, c1 - c0 - 2.0)
            html = _block_html(block, rel_x, y0, max_w, highlight=highlight)
            if html:
                layers[css_layer].append(html)
        except Exception:
            continue

    def _col_div(idx, c0, c1):
        body = "".join(layers.get(idx, []))
        return ('<div class="col" id="col%d" style="left:%.1fpx;width:%.1fpx;height:%.1fpx;">%s</div>'
                % (idx, c0, max(20.0, c1 - c0), ph, body))

    if n_cols == 2:
        cols_html = (_col_div(0, bounds[0][0], bounds[0][1]) +
                     _col_div(1, bounds[1][0], bounds[1][1]))
        g0, g1 = info["gap"]
        gutter = ('<div class="gutter" style="left:%.1fpx;width:%.1fpx;height:%.1fpx;"></div>'
                  % (g0, max(2.0, g1 - g0), ph))
        tip = ("📑 检测到双栏版式（左 %d 块 / 右 %d 块，中缝 %.0f–%.0fpt），已按栏分块渲染"
               % (info["left"], info["right"], g0, g1))
    else:
        cols_html = _col_div(0, 0.0, pw)
        gutter = ""
        tip = "📄 单栏版式，按原生坐标渲染"

    full_html = ('<div class="full" style="height:%.1fpx;">%s</div>' % (ph, "".join(layers.get(-1, []))))

    # 渲染顺序：中缝(底层) → 通栏块(标题/图注) → 左右栏正文(顶层)，DOM 顺序也符合阅读顺序
    body_layers = gutter + full_html + cols_html

    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;}}
html,body{{margin:0;padding:0;background:#f5f7fa;}}
#tip{{position:fixed;top:6px;left:8px;right:8px;z-index:8;background:rgba(43,87,154,.92);color:#fff;
font:12px/1.5 'Microsoft YaHei',sans-serif;padding:4px 10px;border-radius:12px;}}
#page{{position:relative;width:{pw:.1f}px;height:{ph:.1f}px;background:#fff;margin:40px auto 18px;
box-shadow:0 2px 10px rgba(0,0,0,.15);}}
.col{{position:absolute;top:0;overflow:visible;}}
.col#col1{{border-left:0;}}
.full{{position:absolute;left:0;top:0;width:100%;}}
.gutter{{position:absolute;top:0;background:repeating-linear-gradient(180deg,#eef2f7 0 10px,#f7f9fc 10px 20px);
border-left:1px dashed #dbe3ee;border-right:1px dashed #dbe3ee;}}
.blk{{position:absolute;font-family:'Times New Roman',Times,serif;color:#1a1a1a;
white-space:normal;overflow-wrap:break-word;word-break:break-word;user-select:text;}}
.ln{{white-space:pre-wrap;}}
.blk:hover{{background:#fff3b0;}}
.blk span{{user-select:text;}}
/* T2：标题树点击后，命中的标题文字高亮（3 秒后由 JS 淡出） */
.mk{{background:#ffe08a;border-radius:2px;box-shadow:inset 0 -2px 0 #f0ad2e;}}
@keyframes ai4smkf{{0%{{background:#ffcf4d;}}100%{{background:#ffe08a;}}}}
.mk{{animation:ai4smkf 3s ease-out 1;}}
</style></head><body><div id="tip">{tip}</div>
<div id="page">{body}</div>{sel}{hgt}</body></html>""".format(
        pw=pw, ph=ph, tip=_esc(tip), body=body_layers,
        sel=SELECTION_JS, hgt=_HEIGHT_JS)
    return html, pw, ph


# =====================================================================
# A2：中英同步滚动（按比例 + isSyncing + rAF 节流 + data-index 高亮）
# =====================================================================
def bilingual_html(pairs):
    """中英对比：两栏独立滚动，按 scrollTop 比例双向同步，段落用 data-index 联动高亮。"""
    import json
    left, right = [], []
    for i, (_en, _zh) in enumerate(pairs):
        en = _esc(_en)
        zh = _esc(_zh) if _zh else "<em>（待翻译）</em>"
        left.append('<p data-index="%d" data-i="%d">%s</p>' % (i, i, en))
        right.append('<p data-index="%d" data-i="%d">%s</p>' % (i, i, zh))
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;}} html,body{{margin:0;height:100%;
font-family:'Microsoft YaHei','Segoe UI',sans-serif;background:#f5f7fa;}}
#wrap{{display:flex;height:100vh;}}
.pane{{flex:1 1 50%;overflow-y:auto;padding:26px 22px 60px;}}
#l{{background:#fbfcfe;border-right:2px solid #e3e8ef;}}
#r{{background:#f4fbf6;}}
p{{font-size:13.5px;line-height:1.85;margin:0 0 12px;padding:8px 10px;border-radius:6px;
border-bottom:1px solid #eef1f5;color:#26313f;cursor:default;}}
p.active{{background:#fff2bf;box-shadow:inset 3px 0 #f0ad2e;}}
#bar{{position:fixed;top:8px;left:50%;transform:translateX(-50%);background:rgba(30,60,110,.9);
color:#fff;padding:5px 14px;border-radius:14px;font-size:12px;z-index:9;}}
</style></head><body><div id="bar">中英同步滚动（按比例）· 悬停/点击段落联动</div>
<div id="wrap"><div class="pane" id="l">{L}</div><div class="pane" id="r">{R}</div></div>
<script>
(function(){{
  var l = document.getElementById('l'), r = document.getElementById('r');
  if (!l || !r) return;
  var isSyncing = false, pendingRAF = 0, releaseTimer = 0;

  /* 可滚动距离；scrollHeight<=clientHeight 时为 0，避免除零 */
  function maxScroll(el){{
    var m = el.scrollHeight - el.clientHeight;
    return (m > 0) ? m : 0;
  }}
  function ratio(el){{
    var m = maxScroll(el);
    return m <= 0 ? 0 : (el.scrollTop / m);
  }}
  function release(){{ if (releaseTimer) clearTimeout(releaseTimer); releaseTimer = 0; isSyncing = false; }}
  /* source -> target：按同一比例设置 scrollTop */
  function applyRatio(src, dst){{
    var m = maxScroll(dst);
    if (m <= 0) return;
    dst.scrollTop = ratio(src) * m;
  }}
  function bindScroll(src, dst){{
    src.addEventListener('scroll', function(){{
      if (isSyncing) return;          /* 正在同步：忽略被同步方回调，防互相触发死循环 */
      if (pendingRAF) return;         /* rAF 节流：一帧最多同步一次 */
      pendingRAF = window.requestAnimationFrame(function(){{
        pendingRAF = 0;
        isSyncing = true;
        try{{ applyRatio(src, dst); }}
        finally{{
          /* 下一个渲染帧再释放：程序化 scroll 事件已派发完毕 */
          window.requestAnimationFrame(release);
          releaseTimer = window.setTimeout(release, 200);   /* 后台标签页 rAF 不触发时的兜底 */
        }}
      }});
    }}, {{passive:true}});
  }}
  bindScroll(l, r); bindScroll(r, l);

  /* 段落联动高亮：只用 data-index 查找，杜绝文本重复时跳错段落 */
  function highlight(idx){{
    [l, r].forEach(function(pane){{
      var ps = pane.querySelectorAll('p');
      for (var k = 0; k < ps.length; k++){{
        if (ps[k].getAttribute('data-index') === idx) ps[k].classList.add('active');
        else ps[k].classList.remove('active');
      }}
    }});
  }}
  function bindPane(a, b){{
    a.querySelectorAll('p').forEach(function(p){{
      var on = function(){{
        var idx = p.getAttribute('data-index');
        if (idx === null || idx === undefined) return;
        highlight(idx);
        var t = b.querySelector('p[data-index="' + idx + '"]');
        if (t) t.scrollIntoView({{block:'nearest'}});
      }};
      p.addEventListener('mouseenter', on);
      p.addEventListener('click', on);
    }});
  }}
  bindPane(l, r); bindPane(r, l);

  /* 窗口尺寸变化后重算一次比例，避免 resize 后错位 */
  window.addEventListener('resize', function(){{
    if (isSyncing) return;
    isSyncing = true;
    try{{ applyRatio(l, r); }} finally{{ releaseTimer = window.setTimeout(release, 60); }}
  }});
  window.ai4sSyncState = function(){{ return {{isSyncing: isSyncing, lr: ratio(l), rr: ratio(r)}}; }};
}})();
</script>{sel}</body></html>""".format(L="".join(left), R="".join(right), sel=SELECTION_JS)


# =====================================================================
# A4：原位 PDF（内置 CJK 字体 china-s）
# =====================================================================
def _cjk_font():
    """兼容获取 PyMuPDF 内置简体中文字体。

    新版：pymupdf.Font('china-s')；老版：fitz.Font('china-s')。
    返回 (fontname, font_obj|None)；font_obj 仅用于测宽，可为 None。
    """
    for mod_name in ("pymupdf", "fitz"):
        try:
            mod = importlib.import_module(mod_name)
            f = mod.Font(_CJK_FONTNAME)          # 内置字体，无需外部字体文件
            if f is not None:
                return _CJK_FONTNAME, f
        except Exception:
            continue
    return _CJK_FONTNAME, None


def _fit_chars(font, text, fs, w, h):
    """估算 w×h 的文本框以 fs 号 CJK 字能放下多少字符（用于截断回退）。"""
    n = len(text)
    if n <= 0:
        return 0
    try:
        total = float(font.text_length(text, fs))
    except Exception:
        total = n * fs * 1.0
    max_lines = max(1, int(h // (fs * 1.34)))
    capacity = max_lines * max(1.0, float(w))
    if total <= capacity:
        return n
    ratio = capacity / total if total > 0 else 0.0
    return max(1, int(n * max(0.0, ratio) * 0.97))


def _insert_lines_fallback(page, rect, text, fontname, fontsize=7.5):
    """最后兜底：逐行 insert_text（内置 china-s），超出框底即停。"""
    try:
        max_chars = max(4, int(rect.width / (fontsize * 1.02)))
    except Exception:
        max_chars = 40
    lines = []
    for para in str(text or "").split("\n"):
        p = para.strip()
        while len(p) > max_chars:
            lines.append(p[:max_chars]); p = p[max_chars:]
        if p:
            lines.append(p)
    y = rect.y0
    step = fontsize * 1.32
    for line in lines:
        if y + step > rect.y1:
            break
        try:
            page.insert_text((rect.x0, y), line, fontsize=fontsize,
                             fontname=fontname, fontfile=None, color=(0, 0, 0))
        except TypeError:
            try:
                page.insert_text((rect.x0, y), line, fontsize=fontsize,
                                 fontname=fontname, color=(0, 0, 0))
            except Exception:
                pass
        except Exception:
            pass
        y += step
    return True


def in_place_pdf(doc, page_num, zh_text, out_path):
    """原页做底，底部区域叠加中文译文（内置 china-s 字体，不乱码）。

    insert_textbox 返回负值=放不下：依次 ①缩小字号重试 ②按可容纳字数截断并追加省略号
    ③逐行 insert_text 兜底。全部写操作都在 try/except 内，异常不冒泡。
    """
    src = doc[page_num - 1]
    pw, ph = float(src.rect.width), float(src.rect.height)
    if fitz is None:
        raise RuntimeError("PyMuPDF 不可用，无法生成原位 PDF。")
    fontname, font = _cjk_font()
    nd = fitz.open()
    try:
        np = nd.new_page(width=pw, height=ph)
        try:
            np.show_pdf_page(np.rect, doc, page_num - 1)
        except Exception:
            pass
        top = ph * 0.74
        try:
            np.draw_rect(fitz.Rect(18, top + 2, pw - 18, ph - 14),
                         color=(1, 1, 1), fill=(1, 1, 1), width=0.5)
            np.draw_line(fitz.Point(18, top + 2), fitz.Point(pw - 18, top + 2),
                         color=(0.72, 0.76, 0.82), width=0.6)
        except Exception:
            pass
        box = fitz.Rect(24, top + 10, pw - 24, ph - 12)
        text = str(zh_text or "").strip() or "（本页无译文）"
        done = False
        # ①/②：字号从大到小；放不下就按可容纳字数截断 + 省略号
        for fs in (9.0, 8.0, 7.0, 6.0, 5.0):
            cand = text
            try:
                keep = _fit_chars(font, text, fs, box.width, box.height)
                cand = text[:keep]
                if keep < len(text):
                    cand = cand.rstrip() + "…"
                rest = np.insert_textbox(box, cand, fontname=fontname, fontfile=None,
                                         fontsize=fs, color=(0, 0, 0), align=0)
                if isinstance(rest, (int, float)) and rest >= 0:
                    done = True
                    break
            except TypeError:      # 老版本 insert_textbox 不接受 fontfile/align
                try:
                    rest = np.insert_textbox(box, cand, fontname=fontname,
                                             fontsize=fs, color=(0, 0, 0))
                    if isinstance(rest, (int, float)) and rest >= 0:
                        done = True
                        break
                except Exception:
                    continue
            except Exception:
                continue
        # ③：逐行 insert_text 兜底
        if not done:
            try:
                _insert_lines_fallback(np, box, text, fontname)
            except Exception:
                pass
        try:
            nd.save(out_path, garbage=3, deflate=True)
        except TypeError:
            nd.save(out_path)
    finally:
        try:
            nd.close()
        except Exception:
            pass
    return out_path


# =====================================================================
# 引用双向跳转（签名不变）
# =====================================================================
def citation_jump_html(forward_ctx, backward_ctx):
    fw = _esc(forward_ctx)
    bw = _esc(backward_ctx)
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{font-family:"Microsoft YaHei",sans-serif;padding:16px;background:#f7fafd;}}
.s{{margin-bottom:14px;padding:14px 16px;border-radius:8px;font-size:13.5px;line-height:1.8;color:#26313f;}}
.f{{background:#eef5ff;border-left:4px solid #4A90E2;}}
.b{{background:#eefbf1;border-left:4px solid #2e9d57;}}
.btn{{display:inline-block;margin-top:10px;padding:6px 14px;background:#4A90E2;color:#fff;
border-radius:6px;cursor:pointer;font-size:13px;text-decoration:none;}}
</style></head><body>
<div class="s f" id="fw"><b>📘 正文引用位置</b><div>...{fw}...</div>
<a class="btn" href="#bw">⬇ 跳转参考文献</a></div>
<div class="s b" id="bw"><b>📗 参考文献条目</b><div>{bw}</div>
<a class="btn" href="#fw">⬆ 跳回正文</a></div>
<script>
document.querySelectorAll('.btn').forEach(function(b){{b.onclick=function(e){{
e.preventDefault();
var t=document.getElementById(b.getAttribute('href').slice(1));
if(t) t.scrollIntoView({{behavior:'smooth',block:'center'}});}};}});
</script>{hgt}</body></html>""".format(fw=fw, bw=bw, hgt=_HEIGHT_JS)


# =====================================================================
# 供「划词翻译」标签页使用的可选中文本 HTML（带选区清空 JS）
# =====================================================================
def selectable_text_html(text, title="在该区域用鼠标划选要翻译的词句"):
    """把整页文本渲染为可选中段落 HTML，并挂上 A3 的选区捕获/清空 JS。"""
    paras = [p.strip() for p in str(text or "").split("\n") if p.strip()]
    body = "".join('<p style="margin:0 0 10px;padding:8px 10px;border-radius:6px;'
                   'background:#fff;border:1px solid #eef1f5;">%s</p>' % _esc(p)
                   for p in paras)
    if not body:
        body = '<p style="color:#888;">（该页无可提取文本，可能是扫描件）</p>'
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;background:#f7fafd;font-family:'Microsoft YaHei',sans-serif;}}
#tip{{position:fixed;top:6px;left:8px;right:8px;background:rgba(43,87,154,.92);color:#fff;
font:12px/1.5 'Microsoft YaHei',sans-serif;padding:4px 10px;border-radius:12px;z-index:8;}}
#box{{margin:38px 8px 12px;padding:12px 14px;background:#fff;border:1px solid #e3e8ef;
border-radius:10px;}}
#box p{{font-size:13.5px;line-height:1.85;color:#26313f;user-select:text;cursor:text;}}
#box p:hover{{background:#fffbe6;}}
</style></head><body><div id="tip">{t}</div><div id="box">{b}</div>{s}{h}</body></html>""".format(
        t=_esc(title), b=body, s=SELECTION_JS, h=_HEIGHT_JS)


# =====================================================================
# 模块三 T1：正文引用标记 ↔ References 双向无损跳转
# =====================================================================
# 支持的正文引用标记形态：[11] / [1,2] / [11, 12] / [3-7] / (Smith et al., 2020)
_CITE_NUM = r"\d{1,3}"
_BRACKET_CITE_RE = re.compile(
    r"\[(" + _CITE_NUM + r"(?:\s*(?:,|，|;)\s*" + _CITE_NUM + r")*"
    r"|" + _CITE_NUM + r"\s*[-\u2013\u2014]\s*" + _CITE_NUM + r")\]")
_PAREN_CITE_RE = re.compile(
    r"\(([A-Z][A-Za-z\-\u2019']+(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z\-\u2019']+))?"
    r"\s*,?\s*((?:19|20)\d{2})[a-z]?)\)")
_RANGE_CITE_RE = re.compile(r"^(\d{1,3})\s*[-\u2013\u2014]\s*(\d{1,3})$")
_MAX_RANGE_SPAN = 40      # [a-b] 跨度超过该值视为误匹配（页码/章节号），不建链


def _attr(s):
    """HTML 属性值转义（比 _esc 多处理引号）。"""
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def _safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ref_guess_title(text):
    """没有结构化 title 时，从条目原文里猜一个标题（取第一个长度合理的句子）。"""
    try:
        s = re.sub(r"^\s*\[\d{1,4}\]\s*", "", str(text or "")).strip()
        s = re.sub(r"^\s*\d{1,4}[.)]\s*", "", s).strip()
        for seg in re.split(r"(?<=[a-zA-Z0-9])\.\s+", s)[:4]:
            seg = seg.strip()
            if 12 <= len(seg) <= 180:
                return seg
        return s[:120]
    except Exception:
        return str(text or "")[:120]


def build_reference_index(doc, max_entries=600):
    """从 PDF 真实抽取 References 并建立编号索引（不猜测）。

    优先复用 services.writing.extract_references_from_pdf（考生4，字段级解析）；
    失败时降级 services.parsing.split_reference_entries（考生1，文本切分）。

    返回 (refs, meta)：
        refs = [(num, text, title), ...]（按编号升序，编号去重）
        meta = {"source": str, "status": "ok"|"fallback"|"empty", "message": str}
    """
    refs = []
    meta = {"source": "", "status": "empty", "message": "未抽取到参考文献条目"}
    try:
        from services import writing
        items = writing.extract_references_from_pdf(doc) or []
        for it in items:
            if isinstance(it, dict):
                num = _safe_int(it.get("id"), 0)
                title = str(it.get("title") or "").strip()
                raw = str(it.get("raw") or "").strip()
                if not raw:
                    raw = " ".join(str(x) for x in (it.get("authors"), title,
                                                    it.get("venue"), it.get("year")) if x).strip()
                refs.append((num if num > 0 else len(refs) + 1, raw, title))
            else:
                refs.append((len(refs) + 1, str(it), ""))
        if refs:
            meta = {"source": "services.writing.extract_references_from_pdf",
                    "status": "ok", "message": ""}
    except Exception as e:
        refs = []
        meta["message"] = "结构化抽取异常：%s: %s" % (type(e).__name__, str(e)[:120])
    if not refs:
        try:
            from services import parsing
            raw = parsing.extract_references_raw(doc) or ""
            m = re.search(r"(?im)^[ \t]*(references|bibliography|works\s+cited|"
                          r"\u53c2\u8003\u6587\u732e)[ \t]*$", raw)
            if m:
                raw = raw[m.end():]
            for i, entry in enumerate(parsing.split_reference_entries(raw) or [], 1):
                refs.append((i, str(entry), ""))
            if refs:
                meta = {"source": "services.parsing.split_reference_entries（降级）",
                        "status": "fallback", "message": "结构化抽取不可用，改用文本切分降级"}
        except Exception as e:
            refs = []
            meta["message"] = "降级抽取同样失败：%s: %s" % (type(e).__name__, str(e)[:120])

    # 编号归一：条目原文开头自带 [n] 时以真实编号为准
    norm = []
    numbered = False
    for num, text, title in refs[:int(max_entries)]:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            continue
        m = re.match(r"^\s*\[(\d{1,4})\]", text)
        if m:
            num = _safe_int(m.group(1), num)
            numbered = True
        norm.append((_safe_int(num, len(norm) + 1), text,
                     str(title or "").strip() or _ref_guess_title(text)))
    best = {}
    for num, text, title in norm:
        old = best.get(num)
        if old is None or len(text) > len(old[1]):
            best[num] = (num, text, title)
    out = sorted(best.values(), key=lambda x: x[0])
    # numbered=False：该 PDF 参考文献本身不带 [n] 编号（如 BERT 的作者-年份文献表），
    # 上面的编号是按出现顺序生成的索引号——页面据此如实告知用户，不冒充原文编号。
    meta["numbered"] = bool(numbered) and bool(out)
    if not out:
        meta["status"] = "empty"
    return out, meta


def find_reference_start_page(doc, back_scan=40):
    """从后往前找 References/Bibliography 标题所在页（1-based）；找不到返回 None。"""
    try:
        total = int(getattr(doc, "page_count", 0) or 0)
    except Exception:
        return None
    if total <= 0:
        return None
    lo = max(1, total - int(back_scan) + 1)
    for pno in range(total, lo - 1, -1):
        try:
            txt = doc[pno - 1].get_text() or ""
        except Exception:
            continue
        if re.search(r"(?im)^[ \t]*(references|bibliography|works\s+cited|"
                     r"\u53c2\u8003\u6587\u732e)[ \t]*$", txt):
            return pno
    return None


def _cite_spans(text):
    """扫描一段正文里的引用标记，返回 [(start, end, kind, payload), ...]（不重叠、按位置升序）。"""
    spans = []
    try:
        s = str(text or "")
        for m in _BRACKET_CITE_RE.finditer(s):
            spans.append((m.start(), m.end(), "bracket", m.group(1)))
        taken = [(a, b) for (a, b, _k, _p) in spans]
        for m in _PAREN_CITE_RE.finditer(s):
            if any(a <= m.start() < b for (a, b) in taken):
                continue
            spans.append((m.start(), m.end(), "paren", m.group(1)))
    except Exception:
        return []
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    return spans


def _split_cite_numbers(payload):
    """[11] → [11]；[1,2] → [1,2]；[3-7] → [3,7]（只取两端，保持原文形态不被改写）。"""
    nums = []
    try:
        for part in re.split(r"\s*(?:,|，|;)\s*", str(payload or "").strip()):
            part = part.strip()
            if not part:
                continue
            m = _RANGE_CITE_RE.match(part)
            if m:
                a, b = _safe_int(m.group(1), 0), _safe_int(m.group(2), 0)
                if a <= 0 or b <= 0:
                    continue
                if a > b:
                    a, b = b, a
                if (b - a) > _MAX_RANGE_SPAN:      # 多半是页码/章节号，不建链
                    continue
                nums.extend([a] if a == b else [a, b])
                continue
            m2 = re.match(r"^(\d{1,3})$", part)
            if m2:
                nums.append(_safe_int(m2.group(1), 0))
    except Exception:
        return []
    out, seen = [], set()
    for n in nums:
        if n > 0 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _author_year_ref(ref_items, surname, year):
    """按（姓氏, 年份）在 References 里定位编号；命中不了返回 None（宁可不建链也不错链）。"""
    try:
        yr = str(year or "").strip()
        sn = str(surname or "").strip().lower()
        if not yr:
            return None
        for num, text in ref_items:
            low = str(text).lower()
            if yr in low and (not sn or sn in low):
                return num
        return None
    except Exception:
        return None


def _render_cite(raw, nums, counter, ref_tip, stats, kind="bracket"):
    """把引用标记包成独立可点击锚点，原文字符一字不改。

    bracket（[11] / [1,2] / [3-7]）：逐个数字包（同一标记里多个编号各自成链）；
    paren（(Vaswani et al., 2017)）：整段包一个锚点——其中的年份数字不是文献编号，
    逐数字匹配会一个都匹配不上。实测缺陷：BERT 正文 31 个作者-年份标记此前
    一个都不可点击（stats 却上报 linked=31 / occurrences=0），此分支修复该问题。
    """
    html = []
    pos = 0
    try:
        if kind == "paren":
            n = nums[0] if nums else 0
            if n <= 0:
                return _esc(raw)
            k = counter.get(n, 0) + 1
            counter[n] = k
            stats["occurrences"] += 1
            tip = ref_tip.get(n, "")
            return ('<a class="cit" id="cit-%d-%d" href="#ref-%d" data-ref="%d" '
                    'data-idx="%d" title="%s" data-tip="%s">%s</a>'
                    % (n, k, n, n, k, _attr(tip), _attr(tip), _esc(raw)))
        for m in re.finditer(r"\d{1,3}", str(raw or "")):
            n = _safe_int(m.group(0), 0)
            if n not in nums:
                continue
            k = counter.get(n, 0) + 1
            counter[n] = k
            stats["occurrences"] += 1
            tip = ref_tip.get(n, "")
            html.append(_esc(raw[pos:m.start()]))
            html.append('<a class="cit" id="cit-%d-%d" href="#ref-%d" data-ref="%d" '
                        'data-idx="%d" title="%s" data-tip="%s">%s</a>'
                        % (n, k, n, n, k, _attr(tip), _attr(tip), _esc(m.group(0))))
            pos = m.end()
        html.append(_esc(raw[pos:]))
    except Exception:
        return _esc(raw)
    return "".join(html)


def _link_citations(text, ref_text, ref_tip, counter, stats):
    """把一段正文里的引用标记替换成锚点；匹配不上 References 的标记保持纯文本（不可点击）。"""
    out = []
    pos = 0
    try:
        s = str(text or "")
        for span in _cite_spans(s):
            a, b, kind, payload = span[0], span[1], span[2], span[3]
            if a < pos or b > len(s):
                continue
            out.append(_esc(s[pos:a]))
            nums = []
            if kind == "bracket":
                nums = [n for n in _split_cite_numbers(payload) if n in ref_text]
            else:
                yr = re.search(r"((?:19|20)\d{2})", str(payload))
                sn = re.match(r"\s*([A-Za-z\-\u2019']+)", str(payload))
                n = _author_year_ref(sorted(ref_text.items()),
                                     sn.group(1) if sn else "", yr.group(1) if yr else "")
                if n:
                    nums = [n]
            stats["markers"] += 1
            if nums:
                stats["linked"] += 1
                out.append(_render_cite(s[a:b], nums, counter, ref_tip, stats, kind))
            else:
                stats["unlinked"] += 1
                out.append(_esc(s[a:b]))       # 优雅降级：标记照常显示，不可点击
            pos = b
        out.append(_esc(s[pos:]))
    except Exception:
        return _esc(text)
    return re.sub(r"\s*\n\s*", " ", "".join(out))


# ---- 双向跳转 JS：锚点滚动 + 临时 class 高亮（几秒后淡出），不刷新页面、不重置状态 ----
_CITE_JUMP_JS = """
<script>
(function(){
  function el(id){ try{ return document.getElementById(id); }catch(e){ return null; } }
  var timers = {};
  function flash(node){
    if(!node) return;
    try{
      node.classList.remove('hit');
      void node.offsetWidth;
      node.classList.add('hit');
      if(timers[node.id]) clearTimeout(timers[node.id]);
      timers[node.id] = setTimeout(function(){ try{ node.classList.remove('hit'); }catch(e){} }, 2600);
    }catch(e){}
  }
  function goto(id){
    var t = el(id);
    if(!t) return false;
    try{ t.scrollIntoView({behavior:'smooth', block:'center'}); }
    catch(e){ try{ t.scrollIntoView(true); }catch(e2){} }
    flash(t);
    return true;
  }
  function closest(node, sel){
    try{ return (node && node.closest) ? node.closest(sel) : null; }catch(e){ return null; }
  }
  /* 悬停 tooltip：显示该条参考文献标题前 60 字 */
  var tip = null;
  function tipEl(){
    if(tip) return tip;
    try{
      tip = document.createElement('div');
      tip.className = 'ctip';
      document.body.appendChild(tip);
    }catch(e){ tip = null; }
    return tip;
  }
  document.addEventListener('click', function(e){
    var a = closest(e.target, '.cit');
    if(a){
      e.preventDefault();
      var n = a.getAttribute('data-ref');
      var k = a.getAttribute('data-idx');
      if(!n) return;
      goto('ref-' + n);
      /* 同步该文献「↩ 回到正文」的游标：下次点击从当前这处的下一处开始 */
      var b = document.querySelector('.back[data-ref="' + n + '"]');
      if(b && k){ b.setAttribute('data-idx', k); }
      return;
    }
    var b2 = closest(e.target, '.back');
    if(b2){
      e.preventDefault();
      var n2 = parseInt(b2.getAttribute('data-ref'), 10);
      var total = parseInt(b2.getAttribute('data-total'), 10) || 1;
      var idx = (parseInt(b2.getAttribute('data-idx'), 10) || 0) + 1;
      if(idx > total){ idx = 1; }
      b2.setAttribute('data-idx', idx);
      if(goto('cit-' + n2 + '-' + idx)){
        b2.textContent = '\\u21a9 \\u7b2c ' + idx + '/' + total + ' \\u5904\\u5f15\\u7528\\uff08\\u70b9\\u51fb\\u7ee7\\u7eed\\uff09';
      }else{
        b2.textContent = '\\u21a9 \\u65e0\\u53ef\\u7528\\u5f15\\u7528\\u4f4d\\u7f6e';
      }
      return;
    }
  }, false);
  document.addEventListener('mouseover', function(e){
    var a = closest(e.target, '.cit');
    if(!a) return;
    var t = tipEl();
    if(!t) return;
    var txt = a.getAttribute('data-tip') || '';
    if(!txt){ t.style.display='none'; return; }
    t.textContent = '\\u3010' + a.getAttribute('data-ref') + '\\u3011 ' + txt;
    t.style.display = 'block';
    try{
      var r = a.getBoundingClientRect();
      var w = 320;
      var left = Math.max(4, Math.min(r.left, (window.innerWidth || 900) - w - 8));
      t.style.left = left + 'px';
      t.style.top = (r.bottom + 6) + 'px';
    }catch(e){}
  }, false);
  document.addEventListener('mouseout', function(e){
    var a = closest(e.target, '.cit');
    if(a && tip){ try{ tip.style.display = 'none'; }catch(e){} }
  }, false);
  window.ai4sGotoRef = goto;
})();
</script>
"""

_CITE_JUMP_CSS = """
* {box-sizing:border-box;}
html,body{margin:0;background:#f5f7fa;font-family:'Microsoft YaHei','Segoe UI',sans-serif;}
#bar{position:fixed;top:6px;left:8px;right:8px;z-index:8;background:rgba(43,87,154,.92);color:#fff;
font:12px/1.5 'Microsoft YaHei',sans-serif;padding:5px 12px;border-radius:12px;}
#wrap{display:flex;gap:10px;padding:40px 8px 14px;height:720px;}
.pane{flex:1 1 50%;overflow-y:auto;background:#fff;border:1px solid #e3e8ef;border-radius:10px;
padding:10px 12px 24px;}
#refsPane{background:#fbfcfe;}
.pgn{font-size:11.5px;color:#7a869a;border-bottom:1px dashed #dde3ec;margin:12px 0 6px;padding-bottom:3px;}
.pgn:first-child{margin-top:0;}
p.bp{font-size:13.2px;line-height:1.85;color:#26313f;margin:0 0 10px;text-align:justify;user-select:text;}
a.cit{color:#2f6fd0;text-decoration:none;font-weight:700;padding:0 1px;border-radius:3px;cursor:pointer;}
a.cit:hover{background:#dbe8fa;text-decoration:underline;}
.ref{position:relative;padding:8px 10px 8px 46px;border-bottom:1px solid #eef1f5;font-size:12.6px;
line-height:1.7;color:#26313f;}
.ref .rn{position:absolute;left:8px;top:8px;color:#2f6fd0;font-weight:700;}
.ref .rt{user-select:text;}
.ref .rb{margin-top:4px;}
a.back{display:inline-block;font-size:11.5px;padding:2px 9px;border:1px solid #3b6fb6;border-radius:11px;
background:#eef4fc;color:#173a63;text-decoration:none;cursor:pointer;}
a.back:hover{background:#dbe8fa;}
.noback{font-size:11.5px;color:#98a2b3;}
@keyframes ai4sflash{0%{background:#ffd85e;}60%{background:#fff3b0;}100%{background:transparent;}}
.hit{animation:ai4sflash 2.6s ease-out 1;border-radius:4px;}
.ctip{position:fixed;z-index:99;display:none;max-width:320px;background:#173a63;color:#fff;
font-size:11.5px;line-height:1.6;padding:6px 9px;border-radius:6px;box-shadow:0 4px 14px rgba(0,0,0,.22);
pointer-events:none;}
.empty{color:#98a2b3;font-size:12.5px;padding:12px;}
"""


def citation_links_html(body_pages, refs, tip_len=60):
    """生成「正文 ↔ References」双向无损跳转视图（左：正文，右：参考文献）。

    正向：正文 [n] → <a class="cit" href="#ref-n" id="cit-n-k">，点击滚动+高亮第 n 条
    反向：第 n 条 → 「↩ 回到正文」，同一文献被多次引用时循环切换第 1/m、2/m … 处

    body_pages : [(page_no, page_text), ...]
    refs       : build_reference_index 的返回 [(num, text, title), ...]
    返回 (html, stats)
    """
    ref_text, ref_tip = {}, {}
    for item in (refs or []):
        try:
            n = _safe_int(item[0], 0)
            text = re.sub(r"\s+", " ", str(item[1] or "")).strip()
        except Exception:
            continue
        if n <= 0 or not text:
            continue
        ref_text[n] = text
        ref_tip[n] = (str(item[2] if len(item) > 2 else "" or "").strip() or text)[:int(tip_len)]

    counter = {}
    stats = {"pages": 0, "markers": 0, "linked": 0, "unlinked": 0,
             "occurrences": 0, "refs": len(ref_text), "refs_in_body": 0}

    body_parts = []
    for item in (body_pages or []):
        try:
            pno = _safe_int(item[0], 0)
            raw = str(item[1] or "")
        except Exception:
            continue
        stats["pages"] += 1
        paras = [p for p in re.split(r"\n\s*\n+", raw) if p.strip()] or [raw]
        inner = "".join('<p class="bp">%s</p>'
                        % _link_citations(p, ref_text, ref_tip, counter, stats)
                        for p in paras)
        body_parts.append('<div class="pgn">\u7b2c %d \u9875</div>%s' % (pno, inner))
    if not body_parts:
        body_parts.append('<p class="empty">\uff08\u672c\u6b21\u9875\u7a97\u53e3\u5185\u65e0\u53ef\u63d0\u53d6\u6b63\u6587\uff09</p>')
    stats["refs_in_body"] = sum(1 for n in ref_text if counter.get(n, 0) > 0)
    # 每条文献在正文窗口内的权威引用处数（含 [a,b]/[a-b]/作者-年份 等所有形态）。
    # 页面「↩ 回到正文（循环）」按钮的分母 m 必须用它——旧实现用 regex 逐页重扫，
    # 只认 [n]，会漏计列表/区间/作者-年份引用（实测 Transformer 7 条不一致，
    # BERT 全文作者-年份引用一条都统计不到）。
    stats["per_ref"] = {n: int(counter.get(n, 0)) for n in ref_text}

    ref_parts = []
    for num, text, _title in sorted(((r[0], r[1], r[2] if len(r) > 2 else "") for r in (refs or [])),
                                    key=lambda x: _safe_int(x[0], 0)):
        n = _safe_int(num, 0)
        if n <= 0:
            continue
        total = counter.get(n, 0)
        if total:
            back = ('<a class="back" href="#cit-%d-1" data-ref="%d" data-total="%d" '
                    'data-idx="0">\u21a9 \u56de\u5230\u6b63\u6587\uff08%d \u5904\uff09</a>'
                    % (n, n, total, total))
        else:
            back = ('<span class="noback">\u21a9 \u6b63\u6587\u672a\u68c0\u7d22\u5230\u8be5\u6807\u8bb0'
                    '\uff08\u53ef\u80fd\u4ee5 [a-b] \u533a\u95f4\u5f62\u5f0f\u5f15\u7528\uff09</span>')
        ref_parts.append('<div class="ref" id="ref-%d"><span class="rn">[%d]</span>'
                         '<span class="rt">%s</span><div class="rb">%s</div></div>'
                         % (n, n, _esc(ref_text.get(n, text)), back))
    if not ref_parts:
        ref_parts.append('<p class="empty">\uff08\u672a\u628a\u5230 References \u6761\u76ee\uff09</p>')

    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>' + _CITE_JUMP_CSS
        + '</style></head><body>'
        '<div id="bar">\u6b63\u6587\u5f15\u7528\u6807\u8bb0 \u2194 References \u53cc\u5411\u65e0\u635f\u8df3\u8f6c'
        ' \u00b7 \u70b9\u51fb [n] \u5b9a\u4f4d\u6587\u732e\uff0c\u70b9\u51fb \u21a9 \u5faa\u73af\u56de\u8df3\u6b63\u6587'
        '</div><div id="wrap">'
        '<div class="pane" id="bodyPane">' + "".join(body_parts) + '</div>'
        '<div class="pane" id="refsPane">' + "".join(ref_parts) + '</div>'
        '</div>' + _CITE_JUMP_JS + _HEIGHT_JS + '</body></html>')
    return html, stats


# =====================================================================
# 模块三 T3：公式 / 代码 / 数值保护（翻译前占位，翻译后无损还原）
# =====================================================================
_MATH_TOKEN_FMT = "@@FM%d@@"
_RESTORE_RE = re.compile(r"@\s*@\s*FM\s*(\d+)\s*@\s*@")
# 代码块/行内代码：先于公式占位，避免模型翻译代码内容（含代码里的数字/公式）
_CODE_RE = re.compile(r"```[\s\S]{0,2000}?```|`[^`\n]{1,200}`")
_MATH_RE = re.compile(
    r"\$\$[\s\S]{1,400}?\$\$"                                      # 行间公式 $$..$$
    r"|\$[^\$\n]{1,200}?\$"                                        # 行内公式 $..$
    r"|\\\((?:[^\\]|\\(?!\)))*?\\\)"                               # \(..\)
    r"|\\\[(?:[^\\]|\\(?!\]))*?\\\]"                               # \[..\]
    r"|\\[a-zA-Z]{1,}(?:\s*(?:\{[^{}]{0,80}\}|_[^{}\s]+|\^[^{}\s]+))*"          # \beta_1 / \frac{a}{b}
    r"(?:\s*[-+*/=]\s*(?:[A-Za-z0-9.]+"
    r"|\\[a-zA-Z]+(?:\s*(?:\{[^{}]{0,80}\}|_[^{}\s]+|\^[^{}\s]+))*))*"
    r"|\b[A-Za-z][A-Za-z0-9_.\-]{0,24}\s*=\s*[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?"  # BLEU=28.4
    r"|\b\d+(?:\.\d+)?\s*%"                                        # 28.4%
    r"|\b\d+\.\d+\b"                                               # 0.9 / 3.14
)
_RESTORE_RE = re.compile(r"@\s*@\s*FM\s*(\d+)\s*@\s*@")


def protect_math(text):
    """翻译前：把代码块/公式/LaTeX/数值表达式替换为占位符。

    返回 (masked_text, store)；store 为原始片段列表，供 restore_math 还原。
    任何异常都退化为「原样返回 + 空 store」，绝不冒泡。
    """
    store = []
    try:
        def _sub_code(m):
            store.append(m.group(0))
            return _MATH_TOKEN_FMT % (len(store) - 1)

        def _sub(m):
            store.append(m.group(0))
            return _MATH_TOKEN_FMT % (len(store) - 1)

        s = str(text or "")
        s = _CODE_RE.sub(_sub_code, s)      # ① 代码块先行（代码里可能含 $ 公式/数字）
        return _MATH_RE.sub(_sub, s), store  # ② 再占位公式/数值
    except Exception:
        return str(text or ""), []


def restore_math(text, store):
    """翻译后：把占位符还原成原始公式片段（兼容模型把空格插进占位符的情况）。"""
    if not store:
        return str(text or "")
    try:
        def _sub(m):
            try:
                i = int(m.group(1))
            except (TypeError, ValueError):
                return m.group(0)
            return store[i] if 0 <= i < len(store) else m.group(0)
        return _RESTORE_RE.sub(_sub, str(text or ""))
    except Exception:
        return str(text or "")


def math_safe_translate(ctx, text, call=None):
    """带公式保护的翻译：protect → 调模型 → restore。

    call 默认为 services.writing.translate_to_chinese(ctx, masked)；
    失败返回 ("", 错误信息)，与页面其它翻译路径口径一致。
    """
    try:
        src = str(text or "")
        if not src.strip():
            return "", "内容为空"
        if call is None:
            cached = _translate_cache_get(src)
            if cached is not None:
                return cached, ""
        masked, store = protect_math(src)
        fn = call
        if fn is None:
            from services import writing as _w
            fn = _w.translate_to_chinese
        out = fn(ctx, masked)
        out = "" if out is None else str(out)
        if not out.strip():
            return "", "模型返回空内容"
        zh = restore_math(out, store)
        # 仅缓存默认路径的成功译文；降级文案（以「【LLM」开头）不入缓存。
        if call is None and not zh.lstrip().startswith("【LLM"):
            _translate_cache_put(src, zh)
        return zh, ""
    except Exception as e:
        return "", "%s: %s" % (type(e).__name__, str(e)[:120])


# =====================================================================
# T3+：流式翻译（公式保护 + 流式还原，供「划词翻译」消等待）
# =====================================================================
# 与 services.writing.translate_to_chinese 的 prompt 保持一致（改那边记得同步这里）
_STREAM_TRANSLATE_PROMPT = ("请将以下英文学术内容精准翻译为中文：保留专业术语（首次出现时可在括号内标注原文）、"
                            "公式与引用标记，保持段落结构；只输出中文译文，不要解释。\n内容：\n%s")
_STREAM_FALLBACK_PREFIX = "【LLM 暂时不可用"   # core.llm 全部重试失败时的兜底文案前缀


def _stream_restore(chunks, store):
    """边收流边还原占位符：完整出现的 @@FM\\d+@@ 立即替换，疑似不完整的前缀留在缓冲区。

    store 为空时直通；任何异常都退化为原样 yield，绝不冒泡。

    实现说明：chunk 边界可能把一个占位符切成两半（"...@@FM3" + "@@，..."）。
    旧实现用 rfind("@") 找切分点，会把 "@@FM3" 切成 "@" + "@FM3"——后半辈子孙
    都无法再匹配 _RESTORE_RE，占位符原样漏到译文里（实测复现）。现在用
    「占位符前缀」正则精确定位结尾处待定的一部分，只保留真正不完整的前缀。
    """
    # 结尾处可能出现的「不完整占位符」前缀（完整占位符会被下面的循环先吃掉）
    _PARTIAL_TOKEN_RE = re.compile(r"@(?:@(?:F(?:M(?:\d{0,4}(?:@(?:@)?)?)?)?)?)?$")
    buf = ""
    try:
        for ch in chunks:
            s = "" if ch is None else str(ch)
            if not s:
                continue
            if not store:
                yield s
                continue
            buf += s
            pos, out = 0, []
            while True:
                m = _RESTORE_RE.search(buf, pos)
                if not m:
                    break
                out.append(buf[pos:m.start()])
                try:
                    i = int(m.group(1))
                except (TypeError, ValueError):
                    out.append(m.group(0))
                    pos = m.end()
                    continue
                out.append(store[i] if 0 <= i < len(store) else m.group(0))
                pos = m.end()
            rest = buf[pos:]
            m = _PARTIAL_TOKEN_RE.search(rest)
            if m:
                out.append(rest[:m.start()])
                buf = rest[m.start():]
            else:
                out.append(rest)
                buf = ""
            if out:
                yield "".join(out)
        if buf:
            yield buf
    except Exception:
        if buf:
            yield buf


def stream_translate_to_chinese(ctx, text, temperature=0.2):
    """流式翻译生成器：protect_math → ctx.llm.stream → 流式还原公式 → yield 增量文本。

    设计约束（与页面其它翻译路径口径一致）：
      - Key 不可用 / 内容为空：直接 return（页面侧看到空输出即提示失败）；
      - core.llm.stream 全部重试失败时会 yield「【LLM 暂时不可用…】」兜底文案
        （core/llm.py 的行为，不抛异常），此处识别到该前缀即停止 yield，
        绝不把兜底文案当译文展示；
      - 任何异常都在 generator 内吞掉并停止，不冒泡到页面。
    """
    src = str(text or "")
    if not src.strip():
        return
    if not getattr(getattr(ctx, "llm", None), "available", False):
        return
    masked, store = protect_math(src)
    try:
        chunks = ctx.llm.stream(
            [{"role": "user", "content": _STREAM_TRANSLATE_PROMPT % masked[:6000]}],
            temperature=temperature)
        for piece in _stream_restore(chunks, store):
            if not piece:
                continue
            if piece.lstrip().startswith(_STREAM_FALLBACK_PREFIX):
                return
            yield piece
    except Exception:
        return

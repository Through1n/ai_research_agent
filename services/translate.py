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
"""
import importlib

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


def _block_html(block, rel_x, y, max_w):
    """把一个 dict block 渲染成绝对定位的 .blk（内部行用常规流，避免块内重叠）。"""
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
            inner.append(
                '<span style="font-size:%.1fpx;color:%s;%s">%s</span>'
                % (sfs, rgb, style, _esc(s.get("text", ""))))
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


def native_page_html(doc, page_num):
    """文本块+坐标→HTML：双栏 PDF 按栏分组进独立容器，块宽限制在栏宽内（A1）。

    返回 (html, pw, ph) —— 与旧签名一致。
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
            html = _block_html(block, rel_x, y0, max_w)
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

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


def _words_of_page(page):
    """取词级坐标（`get_text("words")`），用于给行内文字补回词间空格。

    2026-10-24 真机根因：这批 PDF 的文本层是逐字形定位（TJ 数组），`get_text("dict")`
    返回的 span 文本**没有词间空格**（"ingandauto-encoderobjectiveshavebeenused"）。
    没有空格的长串在 `overflow-wrap:anywhere` 下被浏览器从中间拆行 → 块高超出原始
    高度 → 绝对定位的下一个块压上来，看起来就是「文字重叠 / 串栏」。
    词级接口本身带空格语义，用它做行内补空，块高才能与原版面一致。
    """
    try:
        return list(page.get_text("words") or [])
    except Exception:
        return []


def _join_words(ws):
    """按 x 顺序拼接一行内的词：纯 ASCII 边界补空格，CJK 之间不补（避免「中 文 字」）。"""
    out = ""
    for w in ws:
        t = str(w[4] or "")
        if not t:
            continue
        if out and out[-1].isascii() and t[:1].isascii():
            out += " "
        out += t
    return out


def _line_text_from_words(words, bb):
    """在给定行 bbox 内挑出词并按序拼接；挑不到返回空串（调用方回落到 span 文本）。"""
    if not words:
        return ""
    x0, y0, x1, y1 = bb
    picked = [w for w in words
              if (y0 - 2.0) <= float(w[1]) <= (y1 + 2.0)
              and float(w[2]) >= (x0 - 2.0) and float(w[0]) <= (x1 + 2.0)]
    if not picked:
        return ""
    picked.sort(key=lambda w: float(w[0]))
    return _join_words(picked)


def _word_rows_from_words(words, bb, y_tol=3.0):
    """按 y 把 bbox 内的词聚成「视觉行」并各自拼接。

    为什么需要分簇：dict 里的一个 line 可能横跨整页（图注 / 通栏标题），此时按 bbox
    一次性取词会把左右两栏的词交错拼在一起（实测：「symbol models for added
    different in front down-stream of every input tasks…」就是交错产物）。
    先按 y 分簇，每簇只包含同一视觉行上的词，再拼接。
    """
    if not words:
        return []
    x0, y0, x1, y1 = bb
    picked = [w for w in words
              if (y0 - 2.0) <= float(w[1]) <= (y1 + 2.0)
              and float(w[2]) >= (x0 - 2.0) and float(w[0]) <= (x1 + 2.0)]
    if not picked:
        return []
    picked.sort(key=lambda w: (float(w[1]), float(w[0])))
    rows, cur = [], [picked[0]]
    for w in picked[1:]:
        if abs(float(w[1]) - float(cur[-1][1])) <= y_tol:
            cur.append(w)
        else:
            rows.append(cur)
            cur = [w]
    rows.append(cur)
    out = []
    for row in rows:
        row.sort(key=lambda w: float(w[0]))
        t = _join_words(row)
        if t.strip():
            out.append((min(float(w[1]) for w in row), t))
    return out


def _block_html(block, rel_x, y, max_w, highlight=None, words=None, bbox=None):
    """把一个 dict block 渲染成绝对定位的 .blk；**块内每一行也按 PDF 原始坐标绝对定位**。

    为什么行也要绝对定位（2026-10-24 真机定位到的根因）：行若走常规流，行高由浏览器
    字体度量决定，块内容高度就可能超过 PDF 版面的原始高度，绝对定位的下一个块随即被
    压住 —— 截图上就是「文字重叠 / 串栏」。改成「块按 bbox 定框 + 行按原始 y 定位」后，
    版式与 PDF 一一对应，与字体是否一致无关。

    T2：highlight 为标题文本时，命中的 span 加 class="mk"（黄色高亮），
    供「标题树点击 → 原生渲染定位并高亮该标题」使用；highlight 为空则行为不变。
    `words`：整页词级坐标，用于补回词间空格（见 `_words_of_page` 的根因说明）。
    `bbox`：(x0,y0,x1,y1) 页面坐标；给了就按它定块框，行坐标相对它换算。
    """
    hl = str(highlight or "").strip().lower()
    lines_html = []
    bx0 = float(bbox[0]) if bbox else float(rel_x)
    by0 = float(bbox[1]) if bbox else float(y)
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
        # 逐字形定位的 PDF：span 文本是一整串没有空格的字（长串）→ 用词级坐标重建
        # 该行文本；通栏行（图注/标题）再按 y 分簇，避免左右栏词交错。
        span_text = "".join(str(s.get("text", "")) for s in spans).strip()
        lx0 = min(float((s.get("bbox") or [0, 0, 0, 0])[0]) for s in spans)
        lx1 = max(float((s.get("bbox") or [0, 0, 0, 0])[2]) for s in spans)
        rows = []
        if words and " " not in span_text and len(span_text) > 12 and ly0 is not None:
            rows = [r for r in _word_rows_from_words(words, (lx0, ly0, lx1, ly1))
                    if " " in r[1]]
        if rows:
            for ry, rtext in rows:
                mk = " mk" if (hl and hl in rtext.lower()) else ""
                lines_html.append(
                    '<div class="ln" style="left:%.1fpx;top:%.1fpx;font-size:%.1fpx;'
                    'line-height:%.2fpx;"><span class="sp%s">%s</span></div>'
                    % (lx0 - bx0, ry - by0, fs, lh, mk, _esc(rtext)))
        else:
            # 每行按 PDF 原始坐标绝对定位：行高由版面决定，不再随浏览器字体度量
            # 撑高块、把下一个块压住（2026-10-24 实测「文字重叠」的根因）。
            lines_html.append(
                '<div class="ln" style="left:%.1fpx;top:%.1fpx;font-size:%.1fpx;'
                'line-height:%.2fpx;">%s</div>'
                % (lx0 - bx0, (ly0 if ly0 is not None else by0) - by0, fs, lh, "".join(inner)))
    if not lines_html:
        return None
    if bbox is not None:
        bw = max(12.0, float(bbox[2]) - float(bbox[0]))
        bh = max(4.0, float(bbox[3]) - float(bbox[1]))
        return ('<div class="blk" style="left:%.1fpx;top:%.1fpx;width:%.1fpx;height:%.1fpx;">%s</div>'
                % (rel_x, y, bw, bh, "".join(lines_html)))
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
/* 令牌化（自包含）：每个自定义属性都写成「变量名 + 兜底字面量」的形式，
   iframe 内取不到父窗口变量时自动回落，外部注入令牌后又能无缝接管。 */
.ai4s-panel{position:fixed;right:14px;bottom:14px;width:100%;max-width:330px;
max-width:min(330px, calc(100vw - 28px));z-index:99;
background:var(--c-surface,#ffffff);border:1px solid var(--c-border-strong,#d4d4d8);
border-radius:var(--r,8px);padding:var(--s-3,12px) var(--s-4,16px);box-shadow:none;
font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","WenQuanYi Micro Hei","Helvetica Neue",Arial,sans-serif);
font-size:var(--fs-caption,12px);line-height:1.5;color:var(--c-text,#18181b);overflow-wrap:anywhere;}
.ai4s-panel .ai4s-t{font-size:var(--fs-caption,12px);font-weight:600;color:var(--c-text,#18181b);
margin-bottom:var(--s-2,8px);}
.ai4s-panel .ai4s-x{font-size:var(--fs-caption,12px);color:var(--c-text-2,#52525b);line-height:1.5;
max-height:96px;overflow:auto;background:var(--c-bg,#fafafa);
border:1px solid var(--c-border,#e4e4e7);border-radius:var(--r,8px);
padding:var(--s-2,8px) var(--s-3,12px);white-space:pre-wrap;overflow-wrap:anywhere;}
.ai4s-panel .ai4s-b{margin-top:var(--s-2,8px);display:flex;gap:var(--s-2,8px);flex-wrap:wrap;}
.ai4s-panel .ai4s-b button{border:1px solid var(--c-accent,#1f5fae);
background:var(--c-accent-soft,rgba(31, 95, 174, 0.08));color:var(--c-accent,#1f5fae);
border-radius:var(--r,8px);padding:var(--s-1,4px) var(--s-3,12px);
font-size:var(--fs-caption,12px);font-family:inherit;line-height:1.5;cursor:pointer;}
.ai4s-panel .ai4s-b button:hover{background:var(--c-accent,#1f5fae);color:var(--c-surface,#ffffff);}
.ai4s-panel .ai4s-h{margin-top:var(--s-2,8px);font-size:var(--fs-micro,11.5px);line-height:1.5;
color:var(--c-text-3,#a1a1aa);}
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
    _page_words = _words_of_page(page)          # 词级坐标：补回词间空格（防长串拆行）
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
            html = _block_html(block, rel_x, y0, max_w, highlight=highlight,
                               words=_page_words, bbox=bb)
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

    # 渲染顺序：中缝(底层) → 通栏块 → 左右栏正文 → 图片保位层，DOM 顺序符合阅读顺序
    body_layers = gutter + full_html + cols_html + _figures_html(doc, page, pw, ph)

    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;}}
html,body{{margin:0;padding:0;background:var(--c-bg,#fafafa);color:var(--c-text,#18181b);overflow-wrap:anywhere;}}
img{{max-width:100%;height:auto;}}
#tip{{position:fixed;top:var(--s-2,8px);left:var(--s-2,8px);right:var(--s-2,8px);z-index:8;
background:var(--c-accent,#1f5fae);color:var(--c-surface,#ffffff);
font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","WenQuanYi Micro Hei","Helvetica Neue",Arial,sans-serif);
font-size:var(--fs-caption,12px);line-height:1.5;padding:var(--s-1,4px) var(--s-3,12px);
border-radius:var(--r,8px);}}
/* 外层自适应容器：窄屏时 #page 不再撑破 iframe（只加容器，不动任何 PDF 坐标） */
#stage{{max-width:100%;overflow-x:auto;overflow-y:hidden;padding:0 var(--s-2,8px);}}
#page{{position:relative;width:{pw:.1f}px;height:{ph:.1f}px;background:var(--c-surface,#ffffff);
margin:var(--s-5,40px) auto var(--s-3,16px);box-shadow:0 0 0 1px var(--c-border,#e4e4e7);}}
.col{{position:absolute;top:0;overflow:visible;}}
.col#col1{{border-left:0;}}
.full{{position:absolute;left:0;top:0;width:100%;}}
.gutter{{position:absolute;top:0;background:repeating-linear-gradient(180deg,var(--c-subtle,#f4f4f5) 0 10px,var(--c-bg,#fafafa) 10px 20px);
border-left:1px dashed var(--c-border-strong,#d4d4d8);border-right:1px dashed var(--c-border-strong,#d4d4d8);}}
.blk{{position:absolute;font-family:'Times New Roman',Times,serif;color:var(--c-text,#18181b);
white-space:normal;overflow-wrap:break-word;word-break:break-word;user-select:text;}}
.ln{{position:absolute;white-space:nowrap;}}
/* 悬停高亮：半透明底 + 细描边。块 bbox 可能远大于文字本身（通栏块/长换行块），
   早期用不透明 #fff3b0 会把页面图片与相邻文本整片糊住（2026-10-22 用户实测
   「鼠标放置遮挡图片」）；半透明化后既保留「当前在哪一块」的提示，又不遮内容。 */
.blk:hover{{background:rgba(255,243,176,.42);box-shadow:inset 0 0 0 1px rgba(240,173,46,.5);}}
.blk span{{user-select:text;}}
/* T2：标题树点击后，命中的标题文字高亮（3 秒后由 JS 淡出） */
.mk{{background:#ffe08a;border-radius:2px;box-shadow:inset 0 -2px 0 #f0ad2e;}}
@keyframes ai4smkf{{0%{{background:#ffcf4d;}}100%{{background:#ffe08a;}}}}
.mk{{animation:ai4smkf 3s ease-out 1;}}
</style></head><body><div id="tip">{tip}</div>
<div id="stage"><div id="page">{body}</div></div>{sel}{hgt}</body></html>""".format(
        pw=pw, ph=ph, tip=_esc(tip), body=body_layers,
        sel=SELECTION_JS + _SENT_EXPAND_JS, hgt=_HEIGHT_JS)
    return html, pw, ph


# =====================================================================
# A2：中英同步滚动（按比例 + isSyncing + rAF 节流 + data-index 高亮）
# =====================================================================
def bilingual_html(pairs, glossary=None, align=True):
    """中英对比：两栏独立滚动，比例同步 + data-index 联动；句级对齐 + 术语高亮（Round13）。"""
    import json
    left, right = [], []
    for i, (_en, _zh) in enumerate(pairs):
        en, zh = _aligned_pair_html(_en, _zh, i, align)
        zh = _glossary_mark(zh, glossary) if (_zh and glossary) else zh
        left.append('<p data-index="%d" data-i="%d">%s</p>' % (i, i, en))
        right.append('<p data-index="%d" data-i="%d">%s</p>' % (i, i, zh))
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;}}
html,body{{margin:0;height:100%;overflow:hidden;background:var(--c-bg,#fafafa);
font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","WenQuanYi Micro Hei","Helvetica Neue",Arial,sans-serif);
font-size:var(--fs-body,13.5px);line-height:1.65;color:var(--c-text,#18181b);overflow-wrap:anywhere;}}
img{{max-width:100%;height:auto;}}
/* 单层滚动（选 (b)）：#wrap 是全文档唯一滚动容器，两栏共用同一滚动位置。
   理由：本视图由 embed_html(..., height=760, scrolling=False) 装载，iframe 本身
   不可滚动；若选 (a) 内容自适应 + 高度 JS，长文档会被高度上限截断且无滚动条可救，
   内容直接不可达；(b) 把滚动收敛到一层，两栏天然同步（不弱于原比例同步），
   且窗口再窄也只会出现「一条」滚动条。 */
#wrap{{display:flex;align-items:flex-start;flex-wrap:wrap;height:100vh;max-height:100vh;
overflow-y:auto;overflow-x:hidden;}}
.pane{{flex:1 1 320px;min-width:0;max-width:100%;
padding:var(--s-5,24px) var(--s-4,16px) var(--s-7,40px);overflow-wrap:anywhere;}}
#l{{background:var(--c-surface,#ffffff);border-right:1px solid var(--c-border,#e4e4e7);}}
#r{{background:var(--c-bg,#fafafa);}}
p{{font-size:var(--fs-body,13.5px);line-height:1.65;margin:0 0 var(--s-3,12px);
padding:var(--s-2,8px) var(--s-3,12px);border-radius:var(--r,8px);
border-bottom:1px solid var(--c-border,#e4e4e7);color:var(--c-text,#18181b);cursor:default;
overflow-wrap:anywhere;}}
p.active{{background:var(--c-accent-soft,rgba(31, 95, 174, 0.08));
box-shadow:inset 3px 0 0 var(--c-accent,#1f5fae);}}
#bar{{position:fixed;top:var(--s-2,8px);left:50%;transform:translateX(-50%);
background:var(--c-accent,#1f5fae);color:var(--c-surface,#ffffff);
padding:var(--s-1,4px) var(--s-4,16px);border-radius:var(--r,8px);
font-size:var(--fs-caption,12px);line-height:1.5;z-index:9;
max-width:calc(100% - 16px);overflow-wrap:anywhere;}}
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
</script>{sel}</body></html>""".format(L="".join(left), R="".join(right), sel=SELECTION_JS + _SENT_LINK_JS)


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
        top = _figure_safe_top(src, ph)
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
*{{box-sizing:border-box;}}
html,body{{margin:0;background:var(--c-bg,#fafafa);
font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","WenQuanYi Micro Hei","Helvetica Neue",Arial,sans-serif);
font-size:var(--fs-body,13.5px);line-height:1.65;color:var(--c-text,#18181b);overflow-wrap:anywhere;}}
body{{padding:var(--s-4,16px);}}
img{{max-width:100%;height:auto;}}
.s{{margin-bottom:var(--s-3,12px);padding:var(--s-4,16px);border-radius:var(--r,8px);
background:var(--c-surface,#ffffff);border:1px solid var(--c-border,#e4e4e7);
font-size:var(--fs-body,13.5px);line-height:1.65;color:var(--c-text,#18181b);overflow-wrap:anywhere;}}
.f{{background:var(--c-subtle,#f4f4f5);border-left:3px solid var(--c-accent,#1f5fae);}}
.b{{background:var(--c-surface,#ffffff);border-left:3px solid var(--c-border-strong,#d4d4d8);}}
.btn{{display:inline-block;margin-top:var(--s-2,8px);padding:var(--s-2,8px) var(--s-4,16px);
background:var(--c-accent,#1f5fae);color:var(--c-surface,#ffffff);
border:1px solid var(--c-accent,#1f5fae);border-radius:var(--r,8px);cursor:pointer;
font-family:inherit;font-size:var(--fs-caption,12px);line-height:1.5;text-decoration:none;}}
.btn:hover{{background:var(--c-accent-hover,#1a4f92);border-color:var(--c-accent-hover,#1a4f92);}}
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
    body = "".join('<p style="margin:0 0 var(--s-3,12px);padding:var(--s-2,8px) var(--s-3,12px);'
                   'border-radius:var(--r,8px);background:var(--c-surface,#ffffff);'
                   'border:1px solid var(--c-border,#e4e4e7);overflow-wrap:anywhere;">%s</p>'
                   % _esc(p) for p in paras)
    if not body:
        body = '<p style="color:var(--c-text-3,#a1a1aa);">（该页无可提取文本，可能是扫描件）</p>'
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;}}
html,body{{margin:0;background:var(--c-bg,#fafafa);
font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","WenQuanYi Micro Hei","Helvetica Neue",Arial,sans-serif);
font-size:var(--fs-body,13.5px);line-height:1.65;color:var(--c-text,#18181b);overflow-wrap:anywhere;}}
img{{max-width:100%;height:auto;}}
#tip{{position:fixed;top:var(--s-2,8px);left:var(--s-2,8px);right:var(--s-2,8px);
background:var(--c-accent,#1f5fae);color:var(--c-surface,#ffffff);
font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","WenQuanYi Micro Hei","Helvetica Neue",Arial,sans-serif);
font-size:var(--fs-caption,12px);line-height:1.5;padding:var(--s-1,4px) var(--s-3,12px);
border-radius:var(--r,8px);z-index:8;overflow-wrap:anywhere;}}
#box{{margin:var(--s-5,40px) var(--s-2,8px) var(--s-3,12px);
padding:var(--s-3,12px) var(--s-4,16px);background:var(--c-surface,#ffffff);
border:1px solid var(--c-border,#e4e4e7);border-radius:var(--r,8px);}}
#box p{{font-size:var(--fs-body,13.5px);line-height:1.65;color:var(--c-text,#18181b);
user-select:text;cursor:text;overflow-wrap:anywhere;}}
#box p:hover{{background:var(--c-accent-soft,rgba(31, 95, 174, 0.08));}}
</style></head><body><div id="tip">{t}</div><div id="box">{b}</div>{s}{h}</body></html>""".format(
        t=_esc(title), b=body, s=SELECTION_JS + _SENT_EXPAND_JS, h=_HEIGHT_JS)


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
html,body{margin:0;background:var(--c-bg,#fafafa);
font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","WenQuanYi Micro Hei","Helvetica Neue",Arial,sans-serif);
font-size:var(--fs-body,13.5px);line-height:1.65;color:var(--c-text,#18181b);overflow-wrap:anywhere;}
img{max-width:100%;height:auto;}
#bar{position:fixed;top:var(--s-2,8px);left:var(--s-2,8px);right:var(--s-2,8px);z-index:8;
background:var(--c-accent,#1f5fae);color:var(--c-surface,#ffffff);
font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","WenQuanYi Micro Hei","Helvetica Neue",Arial,sans-serif);
font-size:var(--fs-caption,12px);line-height:1.5;padding:var(--s-1,4px) var(--s-3,12px);
border-radius:var(--r,8px);overflow-wrap:anywhere;}
/* 高度解耦：不再写死 720px（旧写法与外层 iframe height=780 强耦合，容器高度一变
   就变成「iframe 一条 + pane 一条」的双重滚动）。#wrap 高度完全由内容决定，
   内部不设任何固定高度上限，也不使用 vh（vh + 高度自适应 JS 会互相追赶导致
   iframe 高度震荡收缩）；_HEIGHT_JS 把 iframe 贴合内容 → 全程只剩一层滚动。 */
#wrap{display:flex;flex-wrap:wrap;gap:var(--s-3,12px);height:auto;min-height:320px;
padding:40px var(--s-2,8px) var(--s-3,12px);align-items:stretch;}
.pane{flex:1 1 320px;min-width:0;max-width:100%;overflow-y:auto;overflow-x:hidden;
background:var(--c-surface,#ffffff);border:1px solid var(--c-border,#e4e4e7);
border-radius:var(--r,8px);padding:var(--s-3,12px) var(--s-3,12px) var(--s-5,24px);}
#refsPane{background:var(--c-bg,#fafafa);}
.pgn{font-size:var(--fs-micro,11.5px);line-height:1.5;color:var(--c-text-3,#a1a1aa);
border-bottom:1px dashed var(--c-border,#e4e4e7);margin:var(--s-3,12px) 0 var(--s-2,8px);
padding-bottom:var(--s-1,4px);}
.pgn:first-child{margin-top:0;}
p.bp{font-size:var(--fs-body,13.5px);line-height:1.65;color:var(--c-text,#18181b);
margin:0 0 var(--s-2,8px);text-align:justify;user-select:text;overflow-wrap:anywhere;}
a.cit{color:var(--c-accent,#1f5fae);text-decoration:none;font-weight:600;padding:0 1px;
border-radius:2px;cursor:pointer;}
a.cit:hover{background:var(--c-accent-soft,rgba(31, 95, 174, 0.08));text-decoration:underline;}
.ref{position:relative;padding:var(--s-2,8px) var(--s-3,12px) var(--s-2,8px) var(--s-7,40px);
border-bottom:1px solid var(--c-border,#e4e4e7);font-size:var(--fs-caption,12px);line-height:1.6;
color:var(--c-text,#18181b);overflow-wrap:anywhere;}
.ref .rn{position:absolute;left:var(--s-2,8px);top:var(--s-2,8px);color:var(--c-accent,#1f5fae);
font-weight:600;}
.ref .rt{user-select:text;}
.ref .rb{margin-top:var(--s-1,4px);}
a.back{display:inline-block;font-size:var(--fs-micro,11.5px);line-height:1.5;
padding:2px var(--s-2,8px);border:1px solid var(--c-accent,#1f5fae);border-radius:var(--r,8px);
background:var(--c-accent-soft,rgba(31, 95, 174, 0.08));color:var(--c-accent,#1f5fae);
text-decoration:none;cursor:pointer;}
a.back:hover{background:var(--c-accent,#1f5fae);color:var(--c-surface,#ffffff);}
.noback{font-size:var(--fs-micro,11.5px);line-height:1.5;color:var(--c-text-3,#a1a1aa);}
@keyframes ai4sflash{0%{background:#ffd85e;}60%{background:#fff3b0;}100%{background:transparent;}}
.hit{animation:ai4sflash 2.6s ease-out 1;border-radius:4px;}
.ctip{position:fixed;z-index:99;max-width:320px;max-width:min(320px, calc(100vw - 16px));
background:var(--c-text,#18181b);color:var(--c-surface,#ffffff);
font-size:var(--fs-micro,11.5px);line-height:1.6;padding:var(--s-2,8px) var(--s-3,12px);
border-radius:var(--r,8px);box-shadow:none;pointer-events:none;overflow-wrap:anywhere;}
.empty{color:var(--c-text-3,#a1a1aa);font-size:var(--fs-caption,12px);padding:var(--s-3,12px);}
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


def stream_translate_to_chinese(ctx, text, temperature=0.2, glossary=None):
    """流式翻译生成器：protect_math → 术语表注入 → ctx.llm.stream → 流式还原公式 → yield 增量文本。

    设计约束（与页面其它翻译路径口径一致）：
      - Key 不可用 / 内容为空：直接 return（页面侧看到空输出即提示失败）；
      - core.llm.stream 全部重试失败时会 yield「【LLM 暂时不可用…】」兜底文案
        （core/llm.py 的行为，不抛异常），此处识别到该前缀即停止 yield，
        绝不把兜底文案当译文展示；
      - glossary：术语表 [(en, zh, variants), ...]，以「首选译法」前缀注入 prompt
        （DeepL 术语表式）；流式路径不做译后强制统一（增量输出无法回改），
        由页面在非流式路径（_safe_translate）补做；
      - 任何异常都在 generator 内吞掉并停止，不冒泡到页面。
    """
    src = str(text or "")
    if not src.strip():
        return
    if not getattr(getattr(ctx, "llm", None), "available", False):
        return
    masked, store = protect_math(src)
    prompt = _STREAM_TRANSLATE_PROMPT % _glossary_inject(masked[:6000], glossary)
    try:
        chunks = ctx.llm.stream(
            [{"role": "user", "content": prompt}],
            temperature=temperature)
        for piece in _stream_restore(chunks, store):
            if not piece:
                continue
            if piece.lstrip().startswith(_STREAM_FALLBACK_PREFIX):
                return
            yield piece
    except Exception:
        return


# =====================================================================
# Round13（P6）新增：句级双语对齐 / 术语一致性 / 图文保位 / 划词上下文扩展
# ---------------------------------------------------------------------
# 全部追加在文件末尾：既有符号行号不变 → docs/technical_doc.md 行号引用零漂移。
# 业界借鉴（均已落地为离线确定性实现，CI 无 Key 可跑）：
#   ① Gale & Church (1991/1993)：译文与原文句长强相关（英中亦成立），长度比可作
#      对齐主信号；Johansson & Hofland (1994)：混入数字/专名等跨语言 1:1「词汇
#      锚点」后英中对齐准确率 86.4% → 92.1% → 本文 length + anchor + DP 混合对齐；
#   ② DeepL 术语表：首选译法注入翻译上下文（而非事后查找替换）+ 译后强制统一
#      + 译文受控术语高亮；术语候选从文档离线挖掘（括号定义式/重复大写短语）；
#   ③ Docling / Marker 版式分析：图片按原始坐标保位渲染、译文叠加区避让图片，
#      保持「读到的位置」与「译文的位置」一致。
# =====================================================================

# ---------------- ① 句级双语对齐（Gale-Church 长度 + 词汇锚点 + DP） ----------------
# 英文句末缩写/小数/首字母缩写不切句（否则 "et al." "Fig. 1" "3.14" 会被切碎）
_SENT_ABBREV = frozenset((
    "fig", "figs", "eq", "eqs", "eqn", "tab", "tabs", "sec", "secs", "sect",
    "ch", "chap", "no", "pp", "vol", "pt", "ref", "refs", "resp", "viz",
    "ca", "cf", "et al", "etc", "eg", "ie", "vs", "al", "approx", "dept",
    "univ", "incl", "wrt", "min", "max", "avg", "std", "dr", "prof", "mr",
    "ms", "jr", "sr", "st",
))
# 句末标点：英文 . ! ? ；中文 。！？；
_SENT_END_CHARS = ".!?\u3002\uff01\uff1f\uff1b"


def _is_abbrev_token(tok):
    """句点前的 token 是缩写/首字母缩写 → 不是句界（小数由「句点后跟数字」另行判断）。"""
    t = str(tok or "").lower().strip(".")
    if not t:
        return False
    if len(t) == 1:                            # 姓名首字母 A. / 单字母变量
        return True
    if re.match(r"^([a-z]\.)+[a-z]?$", t):     # U.S. / e.g. / i.e. 形态
        return True
    return t in _SENT_ABBREV


def split_sentences(text, max_sent=400):
    """确定性句子切分（英/中混合，无需模型）。

    英文：`. ! ?` 后随空白/引号/括号/EOF 视为句界，但缩写（et al. / Fig. 1）、
    小数（3.14——句点后跟数字）、首字母缩写（U.S.）不切；中文：。！？；直接成句界。
    任何异常退化为按换行硬切，绝不冒泡；返回句子列表（保留句末标点）。
    """
    try:
        s = str(text or "")
        if not s.strip():
            return []
        out, buf = [], []
        i, n = 0, len(s)
        while i < n:
            ch = s[i]
            buf.append(ch)
            if ch in _SENT_END_CHARS:
                if ch in ".!?":
                    nxt = s[i + 1] if i + 1 < n else " "
                    if nxt not in " \t\r\n\"'\u201d\u2019)\uff09]\u300d":
                        i += 1
                        continue
                    if nxt.isdigit():
                        i += 1
                        continue                  # 小数点后跟数字：3.14 / 0.001 不断句
                    j = len(buf) - 2
                    while j >= 0 and (buf[j].isalnum() or buf[j] in ".-'"):
                        j -= 1
                    tok = "".join(buf[j + 1:len(buf) - 1])
                    if _is_abbrev_token(tok):
                        i += 1
                        continue
                seg = "".join(buf).strip()
                if seg:
                    out.append(seg)
                buf = []
                # 紧随句末的闭合引号/括号归入本句：否则会孤零零挂到下一句开头
                # （双语句级对齐时表现为一侧句子 span 以引号起始）。
                while i + 1 < n and s[i + 1] in "\"'\u201d\u2019)\uff09]\u300d\u300f":
                    if out:
                        out[-1] += s[i + 1]
                    i += 1
            i += 1
        tail = "".join(buf).strip()
        if tail:
            out.append(tail)
        return out[:max_sent]
    except Exception:
        try:
            return [x.strip() for x in str(text or "").split("\n") if x.strip()][:max_sent]
        except Exception:
            return []


# 跨语言 1:1「词汇锚点」：数字/百分比 + 保留原文的拉丁术语（Transformer/BLEU/GPT）
_ANCHOR_RE = re.compile(r"\d+(?:\.\d+)?%?|[A-Za-z][A-Za-z0-9\-]{1,}")


def _anchor_set(seg):
    """片段锚点集合：数字/百分比原样；≥3 字母的大写拉丁词（去尾 s 归一）。"""
    out = set()
    try:
        for m in _ANCHOR_RE.finditer(str(seg or "")):
            t = m.group(0)
            if t[0].isdigit() or t.endswith("%"):
                out.add(t)
            elif len(t) >= 3 and t[0].isupper() and any(c.isalpha() for c in t):
                out.add(t.lower().rstrip("s") if len(t) > 3 else t.lower())
    except Exception:
        return set()
    return out


def _pair_cost(en, zh, ratio):
    """候选句对代价：长度偏差（Gale-Church 主信号）- 锚点命中奖励 + 基础代价。"""
    le, lz = max(1, len(str(en or ""))), max(1, len(str(zh or "")))
    exp = max(6.0, le * float(ratio))
    dev = abs(lz - exp) / exp
    anch = len(_anchor_set(en) & _anchor_set(zh))
    return dev * 2.0 - 0.45 * min(anch, 3) + 0.20


def align_pairs(en_text, zh_text, max_units=160):
    """双语句级对齐：长度比 + 词汇锚点 + DP（离线确定性，无模型）。

    DP 操作集：1:1 / 2:1 合并（译文并句）/ 1:2 拆分 / 单侧未译（漏译或删节，
    该侧为空串）。段数悬殊或超限（>160 句或 >40000 单元格）时退化为按序号
    1:1 配对（截断/补空串）——序号兜底保证任何输入都有确定输出。

    返回 [(en_seg, zh_seg), ...]；任一侧为 "" 表示无对应句。
    """
    try:
        ens = split_sentences(en_text)
        zhs = split_sentences(zh_text)
        if not ens and not zhs:
            return []
        if not ens:
            return [("", z) for z in zhs]
        if not zhs:
            return [(e, "") for e in ens]
        if len(ens) > max_units or len(zhs) > max_units or len(ens) * len(zhs) > 40000:
            n = max(len(ens), len(zhs))
            return [((ens[i] if i < len(ens) else ""),
                     (zhs[i] if i < len(zhs) else "")) for i in range(n)]
        en_total = sum(len(e) for e in ens) or 1
        ratio = min(1.5, max(0.15, sum(len(z) for z in zhs) / float(en_total)))
        n, m = len(ens), len(zhs)
        INF = float("inf")
        D = [[INF] * (m + 1) for _ in range(n + 1)]
        B = [[None] * (m + 1) for _ in range(n + 1)]
        D[0][0] = 0.0
        for i in range(n + 1):
            for j in range(m + 1):
                cur = D[i][j]
                if cur == INF:
                    continue
                if i < n and j < m:
                    c = cur + _pair_cost(ens[i], zhs[j], ratio)
                    if c < D[i + 1][j + 1]:
                        D[i + 1][j + 1] = c
                        B[i + 1][j + 1] = (i, j, 1, 1)
                if i + 1 < n and j < m:                       # 2 英 → 1 中（译文并句）
                    c = cur + _pair_cost(ens[i] + " " + ens[i + 1], zhs[j], ratio) + 0.35
                    if c < D[i + 2][j + 1]:
                        D[i + 2][j + 1] = c
                        B[i + 2][j + 1] = (i, j, 2, 1)
                if i < n and j + 1 < m:                       # 1 英 → 2 中（译文拆句）
                    c = cur + _pair_cost(ens[i], zhs[j] + zhs[j + 1], ratio) + 0.35
                    if c < D[i + 1][j + 2]:
                        D[i + 1][j + 2] = c
                        B[i + 1][j + 2] = (i, j, 1, 2)
                if i < n:                                     # 英文未译（漏译/引用句被删）
                    c = cur + 1.8
                    if c < D[i + 1][j]:
                        D[i + 1][j] = c
                        B[i + 1][j] = (i, j, 1, 0)
                if j < m:                                     # 译文无对应原文（译者增补）
                    c = cur + 1.8
                    if c < D[i][j + 1]:
                        D[i][j + 1] = c
                        B[i][j + 1] = (i, j, 0, 1)
        pairs = []
        i, j = n, m
        while i > 0 or j > 0:
            step = B[i][j]
            if step is None:
                break
            pi, pj, de, dz = step
            e_seg = " ".join(ens[pi:pi + de]) if de else ""
            z_seg = "".join(zhs[pj:pj + dz]) if dz else ""
            pairs.append((e_seg, z_seg))
            i, j = pi, pj
        pairs.reverse()
        return pairs
    except Exception:
        # 兜底：按序号 1:1，绝不冒泡
        try:
            n = max(len(split_sentences(en_text)), len(split_sentences(zh_text)))
            return [(en_text or "", zh_text or "")] if n <= 1 else []
        except Exception:
            return [(str(en_text or ""), str(zh_text or ""))]


def _aligned_pair_html(en, zh, idx, align=True):
    """把一个 (原文, 译文) 段落对渲染成句级对齐 HTML（供 bilingual_html 调用）。

    句数一致且对齐成功时每对句子包 <span class="sn" data-sidx="k">，悬停/点击一侧
    句子即高亮并滚动到另一侧对应句子（_SENT_LINK_JS）；对齐不可用（空译文/
    单句/异常）时退化为整段一个容器，输出与旧实现完全一致。
    """
    try:
        en_esc = _esc(en)
        if not zh:
            return en_esc, "<em>（待翻译）</em>"
        zh_esc = _esc(zh)
        if not align:
            return en_esc, zh_esc
        pairs = align_pairs(en, zh)
        if len(pairs) <= 1:
            return en_esc, zh_esc
        l_parts, r_parts = [], []
        for k, (es, zs) in enumerate(pairs):
            if es:
                l_parts.append('<span class="sn" data-sidx="%d">%s</span>' % (k, _esc(es)))
            if zs:
                r_parts.append('<span class="sn" data-sidx="%d">%s</span>' % (k, _esc(zs)))
        if not l_parts or not r_parts:
            return en_esc, zh_esc
        return "".join(l_parts), "".join(r_parts)
    except Exception:
        return _esc(en), (_esc(zh) if zh else "<em>（待翻译）</em>")


# 句级联动 JS（注入 bilingual_html）：悬停/点击 .sn → 另一侧同 data-sidx 句子高亮 + 滚动到位
_SENT_LINK_JS = """
<style>
.sn{cursor:default;border-radius:2px;transition:background .12s;}
.sn.on{background:var(--c-accent-soft,rgba(31, 95, 174, 0.08));
box-shadow:inset 0 -2px 0 var(--c-accent,#1f5fae);}
</style>
<script>
(function(){
  function paneOf(el){ var p = el; while (p && p !== document.body){ if (p.id === 'l' || p.id === 'r') return p; p = p.parentElement; } return null; }
  function other(p){ return document.getElementById(p && p.id === 'l' ? 'r' : 'l'); }
  function clear(){ var ms = document.querySelectorAll('.sn.on'); for (var i = 0; i < ms.length; i++){ ms[i].classList.remove('on'); } }
  function link(el){
    var idx = el.getAttribute('data-sidx');
    if (idx === null) return;
    var o = other(paneOf(el));
    if (!o) return;
    clear();
    el.classList.add('on');
    var t = o.querySelector('.sn[data-sidx="' + idx + '"]');
    if (t){ t.classList.add('on'); try{ t.scrollIntoView({block:'nearest'}); }catch(e){} }
  }
  var ps = document.querySelectorAll('.sn');
  for (var i = 0; i < ps.length; i++){
    (function(el){
      el.addEventListener('mouseenter', function(){ link(el); });
      el.addEventListener('click', function(){ link(el); });
    })(ps[i]);
  }
  var panes = document.querySelectorAll('.pane');
  for (var j = 0; j < panes.length; j++){ panes[j].addEventListener('mouseleave', clear); }
})();
</script>
"""


# ---------------- ② 术语一致性（DeepL 术语表式：注入 + 统一 + 高亮） ----------------
# 学术 AI 领域常用术语的默认首选译法（用户可改；空中文行 = 候选待填，不生效）
DEFAULT_GLOSSARY_TEXT = """# 术语表：每行一条「英文 = 中文」；可追加误译变体「英文 = 中文; 变体1; 变体2」
# 填好中文的条目才会生效；# 开头为注释行
attention mechanism = 注意力机制
self-attention = 自注意力
multi-head attention = 多头注意力
feed-forward network = 前馈网络
layer normalization = 层归一化
residual connection = 残差连接
gradient descent = 梯度下降
backpropagation = 反向传播
learning rate = 学习率
batch size = 批大小
overfitting = 过拟合
regularization = 正则化
dropout = 随机失活
embedding = 嵌入
tokenizer = 分词器
pre-training = 预训练
fine-tuning = 微调
large language model = 大语言模型
benchmark = 基准测试
corpus = 语料库
hyperparameter = 超参数
loss function = 损失函数
ablation study = 消融实验
state of the art = 最先进水平
encoder = 编码器
decoder = 解码器
sequence-to-sequence = 序列到序列
machine translation = 机器翻译
natural language processing = 自然语言处理
zero-shot = 零样本
few-shot = 少样本
"""

_GLOSSARY_SEP_RE = re.compile(r"[=|\uff03:：\t]")
_GLOSSARY_STOP_EN = frozenset(("the", "and", "for", "with", "this", "that", "from"))


def parse_glossary(text):
    """解析术语表文本 → [(en, zh, [variants]), ...]（后者为误译变体，可为空）。

    每行支持 `英文 = 中文` / `英文|中文` / `英文：中文` / `英文,中文`（无其它分隔符时）；
    `英文 = 中文; 变体1; 变体2` 登记误译变体；`#` 注释与空行忽略；同词条后出现者覆盖先者。
    纯功能词/过短英文（<3 且无空格）跳过；任何异常退化为已解析部分，不抛泡。
    """
    out = []
    try:
        for raw in str(text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = _GLOSSARY_SEP_RE.split(line, maxsplit=1) if _GLOSSARY_SEP_RE.search(line) else None
            if not parts or len(parts) < 2:
                m = re.match(r"^([A-Za-z][A-Za-z0-9 \-/']{2,60})\s*,\s*(\S.*)$", line)
                if not m:
                    continue
                parts = [m.group(1), m.group(2)]
            en = parts[0].strip()
            rest = parts[1].strip()
            if not en or not rest:
                continue
            variants = []
            segs = [x.strip() for x in rest.split(";")]
            zh = segs[0]
            variants = [x for x in segs[1:] if x]
            low = en.lower()
            if len(en) < 3 and " " not in en and "-" not in en:
                continue
            if low in _GLOSSARY_STOP_EN:
                continue
            out.append((en, zh, variants))
        best = {}
        for en, zh, var in out:
            best[en.lower()] = (en, zh, var)
        return [best[k] for k in sorted(best)]
    except Exception:
        return out


def _norm_glossary(glossary):
    """把术语表归一成 [(en, zh, [variants]), ...]；容忍 2 元组/dict/None，无效条目跳过。"""
    out = []
    try:
        for item in (glossary or []):
            if isinstance(item, (list, tuple)):
                if len(item) >= 3:
                    en, zh, var = str(item[0]), str(item[1]), item[2] or ()
                elif len(item) == 2:
                    en, zh, var = str(item[0]), str(item[1]), ()
                else:
                    continue
            elif isinstance(item, dict):
                en, zh = str(item.get("en") or ""), str(item.get("zh") or "")
                var = item.get("variants") or ()
            else:
                continue
            en, zh = en.strip(), zh.strip()
            if len(en) < 2 or not zh:
                continue
            variants = ([str(v).strip() for v in var if str(v).strip()]
                        if isinstance(var, (list, tuple)) else ([str(var).strip()] if str(var).strip() else []))
            out.append((en, zh, variants))
    except Exception:
        return out
    return out


def glossary_prompt_block(glossary, max_entries=40):
    """术语表 → 注入翻译 prompt 的前缀块（DeepL 术语表式「首选译法」注入）。

    无有效条目返回 ""——调用方行为与不传术语表完全一致。
    """
    gl = _norm_glossary(glossary)
    if not gl:
        return ""
    try:
        lines = ["【术语表】翻译时必须采用以下首选译法，保持全文一致，不要改用其它译法："]
        for en, zh, _v in gl[:max_entries]:
            lines.append("- %s → %s" % (en, zh))
        return "\n".join(lines) + "\n\n"
    except Exception:
        return ""


def _glossary_inject(text, glossary):
    """翻译前注入：把术语表块前置到待译文本（公式占位之后调用，避免术语被 mask）。"""
    block = glossary_prompt_block(glossary)
    return (block + str(text or "")) if block else str(text or "")


def apply_glossary(zh_text, glossary):
    """译后术语强制统一（离线确定性，无模型）。

    ① 译文里残留的未译英文术语（整词匹配、大小写不敏感）→ 首选中文；
    ② 条目登记的误译变体 → 首选中文（长变体优先，防短变体切碎长变体）；
    ③ 每条替换上限 200 次，防病态输入。无术语表/异常 → 原样返回。
    """
    gl = _norm_glossary(glossary)
    if not gl:
        return str(zh_text or "")
    try:
        s = str(zh_text or "")
        for _en, zh_t, variants in gl:
            for v in sorted(variants, key=len, reverse=True):
                if v and v in s:
                    s = s.replace(v, zh_t)
        for en, zh_t, _v in sorted(gl, key=lambda x: len(x[0]), reverse=True):
            if not re.match(r"^[A-Za-z][A-Za-z0-9 \-/'-]+$", en):
                continue                      # 非纯拉丁术语不做整词替换（避免误伤中文）
            try:
                pat = re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])",
                                 re.IGNORECASE)
                s = pat.sub(lambda _m, _t=zh_t: _t, s, count=200)
            except Exception:
                continue
        return s
    except Exception:
        return str(zh_text or "")


def _glossary_apply(zh_text, glossary):
    """apply_glossary 的薄封装（保持 _safe_translate 内一行调用的可读性）。"""
    return apply_glossary(zh_text, glossary)


def _glossary_mark(zh_html, glossary):
    """受控术语高亮：把译文里出现的首选中文译法包上 <mark class="gt">（内联样式）。

    两段式（占位符 → 标签）替换，避免「长术语套短术语」被二次包裹；zh_html 假定
    已 HTML 转义，中文译法同样转义后匹配。无术语表/无命中 → 原样返回。
    """
    gl = _norm_glossary(glossary)
    if not gl or not zh_html:
        return zh_html
    try:
        targets = sorted({_esc(zh) for _e, zh, _v in gl if zh}, key=len, reverse=True)
        if not targets:
            return zh_html
        sentinels = []
        for t in targets:
            if t and t in zh_html:
                tok = "\x01%d\x02" % len(sentinels)
                zh_html = zh_html.replace(t, tok)
                sentinels.append((tok, t))
        for tok, t in sentinels:
            zh_html = zh_html.replace(
                tok, '<mark class="gt" style="background:var(--c-accent-soft,rgba(31, 95, 174, 0.08));'
                     'border-bottom:2px solid var(--c-accent,#1f5fae);'
                     'border-radius:2px;padding:0 1px;">%s</mark>' % t)
        return zh_html
    except Exception:
        return zh_html


# 术语候选挖掘：句首大写不可信，只统计行内出现；通用学术词不进术语表
_TERM_STOP = frozenset((
    "The", "This", "These", "Those", "We", "Our", "In", "On", "At", "As", "By",
    "To", "For", "From", "With", "That", "It", "Its", "Their", "His", "Her",
    "And", "But", "Or", "If", "When", "While", "However", "Moreover", "Note",
    "See", "Here", "There", "Thus", "Hence", "Also", "Then", "So", "No", "Not",
    "One", "Two", "First", "Second", "Third", "New", "Using", "Based", "Given",
    "During", "After", "Before", "Between", "Among", "Within", "Without", "Per",
    "Via", "Unlike", "Like", "Due", "Since", "Because", "Although", "Though",
    "Whether", "Each", "All", "Some", "Most", "Many", "Several", "Such", "Both",
    "Any", "Every", "None", "Figure", "Table", "Section", "Chapter", "Where",
    "Which", "What", "How", "Why", "Equation", "Algorithm", "Model", "Method",
    "Result", "Paper", "Work", "Approach", "Task", "Data", "State", "Art",
    "Training", "Learning", "Machine", "Deep", "Natural", "Language", "English",
    "Chinese", "Results", "Methods", "Models", "Datasets", "Experiment",
    "Experiments", "Analysis", "Performance", "Accuracy", "Proposed", "Show",
    "Shows", "Shown", "Used", "Use", "Uses", "Finally", "Furthermore",
))
# 短语前导的冠词/指示词（"The Transformer" → "Transformer"）
_TERM_ARTICLES = frozenset(("The", "A", "An", "This", "These", "Those"))
# 「全称 (缩写)」/「缩写 (全称)」——学术论文定义术语的标准写法（高精度模式 A）
_TERM_PAREN_FULL = re.compile(
    r"(?<![A-Za-z])([A-Z][A-Za-z\-]*(?:\s+[A-Z][A-Za-z\-]*){0,4})\s*\(([A-Za-z0-9\-]{1,12})\)")
_TERM_PAREN_ABBR = re.compile(
    r"(?<![A-Za-z])([A-Za-z0-9\-]{1,12})\s*\(([A-Z][A-Za-z\-]*(?:\s+[A-Z][A-Za-z\-]*){0,4})\)")
# 行内大写短语（1-4 词），模式 B
_TERM_CAP_RE = re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z0-9\-]{1,}(?:\s+[A-Z][A-Za-z0-9\-]{1,}){0,3})")


def _strip_leading_stop(term):
    """去掉短语前导的冠词/指示词：'The Transformer' → 'Transformer'。"""
    parts = str(term or "").split()
    while len(parts) > 1 and parts[0] in _TERM_ARTICLES:
        parts = parts[1:]
    return " ".join(parts)


def extract_terms(text, top_k=25, min_count=2):
    """离线术语挖掘（确定性，无模型）→ [(term, count), ...] 按频次降序。

    模式 A（精度最高）：`Full Term (ACRONYM)` / `ACRONYM (Full Term)` 取全称为术语，
    定义式本身就是强信号，出现一次即收；
    模式 B：重复出现的首字母大写短语/单词——行内出现计 mid、行首出现计 start，
    mid≥1 且总数≥min_count，或行首≥2 次（多行行首同一个大写词 = 专名而非句首大写）。
    通用学术词（Model/Method/We...）由停用表排除；任何异常返回 []。
    """
    try:
        s = str(text or "")
        if not s.strip():
            return []
        counts = {}
        paren_terms = set()
        for m in _TERM_PAREN_FULL.finditer(s):
            full = _strip_leading_stop(m.group(1).strip())
            if 3 <= len(full) <= 60 and full not in _TERM_STOP:
                paren_terms.add(full)
                counts[full] = counts.get(full, 0) + 1
        for m in _TERM_PAREN_ABBR.finditer(s):
            full = _strip_leading_stop(m.group(2).strip())
            if 3 <= len(full) <= 60 and full not in _TERM_STOP:
                paren_terms.add(full)
                counts[full] = counts.get(full, 0) + 1
        mid, start = {}, {}
        for line in s.splitlines():
            if not line.strip():
                continue
            for m in _TERM_CAP_RE.finditer(line):
                term = _strip_leading_stop(m.group(1).strip())
                if len(term) < 3 or term in _TERM_STOP:
                    continue
                if m.start() == 0:
                    start[term] = start.get(term, 0) + 1
                else:
                    mid[term] = mid.get(term, 0) + 1
        for t in set(list(mid) + list(start)):
            c_mid, c_start = mid.get(t, 0), start.get(t, 0)
            if (c_mid >= 1 and (c_mid + c_start) >= int(min_count)) or c_start >= 2:
                counts[t] = counts.get(t, 0) + c_mid + c_start
        items = [(t, c) for t, c in counts.items()
                 if t in paren_terms or c >= int(min_count)]
        items.sort(key=lambda x: (-x[1], x[0].lower()))
        return items[:int(top_k)]
    except Exception:
        return []


# ---------------- ③ 图文保位（图片坐标渲染 + 译文叠加避让） ----------------
def page_image_rects(page, limit=12):
    """页面图片区域（确定性几何，无模型）→ [(xref, (x0,y0,x1,y1)), ...] 按面积降序。

    get_image_info(xrefs=True) 优先；老版本 PyMuPDF 退化为
    get_images(full=True) + get_image_rects(xref)。过滤两类非"图"区域：
    ① 接近整页的背景/扫描图（面积 ≥ 85% 页面）——它是纸的扫描底，不是插图，
    渲染它会盖住文字层、并把译文叠加区避让到页顶；② <6pt 的碎屑图。
    任何失败返回 []。
    """
    out = []
    try:
        try:
            pw = float(page.rect.width)
            ph = float(page.rect.height)
        except Exception:
            pw, ph = 0.0, 0.0
        page_area = max(1.0, pw * ph)
        infos = []
        try:
            infos = page.get_image_info(xrefs=True) or []
        except TypeError:
            infos = page.get_image_info() or []
        except Exception:
            infos = []
        for it in infos:
            try:
                bb = it.get("bbox") or [0, 0, 0, 0]
                rect = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                out.append((int(it.get("xref", 0) or 0), rect))
            except Exception:
                continue
        if not out:
            for img in (page.get_images(full=True) or [])[:limit * 2]:
                try:
                    xref = int(img[0])
                    for r in (page.get_image_rects(xref) or []):
                        out.append((xref, (float(r.x0), float(r.y0), float(r.x1), float(r.y1))))
                except Exception:
                    continue
    except Exception:
        return []
    uniq = {}
    for xref, rect in out:
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w > 6.0 and h > 6.0 and (w * h) < page_area * 0.85:
            uniq[(xref, rect)] = True
    return sorted(uniq, key=lambda x: -((x[1][2] - x[1][0]) * (x[1][3] - x[1][1])))[:limit]


_FIG_MAX_BYTES = 160 * 1024        # 单张图片 base64 前字节上限
_FIG_TOTAL_BYTES = 420 * 1024     # 每页图片总字节上限（防 HTML 体积失控）
_FIG_MAX_COUNT = 6                # 每页最多渲染图片数


def _image_bytes(doc, xref, max_bytes=_FIG_MAX_BYTES):
    """抽取图片字节 → (data, mime)；超限依次尝试 JPEG 重编码 + 三次减半缩放，仍超限跳过。"""
    try:
        info = doc.extract_image(xref) or {}
        data = info.get("image") or b""
        ext = str(info.get("ext", "png") or "png").lower()
        mime = ("image/png" if ext in ("png", "") else
                "image/jpeg" if ext in ("jpg", "jpeg") else "image/" + ext)
        if data and len(data) <= max_bytes:
            return data, mime
    except Exception:
        pass
    try:
        pix = fitz.Pixmap(doc, xref)
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        for _ in range(3):
            data = pix.tobytes("jpeg")
            if data and len(data) <= max_bytes:
                return data, "image/jpeg"
            pix.shrink(1)
    except Exception:
        pass
    return b"", ""


def _figures_html(doc, page, pw, ph):
    """图文保位：按原始坐标把页面图片渲染为 base64 <img>（Docling/Marker 式保位阅读）。

    每页最多 _FIG_MAX_COUNT 张、总量 ≤ _FIG_TOTAL_BYTES；无图/抽取失败/fitz 缺失
    一律返回 ""（文字渲染路径与旧版逐字节一致）。图片层置于文字之上（z-index:2），
    图注文字不与图片 bbox 重叠，不影响阅读顺序。
    """
    try:
        if fitz is None or doc is None or page is None:
            return ""
        import base64
        rects = page_image_rects(page, limit=_FIG_MAX_COUNT)
        if not rects:
            return ""
        parts, total = [], 0
        for xref, (x0, y0, x1, y1) in rects:
            if total >= _FIG_TOTAL_BYTES or len(parts) >= _FIG_MAX_COUNT:
                break
            data, mime = _image_bytes(doc, xref)
            if not data:
                continue
            total += len(data)
            b64 = base64.b64encode(data).decode("ascii")
            w, h = max(8.0, x1 - x0), max(8.0, y1 - y0)
            parts.append(
                '<div class="fig" style="position:absolute;left:%.1fpx;top:%.1fpx;'
                'width:%.1fpx;height:%.1fpx;z-index:2;" title="图片（按原始坐标保位）">'
                '<img src="data:%s;base64,%s" alt="figure" style="width:100%%;height:100%%;'
                'object-fit:contain;border:1px dashed var(--c-border-strong,#d4d4d8);'
                'border-radius:2px;background:var(--c-surface,#ffffff);"></div>'
                % (x0, y0, w, h, mime, b64))
        return "".join(parts)
    except Exception:
        return ""


def _figure_safe_top(page, ph, ratio=0.74, min_h=54.0):
    """原位 PDF 译文叠加区避让：译文白框不压页面图片（图文保位）。

    策略（确定性）：
      ① 底部条带 [0.74*ph, ph-12] 不碰任何图片 → 原样返回（既有行为不变）；
      ② 否则找与底部条带相交图片的上沿/下沿，在其「上方」或「下方」找不碰任何
         图片、高度 ≥ min_h 的条带，高者优先（同高取靠下，贴近原文放置惯例）；
      ③ 都放不下 → 保持底部条带（宁可压图也不生成压不下译文的版式）。
    无图片/异常 → ph*ratio。
    """
    try:
        base = float(ph) * float(ratio)
        rects = page_image_rects(page, limit=8)
        if not rects:
            return base
        bottom = float(ph) - 12.0
        spans = sorted((float(r[1]), float(r[3])) for _x, r in rects
                       if float(r[3]) > 4.0 and float(r[1]) < bottom)
        if not spans:
            return base
        hit = [(y0, y1) for (y0, y1) in spans if y1 > base and y0 < bottom]
        if not hit:
            return base                                   # ① 底部条带干净
        lo = min(y0 for (y0, _y1) in hit)                 # 最高相交图片的上沿
        hi = max(y1 for (_y0, y1) in hit)                # 最低相交图片的下沿
        cands = []
        if bottom - (hi + 6.0) >= float(min_h):           # ②A 条带：整体下移到图下
            cands.append((bottom - (hi + 6.0), hi + 6.0))
        above = [y1 for (y0, y1) in spans if y1 <= lo]
        t = max(above) + 6.0 if above else 0.0            # ②B 条带：收到相交图片之上
        if (lo - 6.0) - t >= float(min_h):
            cands.append(((lo - 6.0) - t, t))
        if cands:
            cands.sort(key=lambda x: (-x[0], x[1]))
            return cands[0][1]
        return base                                       # ③ 兜底：保持底部条带
    except Exception:
        return float(ph) * float(ratio)


# ---------------- ④ 划词上下文扩展（短选词自动扩展到所在句子） ----------------
# 注入 native_page_html / selectable_text_html / bilingual_html：捕获阶段（capture=true）
# 先于 SELECTION_JS 的 mouseup 冒泡处理器执行，把 <40 字的短选词替换为所在整句，
# 解决「划一个词、翻译没上下文」的问题（CopyTranslator 类工具的经典优化）。
_SENT_EXPAND_JS = """
<script>
(function(){
  if (window.__ai4sExpand) return;
  window.__ai4sExpand = true;
  var END = '.!?\\u3002\\uff01\\uff1b\\uff1f';
  var MAX_SEL = 40, MAX_SEG = 600;
  function blockOf(node){
    var el = node && (node.parentElement || node.parentNode);
    while (el && el !== document.body){
      var t = el.textContent || '';
      if (t.length >= 12 && /[.!?\\u3002\\uff01\\uff1f]/.test(t)) return el;
      el = el.parentElement || el.parentNode;
    }
    return null;
  }
  function offsetIn(el, node, off){
    try{
      var r = document.createRange();
      r.selectNodeContents(el);
      r.setEnd(node, off);
      return r.toString().length;
    }catch(e){ return -1; }
  }
  function expand(range){
    try{
      var txt = String(range.toString() || '').replace(/\\s+/g, ' ').trim();
      if (!txt || txt.length >= MAX_SEL) return null;
      var el = blockOf(range.startContainer);
      if (!el) return null;
      var full = (el.textContent || '').replace(/\\s+/g, ' ');
      var idx = offsetIn(el, range.startContainer, range.startOffset);
      if (idx < 0) return null;
      var s0 = 0;
      for (var k = idx - 1; k >= 0; k--){ if (END.indexOf(full.charAt(k)) >= 0){ s0 = k + 1; break; } }
      var e0 = full.length;
      for (var m2 = idx; m2 < full.length; m2++){ if (END.indexOf(full.charAt(m2)) >= 0){ e0 = m2 + 1; break; } }
      var seg = full.slice(s0, e0).trim();
      if (!seg || seg.length <= txt.length || seg.length > MAX_SEG) return null;
      return seg;
    }catch(e){ return null; }
  }
  function onUp(){
    try{
      var sel = window.getSelection && window.getSelection();
      if (!sel || sel.rangeCount < 1) return;
      var raw = String(sel.toString() || '').replace(/\\s+/g, ' ').trim();
      if (raw.length < 2 || raw.length >= MAX_SEL) return;
      var seg = expand(sel.getRangeAt(0));
      if (!seg) return;
      var el = blockOf(sel.getRangeAt(0).startContainer);
      if (!el) return;
      var node = document.createTextNode(seg);
      el.appendChild(node);
      var r2 = document.createRange();
      r2.selectNodeContents(node);
      sel.removeAllRanges();
      sel.addRange(r2);
      setTimeout(function(){ try{ if (node.parentNode) node.parentNode.removeChild(node); }catch(e){} }, 0);
    }catch(e){}
  }
  try{
    document.addEventListener('mouseup', onUp, true);
    document.addEventListener('touchend', function(){ setTimeout(onUp, 0); }, true);
    document.addEventListener('dblclick', function(){ setTimeout(onUp, 0); }, true);
  }catch(e){}
})();
</script>
"""

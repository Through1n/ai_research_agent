# -*- coding: utf-8 -*-
"""全站主题一致性门禁 · 西式简洁风格（中性色 + 单一强调色 + 统一无衬线 + 去装饰）

=============================================================================
看守什么（2026-10-24 用户要求："配色克制、仅用一种强调色、字体统一无衬线、
层级分明、去除多余装饰与阴影、组件尺寸圆角一致、8px 间距节奏、任何情况下整齐"）
=============================================================================
两道互补的验证：

A. 静态扫描（Python 侧）
   扫 ui/*.py + services/graph.py + services/translate.py 的**字符串字面量**里
   的色值 / 圆角 / 阴影 / 字体声明（tokenize 只取 STRING token，注释天然排除）：
     · 渲染层（页面 CSS / 内联 style）里的颜色必须严格落在色板内
       （中性锌灰阶 8 色 + 强调蓝 #1f5fae/#1a4f92 两态 + 强调底白字）；
     · 圆角必须收敛到 var(--r)/8px（PDF 批注 2px 仅限文档面）；
     · 装饰性 box-shadow 必须为 none（唯一允许：design.py 的键盘焦点环）；
     · font-family 必须走 var(--font-sans)（.ai-snippet 等宽栈是代码块例外）。
   文档字符串里的**历史记载**（"原 #3b6fb6 已删除"）显式列入 DOC_HISTORY，
   不参与渲染故放行——新增历史记载请加进对应文件的集合，别悄悄放宽色板。

B. 浏览器计算样式（无头 Chromium）
   按 Streamlit 1.63 真实 DOM 结构复刻合成页，灌入**真实的**
   TOKENS_CSS（ui/design.py）+ _COMPONENT_CSS + _PRETTY_CSS（ui/common.py），
   1440 / 390 两个视口断言：
     1 色板纯净：所有计算色落在中性阶或强调蓝系（近似蓝 #4a90e2 这类必须翻红）
     2 单一强调色：非中性色必须精确是 #1f5fae/#1a4f92 家族
     3 无装饰阴影：所有元素 computed box-shadow 均为 none
     4 无渐变装饰：所有元素 background-image 均为 none
     5 圆角一致：按钮/卡片/输入/ pills/页签/expander/表格/KPI 全部 8px
     6 字体统一：所有含文本元素 font-family 首族相同（-apple-system）
     7 字阶层级：22 > 16 > 13.5 > 12 单调递减，行高按字阶
     8 间距落 8px 集合：卡片/KPI/芯片/表格/区块间距 ∈ {0,4,8,12,16,24,32,40,48}
     9 响应式：390 视口无横向溢出且三列换行（不挤压）；1440 三列并排

C. 反向对照（硬性纪律，同 _verify_layout_grid.py）
   门禁变绿有两种可能：真干净 / 测试看不见。结尾注入 7 份**故意写坏的样式**
   （旧蓝灰底 #f7f9fc、第二强调蓝 #4a90e2、pills 9999px 圆角、卡片投影、
   分区头渐变、15px 脱轨内边距、 Comic Sans 字体），逐一验证对应断言**真的
   会翻红**。任何一份没翻红 → 视为门禁失明，退出码 1。

依赖
----
- 解释器：C:/Users/Administrator/anaconda3/envs/ai_research/python.exe
- playwright + chromium（headless），无需起 streamlit 服务、无需网络。

用法
----
    python scripts/_verify_theme.py
退出码：全过 0，任一失败 1；结尾打印汇总行。
"""
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.design import TOKENS_CSS            # noqa: E402  真实令牌层
from ui.common import _COMPONENT_CSS, _PRETTY_CSS   # noqa: E402  真实构件/表格 CSS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =============================================================================
# 色板（与 ui/design.py :root 同值；改令牌必须同步这里）
# =============================================================================
NEUTRAL_RAMP = {  # 真中性锌灰阶（HSL 饱和度 0）
    "#fafafa": "bg", "#ffffff": "surface", "#f4f4f5": "subtle",
    "#e4e4e7": "border", "#d4d4d8": "border-strong",
    "#18181b": "text", "#52525b": "text-2", "#a1a1aa": "text-3",
}
ACCENT_HEX = {"#1f5fae", "#1a4f92"}     # 唯一强调色两态（允许以字面量做令牌定义）
ACCENT_FAMILY = {(31, 95, 174): "accent", (26, 79, 146): "accent-hover"}
ALLOWED_SPACING = {0, 4, 8, 12, 16, 24, 32, 40, 48}
RADII_OK = {"var(--r)", "8px", "calc(var(--r) - 1px)", "var(--radius)"}
FONT_OK = {"var(--font-sans)"}
MONO_HINT = "monospace"          # .ai-snippet 等宽代码块是唯一例外
# 文档面（PDF 原生渲染 / 双语视图 / 引用跳转）豁免：荧光笔语义色 + Times 纸质衬线
DOC_SURFACE_OK_HEX = {"#ffe08a", "#f0ad2e", "#ffcf4d", "#ffd85e", "#fff3b0",
                      "#ffffff", "#fafafa", "#18181b",
                      "#52525b", "#a1a1aa", "#f4f4f5", "#e4e4e7", "#d4d4d8",
                      "#1f5fae", "#1a4f92"}
DOC_SURFACE_OK_RGBA = {"rgba(255,243,176,.42)", "rgba(240,173,46,.5)"}
DOC_SURFACE_RADII = {"2px", "4px", "var(--r,8px)"}   # 荧光笔标记/句子标记/图片占位
DOC_SURFACE_FONTS = ("times", "serif", "sans-serif", "var(--font-sans", "inherit")
# 文档字符串 / 历史记载中的旧色值（"原 #xxx 已删除"），不渲染，显式放行
DOC_HISTORY = {
    "ui/design.py": {"#f7f9fc", "#e3e8ef", "#1d2733", "#5a6678", "#8a97a8",
                     "#eef4fb", "#cdd6e2", "#3b6fb6", "#888", "#333", "#999", "#173a63"},
    "ui/page_library.py": {"#3b6fb6"},
    "ui/page_qa.py": {"#f7faff", "#3b6fb6", "#27518a"},
    "services/graph.py": {"#E8A33D", "#4A90E2", "#9aa5b1"},
}
# 静态扫描范围：渲染层文件（translate.py 是文档面，走 DOC_SURFACE 豁免集）
SCAN_FILES = ["ui/common.py", "ui/design.py", "ui/sidebar.py", "ui/desktop_chrome.py",
              "ui/page_agent.py", "ui/page_library.py", "ui/page_qa.py",
              "ui/page_reading.py", "ui/page_search.py", "ui/page_graph.py",
              "ui/page_writing.py", "ui/page_misc.py",
              "services/graph.py", "services/translate.py"]
# 含 CSS/内联样式语法的字符串才参与色值/圆角/字体扫描（避开纯文案误报）
CSS_MARKER = re.compile(r"(?i)(style=|<style|var\(--|background|color|border|"
                        r"font|radius|shadow|padding|margin|gradient)")

_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])")
_RGBA_RE = re.compile(r"rgba?\([^)]*\)")
_RADIUS_RE = re.compile(r"border-radius\s*:\s*([^;}\"']+)")
_SHADOW_RE = re.compile(r"box-shadow\s*:\s*([^;}\"']+)")
_FONT_RE = re.compile(r"font-family\s*:\s*([^;}\"']+)")

_problems = []
_RECORD = True        # 反向对照期间置 False：那里的翻红是预期，不计入失败


def ok(msg):
    print(f"  ✅ {msg}")


def bad(msg):
    print(f"  ❌ {msg}")
    if _RECORD:
        _problems.append(msg)


# =============================================================================
# A. 静态扫描
# =============================================================================
def _string_literals(src):
    """tokenize 取全部 STRING token（注释是 COMMENT token，天然排除）。
    返回 [(lineno, literal)]；tokenize 失败时退化为整源扫描。"""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return [(0, src)]
    return out


def _norm_hex(h):
    h = h.lower()
    if len(h) == 4:                       # #abc → #aabbcc
        h = "#" + h[1] + h[1] + h[2] + h[2] + h[3] + h[3]
    return h


def _norm_rgba(s):
    return re.sub(r"\s+", "", s).lower()


def static_scan():
    print("== A. 静态扫描：色值 / 圆角 / 阴影 / 字体字面量 ==")
    for rel in SCAN_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            bad(f"{rel} 不存在")
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        doc_ok = {_norm_hex(h) for h in DOC_HISTORY.get(rel, set())}
        is_doc_surface = rel == "services/translate.py"
        is_graph = rel == "services/graph.py"
        for lineno, lit in _string_literals(src):
            if not CSS_MARKER.search(lit):
                continue
            for m in _HEX_RE.finditer(lit):
                h = _norm_hex(m.group(0))
                if h in NEUTRAL_RAMP or h in ACCENT_HEX:
                    continue
                if h == "#ffffff" or h == "#000000":
                    continue
                if h in doc_ok:
                    continue
                if is_doc_surface and h in DOC_SURFACE_OK_HEX:
                    continue
                bad(f"{rel}:{lineno} 色板外色值 {h}（渲染层只允许中性阶+强调蓝系）")
            for m in _RGBA_RE.finditer(lit):
                v = _norm_rgba(m.group(0))
                nums = re.findall(r"[\d.]+", v)
                if len(nums) >= 3 and all(float(n) == 0 for n in nums[:3]):
                    continue                      # 透明
                rgb = tuple(int(float(n)) for n in nums[:3])
                if rgb in ACCENT_FAMILY:
                    continue
                if is_doc_surface and v in DOC_SURFACE_OK_RGBA:
                    continue
                bad(f"{rel}:{lineno} 色板外颜色 {v}（强调蓝系外的彩色必须删除）")
            for m in _RADIUS_RE.finditer(lit):
                v = m.group(1).strip()
                if v in RADII_OK:
                    continue
                if is_doc_surface and v in DOC_SURFACE_RADII:
                    continue                      # 文档面：荧光笔/句子/图片占位标记
                if is_graph and v == "50%":
                    continue                      # 图例圆点
                bad(f"{rel}:{lineno} 圆角 {v} 不在收敛集合 {sorted(RADII_OK)}")
            for m in _SHADOW_RE.finditer(lit):
                v = m.group(1).strip()
                if v == "none" or "accent-ring" in v:
                    continue                      # 去阴影；焦点环是唯一保留
                if is_doc_surface:
                    continue                      # 文档面：纸质描边/荧光笔 inset
                bad(f"{rel}:{lineno} 装饰性 box-shadow: {v}")
            for m in _FONT_RE.finditer(lit):
                v = m.group(1).strip()
                if v in FONT_OK or MONO_HINT in v:
                    continue
                if v.startswith("-apple-system") or v == "inherit":
                    continue                      # design.py 的栈定义本体 / 显式继承
                if is_doc_surface and any(t in v.lower() for t in DOC_SURFACE_FONTS):
                    continue
                if is_graph:
                    continue                      # 图谱 canvas font 简写另行管理
                bad(f"{rel}:{lineno} font-family 未走 var(--font-sans): {v[:60]}")
    if not _problems:
        ok("ui/ 与 services 渲染层字面量全部在色板内（圆角/阴影/字体同样合规）")


# =============================================================================
# B. 浏览器计算样式
# =============================================================================
# Streamlit 1.63 结构/默认几何复刻（与 _verify_layout_grid.py 同源实测）：
#   未覆盖元素一律用色板值，避免"结构默认色"误报。
_STRUCT_CSS = """
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0}
body{background:#fafafa;color:#18181b;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC","Helvetica Neue",Arial,sans-serif;
  font-size:13.5px;line-height:1.65}
[data-testid="stAppViewContainer"]{position:relative}
[data-testid="stHeader"]{position:absolute;top:0;left:0;right:0;height:60px;
  z-index:999990;background:rgba(0,0,0,0)}
[data-testid="stMainBlockContainer"]{display:flex;flex-direction:column;
  padding:96px 80px 160px;max-width:none}
[data-testid="stVerticalBlock"]{display:flex;flex-direction:column;gap:16px;
  align-items:stretch;min-width:0}
[data-testid="stHorizontalBlock"]{display:flex;flex-direction:row;gap:16px;
  flex-wrap:wrap;align-items:stretch}
[data-testid="stColumn"]{display:flex;flex-direction:column;min-width:0;gap:16px}
.triplet > [data-testid="stColumn"]{flex:1 1 calc(33.3333% - 16px)}
[data-testid="stElementContainer"]{display:flex;flex-direction:column;min-width:0}
[data-testid="stBaseButton-secondary"],[data-testid="stBaseButton-primary"]{
  display:inline-flex;align-items:center;justify-content:center;padding:4px 12px;
  border:1px solid #d4d4d8;border-radius:8px;font-size:13.5px;min-height:40px;
  background:#ffffff;color:#18181b}
[data-testid="stBaseButton-primary"]{background:#1f5fae;color:#ffffff;border-color:#1f5fae}
[data-testid="stBaseButton-pills"],[data-testid="stBaseButton-pillsActive"]{
  display:inline-flex;align-items:center;padding:4px 12px;border:1px solid #d4d4d8;
  border-radius:9999px;font-size:12px;min-height:32px;background:#ffffff;color:#18181b}
[data-testid="stBaseButton-pillsActive"]{background:#4a90e2;color:#ffffff;border-color:#4a90e2}
[data-testid="stTextInputRootElement"]{display:flex;align-items:center;
  border:1px solid #d4d4d8;border-radius:8px;padding:0 8px;min-height:40px;background:#ffffff}
[data-testid="stTextInputField"]{height:35.6px;border:0;outline:none;background:transparent;
  font-size:13.5px;color:#18181b;width:100%}
[data-testid="stSelectbox"] > div{display:flex;align-items:center;border:1px solid #d4d4d8;
  border-radius:8px;min-height:40px;padding:0 8px;background:#ffffff}
[data-testid="stSelectbox"] > div > div{display:flex;align-items:center;min-height:38px;
  font-size:13.5px;color:#18181b}
[data-testid="stButtonGroup"]{display:flex;gap:8px;flex-wrap:nowrap}
[data-testid="stExpander"] details{border:1px solid #e4e4e7;border-radius:8px;background:#ffffff;padding:0}
[data-testid="stExpander"] summary{padding:4px 12px;border-radius:8px;font-size:13.5px}
[data-testid="stExpanderDetails"]{padding:12px;font-size:13.5px;color:#18181b}
[data-testid="stTabs"] [role="tablist"]{display:flex;gap:16px;flex-wrap:nowrap;overflow:auto hidden}
[data-testid="stTabs"] [role="tab"]{padding:4px 12px;font-size:13.5px;min-height:40px;
  display:inline-flex;align-items:center;white-space:nowrap;background:transparent;color:#18181b;border:0}
[data-testid="stMetric"]{display:flex;flex-direction:column;background:#ffffff;
  border:1px solid #e4e4e7;border-radius:8px;padding:12px 16px}
[data-testid="stMetricLabel"]{font-size:12px;color:#52525b}
[data-testid="stMetricValue"]{font-size:22px;font-weight:600;color:#18181b}
[data-testid="stImageContainer"] img{max-width:100%;height:auto;display:block;border-radius:8px}
img{display:block}
"""

_IMG = ("data:image/svg+xml;utf8,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='450'%3E"
        "%3Crect width='800' height='450' fill='%231f5fae'/%3E%3C/svg%3E")


def _page_html():
    """合成页：覆盖全部受管组件形态（含真实构件类与表格类）。"""
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
__STRUCT__
__TOKENS__
__COMPONENT__
__PRETTY__
</style></head><body>
<div data-testid="stAppViewContainer">
<header data-testid="stHeader"></header>
<div class="block-container" data-testid="stMainBlockContainer">
<div data-testid="stVerticalBlock" class="stVerticalBlock">

<div data-testid="stElementContainer"><div data-testid="stHeading"><h1>页面主标题</h1></div></div>

<div data-testid="stElementContainer"><div data-testid="stMarkdown"><p>正文段落示例，用于验证正文字阶与行高层级。</p></div></div>

<div data-testid="stElementContainer"><div class="ai-card" style="border-left:3px solid var(--c-accent);">
<div style="font-size:var(--fs-title);line-height:var(--lh-title);font-weight:700;color:var(--c-text);">📁 文献库管理</div>
<div style="margin-top:var(--s-1);font-size:var(--fs-caption);line-height:var(--lh-caption);color:var(--c-text-2);">页面头部卡片副文案</div>
</div></div>

<div data-testid="stElementContainer"><div class="ai-section">
<div class="ai-section-title">分区标题</div>
<div class="ai-section-sub">分区副文案一句话</div>
</div></div>

<div data-testid="stElementContainer"><div class="ai-kpi">
<div class="ai-kpi-val">1,234</div><div class="ai-kpi-label">指标名 / 口径</div>
</div></div>

<div data-testid="stHorizontalBlock" class="stHorizontalBlock triplet">
<div data-testid="stColumn" class="stColumn">
<div data-testid="stElementContainer"><button data-testid="stBaseButton-secondary">次要按钮</button></div>
<div data-testid="stElementContainer"><button data-testid="stBaseButton-primary">主要按钮</button></div>
</div>
<div data-testid="stColumn" class="stColumn">
<div data-testid="stElementContainer"><div data-testid="stTextInputRootElement"><input data-testid="stTextInputField" value="输入框"></div></div>
<div data-testid="stElementContainer"><div data-testid="stSelectbox"><div><div><span>下拉选项</span></div></div></div></div>
</div>
<div data-testid="stColumn" class="stColumn">
<div data-testid="stElementContainer"><div data-testid="stButtonGroup" class="stButtonGroup">
<button data-testid="stBaseButton-pills">示例一</button>
<button data-testid="stBaseButton-pillsActive">示例二</button>
</div></div>
<div data-testid="stElementContainer"><span class="ai-chip">标签</span></div>
</div>
</div>

<div data-testid="stElementContainer"><div class="ptab-wrap"><table class="ptab">
<thead><tr><th>列名</th><th>列名二</th></tr></thead>
<tbody><tr><td>单元格</td><td>Unsupervised-Pretraining-for-Language-Understanding.pdf</td></tr></tbody>
</table></div></div>

<div data-testid="stElementContainer"><div data-testid="stExpander"><details>
<summary>展开更多</summary><div data-testid="stExpanderDetails">折叠内容</div>
</details></div></div>

<div data-testid="stElementContainer"><div data-testid="stTabs"><div role="tablist">
<button role="tab">页签一</button><button role="tab">页签二</button>
</div></div></div>

<div data-testid="stElementContainer"><div data-testid="stImageContainer">
<img src="__IMG__" alt="插图"></div></div>

<div data-testid="stElementContainer"><div data-testid="stMetric">
<div data-testid="stMetricLabel">指标</div><div data-testid="stMetricValue">42</div>
</div></div>

</div></div></div></body></html>""".replace(
        "__STRUCT__", _STRUCT_CSS).replace(
        "__TOKENS__", TOKENS_CSS).replace(
        "__COMPONENT__", _COMPONENT_CSS).replace(
        "__PRETTY__", _PRETTY_CSS).replace(
        "__IMG__", _IMG)


_JS_MEASURE = r"""
() => {
  const colors = [], shadows = [], radii = [], fonts = [], bgimgs = [], pads = [];
  const COMPONENTS = '[data-testid="stBaseButton-secondary"],[data-testid="stBaseButton-primary"],'
    + '[data-testid="stBaseButton-pills"],[data-testid="stBaseButton-pillsActive"],'
    + '.ai-card,.ai-kpi,.ai-chip,.ai-section-title,.ptab-wrap,.ptab th,.ptab td,'
    + '[data-testid="stExpander"] details,[role="tab"],[data-testid="stMetric"],'
    + '[data-testid="stTextInputRootElement"],[data-testid="stSelectbox"] > div';
  const describe = (el) => {
    const t = el.getAttribute('data-testid') || el.tagName.toLowerCase();
    const c = (el.className && el.className.baseVal !== undefined) ? '' : (el.className || '');
    return t + (c ? '.' + String(c).split(' ')[0] : '');
  };
  document.querySelectorAll('*').forEach((el) => {
    if (['HTML','HEAD','STYLE','SCRIPT','TITLE','META'].indexOf(el.tagName) !== -1) return;
    const cs = getComputedStyle(el);
    const sel = describe(el);
    if (cs.color && cs.color !== 'rgba(0, 0, 0, 0)') colors.push({sel: sel, prop: 'color', val: cs.color});
    if (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)') colors.push({sel: sel, prop: 'backgroundColor', val: cs.backgroundColor});
    ['Top','Left','Right','Bottom'].forEach((s) => {
      if (cs['border' + s + 'Style'] !== 'none') {          // 无边框时 UA 的 currentColor 不可见，跳过
        const v = cs['border' + s + 'Color'];
        if (v && v !== 'rgba(0, 0, 0, 0)') colors.push({sel: sel, prop: 'border' + s + 'Color', val: v});
      }
    });
    if (cs.boxShadow && cs.boxShadow !== 'none') shadows.push({sel: sel, val: cs.boxShadow});
    if (cs.backgroundImage && cs.backgroundImage !== 'none') bgimgs.push({sel: sel, val: cs.backgroundImage.slice(0, 60)});
    if (el.matches(COMPONENTS)) {
      if (cs.borderRadius && cs.borderRadius !== '0px') radii.push({sel: sel, val: cs.borderRadius});
      ['paddingLeft','paddingRight','paddingTop','paddingBottom'].forEach((p) => pads.push({sel: sel, prop: p, val: cs[p]}));
    }
    const txt = (el.textContent || '').trim();
    if (txt && el.children.length === 0) {
      fonts.push({sel: sel, family: cs.fontFamily, size: cs.fontSize, lh: cs.lineHeight});
    }
  });
  const cols = [...document.querySelectorAll('.triplet > [data-testid="stColumn"]')];
  return {
    colors: colors, shadows: shadows, bgimgs: bgimgs, radii: radii, fonts: fonts, pads: pads,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    colTops: cols.map((c) => c.getBoundingClientRect().top),
    h1: getComputedStyle(document.querySelector('[data-testid="stHeading"] h1')).fontSize,
    h1lh: getComputedStyle(document.querySelector('[data-testid="stHeading"] h1')).lineHeight,
    sec: getComputedStyle(document.querySelector('.ai-section-title')).fontSize,
    body: getComputedStyle(document.querySelector('[data-testid="stMarkdown"] p')).fontSize,
    bodylh: getComputedStyle(document.querySelector('[data-testid="stMarkdown"] p')).lineHeight,
    cap: getComputedStyle(document.querySelector('.ai-kpi-label')).fontSize,
  };
}
"""


def _parse_color(v):
    """'rgb(31, 95, 174)' / 'rgba(31, 95, 174, 0.08)' → (r,g,b,a)"""
    nums = re.findall(r"[\d.]+", v)
    if len(nums) < 3:
        return None
    r, g, b = (int(float(n)) for n in nums[:3])
    a = float(nums[3]) if len(nums) > 3 else 1.0
    return (r, g, b, a)


def _is_gray(rgb):
    # 锌灰阶最大通道差 9（#52525b / #a1a1aa）；阈值 10 以上仍要求精确命中色板
    return max(rgb) - min(rgb) <= 10


def _px(v):
    m = re.match(r"([\d.]+)px", v or "")
    return float(m.group(1)) if m else None


def browser_checks(page, vw, results):
    m = page.evaluate(_JS_MEASURE)
    tag = f"[{vw}px]"

    # 1+2 色板纯净 / 单一强调色
    off = []
    for c in m["colors"]:
        p = _parse_color(c["val"])
        if not p or p[3] == 0:
            continue
        r, g, b, _a = p
        if _is_gray((r, g, b)):
            hexv = "#%02x%02x%02x" % (r, g, b)
            if hexv not in NEUTRAL_RAMP:
                off.append(f"{c['sel']}.{c['prop']}={hexv}(非色板灰)")
        elif (r, g, b) not in ACCENT_FAMILY:
            off.append(f"{c['sel']}.{c['prop']}={c['val']}(第三种色相)")
    results["palette"] = results.get("palette") and results["palette"] and not off
    (ok if not off else bad)(f"{tag} 色板纯净+单一强调色" + (f"：越界 {off[:4]}" if off else ""))

    # 3 无装饰阴影
    sh = m["shadows"]
    results["shadow"] = not sh
    (ok if not sh else bad)(f"{tag} 无装饰阴影" + (f"：{sh[:3]}" if sh else ""))

    # 4 无渐变装饰
    bg = m["bgimgs"]
    results["gradient"] = not bg
    (ok if not bg else bad)(f"{tag} 无渐变装饰" + (f"：{bg[:3]}" if bg else ""))

    # 5 圆角一致（组件根全部 8px）
    radii = {r["val"] for r in m["radii"]}
    results["radius"] = radii == {"8px"}
    (ok if radii == {"8px"} else bad)(f"{tag} 组件圆角一致=8px" + (f"：实测 {sorted(radii)}" if radii != {"8px"} else ""))

    # 6 字体统一
    fams = {f["family"].split(",")[0].strip().strip('"') for f in m["fonts"]}
    results["font"] = len(fams) == 1 and "-apple-system" in fams
    (ok if results["font"] else bad)(f"{tag} 字体统一无衬线" + (f"：首族 {fams}" if not results["font"] else ""))

    # 7 字阶层级（字号单调递减 + 行高按字阶：h1 1.3 / 正文 1.65）
    # ≤767px 时 h1 按设计降一档到 --fs-title（与分区标题同级），只要求不反序
    h1, sec, body, cap = (_px(m["h1"]), _px(m["sec"]), _px(m["body"]), _px(m["cap"]))
    lh1 = _px(m["h1lh"]) / h1 if h1 else 0
    lbody = _px(m["bodylh"]) / body if body else 0
    if vw <= 767:
        hier = h1 >= sec > body > cap and abs(lh1 - 1.4) < 0.05 and abs(lbody - 1.65) < 0.05
    else:
        hier = h1 > sec > body > cap and abs(lh1 - 1.3) < 0.05 and abs(lbody - 1.65) < 0.05
    results["hierarchy"] = hier
    (ok if hier else bad)(f"{tag} 字阶层级 {h1}>={sec}>{body}>{cap} 行高 h1={lh1:.2f}/正文={lbody:.2f}"
                          + ("" if hier else " 反序或行高脱轨"))

    # 8 间距落 8px 集合
    badpad = [f"{p['sel']}.{p['prop']}={p['val']}" for p in m["pads"]
              if _px(p["val"]) is not None and _px(p["val"]) not in ALLOWED_SPACING]
    results["rhythm"] = not badpad
    (ok if not badpad else bad)(f"{tag} 间距落 8px 集合" + (f"：{badpad[:4]}" if badpad else ""))

    # 9 响应式
    ov = m["overflow"]
    tops = m["colTops"]
    if vw <= 768:
        wrapped = len(set(tops)) > 1
        cond = ov <= 1 and wrapped
        results["responsive"] = cond
        (ok if cond else bad)(f"{tag} 窄屏换行不挤压+无横向溢出（overflow={ov} tops={[round(t) for t in tops]}）")
    else:
        side = len(set(tops)) == 1
        cond = ov <= 1 and side
        results["responsive"] = cond
        (ok if cond else bad)(f"{tag} 宽屏并排+无横向溢出（overflow={ov} tops={[round(t) for t in tops]}）")
    return results


# =============================================================================
# C. 反向对照：故意写坏，必须让对应断言翻红
# =============================================================================
SABOTAGES = [
    ("旧蓝灰底 #f7f9fc（重构前页面底色）", "palette",
     'html,body{background:#f7f9fc}'),
    ("第二强调蓝 #4a90e2 复辟（pills 选中态）", "palette",
     '[data-testid="stBaseButton-pillsActive"]{background:#4a90e2;border-color:#4a90e2;color:#ffffff}'),
    ("pills 回到 9999px 全圆角", "radius",
     '[data-testid="stButtonGroup"].stButtonGroup button{border-radius:9999px}'),
    ("卡片加装饰性投影", "shadow",
     '.ai-card{box-shadow:0 2px 8px rgba(0,0,0,.15)}'),
    ("分区头加蓝色渐变", "gradient",
     '.ai-section-title{background:linear-gradient(90deg,#eef4fc,rgba(238,244,252,0))}'),
    ("卡片内边距脱轨成 15px", "rhythm",
     '.ai-card{padding:15px}'),
    ("字体换成 Comic Sans", "font",
     'html,body{font-family:"Comic Sans MS",cursive}'),
]


def run_sabotages(browser):
    print("== C. 反向对照：7 份故意写坏，对应断言必须翻红 ==")
    global _RECORD
    _RECORD = False                   # 预期翻红不计失败
    try:
        blind = []
        for name, key, css in SABOTAGES:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            html = _page_html().replace("</style>", css + "\n</style>", 1)
            page.set_content(html, wait_until="load")
            res = {}
            browser_checks(page, 1440, res)
            page.close()
            # 只有目标断言需要翻红；其它断言允许连带翻红（信息性）
            if res.get(key):
                blind.append(name)
                bad(f"破坏「{name}」后断言 {key} 仍为绿 → 门禁失明")
            else:
                ok(f"破坏「{name}」→ 断言 {key} 精确翻红")
    finally:
        _RECORD = True
    return blind


def main():
    print("全站主题门禁 · 西式简洁（中性+单一强调色+无衬线+去装饰）")
    print("=" * 74)
    static_scan()
    print("== B. 浏览器计算样式（真实 TOKENS_CSS / _COMPONENT_CSS / _PRETTY_CSS） ==")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        base = {}
        for vw in (1440, 390):
            page = browser.new_page(viewport={"width": vw, "height": 900})
            page.set_content(_page_html(), wait_until="load")
            browser_checks(page, vw, base)
            page.close()
        blind = run_sabotages(browser)
        browser.close()
    print("=" * 74)
    if _problems or blind:
        print(f"汇总：失败 {len(_problems)} 项 + 失明 {len(blind)} 份")
        return 1
    print("汇总：全部通过（静态字面量 + 2 视口 × 9 组计算样式 + 7 份反向对照）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""全站布局栅格常驻门禁（Round 13 视觉重做 · 重做后的常驻验收闸门）。

用途
----
证明「排版在任何屏幕尺寸、任何内容长度下都整齐」——用无头 Chromium 加载一个
**按 Streamlit 1.63 真实 DOM 结构复刻的最小验证页**，注入 `ui/design.py` 的
`TOKENS_CSS`，在 1440 / 1024 / 768 / 390 四个视口下逐项量真实几何并断言：

  1. 无横向溢出       documentElement.scrollWidth <= clientWidth + 1
  2. 无元素重叠       主区内可见顶层块两两求交，相交面积 > 1px² 即失败（排除父子包含）
  3. 无换行塌陷       窄屏列必须换行（offsetTop 递增）且列宽不低于目标下限
  4. 栅格间距合规     stHorizontalBlock gap / block-container 内边距 / 卡片 padding
                      必须落在 8px 倍数允许集合（±1px 公差），并打印实测值
  5. 组件尺寸一致     所有单行按钮与输入框 offsetHeight 两两相等（±0.5px）、圆角相等
  6. 图片不撑破       所有 img 渲染宽度 <= 父容器内容宽度
  7. 超长文本受控     超长 arXiv 文件名元素走 CSS 截断/换行（不撑破），
                      且 innerText 仍保留完整文件名（证明不是文本截断）

反向对照（硬性纪律）
--------------------
门禁变绿有两种可能：真修好了 / 测试看不见。故脚本结尾会注入 7 份**故意写坏的 CSS**
（gap 改成 13px、去掉 flex-wrap、图片放大到 1400px、按钮高度改成 44px……），
逐一验证对应断言**真的会翻红**。任何一份没翻红 → 视为门禁失明，退出码 1。

依赖
----
- 解释器：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`
- 需要 playwright + chromium（headless），无需起 streamlit 服务、无需网络。

用法
----
    python scripts/_verify_layout_grid.py
退出码：全过 0，任一失败 1；结尾打印汇总行。

为什么是"复刻 DOM"而不是起真应用：本脚本要的是**可复跑、无外部依赖的几何门禁**，
真 streamlit 服务（端口/冷启动/业务数据）会让它变得不稳定。复刻页的结构与默认
几何全部来自 2026-10-24 在真 streamlit 1.63 上的实测（见 `_ST_STRUCT_CSS` 注释），
令牌层本身则是从 ui/design.py 原样读取的真实 CSS。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.design import (  # noqa: E402
    TOKENS_CSS, IFRAME_TOKENS_CSS, inject_into_head, ellipsis,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VIEWPORTS = [1440, 1024, 768, 390]
# 8px 基准间距允许集合（S1=4 / S3=12 为允许的半档），公差 ±1px
ALLOWED_SPACING = {0, 4, 8, 12, 16, 24, 32, 40, 48}
TOL_SPACING = 1
TOL_SIZE = 0.5
HEADER_H = 60          # stHeader 实测高度；block-container 顶部内边距含这段让位
MIN_COL_W = 200        # 窄屏列宽下限：低于此值说明被挤压而非换行


# ==============================================================================
# Streamlit 1.63 结构/默认几何复刻（2026-10-24 真机实测，勿凭感觉改）
#   · stMainBlockContainer: padding 96px 80px 160px、max-width none
#   · stVerticalBlock:  display flex / direction column / gap 16px
#   · stHorizontalBlock: data-test-wrap=true / gap 16px / flex-wrap wrap
#   · stColumn: 三等分时 flex 1 1 calc(33.3333% - 16px)，1440 视口下宽 416px
#   · stHeader: position absolute / 高 60px / z-index 999990
#   · 按钮默认 min-height 40px / radius 8px / padding 4px 12px / font-size 16px
#   · 输入框 RootElement 默认 40px，内层 field 35.6px
#   · tablist 默认 flex-wrap nowrap + overflow auto hidden（标签多时横向溢出）
# ==============================================================================
_ST_STRUCT_CSS = """
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0}
body{background:#f7f9fc;color:#1d2733;font-family:sans-serif}
[data-testid="stAppViewContainer"]{position:relative}
[data-testid="stHeader"]{position:absolute;top:0;left:0;right:0;height:60px;
    z-index:999990;background:rgba(0,0,0,0)}
[data-testid="stMainBlockContainer"]{display:flex;flex-direction:column;
    padding:96px 80px 160px;max-width:none}
[data-testid="stVerticalBlock"]{display:flex;flex-direction:column;gap:16px;
    align-items:stretch;min-width:0}
[data-testid="stHorizontalBlock"]{display:flex;flex-direction:row;gap:16px;
    flex-wrap:wrap;align-items:stretch}
[data-testid="stColumn"]{display:flex;flex-direction:column;min-width:0}
.triplet > [data-testid="stColumn"]{flex:1 1 calc(33.3333% - 16px)}
.duplet > [data-testid="stColumn"]:nth-child(1){flex:1 1 calc(66.6667% - 16px)}
.duplet > [data-testid="stColumn"]:nth-child(2){flex:1 1 calc(33.3333% - 16px)}
[data-testid="stElementContainer"]{display:flex;flex-direction:column;min-width:0}
[data-testid="stLayoutWrapper"]{display:flex;flex-direction:column;width:100%}
[data-testid="stMarkdown"] p{margin:0 0 16px;font-size:16px;line-height:25.6px}
[data-testid="stMarkdown"] h1{font-size:44px;line-height:52.8px;font-weight:700;margin:0}
[data-testid="stCaptionContainer"]{font-size:14px;line-height:22.4px}
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-primary"]{display:inline-flex;align-items:center;
    justify-content:center;padding:4px 12px;border:1px solid #cdd6e2;border-radius:8px;
    font-size:16px;min-height:40px;background:#fff;color:#1d2733}
[data-testid="stBaseButton-primary"]{background:rgb(31,95,174);color:#fff;
    border-color:rgb(31,95,174)}
[data-testid="stTextInputRootElement"],
[data-testid="stNumberInputContainer"],
[data-testid="stTextAreaRootElement"]{display:flex;align-items:center;
    border:1px solid #cdd6e2;border-radius:8px;padding:0 8px;min-height:40px}
[data-testid="stTextInputField"],
[data-testid="stNumberInputField"]{height:35.6px;border:0;outline:none;background:transparent}
[data-testid="stTextArea"] textarea,textarea{border:0;outline:none;background:transparent}
[data-testid="stSelectbox"] > div{display:flex;align-items:center;
    border:1px solid #cdd6e2;border-radius:8px;min-height:40px;padding:0 8px}
[data-testid="stSelectbox"] > div > div{display:flex;align-items:center;min-height:38px}
[data-testid="stButtonGroup"]{display:flex;gap:8px;flex-wrap:nowrap}
[data-testid="stExpander"] details{border:1px solid #e3e8ef;border-radius:8px;
    background:#fff;padding:0}
[data-testid="stExpander"] summary{padding:4px 12px;border-radius:8px;font-size:16px}
[data-testid="stExpanderDetails"]{padding:12px}
[data-testid="stTabs"] [role="tablist"]{display:flex;gap:16px;flex-wrap:nowrap;
    overflow:auto hidden}
[data-testid="stTabs"] [role="tab"]{padding:4px 12px;font-size:16px;min-height:40px;
    display:inline-flex;align-items:center;white-space:nowrap}
[data-testid="stMetric"]{display:flex;flex-direction:column}
[data-testid="stMetricLabel"]{font-size:14px}
[data-testid="stMetricValue"]{font-size:32px;font-weight:600}
[data-testid="stFileUploaderDropzone"]{border:1px dashed #cdd6e2;border-radius:8px;
    padding:16px}
[data-testid="stAlertContainer"]{border-radius:8px;padding:12px 16px;background:#eef4fb}
[data-testid="stTable"] > div{width:100%}
[data-testid="stTable"] table{border-collapse:collapse}
[data-testid="stIFrame"]{width:100%}
img{display:block}
"""

_IMG = ("data:image/svg+xml;utf8,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='450'%3E"
        "%3Crect width='800' height='450' fill='%231f5fae'/%3E%3C/svg%3E")

LONG_NAME = ("arXiv.2106.09685v3_Neural-Network-Representation-of-Tensor-Network-and-"
             "Convolutional-Architectures-for-Image-Classification-with-"
             "Unsupervised-Pretraining.pdf")
LONG_WORD = ("pneumonoultramicroscopicsilicovolcanoconiosis_"
             "supercalifragilisticexpialidocious_abcdefghijklmnopqrstuvwxyz")


def _page(extra_css: str = "") -> str:
    """拼装最小验证页：覆盖本项目会遇到的各类组件。"""
    style = "<style>" + _ST_STRUCT_CSS + "</style><style>" + TOKENS_CSS + "</style>"
    if extra_css:
        style += "<style>" + extra_css + "</style>"
    return f"""<!doctype html><html><head><meta charset="utf-8">{style}</head>
<body>
<div data-testid="stAppViewContainer">
  <header data-testid="stHeader"></header>
  <main data-testid="stMain">
   <div data-testid="stMainBlockContainer" class="block-container">
    <div data-testid="stVerticalBlock">

      <div data-testid="stElementContainer">
        <div data-testid="stHeading"><h1>研究总览 Dashboard</h1></div></div>
      <div data-testid="stElementContainer"><div data-testid="stMarkdown">
        <div data-testid="stMarkdownContainer"><p>正文段落：用于验证字号与行高的层级关系。</p></div>
      </div></div>
      <div data-testid="stElementContainer">
        <div data-testid="stCaptionContainer">辅助说明文字 caption</div></div>

      <div data-testid="stElementContainer"><div data-testid="stLayoutWrapper">
        <div data-testid="stVerticalBlock" class="stVerticalBlock">
          <div data-testid="stElementContainer"><div data-testid="stMarkdown">
            <p>container(border=True) 内容区块</p></div></div>
        </div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stLayoutWrapper">
        <div data-testid="stHorizontalBlock" class="stHorizontalBlock triplet" data-test-wrap="true">
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer"><div data-testid="stMetric">
              <div data-testid="stMetricLabel">文献总数</div>
              <div data-testid="stMetricValue">128</div>
              <div data-testid="stMetricDelta">+12</div></div></div>
          </div></div>
          <div data-testid="stColumn" class="stColumn" data-mark="col2"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer"><div data-testid="stMetric">
              <div data-testid="stMetricLabel">本周新增</div>
              <div data-testid="stMetricValue">24</div>
              <div data-testid="stMetricDelta">+3</div></div></div>
          </div></div>
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer"><div data-testid="stMetric">
              <div data-testid="stMetricLabel">图谱节点</div>
              <div data-testid="stMetricValue">1,024</div>
              <div data-testid="stMetricDelta">+96</div></div></div>
          </div></div>
        </div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stLayoutWrapper">
        <div data-testid="stHorizontalBlock" class="stHorizontalBlock duplet" data-test-wrap="true">
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer"><div data-testid="stMarkdown">
              <p>宽列（2:1 权重）</p></div></div></div></div>
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer">
              <button data-testid="stBaseButton-primary">主要按钮</button></div></div></div>
        </div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stLayoutWrapper">
        <div data-testid="stHorizontalBlock" class="stHorizontalBlock triplet" data-test-wrap="true">
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer">
              <button data-testid="stBaseButton-secondary">检索</button></div></div></div>
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer">
              <button data-testid="stBaseButton-secondary">解析</button></div></div></div>
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer">
              <button data-testid="stBaseButton-secondary">导出</button></div></div></div>
        </div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stLayoutWrapper">
        <div data-testid="stHorizontalBlock" class="stHorizontalBlock triplet" data-test-wrap="true">
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer">
              <div data-testid="stTextInput"><div data-testid="stTextInputRootElement">
                <input data-testid="stTextInputField" value="关键词"></div></div></div></div></div>
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer">
              <div data-testid="stNumberInput"><div data-testid="stNumberInputContainer">
                <input data-testid="stNumberInputField" value="10"></div></div></div></div></div>
          <div data-testid="stColumn" class="stColumn"><div data-testid="stVerticalBlock">
            <div data-testid="stElementContainer">
              <div data-testid="stSelectbox"><div class="react-aria-ComboBox"><div>
                <input value="选项一"><button>▾</button></div></div></div></div></div></div>
        </div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stTextArea">
        <div data-testid="stTextAreaRootElement"><textarea>多行输入</textarea></div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stButtonGroup">
        <button data-testid="stBaseButton-secondary">Pill A</button>
        <button data-testid="stBaseButton-secondary">Pill B</button>
        <button data-testid="stBaseButton-secondary">Pill C</button></div></div>

      <div data-testid="stElementContainer"><div data-testid="stExpander">
        <details open><summary>可展开区</summary>
          <div data-testid="stExpanderDetails">展开后的正文内容</div></details></div></div>

      <div data-testid="stElementContainer"><div data-testid="stTabs">
        <div role="tablist">
          <button role="tab">标签一</button><button role="tab">标签二</button>
          <button role="tab">标签三</button><button role="tab">标签四</button>
          <button role="tab">标签五</button><button role="tab">标签六</button>
          <button role="tab">标签七</button></div>
        <div role="tabpanel">面板内容</div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stFileUploader">
        <div data-testid="stFileUploaderDropzone">拖放文件到此处</div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stAlertContainer">
        <div data-testid="stAlert"><p>信息提示 info</p></div></div></div>
      <div data-testid="stElementContainer"><div data-testid="stAlertContainer">
        <div data-testid="stAlert"><p>成功提示 success</p></div></div></div>
      <div data-testid="stElementContainer"><div data-testid="stAlertContainer">
        <div data-testid="stAlert"><p>警告提示 warning</p></div></div></div>
      <div data-testid="stElementContainer"><div data-testid="stAlertContainer">
        <div data-testid="stAlert"><p>错误提示 error</p></div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stMarkdown">
        <div data-testid="stMarkdownContainer">
          <p data-mark="long-wrap">超长文件名：{LONG_NAME}</p></div></div></div>
      <div data-testid="stElementContainer"><div data-testid="stMarkdown">
        <div data-testid="stMarkdownContainer">
          <p>{ellipsis(LONG_NAME)}</p></div></div></div>
      <div data-testid="stElementContainer"><div data-testid="stMarkdown">
        <div data-testid="stMarkdownContainer">
          <p data-mark="long-word">超长英文单词：{LONG_WORD}</p></div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stImageContainer">
        <img src="{_IMG}" alt="figure"></div></div>

      <div data-testid="stElementContainer"><div data-testid="stTable"><div>
        <table style="width:900px"><tr><th>列A</th><th>列B</th><th>列C</th></tr>
        <tr><td>行1</td><td>值1</td><td>备注1</td></tr>
        <tr><td>行2</td><td>值2</td><td>备注2</td></tr></table></div></div></div>

      <div data-testid="stElementContainer"><div data-testid="stIFrame">
        <iframe title="embed"></iframe></div></div>

    </div>
   </div>
  </main>
</div>
</body></html>"""


# ==============================================================================
# 浏览器内测量
# ==============================================================================
_JS_MEASURE = r"""() => {
  const q = s => document.querySelector(s);
  const qa = s => Array.from(document.querySelectorAll(s));
  const cs = e => getComputedStyle(e);
  const px = v => parseFloat(v) || 0;
  const R = e => e.getBoundingClientRect();
  const H = e => +R(e).height.toFixed(2);
  const W = e => +R(e).width.toFixed(2);

  const de = document.documentElement;
  const bc = q('[data-testid="stMainBlockContainer"]');
  const hb = q('.triplet[data-testid="stHorizontalBlock"]');
  const vb = q('[data-testid="stVerticalBlock"]');

  // ---- 1. 横向溢出
  const overflow = de.scrollWidth - de.clientWidth;

  // ---- 2. 重叠检测（排除父子包含）
  const cands = qa('[data-testid="stMainBlockContainer"] [data-testid="stElementContainer"],'
                 + '[data-testid="stMainBlockContainer"] [data-testid="stColumn"],'
                 + '[data-testid="stMainBlockContainer"] [data-testid="stLayoutWrapper"]')
      .filter(e => W(e) > 0 && H(e) > 0);
  let worst = 0, worstPair = '';
  for (let i = 0; i < cands.length; i++) {
    for (let j = i + 1; j < cands.length; j++) {
      const a = cands[i], b = cands[j];
      if (a.contains(b) || b.contains(a)) continue;      // 父子关系不算重叠
      const ra = R(a), rb = R(b);
      const dx = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const dy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (dx > 0 && dy > 0 && dx * dy > worst) {
        worst = dx * dy;
        worstPair = (a.getAttribute('data-testid')) + ' x ' + (b.getAttribute('data-testid'));
      }
    }
  }

  // ---- 3. 列换行
  const cols = Array.from(hb.children);
  const colW = cols.map(c => W(c));
  const colTops = cols.map(c => Math.round(c.offsetTop));
  const colMinW = cols.map(c => cs(c).minWidth);
  const colBasis = cols.map(c => cs(c).flexBasis);
  const colBasisVar = cs(document.documentElement).getPropertyValue('--col-basis').trim();

  // ---- 4. 间距
  const bcPad = {top: px(cs(bc).paddingTop), left: px(cs(bc).paddingLeft),
                 right: px(cs(bc).paddingRight), bottom: px(cs(bc).paddingBottom)};
  const hbGap = px(cs(hb).columnGap) || px(cs(hb).gap);
  const vbGap = px(cs(vb).rowGap) || px(cs(vb).gap);
  const cardPad = px(cs(q('[data-testid="stMetric"]')).paddingTop);
  const contPad = px(cs(q('[data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"]')).paddingTop);

  // ---- 5. 控件尺寸（多行文本域不参与等值比较：高度由业务 height 参数决定）
  const ctlSel = ['[data-testid="stBaseButton-secondary"]',
                  '[data-testid="stBaseButton-primary"]',
                  '[data-testid="stTextInputRootElement"]',
                  '[data-testid="stNumberInputContainer"]',
                  '[data-testid="stSelectbox"] > div'];
  const ctlH = ctlSel.map(s => { const e = q(s); return e ? H(e) : null; });
  const ctlR = ctlSel.map(s => { const e = q(s); return e ? cs(e).borderTopLeftRadius : null; });
  const taH = H(q('[data-testid="stTextAreaRootElement"]'));

  // ---- 6. 图片
  const imgs = qa('img').map(i => {
    const p = i.parentElement, pc = cs(p);
    const content = p.clientWidth - px(pc.paddingLeft) - px(pc.paddingRight);
    return {w: W(i), parentContent: +content.toFixed(2)};
  });

  // ---- 7. 超长文本
  // ⚠️ 不能用 scrollWidth 判溢出：overflow:visible 的元素 scrollWidth 恒等于 clientWidth，
  //    长文本已撑出去却测不出来（反向对照 S6 就是这么发现的）。改用 Range 量文本真实
  //    渲染宽度：换行成功时每行 rect 宽度 <= clientWidth；nowrap 溢出时单行 rect 更宽。
  const textOverflowPx = e => {
    if (!e) return null;
    const r = document.createRange();
    r.selectNodeContents(e);
    const rects = Array.from(r.getClientRects());
    if (!rects.length) return 0;
    return +(Math.max.apply(null, rects.map(x => x.width)) - e.clientWidth).toFixed(1);
  };
  const lw = q('[data-mark="long-wrap"]');
  const le = q('.ai-ellipsis');
  const leStyle = le ? {overflow: cs(le).overflow, textOverflow: cs(le).textOverflow,
                        whiteSpace: cs(le).whiteSpace} : null;
  const lword = q('[data-mark="long-word"]');
  const long = {
    wrapDiff: textOverflowPx(lw),
    wrapScrollDiff: lw ? +(lw.scrollWidth - lw.clientWidth).toFixed(1) : null,
    wrapClient: lw ? lw.clientWidth : null,
    wrapLines: lw ? Array.from(document.createRange() && (() => {
      const r = document.createRange(); r.selectNodeContents(lw);
      return r.getClientRects();
    })()).length : null,
    ellClient: le ? le.clientWidth : null,
    ellScroll: le ? le.scrollWidth : null,
    ellParent: le ? +(le.parentElement.clientWidth
        - px(cs(le.parentElement).paddingLeft)
        - px(cs(le.parentElement).paddingRight)).toFixed(2) : null,
    ellFull: le ? le.innerText.indexOf('Unsupervised-Pretraining.pdf') >= 0 : null,
    wordDiff: textOverflowPx(lword),
    wordScrollDiff: lword ? +(lword.scrollWidth - lword.clientWidth).toFixed(1) : null,
    leStyle: leStyle,
  };

  return ({vw: window.innerWidth, overflow: +overflow.toFixed(1),
    docScrollW: de.scrollWidth, docClientW: de.clientWidth,
    worstOverlap: +worst.toFixed(2), worstPair: worstPair,
    colW: colW.map(v => +v.toFixed(1)), colTops: colTops,
    colMinW: colMinW, colBasis: colBasis, colBasisVar: colBasisVar,
    bcPad: bcPad, hbGap: +hbGap.toFixed(2), vbGap: +vbGap.toFixed(2),
    cardPad: +cardPad.toFixed(2), contPad: +contPad.toFixed(2),
    ctlH: ctlH, ctlR: ctlR, taH: +taH.toFixed(2),
    imgs: imgs, long: long});
}"""


# ==============================================================================
# 断言
# ==============================================================================
class _Report:
    """逐条即时打印实测数值（门禁的价值在于看得见数字，不只是"通过"）。"""

    def __init__(self):
        self.failed = 0

    def check(self, group, name, ok, detail):
        if not ok:
            self.failed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] G{group} {name}\n        实测：{detail}")
        return ok


def _in_spacing(v):
    """8px 倍数集合判定。

    ±1px 容差只为吸收亚像素/取整误差（如 15.6 → 16）；**整数值必须精确命中集合**。
    否则 13px 会被当成 "12 ± 1" 蒙混过关——那正是反向对照 S1 要抓的情况。
    """
    if abs(v - round(v)) > 1e-6:                     # 小数：按 ±1 就近匹配
        return any(abs(v - a) <= TOL_SPACING for a in ALLOWED_SPACING)
    return int(round(v)) in ALLOWED_SPACING          # 整数：精确命中


def _max_pair_delta(vals):
    vals = [v for v in vals if v is not None]
    return 0.0 if len(vals) < 2 else round(max(vals) - min(vals), 3)


def run_checks(page, rep, vw):
    """在当前页面跑 1–7 组断言，返回 {group: ok}。"""
    m = page.evaluate(_JS_MEASURE)
    res = {}

    # 1 无横向溢出
    ok = m["overflow"] <= 1
    res[1] = rep.check(1, "无横向溢出", ok,
                       f"scrollW={m['docScrollW']} clientW={m['docClientW']} 溢出={m['overflow']}px")

    # 2 无元素重叠
    ok = m["worstOverlap"] <= 1
    res[2] = rep.check(2, "无元素重叠", ok,
                       f"最大相交面积={m['worstOverlap']}px² 配对={m['worstPair'] or '无'}")

    # 3 无换行塌陷 / 挤压
    tops = m["colTops"]
    min_w = min(m["colW"])
    wrapped = len(set(tops)) > 1
    geo = (f"列宽={m['colW']} 最小={min_w}px(下限{MIN_COL_W}) offsetTop={tops} "
           f"--col-basis={m['colBasisVar']} min-width={m['colMinW']} basis={m['colBasis']}")
    if vw <= 768:
        ok = wrapped and min_w >= MIN_COL_W
        res[3] = rep.check(3, "窄屏换行而非挤压", ok, f"{geo} 换行={wrapped}")
    else:
        ok = not wrapped and min_w >= MIN_COL_W
        res[3] = rep.check(3, "宽屏保持并排不挤压", ok, f"{geo} 并排={not wrapped}")

    # 4 栅格间距合规（顶部内边距扣除 60px header 让位后参与判定）
    p = m["bcPad"]
    pad_ok = {
        "hbGap": (m["hbGap"], _in_spacing(m["hbGap"])),
        "vbGap": (m["vbGap"], _in_spacing(m["vbGap"])),
        "padLeft": (p["left"], _in_spacing(p["left"])),
        "padRight": (p["right"], _in_spacing(p["right"])),
        "padBottom": (p["bottom"], _in_spacing(p["bottom"])),
        "padTop-Header": (p["top"] - HEADER_H, _in_spacing(p["top"] - HEADER_H)),
        "cardPad": (m["cardPad"], _in_spacing(m["cardPad"])),
        "contPad": (m["contPad"], _in_spacing(m["contPad"])),
    }
    bad = [k for k, (v, o) in pad_ok.items() if not o]
    res[4] = rep.check(4, "间距落在 8px 倍数集合", not bad,
                       " ".join(f"{k}={v:g}" for k, (v, _) in pad_ok.items())
                       + (f" 越界={bad}" if bad else ""))

    # 5 组件尺寸一致
    dh, dr = _max_pair_delta(m["ctlH"]), len(set(m["ctlR"]))
    ok = dh <= TOL_SIZE and dr == 1
    res[5] = rep.check(5, "控件高度/圆角一致", ok,
                       f"高度={m['ctlH']} 极差={dh}px(公差{TOL_SIZE}) 圆角集合={set(m['ctlR'])} "
                       f"多行文本域={m['taH']}px(业务高度，不参与等值比较)")

    # 6 图片不撑破
    worst = max((i["w"] - i["parentContent"] for i in m["imgs"]), default=0)
    ok = worst <= 1
    detail = "; ".join(f"img {i['w']:.1f}px <= 父内容宽 {i['parentContent']:.1f}px"
                       for i in m["imgs"])
    res[6] = rep.check(6, "图片不撑破父容器", ok, f"{detail}；最大超出={worst:.2f}px")

    # 7 超长文本受控
    lg = m["long"]
    cond_wrap = lg["wrapDiff"] is not None and lg["wrapDiff"] <= 1
    cond_word = lg["wordDiff"] is not None and lg["wordDiff"] <= 1
    # 截断元素：① 不撑破父容器；② DOM 文本完整（证明是 CSS 截断不是文本截断）；
    # ③ 截断机制确实挂着（宽屏下文本本就放得下时 scrollW 会等于 clientW，
    #    所以不能拿 scrollW>clientW 当判据，要看 computed overflow/text-overflow）。
    s = lg["leStyle"] or {}
    cond_ell = (lg["ellFull"] is True
                and lg["ellClient"] is not None
                and lg["ellClient"] <= lg["ellParent"] + 1
                and s.get("overflow") == "hidden"
                and s.get("textOverflow") == "ellipsis"
                and s.get("whiteSpace") == "nowrap")
    ok = cond_wrap and cond_ell and cond_word
    res[7] = rep.check(7, "超长文本溢出受控且文本完整", ok,
                       f"换行元素 文本渲染宽-clientW={lg['wrapDiff']}px(行数{lg['wrapLines']}, "
                       f"scrollW法={lg['wrapScrollDiff']}px); "
                       f"截断元素 clientW={lg['ellClient']}<=父{lg['ellParent']} "
                       f"scrollW={lg['ellScroll']} computed={s} 完整文件名={lg['ellFull']}; "
                       f"长单词 文本渲染宽-clientW={lg['wordDiff']}px")
    return res


# ==============================================================================
# 附：Python 侧工具函数的单元级校验
# ==============================================================================
def check_helpers(rep):
    # inject_into_head：有 </head> 时插在内部；没有时原样返回
    h1 = "<!doctype html><html><head><title>t</title></head><body>x</body></html>"
    out = inject_into_head(h1)
    rep.check("H", "inject_into_head 插在 </head> 内部",
              out.count("</head>") == 1
              and IFRAME_TOKENS_CSS in out
              and out.index(IFRAME_TOKENS_CSS) < out.index("</head>")
              and out.startswith("<!doctype html>"),
              f"仍含 </head> x{out.count('</head>')}，未拼到 DOCTYPE 之前")
    h2 = "<!doctype html><html><body>no head</body></html>"
    rep.check("H", "无 </head> 时原样返回", inject_into_head(h2) == h2, "未被破坏")
    # ellipsis：转义 + 纯 CSS 截断（文本完整）
    e = ellipsis("a<b>&'\" " + LONG_NAME)
    rep.check("H", "ellipsis 转义 HTML 且保留完整文本",
              "<b>" not in e and "&lt;b&gt;" in e and "Unsupervised-Pretraining.pdf" in e
              and 'class="ai-ellipsis"' in e and 'title=' in e,
              "特殊字符已转义，DOM 内字符串完整（纯 CSS 截断）")
    rep.check("H", "ellipsis(lines=2/3) 用多行截断类",
              'class="ai-clamp-2"' in ellipsis("x", lines=2)
              and 'class="ai-clamp-3"' in ellipsis("x", lines=3),
              "ai-clamp-2 / ai-clamp-3")


# ==============================================================================
# 反向对照：故意写坏的 CSS，必须让对应断言翻红
# ==============================================================================
SABOTAGES = [
    ("S1 栅格 gap 改成 13px", 1440, 4,
     '[data-testid="stHorizontalBlock"].stHorizontalBlock{gap:13px}'),
    ("S2 列禁止换行 + min-width:0（挤压塌陷）", 768, 3,
     '[data-testid="stHorizontalBlock"].stHorizontalBlock{flex-wrap:nowrap}'
     '[data-testid="stHorizontalBlock"] > [data-testid="stColumn"].stColumn{min-width:0}'),
    # 破坏选择器必须用与令牌层同等的特异度，否则压不过
    # [data-testid="stImageContainer"] img{max-width:100%}，会变成"假破坏"
    ("S3 图片放大到 1400px 且不限制最大宽度", 1440, 6,
     '[data-testid="stImageContainer"] img{width:1400px;max-width:none}'),
    ("S3b 同上（同时应触发横向溢出）", 1440, 1,
     '[data-testid="stImageContainer"] img{width:1400px;max-width:none}'),
    ("S4 容器左右内边距改成 15px", 1440, 4,
     '.block-container[data-testid="stMainBlockContainer"]{padding-left:15px;padding-right:15px}'),
    ("S5 次要按钮高度改成 44px", 1440, 5,
     '[data-testid="stBaseButton-secondary"]{min-height:44px}'),
    # 必须在窄屏跑：1440 下这串文件名本来就放得下，nowrap 也不会溢出
    ("S6 长文本强制不换行（模拟忘记断行规则）", 390, 7,
     '[data-mark="long-wrap"]{white-space:nowrap;overflow-wrap:normal;word-break:normal}'),
    ("S7 列负 margin 制造重叠", 1440, 2,
     '[data-mark="col2"]{margin-left:-200px}'),
]


def main() -> int:
    print("=" * 78)
    print("全站布局栅格门禁 · ui/design.py TOKENS_CSS · 视口 " + "/".join(map(str, VIEWPORTS)))
    print("=" * 78)

    from playwright.sync_api import sync_playwright
    rep = _Report()
    failures = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": VIEWPORTS[0], "height": 900})

        for vw in VIEWPORTS:
            print(f"\n--- 视口 {vw}px —— 基线（真实 TOKENS_CSS）---")
            page.set_viewport_size({"width": vw, "height": 900})
            page.set_content(_page())
            page.wait_for_timeout(120)
            r = run_checks(page, rep, vw)
            for g, ok in sorted(r.items()):
                if not ok:
                    failures.append(f"{vw}px/G{g}")

        print("\n--- 附：Python 侧工具函数 ---")
        check_helpers(rep)

        print("\n--- 反向对照：故意写坏的 CSS 必须让对应断言翻红 ---")
        for name, vw, group, css in SABOTAGES:
            page.set_viewport_size({"width": vw, "height": 900})
            page.set_content(_page(css))
            page.wait_for_timeout(120)
            sub = _Report()
            r = run_checks(page, sub, vw)
            flipped = r.get(group) is False
            if not flipped:
                rep.failed += 1
                failures.append(f"反向对照失明:{name}")
            print(f"  [{'PASS' if flipped else 'FAIL'}] {name} → 断言 G{group} "
                  f"{'已翻红（门禁看得见）' if flipped else '未翻红：门禁失明！'}")

        browser.close()

    print("-" * 78)
    total = rep.failed
    if failures:
        print(f"汇总：失败 {len(failures)} 项 -> {failures}")
        return 1
    print(f"汇总：全部通过（{len(VIEWPORTS)} 视口 × 7 组断言 + 工具函数 + "
          f"{len(SABOTAGES)} 份反向对照全部翻红），失败计数={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

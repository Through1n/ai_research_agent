# -*- coding: utf-8 -*-
"""iframe 孤岛资产门禁 · 西式简洁（自适应/无溢出/单滚动/中性色/无衬线/去装饰）

=============================================================================
看守什么（2026-10-24 用户要求的关键条款："排版必须在任何情况下保持整齐——
包括不同屏幕尺寸（桌面、平板、手机）、内容长度变化……均不能出现错位、
换行塌陷或元素堆叠。请使用弹性或栅格布局实现自适应，并为文本溢出、
图片比例等设置明确的处理规则。"）
=============================================================================
主文档的样式由 _verify_theme.py 看守；本门禁专管 **iframe 孤岛**——
services/translate.py 的 native_page_html / bilingual_html /
citation_jump_html / selectable_text_html 与 ui/page_misc.py 的
_ARCH_HTML。这些资产经 st.components.v1.html 以 srcdoc 渲染，**父窗口
CSS 变量穿不透 iframe**（实测反证），所以每份文档自带迷你 :root 副本——
也因此它们不受主文档门禁保护，必须单独验。

五份资产全部用**真实生成器 + 真实输入**产出（native 用 论文样本/ 下真实
双栏论文），无头 Chromium 三档视口（1440 桌面 / 768 平板 / 390 手机）
set_content 后断言计算样式：

  1 无横向溢出：scrollWidth <= 视口宽 + 1（含 300 字符不间断串/长 URL 压力文本）；
    文本不得被横向裁切（叶子文本元素 scrollWidth 不超盒子宽——换行规则必须生效，
    否则长串会被 overflow-x:hidden 吃掉变成内容不可达）
  2 中性色板：所有计算色落锌灰色板或强调蓝两态；旧蓝灰族/第二强调色必须清零
  3 单层滚动（bilingual）：#wrap 是唯一滚动容器，两栏 pane 不独立滚动
  4 字体统一：界面文本首族 -apple-system；.blk 的 Times 是文档面豁免（纸质衬线）
  5 无装饰阴影：box-shadow 模糊半径必须为 0（ inset 标记条/1px 描边环放行）
  6 圆角一致：非文档面元素圆角 ∈ {8px}；.mk 荧光笔 2px 是文档面豁免
  7 间距落 8px 集合：采样容器 padding/margin/gap ∈ {0,4,8,12,16,24,32,40,48}
  8 架构图层不重叠：_ARCH_HTML 四层纵向堆叠互不相交、不越界
  9 结构契约：#stage 自适应容器存在；img{max-width:100%;height:auto}；
    _HEIGHT_JS 高度上报脚本在场（postMessage）

C. 反向对照（硬纪律）：8 份故意写坏（旧蓝灰底复辟、第二强调橙、
  去掉 overflow-wrap 后长串溢出、Comic Sans、装饰投影、双滚动复辟、
  15px 脱轨、9999px 圆角），对应断言必须精确翻红；任何一份没翻红
  → 门禁失明，退出码 1。

依赖：playwright + chromium（headless）；论文样本/ 下至少一份 PDF（native 资产用）。
用法：python scripts/_verify_iframe_assets.py
退出码：全过 0，任一失败/失明 1。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =============================================================================
# 色板/字体/间距常量（与 ui/design.py :root 同值；改令牌必须同步这里）
# =============================================================================
NEUTRAL_HEX = {"#fafafa", "#ffffff", "#f4f4f5", "#e4e4e7", "#d4d4d8",
               "#18181b", "#52525b", "#a1a1aa"}
ACCENT_FAMILY = {(31, 95, 174), (26, 79, 146)}          # #1f5fae / #1a4f92
# 旧蓝灰族（重构前）+ 第二强调色：计算样式里出现即翻红
OFF_PALETTE = {
    (247, 249, 252), (238, 244, 251), (231, 232, 239), (205, 214, 226),
    (238, 242, 247), (243, 247, 252), (247, 250, 253), (29, 39, 51),
    (90, 102, 120), (138, 151, 168), (23, 58, 99), (59, 111, 182),
    (74, 144, 226), (232, 163, 61), (154, 165, 177),
}
DOC_SURFACE_OK = {"#ffe08a", "#f0ad2e", "#ffcf4d", "#ffd85e", "#fff3b0"}
DOC_SURFACE_RGBA = {"rgba(255, 243, 176, 0.42)", "rgba(240, 173, 46, 0.5)"}
ALLOWED_SPACING = {0, 4, 8, 12, 16, 24, 32, 40, 48}
RADII_OK = {"8px"}                       # 界面层唯一圆角
DOC_RADII_OK = {"2px", "4px"}            # .mk 荧光笔 / 句子标记（文档面豁免）
SANS_FIRST = "-apple-system"
VIEWPORTS = [(1440, 900, "桌面"), (768, 1024, "平板"), (390, 844, "手机")]

_problems = []
_RECORD = True


def ok(msg):
    if _RECORD:
        print(f"  ✅ {msg}")


def bad(msg):
    if _RECORD:
        print(f"  ❌ {msg}")
        _problems.append(msg)
    else:
        # 反向对照阶段 _RECORD=False：这里的红是「故意写坏」换来的预期结果，
        # 用不同符号打印，免得人读日志时把对照误读成门禁失败
        # （破坏一条规则常会连带踩到相邻规则，那些连带项也不是缺陷）。
        print(f"  ↯[对照预期] {msg}")


# =============================================================================
# 资产构造：全部走真实生成器
# =============================================================================
_LONG_TOKEN = "A" * 300 + "-Unsupervised-Pretraining-for-Language-Understanding-and-Generation.pdf"
_LONG_URL = "https://arxiv.org/abs/" + "1706.03762" * 20


def _load_translate():
    from services import translate as T          # noqa: E402
    return T


def build_assets():
    """返回 {资产名: html}。native 依赖真实 PDF；缺失时跳过并在汇总里显式报出。"""
    assets = {}
    skipped = []
    T = _load_translate()

    # --- 1. native_page_html：真实双栏论文首页（坐标绝对定位 + #stage 自适应容器）
    pdf = None
    for name in ("1706.03762v7.pdf", "1706.03762v1.pdf", "1810.04805v2.pdf"):
        p = os.path.join(ROOT, "论文样本", name)
        if os.path.isfile(p):
            pdf = p
            break
    if pdf:
        try:
            import pymupdf
            doc = pymupdf.open(pdf)
            html, _pw, _ph = T.native_page_html(doc, 1, highlight="Transformer")
            assets["native_page_html"] = html
        except Exception as e:                       # noqa: BLE001
            skipped.append(f"native_page_html 生成失败：{e!r}")
    else:
        skipped.append("native_page_html：论文样本/ 下无可用 PDF（跳过=缺陷，需补语料）")

    # --- 2. bilingual_html：长文 + 不间断串 + 长 URL 压力文本（溢出规则验证）
    pairs = [
        ("The Transformer is based solely on attention mechanisms, dispensing "
         "with recurrence and convolutions entirely.",
         "Transformer 完全基于注意力机制，彻底摒弃了循环与卷积结构。"),
        ("Experiments on two machine translation tasks show these models to be "
         "superior in quality while being more parallelizable and requiring "
         "significantly less time to train.",
         "在两个机器翻译任务上的实验表明，这些模型在质量上更优，同时并行性更好、训练时间显著更短。"),
        ("Token: " + _LONG_TOKEN, "压力文本：" + _LONG_TOKEN),
        ("See " + _LONG_URL + " for details.", "详见 " + _LONG_URL + " 了解详情。"),
        ("We report new single-model state-of-the-art BLEU scores on the "
         "WMT 2014 English-to-German and English-to-French translation tasks.",
         "我们报告了在 WMT 2014 英德与英法翻译任务上的单模型最新 BLEU 分数。"),
    ]
    assets["bilingual_html"] = T.bilingual_html(pairs, glossary=None, align=True)

    # --- 3. citation_jump_html：双向跳转 + 长上下文
    assets["citation_jump_html"] = T.citation_jump_html(
        forward_ctx="……we propose a new simple network architecture, the "
                    "Transformer, based solely on attention mechanisms… " + _LONG_TOKEN,
        backward_ctx="[1] Ashish Vaswani et al. Attention Is All You Need. "
                     "NeurIPS 2017. " + _LONG_URL)

    # --- 4. selectable_text_html：划词面板
    assets["selectable_text_html"] = T.selectable_text_html(
        "Attention is all you need. " * 30 + _LONG_TOKEN,
        title="在该区域用鼠标划选要翻译的词句")

    # --- 5. _ARCH_HTML：架构图孤岛（层叠堆叠不重叠）
    from ui.page_misc import _ARCH_HTML               # noqa: E402
    assets["_ARCH_HTML"] = _ARCH_HTML
    return assets, skipped


# =============================================================================
# 浏览器测量：一次 evaluate 取回全部计算样式
# =============================================================================
_JS_MEASURE = r"""
() => {
  const out = {colors: [], shadows: [], radii: [], fonts: [], spaces: [], clips: [],
               overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
               scrollables: [], imgs: [], layers: [], hasStage: !!document.getElementById('stage'),
               hasHeightJs: (document.documentElement.innerHTML.indexOf('postMessage') !== -1)};
  const SPACE_SEL = 'body,#wrap,.pane,p,#stage,#page,.layer,.chip,.fn,.s,.btn,#bar,.arch,.arch-root,.col,.full,.gutter';
  document.querySelectorAll('*').forEach((el) => {
    const tag = el.tagName;
    if (['HTML','HEAD','STYLE','SCRIPT','TITLE','META'].indexOf(tag) !== -1) return;
    const cs = getComputedStyle(el);
    const cls = (el.className && typeof el.className === 'string') ? el.className : '';
    const sel = tag.toLowerCase() + (el.id ? '#' + el.id : '') + (cls ? '.' + cls.trim().split(/\s+/).join('.') : '');
    // 文档面判定：#page（PDF 版面容器）的后代整体豁免——纸质衬线/荧光笔/坐标定位
    // 都是内容级标注而非界面装饰（与 _verify_theme.py 的 DOC_SURFACE 政策一致）
    const docSurf = !!el.closest('#page');
    // 内容级颜色豁免：span.sp/.mk/.ln/.blk 的色值直接取自 PDF 原文（论文里的
    // 红色 CC 许可证、灰色 arXiv 印章都是内容忠实渲染，不是界面用色）
    const docContent = docSurf && (el.classList.contains('sp') || el.classList.contains('mk')
      || el.classList.contains('ln') || el.classList.contains('blk'));
    const push = (prop, val) => { if (val && val !== 'rgba(0, 0, 0, 0)') out.colors.push({sel: sel, prop: prop, val: val, doc: docSurf, content: docContent}); };
    push('color', cs.color);
    push('backgroundColor', cs.backgroundColor);
    ['Top','Left','Right','Bottom'].forEach((s) => {
      if (cs['border' + s + 'Style'] !== 'none') push('border' + s + 'Color', cs['border' + s + 'Color']);
    });
    if (cs.boxShadow && cs.boxShadow !== 'none') out.shadows.push({sel: sel, val: cs.boxShadow, doc: docSurf});
    if (cs.borderRadius && cs.borderRadius !== '0px') out.radii.push({sel: sel, val: cs.borderRadius, doc: docSurf});
    if (el.matches(SPACE_SEL)) {
      // margin-inline:auto 居中会解析出像素值（如 164px/414px），属布局而非间距节奏：
      // 左右边距相等且恰为父容器剩余空间 → 认定为 auto 居中，豁免
      let autoCenter = false;
      const par = el.parentElement;
      if (par) {
        const ml = parseFloat(cs.marginLeft), mr = parseFloat(cs.marginRight);
        if (isFinite(ml) && isFinite(mr) && Math.abs(ml - mr) < 0.5 && ml > 0) {
          // 注意：clientWidth 含 padding，offsetWidth 是边框盒——auto 边距解析对标的是
          // 「父内容盒宽 = 子边框盒宽 + 左右边距」，两边都必须换算成这个口径
          const ps = getComputedStyle(par);
          const padL = parseFloat(ps.paddingLeft) || 0, padR = parseFloat(ps.paddingRight) || 0;
          const parContent = par.clientWidth - padL - padR;
          if (Math.abs(parContent - el.offsetWidth - (ml + mr)) < 1.0) autoCenter = true;
        }
      }
      ['paddingLeft','paddingRight','paddingTop','paddingBottom',
       'marginTop','marginBottom','rowGap','columnGap'].forEach((p) => {
        const v = cs[p];
        if (v && v !== 'normal' && v !== 'auto') out.spaces.push({sel: sel, prop: p, val: v});
      });
      if (!autoCenter) {
        ['marginLeft','marginRight'].forEach((p) => {
          const v = cs[p];
          if (v && v !== 'normal' && v !== 'auto') out.spaces.push({sel: sel, prop: p, val: v});
        });
      }
    }
    const txt = (el.textContent || '').trim();
    if (txt && el.children.length === 0) {
      out.fonts.push({sel: sel, family: cs.fontFamily, first: cs.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, ''), doc: docSurf});
      // 文本裁切检测：叶子文本元素自身内容宽超出盒子宽 = 被裁切（换行塌陷/内容不可达，
      // 正是用户要求的"任何情况下不能错位"的反面）。横向滚动容器（#stage 装固定宽
      // PDF 版面）的后代跳过——那里滚动是设计意图而非缺陷。
      let inHScroll = false;
      for (let a = el.parentElement; a; a = a.parentElement) {
        const ox = getComputedStyle(a).overflowX;
        if (ox === 'auto' || ox === 'scroll') { inHScroll = true; break; }
      }
      if (!inHScroll) {
        const clip = el.scrollWidth - el.clientWidth;
        if (clip > 1) out.clips.push({sel: sel, clip: Math.round(clip), text: txt.slice(0, 24)});
      }
    }
    // 可独立滚动容器（scrollHeight 超出且 overflow 为 auto/scroll）
    const oy = cs.overflowY;
    if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1) {
      out.scrollables.push(sel);
    }
    if (tag === 'IMG') out.imgs.push({sel: sel, maxW: cs.maxWidth, h: cs.height});
    if (el.classList.contains('layer')) {
      const r = el.getBoundingClientRect();
      out.layers.push({sel: sel, top: r.top, bottom: r.bottom, left: r.left, right: r.right});
    }
  });
  const arch = document.querySelector('.arch');
  if (arch) {
    const ar = arch.getBoundingClientRect();
    out.archBox = {top: ar.top, bottom: ar.bottom, left: ar.left, right: ar.right};
  }
  return out;
}
"""


def _px(v):
    m = re.match(r"([\d.]+)px", v or "")
    return float(m.group(1)) if m else None


def _parse_color(v):
    nums = re.findall(r"[\d.]+", v or "")
    if len(nums) < 3:
        return None
    r, g, b = (int(float(n)) for n in nums[:3])
    a = float(nums[3]) if len(nums) > 3 else 1.0
    return (r, g, b, a)


def _shadow_blur_ok(val):
    """装饰阴影判定：模糊半径>0 即装饰；inset 标记条 / 0 模糊 1px 描边环放行。"""
    # computed 形如 "rgba(0, 0, 0, 0.15) 0px 2px 8px 0px" 或 "rgb(...) 3px 0px 0px inset"
    parts = val.split(",")
    tail = parts[-1] if len(parts) >= 4 else val
    lens = re.findall(r"(-?[\d.]+)px", tail)
    # 颜色分量在前，长度分量从尾部取：offset-x offset-y blur [spread]
    if len(lens) >= 3:
        return float(lens[2]) == 0.0
    return True


def check_asset(name, html, page, vw, results):
    page.set_content(html, wait_until="load")
    m = page.evaluate(_JS_MEASURE)
    tag = f"[{vw}px]"

    # 1 无横向溢出（含长串/长 URL 压力文本）
    ov = m["overflow"]
    results["overflow"] = ov <= 1
    (ok if ov <= 1 else bad)(f"{tag} {name} 无横向溢出（overflow={ov}px）")

    # 1b 文本不得被横向裁切（overflow-wrap 规则生效；scrollWidth 超出即内容不可达）
    clips = m.get("clips") or []
    results["clip"] = not clips
    (ok if not clips else bad)(f"{tag} {name} 文本无横向裁切" + (f"：{clips[:3]}" if clips else ""))

    # 2 中性色板 + 单一强调色
    off = []
    for c in m["colors"]:
        if c.get("content"):
            continue                                    # PDF 原文内容色（红色许可证等）
        p = _parse_color(c["val"])
        if not p or p[3] == 0:
            continue
        r, g, b, _a = p
        hexv = "#%02x%02x%02x" % (r, g, b)
        if c["val"] in DOC_SURFACE_RGBA or hexv in DOC_SURFACE_OK:
            continue                                    # 文档面荧光笔族
        if (r, g, b) in ACCENT_FAMILY:
            continue
        if max(r, g, b) - min(r, g, b) <= 10:
            if hexv not in NEUTRAL_HEX:
                off.append(f"{c['sel']}.{c['prop']}={hexv}(非色板灰)")
        else:
            off.append(f"{c['sel']}.{c['prop']}={c['val']}(第三种色相/旧色)")
    if name == "native_page_html" and not m["hasStage"]:
        off.append("缺少 #stage 自适应容器")
    results["palette"] = not off
    (ok if not off else bad)(f"{tag} {name} 中性色板+单一强调色" + (f"：越界 {off[:4]}" if off else ""))

    # 3 单层滚动（仅 bilingual 有该契约）
    if name == "bilingual_html":
        scr = m["scrollables"]
        only_wrap = scr == ["div#wrap"] or scr == []
        results["scroll"] = only_wrap
        (ok if only_wrap else bad)(
            f"{tag} {name} 单层滚动（唯一滚动容器=#wrap）" + (f"：实测 {scr}" if not only_wrap else ""))

    # 4 字体统一（#page 内 .blk 的 Times 是文档面豁免：纸质衬线）
    badfont = [f for f in m["fonts"]
               if f["first"] != SANS_FIRST and not f.get("doc")]
    results["font"] = not badfont
    (ok if not badfont else bad)(f"{tag} {name} 界面文本无衬线统一" + (f"：{badfont[:3]}" if badfont else ""))

    # 5 无装饰阴影（模糊半径必须 0）
    deco = [s for s in m["shadows"] if not _shadow_blur_ok(s["val"])]
    results["shadow"] = not deco
    (ok if not deco else bad)(f"{tag} {name} 无装饰阴影" + (f"：{deco[:3]}" if deco else ""))

    # 6 圆角一致（文档面 .mk 荧光笔 2px 豁免）
    badr = [r for r in m["radii"]
            if r["val"] not in RADII_OK and not (r.get("doc") and r["val"] in DOC_RADII_OK)]
    results["radius"] = not badr
    (ok if not badr else bad)(f"{tag} {name} 圆角一致=8px" + (f"：{badr[:3]}" if badr else ""))

    # 7 间距落 8px 集合
    badpad = [f"{s['sel']}.{s['prop']}={s['val']}" for s in m["spaces"]
              if _px(s["val"]) is not None and _px(s["val"]) not in ALLOWED_SPACING]
    results["rhythm"] = not badpad
    (ok if not badpad else bad)(f"{tag} {name} 间距落 8px 集合" + (f"：{badpad[:4]}" if badpad else ""))

    # 8 架构图层不重叠 + 不越界（仅 _ARCH_HTML）
    if name == "_ARCH_HTML":
        layers = sorted(m["layers"], key=lambda x: x["top"])
        overlap = []
        for a, b in zip(layers, layers[1:]):
            if b["top"] < a["bottom"] - 0.5:
                overlap.append(f"{a['sel']}↔{b['sel']}")
        box = m.get("archBox")
        outside = []
        if box:
            for L in layers:
                if L["top"] < box["top"] - 0.5 or L["bottom"] > box["bottom"] + 0.5 \
                        or L["left"] < box["left"] - 0.5 or L["right"] > box["right"] + 0.5:
                    outside.append(L["sel"])
        results["no_overlap"] = not overlap and not outside
        (ok if not overlap and not outside else bad)(
            f"{tag} {name} 四层纵向堆叠不重叠不越界（{len(layers)} 层）"
            + (f"：重叠 {overlap[:3]} 越界 {outside[:3]}" if (overlap or outside) else ""))

    # 9 结构契约：图片比例规则 + 高度上报脚本
    if name in ("bilingual_html", "citation_jump_html"):
        img_rule = "max-width:100%" in html and "height:auto" in html
        results["img_rule"] = img_rule
        (ok if img_rule else bad)(f"{tag} {name} 图片比例规则 img{{max-width:100%;height:auto}}")
    hj = m["hasHeightJs"]
    results["height_js"] = hj
    (ok if hj else bad)(f"{tag} {name} 高度上报脚本在场（postMessage）")
    return results


# =============================================================================
# C. 反向对照：故意写坏，对应断言必须翻红
# =============================================================================
def _sabotages(assets):
    bi = assets["bilingual_html"]
    arch = assets["_ARCH_HTML"]
    nat = assets.get("native_page_html", "")
    return [
        ("旧蓝灰底 #f7f9fc 复辟", "palette", bi.replace(
            "var(--c-bg,#fafafa)", "#f7f9fc", 1)),
        ("第二强调橙 #e8a33d 复辟", "palette", arch.replace(
            "--accent:#1f5fae;", "--accent:#e8a33d;", 1)),
        ("去掉 overflow-wrap 后 300 字符长串被裁切（内容不可达）", "clip", bi.replace(
            "overflow-wrap:anywhere", "overflow-wrap:normal")),
        ("字体换 Comic Sans", "font", re.sub(
            r"font-family:var\(--font-sans[^;]*;", 'font-family:"Comic Sans MS",cursive;',
            bi, count=1)),
        ("pane 加装饰性投影", "shadow", bi.replace(
            ".pane{flex:1 1 320px", ".pane{box-shadow:0 2px 8px rgba(0,0,0,.15);flex:1 1 320px", 1)),
        ("双滚动复辟（pane 独立滚动）", "scroll", bi.replace(
            ".pane{flex:1 1 320px;min-width:0;max-width:100%;",
            ".pane{flex:1 1 320px;min-width:0;max-width:100%;max-height:300px;overflow-y:auto;", 1)),
        ("内边距脱轨成 15px", "rhythm", bi.replace(
            "padding:var(--s-5,24px) var(--s-4,16px) var(--s-7,40px);",
            "padding:15px;", 1)),
        ("圆角回到 9999px", "radius", arch.replace(
            "--radius:8px;", "--radius:9999px;", 1)),
        ("native 去掉 #stage 自适应容器", "palette", nat.replace(
            '<div id="stage"><div id="page">', '<div id="page">', 1) if nat else bi),
    ]


def run_sabotages(browser, assets):
    print("== C. 反向对照：故意写坏，对应断言必须翻红 ==")
    global _RECORD
    _RECORD = False
    try:
        blind = []
        for name, key, html in _sabotages(assets):
            asset = "native_page_html" if "native" in name else \
                "_ARCH_HTML" if "9999px" in name or "e8a33d" in name else "bilingual_html"
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            res = {}
            try:
                check_asset(asset, html, page, 1440, res)
            except Exception as e:                        # noqa: BLE001
                bad(f"破坏「{name}」后测量异常：{e!r}")
            page.close()
            if res.get(key):
                blind.append(name)
                bad(f"破坏「{name}」后断言 {key} 仍为绿 → 门禁失明")
            else:
                ok(f"破坏「{name}」→ 断言 {key} 精确翻红")
    finally:
        _RECORD = True
    return blind


def main():
    print("iframe 孤岛资产门禁 · 西式简洁（自适应/无溢出/单滚动/中性色/无衬线/去装饰）")
    print("=" * 74)
    assets, skipped = build_assets()
    for s in skipped:
        bad(f"资产缺失：{s}")
    if not assets:
        print("汇总：无任何资产可测（全部缺失）")
        return 1
    print(f"== 资产（{len(assets)} 份，真实生成器） == " + " / ".join(assets))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for vw, vh, label in VIEWPORTS:
            print(f"== B. 计算样式 · {label} {vw}×{vh} ==")
            for name, html in assets.items():
                page = browser.new_page(viewport={"width": vw, "height": vh})
                try:
                    check_asset(name, html, page, vw, {})
                except Exception as e:                    # noqa: BLE001
                    bad(f"[{vw}px] {name} 测量异常：{e!r}")
                page.close()
        blind = run_sabotages(browser, assets)
        browser.close()
    print("=" * 74)
    if _problems or blind:
        print(f"汇总：失败 {len(_problems)} 项 + 失明 {len(blind)} 份")
        return 1
    print(f"汇总：全部通过（{len(assets)} 资产 × 3 视口计算样式 + 9 份反向对照）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

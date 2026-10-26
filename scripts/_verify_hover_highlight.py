"""阅读页悬停高亮修复的浏览器级验证（2026-10-22）。

背景：services/translate.py 的 native_page_html 用 .blk:hover 高亮当前文本块，
早期规则是不透明的 #fff3b0——文本块 bbox 常远大于文字本身（通栏块/长换行块），
用户实测「鼠标放置到某一个地方会有显示 bug，遮挡图片」：整片页面内容被糊住。
修复改为半透明底 + 细描边。tests/ 里的单测只能断言 CSS 字符串，证明不了真实
浏览器引擎里悬停色真的可透见，故用无头 Chromium 加载**模块真实渲染产物**断言。

断言：
  A. 修复后：悬停 .blk 的计算背景色 alpha < 1（内容可透见），且有细描边；
  B. 复现 bug：把旧规则（不透明 #fff3b0）注入同 DOM → alpha == 1（整片糊住）；
  C. 不影响 T2 标题高亮 .mk（仍是不透明 #ffe08a，那是刻意的人工高亮）；
  D. 悬停前后文字仍可读（颜色对比不丢失）。

退出码：0 = 全部通过；1 = 有失败项。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402

from services import parsing  # noqa: E402
from services.translate import native_page_html  # noqa: E402

PDF = os.path.join(ROOT, "论文样本", "1706.03762v7.pdf")
problems = []


def _fail(tag, msg):
    print("  ✗ %s：%s" % (tag, msg))
    problems.append(tag)


def _ok(tag, msg=""):
    print("  ✅ %s%s" % (tag, ("：" + msg) if msg else ""))


def _render_html():
    doc = parsing.open_doc(PDF)
    try:
        html, _pw, _ph = native_page_html(doc, 1)
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return html


def _hover_bg(page):
    """悬停第一个 .blk，返回其计算背景色与 box-shadow。"""
    page.locator(".blk").first.hover()
    page.wait_for_timeout(120)
    return page.evaluate(
        "() => { const el = document.querySelector('.blk');"
        " const s = getComputedStyle(el);"
        " return {bg: s.backgroundColor, shadow: s.boxShadow, color: s.color}; }")


def main():
    html = _render_html()
    if ".blk:hover" not in html:
        _fail("规则存在", "渲染产物中找不到 .blk:hover 规则")
        return
    _ok("规则存在", "渲染产物含 .blk:hover")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---------- A. 修复后：半透明 + 描边 ----------
        page = browser.new_page(viewport={"width": 900, "height": 1000})
        page.set_content(html, wait_until="load")
        st = _hover_bg(page)
        print("  修复后计算样式:", st)
        bg = st["bg"]
        if bg.startswith("rgba("):
            alpha = float(bg.rsplit(",", 1)[1].strip(" )"))
            if alpha < 1.0:
                _ok("半透明高亮", "背景 alpha=%.2f（内容可透见）" % alpha)
            else:
                _fail("半透明高亮", "背景仍不透明：%s" % bg)
        else:
            _fail("半透明高亮", "背景不是 rgba 形式：%s" % bg)
        if "inset" in (st["shadow"] or ""):
            _ok("细描边", "box-shadow 含 inset 描边")
        else:
            _fail("细描边", "缺少 inset 描边：%s" % st["shadow"])
        page.close()

        # ---------- B. 复现 bug：注入旧的不透明规则 ----------
        old_css = "<style>.blk:hover{background:#fff3b0 !important;box-shadow:none !important;}</style>"
        page = browser.new_page(viewport={"width": 900, "height": 1000})
        page.set_content(html.replace("</head>", old_css + "</head>"), wait_until="load")
        st_old = _hover_bg(page)
        print("  旧规则计算样式:", st_old)
        if st_old["bg"] in ("rgb(255, 243, 176)", "rgb(255,243,176)"):
            _ok("bug 复现", "旧规则悬停底色为不透明 #fff3b0（整片糊住）")
        else:
            _fail("bug 复现", "旧规则底色异常：%s" % st_old["bg"])
        page.close()

        # ---------- C. .mk 标题高亮不受影响 ----------
        page = browser.new_page(viewport={"width": 900, "height": 1000})
        page.set_content(html, wait_until="load")
        mk_bg = page.evaluate(
            "() => { const el = document.querySelector('.mk');"
            " return el ? getComputedStyle(el).backgroundColor : null; }")
        if mk_bg is None:
            _ok(".mk 高亮", "该页无标题命中样本，跳过（不视为失败）")
        elif mk_bg in ("rgb(255, 224, 138)", "rgb(255,224,138)"):
            _ok(".mk 高亮", "T2 标题高亮仍为不透明 #ffe08a")
        else:
            _fail(".mk 高亮", "背景色被改动：%s" % mk_bg)
        page.close()

        # ---------- D. 悬停不改变文字颜色 ----------
        page = browser.new_page(viewport={"width": 900, "height": 1000})
        page.set_content(html, wait_until="load")
        before = page.evaluate(
            "() => getComputedStyle(document.querySelector('.blk .ln span')"
            " || document.querySelector('.blk')).color")
        page.locator(".blk").first.hover()
        page.wait_for_timeout(120)
        after = page.evaluate(
            "() => getComputedStyle(document.querySelector('.blk .ln span')"
            " || document.querySelector('.blk')).color")
        if before == after:
            _ok("文字颜色稳定", before)
        else:
            _fail("文字颜色稳定", "悬停前后文字颜色变化：%s -> %s" % (before, after))
        page.close()

        browser.close()

    print("\n结果：%s（%d 项失败）" % ("全部通过" if not problems else "存在失败", len(problems)))
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()

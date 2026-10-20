"""桌面版侧边栏展开入口修复的浏览器级验证（Round 13，2026-10-20）。

为什么需要这个脚本：tests/test_desktop_chrome.py 只能断言 CSS 字符串里"写了放行规则"，
证明不了真实浏览器引擎里 visibility 继承链覆盖真的生效、按钮真的可点击。
本脚本用无头 Chromium 加载「按 Streamlit 1.63 前端结构复刻的 DOM」+
「ui/desktop_chrome.py 的真实 _CSS」，逐项断言计算样式、几何位置与点击命中。

DOM 结构依据（streamlit 1.63.0 静态包 index.*.js 的 hb 组件，已逐层核对）：
    stHeader(lb) → stToolbar(ub) → fb → pb → db → stExpandSidebarButton(Yr)
    且 d = hasSidebar && !isSidebarOpen → 按钮仅在侧边栏收起态才存在于 DOM。

场景：
  A. 修复后（现行 _CSS）        → 按钮可见、可点击、悬停有底色；header/toolbar 仍隐藏
  B. 修复前（git 历史版本 CSS） → 按钮被继承埋掉（复现用户反馈的 bug，截图取证）
  C. 展开态（按钮不在 DOM）     → 放行规则不泄漏、header 仍隐藏
  D. 旧版 testid 兼容           → stSidebarCollapsedControl 同样被放行

截图输出 docs/shots/round13_sidebar_expand_{bug,fixed}.png——
注意图注：这是按前端结构复刻的 DOM，不是应用真实截图（合成 DOM + 真实 CSS）。
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.desktop_chrome import _CSS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "docs", "shots")

# 修复前版本（git 历史 verbatim）：整段隐藏 header/toolbar，无放行规则。
# 故意硬编码而非现场 git show：修复提交后 HEAD 即是新版，现场取会把"bug 现场"洗掉。
OLD_CSS = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header[data-testid="stHeader"] {visibility: hidden;}
[data-testid="stToolbar"] {visibility: hidden;}
[data-testid="stStatusWidget"] {visibility: hidden;}
.stDeployButton {visibility: hidden;}
[data-testid="stDecoration"] {visibility: hidden;}
.block-container {padding-top: 1.2rem; padding-bottom: 1rem;}
</style>
"""

# 模拟 Streamlit 默认布局的关键定位样式（header 固定顶部；其余视觉从简）
_LAYOUT = """
[data-testid="stHeader"] {position: fixed; top:0; left:0; right:0; height:3rem; z-index:999991;}
[data-testid="stToolbar"] {display:flex; align-items:center; height:100%; padding:0 .75rem;}
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] {
    border:none; background:none; cursor:pointer; font-size:1.5rem;
    padding:.25rem .5rem; color:#8a97a8;
}
.stAppViewContainer {padding-top:3rem;}
body {margin:0; font-family:sans-serif; background:#f7f9fc; color:#1d2733;}
main {padding:2rem;}
.card {background:#eef4fb; border-radius:8px; padding:1rem; margin-top:1rem;}
"""


def _page(css: str, expand_testid: str | None) -> str:
    """按 1.63 结构拼 DOM。expand_testid=None 表示侧边栏展开态（按钮不在 DOM）。"""
    if expand_testid == "stExpandSidebarButton":
        btn = '<button data-testid="stExpandSidebarButton" id="expandBtn">&raquo;</button>'
    elif expand_testid == "stSidebarCollapsedControl":
        btn = '<button data-testid="stSidebarCollapsedControl" id="expandBtn">&raquo;</button>'
    else:
        btn = ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_LAYOUT}</style>{css}</head><body>
<div class="stAppViewContainer" data-testid="stAppViewContainer">
  <header class="stAppHeader" data-testid="stHeader">
    <div class="stAppToolbar" data-testid="stToolbar">
      <div>
        <div>
          <div data-testid="stHeaderLogo"><span>LOGO</span></div>
          <div>{btn}</div>
        </div>
        <div data-testid="stToolbarNavigation"><span>NAV-DECOY</span></div>
        <div data-testid="stToolbarRightContent"><span>RIGHT-DECOY</span></div>
      </div>
    </div>
  </header>
  <main>
    <h2>AI4S 科研助手（DOM 复刻页）</h2>
    <div class="card">主区域内容：修复只影响左上角展开按钮的可见性，不得波及此处。</div>
  </main>
</div>
<script>
window.__clicks = 0;
var b = document.getElementById('expandBtn');
if (b) b.addEventListener('click', function () {{ window.__clicks += 1; }});
</script>
</body></html>"""


def _vis(page, selector: str) -> str:
    return page.evaluate(
        "s => getComputedStyle(document.querySelector(s)).visibility", selector)


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    failures = []

    def check(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
        if not cond:
            failures.append(name)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        # ---------- A. 修复后 ----------
        page.set_content(_page(_CSS, "stExpandSidebarButton"))
        btn = '[data-testid="stExpandSidebarButton"]'
        check("A1 修复后按钮计算样式可见", _vis(page, btn) == "visible", _vis(page, btn))
        check("A2 header 仍隐藏（未整体放行）", _vis(page, '[data-testid="stHeader"]') == "hidden")
        check("A3 toolbar 仍隐藏", _vis(page, '[data-testid="stToolbar"]') == "hidden")
        check("A4 logo 装饰仍隐藏", _vis(page, '[data-testid="stHeaderLogo"]') == "hidden")
        check("A5 右侧装饰仍隐藏", _vis(page, '[data-testid="stToolbarRightContent"]') == "hidden")
        check("A6 主区域不受影响", _vis(page, "main") == "visible")
        check("A7 按钮在视口内且位于左上角",
              page.evaluate("() => { const r = document.querySelector('%s').getBoundingClientRect();"
                            " return r.width > 0 && r.height > 0 && r.left < 80 && r.top < 80; }" % btn))
        page.click(btn)  # visibility:hidden 的元素 click 会超时——能点成功即证明可交互
        check("A8 点击命中处理器", page.evaluate("() => window.__clicks") == 1)
        page.hover(btn)
        check("A9 悬停底色生效（主题色 8% 不透明度）",
              page.evaluate("() => getComputedStyle(document.querySelector('%s')).backgroundColor" % btn)
              == "rgba(31, 95, 174, 0.08)")
        page.screenshot(path=os.path.join(SHOTS, "round13_sidebar_expand_fixed.png"))

        # ---------- B. 修复前（复现 bug） ----------
        page.set_content(_page(OLD_CSS, "stExpandSidebarButton"))
        check("B1 修复前按钮被继承埋掉（复现用户反馈）",
              _vis(page, btn) == "hidden", _vis(page, btn))
        check("B2 修复前按钮不可见（is_visible=False）", page.is_visible(btn) is False)
        page.screenshot(path=os.path.join(SHOTS, "round13_sidebar_expand_bug.png"))

        # ---------- C. 展开态（按钮不在 DOM） ----------
        page.set_content(_page(_CSS, None))
        check("C1 展开态无展开按钮节点",
              page.evaluate("() => document.querySelectorAll("
                            "'[data-testid=\"stExpandSidebarButton\"],[data-testid=\"stSidebarCollapsedControl\"]').length") == 0)
        check("C2 展开态 header 仍隐藏", _vis(page, '[data-testid="stHeader"]') == "hidden")
        check("C3 展开态主区域可见", _vis(page, "main") == "visible")

        # ---------- D. 旧版 testid 兼容 ----------
        page.set_content(_page(_CSS, "stSidebarCollapsedControl"))
        legacy = '[data-testid="stSidebarCollapsedControl"]'
        check("D1 旧版 testid 同样放行", _vis(page, legacy) == "visible", _vis(page, legacy))
        page.click(legacy)
        check("D2 旧版 testid 可点击", page.evaluate("() => window.__clicks") == 1)

        browser.close()

    print("-" * 60)
    if failures:
        print(f"结果：{len(failures)} 项失败 -> {failures}")
        return 1
    print("结果：全部通过（修复后可见可点 / 修复前复现 bug / 展开态不泄漏 / 旧版兼容）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""桌面版 UI 外壳：注入 CSS 隐藏 Streamlit 默认菜单 / 页脚 / 工具栏，收紧主区边距。

仅在桌面模式（AI4S_DESKTOP=1 或 PyInstaller 冻结态）生效，开发浏览器模式零影响。
整体 try/except 包裹，任何异常都不得影响页面渲染。

⚠️ 历史坑（Round 13，2026-10-20 用户实测反馈「侧边栏收起之后还是没找到展开的选项」）：
侧边栏收起后唯一的图形展开入口是 stHeader → stToolbar 内的展开按钮
（Streamlit 1.63 testid = stExpandSidebarButton；≤1.62 旧版 = stSidebarCollapsedControl，
已从 1.63.0 前端包 index.*.js 的 hb 组件逐层确认该 DOM 层级）。
本模块原来整段隐藏 header / toolbar，把该按钮连带埋掉 → 收起即永久失去入口。
故下方必须显式放行它：visibility 与 display 不同，后代可用 visible 覆盖继承值；
且该按钮仅在侧边栏收起时才存在于 DOM，规则天然自带条件，不会污染展开态。
改这里前先读 tests/test_desktop_chrome.py——那三条回归断言就是守这个坑的。
"""
import os

import streamlit as st

try:
    from config import DESKTOP_MODE
except Exception:                       # pragma: no cover - import 异常时退化为环境变量判定
    DESKTOP_MODE = False

_CSS = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header[data-testid="stHeader"] {visibility: hidden;}
[data-testid="stToolbar"] {visibility: hidden;}
[data-testid="stStatusWidget"] {visibility: hidden;}
.stDeployButton {visibility: hidden;}
[data-testid="stDecoration"] {visibility: hidden;}
.block-container {padding-top: 1.2rem; padding-bottom: 1rem;}
/* 放行侧边栏展开按钮（Round 13 事故修复）：收起态唯一入口，埋在 stHeader/stToolbar 内，
   上面两条 visibility:hidden 会沿继承链把它一起埋掉。
   · 用 visibility:visible 覆盖而非删掉隐藏规则——展开态下该按钮不在 DOM 中，无需条件判断；
   · 绝不可把上面两条改成 display:none：display 不继承、后代无法覆盖，这个修复会静默失效；
   · 双 testid 兼容：stExpandSidebarButton 为 1.63 现行，stSidebarCollapsedControl 为旧版残留。 */
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] {
    visibility: visible !important;
}
/* 悬停底色（取主题 primaryColor #1f5fae）：默认只是个灰色箭头，用户实测"找不到"，
   给一点可点击暗示，避免它看起来像装饰性图标。 */
[data-testid="stExpandSidebarButton"]:hover,
[data-testid="stSidebarCollapsedControl"]:hover {
    background: rgba(31, 95, 174, 0.08);
    border-radius: 6px;
}
</style>
"""


def apply():
    """桌面模式注入外壳 CSS；开发模式直接返回，失败静默忽略。"""
    try:
        desktop = DESKTOP_MODE or os.environ.get("AI4S_DESKTOP") == "1"
        if not desktop:
            return
        st.markdown(_CSS, unsafe_allow_html=True)
    except Exception:
        pass

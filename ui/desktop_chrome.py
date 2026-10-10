"""桌面版 UI 外壳：注入 CSS 隐藏 Streamlit 默认菜单 / 页脚 / 工具栏，收紧主区边距。

仅在桌面模式（AI4S_DESKTOP=1 或 PyInstaller 冻结态）生效，开发浏览器模式零影响。
整体 try/except 包裹，任何异常都不得影响页面渲染。
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

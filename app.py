"""AI4S 科研助手 Agent —— Streamlit 入口。

运行：streamlit run app.py   （或双击「启动科研助手.bat」）
架构：交互层(ui) → Agent 内核(core) → 工具/服务层(tools/services) → 存储层(storage)。
"""
import streamlit as st

from services.state import get_container
from ui.sidebar import render as render_sidebar
from ui.common import safe_render
from ui import page_agent, page_library, page_qa, page_reading, page_search, page_graph, page_writing, page_misc
from ui import desktop_chrome

st.set_page_config(page_title="AI4S 科研助手 Agent", page_icon="🔬", layout="wide")
desktop_chrome.apply()

ctx = get_container()
menu = render_sidebar(ctx)

_PAGES = {
    "🤖": (page_agent.render, "Agent 总助手"),
    "📁": (page_library.render, "文献库管理"),
    "💬": (page_qa.render, "问答与溯源"),
    "📖": (page_reading.render, "版式阅读与翻译"),
    "🔍": (page_search.render, "检索与追踪"),
    "🕸️": (page_graph.render, "引用图谱与综述"),
    "✍️": (page_writing.render, "写作辅助与可视化"),
    "🏗️": (page_misc.render_arch, "系统架构"),
    "💡": (page_misc.render_innov, "创新亮点"),
}

for prefix, (fn, name) in _PAGES.items():
    if menu.startswith(prefix):
        safe_render(fn, ctx, name)
        break

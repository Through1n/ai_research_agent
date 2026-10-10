"""侧边栏：API Key、用户、导航菜单、演示数据加载、清空文献库。"""
import streamlit as st

from config import DEMO_PAPERS
from services import library
from ui.common import memory_mb

MENU = [
    "🤖 0. Agent 总助手",
    "📁 1. 文献库管理",
    "💬 2. 跨文献问答与溯源",
    "📖 3. 版式阅读与智能翻译",
    "🔍 4. 论文自动检索与追踪",
    "🕸️ 5. 引用图谱与综述",
    "✍️ 6. 写作辅助与可视化",
    "🏗️ 系统架构设计",
    "💡 技术创新亮点",
]


def render(ctx):
    with st.sidebar:
        st.title("🔬 AI4S 科研助手")
        if "saved_api_key" not in st.session_state:
            st.session_state.saved_api_key = ""
        key = st.text_input("智谱 API Key（GLM）", type="password",
                            value=st.session_state.saved_api_key).strip()
        st.session_state.saved_api_key = key
        if ctx.set_api_key(key):
            st.success("✅ API Key 已加载")
        else:
            st.caption("未填 Key：可使用内置离线演示数据")

        st.markdown("---")
        ctx.memory.user = st.text_input("当前用户 ID", value=ctx.memory.user)
        prof = ctx.memory.profile()
        if prof:
            st.caption("📊 画像：" + "，".join(f"{k}×{v}" for k, v in prof.items()))

        st.markdown("---")
        qp = st.query_params
        if "p" in qp:
            try:
                idx = int(qp["p"])
                if 0 <= idx < len(MENU):
                    st.session_state["menu"] = MENU[idx]
            except (TypeError, ValueError):
                pass

        def _sync_menu():
            st.query_params["p"] = MENU.index(st.session_state["menu"])

        menu = st.radio("导航菜单", MENU, key="menu", on_change=_sync_menu)

        st.markdown("---")
        if st.button("🚀 一键加载演示数据（v1/v7/BERT）"):
            with st.spinner("加载中…"):
                for fn, info in DEMO_PAPERS.items():
                    if fn not in ctx.files:
                        library.add_text_corpus(ctx, fn, info["text"], info)
                st.success(f"✅ 已就绪，当前 {len(ctx.files)} 个文件")
                st.rerun()
        if st.button("🗑️ 清空文献库"):
            for fn in list(ctx.files):
                library.remove_paper(ctx, fn)
            st.success("已清空"); st.rerun()
        st.caption(f"💾 内存 {memory_mb()}")
    return menu

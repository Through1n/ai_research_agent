"""侧边栏：模型 Provider、API Key、用户、导航菜单、演示数据加载、清空文献库。"""
import streamlit as st

from config import DEMO_PAPERS
from core.providers import list_providers, get_provider
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

        # ---- 大模型 Provider 切换（OpenAI 兼容协议，一套代码多家模型） ----
        provs = list_providers()
        labels = [f"{p.label}" for p in provs]
        sel = st.selectbox(
            "大模型 Provider", labels,
            index=max(0, [p.id for p in provs].index(ctx.provider_id)
                      if ctx.provider_id in [p.id for p in provs] else 0),
            help="全部走 OpenAI 兼容协议；DeepSeek/Ollama 无视觉模型，扫描件 OCR 自动降级"
        )
        pid = provs[labels.index(sel)].id
        p = get_provider(pid)
        st.caption(("🖼️ 支持多模态 OCR：" + p.models_hint) if p.has_vision
                   else "📵 无多模态：扫描件 OCR 自动降级为离线解析")
        if pid != ctx.provider_id:
            ctx.set_provider(pid, st.session_state.get("saved_api_key", ""))

        if "saved_api_key" not in st.session_state:
            st.session_state.saved_api_key = ""
        key = st.text_input(f"{p.label.split('（')[0]} API Key",
                            type="password", value=st.session_state.saved_api_key,
                            key="key_input").strip()
        st.session_state.saved_api_key = key
        if ctx.set_provider(pid, key):
            st.success("✅ 模型已就绪")
        elif not p.needs_key:
            st.caption("本地 Ollama 需先运行 `ollama serve` 并拉取模型")
        else:
            st.caption("未填 Key：可使用内置离线演示数据")
        if p.hint:
            st.caption("💡 " + p.hint)

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

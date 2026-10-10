"""侧边栏：大模型接入（预设五家 / 自定义任意端点）、API Key、用户、导航菜单、演示数据。"""
import os

import streamlit as st

from config import DEMO_PAPERS, DESKTOP_MODE
from core.providers import list_providers, get_provider, custom_provider, CUSTOM_ID
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

        # 桌面版首次启动提示：无 API Key 时引导在下方「接入方式」配置（不弹窗、不跳转）
        if DESKTOP_MODE and not os.getenv("ZHIPU_API_KEY") and not os.getenv("LLM_API_KEY") \
                and not st.session_state.get("saved_api_key"):
            with st.container(border=True):
                st.warning("📌 尚未配置 API Key：请在下方「接入方式」选择平台并填入 Key；无 Key 也可使用底部按钮一键加载离线演示数据。")

        # ================= 大模型接入（OpenAI 兼容协议，任意模型热切换） =================
        provs = list_providers()
        mode = st.radio("接入方式", ["🏢 预设平台", "➕ 自定义端点（任意模型）"], horizontal=False)

        if mode.startswith("➕"):
            st.caption("填写任意 OpenAI 兼容端点即可接入（vLLM / LM Studio / 硅基流动 / 校内网关…）")
            c_base = st.text_input("Base URL", key="cu_base",
                                   placeholder="如 http://10.0.0.8:8000/v1")
            c_model = st.text_input("对话模型名", key="cu_model", placeholder="如 qwen2.5-14b-instruct")
            c_vl = st.text_input("视觉模型名（可留空 = 无多模态）", key="cu_vl", placeholder="如 qwen2.5-vl-7b")
            c_needs = st.checkbox("该端点需要 API Key", key="cu_needs", value=True)
            spec = {"base_url": c_base, "model": c_model, "vl_model": c_vl, "needs_key": c_needs}
            key = st.text_input("API Key（免密端点可不填）", type="password",
                                value=st.session_state.get("saved_api_key", ""), key="cu_key").strip()
            st.session_state.saved_api_key = key
            if c_base.strip() and c_model.strip():
                ok = ctx.set_provider(CUSTOM_ID, key, custom_spec=spec)
                p = custom_provider(spec)
                st.caption(("🖼️ 多模态 OCR 可用：" + p.models_hint) if p.has_vision
                           else "📵 未填视觉模型：OCR 自动降级")
                if ok:
                    st.success(f"✅ 已接入 {c_model}")
                else:
                    st.caption("⚠️ 请填写端点与模型名后生效")
            else:
                st.caption("⚠️ 请至少填写 Base URL 与对话模型名")
        else:
            labels = [p.label for p in provs]
            sel = st.selectbox(
                "选择平台", labels,
                index=max(0, [p.id for p in provs].index(ctx.provider_id)
                          if ctx.provider_id in [p.id for p in provs] else 0),
                help="全部走 OpenAI 兼容协议；DeepSeek/Ollama 无视觉模型，扫描件 OCR 自动降级"
            )
            pid = provs[labels.index(sel)].id
            p = get_provider(pid)
            st.caption(("🖼️ 支持多模态 OCR：" + p.models_hint) if p.has_vision
                       else "📵 无多模态：扫描件 OCR 自动降级为离线解析")
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
        st.caption("桌面版 v1.0")
    return menu

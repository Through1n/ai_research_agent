"""页面 2：抗幻觉跨文献问答。混合检索 + 重排，答案内联【n】引用，下方给出编号溯源锚点。"""
import streamlit as st
from services import rag
from ui.common import source_card, require_library, page_header


EX = ["这篇论文提出了什么模型？", "训练用了什么 GPU？数量和型号？",
      "对比 Transformer 与 BERT 的架构（表格）", "GPT/BERT/Transformer 参数量演化",
      "Adam 的 beta_1、beta_2 是多少？BLEU 分数分别是多少？"]


def render(ctx):
    page_header("💬 跨文献问答与溯源", "Dense+Sparse 混合检索 → RRF 融合 → 可选 LLM 重排；每个结论带【n】内联引用与原文锚点。")
    require_library(ctx)
    st.session_state.setdefault("qa_runs", [])
    st.session_state.setdefault("qa_q", "")

    cols = st.columns(3)
    for i, e in enumerate(EX):
        if cols[i % 3].button(e, key=f"ex{i}"):
            st.session_state.qa_q = e; st.rerun()

    use_rerank = st.checkbox("启用 LLM 重排（更精准，耗额度）")

    with st.form("qa_form", clear_on_submit=True):
        q = st.text_input("请输入问题", value=st.session_state.qa_q)
        go = st.form_submit_button("🔎 提问")

    if go and q.strip():
        if not ctx.llm.available:
            st.warning("请先在左侧填入 API Key。"); return
        # 统一走 rag.answer：内部已完成检索→生成→引用校验→一次自动修复
        with st.spinner("正在检索、生成并校验答案…"):
            result = rag.answer(ctx, q.strip(), use_rerank=use_rerank)
        st.session_state.qa_runs.append({
            "q": q.strip(),
            "a": result["answer"],          # 修复后的答案（若无需修复即原答案）
            "sources": result["sources"],
            "issues": result["issues"],
            "debug": result["debug"],
        })
        st.session_state.qa_q = ""
        st.rerun()

    for run in reversed(st.session_state.qa_runs):
        st.markdown(f"#### 🙋 {run['q']}")
        st.markdown(run["a"])
        if run["issues"]:
            st.caption("⚠️ 引用校验：" + "；".join(run["issues"]))
        else:
            st.success("✅ 引用校验通过：所有【n】角标均在有效来源范围内")
        with st.expander(f"🔍 溯源（{len(run['sources'])} 条来源）"):
            for i, c in enumerate(run["sources"], 1):
                st.markdown(source_card(c["source"], c["page"], c["text"], n=i), unsafe_allow_html=True)
        with st.expander("🛠️ 检索调试"):
            d = run["debug"]
            st.caption(f"向量召回 {len(d['dense'])} 条，BM25 召回 {len(d['sparse'])} 条，RRF 融合后 {len(d['fused'])} 条")
            if d.get("variants"):
                st.caption("🌐 跨语言查询扩展（供 BM25 使用）：" + " ┃ ".join(d["variants"]))
            hit = set(d["sparse"]) & set(d["dense"])
            if hit:
                st.caption(f"✅ 双路共同命中 {len(hit)} 条——混合检索交叉验证降低漏检")
        st.markdown("---")

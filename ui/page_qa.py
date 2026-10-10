"""页面 2：抗幻觉跨文献问答。混合检索 + 重排，答案内联【n】引用，下方给出编号溯源锚点，支持流式。"""
import streamlit as st
from services import rag
from core.guardrails import validate_citations
from ui.common import source_card, require_library


EX = ["这篇论文提出了什么模型？", "训练用了什么 GPU？数量和型号？",
      "对比 Transformer 与 BERT 的架构（表格）", "GPT/BERT/Transformer 参数量演化",
      "Adam 的 beta_1、beta_2 是多少？BLEU 分数分别是多少？"]


def render(ctx):
    st.header("💬 跨文献问答与溯源")
    st.caption("Dense+Sparse 混合检索 → RRF 融合 → 可选 LLM 重排；每个结论带【n】内联引用与原文锚点。")
    require_library(ctx)
    st.session_state.setdefault("qa_runs", [])
    st.session_state.setdefault("qa_q", "")

    cols = st.columns(3)
    for i, e in enumerate(EX):
        if cols[i % 3].button(e, key=f"ex{i}"):
            st.session_state.qa_q = e; st.rerun()

    opt1, opt2 = st.columns(2)
    use_rerank = opt1.checkbox("启用 LLM 重排（更精准，耗额度）")
    use_stream = opt2.checkbox("Token 级流式输出")

    with st.form("qa_form", clear_on_submit=True):
        q = st.text_input("请输入问题", value=st.session_state.qa_q)
        go = st.form_submit_button("🔎 提问")

    if go and q.strip():
        if not ctx.llm.available:
            st.warning("请先在左侧填入 API Key。"); return
        sources, debug = rag.retrieve(ctx, q.strip(), use_rerank=use_rerank)
        if not sources:
            st.warning("未检索到相关内容，请先导入论文。"); return
        context = rag.build_context(sources)
        msgs = rag.qa_messages(q.strip(), context)
        if use_stream:
            text = st.write_stream(ctx.llm.stream(msgs))
        else:
            m = ctx.llm.chat(msgs, temperature=0.1)
            text = m.content or ""
        issues = validate_citations(text, len(sources))
        st.session_state.qa_runs.append({"q": q.strip(), "a": text, "sources": sources,
                                         "issues": issues, "debug": debug})
        st.session_state.qa_q = ""
        st.rerun()

    for run in reversed(st.session_state.qa_runs):
        st.markdown(f"#### 🙋 {run['q']}")
        st.markdown(run["a"])
        if run["issues"]:
            st.caption("⚠️ 引用校验：" + "；".join(run["issues"]))
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

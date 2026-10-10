"""页面 5：局域引用图谱与学术综述。真实引用关系、图论基石/边缘挖掘、自动综述、Future Work 前沿过滤。"""
import pandas as pd
import streamlit as st

from services import graph as g
from ui.common import embed_html, require_library, page_header, render_styled_table


def render(ctx):
    page_header("🕸️ 引用图谱与综述", "自动解析论文间真实引用关系，PageRank/中心性挖掘基石论文，结合时间线过滤已被解决的旧 idea。")
    st.session_state.setdefault("graph_pack", None)

    if not require_library(ctx):
        return

    if st.button("🕸️ 构建引用图谱"):
        try:
            bar = st.progress(0.0, text="解析引用关系…")
            G, edges = g.build_graph(ctx, progress_cb=lambda r: bar.progress(min(r, 1.0)))
            bar.progress(1.0, text="图论指标计算…")
            metrics = g.analyze(G)
            corner, edge = g.classify_papers(G, metrics)
            html = g.graph_html(G, metrics, corner)
            bar.empty()
            st.session_state.graph_pack = {
                "html": html, "metrics": metrics, "corner": corner, "edge": edge,
                "edges": edges, "nodes": list(G.nodes)}
            if not edges:
                st.info("未检测到文献间的真实引用关系。提示：需导入存在引用关系的论文（如 BERT 与 Transformer）。")
        except Exception as e:
            st.error(f"构建引用图谱失败：{str(e)[:160]}")

    pack = st.session_state.get("graph_pack")
    if pack:
        embed_html(pack["html"], height=560)
        m = pack["metrics"]
        df = pd.DataFrame({
            "论文": pack["nodes"],
            "PageRank": [round(m["pagerank"].get(n, 0), 4) for n in pack["nodes"]],
            "被引次数(入度)": [m["indegree"].get(n, 0) for n in pack["nodes"]],
            "中介中心性": [round(m["betweenness"].get(n, 0), 3) for n in pack["nodes"]],
            "特征向量": [round(m["eigenvector"].get(n, 0), 4) for n in pack["nodes"]],
        }).sort_values("PageRank", ascending=False)
        st.dataframe(df, width="stretch", hide_index=True)

        def _title(n):
            p = ctx.papers.get(n, {})
            return p.get("title", n)

        if pack["corner"]:
            st.success("🏆 基石论文：" + "、".join(f"《{_title(n)}》" for n in pack["corner"]))
        if pack["edge"]:
            st.caption("🌿 边缘衍生（暂未被他文引用）：" + "、".join(
                f"《{_title(n)}》" for n in pack["edge"]))
        with st.expander(f"🔗 引用关系（{len(pack['edges'])}）"):
            for a, b, ev in pack["edges"]:
                st.markdown(f"《{_title(a)}》 → 引用 → 《{_title(b)}》")
                st.caption(ev[:120])

    st.markdown("---")
    c1, c2 = st.columns(2)
    if c1.button("📝 一键生成领域综述") and ctx.llm.available:
        try:
            with st.spinner("📝 正在生成领域综述…"):
                G, edges = g.build_graph(ctx); metrics = g.analyze(G)
                corner, edge = g.classify_papers(G, metrics)
                st.session_state["review"] = g.generate_review(ctx, G, metrics, corner)
        except Exception as e:
            st.error(f"生成综述失败：{str(e)[:160]}")
    if c2.button("🚀 Future Work 前沿扫描（排除已解决）") and ctx.llm.available:
        try:
            with st.spinner("🚀 正在扫描前沿方向…"):
                G, _ = g.build_graph(ctx)
                st.session_state["future"] = g.explore_future(ctx, G)
        except Exception as e:
            st.error(f"前沿扫描失败：{str(e)[:160]}")
    review_txt = st.session_state.get("review", "")
    future_txt = st.session_state.get("future", "")
    if review_txt:
        st.markdown("##### 📖 领域综述"); st.markdown(review_txt)
    if future_txt:
        st.markdown("##### 🚀 前沿方向"); st.markdown(future_txt)

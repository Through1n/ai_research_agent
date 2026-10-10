"""页面 5：局域引用图谱与学术综述。真实引用关系、图论基石/边缘挖掘、自动综述、Future Work 前沿过滤。"""
import pandas as pd
import streamlit as st

from services import graph as g
from ui.common import embed_html, require_library


def render(ctx):
    st.header("🕸️ 引用图谱与综述")
    st.caption("自动解析论文间真实引用关系，PageRank/中心性挖掘基石论文，结合时间线过滤已被解决的旧 idea。")
    st.session_state.setdefault("graph_pack", None)

    if not require_library(ctx):
        return

    if st.button("🕸️ 构建引用图谱"):
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

    pack = st.session_state.graph_pack
    if pack:
        embed_html(pack["html"], height=560)
        m = pack["metrics"]
        df = pd.DataFrame({
            "论文": pack["nodes"],
            "PageRank": [round(m["pagerank"][n], 4) for n in pack["nodes"]],
            "被引次数(入度)": [m["indegree"][n] for n in pack["nodes"]],
            "中介中心性": [round(m["betweenness"][n], 3) for n in pack["nodes"]],
            "特征向量": [round(m["eigenvector"][n], 4) for n in pack["nodes"]],
        }).sort_values("PageRank", ascending=False)
        st.dataframe(df, use_container_width=True, hide_index=True)

        if pack["corner"]:
            st.success("🏆 基石论文：" + "、".join(f"《{ctx.papers[n]['title']}》" for n in pack["corner"]))
        if pack["edge"]:
            st.caption("🌿 边缘衍生（暂未被他文引用）：" + "、".join(
                f"《{ctx.papers[n]['title']}》" for n in pack["edge"]))
        with st.expander(f"🔗 引用关系（{len(pack['edges'])}）"):
            for a, b, ev in pack["edges"]:
                st.markdown(f"《{ctx.papers[a]['title']}》 → 引用 → 《{ctx.papers[b]['title']}》")
                st.caption(ev[:120])

    st.markdown("---")
    c1, c2 = st.columns(2)
    if c1.button("📝 一键生成领域综述") and ctx.llm.available:
        G, edges = g.build_graph(ctx); metrics = g.analyze(G)
        corner, edge = g.classify_papers(G, metrics)
        st.session_state["review"] = g.generate_review(ctx, G, metrics, corner)
    if c2.button("🚀 Future Work 前沿扫描（排除已解决）") and ctx.llm.available:
        G, _ = g.build_graph(ctx)
        st.session_state["future"] = g.explore_future(ctx, G)
    if st.session_state.get("review"):
        st.markdown("##### 📖 领域综述"); st.markdown(st.session_state["review"])
    if st.session_state.get("future"):
        st.markdown("##### 🚀 前沿方向"); st.markdown(st.session_state["future"])

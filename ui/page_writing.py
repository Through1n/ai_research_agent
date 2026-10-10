"""页面 6：学术写作与数据可视化 Copilot。CSV→图表+Figure Caption、无幻觉框架、四类拓扑图、中英学术翻译。"""
import streamlit as st
from ui.common import embed_html
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from services import writing


def _csv_section(ctx):
    st.subheader("📊 CSV 数据可视化")
    cf = st.file_uploader("上传 CSV", type=["csv"], key="csv")
    if cf:
        st.session_state.df = pd.read_csv(cf)
    df = st.session_state.get("df")
    if df is None:
        return
    st.write(df.head())
    cols = df.columns.tolist()
    c1, c2, c3 = st.columns(3)
    x = c1.selectbox("X", cols)
    y = c2.selectbox("Y", cols, index=min(1, len(cols) - 1))
    ct = c3.selectbox("图表", ["折线图", "柱状图", "散点图", "三维雷达图", "相关性热力图", "平行坐标图"])
    tpl = "plotly_white"
    if ct == "折线图":
        st.plotly_chart(px.line(df, x=x, y=y, template=tpl), use_container_width=True)
    elif ct == "柱状图":
        st.plotly_chart(px.bar(df, x=x, y=y, template=tpl), use_container_width=True)
    elif ct == "散点图":
        st.plotly_chart(px.scatter(df, x=x, y=y, template=tpl), use_container_width=True)
    elif ct == "三维雷达图":
        nums = df.select_dtypes(include="number").columns.tolist()
        if len(nums) >= 3:
            fg = go.Figure()
            for c in nums[:5]:
                fg.add_trace(go.Scatterpolar(r=df[c], theta=[f"{c}"] * len(df), fill="toself", name=str(c)))
            st.plotly_chart(fg, use_container_width=True)
    elif ct == "相关性热力图":
        nums = df.select_dtypes(include="number")
        if len(nums.columns) >= 2:
            st.plotly_chart(px.imshow(nums.corr(), text_auto=True, aspect="auto", template=tpl),
                            use_container_width=True)
    elif ct == "平行坐标图":
        nums = df.select_dtypes(include="number").columns.tolist()
        if len(nums) >= 3:
            st.plotly_chart(px.parallel_coordinates(df, dimensions=nums, template=tpl),
                            use_container_width=True)
    if st.button("🤖 自动提炼 Figure Caption") and ctx.llm.available:
        st.session_state.caption = writing.figure_caption(ctx, df, ct)
    if st.session_state.get("caption"):
        st.info("**Figure Caption:** " + st.session_state.caption)


def _outline_section(ctx):
    st.subheader("✍️ 无幻觉论文框架")
    idea = st.text_area("输入 Idea", key="idea")
    if st.button("🧱 生成框架（引用本地真实文献）") and idea and ctx.llm.available:
        st.session_state.outline = writing.generate_outline(ctx, idea)
    if st.session_state.get("outline"):
        st.markdown(st.session_state["outline"])


def _diagram_section(ctx):
    st.subheader("🏗️ 架构拓扑图脚本")
    c1, c2 = st.columns(2)
    kind = c1.selectbox("类型", ["mermaid", "tikz", "graphviz", "matplotlib"])
    topic = c2.text_input("主题", key="topic")
    if st.button("🎨 生成代码") and topic and ctx.llm.available:
        st.session_state.diagram = (kind, writing.diagram(ctx, kind, topic))
    if st.session_state.get("diagram"):
        k, code = st.session_state.diagram
        st.code(code, language=("mermaid" if k == "mermaid" else ("latex" if k == "tikz" else
              ("dot" if k == "graphviz" else "python"))))
        if k == "mermaid":
            embed_html(f'<div class="mermaid" style="display:flex;justify-content:center;">{code}</div>'
                            '<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";'
                            'mermaid.initialize({startOnLoad:true,flowchart:{useMaxWidth:false}});</script>',
                            height=600, scrolling=True)


def _translate_section(ctx):
    st.subheader("🌐 学术翻译")
    direction = st.radio("方向", ["中→英", "英→中"], horizontal=True)
    text = st.text_area("粘贴文本", key="trtext", height=160)
    if st.button("🔁 一键翻译") and text and ctx.llm.available:
        if direction == "中→英":
            st.session_state.trres = writing.translate_to_english(ctx, text)
        else:
            st.session_state.trres = writing.translate_to_chinese(ctx, text)
    if st.session_state.get("trres"):
        st.markdown(st.session_state["trres"])


def render(ctx):
    st.header("✍️ 学术写作与可视化")
    _csv_section(ctx)
    st.markdown("---"); _outline_section(ctx)
    st.markdown("---"); _diagram_section(ctx)
    st.markdown("---"); _translate_section(ctx)

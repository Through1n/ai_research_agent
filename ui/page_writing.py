"""页面 6：学术写作与数据可视化 Copilot。

本轮整改（对应 docs/audit_report_20261010.md 第五节 E 路）：
- 5.1 无幻觉 References：生成后**自动**跑白名单校验，幻觉用 st.error 明确列出并提供
  「重新生成（强化约束）」按钮；校验通过才 st.success；文献库为空时不生成占位引用；
- 5.2 TikZ：尝试本地编译成 PNG 真实渲染，无编译器时降级为源码 + 在线编辑器链接；
- 5.3 Graphviz：dot -Tsvg → networkx+matplotlib → 零依赖 DOT→SVG 三级降级渲染；
- 5.4 CSV 自动出图：上传即按列类型自动渲染推荐图表（附推荐依据），手动选择收进折叠区；
- 5.5 Figure Caption：注入实测数值事实，并对无数字的 caption 给出提示。

所有 session_state key 均在 ``_init_state`` 中先初始化再读；所有外部调用均 try/except，
失败给中文友好提示。
"""
import copy
import os
import re

import streamlit as st
from ui.common import embed_html, page_header
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from services import writing

# 所有本页用到的 session_state key 的默认值（先初始化再读，避免 KeyError）
_SS_DEFAULTS = {
    "df": None,
    "df_name": "",
    "chart_plan": None,
    "auto_captions": {},
    "caption": "",
    "idea": "",
    "topic": "",
    "trtext": "",
    "trdir": "中→英",
    "trres": "",
    "ref_source": "自动（文献库首篇）",
    "outline": "",
    "outline_meta": None,
    "diagram": None,
    "dg_kind": "mermaid",
    "tikz_cache": {},
    "dot_cache": {},
}


def _init_state():
    for k, v in _SS_DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = copy.deepcopy(v)


# ---------------------------------------------------------------- CSV 自动出图（T4）
def _render_plan_chart(df, plan):
    """按 plan 渲染一张 plotly 图；失败给中文提示，不抛异常。"""
    kind = plan.get("kind")
    x, y = plan.get("x"), plan.get("y")
    dims = plan.get("dims") or []
    tpl = "plotly_white"
    try:
        if kind == "line":
            fig = px.line(df, x=x, y=y, template=tpl, title=plan.get("title", ""))
        elif kind == "bar":
            if y:
                fig = px.bar(df, x=x, y=y, template=tpl, title=plan.get("title", ""))
            else:
                counts = df[x].value_counts().head(20)
                fig = px.bar(x=counts.index.astype(str), y=counts.values, template=tpl,
                             labels={"x": x, "y": "count"}, title=plan.get("title", ""))
        elif kind == "scatter":
            fig = px.scatter(df, x=x, y=y, template=tpl, title=plan.get("title", ""))
        elif kind == "histogram":
            fig = px.histogram(df, x=y, template=tpl, title=plan.get("title", ""))
        elif kind == "heatmap":
            fig = px.imshow(df[dims].corr(), text_auto=True, aspect="auto", template=tpl,
                            title=plan.get("title", ""))
        elif kind == "parallel":
            fig = px.parallel_coordinates(df, dimensions=dims, template=tpl,
                                          title=plan.get("title", ""))
        elif kind == "box":
            fig = px.box(df, x=x, y=y, template=tpl, title=plan.get("title", ""))
        else:
            st.info(f"暂不支持自动渲染的图表类型：{kind}")
            return
        st.plotly_chart(fig, width="stretch")
    except Exception as e:
        st.error(f"自动图表渲染失败（{plan.get('title', kind)}）：{type(e).__name__}: {str(e)[:160]}")


def _csv_section(ctx):
    st.subheader("📊 CSV 数据可视化（自动出图）")
    cf = st.file_uploader("上传 CSV", type=["csv"], key="csv")
    if cf:
        try:
            st.session_state.df = pd.read_csv(cf)
            st.session_state.df_name = getattr(cf, "name", "") or ""
            st.session_state.chart_plan = None       # 换了数据就要重算推荐
            st.session_state.auto_captions = {}
            st.session_state.caption = ""
        except Exception as e:
            st.error(f"CSV 解析失败：{type(e).__name__}: {str(e)[:160]}")
    if st.button("📂 载入示例数据 demo_data/data.csv"):
        try:
            from config import DEMO_DIR
            p = os.path.join(str(DEMO_DIR), "data.csv")
            if os.path.exists(p):
                st.session_state.df = pd.read_csv(p)
                st.session_state.df_name = "demo_data/data.csv"
                st.session_state.chart_plan = None
                st.session_state.auto_captions = {}
                st.session_state.caption = ""
            else:
                st.warning("未找到示例 CSV。")
        except Exception as e:
            st.error(f"载入示例数据失败：{str(e)[:160]}")

    df = st.session_state.get("df")
    if df is None:
        st.caption("上传 CSV 或载入示例数据后，系统会按列类型**自动**推荐并渲染图表，无需手动选择。")
        return
    st.write(df.head())

    # —— 自动推荐区（T4）——
    plan = st.session_state.get("chart_plan")
    if not plan:
        try:
            plan = writing.infer_chart_plan(df)
        except Exception as e:
            st.error(f"图表推荐失败：{type(e).__name__}: {str(e)[:160]}")
            plan = []
        st.session_state.chart_plan = plan
    if plan:
        st.markdown(f"#### 🤖 自动推荐图表（{len(plan)} 个，基于列类型推断）")
        for i, p in enumerate(plan):
            _render_plan_chart(df, p)
            st.caption("🧭 **推荐依据：** " + str(p.get("reason", "")))
            btn_key = f"auto_cap_{i}"
            if st.button("🤖 生成该图 Figure Caption", key=btn_key):
                if not writing.llm_available(ctx):
                    st.warning("未配置 API Key，无法生成 Figure Caption。")
                else:
                    with st.spinner("🤖 正在生成 Figure Caption…"):
                        cap = writing.figure_caption(ctx, df, p.get("title", ""),
                                                     x=p.get("x"), y=p.get("y"))
                    st.session_state.auto_captions[i] = cap or ""
            cap_text = (st.session_state.get("auto_captions") or {}).get(i, "")
            if cap_text:
                st.info("**Figure Caption:** " + cap_text)
                if not writing.caption_has_numbers(cap_text):
                    st.warning("该 caption 未包含任何具体数值（可能未引用真实数据），可点击上方按钮重试一次。")
            st.markdown("")
    else:
        st.info("未能从该表自动推断出合适图表，请在下方「自定义图表」中手动选择 X / Y / 图表类型。")

    # —— 手动自定义区（保留原有能力）——
    with st.expander("🛠️ 自定义图表（手动选择 X / Y / 类型）", expanded=False):
        cols = df.columns.tolist()
        c1, c2, c3 = st.columns(3)
        # 注意：这三项不用 session_state key —— 选项随上传的 CSV 变化，预置 key 会因
        # 值不在 options 中而抛 StreamlitAPIException。由 Streamlit 自动管理其状态。
        x = c1.selectbox("X", cols)
        y = c2.selectbox("Y", cols, index=min(1, len(cols) - 1))
        ct = c3.selectbox("图表", ["折线图", "柱状图", "散点图", "三维雷达图", "相关性热力图",
                                   "平行坐标图"])
        tpl = "plotly_white"
        try:
            if ct == "折线图":
                st.plotly_chart(px.line(df, x=x, y=y, template=tpl), width="stretch")
            elif ct == "柱状图":
                st.plotly_chart(px.bar(df, x=x, y=y, template=tpl), width="stretch")
            elif ct == "散点图":
                st.plotly_chart(px.scatter(df, x=x, y=y, template=tpl), width="stretch")
            elif ct == "三维雷达图":
                nums = df.select_dtypes(include="number").columns.tolist()
                label_col = x if x not in nums else (cols[0] if cols[0] not in nums else None)
                if len(nums) >= 3:
                    norm = df[nums[:6]] / df[nums[:6]].max()
                    fg = go.Figure()
                    for i, row in norm.iterrows():
                        fg.add_trace(go.Scatterpolar(
                            r=list(row), theta=nums[:6], fill="toself",
                            name=str(df.iloc[i][label_col])[:22] if label_col else f"#{i}"))
                    st.plotly_chart(fg, width="stretch")
                else:
                    st.info("三维雷达图需要至少 3 个数值列。")
            elif ct == "相关性热力图":
                nums = df.select_dtypes(include="number")
                if len(nums.columns) >= 2:
                    st.plotly_chart(px.imshow(nums.corr(), text_auto=True, aspect="auto",
                                              template=tpl), width="stretch")
            elif ct == "平行坐标图":
                nums = df.select_dtypes(include="number").columns.tolist()
                if len(nums) >= 3:
                    st.plotly_chart(px.parallel_coordinates(df, dimensions=nums, template=tpl),
                                    width="stretch")
        except Exception as e:
            st.error(f"自定义图表渲染失败：{type(e).__name__}: {str(e)[:160]}")
        if st.button("🤖 自动提炼 Figure Caption（自定义图）"):
            if not writing.llm_available(ctx):
                st.warning("未配置 API Key，无法生成 Figure Caption。")
            else:
                with st.spinner("🤖 正在生成 Figure Caption…"):
                    st.session_state.caption = writing.figure_caption(ctx, df, ct, x=x, y=y) or ""
        if st.session_state.get("caption"):
            st.info("**Figure Caption:** " + str(st.session_state["caption"]))


# ---------------------------------------------------------------- 无幻觉大纲（T1）
def _outline_section(ctx):
    st.subheader("✍️ 无幻觉论文框架（生成后自动白名单校验）")
    files = list(getattr(ctx, "paper_order", []) or [])
    if not files:
        st.error("📭 **请先上传文献到「📁 文献库管理」**（或在左侧点击「🚀 一键加载演示数据」）。"
                 "文献库为空时不会生成任何引用，以免出现编造文献。")
        return
    options = ["自动（文献库首篇）"] + files
    src = st.selectbox("References 抽取来源", options, key="ref_source")
    idea = st.text_area("输入 Idea", key="idea")
    preferred = None if str(src).startswith("自动") else str(src)

    def _gen(strict):
        with st.spinner("🧱 正在生成论文框架…"):
            res = writing.generate_outline_verified(ctx, idea, preferred=preferred, strict=strict)
        st.session_state.outline = res.get("text", "") or ""
        st.session_state.outline_meta = res

    if st.button("🧱 生成框架（引用真实文献）"):
        if not idea:
            st.warning("请先输入 Idea。")
        elif not writing.llm_available(ctx):
            st.warning("未配置 API Key，无法调用大模型。")
        else:
            _gen(False)

    meta = st.session_state.get("outline_meta") or {}
    text = st.session_state.get("outline") or ""
    if not text:
        return
    st.markdown(text)
    refs = meta.get("refs") or []
    st.caption("📚 引用白名单来源：" + (meta.get("source") or "—") + f"（共 {len(refs)} 条真实条目）")
    if meta.get("empty_library"):
        st.error("📭 文献库为空，未生成任何引用。请先上传文献到「📁 文献库管理」。")
        return
    v = meta.get("validation")
    if not v:
        st.info("本次未执行白名单校验（可能未返回正文内容）。")
        return
    if v.get("ok"):
        st.success(f"✅ 引用白名单校验通过：{v.get('n_citations', 0)} 处 [n] 角标、"
                   f"{v.get('n_entries', 0)} 条 References 条目，全部命中 {v.get('n_allowed', 0)} 条真实文献。")
        return
    hall = v.get("hallucinated") or []
    st.error(f"🚨 检测到 {len(hall)} 条疑似编造文献/越界角标：")
    for h in hall[:10]:
        st.markdown(f"- `[{h.get('kind','?')}]` {str(h.get('text',''))[:140]} —— {h.get('reason','')}")
    if st.button("🔁 重新生成（强化约束）"):
        if not writing.llm_available(ctx):
            st.warning("未配置 API Key，无法调用大模型。")
        else:
            _gen(True)


# ---------------------------------------------------------------- 拓扑图渲染（T2/T3）
def _svg_height(svg, default=460, cap=900):
    m = re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', svg or "") \
        or re.search(r'height="([\d.]+)', svg or "")
    try:
        return int(min(cap, max(240, float(m.group(1)) + 40)))
    except Exception:
        return default


def _render_tikz(code):
    fp = writing.code_fingerprint(code)
    cache = st.session_state.get("tikz_cache") or {}
    res = cache.get(fp)
    if res is None:
        with st.spinner("🔧 正在尝试本地编译 TikZ…"):
            res = writing.try_compile_tikz(code)
        cache[fp] = res
        st.session_state.tikz_cache = cache
    if res.get("ok") and res.get("png"):
        st.image(res["png"], caption=f"✅ 真实编译渲染（{res.get('engine', 'LaTeX')} → PDF → PNG）",
                 width="stretch")
        return
    if res.get("log"):
        with st.expander("🧾 LaTeX 编译日志（前 500 字）", expanded=False):
            st.code((res.get("log") or "")[:500] or "（无日志输出）", language="text")
    reason = res.get("reason") or ""
    if not res.get("engine"):
        st.info("🖇️ 本机未检测到 LaTeX 编译器（已依次查找 tectonic / pdflatex / xelatex / "
                "lualatex），已降级为源码展示。可复制代码到在线编辑器渲染："
                " [Overleaf](https://www.overleaf.com) · [tikz.dev](https://tikz.dev)")
    else:
        st.warning(f"⚠️ TikZ 编译未成功：{reason}")


def _render_graphviz(code):
    fp = writing.code_fingerprint(code)
    cache = st.session_state.get("dot_cache") or {}
    res = cache.get(fp)
    if res is None:
        with st.spinner("🔧 正在渲染 Graphviz…"):
            res = writing.render_dot(code)
        cache[fp] = res
        st.session_state.dot_cache = cache
    if res.get("ok") and res.get("svg"):
        note = res.get("note", "")
        if res.get("mode") == "graphviz":
            st.success("✅ " + note)
        else:
            st.caption("ℹ️ " + note + "（降级渲染（等价拓扑图））")
        html = ('<div style="background:#fff;overflow:auto;display:flex;'
                f'justify-content:center;padding:8px;">{res["svg"]}</div>')
        embed_html(html, height=_svg_height(res["svg"]), scrolling=True)
        return
    if res.get("ok") and res.get("png"):
        st.caption("ℹ️ " + str(res.get("note", "")) + " —— 降级渲染（等价拓扑图）")
        st.image(res["png"], caption="降级渲染（等价拓扑图）", width="stretch")
        return
    st.warning("⚠️ 未能渲染 Graphviz：" + str(res.get("note", "未知原因")))
    if res.get("log"):
        with st.expander("🧾 渲染诊断日志（前 500 字）", expanded=False):
            st.code(str(res.get("log"))[:500], language="text")


def _diagram_section(ctx):
    st.subheader("🏗️ 架构拓扑图（真实渲染）")
    c1, c2 = st.columns(2)
    kind = c1.selectbox("类型", ["mermaid", "tikz", "graphviz", "matplotlib"], key="dg_kind")
    topic = c2.text_input("主题", key="topic")
    if st.button("🎨 生成代码"):
        if not topic:
            st.warning("请先填写主题。")
        elif not writing.llm_available(ctx):
            st.warning("未配置 API Key，无法调用大模型。")
        else:
            try:
                with st.spinner("🎨 正在生成拓扑图代码…"):
                    st.session_state.diagram = (kind, writing.diagram(ctx, kind, topic))
            except Exception as e:
                st.error(f"生成失败：{type(e).__name__}: {str(e)[:160]}")
    dia = st.session_state.get("diagram")
    if not dia:
        return
    k, code = dia
    st.code(code, language=("mermaid" if k == "mermaid" else ("latex" if k == "tikz" else
                            ("dot" if k == "graphviz" else "python"))))
    try:
        if k == "mermaid":
            embed_html(f'<div class="mermaid" style="display:flex;justify-content:center;">{code}</div>'
                       '<script type="module">import mermaid from '
                       '"https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";'
                       'mermaid.initialize({startOnLoad:true,flowchart:{useMaxWidth:false}});</script>',
                       height=600, scrolling=True)
        elif k == "tikz":
            _render_tikz(code)
        elif k == "graphviz":
            _render_graphviz(code)
        else:
            st.caption("Python Matplotlib 代码已生成；可在「🤖 Agent 总助手」的代码执行沙箱中"
                       "运行后查看出图结果。")
    except Exception as e:
        st.error(f"拓扑图渲染失败：{type(e).__name__}: {str(e)[:160]}")


# ---------------------------------------------------------------- 翻译
def _translate_section(ctx):
    st.subheader("🌐 学术翻译")
    direction = st.radio("方向", ["中→英", "英→中"], horizontal=True, key="trdir")
    text = st.text_area("粘贴文本", key="trtext", height=160)
    if st.button("🔁 一键翻译"):
        if not text:
            st.warning("请先粘贴待翻译文本。")
        elif not writing.llm_available(ctx):
            st.warning("未配置 API Key，无法调用大模型。")
        else:
            try:
                with st.spinner("🌐 正在翻译…"):
                    if direction == "中→英":
                        st.session_state.trres = writing.translate_to_english(ctx, text) or ""
                    else:
                        st.session_state.trres = writing.translate_to_chinese(ctx, text) or ""
            except Exception as e:
                st.error(f"翻译失败：{str(e)[:160]}")
    if st.session_state.get("trres"):
        st.markdown(st.session_state["trres"])


def render(ctx):
    _init_state()
    page_header("✍️ 学术写作与可视化",
                "CSV→自动出图+Figure Caption、无幻觉论文框架（白名单校验）、拓扑图真实渲染、中英学术翻译。")
    _csv_section(ctx)
    st.markdown("---")
    _outline_section(ctx)
    st.markdown("---")
    _diagram_section(ctx)
    st.markdown("---")
    _translate_section(ctx)

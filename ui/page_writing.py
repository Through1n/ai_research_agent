"""页面 6：学术写作与数据可视化 Copilot。

本轮整改（对应 docs/audit_report_20261010.md 第五节 E 路）：
- 5.1 无幻觉 References： References 章节由 services.writing 用白名单**确定性拼接**
  （不经过 LLM），UI 逐条展示条目来源何篇入库 PDF 的参考文献表；生成后**自动**跑白名单
  校验，幻觉/章节缺失用 st.error 明确列出并提供「重新生成（强化约束）」按钮；占位串残留
  显式告警；文献库为空时不生成占位引用；
- 5.2 TikZ：尝试本地编译成 PNG 真实渲染，无编译器时降级为源码 + 在线编辑器链接；
- 5.3 Graphviz：dot -Tsvg → networkx+matplotlib → 零依赖 DOT→SVG 三级降级渲染；
- 5.4 CSV 自动出图：上传即按列类型自动渲染推荐图表（附推荐依据），手动选择收进折叠区；
- 5.5 Figure Caption：注入实测数值事实，并对无数字的 caption 给出提示。

所有 session_state key 均在 ``_init_state`` 中先初始化再读；所有外部调用均 try/except，
失败给中文友好提示。

Round 19 后台化改造（用户需求：「0 到 6 的板块中的任务任何一项都是可以同时运行的」）：
本页 4 类 LLM 长任务共 6 个按钮（论文框架 ×2 / Figure Caption 自动+自定义 /
拓扑图代码 / 学术翻译）全部改为 services/tasks.py 后台执行 + bg_consume 交付，
主线程只留输入校验、缓存命中判定与结果渲染。框架生成的缓存命中仍在主线程
即时返回（无 LLM 调用，不占后台并发队列）；流式增量展示让位于任务中心进度
（后台无人观看增量输出）。TikZ / Graphviz 本地编译保持内联（渲染路径 + 结果
缓存，见 _render_tikz 注释）。worker 硬约束：禁止任何 st.* 调用（线程内没有
ScriptRunContext）。
"""
import copy
import os
import re
import time

import streamlit as st
from ui.common import (embed_html, page_header, show_error, _esc, empty_state, pretty_table,
                       is_llm_degraded, llm_degraded_banner,
                       bg_submit, bg_consume, bg_running)
from ui.sidebar import set_flash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from services import writing

# ---------------- 后台任务 kind（Round 19 后台化：本页提交/认领的唯一标识） ----------------
_K_OUTLINE = "writing_outline"           # 无幻觉论文框架生成
_K_CAPTION_AUTO = "writing_caption_auto"    # 自动推荐图 Figure Caption
_K_CAPTION_CUSTOM = "writing_caption_custom"  # 自定义图 Figure Caption
_K_DIAGRAM = "writing_diagram"           # 拓扑图代码生成
_K_TRANSLATE = "writing_translate"       # 学术翻译

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
    "outline_cache": {},
    "diagram": None,
    "dg_kind": "mermaid",
    "tikz_cache": {},
    "dot_cache": {},
}


def _init_state():
    for k, v in _SS_DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = copy.deepcopy(v)


# ============================ Round 19 后台化：worker / 交付处理器 ============================
# worker 硬约束：运行在 services/tasks.py 的 worker 线程里，禁止任何 st.* 调用。

def _toast(msg):
    try:
        st.toast(msg, icon="🚀")
    except Exception:
        pass


def _w_outline(ctx, idea, preferred, strict, progress):
    """后台线程生成无幻觉论文框架（Round 19）。

    与旧内联 _gen 的差异：白名单抽取 / 正文生成 / 后处理全在线程内；流式通道
    （outline_body_stream）在后台聚合——无人观看增量输出，进度改经 progress
    上报；异常不调 show_error，随结果返回 error 字段由交付侧提示。
    """
    progress("🔍 抽取 References 白名单…", 0.1)
    try:
        refs_text, refs_list, source = writing.reference_whitelist(ctx, preferred)
    except Exception as e:
        return {"res": {"error": "抽取 References 白名单失败：%s" % str(e)[:160]},
                "idea": str(idea), "preferred": preferred, "strict": bool(strict)}
    if not refs_list:
        return {"res": {"empty_library": True, "text": writing.EMPTY_LIBRARY_HINT,
                        "refs": [], "source": source},
                "idea": str(idea), "preferred": preferred, "strict": bool(strict)}
    progress("✍️ 正在生成大纲正文…", 0.3)
    prompt = writing._outline_prompt(idea, refs_text, len(refs_list), strict=strict)
    t0 = time.time()
    chunks = []
    try:
        for ch in writing.outline_body_stream(ctx, prompt, 0.4):
            if ch:
                chunks.append(str(ch))
    except Exception as e:      # 流式通道整体异常：与旧内联一致的中断提示
        chunks.append(f"\n\n⚠️ 流式输出中断：{type(e).__name__}: {str(e)[:120]}")
    raw = "".join(chunks)
    progress("🧹 后处理与白名单校验…", 0.85)
    try:
        res = writing.finalize_outline(raw, refs_list, source, elapsed_sec=time.time() - t0)
    except Exception as e:
        return {"res": {"error": "处理大纲失败：%s" % str(e)[:160]},
                "idea": str(idea), "preferred": preferred, "strict": bool(strict)}
    return {"res": res, "idea": str(idea), "preferred": preferred, "strict": bool(strict)}


def _w_caption(ctx, df, chart_desc, x, y, idx, progress):
    """后台线程生成 Figure Caption（Round 19）。idx 回显供交付侧写回对应图表位。"""
    progress("🤖 正在生成 Figure Caption…", None)
    cap = ""
    try:
        cap = writing.figure_caption(ctx, df, chart_desc, x=x, y=y) or ""
    except Exception as e:
        return {"idx": idx, "error": str(e)[:160]}
    return {"idx": idx, "cap": cap}


def _w_diagram(ctx, kind, topic, progress):
    """后台线程生成拓扑图代码（Round 19）。"""
    progress("🎨 正在生成拓扑图代码…", None)
    try:
        return {"kind": kind, "code": writing.diagram(ctx, kind, topic)}
    except Exception as e:
        return {"kind": kind, "error": str(e)[:160]}


def _w_translate(ctx, text, to_english, progress):
    """后台线程执行学术翻译（Round 19）。"""
    progress("🌐 正在翻译…", None)
    try:
        if to_english:
            return {"text": writing.translate_to_english(ctx, text) or ""}
        return {"text": writing.translate_to_chinese(ctx, text) or ""}
    except Exception as e:
        return {"error": str(e)[:160]}


def _consume_outline(kind, t):
    """论文框架交付（Round 19）：写回 outline / outline_meta，缓存与空库语义同旧内联。"""
    if kind != _K_OUTLINE:
        return
    if t["state"] == "error":
        set_flash("error", "⚠️ 生成框架失败：%s" % str(t.get("error") or "")[:150])
        return
    payload = t.get("result") or {}
    res = payload.get("res") or {}
    if res.get("error"):
        set_flash("error", "⚠️ " + str(res.get("error"))[:200])
        return
    st.session_state.outline = res.get("text", "") or ""
    st.session_state.outline_meta = res
    if res.get("text") and not res.get("empty_library"):
        # 缓存写入（与旧内联一致：仅成功且非空库时缓存）
        cache = st.session_state.get("outline_cache") or {}
        key = (payload.get("idea") or "", str(payload.get("preferred")),
               bool(payload.get("strict")))
        cache[key] = res
        st.session_state.outline_cache = cache
    if res.get("empty_library"):
        set_flash("warning", "⚠️ 文献库中没有可抽取的 References，已中止生成"
                             "（不输出任何占位/编造引用）。")
    else:
        set_flash("success", "✅ 框架已生成（References 为确定性拼接，已做白名单校验）")


def _consume_caption_auto(kind, t):
    """自动推荐图 Caption 交付（Round 19）：按回显的图表位写回 auto_captions。"""
    if kind != _K_CAPTION_AUTO:
        return
    if t["state"] == "error":
        set_flash("error", "⚠️ Figure Caption 生成失败：%s" % str(t.get("error") or "")[:150])
        return
    r = t.get("result") or {}
    if r.get("error"):
        set_flash("error", "⚠️ Figure Caption 生成失败：%s" % str(r.get("error"))[:150])
        return
    idx = r.get("idx")
    caps = dict(st.session_state.get("auto_captions") or {})
    caps[idx] = r.get("cap") or ""
    st.session_state.auto_captions = caps


def _consume_caption_custom(kind, t):
    """自定义图 Caption 交付（Round 19）：写回 caption（降级态由既有渲染路径展示）。"""
    if kind != _K_CAPTION_CUSTOM:
        return
    if t["state"] == "error":
        set_flash("error", "⚠️ Figure Caption 生成失败：%s" % str(t.get("error") or "")[:150])
        return
    r = t.get("result") or {}
    if r.get("error"):
        set_flash("error", "⚠️ Figure Caption 生成失败：%s" % str(r.get("error"))[:150])
        return
    st.session_state.caption = r.get("cap") or ""


def _consume_diagram(kind, t):
    """拓扑图代码交付（Round 19）：写回 (kind, code) 二元组（落点与旧内联一致）。"""
    if kind != _K_DIAGRAM:
        return
    if t["state"] == "error":
        set_flash("error", "⚠️ 生成拓扑图代码失败：%s" % str(t.get("error") or "")[:150])
        return
    r = t.get("result") or {}
    if r.get("error"):
        set_flash("error", "⚠️ 生成拓扑图代码失败：%s" % str(r.get("error"))[:150])
        return
    st.session_state.diagram = (r.get("kind"), r.get("code") or "")


def _consume_translate(kind, t):
    """学术翻译交付（Round 19）：写回 trres（降级横幅由既有渲染路径展示）。"""
    if kind != _K_TRANSLATE:
        return
    if t["state"] == "error":
        set_flash("error", "⚠️ 翻译失败：%s" % str(t.get("error") or "")[:150])
        return
    r = t.get("result") or {}
    if r.get("error"):
        set_flash("error", "⚠️ 翻译失败：%s" % str(r.get("error"))[:150])
        return
    st.session_state.trres = r.get("text") or ""


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
        show_error("渲染图表（%s）" % plan.get('title', kind), e)


def _csv_section(ctx):
    st.subheader("📊 CSV 数据可视化（自动出图）")
    st.caption("💡 上传 CSV 或载入示例后，系统按列类型**自动**推荐并渲染图表，无需手动选择；"
               "需要更精细控制时，用下方「🛠️ 自定义图表」手动调整。")
    c_up, c_demo = st.columns([2, 1])
    cf = c_up.file_uploader("上传 CSV", type=["csv"], key="csv")
    with c_demo:
        st.write("")  # 与上传框垂直对齐留白
        if st.button("📂 载入示例数据", key="csv_demo", use_container_width=True):
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
                show_error("载入示例数据", e)
    if cf:
        try:
            st.session_state.df = pd.read_csv(cf)
            st.session_state.df_name = getattr(cf, "name", "") or ""
            st.session_state.chart_plan = None       # 换了数据就要重算推荐
            st.session_state.auto_captions = {}
            st.session_state.caption = ""
        except Exception as e:
            show_error("解析 CSV", e)

    df = st.session_state.get("df")
    if df is None:
        empty_state("尚未载入数据，暂无可可视化的内容。",
                    hints=["上传 CSV 文件", "或点击上方「📂 载入示例数据」"])
        return
    st.write(df.head())

    # —— 自动推荐区（T4）——
    plan = st.session_state.get("chart_plan")
    if not plan:
        try:
            plan = writing.infer_chart_plan(df)
        except Exception as e:
            show_error("推荐图表", e)
            plan = []
        st.session_state.chart_plan = plan
    if plan:
        st.markdown(f"#### 🤖 自动推荐图表（{len(plan)} 个，基于列类型推断）")
        for i, p in enumerate(plan):
            _render_plan_chart(df, p)
            st.caption("🧭 **推荐依据：** " + str(p.get("reason", "")))
            btn_key = f"auto_cap_{i}"
            _cap_run = bg_running(kinds={_K_CAPTION_AUTO})
            if st.button("🤖 生成该图 Figure Caption", key=btn_key, disabled=_cap_run,
                         help="后台生成：提交后可切换其他页面，进度见左侧「任务中心」"):
                if not writing.llm_available(ctx):
                    st.warning("未配置 API Key，无法生成 Figure Caption。")
                else:
                    # 后台化改造（Round 19）：原内联 spinner 跑主线程，生成期间整个 UI 冻结。
                    # df 用默认参数快照（闭包直捕会在 worker 执行时读到上传新表后的对象）。
                    bg_submit("Figure Caption（图 %d）" % (i + 1),
                              lambda progress, _df=df, _i=i, _p=p:
                                  _w_caption(ctx, _df, _p.get("title", ""),
                                             _p.get("x"), _p.get("y"), _i, progress),
                              kind=_K_CAPTION_AUTO)
                    _toast("🤖 已转入后台生成，可切换其他页面")
            if _cap_run:
                st.caption("⏳ Figure Caption 正在后台生成（进度见左侧「任务中心」）——"
                           "可切换其他页面。")
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
            show_error("渲染自定义图表", e)
        _capc_run = bg_running(kinds={_K_CAPTION_CUSTOM})
        if st.button("🤖 自动提炼 Figure Caption（自定义图）", disabled=_capc_run,
                     help="后台生成：提交后可切换其他页面，进度见左侧「任务中心」"):
            if not writing.llm_available(ctx):
                st.warning("未配置 API Key，无法生成 Figure Caption。")
            else:
                # Round 19 后台化：原内联 spinner 跑主线程，生成期间整个 UI 冻结。
                # ct/x/y/df 用默认参数快照（闭包直捕会在 worker 执行时读到用户改选后的值）。
                bg_submit("Figure Caption（自定义图）",
                          lambda progress, _df=df, _ct=ct, _x=x, _y=y:
                              _w_caption(ctx, _df, _ct, x=_x, y=_y, idx=None,
                                         progress=progress),
                          kind=_K_CAPTION_CUSTOM)
                _toast("🤖 已转入后台生成，可切换其他页面")
        if _capc_run:
            st.caption("⏳ Figure Caption 正在后台生成（进度见左侧「任务中心」）——"
                       "可切换其他页面。")
        if st.session_state.get("caption"):
            st.info("**Figure Caption:** " + str(st.session_state["caption"]))


# ---------------------------------------------------------------- 无幻觉大纲（T1）
def _render_reference_provenance(refs, source):
    """逐条展示 References 及其来源（R6-T4）。

    赛题卖点是「真实无幻觉 References」，评委要看的不是"我们说它没幻觉"，而是
    **每一条都能追溯到某篇入库 PDF 的参考文献表**。这里把编号/作者/标题/年份/来路
    摊平成表格，并标注这些条目是系统确定性拼接的（不经过大模型）。
    """
    refs = refs or []
    if not refs:
        st.error("📭 本次没有可用的真实文献（文献库为空或 PDF 未抽出 References 段），"
                 "系统**不会**生成任何占位/编造引用。请先到「📁 文献库管理」上传 PDF。")
        return
    src_files = sorted({str(r.get("source_pdf") or "").strip() for r in refs
                        if str(r.get("source_pdf") or "").strip()})
    where = "、".join(src_files) if src_files else "（未知来源）"
    st.success(f"🔒 **References 章节共 {len(refs)} 条，全部为确定性拼接**："
               f"编号 [1..{len(refs)}] 逐条照抄自「{where}」正文末尾的 References 段，"
               f"未经大模型改写或生成，因此不存在编造空间。")
    rows = []
    for i, r in enumerate(refs, 1):
        rows.append({
            "#": i,
            "作者": str(r.get("authors") or "")[:70] or "（见原文）",
            "标题": str(r.get("title") or r.get("raw") or "")[:140],
            "年份": str(r.get("year") or ""),
            "出处": str(r.get("venue") or "")[:60],
            "来路 PDF": str(r.get("source_pdf") or where)[:40],
        })
    try:
        pretty_table(rows)
    except Exception as e:                       # 不同 streamlit 版本的参数差异
        st.table(pd.DataFrame(rows[:40]))
        st.caption(f"（表格参数降级：{type(e).__name__}）")
    with st.expander("📄 查看完整 References 条目文本", expanded=False):
        st.markdown("\n\n".join("- " + writing._ref_line(i, r) for i, r in enumerate(refs, 1)))
    label = str(source or "—")
    if label.startswith("⚠️"):
        st.warning("抽取质量提示：" + label)
    else:
        st.caption("📚 引用白名单来源：" + label)


def _outline_section(ctx):
    st.subheader("✍️ 无幻觉论文框架（References 确定性拼接 + 白名单校验）")
    st.caption("💡 输入研究 Idea 并选择引用来源，系统生成正文框架；References 由入库 PDF "
               "**确定性拼接**（不经大模型），生成后自动做白名单校验，杜绝编造文献。")
    files = list(getattr(ctx, "paper_order", []) or [])
    if not files:
        empty_state("文献库为空，无法生成框架（以免出现编造文献）。",
                    hints=["到「📁 文献库管理」上传 PDF",
                           "或左侧点击「🚀 一键加载演示数据」"])
        return
    options = ["自动（文献库首篇）"] + files
    src = st.selectbox("References 抽取来源", options, key="ref_source")
    idea = st.text_area("输入 Idea", key="idea")
    preferred = None if str(src).startswith("自动") else str(src)

    _outline_run = bg_running(kinds={_K_OUTLINE})
    if st.button("🧱 生成框架（引用真实文献）", disabled=_outline_run,
                 help="后台生成：提交后可切换其他页面，进度见左侧「任务中心」"):
        if not idea:
            st.warning("请先输入 Idea。")
        elif not writing.llm_available(ctx):
            st.warning("未配置 API Key，无法调用大模型。")
        else:
            # 缓存命中在主线程即时完成（无 LLM 调用，不占后台并发队列）；
            # 未命中才提交后台 worker（Round 19：原内联流式生成冻结整个 UI，
            # 且后台无人观看增量输出，进度改经任务中心上报）。
            cache = st.session_state.get("outline_cache") or {}
            key = (str(idea), str(preferred), False)
            cached = cache.get(key)
            if cached is not None:
                st.session_state.outline = cached.get("text", "") or ""
                st.session_state.outline_meta = cached
                st.caption("⚡ 相同 Idea + 相同文献来源命中缓存，直接复用上次结果（未重新调用大模型）。")
            else:
                # idea/preferred 默认参数快照，防 worker 执行时读到改选后的值
                bg_submit("论文框架：" + str(idea)[:30],
                          lambda progress, _idea=idea, _pre=preferred:
                              _w_outline(ctx, _idea, _pre, False, progress),
                          kind=_K_OUTLINE)
                _toast("🧱 已转入后台生成，可切换其他页面")
    if _outline_run:
        st.caption("⏳ 论文框架正在后台生成（进度见左侧「任务中心」）——可切换其他页面。")

    meta = st.session_state.get("outline_meta") or {}
    text = st.session_state.get("outline") or ""
    refs = meta.get("refs") or []
    if meta.get("elapsed_sec"):
        st.caption(f"⏱️ 本次生成耗时 {meta.get('elapsed_sec')}s"
                   + ("（References 由系统确定性拼接，未占用生成时间）"
                      if meta.get("references_deterministic") else ""))
    if meta.get("llm_error"):
        if meta.get("body"):
            st.error("⚠️ 大模型输出**中途中断**：下方已保留本次真实生成的部分正文（未编造内容），"
                     "可点「🔁 重新生成（强化约束）」获取完整版本。")
        else:
            st.error("⚠️ 大模型未返回任何可用内容（API Key 未配置 / 网络异常 / 额度不足），"
                     "已中止生成，不输出任何引用。请检查左侧 Provider 配置后重试。")
    if not text:
        return
    st.markdown(text)

    # —— T4：References 溯源展示 ——
    st.markdown("#### 🔎 References 溯源（每条都能追到入库 PDF 的参考文献表）")
    if meta.get("empty_library"):
        _render_reference_provenance([], meta.get("source"))
        return
    if not meta.get("body"):
        # LLM 完全失败（非空库原因）：溯源/校验区没有意义，明确告知后结束
        st.info("本次未生成正文，跳过 References 溯源与白名单校验。")
        return
    _render_reference_provenance(refs, meta.get("source"))

    # —— T2：占位串残留必须显式告知，不静默输出 ——
    ph = meta.get("placeholder_issues") or []
    if ph:
        st.error(f"🧹 正文中仍检测到 {len(ph)} 处占位内容（已尽力清理，请人工复核或重新生成）：")
        for h in ph[:10]:
            st.markdown(f"- 第 {h.get('line_no')} 行 `{str(h.get('fragment', ''))[:60]}`"
                        f"（{h.get('rule')}）：…{str(h.get('line', ''))[:120]}…")
    elif meta.get("references_deterministic"):
        # 「没发现问题」是好消息，不该占一整块蓝色提示——降级为一行灰字，
        # 把色块留给真正需要用户行动的问题。
        st.caption("🧹 占位串扫描：全文未发现 `[Placeholder]` / `TODO` / `[1..N]` 等占位内容。")

    # —— 白名单校验：不通过必须明确列出 ——
    v = meta.get("validation")
    if not v:
        st.info("本次未执行白名单校验（可能未返回正文内容）。")
        return
    if v.get("ok"):
        st.success(f"✅ 引用白名单校验通过：{v.get('n_citations', 0)} 处 [n] 角标、"
                   f"{v.get('n_entries', 0)} 条 References 条目，"
                   f"全部命中 {v.get('n_allowed', 0)} 条真实文献。")
        return
    hall = v.get("hallucinated") or []
    st.error(f"🚨 检测到 {len(hall)} 条疑似编造文献/越界角标/章节缺失：")
    for h in hall[:10]:
        st.markdown(f"- `[{h.get('kind','?')}]` {str(h.get('text',''))[:140]} —— {h.get('reason','')}")
    if v.get("n_allowed"):
        st.caption(f"参考：本次真实文献白名单共 {v.get('n_allowed')} 条，"
                   f"允许的引用角标范围是 [1..{v.get('n_allowed')}]。")
    _outline_strict_run = bg_running(kinds={_K_OUTLINE})
    if st.button("🔁 重新生成（强化约束）", disabled=_outline_strict_run,
                 help="后台生成：提交后可切换其他页面，进度见左侧「任务中心」"):
        if not writing.llm_available(ctx):
            st.warning("未配置 API Key，无法调用大模型。")
        else:
            # strict=True 不走缓存（与旧内联 _gen(True) 一致，worker 内同样不读缓存）
            bg_submit("论文框架（强化）：" + str(idea)[:30],
                      lambda progress, _idea=idea, _pre=preferred:
                          _w_outline(ctx, _idea, _pre, True, progress),
                      kind=_K_OUTLINE)
            _toast("🧱 已转入后台重新生成，可切换其他页面")
    if _outline_strict_run:
        st.caption("⏳ 论文框架正在后台重新生成（进度见左侧「任务中心」）——可切换其他页面。")


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
            st.caption("⚠️ " + note + "（降级渲染（等价拓扑图））")
        html = ('<div style="background:#fff;overflow:auto;display:flex;'
                f'justify-content:center;padding:8px;">{res["svg"]}</div>')
        embed_html(html, height=_svg_height(res["svg"]), scrolling=True)
        return
    if res.get("ok") and res.get("png"):
        st.caption("⚠️ " + str(res.get("note", "")) + " —— 降级渲染（等价拓扑图）")
        st.image(res["png"], caption="降级渲染（等价拓扑图）", width="stretch")
        return
    st.warning("⚠️ 未能渲染 Graphviz：" + str(res.get("note", "未知原因")))
    if res.get("log"):
        with st.expander("🧾 渲染诊断日志（前 500 字）", expanded=False):
            st.code(str(res.get("log"))[:500], language="text")


def _diagram_section(ctx):
    st.subheader("🏗️ 架构拓扑图（真实渲染）")
    st.caption("💡 选择类型并填写主题，生成 Mermaid / TikZ / Graphviz / Matplotlib 代码，"
               "并尽可能**真实渲染**（本地编译器优先，缺失时降级为源码 / 在线编辑器）。")
    c1, c2 = st.columns(2)
    kind = c1.selectbox("类型", ["mermaid", "tikz", "graphviz", "matplotlib"], key="dg_kind")
    topic = c2.text_input("主题", key="topic")
    _dg_run = bg_running(kinds={_K_DIAGRAM})
    if st.button("🎨 生成代码", disabled=_dg_run,
                 help="后台生成：提交后可切换其他页面，进度见左侧「任务中心」"):
        if not topic:
            st.warning("请先填写主题。")
        elif not writing.llm_available(ctx):
            st.warning("未配置 API Key，无法调用大模型。")
        else:
            # Round 19 后台化：原内联 spinner 冻结整个 UI；kind/topic 默认参数快照，
            # 防 worker 执行时读到用户改选后的值。
            bg_submit("拓扑图代码（%s）：%s" % (kind, str(topic)[:24]),
                      lambda progress, _kind=kind, _topic=topic:
                          _w_diagram(ctx, _kind, _topic, progress),
                      kind=_K_DIAGRAM)
            _toast("🎨 已转入后台生成，可切换其他页面")
    if _dg_run:
        st.caption("⏳ 拓扑图代码正在后台生成（进度见左侧「任务中心」）——可切换其他页面。")
    dia = st.session_state.get("diagram")
    if not dia:
        return
    k, code = dia
    st.code(code, language=("mermaid" if k == "mermaid" else ("latex" if k == "tikz" else
                            ("dot" if k == "graphviz" else "python"))))
    try:
        if k == "mermaid":
            # 零插件渲染（问题六）：mermaid.js 随软件打包在 static/ 目录，
            # 由 Streamlit 静态服务（/app/static/...）本地加载，断网也能出图；
            # 静态服务不可用（如旧启动脚本）时自动回退 CDN，双保险。
            embed_html(
                f'<div class="mermaid" style="display:flex;justify-content:center;">{_esc(code)}</div>'
                '<script>(function(){function init(){'
                'if(window.mermaid){mermaid.initialize({startOnLoad:true,'
                'flowchart:{useMaxWidth:false}});}}'
                'var s=document.createElement("script");'
                's.src="/app/static/mermaid.min.js";'
                's.onload=init;'
                's.onerror=function(){var c=document.createElement("script");'
                'c.src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js";'
                'c.onload=init;document.head.appendChild(c);};'
                'document.head.appendChild(s);})();</script>',
                height=600, scrolling=True)
        elif k == "tikz":
            _render_tikz(code)
        elif k == "graphviz":
            _render_graphviz(code)
        else:
            st.caption("Python Matplotlib 代码已生成；可在「🤖 Agent 总助手」的代码执行沙箱中"
                       "运行后查看出图结果。")
    except Exception as e:
        show_error("渲染拓扑图", e)


# ---------------------------------------------------------------- 翻译
def _translate_section(ctx):
    st.subheader("🌐 学术翻译")
    st.caption("💡 粘贴待翻译文本，选择中英方向，一键获得学术风格译文（需配置 API Key）。")
    direction = st.radio("方向", ["中→英", "英→中"], horizontal=True, key="trdir")
    text = st.text_area("粘贴文本", key="trtext", height=160)
    _tr_run = bg_running(kinds={_K_TRANSLATE})
    if st.button("🔁 一键翻译", disabled=_tr_run,
                 help="后台翻译：提交后可切换其他页面，进度见左侧「任务中心」"):
        if not text:
            st.warning("请先粘贴待翻译文本。")
        elif not writing.llm_available(ctx):
            st.warning("未配置 API Key，无法调用大模型。")
        else:
            # Round 19 后台化：原内联 spinner 冻结整个 UI；direction/text 默认参数快照。
            bg_submit("学术翻译（%s）" % direction,
                      lambda progress, _text=text, _to_en=(direction == "中→英"):
                          _w_translate(ctx, _text, _to_en, progress),
                      kind=_K_TRANSLATE)
            _toast("🌐 已转入后台翻译，可切换其他页面")
    if _tr_run:
        st.caption("⏳ 翻译正在后台进行（进度见左侧「任务中心」）——可切换其他页面。")
    _tr = st.session_state.get("trres") or ""
    if _tr:
        # 离线占位不能当译文展示（Round 12 ②残留）：模型不可用时
        # translate_to_* 返回的是带降级标记的占位串，直接渲染 = 给评委看一段假译文。
        if is_llm_degraded(_tr):
            llm_degraded_banner()
        st.markdown(_tr)


def render(ctx):
    _init_state()
    # Round 19 后台化交付：5 类 LLM 长任务完成（含失败）时按 kind 认领写回状态。
    # 必须置于各 section 渲染之前——交付处理器写 session_state 后，bg_consume
    # 内部会触发整页重绘，本轮后续 section 读到的直接就是新结果。
    bg_consume({_K_OUTLINE: _consume_outline,
                _K_CAPTION_AUTO: _consume_caption_auto,
                _K_CAPTION_CUSTOM: _consume_caption_custom,
                _K_DIAGRAM: _consume_diagram,
                _K_TRANSLATE: _consume_translate})
    page_header("✍️ 学术写作与可视化",
                "CSV→自动出图+Figure Caption、无幻觉论文框架（白名单校验）、拓扑图真实渲染、中英学术翻译。")
    with st.container(border=True):
        _csv_section(ctx)
    with st.container(border=True):
        _outline_section(ctx)
    with st.container(border=True):
        _diagram_section(ctx)
    with st.container(border=True):
        _translate_section(ctx)

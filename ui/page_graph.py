"""页面 5：局域引用图谱与学术综述。真实引用关系、图论基石/边缘挖掘、自动综述、Future Work 前沿过滤。"""
import numpy as np
import pandas as pd
import streamlit as st

from services import graph as g
from services import library as _lib
from ui.common import (embed_html, require_library, page_header, pretty_table, ensure_state,
                       friendly_error, empty_state, empty_state_card, llm_guard, metric_strip,
                       long_block, step_text, at, is_llm_degraded, llm_degraded_banner,
                       is_llm_offline_generated, llm_offline_generated_banner,
                       bg_submit, bg_consume, bg_running, section_header)
from ui.sidebar import set_flash

# 引用关系列表一次展示的最大条数（其余折叠）
MAX_EDGE_SHOW = 12

# 后台任务 kind（2026-10 R19 后台化改造）：本页三个长任务提交/认领的唯一标识。
# bg_consume 按 kind 路由（只认领本页的，其余留给所属页）；bg_running 只数本页
# 同类任务防重复提交，不拦其他板块的并发（跨板块同时运行是特性，不是缺陷）。
_GRAPH_BUILD_KIND = "graph_build"      # 🕸️ 构建引用图谱（重本地计算）
_GRAPH_REVIEW_KIND = "graph_review"    # 📝 一键生成领域综述（压测 31.9s，全场最长）
_GRAPH_FUTURE_KIND = "graph_future"    # 🚀 Future Work 前沿扫描（排除已解决）


def _init():
    ensure_state({"graph_pack": None, "review": "", "future": ""})


def _title(ctx, n):
    """节点显示名：优先论文标题，缺失时退回文件名。"""
    try:
        return ctx.papers.get(n, {}).get("title", n)
    except Exception:
        return n


def _metrics_rows(ctx, nodes, metrics):
    """把图论指标拼成 DataFrame 行；任何字段缺失都按 0 处理，杜绝 KeyError。

    年份列走 g._node_year：真实元数据优先、arXiv 编号回退次之，取不到写「未知」，
    与综述时间线口径一致（绝不由界面层猜年份）。
    """
    pr = (metrics or {}).get("pagerank", {}) or {}
    ind = (metrics or {}).get("indegree", {}) or {}
    bet = (metrics or {}).get("betweenness", {}) or {}
    eig = (metrics or {}).get("eigenvector", {}) or {}
    df = pd.DataFrame({
        "论文": [_title(ctx, n) for n in nodes],
        "文件名": list(nodes),
        "年份": [g._node_year(ctx, n) or "未知" for n in nodes],
        "PageRank": [round(float(pr.get(n, 0)), 4) for n in nodes],
        "被引次数(入度)": [int(ind.get(n, 0)) for n in nodes],
        "中介中心性": [round(float(bet.get(n, 0)), 3) for n in nodes],
        "特征向量": [round(float(eig.get(n, 0)), 4) for n in nodes],
    }).sort_values("PageRank", ascending=False)
    # 整列同值的列没有信息量（单节点时的「文件名」、年份全缺时的「未知」），
    # 一律删掉——首屏不摆死列；「论文」是行标识，始终保留。
    return df[[c for c in df.columns
               if c == "论文" or df[c].nunique(dropna=False) > 1]]


def _build_worker(ctx, progress):
    """后台线程执行引用图谱构建（2026-10 R19 后台化改造）。

    硬约束：本函数运行在 services/tasks.py 的 worker 线程里，**禁止任何 st.\*
    调用**（会报 missing ScriptRunContext）——原内联的 bar/note 进度条与
    friendly_error（图谱 HTML 生成失败话术）都含 st.*，均不可用：
    · 进度改经 progress(stage, pct) 回调上报，由侧边栏任务中心渲染；
    · HTML 生成失败时置空并在结果里带 html_error 标记，由交付流程经
      _render_pack 用人话提示（表格版图论指标不受影响）。
    """
    progress("🕸️ 解析引用关系", 0.02)

    def _pcb(r):
        try:
            progress("🕸️ 解析引用关系", min(max(float(r or 0.0), 0.0), 0.9))
        except Exception:
            pass

    G, edges = g.build_graph(ctx, progress_cb=_pcb)
    progress("📊 图论指标计算", 0.95)
    metrics = g.analyze(G)
    corner, edge = g.classify_papers(G, metrics)
    progress("🧩 生成可视化与组装", 0.98)
    html, html_err = "", ""
    try:
        html = g.graph_html(G, metrics, corner)
    except Exception as e:
        html, html_err = "", str(e)
    pack = _make_pack(G, edges, metrics, corner, edge)
    pack["html"] = html
    pack["html_error"] = html_err
    return pack


def _build_demo(ctx):
    """演示模式兜底图谱：无真实引用关系时，按主题相似度生成「示意图谱」。

    仅在⬆️真实引用边为空且用户点按钮时触发；每张图取最相似的 1 条示意关系，
    方向按年份从新到旧（近似「新论文参考旧工作」），输出处明确标注「示意，非真实引用」。

    embedding 不可用（模型未就绪 / 推理失败）时降级为「关键词重合度」建边：
    零依赖、不再调用 embedding；返回值第 6 项为降级说明（空串 = 未降级），
    由调用方写进图谱包，跨 rerun 仍对用户可见——绝不做静默降级。
    """
    import networkx as nx
    reps = list(dict.fromkeys(_lib.latest_of(files)[0]
                              for files in (_lib.group_versions(ctx) or {}).values()))
    if len(reps) < 2:
        raise ValueError("至少需要 2 篇论文才能生成示意图谱")
    text = {}
    for n in reps:
        body = "\n".join(c.get("text", "") for c in ctx.chunks if c.get("source") == n)
        title = _title(ctx, n)
        text[n] = (title + "。 " + body)[:1200] or title
    try:
        vecs = ctx.embed([text[n] for n in reps])
    except Exception:
        vecs = None

    if vecs is not None:

        def cos(a, b):
            a = np.asarray(a, dtype=float)
            b = np.asarray(b, dtype=float)
            d = (np.linalg.norm(a) * np.linalg.norm(b))
            return float(np.dot(a, b) / d) if d else 0.0

        def sim_of(i, j):
            return cos(vecs[i], vecs[j])

        sim_label = "主题相似度"
        degrade_note = ""
    else:
        # 语义向量不可用：改用已组好的「标题+正文片段」文本做关键词重合度
        # （走 services.state.tokenize，零外部依赖，不再调用 embedding）。
        # ⚠️ 口径差异必须让用户看见：重合只代表用词相近，不代表主题真实相似。
        from services.state import tokenize as _tokenize
        toks = [set(_tokenize(text[n])) for n in reps]

        def sim_of(i, j):
            a, b = toks[i], toks[j]
            if not a or not b:
                return 0.0
            return len(a & b) / ((len(a) * len(b)) ** 0.5)

        sim_label = "关键词重合度"
        degrade_note = ("⚠️ 语义向量不可用（本地 embedding 模型未就绪或推理失败），"
                        "已降级为关键词重合度建边：边仅代表标题/正文用词重合，"
                        "不代表主题真实相似（仍为示意关系，非真实引用）。")

    G = nx.DiGraph()
    for n in reps:
        G.add_node(n, title=_title(ctx, n), version=ctx.papers.get(n, {}).get("version", ""))
    edges = []
    for i, a in enumerate(reps):
        sims = sorted(((sim_of(i, j), reps[j]) for j in range(len(reps)) if j != i),
                      reverse=True)
        if not sims:
            continue
        sim, b = at(sims, 0, (0.0, None))
        if not b:
            continue
        # 方向按年份从新到旧（近似「新论文参考旧工作」）；年份走 g._node_year
        # （真实元数据/arXiv 回退），与综述时间线口径一致，缺失时按 0 处理
        ya, yb = g._node_year(ctx, a) or 0, g._node_year(ctx, b) or 0
        src, dst = (a, b) if ya >= yb else (b, a)
        if not G.has_edge(src, dst):
            G.add_edge(src, dst)
            ev = f"演示模式：{sim_label} {float(sim):.0%}（示意关系，非真实引用）"
            edges.append((src, dst, ev))
    if not edges:
        raise ValueError("论文主题差异过大，未能生成示意关系")
    metrics = g.analyze(G)
    corner, edge = g.classify_papers(G, metrics)
    return G, edges, metrics, corner, edge, degrade_note


def _make_pack(G, edges, metrics, corner, edge, demo=False):
    # G 一并放进 pack：综述/Future Work 按钮可复用已建好的图，省去重复解析（秒级）
    return {"html": None, "metrics": metrics, "corner": corner or [], "edge": edge or [],
            "edges": edges or [], "nodes": list(G.nodes), "demo": demo, "G": G}


def _degrade_tier(text):
    """降级分档（纯文本判定，无 st.*，可在 worker 线程内调用）。

    返回 "degraded"（离线占位响应，什么都没算出来）/ "offline"（本地确定性
    生成，内容真实但非大模型输出）/ "ok"（真实模型输出）。交付处理器按档位
    给提示——**绝不让降级内容顶着「✅ 已生成」出现**（Round 12 假成功比报错
    更危险）；内容区每次渲染也会复查档位并展示横幅，不依赖 flash 单槽位。
    """
    if is_llm_degraded(text):
        return "degraded"
    if is_llm_offline_generated(text):
        return "offline"
    return "ok"


def _review_worker(ctx, G, progress):
    """后台线程执行领域综述生成（2026-10 R19 后台化改造；原压测 31.9s，全场最长）。

    硬约束同 _build_worker：worker 线程内禁止任何 st.* 调用——原内联的
    st.session_state（读图包）、st.status、llm_degraded_banner 等都不可用。故：
    · 图包 G 由提交时的主线程闭包传入（None = 未构建或演示图谱，线程内自建，
      与原内联「G is None or demo 时重建」的分支等价）；
    · 降级判定在线程内完成，档位随结果返回，交付处理器写回 session_state
      并按档位 set_flash 提示。
    """
    if G is None:
        progress("⓪ 解析引用关系建图…", None)
        G, _ = g.build_graph(ctx)
    progress("📐 图论指标计算", None)
    metrics = g.analyze(G)
    corner, edge = g.classify_papers(G, metrics)

    def _rev_progress(stage):
        try:
            progress(str(stage), None)
        except Exception:
            pass

    review = g.generate_review(ctx, G, metrics, corner, progress_cb=_rev_progress) or ""
    return {"review": review, "tier": _degrade_tier(review)}


def _future_worker(ctx, G, progress):
    """后台线程执行 Future Work 前沿扫描（2026-10 R19 后台化改造）。

    硬约束同 _review_worker：G 由主线程闭包传入，降级档位随结果返回。
    """
    if G is None:
        progress("⓪ 解析引用关系建图…", None)
        G, _ = g.build_graph(ctx)
    progress("🔭 引用图可达性判定 + 语义确认", None)
    future = g.explore_future(ctx, G) or ""
    return {"future": future, "tier": _degrade_tier(future)}


def _consume_build(ctx, kind, t):
    """后台图谱构建交付：写回 graph_pack，成功/空边/失败分档提示。

    与原内联流程逐分支等价；图谱 HTML 失败不再走 friendly_error（线程外交付
    也没有内联展开区），错误标记随 pack 持久化，由 _render_pack 持续展示。
    """
    if kind != _GRAPH_BUILD_KIND:
        return
    if t["state"] == "error":
        set_flash("error", "⚠️ 构建引用图谱失败：%s"
                           "\n\n🩺 可能原因：论文正文缺少 References 章节导致解析为空；"
                           "embedding 推理失败 / 内存不足"
                           "\n\n🛠️ 怎么办：重试一次；点「🎭 演示模式示意图谱」；"
                           "或减少文献数量后重试" % str(t.get("error") or "")[:150])
        return
    pack = t.get("result")
    if not isinstance(pack, dict):
        set_flash("error", "⚠️ 图谱构建返回数据异常，请重试。")
        return
    st.session_state.graph_pack = pack
    edges = pack.get("edges") or []
    nodes = pack.get("nodes") or []
    if edges:
        set_flash("success", f"✅ 图谱构建完成：{len(nodes)} 个节点 · {len(edges)} 条真实引用关系")
    else:
        set_flash("warning", "⚠️ 未检测到文献间的真实引用关系，已生成仅含节点的图谱。"
                             "\n\n🩺 可能原因：论文正文未包含 References 章节 / 演示语料引用条目过少。"
                             "\n\n🛠️ 怎么办：①上传含完整参考文献的真实 PDF（如 BERT 与 Transformer）；"
                             "②或点「🎭 演示模式示意图谱」兜底演示。")


def _consume_review(ctx, kind, t):
    """后台综述交付：写回 session_state["review"] 并按降级档位提示。

    降级档位由 worker 判定随结果返回；综述内容区每次渲染都会复查档位并展示
    横幅，故降级结论不依赖 flash 单槽位也能持续可见。
    """
    if kind != _GRAPH_REVIEW_KIND:
        return
    if t["state"] == "error":
        set_flash("error", "⚠️ 生成综述失败：%s"
                           "\n\n🩺 可能原因：大模型返回超时 / 额度不足；文献库为空导致无素材"
                           "\n\n🛠️ 怎么办：重试一次；或减少单次综述的文献数量"
                   % str(t.get("error") or "")[:150])
        return
    res = t.get("result")
    if not isinstance(res, dict) or not str(res.get("review") or "").strip():
        set_flash("error", "⚠️ 综述生成返回数据异常，请重试。")
        return
    st.session_state["review"] = res.get("review") or ""
    _tier = res.get("tier")
    if _tier == "degraded":
        set_flash("error", "⚠️ 领域综述未生成：大模型暂时不可用，本次没有生成任何真实结果"
                           "（离线占位响应，请勿作为答案引用）。"
                           "\n\n🛠️ 怎么办：在左侧「接入方式」检查 API Key / 平台额度 / 网络后重试。")
    elif _tier == "offline":
        set_flash("warning", "⚠️ 大模型不可用：领域综述由本地确定性算法生成，不是大模型输出"
                             "（内容取自本地真实数据，未编造）。"
                             "\n\n🛠️ 怎么办：在左侧「接入方式」检查 API Key / 平台额度 / 网络后重试，"
                             "即可改由大模型撰写。")
    else:
        set_flash("success", "✅ 领域综述已生成")


def _consume_future(ctx, kind, t):
    """后台前沿扫描交付：写回 session_state["future"] 并按降级档位提示（同综述两档）。"""
    if kind != _GRAPH_FUTURE_KIND:
        return
    if t["state"] == "error":
        set_flash("error", "⚠️ 前沿扫描失败：%s"
                           "\n\n🩺 可能原因：大模型返回超时 / 额度不足；图谱为空"
                           "\n\n🛠️ 怎么办：先点「构建引用图谱」；或重试一次"
                   % str(t.get("error") or "")[:150])
        return
    res = t.get("result")
    if not isinstance(res, dict) or not str(res.get("future") or "").strip():
        set_flash("error", "⚠️ 前沿扫描返回数据异常，请重试。")
        return
    st.session_state["future"] = res.get("future") or ""
    _tier = res.get("tier")
    if _tier == "degraded":
        set_flash("error", "⚠️ 前沿方向未生成：大模型暂时不可用，本次没有生成任何真实结果"
                           "（离线占位响应，请勿作为答案引用）。"
                           "\n\n🛠️ 怎么办：在左侧「接入方式」检查 API Key / 平台额度 / 网络后重试。")
    elif _tier == "offline":
        set_flash("warning", "⚠️ 大模型不可用：前沿方向由本地确定性算法生成，不是大模型输出"
                             "（内容取自本地真实数据，未编造）。"
                             "\n\n🛠️ 怎么办：在左侧「接入方式」检查 API Key / 平台额度 / 网络后重试，"
                             "即可改由大模型撰写。")
    else:
        set_flash("success", "✅ 前沿方向扫描完成")


def _render_pack(ctx, pack):
    """渲染图谱 + 指标表 + 基石/边缘结论（HTML 生成失败不影响其余展示）。

    首屏信息预算（2026-10-25 降杂乱）：只留「图 + KPI 指标条」。图论指标大表、
    基石/边缘论文名单、引用边清单、时间线全部收进折叠区；诊断类提示降级为
    caption——打开本页第一眼是图谱本身，而不是 7 列表格与色块。
    """
    # 演示模式是唯一保留的 warning（内容真实性必须显式声明，不能只做灰字）
    if pack.get("demo"):
        st.warning("当前为**演示模式示意图谱**：依据主题相似度生成，**不代表真实引用关系**。"
                   "需真实引用请上传含 References 章节的完整 PDF。")
        if pack.get("degrade_note"):
            # 语义向量不可用 → 关键词重合度建边的口径说明（随包持久化，跨 rerun 仍可见）
            st.caption(pack["degrade_note"])
    html = pack.get("html") or ""
    if not html:
        _hint = str(pack.get("html_error") or "")
        if _hint:
            # 后台化改造：HTML 生成失败在 worker 内置空 + 带标记（线程内禁 st.*），
            # 这里随 pack 持久化展示，话术沿用原 friendly_error 的原因/动作要点
            st.caption("⚠️ 图谱可视化 HTML 生成失败（下方表格版图论指标不受影响）。")
            st.caption(f"🩺 可能原因：lib/vis-9.1.2 可视化库文件缺失；{_hint[:160]}")
            st.caption("🛠️ 怎么办：检查 lib/ 目录完整性；表格版图论指标仍可正常使用。")
        else:
            st.caption("⚠️ 图谱可视化 HTML 不可用（下方表格版图论指标不受影响）。")
    if html:
        embed_html(html, height=560)

    nodes = pack.get("nodes") or []
    if not nodes:
        empty_state("图谱为空节点。", hints=["点击左侧「一键加载演示数据（6 篇）」", "或在文献库上传 PDF"])
        return

    # KPI 指标条先行（Round 17 UI/UX，借鉴 Stripe/Vercel 仪表盘：hero 指标先行、
    # 明细表格后置）——用户第一眼就该看到「几张网、几条边、谁才是基石」，
    # 而不是先面对一张 7 列的图论指标大表。
    metric_strip([
        ("逻辑节点", len(nodes), "同一 arXiv 多版本已合并"),
        ("引用边", len(pack.get("edges") or []),
         "演示模式为示意关系" if pack.get("demo") else "真实引用关系"),
        ("🏆 基石论文", len(pack.get("corner") or []), "PageRank 领先"),
        ("🌿 边缘衍生", len(pack.get("edge") or []), "暂未被他文引用"),
    ])

    # 语料规模坦诚说明：逻辑节点远低于赛题「20 篇批量文献」时明确标注，
    # 不把 2-3 节点小图上的社区/PageRank 结果冒充成大规模能力。
    # 属说明性信息（不阻断操作），从 warning 色块降级为一行灰字。
    if len(nodes) < g.SMALL_CORPUS_NODES:
        st.caption(
            f"语料规模说明：版本合并后仅 {len(nodes)} 个逻辑节点（同一 arXiv 号的多版本 PDF"
            f"合并为 1 个），未达『20 篇批量文献』规模——社区/PageRank 在极小图上区分度有限，"
            f"此处只证明『解析→建图→分析→综述』链路可用。")

    # 数字明细默认收起：首屏不摊开 7 列表。
    with st.expander("图谱指标明细", expanded=False):
        pretty_table(_metrics_rows(ctx, nodes, pack.get("metrics") or {}), title="图论指标")
        if pack.get("corner"):
            st.markdown("**基石论文（PageRank 领先）**："
                        + "、".join(f"《{_title(ctx, n)}》" for n in pack["corner"]))
        if pack.get("edge"):
            st.markdown("**边缘衍生（暂未被他文引用）**："
                        + "、".join(f"《{_title(ctx, n)}》" for n in pack["edge"]))

    edges = pack.get("edges") or []
    if edges:
        with st.expander(f"引用关系（{len(edges)} 条）", expanded=False):
            for item in edges[:MAX_EDGE_SHOW]:
                try:
                    a, b, ev = item
                except (TypeError, ValueError):
                    continue
                st.markdown(f"《{_title(ctx, a)}》 → 引用 → 《{_title(ctx, b)}》")
                st.caption(str(ev)[:160])
            if len(edges) > MAX_EDGE_SHOW:
                st.caption(f"ℹ️ 共 {len(edges)} 条，已展示前 {MAX_EDGE_SHOW} 条。")
    else:
        st.caption("📭 未检测到引用边（孤立节点图谱仅展示图论指标）。")

    # 时间线 + 主题簇：纯 networkx 确定性计算（非 LLM 生成），让小图谱也有信息量。
    # 默认折叠：它是"补充读物"，不该在首屏与主图争夺注意力。
    G = pack.get("G")
    if G is not None and not pack.get("demo") and G.number_of_nodes() > 0:
        try:
            stx = g.review_structure(ctx, G, pack.get("metrics") or {}, pack.get("corner") or [])
        except Exception:
            stx = None
        if stx:
            with st.expander("时间线与主题簇（networkx 确定性计算，非 LLM 生成）", expanded=False):
                if stx["timeline"]:
                    for y, papers in stx["timeline"]:
                        st.markdown(f"- **{y}**：" + "、".join(f"《{_title(ctx, p)}》" for p in papers))
                else:
                    st.caption("文献元数据无可用年份（arXiv 编号回退也未命中），时间线不可用——不做推断。")
                for c in stx["clusters"]:
                    reps = "、".join(f"《{_title(ctx, r)}》" for r in c["reps"])
                    st.markdown(f"- 主题簇 #{c['id']}「{c['label']}」（{c['size']} 篇）：代表 {reps}")


def render(ctx):
    _init()
    # 后台任务交付：三个长任务（构建图谱 / 领域综述 / 前沿扫描）完成（含失败）时
    # 按 kind 认领，一次性交付并触发整页重绘。必须放在页面顶部——flash 由 app.py
    # 在页面渲染前重放，交付里写入的提示依赖 bg_consume 内部的重绘才能在当轮可见。
    bg_consume({
        _GRAPH_BUILD_KIND: lambda k, t: _consume_build(ctx, k, t),
        _GRAPH_REVIEW_KIND: lambda k, t: _consume_review(ctx, k, t),
        _GRAPH_FUTURE_KIND: lambda k, t: _consume_future(ctx, k, t),
    })
    page_header("引用图谱与综述",
                "自动解析论文间真实引用关系，PageRank/中心性挖掘基石论文，结合时间线过滤已被解决的旧 idea。")

    if not require_library(ctx):
        return

    # 首屏信息预算：页头 → 一行状态 caption → 主按钮/主视图。
    # 指标表、引用边、时间线、演示设置全部进折叠区，打开本页不再被多段说明铺满。
    _pack = st.session_state.get("graph_pack")
    _nodes0 = list(_pack.get("nodes") or []) if isinstance(_pack, dict) else []
    _n_papers = len(getattr(ctx, "paper_order", []) or [])
    if _nodes0:
        st.caption(f"{_n_papers} 篇文献 · 图谱已构建：{len(_nodes0)} 个节点 · "
                   f"{len(_pack.get('edges') or [])} 条引用边")
    else:
        st.caption(f"{_n_papers} 篇文献 · 尚未构建图谱")

    # ===== 区一：引用图谱可视化（构建 / 结果 / 演示，均属图谱范畴） =====
    # 不再单独起 section_header：页头已表明本页主体是图谱，这里直接给主按钮，
    # 避免「页头 28px + 分区 18px」两级标题连排、把主操作挤到第二屏。
    with st.container(border=True, key="card05"):
        _gr_build = bg_running(kinds={_GRAPH_BUILD_KIND})
        if st.button("构建引用图谱", disabled=_gr_build,
                     help="后台构建：提交后可切换其他页面，实时进度见左侧任务中心"):
            # 后台化改造（2026-10 R19）：原内联流程跑主线程，跑时整个 UI 冻结。
            # worker 内完成建图 + HTML + pack 组装并返回 pack，交付处理器写回
            # graph_pack（落点与改造前完全一致）。
            bg_submit("构建引用图谱",
                      lambda progress: _build_worker(ctx, progress),
                      kind=_GRAPH_BUILD_KIND)
            try:
                st.toast("🕸️ 已转入后台构建，进度见左侧任务中心，可随时切换其他页面",
                         icon="🚀")
            except Exception:
                pass
        if _gr_build:
            st.caption("⏳ 图谱构建正在后台运行（实时进度见左侧「任务中心」）——"
                       "可切换其他页面，完成后结果自动出现在本页。")

        pack = st.session_state.get("graph_pack")
        if pack and isinstance(pack, dict):
            try:
                _render_pack(ctx, pack)
            except Exception as e:
                friendly_error("图谱结果渲染", e, reasons=["图谱数据结构不完整"],
                               actions=["点上方按钮重新构建图谱"])
        else:
            empty_state_card(
                "尚未构建图谱",
                desc="构建后，这里会展示引用关系网络图、KPI 指标与图论指标表、基石/边缘论文结论。",
                actions=["点上方「构建引用图谱」，解析文献间的真实引用关系",
                         "演示数据加载后可一键生成 BERT→Transformer 关系",
                         "无真实引用边时，展开下方「渲染设置」用演示模式示意图谱兜底"],
                footer="⚠️ 本地演示语料经版本合并后仅 2-3 个逻辑节点，未达赛题『20 篇』规模；"
                       "图谱指标在小语料下仅供参考，上传更多含 References 的 PDF 效果更佳。",
            )

        # 演示模式属于「兜底渲染方式」，不是主路径：收进渲染设置，避免与主按钮并列
        # 抢注意力（按钮文案/key 不变，功能仍直达）。
        with st.expander("渲染设置", expanded=False):
            st.caption("无真实引用边时，可按主题相似度生成示意关系（示意，非真实引用）。")
            if st.button("🎭 演示模式示意图谱"):
                with st.spinner(step_text("🎭 正在生成演示模式示意图谱")):
                    try:
                        G, edges, metrics, corner, edge, degrade_note = _build_demo(ctx)
                        try:
                            html = g.graph_html(G, metrics, corner)
                        except Exception:
                            html = ""
                        pack = _make_pack(G, edges, metrics, corner, edge, demo=True)
                        pack["html"] = html
                        pack["degrade_note"] = degrade_note
                        st.session_state.graph_pack = pack
                        # 本按钮位于「图谱结果读取」之后，若不 rerun，本轮页面上方的
                        # 「尚未构建图谱」空状态会与新生成的图谱并存，自相矛盾。
                        # 提示写进 flash 以跨 rerun 存活（直接 st.success 会被吞）。
                        set_flash("success", f"✅ 演示图谱已生成：{len(pack['nodes'])} 节点 · "
                                             f"{len(edges)} 条示意关系")
                        st.rerun()
                    except Exception as e:
                        friendly_error("演示模式示意图谱", e,
                                       reasons=["论文数量不足 2 篇", "embedding 推理失败"],
                                       actions=["先加载演示数据（6 篇）", "或上传至少 2 篇 PDF"])

    # ===== 区二：领域综述与前沿方向生成（与图谱可视化明确区分） =====
    # 原先这里有一条 st.markdown("---")：但紧跟其后的 _sec_head 本身已经是分区信号，
    # 横线 + 标题 = 双重视觉打断，删掉横线用留白分区更干净。
    section_header("领域综述与前沿方向生成")
    with st.container(border=True, key="card06"):
        c2, c3 = st.columns([1, 1])
        _gr_review = bg_running(kinds={_GRAPH_REVIEW_KIND})
        _gr_future = bg_running(kinds={_GRAPH_FUTURE_KIND})
        if c2.button("📝 一键生成领域综述", disabled=_gr_review,
                     help="后台生成：提交后可切换其他页面，实时进度见左侧任务中心"):
            if llm_guard(ctx, "领域综述生成"):
                # 后台化改造（2026-10 R19）：该步压测 31.90s，原内联 st.status 跑主线程，
                # 全程 UI 冻结。llm_guard 等前置检查保留在主线程提交前；图包 G 在提交时
                # 由主线程闭包捕获（worker 线程禁止访问 st.session_state）——复用已构建的
                # 真实图谱（演示图谱不复用），避免重复解析引用关系。
                _pack = st.session_state.get("graph_pack") or {}
                _G = _pack.get("G") if isinstance(_pack, dict) else None
                if not isinstance(_pack, dict) or _pack.get("demo"):
                    _G = None
                bg_submit("一键生成领域综述",
                          lambda progress, _G=_G: _review_worker(ctx, _G, progress),
                          kind=_GRAPH_REVIEW_KIND)
                try:
                    st.toast("📝 已转入后台生成，进度见左侧任务中心，可随时切换其他页面",
                             icon="🚀")
                except Exception:
                    pass
        if _gr_review:
            c2.caption("⏳ 领域综述正在后台生成（实时进度见左侧「任务中心」）——"
                       "可切换其他页面，完成后结果自动出现在本页。")
        if c3.button("🚀 Future Work 前沿扫描（排除已解决）", disabled=_gr_future,
                     help="后台扫描：提交后可切换其他页面，实时进度见左侧任务中心"):
            if llm_guard(ctx, "Future Work 前沿扫描"):
                # 后台化改造（2026-10 R19）：同综述——G 闭包传入，结果与降级档位
                # 由 worker 返回，交付处理器写回 session_state["future"]。
                _pack = st.session_state.get("graph_pack") or {}
                _G = _pack.get("G") if isinstance(_pack, dict) else None
                if not isinstance(_pack, dict) or _pack.get("demo"):
                    _G = None
                bg_submit("Future Work 前沿扫描",
                          lambda progress, _G=_G: _future_worker(ctx, _G, progress),
                          kind=_GRAPH_FUTURE_KIND)
                try:
                    st.toast("🚀 已转入后台扫描，进度见左侧任务中心，可随时切换其他页面",
                             icon="🚀")
                except Exception:
                    pass
        if _gr_future:
            c3.caption("⏳ 前沿扫描正在后台运行（实时进度见左侧「任务中心」）——"
                       "可切换其他页面，完成后结果自动出现在本页。")

        review_txt = st.session_state.get("review", "") or ""
        future_txt = st.session_state.get("future", "") or ""
        if review_txt:
            st.markdown("##### 领域综述")
            # 降级内容先给可见标识，再展示正文（覆盖后续所有 rerun）
            if is_llm_degraded(review_txt):
                llm_degraded_banner()
            elif is_llm_offline_generated(review_txt):
                llm_offline_generated_banner("领域综述")
            long_block(review_txt, title="领域综述全文", limit=200)
        if future_txt:
            st.markdown("##### 前沿方向")
            if is_llm_degraded(future_txt):
                llm_degraded_banner()
            elif is_llm_offline_generated(future_txt):
                llm_offline_generated_banner("前沿方向")
            long_block(future_txt, title="前沿方向全文", limit=200)

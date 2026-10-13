"""页面 5：局域引用图谱与学术综述。真实引用关系、图论基石/边缘挖掘、自动综述、Future Work 前沿过滤。"""
import numpy as np
import pandas as pd
import streamlit as st
from html import escape as _esc

from services import graph as g
from services import library as _lib
from ui.common import (embed_html, require_library, page_header, pretty_table, ensure_state,
                       friendly_error, empty_state, llm_guard, long_block, step_text, at)
from ui.sidebar import set_flash

# 分区卡片样式（与 common.page_header 同色系）。图谱可视化区与综述/前沿生成区分区，层级一致。
_SEC_CSS = """<style>
.sec{border-radius:10px;margin:0 0 4px;padding:10px 14px;
border-left:4px solid #3b6fb6;background:#f3f7fc;}
.sec .t{font-size:16px;font-weight:700;color:#173a63;line-height:1.4;}
</style>"""


def _sec_head(title: str, icon: str = ""):
    """统一分区标题：一致层级 + 留白，便于图谱可视化区与生成区清晰区分。"""
    head = f"{icon} {title}".strip() if icon else str(title or "")
    st.markdown(_SEC_CSS + f"<div class='sec'><span class='t'>{_esc(head)}</span></div>",
                unsafe_allow_html=True)

# 引用关系列表一次展示的最大条数（其余折叠）
MAX_EDGE_SHOW = 12


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
    return pd.DataFrame({
        "论文": [_title(ctx, n) for n in nodes],
        "文件名": list(nodes),
        "年份": [g._node_year(ctx, n) or "未知" for n in nodes],
        "PageRank": [round(float(pr.get(n, 0)), 4) for n in nodes],
        "被引次数(入度)": [int(ind.get(n, 0)) for n in nodes],
        "中介中心性": [round(float(bet.get(n, 0)), 3) for n in nodes],
        "特征向量": [round(float(eig.get(n, 0)), 4) for n in nodes],
    }).sort_values("PageRank", ascending=False)


def _build_real(ctx, bar=None, note=None):
    """真实引用关系建图（保持原业务逻辑，仅补异常兜底）。"""
    G, edges = g.build_graph(ctx, progress_cb=(lambda r: bar.progress(min(max(float(r), 0.0), 1.0)))
                             if bar is not None else None)
    if note is not None:
        note.caption(step_text("📊 图论指标计算"))
    if bar is not None:
        bar.progress(1.0)
    metrics = g.analyze(G)
    corner, edge = g.classify_papers(G, metrics)
    return G, edges, metrics, corner, edge


def _build_demo(ctx):
    """演示模式兜底图谱：无真实引用关系时，按主题相似度生成「示意图谱」。

    仅在⬆️真实引用边为空且用户点按钮时触发；每张图取最相似的 1 条示意关系，
    方向按年份从新到旧（近似「新论文参考旧工作」），输出处明确标注「示意，非真实引用」。
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
    vecs = ctx.embed([text[n] for n in reps])

    def cos(a, b):
        a = np.asarray(a, dtype=float)
        b = np.asarray(b, dtype=float)
        d = (np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / d) if d else 0.0

    G = nx.DiGraph()
    for n in reps:
        G.add_node(n, title=_title(ctx, n), version=ctx.papers.get(n, {}).get("version", ""))
    edges = []
    for i, a in enumerate(reps):
        sims = sorted(((cos(vecs[i], vecs[j]), reps[j]) for j in range(len(reps)) if j != i),
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
            ev = f"演示模式：主题相似度 {float(sim):.0%}（示意关系，非真实引用）"
            edges.append((src, dst, ev))
    if not edges:
        raise ValueError("论文主题差异过大，未能生成示意关系")
    metrics = g.analyze(G)
    corner, edge = g.classify_papers(G, metrics)
    return G, edges, metrics, corner, edge


def _make_pack(G, edges, metrics, corner, edge, demo=False):
    # G 一并放进 pack：综述/Future Work 按钮可复用已建好的图，省去重复解析（秒级）
    return {"html": None, "metrics": metrics, "corner": corner or [], "edge": edge or [],
            "edges": edges or [], "nodes": list(G.nodes), "demo": demo, "G": G}


def _render_pack(ctx, pack):
    """渲染图谱 + 指标表 + 基石/边缘结论（HTML 生成失败不影响其余展示）。"""
    if pack.get("demo"):
        st.warning("🎭 当前为**演示模式示意图谱**：依据主题相似度生成，**不代表真实引用关系**。"
                   "需真实引用请上传含 References 章节的完整 PDF。")
    html = pack.get("html") or ""
    if not html:
        st.caption("ℹ️ 图谱可视化 HTML 不可用（下方表格版图论指标不受影响）。")
    if html:
        embed_html(html, height=560)

    nodes = pack.get("nodes") or []
    if not nodes:
        empty_state("图谱为空节点。", hints=["点击左侧「🚀 一键加载演示数据」", "或在📁文献库上传 PDF"])
        return

    # 语料规模坦诚说明：逻辑节点远低于赛题「20 篇批量文献」时明确标注，
    # 不把 2-3 节点小图上的社区/PageRank 结果冒充成大规模能力
    if len(nodes) < g.SMALL_CORPUS_NODES:
        st.warning(
            f"⚠️ **语料规模坦诚说明**：本地语料经版本合并后仅 **{len(nodes)} 个逻辑节点**"
            f"（同一 arXiv 号的多版本 PDF 会合并为 1 个节点），未达赛题『20 篇批量文献』规模。"
            f"社区发现 / PageRank / 时间线在极小图上区分度有限——此处只证明"
            f"『解析→建图→分析→综述』链路可用，不代表 20 篇规模下的效果。")
    pretty_table(_metrics_rows(ctx, nodes, pack.get("metrics") or {}), title="📊 图论指标")

    if pack.get("corner"):
        st.success("🏆 基石论文：" + "、".join(f"《{_title(ctx, n)}》" for n in pack["corner"]))
    if pack.get("edge"):
        st.info("🌿 边缘衍生（暂未被他文引用）：" + "、".join(f"《{_title(ctx, n)}》" for n in pack["edge"]))

    edges = pack.get("edges") or []
    if edges:
        with st.expander(f"🔗 引用关系（{len(edges)} 条）", expanded=False):
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
        st.info("📭 未检测到引用边（孤立节点图谱仅展示图论指标）。")

    # 时间线 + 主题簇：纯 networkx 确定性计算（非 LLM 生成），让小图谱也有信息量
    G = pack.get("G")
    if G is not None and not pack.get("demo") and G.number_of_nodes() > 0:
        try:
            stx = g.review_structure(ctx, G, pack.get("metrics") or {}, pack.get("corner") or [])
        except Exception:
            stx = None
        if stx:
            with st.expander("🗓️ 时间线与主题簇（networkx 确定性计算，非 LLM 生成）", expanded=True):
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
    page_header("🕸️ 引用图谱与综述",
                "自动解析论文间真实引用关系，PageRank/中心性挖掘基石论文，结合时间线过滤已被解决的旧 idea。",
                icon="🕸️")

    if not require_library(ctx):
        return

    # ===== 区一：引用图谱可视化（构建 / 结果 / 演示，均属图谱范畴） =====
    _sec_head("引用图谱可视化", "🕸️")
    with st.container(border=True):
        if st.button("🕸️ 构建引用图谱"):
            bar = st.progress(0.0)
            note = st.empty()
            try:
                note.caption(step_text("🕸️ 解析引用关系"))
                G, edges, metrics, corner, edge = _build_real(ctx, bar=bar, note=note)
                try:
                    html = g.graph_html(G, metrics, corner)
                except Exception as e:
                    html = ""
                    friendly_error("图谱 HTML 生成", e,
                                   reasons=["lib/vis-9.1.2 可视化库文件缺失"],
                                   actions=["检查 lib/ 目录完整性", "表格版图论指标仍可正常使用"])
                try:
                    bar.empty()
                    note.empty()
                except Exception:
                    pass
                pack = _make_pack(G, edges, metrics, corner, edge)
                pack["html"] = html
                st.session_state.graph_pack = pack
                if edges:
                    st.success(f"✅ 图谱构建完成：{len(pack['nodes'])} 个节点 · {len(edges)} 条真实引用关系")
                else:
                    st.warning("⚠️ 未检测到文献间的真实引用关系，已生成仅含节点的图谱。")
                    st.caption("🩺 可能原因：论文正文未包含 References 章节 / 演示语料引用条目过少。")
                    st.caption("🛠️ 怎么办：①上传含完整参考文献的真实 PDF（如 BERT 与 Transformer）；"
                               "②或点下方「🎭 演示模式示意图谱」兜底演示。")
            except Exception as e:
                try:
                    bar.empty()
                    note.empty()
                except Exception:
                    pass
                friendly_error("构建引用图谱", e,
                               reasons=["论文正文缺少 References 章节导致解析为空",
                                        "embedding 推理失败 / 内存不足"],
                               actions=["重试一次", "点下方「🎭 演示模式示意图谱」",
                                        "或减少文献数量后重试"])

        pack = st.session_state.get("graph_pack")
        if pack and isinstance(pack, dict):
            try:
                _render_pack(ctx, pack)
            except Exception as e:
                friendly_error("图谱结果渲染", e, reasons=["图谱数据结构不完整"],
                               actions=["点上方按钮重新构建图谱"])
        else:
            empty_state("尚未构建图谱。",
                        hints=["点上方「🕸️ 构建引用图谱」",
                               "演示数据加载后可一键生成 BERT→Transformer 关系",
                               "⚠️ 本地演示语料经版本合并后仅 2-3 个逻辑节点，未达赛题『20 篇』规模；"
                               "图谱指标在小语料下仅供参考，上传更多含 References 的 PDF 效果更佳"])

        st.markdown("---")
        if st.button("🎭 演示模式示意图谱"):
            with st.spinner(step_text("🎭 正在生成演示模式示意图谱")):
                try:
                    G, edges, metrics, corner, edge = _build_demo(ctx)
                    try:
                        html = g.graph_html(G, metrics, corner)
                    except Exception:
                        html = ""
                    pack = _make_pack(G, edges, metrics, corner, edge, demo=True)
                    pack["html"] = html
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
                                   actions=["先加载演示数据（v1/v7/BERT）", "或上传至少 2 篇 PDF"])

    # ===== 区二：领域综述与前沿方向生成（与图谱可视化明确区分） =====
    st.markdown("---")
    _sec_head("领域综述与前沿方向生成", "📝")
    with st.container(border=True):
        c2, c3 = st.columns([1, 1])
        if c2.button("📝 一键生成领域综述"):
            if llm_guard(ctx, "领域综述生成"):
                # 话术 3：该步压测 31.90s，静态 spinner 全程文案不变，演示时观感等同卡死。
                # 改用 st.status 并把 generate_review 上报的阶段实时反映到标题上。
                with st.status(step_text("📝 正在生成领域综述"), expanded=True) as status:

                    def _rev_progress(stage):
                        try:
                            status.update(label=f"📝 正在生成领域综述 · {stage}")
                        except Exception:
                            pass

                    try:
                        # 复用已构建的真实图谱（演示图谱不复用），避免重复解析引用关系
                        pack = st.session_state.get("graph_pack") or {}
                        G = pack.get("G") if isinstance(pack, dict) else None
                        if G is None or pack.get("demo"):
                            _rev_progress("⓪ 解析引用关系建图…")
                            G, _ = g.build_graph(ctx)
                        metrics = g.analyze(G)
                        corner, edge = g.classify_papers(G, metrics)
                        st.session_state["review"] = g.generate_review(
                            ctx, G, metrics, corner, progress_cb=_rev_progress) or ""
                        status.update(label="✅ 领域综述已生成", state="complete")
                        st.success("✅ 领域综述已生成")
                    except Exception as e:
                        friendly_error("生成综述", e,
                                       reasons=["大模型返回超时 / 额度不足", "文献库为空导致无素材"],
                                       actions=["重试一次", "或减少单次综述的文献数量"])
        if c3.button("🚀 Future Work 前沿扫描（排除已解决）"):
            if llm_guard(ctx, "Future Work 前沿扫描"):
                with st.spinner(step_text("🚀 正在扫描前沿方向")):
                    try:
                        pack = st.session_state.get("graph_pack") or {}
                        G = pack.get("G") if isinstance(pack, dict) else None
                        if G is None or pack.get("demo"):
                            G, _ = g.build_graph(ctx)
                        st.session_state["future"] = g.explore_future(ctx, G) or ""
                        st.success("✅ 前沿方向扫描完成")
                    except Exception as e:
                        friendly_error("前沿扫描", e,
                                       reasons=["大模型返回超时 / 额度不足", "图谱为空"],
                                       actions=["先点「🕸️ 构建引用图谱」", "或重试一次"])

        review_txt = st.session_state.get("review", "") or ""
        future_txt = st.session_state.get("future", "") or ""
        if review_txt:
            st.markdown("##### 📖 领域综述")
            long_block(review_txt, title="领域综述全文", limit=200)
        if future_txt:
            st.markdown("##### 🚀 前沿方向")
            long_block(future_txt, title="前沿方向全文", limit=200)

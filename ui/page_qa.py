"""页面 2：抗幻觉跨文献问答。混合检索 + 重排，答案内联【n】引用，下方给出可点击跳转的溯源锚点。

改动要点（抗幻觉 RAG 专项）：
- 主问答流程统一调用 services/rag.py 的 answer()，不再内联重写，保证自动修复环生效；
- 溯源卡片在本文件内自建增强版（不改 ui/common.py）：带 href="#p{page}" 锚点 + 跳转按钮；
- 跳转写入 session_state["qa_jump_page"]（结构化）与 query_params（阅读页已实现的消费方）；
- 支持"全部文献 / 单篇"范围锁定、多篇勾选对比、拒答闸门与可疑引用的可视化。
"""
import html as _html

import streamlit as st
from services import rag
from ui.common import require_library, page_header


EX = ["这篇论文提出了什么模型？", "训练用了什么 GPU？数量和型号？",
      "对比 Transformer 与 BERT 的架构（表格）", "GPT/BERT/Transformer 参数量演化",
      "Adam 的 beta_1、beta_2 是多少？BLEU 分数分别是多少？"]

# ---------- 本页自持常量（不改动 config.py） ----------
SNIPPET_LEN = 200            # 溯源卡片折叠区展示的原文片段长度（字）
MAX_COMPARE_PAPERS = 5       # 多篇对比最多勾选篇数
JUMP_KEY = "qa_jump_page"    # 跨页联动 session_state key
READING_PAGE_IDX = 3         # 📖 版式阅读页在 MENU 中的索引（ui/sidebar.py MENU / app.py）


def _init_state():
    """所有 session_state key 先初始化再读。"""
    if "qa_runs" not in st.session_state:
        st.session_state["qa_runs"] = []
    if "qa_q" not in st.session_state:
        st.session_state["qa_q"] = ""
    if JUMP_KEY not in st.session_state:
        st.session_state[JUMP_KEY] = None
    if "qa_scope_mode" not in st.session_state:
        st.session_state["qa_scope_mode"] = "全部文献"
    if "qa_single_paper" not in st.session_state:
        st.session_state["qa_single_paper"] = ""
    if "qa_compare_papers" not in st.session_state:
        st.session_state["qa_compare_papers"] = []
    if "qa_use_rerank" not in st.session_state:
        st.session_state["qa_use_rerank"] = False
    if "qa_run_seq" not in st.session_state:
        st.session_state["qa_run_seq"] = 0


def _esc(s):
    return _html.escape(str(s or ""), quote=True)


def anchor_card(source, page, score=0.0, para_idx=None, n=None):
    """增强版溯源卡片（本文件自建，不改 ui/common.py 的 source_card）。

    三处可跳转：
    1) 卡片顶部 <a id="p{page}"> 作为页内锚点落点；
    2) 页码徽章 <a href="#p{page}"> —— 页内锚点跳转；
    3) 徽章旁的"打开阅读页" <a href="?p=3&jump_page=N&paper=X"> —— 跨页跳到版式阅读页
       （ui/page_reading.py:43-60 已消费 jump_page / paper 两个 query 参数）。
    """
    name = _esc(source)
    tag = f"【{n}】 " if n else ""
    para = f" · 第{int(para_idx)}段" if para_idx else ""
    badge = (f'<a href="#p{int(page)}" style="text-decoration:none;color:#fff;display:inline-block;">'
             f'第 {int(page)} 页</a>')
    cross = (f'<a href="?p={READING_PAGE_IDX}&jump_page={int(page)}&paper={name}" target="_self" '
             f'style="color:#27518a;font-size:11.5px;">📖 打开阅读页</a>')
    return (
        f'<a id="p{int(page)}"></a>'
        f'<div style="background:#f7faff;border-left:4px solid #3b6fb6;border-radius:8px;'
        f'padding:12px 16px;margin:8px 0;box-shadow:0 1px 2px rgba(0,0,0,.05);">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
        f'<strong style="color:#27518a;font-size:13.5px;">{tag}📄 {name}</strong>'
        f'<span style="background:#3b6fb6;color:#fff;padding:1px 10px;border-radius:12px;'
        f'font-size:11.5px;">{badge}</span></div>'
        f'<div style="color:#666;font-size:11.5px;margin-bottom:4px;">'
        f'相关度 {float(score):.2f}{para}　{cross}</div></div>'
    )


def _set_jump(source, page, para_idx=None):
    """写入跨页联动状态：结构化 session_state + 阅读页已在消费的 query_params。"""
    try:
        payload = {"source": str(source), "page": int(page),
                   "para_idx": int(para_idx) if para_idx else None, "from": "qa"}
    except (TypeError, ValueError):
        payload = {"source": str(source), "page": 1, "para_idx": None, "from": "qa"}
    st.session_state[JUMP_KEY] = payload
    try:
        st.query_params["jump_page"] = str(payload["page"])
        st.query_params["paper"] = str(payload["source"])
    except Exception as e:
        st.caption(f"（写入 URL 参数失败，已仅用会话状态记录：{str(e)[:80]}）")
    st.success(f"已定位到《{payload['source']}》第 {payload['page']} 页"
               + (f"（第 {payload['para_idx']} 段）" if payload["para_idx"] else "")
               + "，可到📖版式阅读页查看。")


def _render_source(rid, i, c):
    source = c.get("source", "未知来源")
    try:
        page = int(c.get("page", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    st.markdown(anchor_card(source, page, c.get("score", 0.0), c.get("para_idx"), n=i),
                unsafe_allow_html=True)
    col_btn, col_meta, col_snip = st.columns([1.4, 1.0, 3.0])
    with col_btn:
        if st.button(f"📖 定位到《{str(source)[:16]}》第 {page} 页", key=f"jump_{rid}_{i}"):
            _set_jump(source, page, c.get("para_idx"))
    with col_meta:
        st.caption(f"相关度 {float(c.get('score', 0.0)):.2f}"
                   + (f"｜第 {c['para_idx']} 段" if c.get("para_idx") else ""))
    with col_snip:
        with st.expander(f"原文片段（前 {SNIPPET_LEN} 字）", expanded=False):
            st.text((c.get("text") or "")[:SNIPPET_LEN])


def _render_debug(run):
    d = run.get("debug") or {}
    st.caption(f"向量召回 {len(d.get('dense') or [])} 条，BM25 召回 {len(d.get('sparse') or [])} 条，"
               f"RRF 融合后 {len(d.get('fused') or [])} 条")
    if d.get("variants"):
        st.caption("🌐 查询扩展（dense 与 sparse 双路独立参与 RRF）：" + " ┃ ".join(d["variants"]))
    dense_ranks = d.get("dense_variant_ranks") or []
    if len(dense_ranks) > 1:
        st.caption(f"🧬 dense 路消费 {len(dense_ranks)} 个查询变体（含原查询），结果按 RRF(k=60) 合并")
    if d.get("source_filter"):
        st.caption(f"🔒 范围已锁定单篇：{d['source_filter']}")
    if d.get("per_paper"):
        st.caption("📚 对比模式每篇取到：" + "，".join(f"{k}×{v}条" for k, v in d["per_paper"].items()))
    st.caption(f"🎯 最高相关度 {float(d.get('top_score') or 0):.2f}"
               f"（拒答阈值 {rag.RELEVANCE_FLOOR}）；自动修复次数 {int(d.get('repairs') or 0)}"
               f"（上限 {rag.MAX_REPAIR}）")
    hit = set(d.get("sparse") or []) & set(d.get("dense") or [])
    if hit:
        st.caption(f"✅ 双路共同命中 {len(hit)} 条——混合检索交叉验证降低漏检")


def render(ctx):
    page_header("💬 跨文献问答与溯源",
                "Dense+Sparse 混合检索（多查询变体 RRF）→ 可选 LLM 重排 → 引用校验 + 一次自动修复；"
                "每个结论带【n】内联引用与可点击的原文出处锚点。")
    if not require_library(ctx):
        return
    _init_state()

    # 跨页联动提示：本页写入，📖版式阅读页消费（见报告"跨文件约定"）
    pending = st.session_state.get(JUMP_KEY)
    if pending:
        st.info(f"🎯 当前待跳转定位：《{pending.get('source')}》第 {pending.get('page')} 页"
                + (f"（第 {pending.get('para_idx')} 段）" if pending.get("para_idx") else "")
                + f"。请到左侧导航切换到「📖 版式阅读与翻译」查看（session_state['{JUMP_KEY}']）。")

    cols = st.columns(3)
    for i, e in enumerate(EX):
        if cols[i % 3].button(e, key=f"ex{i}"):
            st.session_state["qa_q"] = e
            st.rerun()

    # ---------- 范围选择（单篇锁定 / 多篇对比） ----------
    scope = st.radio("检索范围", ["全部文献", "单篇"], horizontal=True, key="qa_scope_mode")
    source_filter = None
    if scope == "单篇" and ctx.files:
        cur = st.session_state.get("qa_single_paper")
        if cur not in ctx.files:
            st.session_state["qa_single_paper"] = ctx.files[0]
        source_filter = st.selectbox("锁定论文（只在该篇内检索）", ctx.files, key="qa_single_paper")

    st.session_state["qa_compare_papers"] = [p for p in st.session_state.get("qa_compare_papers", [])
                                             if p in ctx.files]
    compare_papers = st.multiselect(
        f"多篇对比：勾选 2~{MAX_COMPARE_PAPERS} 篇（走按论文分组的对比流程，输出分论文陈述 + 对比表格）",
        ctx.files, key="qa_compare_papers")
    if len(compare_papers) > MAX_COMPARE_PAPERS:
        st.warning(f"最多勾选 {MAX_COMPARE_PAPERS} 篇，已自动截取前 {MAX_COMPARE_PAPERS} 篇。")
        compare_papers = compare_papers[:MAX_COMPARE_PAPERS]
    if scope == "单篇" and compare_papers:
        st.caption("ℹ️ 已选择单篇锁定，多篇对比勾选本次不生效（两种模式互斥）。")
        compare_papers = []
    if len(compare_papers) == 1:
        st.caption("ℹ️ 多篇对比至少需要 2 篇，本次按普通问答处理。")
        compare_papers = []

    use_rerank = st.checkbox("启用 LLM 重排（更精准，耗额度）", key="qa_use_rerank")

    with st.form("qa_form", clear_on_submit=True):
        q = st.text_input("请输入问题", value=st.session_state.get("qa_q", ""))
        go = st.form_submit_button("🔎 提问")

    if go and q.strip():
        if not getattr(ctx.llm, "available", False):
            st.warning("请先在左侧填入 API Key。")
        else:
            with st.spinner("正在检索、生成并校验答案…"):
                try:
                    result = rag.answer(ctx, q.strip(), use_rerank=use_rerank,
                                        source_filter=source_filter,
                                        compare_papers=list(compare_papers) or None)
                except Exception as e:
                    st.error(f"问答失败：{str(e)[:200]}")
                    result = None
            if result:
                st.session_state["qa_runs"].append({
                    "id": st.session_state["qa_run_seq"],
                    "q": q.strip(),
                    "a": result.get("answer", ""),
                    "sources": result.get("sources", []),
                    "issues": result.get("issues", []),
                    "debug": result.get("debug", {}),
                    "refused": bool(result.get("refused")),
                    "top_score": result.get("top_score", 0.0),
                    "scope": f"单篇：{source_filter}" if source_filter else (
                        "对比：" + "、".join(compare_papers) if compare_papers else "全部文献"),
                })
                st.session_state["qa_run_seq"] += 1
                st.session_state["qa_q"] = ""
                st.rerun()

    # ---------- 历史问答渲染 ----------
    for run in reversed(st.session_state.get("qa_runs", [])):
        rid = run.get("id", 0)
        st.markdown(f"#### 🙋 {run.get('q', '')}")
        st.caption(f"检索范围：{run.get('scope', '全部文献')}")
        if run.get("refused"):
            st.warning("🛑 " + str(run.get("a", "")))
        else:
            st.markdown(str(run.get("a", "")))
        if run.get("issues"):
            st.warning("⚠️ 幻觉防护校验未完全通过：\n" + "\n".join(f"- {x}" for x in run["issues"]))
        else:
            st.success("✅ 引用校验通过：角标均在有效来源范围内，且与来源文本存在实质词汇重叠")
        srcs = run.get("sources") or []
        if srcs:
            with st.expander(f"🔍 溯源（{len(srcs)} 条来源，点击可定位原文）", expanded=False):
                for i, c in enumerate(srcs, 1):
                    _render_source(rid, i, c)
        with st.expander("🛠️ 检索调试", expanded=False):
            _render_debug(run)
        st.markdown("---")

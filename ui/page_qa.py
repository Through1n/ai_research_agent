"""页面 2：抗幻觉跨文献问答。混合检索 + 重排，答案内联【n】引用，下方给出可点击跳转的溯源锚点。

改动要点（抗幻觉 RAG 专项）：
- 主问答流程统一调用 services/rag.py 的 answer()，不再内联重写，保证自动修复环生效；
- 溯源卡片在本文件内自建增强版（不改 ui/common.py）：带 href="#p{page}" 锚点 + 跳转按钮；
- 跳转写入 session_state["qa_jump_page"]（结构化）与 query_params（阅读页已实现的消费方）；
- 支持"全部文献 / 单篇"范围锁定、多篇勾选对比、拒答闸门与可疑引用的可视化。

Round 9 改动（S1）：
- 缺陷 C：库内片段极少（< SMALL_LIBRARY_CHUNKS）时给出可操作的补充引导；
- 缺陷 B：问答期间通过 progress 回调展示"检索→扩展→生成"阶段进度，
  强命中时跳过查询扩展，用户更快看到首字/结果；
- 抗幻觉：展示 result["unverified"]（答案中未在来源验证的数字/实体）与
  引用净化记录；溯源卡片编号与答案角标由 rag.sanitize_citations 保证一一对应。
"""
import html as _html
import time

import streamlit as st
from services import rag
from ui.common import require_library, page_header, show_error


EX = ["这篇论文提出了什么模型？", "训练用了什么 GPU？数量和型号？",
      "对比 Transformer 与 BERT 的架构（表格）", "GPT/BERT/Transformer 参数量演化",
      "Adam 的 beta_1、beta_2 是多少？BLEU 分数分别是多少？"]

# ---------- 本页自持常量（不改动 config.py） ----------
SNIPPET_LEN = 200            # 溯源卡片折叠区展示的原文片段长度（字）
MAX_COMPARE_PAPERS = 5       # 多篇对比最多勾选篇数
JUMP_KEY = "qa_jump_page"    # 跨页联动 session_state key
READING_PAGE_IDX = 3         # 📖 版式阅读页在 MENU 中的索引（ui/sidebar.py MENU / app.py）
SMALL_LIBRARY_CHUNKS = 8     # 与 services/rag.py 保持一致：片段少于此值给出补充引导

# 查询扩展模式 → 人话说明（debug["expansion_mode"]）
_EXPAND_MODE_LABELS = {
    "llm": "LLM 查询扩展（弱命中触发）",
    "cached": "查询扩展命中缓存（未重复调用 LLM）",
    "skipped_strong_hit": "原查询已强命中，跳过查询扩展（节省一次 LLM 调用）",
    "offline_strong_hit": "原查询已强命中，跳过 LLM 扩展；离线符号对齐变体仍参与检索（零成本）",
    "offline_no_key": "未配置 Key，离线词表兜底",
    "offline_key_issue": "Key 形态异常，已拦截并走离线词表兜底",
    "offline_llm_failed": "LLM 扩展失败，离线词表兜底",
}


def _library_guidance(ctx):
    """缺陷 C：库内片段极少时返回可操作引导文案；内容丰富返回 None。"""
    try:
        n = len(getattr(ctx, "chunks", []) or [])
    except Exception:
        n = 0
    if n == 0:
        return None  # require_library 已给出空库引导
    if n < SMALL_LIBRARY_CHUNKS:
        return (f"📌 **当前文献库仅 {n} 个片段**（{len(getattr(ctx, 'files', []) or [])} 篇），"
                f"检索覆盖面有限，答案可能不完整。建议：① 到「📁 文献库管理」上传更多 PDF；"
                f"② 或点击左侧「🚀 一键加载演示数据」；③ 或在该页重建索引后重试。")
    return None


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
    cap = d.get("source_cap") or {}
    if cap:
        dist = "，".join(f"{k}×{v}" for k, v in (cap.get("per_source") or {}).items())
        st.caption(f"⚖️ 来源多样性截断：融合池 {cap.get('pool', 0)} 条 → 入选 {cap.get('kept', 0)} 条"
                   f"（{dist}）——防止大书淹没 top-k")
    if (d.get("source_cap") or {}).get("dropped_stale"):
        st.caption(f"🗑️ 同论文多版本去重：丢弃旧版本片段 {(d.get('source_cap') or {}).get('dropped_stale')} 条"
                   "（最新版入池覆盖充分，旧版数值可能已被作者修订）")
    if d.get("per_paper"):
        st.caption("📚 对比模式每篇取到：" + "，".join(f"{k}×{v}条" for k, v in d["per_paper"].items()))
    mode = d.get("expansion_mode")
    if mode:
        label = _EXPAND_MODE_LABELS.get(mode, mode)
        note = f"（{d['expand_note']}）" if d.get("expand_note") else ""
        st.caption(f"🧭 查询扩展：{label}{note}")
    t = d.get("timings") or {}
    if t:
        st.caption(f"⏱️ 检索耗时：原查询双路 {t.get('phase1_ms', 0)}ms"
                   f" + 扩展/变体 {t.get('expand_ms', 0)}ms")
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

    # ---------- 缺陷 C：库内内容极少时的可操作引导 ----------
    guidance = _library_guidance(ctx)
    if guidance:
        st.info(guidance)

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
            # 话术 7：静态 spinner 改为「阶段进度 + 流式正文」。
            # 流式只提前"看到字的时机"，rag.answer 仍照常执行引用校验/净化/修复环；
            # 因此流式期间明确标注"未校验"，完成后由下方已校验结果覆盖。
            note_ph = st.empty()
            stage_ph = st.empty()   # 阶段进度（rag.answer 的 progress 回调驱动）
            stream_ph = st.empty()  # 流式正文
            _buf = []
            _last = [0.0]
            _t0 = [time.perf_counter()]
            _ttft = [0.0]           # time to first token

            def _progress(stage):
                try:
                    stage_ph.caption(f"⏳ {stage}")
                except Exception:
                    pass

            def _on_token(tok):
                _buf.append(tok)
                if _ttft[0] == 0.0:
                    _ttft[0] = time.perf_counter() - _t0[0]
                    try:
                        note_ph.caption(
                            f"✍️ 流式生成中（首字 {_ttft[0]:.1f}s，内容尚未经引用校验，"
                            f"完成后自动替换为已校验结果）")
                    except Exception:
                        pass
                now = time.perf_counter()
                if now - _last[0] >= 0.08:   # 节流约 12 帧/秒，避免高频重绘拖慢 UI
                    _last[0] = now
                    try:
                        stream_ph.markdown("".join(_buf) + "▌")
                    except Exception:
                        pass

            try:
                result = rag.answer(ctx, q.strip(), use_rerank=use_rerank,
                                    source_filter=source_filter,
                                    compare_papers=list(compare_papers) or None,
                                    progress=_progress, on_token=_on_token)
                if result is not None and _ttft[0]:
                    result.setdefault("debug", {})["ttft_sec"] = round(_ttft[0], 2)
            except Exception as e:
                show_error("生成回答", e)
                result = None
            finally:
                for ph in (note_ph, stage_ph, stream_ph):
                    try:
                        ph.empty()
                    except Exception:
                        pass
            if result:
                st.session_state["qa_runs"].append({
                    "id": st.session_state["qa_run_seq"],
                    "q": q.strip(),
                    "a": result.get("answer", ""),
                    "sources": result.get("sources", []),
                    "issues": result.get("issues", []),
                    "unverified": result.get("unverified", []),
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
        if run.get("unverified"):
            st.warning("⚠️ 以下内容未在来源文本中找到对应依据（已自动标注，请谨慎对待）：\n"
                       + "\n".join(f"- {x}" for x in run["unverified"]))
        srcs = run.get("sources") or []
        if srcs:
            with st.expander(f"🔍 溯源（{len(srcs)} 条来源，点击可定位原文）", expanded=False):
                for i, c in enumerate(srcs, 1):
                    _render_source(rid, i, c)
        with st.expander("🛠️ 检索调试", expanded=False):
            _render_debug(run)
        st.markdown("---")

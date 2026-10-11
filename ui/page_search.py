"""页面 4：论文自动检索、追踪与智能推荐。arXiv/PubMed/网页、批量下载、引用含金量筛选、个性化推荐、动态追踪。"""
import numpy as np
import streamlit as st

from services import extern
from tools import definitions as df
from ui.common import (ensure_state, page_header, net_error, friendly_error, pretty_table,
                       empty_state, llm_guard, long_block, step_text)

# 推荐列表最多展示条数
MAX_REC_SHOW = 6

# 用户画像（中文领域标签）→ 英文论文关键词映射，用于个性化加权
_DOMAIN_EN = {
    "NLP / 大模型": ["language model", "transformer", "bert", "attention", "llm", "nlp", "pretrain"],
    "计算机视觉": ["image", "vision", "detection", "segmentation", "cnn"],
    "生物医学": ["protein", "clinical", "medical", "biomedical", "drug"],
    "图神经网络": ["graph neural", "gnn", "citation network"],
}

_SEARCH_DEMO_HINT = "如需离线演示，请点左侧「🚀 一键加载演示数据」并使用📁/💬/🕸️页面"


def _arxiv(ctx):
    st.subheader("📚 arXiv 检索 / 下载")
    q = st.text_input("标题 / 关键词 / ID", key="aq")
    if st.button("🔍 搜索 arXiv"):
        if not (q or "").strip():
            st.warning("⚠️ 请先输入标题、关键词或 arXiv ID。")
        else:
            try:
                with st.spinner(step_text("🔍 正在检索 arXiv")):
                    res = extern.search_arxiv(q.strip(), 5) or []
                st.session_state.arxiv_res = res
                if res:
                    st.success(f"✅ 检索到 {len(res)} 篇论文")
                else:
                    empty_state("arXiv 未返回任何结果。",
                                hints=["换个更宽泛的关键词（如 transformer）",
                                       "直接输入 arXiv ID（如 1706.03762）", _SEARCH_DEMO_HINT])
            except Exception as e:
                st.session_state.arxiv_res = []
                net_error("arXiv 检索", e, fallback_hint=_SEARCH_DEMO_HINT)

    res = st.session_state.get("arxiv_res") or []
    for i, p in enumerate(res):
        if not isinstance(p, dict):
            continue
        st.markdown(f"**{i+1}. {p.get('title','(无标题)')}**（{p.get('published','')}，{p.get('arxiv_id','')}）")
        long_block(p.get("summary", ""), title=f"摘要 · {p.get('arxiv_id','')}", limit=160)
        if p.get("github"):
            st.info("💻 开源代码：" + str(p["github"][0]))
        key = str(p.get("id") or p.get("arxiv_id") or i)
        if st.button("⬇️ 下载并入库", key=f"ad_{key}"):
            try:
                with st.spinner(step_text("⬇️ 正在下载并入库")):
                    r = df._ingest_arxiv_paper(ctx, p)
                if not isinstance(r, dict):
                    st.warning("⚠️ 入库结果异常，请重试。")
                elif r.get("ok"):
                    st.success(f"✅ 已入库：{r.get('title','')}")
                elif r.get("duplicate"):
                    st.warning(f"⚠️ 查重命中：{r.get('duplicate')}（相似度 {r.get('sim')}）")
                else:
                    st.error(f"❌ 入库失败：{r.get('error') or '未知原因'}")
            except Exception as e:
                friendly_error("下载入库", e,
                               reasons=["外网不可达 / PDF 下载超时", "磁盘写入失败"],
                               actions=["重试一次", _SEARCH_DEMO_HINT])


def _batch(ctx):
    st.subheader("📋 参考文献批量抓取")
    titles = st.text_area("标题列表（每行一条）", height=130, key="bt")
    if st.button("🚀 批量下载入库"):
        lines = [x.strip() for x in str(titles or "").split("\n") if x.strip()]
        if not lines:
            st.warning("⚠️ 请先粘贴至少一个论文标题（每行一条）。")
        else:
            try:
                with st.spinner(step_text("🚀 正在批量抓取", len(lines), len(lines))):
                    r = df.t_batch_download(ctx, str(titles)) or {}
                statuses = r.get("statuses") or []
                ok_n = len([s for s in statuses if isinstance(s, dict) and s.get("ok")])
                if ok_n:
                    st.success(f"✅ 批量入库完成：成功 {ok_n} / 共 {len(statuses)} 条")
                for s in statuses:
                    if not isinstance(s, dict):
                        continue
                    if s.get("ok"):
                        st.caption(f"✅ {str(s.get('query',''))[:50]} → {str(s.get('title',''))[:60]}")
                    else:
                        st.caption(f"❌ {str(s.get('query',''))[:50]}：{s.get('error') or s.get('note') or '未知原因'}")
                if not statuses:
                    empty_state("批量抓取未返回任何结果。",
                                hints=["确认标题准确（建议用英文原题）", _SEARCH_DEMO_HINT])
            except Exception as e:
                friendly_error("批量抓取", e,
                               reasons=["外网不可达 / 标题无法匹配", "抓取服务限流"],
                               actions=["减少条目后重试", _SEARCH_DEMO_HINT])


def _others(ctx):
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("🧬 PubMed")
        pq = st.text_input("关键词", key="pq")
        if st.button("🔍 PubMed"):
            if not (pq or "").strip():
                st.warning("⚠️ 请输入检索关键词。")
            else:
                try:
                    with st.spinner(step_text("🧬 正在检索 PubMed")):
                        res = extern.search_pubmed(pq.strip(), 5) or []
                    st.session_state.pm = res
                    if res:
                        st.success(f"✅ 检索到 {len(res)} 篇文献")
                    else:
                        empty_state("PubMed 未返回任何结果。",
                                    hints=["换成英文医学关键词", _SEARCH_DEMO_HINT])
                except Exception as e:
                    st.session_state.pm = []
                    net_error("PubMed 检索", e, fallback_hint=_SEARCH_DEMO_HINT)
        rows = st.session_state.get("pm") or []
        if rows:
            pretty_table([{
                "标题": str(r.get("title", ""))[:90],
                "作者": str(r.get("authors", ""))[:60],
                "期刊": r.get("journal", ""),
                "日期": r.get("pubdate", ""),
            } for r in rows if isinstance(r, dict)], title="🧬 PubMed 结果")
        for r in rows:
            if isinstance(r, dict) and r.get("url"):
                st.markdown(f"[📎 PubMed 原文]({r['url']})")
    with c2:
        st.subheader("🌐 网页搜索")
        wq = st.text_input("关键词", key="wq")
        if st.button("🔍 DuckDuckGo"):
            if not (wq or "").strip():
                st.warning("⚠️ 请输入检索关键词。")
            else:
                try:
                    with st.spinner(step_text("🌐 正在网页搜索")):
                        res = extern.web_search(wq.strip(), 6) or []
                    err = [r for r in res if isinstance(r, dict) and r.get("error")]
                    if err:
                        net_error("网页搜索", err[0]["error"], fallback_hint=_SEARCH_DEMO_HINT)
                        st.session_state.web = []
                    else:
                        st.session_state.web = res
                        if res:
                            st.success(f"✅ 网页检索到 {len(res)} 条结果")
                        else:
                            empty_state("网页未返回任何结果。",
                                        hints=["换个关键词试试", _SEARCH_DEMO_HINT])
                except Exception as e:
                    st.session_state.web = []
                    net_error("网页搜索", e, fallback_hint=_SEARCH_DEMO_HINT)
        web = st.session_state.get("web") or []
        if web:
            for r in web:
                if not isinstance(r, dict):
                    continue
                if r.get("error"):
                    st.warning(f"⚠️ {str(r['error'])[:140]}")
                else:
                    st.markdown(f"🔗 **[{str(r.get('title',''))[:90]}]({r.get('url','#')})**")


def _quality(ctx):
    st.subheader("🔬 引用含金量自动剔除")
    refs = st.text_area("粘贴参考文献（每行一条）", height=160, key="qrefs")
    if st.button("🧹 分析并剔除应付式引用"):
        lines = [x.strip() for x in str(refs or "").split("\n") if x.strip()]
        if not lines:
            st.warning("⚠️ 请先粘贴至少一条参考文献（每行一条）。")
        elif llm_guard(ctx, "引用含金量分析"):
            prompt = ("判断每条引用是实质性引用还是应付式引用，分组输出'建议保留/建议剔除'并说明中文理由。\n"
                      + "\n".join(lines))
            try:
                with st.spinner(step_text("🧹 正在分析引用含金量", len(lines), len(lines))):
                    out = ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.2)
                st.success("✅ 分析完成")
                long_block(getattr(out, "content", str(out)), title="引用含金量分析报告", limit=200)
            except Exception as e:
                friendly_error("引用含金量分析", e,
                               reasons=["大模型返回超时 / 额度不足", "粘贴内容过长"],
                               actions=["减少条目后重试", "或换个功能继续演示"])


def _likes_of(ctx, aid):
    """取某篇论文的反馈聚合值（跨用户求和）。"""
    try:
        return sum((ctx.likes.get(aid, {}) or {}).values())
    except Exception:
        return 0


def _set_like(ctx, aid, user, v):
    try:
        ctx.likes.setdefault(aid, {})
        prev = ctx.likes[aid].get(user, 0)
        ctx.likes[aid][user] = v if prev != v else 0
        ctx.save_library()
    except Exception as e:
        friendly_error("保存反馈", e, reasons=["library.json 写入失败"],
                       actions=["检查 storage 目录写权限"])


def _recommend(ctx):
    st.subheader("⭐ 个性化推荐（本地库兴趣 + 开源代码 + 点赞/踩反馈）")
    cat = st.text_input("arXiv 分类 / 关键词", value="cs.AI", key="rc")
    uid = getattr(getattr(ctx, "memory", None), "user", "default")
    if st.button("✨ 刷新并推荐"):
        try:
            with st.spinner(step_text("✨ 正在拉取候选论文")):
                cand = extern.latest_arxiv(cat, 12) if str(cat).startswith("cs.") \
                    else extern.search_arxiv(cat, 12, "submitted")
                cand = cand or []
            if not cand:
                empty_state("未拉取到候选论文。",
                            hints=["确认 arXiv 分类写法（如 cs.AI / cs.CL）", _SEARCH_DEMO_HINT])
                st.session_state["rec_rows"] = []
            else:
                st.session_state["rec_rows"] = _score_rows(ctx, cand, uid)
                st.success(f"✅ 已生成 {min(len(st.session_state['rec_rows']), MAX_REC_SHOW)} 条推荐")
        except Exception as e:
            st.session_state["rec_rows"] = []
            net_error("推荐候选抓取", e, fallback_hint=_SEARCH_DEMO_HINT)

    rows = st.session_state.get("rec_rows") or []
    if not rows:
        empty_state("暂无推荐结果。", hints=["点上方「✨ 刷新并推荐」", _SEARCH_DEMO_HINT])
    for score, p, why in rows[:MAX_REC_SHOW]:
        if not isinstance(p, dict):
            continue
        c1, c2 = st.columns([2, 1])
        with c1:
            st.markdown(f"**{str(p.get('title',''))[:90]}**  ·  {p.get('arxiv_id','')}  ·  相关度 {score:.2f}")
            st.caption("💡 推荐理由：" + "；".join(why or ["该领域最新提交，值得关注"]))
            long_block(str(p.get("summary", ""))[:400], title="摘要", limit=120)
        with c2:
            aid = str(p.get("arxiv_id") or p.get("id") or "")
            b1, b2 = st.columns([1, 1])
            if b1.button("👍", key=f"up_{aid}"):
                _set_like(ctx, aid, uid, 1)
                st.rerun()
            if b2.button("👎", key=f"dn_{aid}"):
                _set_like(ctx, aid, uid, -1)
                st.rerun()
    if rows:
        try:
            voted = len([v for v in ctx.likes.values() if any((v or {}).values())])
        except Exception:
            voted = 0
        st.caption(f"🧑‍🤝‍🧑 当前用户 `{uid}`（切换侧边栏用户 ID 可演示多用户差异化推荐）；"
                   f"已记录反馈 {voted} 篇")


def _score_rows(ctx, cand, uid):
    """候选论文打分排序：本地库兴趣 + 开源代码 + 点赞反馈 + 用户画像。"""
    embs = []
    for n in ctx.files:
        try:
            e = ctx.papers.get(n, {}).get("embedding")
            if e:
                embs.append(np.asarray(e, dtype=float))
        except Exception:
            continue
    centroid = np.mean(np.stack(embs), axis=0) if embs else None
    try:
        prof = ctx.memory.profile() or {}
    except Exception:
        prof = {}
    qvecs = ctx.embed([str(p.get("summary", ""))[:1500] for p in cand])
    rows = []
    for p, v in zip(cand, qvecs):
        try:
            rel = float(np.dot(centroid, np.asarray(v, dtype=float))) if centroid is not None else 0.0
        except Exception:
            rel = 0.0
        gh = 0.15 if p.get("github") else 0.0
        lk = 0.20 * _likes_of(ctx, p.get("arxiv_id"))
        hay = (str(p.get("title", "")) + " " + str(p.get("summary", ""))).lower()
        interest = 0.10 * sum(cnt for dom, cnt in prof.items()
                              for w in _DOMAIN_EN.get(dom, []) if w in hay)
        score = rel + gh + lk + interest
        why = []
        if rel > 0.3:
            why.append(f"与你本地文献库主题相近（余弦 {rel:.2f}）")
        if interest > 0:
            why.append(f"命中你的研究画像（{uid}）")
        if gh:
            why.append("附 GitHub 开源代码，便于复现")
        if lk:
            why.append(f"社区反馈净点赞 {lk/0.20:+.0f}")
        rows.append((score, p, why or ["该领域最新提交，值得关注"]))
    return sorted(rows, key=lambda x: x[0], reverse=True)


def _track(ctx):
    st.subheader("⏰ arXiv 动态追踪（订阅 + 增量新论文检测）")
    st.caption("按话题订阅领域，系统记住检查点，每次只推送「上次以来新增」的论文，可配合 scripts/watch_daemon.py 定时运行。")
    try:
        from services import track
        cur = track.get_subscriptions(ctx) or []
    except Exception as e:
        friendly_error("读取订阅配置", e, reasons=["追踪配置文件缺失"],
                       actions=["忽略并直接输入新话题即可自动创建"])
        return
    topics = st.text_input("订阅话题（逗号分隔）", value=", ".join(cur), key="trk_topics")
    c1, c2 = st.columns([2, 1])
    if c1.button("🔄 运行一次追踪"):
        if not (topics or "").strip():
            st.warning("⚠️ 请先填写至少一个订阅话题（多个用逗号分隔）。")
        else:
            _lst = [t.strip() for t in topics.split(",") if t.strip()]
            try:
                track.set_subscriptions(ctx, _lst)
                with st.spinner(step_text("⏰ 正在抓取订阅话题", len(_lst), len(_lst))):
                    st.session_state["trk_report"] = track.fetch_track(ctx, max_results=6) or []
                st.success("✅ 本轮追踪完成")
            except Exception as e:
                st.session_state["trk_report"] = []
                net_error("动态追踪", e, fallback_hint=_SEARCH_DEMO_HINT)
    if c2.button("♻️ 重置检查点"):
        try:
            track.reset_checkpoints(ctx)
            st.session_state.pop("trk_report", None)
            st.success("✅ 检查点已清空，下次追踪将把当前论文视为全新。")
        except Exception as e:
            friendly_error("重置检查点", e, reasons=["追踪配置文件不可写"],
                           actions=["检查 storage 目录写权限"])

    reports = st.session_state.get("trk_report") or []
    if not reports:
        empty_state("暂无追踪报告。", hints=["填写话题后点「🔄 运行一次追踪」", _SEARCH_DEMO_HINT])
    for r in reports:
        if not isinstance(r, dict):
            continue
        if r.get("error"):
            st.warning(f"⚠️ {r.get('topic','')} 抓取失败（网络受限）：{str(r['error'])[:120]}")
            continue
        new = r.get("new") or []
        st.markdown(f"**{r.get('topic','')}** · 本轮扫描 {r.get('count', 0)} 篇，新增 **{len(new)}** 篇")
        if not new:
            st.caption("ℹ️ 本话题自上次检查点以来暂无新增论文。")
        for p in new[:6]:
            if not isinstance(p, dict):
                continue
            tag = " 💻开源" if p.get("github") else ""
            st.markdown(f"- `{p.get('arxiv_id','')}` {str(p.get('title',''))[:80]}"
                        f"（{p.get('published','')}）{tag}")
            long_block(str(p.get("summary", ""))[:300], title="摘要", limit=100)


def render(ctx):
    ensure_state({"arxiv_res": list, "pm": list, "web": list, "rec_rows": list, "trk_report": list})
    page_header("🔍 论文自动检索、追踪与推荐",
                "arXiv/PubMed/网页批量检索、引用含金量筛选、个性化推荐、动态追踪订阅。",
                icon="🔍")
    st.caption("ℹ️ 本页面依赖外网；比赛现场断网时请先「🚀 一键加载演示数据」，"
               "用📁文献库 / 💬问答 / 🕸️图谱页完成演示。")
    for fn in (_track, _recommend, _arxiv, _batch, _others, _quality):
        try:
            fn(ctx)
        except Exception as e:
            st.error(f"⚠️ 区块渲染异常，已跳过该模块：{str(e)[:160]}")
            st.caption("🛠️ 怎么办：其余检索模块不受影响，可继续使用。")
        st.markdown("---")

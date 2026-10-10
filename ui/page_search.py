"""页面 4：论文自动检索、追踪与智能推荐。arXiv/PubMed/网页、批量下载、引用含金量筛选、个性化推荐、动态追踪。"""
import numpy as np
import streamlit as st

from services import extern
from tools import definitions as df
from ui.common import page_header

# 用户画像（中文领域标签）→ 英文论文关键词映射，用于个性化加权
_DOMAIN_EN = {
    "NLP / 大模型": ["language model", "transformer", "bert", "attention", "llm", "nlp", "pretrain"],
    "计算机视觉": ["image", "vision", "detection", "segmentation", "cnn"],
    "生物医学": ["protein", "clinical", "medical", "biomedical", "drug"],
    "图神经网络": ["graph neural", "gnn", "citation network"],
}


def _arxiv(ctx):
    st.subheader("📚 arXiv 检索 / 下载")
    q = st.text_input("标题 / 关键词 / ID", key="aq")
    if st.button("🔍 搜索 arXiv") and q:
        try:
            with st.spinner("🔍 正在检索 arXiv…"):
                st.session_state.arxiv_res = extern.search_arxiv(q, 5)
        except Exception as e:
            st.error(f"arXiv 检索失败（网络受限？）：{str(e)[:160]}")
            st.session_state.arxiv_res = []
    for i, p in enumerate(st.session_state.get("arxiv_res", [])):
        st.markdown(f"**{p['title']}**（{p['published']}，{p['arxiv_id']}")
        st.caption(p["summary"][:260])
        if p["github"]:
            st.info("💻 开源代码：" + p["github"][0])
        if st.button("⬇️ 下载并入库", key=f"ad_{p['id']}"):
            r = df._ingest_arxiv_paper(ctx, p)
            if r.get("ok"):
                st.success(f"✅ 已入库：{r['title']}")
            elif r.get("duplicate"):
                st.warning(f"查重命中：{r['duplicate']}（{r.get('sim')}）")
            else:
                st.error(r.get("error"))


def _batch(ctx):
    st.subheader("📋 参考文献批量抓取")
    titles = st.text_area("标题列表（每行一条）", height=130, key="bt")
    if st.button("🚀 批量下载入库") and titles:
        try:
            with st.spinner("🚀 正在批量抓取…"):
                r = df.t_batch_download(ctx, titles)
            for s in r.get("statuses", []):
                if s.get("ok"):
                    st.success(f"✅ {s['query'][:50]} → {s.get('title','')[:60]}")
                else:
                    st.caption(f"❌ {s['query'][:50]}：{s.get('error') or s.get('note')}")
        except Exception as e:
            st.error(f"批量抓取失败：{str(e)[:160]}")


def _others(ctx):
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🧬 PubMed")
        pq = st.text_input("关键词", key="pq")
        if st.button("🔍 PubMed") and pq:
            try:
                with st.spinner("🧬 正在检索 PubMed…"):
                    st.session_state.pm = extern.search_pubmed(pq, 5)
            except Exception as e:
                st.error(f"PubMed 检索失败：{str(e)[:160]}")
                st.session_state.pm = []
        for r in st.session_state.get("pm", []):
            st.markdown(f"**{r['title']}**"); st.caption(f"{r['authors']} · {r['journal']} ({r['pubdate']})")
            st.markdown(f"[PubMed]({r['url']})")
    with c2:
        st.subheader("🌐 网页搜索")
        wq = st.text_input("关键词", key="wq")
        if st.button("🔍 DuckDuckGo") and wq:
            try:
                with st.spinner("🌐 正在网页搜索…"):
                    st.session_state.web = extern.web_search(wq, 6)
            except Exception as e:
                st.error(f"网页搜索失败：{str(e)[:160]}")
                st.session_state.web = []
        for r in st.session_state.get("web", []):
            if r.get("error"):
                st.error(r["error"])
            else:
                st.markdown(f"**[{r['title'][:90]}]({r['url']})")


def _quality(ctx):
    st.subheader("🔬 引用含金量自动剔除")
    refs = st.text_area("粘贴参考文献（每行一条）", height=160, key="qrefs")
    if st.button("🧹 分析并剔除应付式引用") and refs and ctx.llm.available:
        prompt = ("判断每条引用是实质性引用还是应付式引用，分组输出'建议保留/建议剔除'并说明中文理由。\n"
                  + "\n".join(x.strip() for x in refs.split("\n") if x.strip()))
        st.markdown(ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.2).content)


def _likes_of(ctx, aid):
    """取某篇论文的反馈聚合值（跨用户求和）。"""
    return sum(ctx.likes.get(aid, {}).values())


def _set_like(ctx, aid, user, v):
    ctx.likes.setdefault(aid, {})
    prev = ctx.likes[aid].get(user, 0)
    ctx.likes[aid][user] = v if prev != v else 0
    ctx.save_library()


def _recommend(ctx):
    st.subheader("⭐ 个性化推荐（本地库兴趣 + 开源代码 + 点赞/踩反馈）")
    cat = st.text_input("arXiv 分类 / 关键词", value="cs.AI", key="rc")
    uid = ctx.memory.user
    if st.button("✨ 刷新并推荐"):
        try:
            cand = extern.latest_arxiv(cat, 12) if cat.startswith("cs.") else extern.search_arxiv(cat, 12, "submitted")
        except Exception as e:
            st.warning(f"抓取 arXiv 失败（网络受限）：{str(e)[:80]}")
            return
        embs = [np.asarray(ctx.papers[n]["embedding"]) for n in ctx.files
                if n in ctx.papers and ctx.papers[n].get("embedding")]
        centroid = np.mean(np.stack(embs), axis=0) if embs else None
        prof = ctx.memory.profile()
        qvecs = ctx.embed([p["summary"][:1500] for p in cand])
        rows = []
        for p, v in zip(cand, qvecs):
            rel = float(np.dot(centroid, np.asarray(v))) if centroid is not None else 0.0
            gh = 0.15 if p["github"] else 0.0
            lk = 0.20 * _likes_of(ctx, p["arxiv_id"])
            hay = (p["title"] + " " + p["summary"]).lower()
            interest = 0.10 * sum(cnt for dom, cnt in prof.items()
                                  for w in _DOMAIN_EN.get(dom, []) if w in hay)
            score = rel + gh + lk + interest
            why = []
            if rel > 0.3: why.append(f"与你本地文献库主题相近(余弦 {rel:.2f})")
            if interest > 0: why.append(f"命中你的研究画像({uid})")
            if gh: why.append("附 GitHub 开源代码，便于复现")
            if lk: why.append(f"社区反馈净点赞 {_likes_of(ctx, p['arxiv_id']):+.0f}")
            rows.append((score, p, why or ["该领域最新提交，值得关注"]))
        st.session_state["rec_rows"] = sorted(rows, key=lambda x: x[0], reverse=True)

    for score, p, why in st.session_state.get("rec_rows", [])[:6]:
        c1, c2 = st.columns([5, 1])
        with c1:
            st.markdown(f"**{p['title']}**  ·  {p['arxiv_id']}  ·  相关度 {score:.2f}")
            st.caption("推荐理由：" + "；".join(why))
            st.caption(p["summary"][:160])
        with c2:
            st.write("")
            b1, b2 = st.columns(2)
            if b1.button("👍", key=f"up_{p['arxiv_id']}"):
                _set_like(ctx, p["arxiv_id"], uid, 1); st.rerun()
            if b2.button("👎", key=f"dn_{p['arxiv_id']}"):
                _set_like(ctx, p["arxiv_id"], uid, -1); st.rerun()
    if st.session_state.get("rec_rows"):
        st.caption(f"🧑‍🤝‍🧑 当前用户 `{uid}`（切换侧边栏用户 ID 可演示多用户差异化推荐）；"
                   f"已记录反馈 {len([v for v in ctx.likes.values() if any(v.values())])} 篇")


def _track(ctx):
    st.subheader("⏰ arXiv 动态追踪（订阅 + 增量新论文检测）")
    st.caption("按话题订阅领域，系统记住检查点，每次只推送「上次以来新增」的论文，可配合 scripts/watch_daemon.py 定时运行。")
    from services import track
    cur = track.get_subscriptions(ctx)
    topics = st.text_input("订阅话题（逗号分隔）", value=", ".join(cur), key="trk_topics")
    c1, c2 = st.columns([3, 1])
    if c1.button("🔄 运行一次追踪") and topics:
        track.set_subscriptions(ctx, topics.split(","))
        with st.spinner("抓取中…"):
            st.session_state["trk_report"] = track.fetch_track(ctx, max_results=6)
    if c2.button("♻️ 重置检查点"):
        track.reset_checkpoints(ctx); st.session_state.pop("trk_report", None)
        st.caption("检查点已清空，下次追踪将把当前论文视为全新。")
    for r in st.session_state.get("trk_report", []):
        if r.get("error"):
            st.warning(f"{r['topic']} 抓取失败（网络受限）：{r['error']}")
            continue
        st.markdown(f"**{r['topic']}** · 本轮扫描 {r.get('count', 0)} 篇，新增 **{len(r['new'])}** 篇")
        for p in r["new"]:
            tag = " 💻开源" if p["github"] else ""
            st.markdown(f"- `{p['arxiv_id']}` {p['title'][:80]}（{p['published']}）{tag}")
            st.caption(p["summary"][:140])


def render(ctx):
    page_header("🔍 论文自动检索、追踪与推荐", "arXiv/PubMed/网页批量检索、引用含金量筛选、个性化推荐、动态追踪订阅。")
    _track(ctx)
    st.markdown("---"); _recommend(ctx)
    st.markdown("---"); _arxiv(ctx)
    st.markdown("---"); _batch(ctx)
    st.markdown("---"); _others(ctx)
    st.markdown("---"); _quality(ctx)

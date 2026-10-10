"""页面 4：论文自动检索、追踪与智能推荐。arXiv/PubMed/网页、批量下载、引用含金量筛选、个性化推荐、动态追踪。"""
import numpy as np
import streamlit as st

from services import extern
from tools import definitions as df


def _arxiv(ctx):
    st.subheader("📚 arXiv 检索 / 下载")
    q = st.text_input("标题 / 关键词 / ID", key="aq")
    if st.button("🔍 搜索 arXiv") and q:
        st.session_state.arxiv_res = extern.search_arxiv(q, 5)
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
        r = df.t_batch_download(ctx, titles)
        for s in r["statuses"]:
            if s.get("ok"):
                st.success(f"✅ {s['query'][:50]} → {s.get('title','')[:60]}")
            else:
                st.caption(f"❌ {s['query'][:50]}：{s.get('error') or s.get('note')}")


def _others(ctx):
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🧬 PubMed")
        pq = st.text_input("关键词", key="pq")
        if st.button("🔍 PubMed") and pq:
            st.session_state.pm = extern.search_pubmed(pq, 5)
        for r in st.session_state.get("pm", []):
            st.markdown(f"**{r['title']}**"); st.caption(f"{r['authors']} · {r['journal']} ({r['pubdate']})")
            st.markdown(f"[PubMed]({r['url']})")
    with c2:
        st.subheader("🌐 网页搜索")
        wq = st.text_input("关键词", key="wq")
        if st.button("🔍 DuckDuckGo") and wq:
            st.session_state.web = extern.web_search(wq, 6)
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


def _recommend(ctx):
    st.subheader("⭐ 个性化推荐（结合本地库 + 开源代码）")
    cat = st.text_input("arXiv 分类 / 关键词", value="cs.AI", key="rc")
    if st.button("✨ 刷新并推荐"):
        cand = extern.latest_arxiv(cat, 12) if cat.startswith("cs.") else extern.search_arxiv(cat, 12, "submitted")
        # 本地兴趣中心
        embs = [np.asarray(ctx.papers[n]["embedding"]) for n in ctx.files if ctx.papers[n].get("embedding")]
        centroid = np.mean(np.stack(embs), axis=0) if embs else None
        rows = []
        qvecs = ctx.embed([p["summary"][:1500] for p in cand])
        for p, v in zip(cand, qvecs):
            rel = float(np.dot(centroid, np.asarray(v))) if centroid is not None else 0.0
            rows.append((rel + (0.15 if p["github"] else 0.0), p))
        for score, p in sorted(rows, key=lambda x: x[0], reverse=True)[:6]:
            tag = "💻 有开源代码" if p["github"] else ""
            st.markdown(f"**{p['title']}**（相关度 {score:.2f}）{tag}")
            st.caption(p["summary"][:200])


def render(ctx):
    st.header("🔍 论文自动检索、追踪与推荐")
    _recommend(ctx)
    st.markdown("---"); _arxiv(ctx)
    st.markdown("---"); _batch(ctx)
    st.markdown("---"); _others(ctx)
    st.markdown("---"); _quality(ctx)

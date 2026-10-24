"""页面 4：论文自动检索、追踪与智能推荐。arXiv/PubMed/网页、批量下载、引用含金量筛选、个性化推荐、动态追踪。

2026-10 R19 后台化改造（用户需求：「0 到 6 的板块中的任务任何一项都是可以同时运行的」）：
本页所有网络 / LLM 长任务均改为 services/tasks.py 后台执行 + bg_consume 交付，
主线程只保留输入校验与结果渲染。硬约束：worker 函数严禁任何 st.* 调用
（线程内没有 ScriptRunContext），进度只能经 progress(stage, pct) 回调上报。
"""
import numpy as np
import streamlit as st

from services import extern
from tools import definitions as df
from ui.common import (ensure_state, page_header, section_header, friendly_error, show_error, pretty_table,
                       empty_state, llm_guard, long_block, show_llm_result,
                       bg_submit, bg_consume, bg_running)
from ui.sidebar import set_flash

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

# ---------------- 后台任务 kind：本页提交 / 认领的唯一标识 ----------------
_K_ARXIV = "search_arxiv"          # arXiv 检索
_K_INGEST = "search_ingest"        # 单篇下载并入库
_K_BATCH = "search_batch"          # 参考文献批量抓取
_K_PUBMED = "search_pubmed"        # PubMed 检索
_K_WEB = "search_web"              # 网页搜索
_K_QUALITY = "search_quality"      # 引用含金量分析（LLM）
_K_RECOMMEND = "search_recommend"  # 个性化推荐候选拉取
_K_TRACK = "search_track"          # 动态追踪抓取


def _toast(msg):
    try:
        st.toast(msg, icon="🚀")
    except Exception:
        pass


# ============================ worker：后台线程执行，严禁 st.* ============================

def _w_arxiv_search(q, progress):
    progress("正在检索 arXiv：%s" % str(q)[:40], None)
    return extern.search_arxiv(q, 5) or []


def _w_ingest(ctx, paper, progress):
    progress("正在下载并入库：%s" % str(paper.get("title") or "")[:40], None)
    return df._ingest_arxiv_paper(ctx, paper)


def _w_batch(ctx, titles, progress):
    lines = [x.strip() for x in str(titles).split("\n") if x.strip()]

    def pcb(i, n, line):
        progress("正在抓取第 %d/%d 条：%s" % (i, n, line[:40]), (i - 1) / n if n else None)

    r = df.t_batch_download(ctx, str(titles), progress_cb=pcb) or {}
    progress("批量抓取完成", 1.0)
    return r


def _w_pubmed(q, progress):
    progress("正在检索 PubMed：%s" % str(q)[:40], None)
    return extern.search_pubmed(q, 5) or []


def _w_web(q, progress):
    progress("正在网页搜索：%s" % str(q)[:40], None)
    return extern.web_search(q, 6) or []


def _w_quality(ctx, prompt, progress):
    progress("正在分析引用含金量", None)
    return ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.2)


def _w_recommend(cat, progress):
    progress("正在拉取候选论文：%s" % str(cat)[:40], None)
    if str(cat).startswith("cs."):
        return extern.latest_arxiv(cat, 12) or []
    return extern.search_arxiv(cat, 12, "submitted") or []


def _w_track(ctx, topics, progress):
    from services import track                      # 线程内 import 安全（模块已缓存）
    progress("正在抓取 %d 个订阅话题" % (len(topics) or 1), None)
    return track.fetch_track(ctx, max_results=6) or []


# ==================== 交付处理器：主线程渲染，逐分支等价旧内联流程 ====================

def _set_ingest_status(key, level, msg):
    """单篇入库结果按论文 key 落 session_state，渲染在对应按钮下方（与原内联同位置）。"""
    st.session_state.setdefault("ingest_status", {})[str(key)] = (level, msg)


def _consume_arxiv_search(kind, t):
    if t["state"] == "error":
        st.session_state.arxiv_res = []
        set_flash("error", "⚠️ arXiv 检索失败：%s" % str(t.get("error") or "")[:150]
                  + "\n\n🩺 可能原因：外网不可达 / arXiv 限流"
                  + "\n\n🛠️ 怎么办：重试一次；换个更宽泛的关键词；" + _SEARCH_DEMO_HINT)
        return
    res = t.get("result") or []
    st.session_state.arxiv_res = res
    st.session_state.arxiv_searched = True
    if res:
        set_flash("success", "✅ 检索到 %d 篇论文" % len(res))
    # 空结果不写 flash：由 _arxiv 区按 arxiv_searched 渲染 empty_state（与原内联同位置）


def _consume_ingest(kind, t):
    key = str(t.get("goal") or "")
    if t["state"] == "error":
        _set_ingest_status(key, "error",
                           "⚠️ 下载入库失败：%s\n\n🛠️ 怎么办：重试一次；确认外网可达；%s"
                           % (str(t.get("error") or "")[:150], _SEARCH_DEMO_HINT))
        return
    r = t.get("result")
    if not isinstance(r, dict):
        _set_ingest_status(key, "error", "⚠️ 入库结果异常，请重试。")
    elif r.get("ok"):
        _set_ingest_status(key, "success", "✅ 已入库：%s" % r.get("title", ""))
    elif r.get("duplicate"):
        _set_ingest_status(key, "warning",
                           "⚠️ 查重命中：%s（相似度 %s）" % (r.get("duplicate"), r.get("sim")))
    else:
        _set_ingest_status(key, "error", "❌ 入库失败：%s" % (r.get("error") or "未知原因"))


def _consume_batch(kind, t):
    if t["state"] == "error":
        set_flash("error", "⚠️ 批量抓取失败：%s" % str(t.get("error") or "")[:150]
                  + "\n\n🩺 可能原因：外网不可达 / 标题无法匹配；抓取服务限流"
                  + "\n\n🛠️ 怎么办：减少条目后重试；" + _SEARCH_DEMO_HINT)
        return
    # 结果落 session_state，由 _batch 区按原内联同位置渲染（成功数 + 逐条 caption）
    st.session_state["batch_report"] = t.get("result") or {}


def _consume_pubmed(kind, t):
    if t["state"] == "error":
        st.session_state.pm = []
        set_flash("error", "⚠️ PubMed 检索失败：%s" % str(t.get("error") or "")[:150]
                  + "\n\n🛠️ 怎么办：重试一次；换成英文医学关键词；" + _SEARCH_DEMO_HINT)
        return
    res = t.get("result") or []
    st.session_state.pm = res
    st.session_state.pm_searched = True
    if res:
        set_flash("success", "✅ 检索到 %d 篇文献" % len(res))
    # 空结果由 _others 区按 pm_searched 渲染 empty_state（与原内联同位置）


def _consume_web(kind, t):
    if t["state"] == "error":
        st.session_state.web = []
        set_flash("error", "⚠️ 网页搜索失败：%s" % str(t.get("error") or "")[:150]
                  + "\n\n🛠️ 怎么办：重试一次；换个关键词试试；" + _SEARCH_DEMO_HINT)
        return
    res = t.get("result") or []
    err = [r for r in res if isinstance(r, dict) and r.get("error")]
    if err:
        st.session_state.web = []
        set_flash("error", "⚠️ 网页搜索失败：%s" % str(err[0]["error"])[:150]
                  + "\n\n🛠️ 怎么办：换个关键词试试；" + _SEARCH_DEMO_HINT)
        return
    st.session_state.web = res
    st.session_state.web_searched = True
    if res:
        set_flash("success", "✅ 网页检索到 %d 条结果" % len(res))
    # 空结果由 _others 区按 web_searched 渲染 empty_state（与原内联同位置）


def _consume_quality(kind, t):
    if t["state"] == "error":
        set_flash("error", "⚠️ 引用含金量分析失败：%s" % str(t.get("error") or "")[:150]
                  + "\n\n🩺 可能原因：大模型返回超时 / 额度不足；粘贴内容过长"
                  + "\n\n🛠️ 怎么办：减少条目后重试；或换个功能继续演示")
        return
    # 原样交给 show_llm_result：降级态（离线占位）必须走可见的降级文案，不得报「完成」
    st.session_state["quality_out"] = t.get("result")


def _consume_recommend(ctx, kind, t):
    uid = getattr(getattr(ctx, "memory", None), "user", "default")
    if t["state"] == "error":
        st.session_state["rec_rows"] = []
        set_flash("error", "⚠️ 推荐候选抓取失败：%s" % str(t.get("error") or "")[:150]
                  + "\n\n🛠️ 怎么办：重试一次；确认 arXiv 分类写法（如 cs.AI / cs.CL）；"
                  + _SEARCH_DEMO_HINT)
        return
    cand = t.get("result") or []
    if not cand:
        st.session_state["rec_rows"] = []
        set_flash("warning", "未拉取到候选论文。\n\n🛠️ 怎么办：确认 arXiv 分类写法（如 cs.AI / cs.CL）；"
                             + _SEARCH_DEMO_HINT)
        return
    try:
        # _score_rows 在主线程调用：内部有 st.caption 降级说明 + ctx.embed 本地推理，
        # 与改造前完全一致（线程内调用会 missing ScriptRunContext）
        rows = _score_rows(ctx, cand, uid)
    except Exception as e:
        st.session_state["rec_rows"] = []
        set_flash("error", "⚠️ 推荐排序失败：%s" % str(e)[:150]
                  + "\n\n🛠️ 怎么办：重试一次；或换个功能继续演示")
        return
    st.session_state["rec_rows"] = rows
    set_flash("success", "✅ 已生成 %d 条推荐" % min(len(rows), MAX_REC_SHOW))


def _consume_track(kind, t):
    if t["state"] == "error":
        st.session_state["trk_report"] = []
        set_flash("error", "⚠️ 动态追踪失败：%s" % str(t.get("error") or "")[:150]
                  + "\n\n🛠️ 怎么办：重试一次；" + _SEARCH_DEMO_HINT)
        return
    st.session_state["trk_report"] = t.get("result") or []
    set_flash("success", "✅ 本轮追踪完成")


# ============================ 页面各区（提交闸门 + 结果渲染） ============================

def _arxiv(ctx):
    section_header("arXiv 检索 / 下载", icon="📚")
    q = st.text_input("标题 / 关键词 / ID", key="aq")
    _running = bg_running(kinds={_K_ARXIV})
    if st.button("🔍 搜索 arXiv", disabled=_running,
                 help="后台检索：提交后可切换其他页面，实时进度见左侧「任务中心」"):
        if not (q or "").strip():
            st.warning("⚠️ 请先输入标题、关键词或 arXiv ID。")
        else:
            bg_submit("arXiv 检索：" + q.strip()[:40],
                      lambda progress, _q=q.strip(): _w_arxiv_search(_q, progress),
                      kind=_K_ARXIV)
            _toast("🔍 已转入后台检索，可切换其他页面")
    if _running:
        st.caption("⏳ arXiv 检索正在后台运行（实时进度见左侧「任务中心」）——"
                   "可切换其他页面，完成后结果自动出现在本页。")

    res = st.session_state.get("arxiv_res") or []
    if st.session_state.get("arxiv_searched") and not res:
        empty_state("arXiv 未返回任何结果。",
                    hints=["换个更宽泛的关键词（如 transformer）",
                           "直接输入 arXiv ID（如 1706.03762）", _SEARCH_DEMO_HINT])
    for i, p in enumerate(res):
        if not isinstance(p, dict):
            continue
        st.markdown(f"**{i+1}. {p.get('title','(无标题)')}**（{p.get('published','')}，{p.get('arxiv_id','')}）")
        long_block(p.get("summary", ""), title=f"摘要 · {p.get('arxiv_id','')}", limit=160)
        if p.get("github"):
            st.markdown("💻 开源代码：" + str(p["github"][0]))
        key = str(p.get("id") or p.get("arxiv_id") or i)
        _ing = bg_running(kinds={_K_INGEST})
        if st.button("⬇️ 下载并入库", key=f"ad_{key}", disabled=_ing,
                     help="后台下载：提交后可切换其他页面，进度见左侧「任务中心」"):
            bg_submit("下载入库：" + str(p.get("title") or "")[:40],
                      lambda progress, _p=p: _w_ingest(ctx, _p, progress),
                      kind=_K_INGEST, goal=key)
            _toast("⬇️ 已转入后台下载，可切换其他页面")
        # 该篇的入库结果：后台交付写入，渲染在按钮下方（与原内联同位置）
        _st = (st.session_state.get("ingest_status") or {}).get(key)
        if _st:
            _lvl, _msg = _st
            {"success": st.success, "warning": st.warning}.get(_lvl, st.error)(_msg)
    if bg_running(kinds={_K_INGEST}):
        st.caption("⏳ 下载入库任务正在后台运行（实时进度见左侧「任务中心」）——"
                   "可切换其他页面，完成后结果自动出现在对应论文下方。")


def _batch(ctx):
    section_header("参考文献批量抓取", icon="📋")
    titles = st.text_area("标题列表（每行一条）", height=130, key="bt")
    _running = bg_running(kinds={_K_BATCH})
    if st.button("🚀 批量下载入库", disabled=_running,
                 help="后台批量抓取：提交后可切换其他页面，逐条进度见左侧「任务中心」"):
        lines = [x.strip() for x in str(titles or "").split("\n") if x.strip()]
        if not lines:
            st.warning("⚠️ 请先粘贴至少一个论文标题（每行一条）。")
        else:
            bg_submit("批量下载入库（%d 条）" % len(lines),
                      lambda progress, _t=str(titles): _w_batch(ctx, _t, progress),
                      kind=_K_BATCH)
            _toast("🚀 已转入后台批量抓取，可切换其他页面")
    if _running:
        st.caption("⏳ 批量抓取正在后台运行（实时进度见左侧「任务中心」）——"
                   "可切换其他页面，完成后结果自动出现在本页。")

    # 上一轮批量抓取的结果：后台交付写入 session_state，渲染即清（与原内联行为一致）
    rep = st.session_state.get("batch_report")
    if rep:
        st.session_state["batch_report"] = None
        if not isinstance(rep, dict):
            st.warning("⚠️ 批量抓取返回数据异常，请重试。")
            return
        statuses = rep.get("statuses") or []
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


def _others(ctx):
    c1, c2 = st.columns([1, 1])
    with c1:
        section_header("PubMed", icon="🧬")
        pq = st.text_input("关键词", key="pq")
        _running = bg_running(kinds={_K_PUBMED})
        if st.button("🔍 PubMed", disabled=_running,
                     help="后台检索：提交后可切换其他页面，进度见左侧「任务中心」"):
            if not (pq or "").strip():
                st.warning("⚠️ 请输入检索关键词。")
            else:
                bg_submit("PubMed 检索：" + pq.strip()[:40],
                          lambda progress, _q=pq.strip(): _w_pubmed(_q, progress),
                          kind=_K_PUBMED)
                _toast("🧬 已转入后台检索，可切换其他页面")
        if _running:
            st.caption("⏳ PubMed 检索正在后台运行（进度见左侧「任务中心」）——可切换其他页面。")
        rows = st.session_state.get("pm") or []
        if st.session_state.get("pm_searched") and not rows:
            empty_state("PubMed 未返回任何结果。",
                        hints=["换成英文医学关键词", _SEARCH_DEMO_HINT])
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
        section_header("网页搜索", icon="🌐")
        wq = st.text_input("关键词", key="wq")
        _running = bg_running(kinds={_K_WEB})
        if st.button("🔍 DuckDuckGo", disabled=_running,
                     help="后台检索：提交后可切换其他页面，进度见左侧「任务中心」"):
            if not (wq or "").strip():
                st.warning("⚠️ 请输入检索关键词。")
            else:
                bg_submit("网页搜索：" + wq.strip()[:40],
                          lambda progress, _q=wq.strip(): _w_web(_q, progress),
                          kind=_K_WEB)
                _toast("🌐 已转入后台检索，可切换其他页面")
        if _running:
            st.caption("⏳ 网页搜索正在后台运行（进度见左侧「任务中心」）——可切换其他页面。")
        web = st.session_state.get("web") or []
        if st.session_state.get("web_searched") and not web:
            empty_state("网页未返回任何结果。",
                        hints=["换个关键词试试", _SEARCH_DEMO_HINT])
        if web:
            for r in web:
                if not isinstance(r, dict):
                    continue
                if r.get("error"):
                    st.warning(f"⚠️ {str(r['error'])[:140]}")
                else:
                    st.markdown(f"🔗 **[{str(r.get('title',''))[:90]}]({r.get('url','#')})**")


def _quality(ctx):
    refs = st.text_area("粘贴参考文献（每行一条）", height=160, key="qrefs")
    _running = bg_running(kinds={_K_QUALITY})
    if st.button("🧹 分析并剔除应付式引用", disabled=_running,
                 help="后台分析：提交后可切换其他页面，进度见左侧「任务中心」"):
        lines = [x.strip() for x in str(refs or "").split("\n") if x.strip()]
        if not lines:
            st.warning("⚠️ 请先粘贴至少一条参考文献（每行一条）。")
        elif llm_guard(ctx, "引用含金量分析"):
            prompt = ("判断每条引用是实质性引用还是应付式引用，分组输出'建议保留/建议剔除'并说明中文理由。\n"
                      + "\n".join(lines))
            bg_submit("引用含金量分析",
                      lambda progress, _p=prompt: _w_quality(ctx, _p, progress),
                      kind=_K_QUALITY)
            _toast("🧹 已转入后台分析，可切换其他页面")
    if _running:
        st.caption("⏳ 引用含金量分析正在后台运行（进度见左侧「任务中心」）——可切换其他页面。")

    # 后台交付的 LLM 产出：渲染即清（与原内联行为一致）；
    # 降级内容由 show_llm_result 走可见降级态，绝不渲染「✅ 分析完成」
    out = st.session_state.get("quality_out")
    if out is not None:
        st.session_state["quality_out"] = None
        show_llm_result(out, success_msg="✅ 分析完成",
                        title="引用含金量分析报告", action="引用含金量分析", limit=200)


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
    cat = st.text_input("arXiv 分类 / 关键词", value="cs.AI", key="rc")
    uid = getattr(getattr(ctx, "memory", None), "user", "default")
    _running = bg_running(kinds={_K_RECOMMEND})
    if st.button("✨ 刷新并推荐", disabled=_running,
                 help="后台拉取：提交后可切换其他页面，进度见左侧「任务中心」"):
        bg_submit("个性化推荐：" + str(cat),
                  lambda progress, _c=str(cat): _w_recommend(_c, progress),
                  kind=_K_RECOMMEND)
        _toast("✨ 已转入后台拉取，可切换其他页面")
    if _running:
        st.caption("⏳ 正在后台拉取候选论文（进度见左侧「任务中心」）——可切换其他页面。")

    rows = st.session_state.get("rec_rows") or []
    if not rows:
        empty_state("暂无推荐结果。", hints=[_SEARCH_DEMO_HINT])
    for score, p, why in rows[:MAX_REC_SHOW]:
        if not isinstance(p, dict):
            continue
        c1, c2 = st.columns([2, 1])
        with c1:
            st.markdown(f"**{str(p.get('title',''))[:90]}**  ·  {p.get('arxiv_id','')}  ·  相关度 {score:.2f}")
            st.caption("推荐理由：" + "；".join(why or ["该领域最新提交，值得关注"]))
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
        st.caption(f"当前用户 `{uid}`（切换侧边栏用户 ID 可演示多用户差异化推荐）；"
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
    try:
        qvecs = ctx.embed([str(p.get("summary", ""))[:1500] for p in cand])
    except Exception:
        # 语义向量不可用：相关性维度全部按 0.0 处理（与下方
        # `except Exception: rel = 0.0` 的既有范式一致）。用 [None]*len(cand)
        # 占位而非 []——[] 会让 zip 直接截空、把整个候选列表清空，
        # 那是把「向量路失败」误做成「没有候选」。降级必须让用户看见：
        # caption 说明哪个维度被清零、排序靠什么。
        qvecs = [None] * len(cand)
        st.caption("⚠️ 语义向量不可用（本地 embedding 模型未就绪或推理失败）："
                   "「与本地文献库主题相近度」已按 0 分处理，排序改由开源代码 / "
                   "社区点赞 / 研究画像决定；可稍后重试恢复语义排序。")
    rows = []
    for p, v in zip(cand, qvecs):
        try:
            rel = float(np.dot(centroid, np.asarray(v, dtype=float))) if (centroid is not None and v is not None) else 0.0
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
    st.caption("系统按检查点增量推送（只给上次以来的新增论文），可配合 scripts/watch_daemon.py 定时运行。")
    try:
        from services import track
        cur = track.get_subscriptions(ctx) or []
    except Exception as e:
        friendly_error("读取订阅配置", e, reasons=["追踪配置文件缺失"],
                       actions=["忽略并直接输入新话题即可自动创建"])
        return
    topics = st.text_input("订阅话题（逗号分隔）", value=", ".join(cur), key="trk_topics")
    c1, c2 = st.columns([2, 1])
    _running = bg_running(kinds={_K_TRACK})
    if c1.button("🔄 运行一次追踪", disabled=_running,
                 help="后台抓取：提交后可切换其他页面，进度见左侧「任务中心」"):
        if not (topics or "").strip():
            st.warning("⚠️ 请先填写至少一个订阅话题（多个用逗号分隔）。")
        else:
            _lst = [t.strip() for t in topics.split(",") if t.strip()]
            try:
                # 订阅配置写入是快本地操作，保留在主线程；只有抓取进后台
                track.set_subscriptions(ctx, _lst)
            except Exception as e:
                friendly_error("保存订阅话题", e, reasons=["追踪配置文件不可写"],
                               actions=["检查 storage 目录写权限"])
            else:
                bg_submit("动态追踪（%d 个话题）" % len(_lst),
                          lambda progress, _l=_lst: _w_track(ctx, _l, progress),
                          kind=_K_TRACK)
                _toast("⏰ 已转入后台抓取，可切换其他页面")
    if c2.button("♻️ 重置检查点"):
        try:
            track.reset_checkpoints(ctx)
            st.session_state.pop("trk_report", None)
            st.success("✅ 检查点已清空，下次追踪将把当前论文视为全新。")
        except Exception as e:
            friendly_error("重置检查点", e, reasons=["追踪配置文件不可写"],
                           actions=["检查 storage 目录写权限"])
    if _running:
        st.caption("⏳ 追踪任务正在后台运行（进度见左侧「任务中心」）——可切换其他页面。")

    reports = st.session_state.get("trk_report") or []
    if not reports:
        empty_state("暂无追踪报告。", hints=[_SEARCH_DEMO_HINT])
    for r in reports:
        if not isinstance(r, dict):
            continue
        if r.get("error"):
            st.warning(f"⚠️ {r.get('topic','')} 抓取失败（网络受限）：{str(r['error'])[:120]}")
            continue
        new = r.get("new") or []
        st.markdown(f"**{r.get('topic','')}** · 本轮扫描 {r.get('count', 0)} 篇，新增 **{len(new)}** 篇")
        if not new:
            st.caption("本话题暂无新增论文。")
        for p in new[:6]:
            if not isinstance(p, dict):
                continue
            tag = " 💻开源" if p.get("github") else ""
            st.markdown(f"- `{p.get('arxiv_id','')}` {str(p.get('title',''))[:80]}"
                        f"（{p.get('published','')}）{tag}")
            long_block(str(p.get("summary", ""))[:300], title="摘要", limit=100)


def render(ctx):
    ensure_state({"arxiv_res": list, "pm": list, "web": list, "rec_rows": list, "trk_report": list,
                  "arxiv_searched": False, "pm_searched": False, "web_searched": False,
                  "ingest_status": dict, "batch_report": None, "quality_out": None})
    # 后台任务交付：任务完成（含失败）时按 kind 认领，逐分支等价旧内联流程。
    # 必须放在页面顶部——flash 由 app.py 在页面渲染前重放，交付里写入的提示
    # 依赖 bg_consume 内部的重绘才能在当轮可见。
    bg_consume({
        _K_ARXIV: lambda k, t: _consume_arxiv_search(k, t),
        _K_INGEST: lambda k, t: _consume_ingest(k, t),
        _K_BATCH: lambda k, t: _consume_batch(k, t),
        _K_PUBMED: lambda k, t: _consume_pubmed(k, t),
        _K_WEB: lambda k, t: _consume_web(k, t),
        _K_QUALITY: lambda k, t: _consume_quality(k, t),
        _K_RECOMMEND: lambda k, t: _consume_recommend(ctx, k, t),
        _K_TRACK: lambda k, t: _consume_track(k, t),
    })
    page_header("🔍 论文自动检索、追踪与推荐",
                "arXiv/PubMed/网页批量检索、引用含金量筛选、个性化推荐、动态追踪订阅。",
                icon="🔍")
    st.caption("本页面依赖外网；比赛现场断网时请先「🚀 一键加载演示数据」，"
               "用📁文献库 / 💬问答 / 🕸️图谱页完成演示。")

    # key=：页签选中态必须钉住。提交后台任务会触发重跑，无 key 时 Streamlit 会把
    # 页签重置回第一个（实测：在「检索与下载」点一次 arXiv 检索，立刻跳回「订阅与推荐」），
    # 用户刚填的关键词和结果当场消失——真机截图脚本也因此在 p6 卡死。
    tab_sub, tab_search, tab_quality = st.tabs(["订阅与推荐", "检索与下载", "引用含金量"],
                                               key="search_tab")

    with tab_sub:
        section_header("动态追踪订阅", icon="⏰")
        try:
            _track(ctx)
        except Exception as e:
            show_error("渲染「动态追踪」区块", e)
            st.caption("🛠️ 怎么办：其余模块不受影响，可继续使用。")

        section_header("个性化推荐", icon="⭐")
        try:
            _recommend(ctx)
        except Exception as e:
            show_error("渲染「个性化推荐」区块", e)
            st.caption("🛠️ 怎么办：其余模块不受影响，可继续使用。")

    with tab_search:
        for fn in (_arxiv, _others, _batch):
            try:
                fn(ctx)
            except Exception as e:
                show_error(f"渲染「{fn.__name__[1:]}」区块", e)
                st.caption("🛠️ 怎么办：其余检索模块不受影响，可继续使用。")

    with tab_quality:
        try:
            _quality(ctx)
        except Exception as e:
            show_error("渲染「引用含金量」区块", e)
            st.caption("🛠️ 怎么办：其余模块不受影响，可继续使用。")

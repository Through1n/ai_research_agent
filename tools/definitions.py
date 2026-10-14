"""工具定义：将各服务能力注册为可被 Function Calling 调用的工具。"""
import os
import sys
import json
import re
import shutil
import tempfile
import subprocess
from datetime import datetime, timezone

from tools.registry import Registry
from services import extern, library, rag, graph as gsvc, writing, track
from services.citation_quality import score_references, assess_reference

# 单条工具调用最多对多少条参考文献走真实网络被引查询（避免请求风暴/超时）
CITATION_NETWORK_BUDGET = 5


def trunc(t, n=600):
    t = t or ""
    return t if len(t) <= n else t[:n] + "…"


# ---------------- 文献库 / 检索 ----------------
def t_list_papers(ctx):
    return {"count": len(ctx.files),
            "papers": [{"file": f, "title": ctx.papers[f].get("title", f),
                        "year": ctx.papers[f].get("year", "")} for f in ctx.files]}


def t_search_literature(ctx, query, k=6):
    sources, _ = rag.retrieve(ctx, query, final_k=k)
    return {"query": query, "results": [
        {"ref": c["source"], "page": c["page"], "snippet": c["text"][:300]} for c in sources]}


def t_multi_paper(ctx, titles, focus=""):
    names = [x.strip() for x in titles.replace(",", "\n").split("\n") if x.strip()]
    out = {}
    for want in names:
        match = next((f for f in ctx.files if want.lower() in f.lower()
                      or want.lower() in ctx.papers[f].get("title", "").lower()), None)
        if match:
            text = "\n".join(c["text"] for c in ctx.chunks if c["source"] == match)
            out[match] = trunc(text, 1800)
    return {"focus": focus, "papers": out}


# ---------------- 外部数据源 / 下载 ----------------
def t_arxiv(ctx, query, max_results=5):
    return {"results": [{"id": p["arxiv_id"], "title": p["title"], "published": p["published"],
                         "summary": p["summary"][:220], "github": p["github"]}
                        for p in extern.search_arxiv(query, max_results)]}


def t_pubmed(ctx, query, max_results=5):
    return {"results": extern.search_pubmed(query, max_results)}


def t_web(ctx, query):
    return {"results": extern.web_search(query, 6)}


# ---------------- 追踪 / 复现性 / 引用质量 ----------------
def t_track_arxiv(ctx, arxiv_id, since_days=7):
    """定时追踪单篇 arXiv 论文：返回最新版本信息，并判断近 N 天是否有更新。"""
    try:
        import arxiv
        base = str(arxiv_id).split("v")[0].strip()
        if not base:
            return {"error": "arxiv_id 为空"}
        search = arxiv.Search(id_list=[base], max_results=1)
        p = next(search.results())
        updated = p.updated or p.published
        now = datetime.now(timezone.utc)
        has_update = False
        delta_days = None
        try:
            delta_days = (now - updated).total_seconds() / 86400.0
            has_update = 0 <= delta_days <= float(since_days)
        except Exception:
            has_update = False
        latest = {
            "id": p.get_short_id(),
            "title": p.title,
            "published": str(p.published.date()) if p.published else "",
            "updated": updated.isoformat() if updated else "",
        }
        if has_update:
            msg = f"近 {since_days} 天内该论文有更新（距今约 {delta_days:.1f} 天）"
        else:
            msg = f"已检查，近 {since_days} 天无新版本" if delta_days is not None else "已检查，无新版本"
        return {
            "arxiv_id": base,
            "checked_at": now.isoformat(timespec="seconds"),
            "has_update": has_update,
            "latest": latest,
            "message": msg,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def t_github_search(ctx, repo_url):
    """查询 GitHub 仓库复现性指标（stars/forks/license/最近推送/开放 issue + README）。

    真实调用 GitHub REST API（services/extern.py:github_repo_info）；限流/失败时返回明确降级说明。
    """
    try:
        info = extern.github_repo_info(repo_url)
    except Exception as e:
        return {"repo_url": repo_url, "degraded": True,
                "error": f"{type(e).__name__}: {str(e)[:120]}", "note": "GitHub API 不可达"}
    if info.get("error") or info.get("degraded"):
        return {"repo": info.get("repo", repo_url), "degraded": True,
                "error": info.get("error", ""), "reasons": info.get("reasons", []),
                "reproducibility": info.get("reproducibility", "未知"),
                "note": "GitHub 数据未取到，未做推测性评级"}
    # 兼容旧字段：repro_score 为 A/B/C 字母评级（由 高/中/低 映射而来）
    letter = {"高": "A", "中": "B", "低": "C"}.get(info.get("reproducibility", ""), "C")
    return {
        "repo": info.get("repo"),
        "url": info.get("url"),
        "stars": info.get("stars"),
        "forks": info.get("forks"),
        "open_issues": info.get("open_issues"),
        "license": info.get("license"),
        "pushed_at": info.get("pushed_at"),
        "last_push_days": info.get("last_push_days"),
        "archived": info.get("archived"),
        "has_readme": info.get("has_readme"),
        "readme_chars": info.get("readme_chars"),
        "reproducibility": info.get("reproducibility"),
        "repro_score": letter,
        "reasons": info.get("reasons", []),
        "degraded": False,
    }


def t_citation_quality(ctx, references):
    """对参考文献列表做含金量评估（真实被引数据 + venue 等级 + 引用位置 + 主题相关性）。

    前 CITATION_NETWORK_BUDGET 条走 OpenAlex→Semantic Scholar→Crossref 真实被引链路；
    全部条目同时给出离线规则打分。取不到真实被引时如实标注，不编造数字。
    """
    try:
        refs = [r for r in (references or []) if isinstance(r, str) and r.strip()]
        if not refs:
            return {"error": "references 为空或无有效字符串条目"}
        results = score_references(refs)
        scores = [r["score"] for r in results]
        avg = round(sum(scores) / len(scores), 1)
        dist = {"A": 0, "B": 0, "C": 0}
        for r in results:
            dist[r["grade"]] = dist.get(r["grade"], 0) + 1
        paired = sorted(zip(refs, results), key=lambda x: x[1]["score"], reverse=True)
        top = [{"ref": ref, "score": res["score"], "grade": res["grade"]}
               for ref, res in paired[:3]]
        bottom = [{"ref": ref, "score": res["score"], "grade": res["grade"]}
                  for ref, res in paired[-3:][::-1]]

        assessed, real_hits = [], 0
        for i, ref in enumerate(refs[:CITATION_NETWORK_BUDGET]):
            try:
                a = assess_reference(ref, use_network=True)
            except Exception as e:
                a = {"level": "评估失败", "real_citations": None, "venue_tier": "未知",
                     "score": 0, "reasons": [f"{type(e).__name__}: {str(e)[:80]}"]}
            if a.get("real_citations") is not None:
                real_hits += 1
            assessed.append({"ref": ref[:160], "level": a.get("level"),
                             "real_citations": a.get("real_citations"),
                             "venue_tier": a.get("venue_tier"),
                             "score": a.get("score"), "reasons": a.get("reasons", [])[:4]})
        return {
            "scored_count": len(results),
            "avg_score": avg,
            "grade_distribution": dist,
            "top_refs": top,
            "bottom_refs": bottom,
            "network_assessed": len(assessed),
            "real_citation_hits": real_hits,
            "assessed": assessed,
            "note": (f"前 {len(assessed)} 条已查询真实被引（OpenAlex→Semantic Scholar→Crossref）；"
                     f"其中 {real_hits} 条取到真实数字，其余标注为不可得，未做任何数值猜测"),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def t_track_topic(ctx, topic="", arxiv_id="", max_results=8, only_new=True):
    """定时追踪：按话题/分类（如 cs.AI 或关键词）抓取最新论文，并给出「上次检查点之后」的新论文。

    底层调用 services/track.py 的 diff_new（检查点持久化在长期记忆），抓取走 arXiv 真实接口。
    二选一传参：topic（话题/分类）或 arxiv_id（单篇论文版本追踪）。
    """
    if arxiv_id:
        return {"mode": "arxiv_id", **t_track_arxiv(ctx, arxiv_id)}
    try:
        return {"mode": "topic", **track.track_topic(ctx, topic, int(max_results), bool(only_new))}
    except Exception as e:
        return {"mode": "topic", "topic": topic, "error": f"{type(e).__name__}: {str(e)[:120]}",
                "count": 0, "new": []}


def _ingest_arxiv_paper(ctx, p):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()                      # 交出句柄：Windows 上重复打开同名文件才不会被占用
    try:
        extern.download(p["pdf_url"], tmp.name, timeout=120)
        with open(tmp.name, "rb") as fh:
            data = fh.read()
    finally:
        # 下载失败/读失败也要删，否则每个失败任务都在临时目录留一份残骸
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    fname = p["arxiv_id"].split("v")[0] + ".pdf"
    prepared = library.prepare_pdf(ctx, data=data, filename=fname)
    if prepared.get("error"):
        return {"ok": False, "error": prepared.get("error"),
                "reason": prepared.get("reason", "")}
    if prepared["dup_name"]:
        return {"ok": False, "duplicate": prepared["dup_name"],
                "sim": round(prepared["dup_sim"], 3), "note": "语义查重命中，该论文已在库"}
    ver = p["arxiv_id"][p["arxiv_id"].find("v"):] if "v" in p["arxiv_id"] else ""
    paper = library.commit(ctx, prepared, title=p["title"], authors="; ".join(p["authors"]),
                           arxiv_id=p["arxiv_id"].split("v")[0], version=ver,
                           year=p["published"][:4], github=p["github"])
    return {"ok": True, "added": paper["filename"], "title": paper["title"], "pages": paper["page_count"]}


def t_download(ctx, title):
    p, err = extern.match_arxiv_title(title)
    if err:
        return {"ok": False, "error": err}
    return _ingest_arxiv_paper(ctx, p)


def t_batch_download(ctx, titles):
    statuses = []
    for line in [x.strip() for x in titles.split("\n") if x.strip()]:
        p, err = extern.match_arxiv_title(line)
        if err:
            statuses.append({"query": line, "ok": False, "error": err}); continue
        statuses.append({"query": line, **_ingest_arxiv_paper(ctx, p)})
    return {"processed": len(statuses), "statuses": statuses}


# ---------------- 图谱 / 综述 ----------------
def _graph(ctx):
    """构建图谱并返回 (G, edges, metrics, cornerstone, frontier)。

    第 5 项由原来的「入度=0 孤儿列表」升级为 detect_frontier 的前沿/潜力论文结果（带 score 与 reasons）。
    """
    G, edges = gsvc.build_graph(ctx)
    metrics = gsvc.analyze(G)
    corner, _ = gsvc.classify_papers(G, metrics)
    try:
        frontier = gsvc.detect_frontier(ctx, G, metrics)
    except Exception:
        frontier = []
    return G, edges, metrics, corner, frontier


def t_build_graph(ctx):
    import streamlit as st
    G, edges, metrics, corner, frontier = _graph(ctx)
    st.session_state["last_graph_html"] = gsvc.graph_html(G, metrics, corner)
    return {"nodes": len(G.nodes), "edge_count": len(edges),
            "edges": [{"from": a, "to": b} for a, b, _ in edges],
            "cornerstone": [ctx.papers[n]["title"] for n in corner],
            # 字段名沿用旧契约 edge_derivative，内容改为 frontier 模型输出（含可解释理由）
            "edge_derivative": [{"paper": f["title"], "score": f["score"],
                                 "reasons": f["reasons"]} for f in frontier]}


def t_review(ctx):
    G, edges, metrics, corner, edge = _graph(ctx)
    return {"review": gsvc.generate_review(ctx, G, metrics, corner)}


def t_future(ctx):
    G, _, _, _, _ = _graph(ctx)
    return {"future_work": gsvc.explore_future(ctx, G)}


# ---------------- 写作 / 翻译 ----------------
def t_outline(ctx, idea):
    return {"outline": writing.generate_outline(ctx, idea)}


def t_diagram(ctx, kind, topic):
    return {"kind": kind, "code": writing.diagram(ctx, kind, topic)}


def t_translate(ctx, text, direction):
    if direction == "zh-to-en":
        return {"translation": writing.translate_to_english(ctx, text)}
    return {"translation": writing.translate_to_chinese(ctx, text)}


# ---------------- 代码执行 ----------------
def _tail_text(value, limit):
    """安全截尾：保留最后 limit 个字符；兼容 None / bytes / 超长文本。"""
    try:
        if not value:
            return ""
        if isinstance(value, (bytes, bytearray)):
            value = bytes(value).decode("utf-8", errors="replace")
        value = str(value)
        return value[-limit:] if len(value) > limit else value
    except Exception:
        return ""


def t_run_python(ctx, code):
    from core.sandbox import validate_code, SandboxViolation
    try:
        validate_code(code)
    except SandboxViolation as e:
        return {"rc": -2, "blocked": True, "reason": f"已被安全护栏拦截：{e}"}
    except Exception as e:
        return {"rc": -2, "blocked": True,
                "reason": f"静态检查失败：{type(e).__name__}: {str(e)[:120]}"}
    try:
        work = tempfile.mkdtemp(prefix="ai4s_code_")
        fp = os.path.join(work, "cell.py")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(code)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            p = subprocess.run([sys.executable, fp], cwd=work, capture_output=True,
                               timeout=25, env=env, encoding="utf-8", errors="replace")
            stdout_text = p.stdout or ""
            return {"rc": p.returncode,
                    "stdout": _tail_text(stdout_text, 3000),
                    "stderr": _tail_text(p.stderr, 1500),
                    "stdout_truncated": len(stdout_text) > 3000}
        except subprocess.TimeoutExpired as e:
            # 超时后 p.stdout 不可用，从异常对象里尽力回收已产生的输出
            return {"rc": -1, "error": "执行超时(>25s)",
                    "stdout": _tail_text(getattr(e, "stdout", None), 3000),
                    "stderr": _tail_text(getattr(e, "stderr", None), 1500)}
    except Exception as e:
        return {"rc": -3, "error": f"{type(e).__name__}: {str(e)[:200]}",
                "stdout": "", "stderr": ""}
    finally:
        # 每次代码执行都 mkdtemp，不清理会在长时间运行的服务里无限堆积
        # （实测：连跑 3 次残留 3 个 ai4s_code_* 目录）。Linux 容器 /tmp 同样会涨。
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass


# ---------------- 记忆 ----------------
def t_save_note(ctx, text):
    ctx.memory.add_note(text)
    return {"saved": True}


def t_recall(ctx, query):
    return {"notes": ctx.memory.relevant_notes(query)}


# ============================ 注册 ============================
def build_registry():
    r = Registry()
    r.register("list_papers", "列出当前文献库中的全部论文及其标题、年份。",
               {"type": "object", "properties": {}}, t_list_papers)
    r.register("search_literature", "在本地文献库中做抗幻觉混合检索，返回带页码的相关片段。",
               {"type": "object", "properties": {"query": {"type": "string", "description": "检索问题/关键词"},
                                               "k": {"type": "integer", "description": "返回条数"}},
                "required": ["query"]}, t_search_literature)
    r.register("multi_paper", "获取多篇指定论文的正文上下文，用于跨文献对比与多文推理。",
               {"type": "object", "properties": {"titles": {"type": "string", "description": "论文标题，逗号或换行分隔"},
                                               "focus": {"type": "string"}}, "required": ["titles"]}, t_multi_paper)
    r.register("arxiv_search", "调用 arXiv 接口检索最新/相关论文，返回标题、摘要、开源代码线索。",
               {"type": "object", "properties": {"query": {"type": "string"},
                                               "max_results": {"type": "integer"}},
                "required": ["query"]}, t_arxiv)
    r.register("pubmed_search", "调用 PubMed(E-utilities) 检索生物医学文献。",
               {"type": "object", "properties": {"query": {"type": "string"},
                                               "max_results": {"type": "integer"}},
                "required": ["query"]}, t_pubmed)
    r.register("web_search", "使用 DuckDuckGo 进行网页搜索（无需 API Key）。",
               {"type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"]}, t_web)
    r.register("track_arxiv", "定时追踪单篇 arXiv 论文，返回最新版本信息并判断近 N 天是否有更新。",
               {"type": "object", "properties": {
                   "arxiv_id": {"type": "string", "description": "arXiv 编号，如 2301.00001 或 2301.00001v2"},
                   "since_days": {"type": "integer", "description": "判定更新的时间窗口（天）", "default": 7}},
                "required": ["arxiv_id"]}, t_track_arxiv)
    r.register("track_topic", "定时追踪：按 arXiv 分类（如 cs.AI）或关键词抓取最新论文，返回「上次检查点之后」的新增论文"
                              "（检查点持久化在长期记忆）；也可传入 arxiv_id 追踪单篇论文的版本更新。"
                              "用于回答「最近有什么新论文/这个方向有新进展吗」。",
               {"type": "object", "properties": {
                   "topic": {"type": "string", "description": "arXiv 分类（如 cs.AI/cs.CL）或关键词；与 arxiv_id 二选一"},
                   "arxiv_id": {"type": "string", "description": "要追踪的 arXiv 编号（如 2301.00001）；与 topic 二选一"},
                   "max_results": {"type": "integer", "description": "每个话题抓取条数", "default": 8},
                   "only_new": {"type": "boolean", "description": "是否只返回上次检查点之后的新论文", "default": True}},
                }, t_track_topic)
    r.register("github_search", "查询 GitHub 仓库复现性指标（stars/license/最近推送），并给出复现性评级 A/B/C。",
               {"type": "object", "properties": {
                   "repo_url": {"type": "string", "description": "形如 https://github.com/owner/repo"}},
                "required": ["repo_url"]}, t_github_search)
    r.register("citation_quality", "对参考文献列表做含金量打分（arXiv/DOI/年份/顶会顶刊），返回均分与等级分布。",
               {"type": "object", "properties": {
                   "references": {"type": "array", "items": {"type": "string"},
                                  "description": "参考文献原文或 title|year|venue 格式字符串列表"}},
                "required": ["references"]}, t_citation_quality)
    r.register("download_paper", "按论文标题在 arXiv 检索并自动下载、语义查重、入库。",
               {"type": "object", "properties": {"title": {"type": "string"}},
                "required": ["title"]}, t_download)
    r.register("batch_download", "按标题列表批量从 arXiv 下载并入库（每行一条）。",
               {"type": "object", "properties": {"titles": {"type": "string"}},
                "required": ["titles"]}, t_batch_download)
    r.register("build_citation_graph", "解析文献间真实引用关系，构建图谱并挖掘基石/边缘论文。",
               {"type": "object", "properties": {}}, t_build_graph)
    r.register("generate_review", "基于图谱与文献内容自动生成中文领域综述。",
               {"type": "object", "properties": {}}, t_review)
    r.register("explore_future_work", "提取 Future Work 并结合时间线自动排除已被解决的旧 idea。",
               {"type": "object", "properties": {}}, t_future)
    r.register("write_outline", "根据 Idea 生成含真实无幻觉参考文献的论文写作框架。",
               {"type": "object", "properties": {"idea": {"type": "string"}},
                "required": ["idea"]}, t_outline)
    r.register("draw_diagram", "生成 Mermaid/TikZ/Graphviz/Matplotlib 架构拓扑图代码。",
               {"type": "object", "properties": {"kind": {"type": "string", "enum": ["mermaid", "tikz", "graphviz", "matplotlib"]},
                                               "topic": {"type": "string"}},
                "required": ["kind", "topic"]}, t_diagram)
    r.register("translate_text", "学术翻译：中英互译。",
               {"type": "object", "properties": {"text": {"type": "string"},
                                               "direction": {"type": "string", "enum": ["zh-to-en", "en-to-zh"]}},
                "required": ["text", "direction"]}, t_translate)
    r.register("run_python", "在隔离的 Python 环境中执行代码并返回标准输出/错误（数据处理与复现）。",
               {"type": "object", "properties": {"code": {"type": "string"}},
                "required": ["code"]}, t_run_python)
    r.register("save_note", "把一条要点写入长期记忆。",
               {"type": "object", "properties": {"text": {"type": "string"}},
                "required": ["text"]}, t_save_note)
    r.register("recall_memory", "从长期记忆中检索与问题相关的笔记。",
               {"type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"]}, t_recall)
    return r

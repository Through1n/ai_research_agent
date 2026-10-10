"""工具定义：将各服务能力注册为可被 Function Calling 调用的工具。"""
import os
import sys
import json
import tempfile
import subprocess

from tools.registry import Registry
from services import extern, library, rag, graph as gsvc, writing


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


def _ingest_arxiv_paper(ctx, p):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    extern.download(p["pdf_url"], tmp.name, timeout=120)
    data = open(tmp.name, "rb").read()
    os.remove(tmp.name)
    fname = p["arxiv_id"].split("v")[0] + ".pdf"
    prepared = library.prepare_pdf(ctx, data=data, filename=fname)
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
    G, edges = gsvc.build_graph(ctx)
    metrics = gsvc.analyze(G)
    corner, edge = gsvc.classify_papers(G, metrics)
    return G, edges, metrics, corner, edge


def t_build_graph(ctx):
    import streamlit as st
    G, edges, metrics, corner, edge = _graph(ctx)
    st.session_state["last_graph_html"] = gsvc.graph_html(G, metrics, corner)
    return {"nodes": len(G.nodes), "edge_count": len(edges),
            "edges": [{"from": a, "to": b} for a, b, _ in edges],
            "cornerstone": [ctx.papers[n]["title"] for n in corner],
            "edge_derivative": [ctx.papers[n]["title"] for n in edge]}


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
def t_run_python(ctx, code):
    from core.sandbox import validate_code, SandboxViolation
    try:
        validate_code(code)
    except SandboxViolation as e:
        return {"rc": -2, "blocked": True, "reason": f"已被安全护栏拦截：{e}"}
    work = tempfile.mkdtemp(prefix="ai4s_code_")
    fp = os.path.join(work, "cell.py")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        p = subprocess.run([sys.executable, fp], cwd=work, capture_output=True,
                           text=True, timeout=25)
        return {"rc": p.returncode, "stdout": p.stdout[-3000], "stderr": p.stderr[-1500]}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "error": "执行超时(>25s)"}


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

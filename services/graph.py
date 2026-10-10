"""局域引用图谱：真实解析论文间引用关系（标题/arXiv 号匹配），图论挖掘基石/边缘论文，生成综述与前沿方向。"""
import re
import math
import networkx as nx

from services.parsing import extract_references_raw


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def _ref_entries(ref_text):
    """把参考文献区切为条目（兼容 [n] / n. 两种编号）。"""
    text = ref_text.replace("\n", " ")
    parts = re.split(r"\s*(?:\[\d+\]|\b\d{1,3}\.\s)", text)
    return [p.strip() for p in parts if len(p.strip()) > 25]


def cites(full_a, ref_a, title_b, arxiv_b):
    """判断论文 A 是否引用了论文 B。返回 (bool, 证据)。"""
    if arxiv_b and re.search(r"\d{4}\.\d{4,5}", arxiv_b):
        if arxiv_b in full_a:
            return True, f"正文/文献中出现 arXiv 号 {arxiv_b}"
    target = norm(title_b)
    toks = target.split()
    if len(toks) < 4:
        return False, ""
    best, best_score = "", 0.0
    for entry in _ref_entries(ref_a):
        en = norm(entry)
        # 连续窗口最佳匹配
        et = en.split()
        window_best = 0.0
        for i in range(0, max(1, len(et) - len(toks) + 1)):
            window = et[i:i + len(toks)]
            overlap = len(set(window) & set(toks)) / len(toks)
            window_best = max(window_best, overlap)
        if window_best > best_score:
            best, best_score = entry[:160], window_best
    if best_score >= 0.8:
        return True, f"参考文献条目高度匹配（{best_score:.0%}）：{best}"
    return False, ""


def build_graph(ctx):
    """构建有向引用图（A→B 表示 A 引用 B）。节点为"逻辑著作"的最新版本代表，旧版本不单独成图。返回 (G, edges_info)。"""
    from services import library as _lib
    G = nx.DiGraph()

    # 每个逻辑著作(arXiv 号)取最新版本文件作为代表节点
    rep_of_group = {gid: _lib.latest_of(files)[0] for gid, files in _lib.group_versions(ctx).items()}
    reps = list(dict.fromkeys(rep_of_group.values()))
    for name in reps:
        p = ctx.papers[name]
        G.add_node(name, title=p.get("title", name), version=p.get("version", ""))

    full, refs = {}, {}
    for name in reps:
        text = "\n".join(c["text"] for c in ctx.chunks if c["source"] == name)
        full[name] = text
        refs[name] = text[int(len(text) * 0.65):]

    edges = []
    for a in reps:
        for b in reps:
            if a == b:
                continue
            pb = ctx.papers[b]
            ok, ev = cites(full[a], refs[a], pb.get("title", b), pb.get("arxiv_id"))
            if ok:
                G.add_edge(a, b, evidence=ev)
                edges.append((a, b, ev))
    return G, edges


def analyze(G):
    """图论指标：PageRank、入度(被引)、中介中心性、特征向量中心性。"""
    if len(G.nodes) == 0:
        return {}
    try:
        pr = nx.pagerank(G, alpha=0.85)
    except Exception:
        pr = {n: 1 / len(G) for n in G.nodes}
    indeg = dict(G.in_degree())
    try:
        bet = nx.betweenness_centrality(G)
    except Exception:
        bet = {n: 0 for n in G.nodes}
    try:
        eig = nx.eigenvector_centrality_numpy(G)
    except Exception:
        eig = dict(pr)
    return {"pagerank": pr, "indegree": indeg, "betweenness": bet, "eigenvector": eig}


def classify_papers(G, metrics):
    """基石论文 = 高被引/高PageRank；边缘衍生 = 入度低(仅引用他人)。"""
    if not metrics:
        return [], []
    pr, indeg = metrics["pagerank"], metrics["indegree"]
    ranked = sorted(pr, key=lambda n: (indeg[n], pr[n]), reverse=True)
    n = len(ranked)
    has_citation = bool(indeg) and max(indeg.values()) > 0
    if has_citation:
        corner = ranked[:max(1, math.ceil(n * 0.3))]
    else:
        corner = ranked[:1]
    edge = [x for x in ranked if indeg[x] == 0]
    return corner, edge


def graph_html(G, metrics, cornerstone):
    """生成自包含 HTML（内联本地 vis-network，离线可用）。"""
    from pathlib import Path
    from config import ROOT
    js = (ROOT / "lib" / "vis-9.1.2" / "vis-network.min.js").read_text(encoding="utf-8")
    css = (ROOT / "lib" / "vis-9.1.2" / "vis-network.css").read_text(encoding="utf-8")
    pr = metrics.get("pagerank", {})
    indeg = metrics.get("indegree", {})
    corner_set = set(cornerstone)
    nodes, edges = [], []
    for name in G.nodes:
        if name in corner_set:
            color, shape = "#E8A33D", "star"
        elif indeg.get(name, 0) == 0:
            color, shape = "#9aa5b1", "dot"
        else:
            color, shape = "#4A90E2", "dot"
        nodes.append({"id": name, "label": G.nodes[name]["title"][:40],
                      "value": 15 + pr.get(name, 0.05) * 120,
                      "color": color, "shape": shape,
                      "title": f"{name}\n被引 {indeg.get(name,0)} 次\nPageRank {pr.get(name,0):.4f}"})
    for a, b in G.edges:
        edges.append({"from": a, "to": b, "arrows": "to", "color": {"color": "#b0b8c1"}})
    import json as _json
    data = _json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}
html,body,#net{{margin:0;padding:0;width:100%;height:100%;}}
#legend{{position:absolute;top:10px;left:10px;background:rgba(255,255,255,.92);padding:8px 12px;
border-radius:8px;font:12px sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.15);z-index:5;}}
#legend span{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;}}
</style></head><body><div id="net"></div>
<div id="legend"><div><span style="background:#E8A33D"></span>基石论文</div>
<div><span style="background:#4A90E2"></span>核心节点</div><div><span style="background:#9aa5b1"></span>边缘衍生</div></div>
<script>{js}</script><script>
var data={data};var nodes=new vis.DataSet(data.nodes),edges=new vis.DataSet(data.edges);
new vis.Network(document.getElementById('net'),{{nodes:nodes,edges:edges}},{{
  nodes:{{shape:'dot',scaling:{{min:12,max:46}},font:{{size:13,face:'sans-serif'}}}},
  edges:{{smooth:{{type:'continuous'}},arrows:{{to:{{scaleFactor:.6}}}}}},
  physics:{{barnesHut:{{gravitationalWeight:-3500,springLength:140}},stabilization:{{iterations:200}}}}
}});
</script></body></html>"""


def generate_review(ctx, G, metrics, cornerstone):
    abstracts = "\n".join(
        f"《{ctx.papers[n].get('title', n)}》({ctx.papers[n].get('year','')}): {ctx.papers[n].get('abstract','')[:500]}"
        for n in ctx.paper_order)
    corner = "、".join(ctx.papers[n].get("title", n) for n in cornerstone)
    prompt = f"""基于以下局域文献库与引用分析，用中文撰写一篇结构完整的简短领域综述（含：领域背景、主要技术脉络、
基石工作（{corner}）的贡献、当前进展与局限）。客观、连贯、800字内。
文献信息：
{abstracts[:6000]}"""
    return ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.4).content


def explore_future(ctx, G):
    """提取各文 Future Work，结合时间线与后续论文，自动排除已被解决的旧 idea。"""
    items = []
    for n in sorted(ctx.paper_order, key=lambda x: ctx.papers[x].get("year", "")):
        text = "\n".join(c["text"] for c in ctx.chunks if c["source"] == n)
        m = re.search(r"(future work|limitations|conclusion|展望).{{0,1200}}", text, re.IGNORECASE | re.DOTALL)
        snippet = m.group(0)[:1200] if m else text[-900:]
        items.append(f"【{ctx.papers[n].get('title', n)} · {ctx.papers[n].get('year','')}】\n{snippet}")
    later = "\n".join(
        f"《{ctx.papers[n].get('title',n)}》({ctx.papers[n].get('year','')})摘要：{ctx.papers[n].get('abstract','')[:300]}"
        for n in ctx.paper_order)
    prompt = f"""下面按时间顺序给出多篇论文的 Future Work / 局限部分，以及后续发表论文的摘要。
请完成"前沿扫描"：1) 逐条判断早期提出的未来方向是否已被后续论文解决或部分解决；
2) 自动排除/标注已解决的旧 idea；3) 提炼出至今仍具探索价值的开放方向。
用中文 Markdown 表格输出，列：提出的方向(来源) | 状态(已解决/部分解决/仍开放) | 判断依据 | 探索价值。
随后用项目符号列出 3-5 个真正值得做的未来方向。

各文 Future Work：
{chr(10).join(items)[:6000]}

后续/同期进展：
{later[:3000]}"""
    return ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.4).content

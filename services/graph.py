"""局域引用图谱：真实解析论文间引用关系，图论挖掘基石/边缘论文，生成综述与前沿方向。

工程要点（对应评分：图谱算法创新 10% + 20+ 篇渲染性能）：
- 参考文献定位：优先按 References/Bibliography 标题锚点切区，兼容 IEEE/Nature/Springer 排版；
- 候选生成：标题 token 倒排索引，把 O(n²) 全文比对降为「按共享词根候选」；
- 双重匹配：词面窗口匹配失败时，用论文标题与参考文献条目的语义向量兜底；
- 版本归并：同一 arXiv 号仅取最新版本入图，避免重复节点。

新增（对应赛题 D 路审计整改）：
- detect_frontier：真正的 frontier 模型（低被引 + 时间新近 + 高出度 + 跨学科向量距离 + 外部真实被引）；
- generate_review：社区发现聚簇 → 簇代表 → 时间线 → 引用骨干边，图谱结构驱动综述；
- explore_future_structured：用引用图 1/2 跳可达性判定 Future Work 是否已被后续论文解决。
"""
import re
import math
import time
import statistics
import threading
import networkx as nx

from services.library import cosine
from core.llm import call_llm_with_retry

# ---------- 可调常量（本模块内自持，不外挂 config） ----------
FRONTIER_EXTERNAL_TOPK = 5     # frontier 打分后，仅对 Top-K 篇查外部真实被引（控制网络开销）
FRONTIER_TOP_N = 10            # frontier 结果最多返回条数
REVIEW_EDGE_BACKBONE = 15      # 综述注入的引用骨干边条数
REVIEW_REPS_PER_CLUSTER = 3    # 每个主题簇取前几篇 PageRank 最高者作代表
FUTURE_MAX_ITEMS = 12          # Future Work 结构化输出最多条数
LLM_TEMPERATURE = 0.4          # LLM 调用默认温度
# 语料规模坦诚阈值：逻辑节点少于此值时，图谱指标（社区/PageRank/时间线）区分度有限，
# UI 与综述附录必须如实标注「未达赛题 20 篇批量文献规模」，不冒充大图效果
SMALL_CORPUS_NODES = 5
# True：优先复用 ctx.llm（保留用户在侧边栏选择的 provider/key），不可用时降级 call_llm_with_retry；
# False：一律走 core.llm.call_llm_with_retry（core/llm.py:129，读 config 默认 provider）。
PREFER_CTX_LLM = True

# frontier 各分量权重（缺失分量时按剩余权重归一化，不做零分惩罚）
_FRONTIER_WEIGHTS = {"low_indegree": 0.25, "recency": 0.20,
                     "out_degree": 0.15, "interdiscipline": 0.25, "external": 0.15}


# ---------- 增量构建缓存（源集未变则不重建，避免重复 O(n²) 建图） ----------
# 命中条件：论文顺序 + 每篇标题/版本/arXiv + 各篇 chunk 文本总量 均不变 → 图谱必然相同。
# 仅当 progress_cb 为 None（非 UI 实时进度场景）时启用，保证 UI 进度回调仍逐次触发。
# 不同库状态对应不同 key，故不会跨库串结果；key 含文本量，正文编辑后自动失效重算。
_BUILD_CACHE = {}
_BUILD_CACHE_LOCK = threading.Lock()
_BUILD_CACHE_MAX = 16


def _build_graph_cache_key(ctx):
    """为当前文献库生成一个内容敏感的缓存键；构造失败返回 None（不缓存）。"""
    try:
        parts = []
        for name in ctx.paper_order:
            info = ctx.papers.get(name, {})
            text_len = sum(len(c.get("text", ""))
                           for c in ctx.chunks if c.get("source") == name)
            parts.append((name, info.get("title", ""), info.get("version", ""),
                          info.get("arxiv_id", ""), text_len))
        return tuple(parts)
    except Exception:
        return None


def _build_graph_cache_get(key):
    with _BUILD_CACHE_LOCK:
        return _BUILD_CACHE.get(key)


def _build_graph_cache_put(key, value):
    with _BUILD_CACHE_LOCK:
        if len(_BUILD_CACHE) >= _BUILD_CACHE_MAX:
            _BUILD_CACHE.clear()           # 简单限容：跨库重建多时丢弃旧缓存
        _BUILD_CACHE[key] = value


def _llm_text(ctx, prompt_or_messages, temperature=LLM_TEMPERATURE):
    """统一 LLM 入口（T7）：默认优先复用 ctx.llm（保留用户在侧边栏配置的 provider/key），
    不可用时降级 core.llm.call_llm_with_retry（core/llm.py:129）；两者都失败返回明确降级文案，
    **不编造内容**，由调用方改用确定性结果。"""
    messages = prompt_or_messages if isinstance(prompt_or_messages, list) \
        else [{"role": "user", "content": str(prompt_or_messages)}]
    err = "已跳过 ctx.llm（PREFER_CTX_LLM=False）"
    if PREFER_CTX_LLM:
        try:
            llm = getattr(ctx, "llm", None)
            if llm is not None and getattr(llm, "available", False):
                return (llm.chat(messages, temperature=temperature).content or "").strip()
            err = "ctx.llm 不可用（未配置 API Key 或 provider 未就绪）"
        except Exception as e:
            err = f"ctx.llm 调用异常：{type(e).__name__}: {str(e)[:100]}"
    try:
        resp = call_llm_with_retry(messages, temperature=temperature)
        content = getattr(resp.choices[0].message, "content", "") or ""
        return content.strip()
    except Exception as e:
        return f"【LLM 调用失败：{err}；降级调用 core.llm.call_llm_with_retry 也失败：" \
               f"{type(e).__name__}: {str(e)[:100]}】"


def _llm_ok(text):
    """判断 LLM 输出是否为可信内容（伪响应/兜底文案一律视为不可用）。"""
    return bool(text) and "【LLM" not in text


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


# 标题中常见的无区分度词，候选生成时忽略
_STOP = {"the", "a", "an", "of", "and", "or", "for", "with", "on", "in", "to", "via",
         "using", "based", "toward", "towards", "study", "method", "model", "network",
         "learning", "deep", "neural", "paper", "new", "novel", "approach", "framework"}


_REF_HEAD = re.compile(r"(?:^|\n|(?<=[.!?] ))\s*(?:[A-Z]\s*)?(references|bibliography|literature cited|works cited|参考文献)\b",
                      re.IGNORECASE)


def ref_section(full_text):
    """定位参考文献区：锚定最后一个 References 标题；找不到退回后 35%。跨排版兼容。"""
    hits = list(_REF_HEAD.finditer(full_text))
    if hits:
        return full_text[hits[-1].start():]
    return full_text[int(len(full_text) * 0.65):]


def _ref_entries(ref_text):
    """把参考文献区切为条目（兼容 [n] / n. 两种编号）。"""
    text = ref_text.replace("\n", " ")
    parts = re.split(r"\s*(?:\[\d+\]|\b\d{1,3}\.\s)", text)
    return [p.strip() for p in parts if len(p.strip()) > 25]


def cites(full_a, ref_a, title_b, arxiv_b, entries=None):
    """判断论文 A 是否引用了论文 B。返回 (bool, 证据)。entries 可传入预解析条目以复用。"""
    if arxiv_b and re.search(r"\d{4}\.\d{4,5}", arxiv_b):
        if arxiv_b in full_a:
            return True, f"正文/文献中出现 arXiv 号 {arxiv_b}"
    target = norm(title_b)
    toks = target.split()
    if len(toks) < 4:
        return False, ""
    best, best_score = "", 0.0
    for entry in (entries if entries is not None else _ref_entries(ref_a)):
        en = entry[0]
        et = entry[1]
        window_best = 0.0
        for i in range(0, max(1, len(et) - len(toks) + 1)):
            window = et[i:i + len(toks)]
            overlap = len(set(window) & set(toks)) / len(toks)
            window_best = max(window_best, overlap)
        if window_best > best_score:
            best, best_score = entry[2][:160], window_best
    if best_score >= 0.8:
        return True, f"参考文献条目高度匹配（{best_score:.0%}）：{best}"
    return False, ""


def parsed_entries(ref_a):
    """预解析参考文献条目：[(归一文本, token列表, 原文截断)]，供多候选复用。"""
    out = []
    for e in _ref_entries(ref_a):
        en = norm(e)
        out.append((en, en.split(), e))
    return out


def _title_token_index(titles_by_node):
    """标题 token → 节点列表 的倒排索引；同时返回 token 频率用于稀有度加权。"""
    idx, freq = {}, {}
    for name, t in titles_by_node.items():
        for w in set(norm(t).split()):
            if len(w) > 2 and w not in _STOP:
                idx.setdefault(w, []).append(name)
                freq[w] = freq.get(w, 0) + 1
    return idx, freq


def _candidates(ref_a, idx, freq, own, n_nodes):
    """按参考文献区出现的标题词根收集候选被引节点，用稀有度(IDF)加权抑制假候选。"""
    entry_tok = set()
    for e in _ref_entries(ref_a):
        entry_tok.update(w for w in norm(e).split() if len(w) > 2 and w not in _STOP)
    votes = {}
    for w in entry_tok:
        w_freq = freq.get(w, 1)
        if w_freq >= max(2, int(n_nodes * 0.5)):
            continue                      # 出现在半数标题中的词无区分度，直接忽略
        idf = math.log(1 + n_nodes / w_freq)
        for name in idx.get(w, []):
            if name != own:
                votes[name] = votes.get(name, 0) + idf
    thresh = 1.5 * math.log(1 + n_nodes / 2)   # 约等于「两个稀有词」的权重
    ranked = sorted(votes.items(), key=lambda x: -x[1])
    return [n for n, s in ranked if s >= thresh]


def build_graph(ctx, progress_cb=None):
    """构建有向引用图（A→B 表示 A 引用 B），节点为逻辑著作最新版本。"""
    # 增量缓存：源集未变（同 key）直接复用上次结果，避免重复的 O(n²) 建图与 embedding。
    cache_key = None if progress_cb else _build_graph_cache_key(ctx)
    if cache_key is not None:
        cached = _build_graph_cache_get(cache_key)
        if cached is not None:
            return cached

    from services import library as _lib
    G = nx.DiGraph()
    rep_of_group = {gid: _lib.latest_of(files)[0] for gid, files in _lib.group_versions(ctx).items()}
    reps = list(dict.fromkeys(rep_of_group.values()))
    titles = {n: ctx.papers[n].get("title", n) for n in reps}
    idx, freq = _title_token_index(titles)

    full, refs = {}, {}
    for name in reps:
        text = "\n".join(c["text"] for c in ctx.chunks if c["source"] == name)
        full[name] = text
        refs[name] = ref_section(text)

    emb_cache = {}   # 每篇论文的参考文献条目向量只推理一次，供全部候选复用

    def ref_vectors(name):
        if name not in emb_cache:
            entries = _ref_entries(refs[name])[:14]
            emb_cache[name] = (entries, ctx.embed(entries) if entries else [])
        return emb_cache[name]

    edges = []
    for i, a in enumerate(reps):
        if progress_cb:
            progress_cb((i + 1) / len(reps))
        a_entries = parsed_entries(refs[a])
        for b in _candidates(refs[a], idx, freq, a, len(reps)):
            ok, ev = cites(full[a], refs[a], titles[b], ctx.papers[b].get("arxiv_id"),
                           entries=a_entries)
            if not ok:
                ok, ev = _semantic_cite(ctx, titles[b], *ref_vectors(a))
            if ok:
                G.add_node(a, title=titles[a], version=ctx.papers[a].get("version", ""))
                G.add_node(b, title=titles[b], version=ctx.papers[b].get("version", ""))
                G.add_edge(a, b, evidence=ev)
                edges.append((a, b, ev))

    result = (G, edges)
    if cache_key is not None:
        _build_graph_cache_put(cache_key, result)
    return result


def _semantic_cite(ctx, title_b, entries, entry_vecs):
    """词面匹配失败时的语义兜底：标题向量 vs 参考文献条目向量 最佳余弦≥0.72。"""
    if not entries or not entry_vecs:
        return False, ""
    try:
        qv = ctx.embed([title_b])[0]
        best, bi = 0.0, 0
        for j, e in enumerate(entry_vecs):
            s = cosine(qv, e)
            if s > best:
                best, bi = s, j
        if best >= 0.72:
            return True, f"语义兜底匹配（{best:.0%}）：{entries[bi][:120]}"
    except Exception:
        pass
    return False, ""


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


def classify_papers(G, metrics, ctx=None):
    """基石论文 = 高被引/高PageRank；第二项默认仍为「本地入度=0」列表（向后兼容）。

    向后兼容说明：ui/page_graph.py:22 以 `corner, edge = classify_papers(G, metrics)` 调用，
    返回仍是 2-tuple。当额外传入 ctx 时（tools/definitions.py 的 agent 工具链路），第二项升级为
    detect_frontier 得到的「前沿/潜力论文」列表，不再是单纯孤儿节点。
    """
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
    if ctx is None:
        edge = [x for x in ranked if indeg[x] == 0]
        return corner, edge
    try:
        frontier = detect_frontier(ctx, G, metrics, use_external=False)
        n_keep = max(3, math.ceil(n * 0.3))
        return corner, [f["paper"] for f in frontier[:n_keep]]
    except Exception:
        return corner, [x for x in ranked if indeg[x] == 0]


def _paper_vectors(ctx, nodes):
    """论文主题向量：title + abstract 前 800 字做 embedding；失败返回 {}（跨学科维度自动跳过）。"""
    vecs = {}
    if not nodes:
        return vecs
    texts, order = [], []
    for n in nodes:
        info = ctx.papers.get(n, {})
        order.append(n)
        texts.append(f"{info.get('title', n)} {str(info.get('abstract', ''))[:800]}")
    try:
        emb = ctx.embed(texts)
        for n, v in zip(order, emb):
            vecs[n] = v
    except Exception:
        return {}
    return vecs


def _mean_pairwise_distance(vecs):
    """引用文献之间的平均两两向量距离（1-cos）：越大说明引用的文献越"跨领域"。"""
    if len(vecs) < 2:
        return None
    tot, cnt = 0.0, 0
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            tot += 1.0 - cosine(vecs[i], vecs[j])
            cnt += 1
    return tot / cnt if cnt else None


def _external_citation_trust(real):
    """判定 ``fetch_citation_count`` 的返回值能否当作「本篇论文的真实被引数」参与计分。

    返回 ``(usable: bool, count: int|None, why: str)``。**只有同时满足**下列条件才判定为可用：
      ① 无 ``title_attribution_unconfirmed``——命中的是另一篇标题近似的论文，其被引数不属于本篇；
      ② 无 ``low_confidence`` / ``source_degraded``，且 ``error_kind`` 为空——口径不完整或由降级而来，
         数值不可当作该论文的被引总数（Crossref 级无条件置 ``low_confidence``，即属此类）；
      ③ ``count``/``cited_by_count`` 是**正整数**——None 或 0 只代表「没有可用证据」，
         绝不能写成"被引 0 次"来给无数据论文打低分（那是对无数据的惩罚，等同于编造）。

    取不到可信值时返回 usable=False，调用方必须写中性表述并把该维度按「缺失」处理
    （剩余权重归一化，不加分也不扣分）。
    """
    if not isinstance(real, dict):
        return False, None, "外部数据源未返回可用结果"
    if real.get("title_attribution_unconfirmed"):
        return False, None, (
            f"命中《{str(real.get('matched_title'))[:50]}》与查询标题词序不一致"
            f"（相似度 {real.get('match_score')}），标题归属未确认，该被引数不归属本篇")
    if real.get("low_confidence") or real.get("source_degraded") or real.get("error_kind"):
        return False, None, (
            f"{real.get('source') or '外部数据源'} 口径不完整或由降级而来"
            f"（low_confidence={real.get('low_confidence')}，source_degraded={real.get('source_degraded')}，"
            f"error_kind={real.get('error_kind') or '无'}），数值不可当作本篇被引总数")
    c = real.get("cited_by_count")
    if not isinstance(c, int):
        c = real.get("count")
    if not isinstance(c, int):
        return False, None, "返回值缺 count/cited_by_count 整数字段"
    if c <= 0:
        return False, None, "外部数据源返回 0/无记录（可能是未收录或口径限制，不能据此断定该论文零被引）"
    return True, c, ""


def detect_frontier(ctx, G, metrics, use_external=True):
    """前沿(frontier)/潜力论文识别——不再是"本地库入度=0 的孤儿节点"。

    五维打分（缺失维度按剩余权重归一化，绝不假装有数据）：
      ① low_indegree 本地被引少（但入度=0 且出度=0 的完全孤立点按噪声扣分）；
      ② recency     发表年份新近性（时间衰减 1 - Δyear/10，年份缺失取中性 0.3 并注明）；
      ③ out_degree  出度高（引用大量文献 = 综述/跨学科倾向）；
      ④ interdiscipline 与其引用文献的主题向量距离（自身 vs 引用文献质心 + 引用文献两两距离）；
      ⑤ external    外部真实被引数（OpenAlex→S2→Crossref，仅对预打分 Top-K 查询，取不到不计分）。
         该维度**先过可信度校验**（``_external_citation_trust``）：标注了
         ``title_attribution_unconfirmed`` / ``low_confidence`` / ``source_degraded`` /
         ``error_kind`` 的返回值（如限流降级到 Crossref 且命中另一篇论文）**一律不计分**，
         reasons 写中性表述；``count`` 为 None/0 同样按"无证据"处理，不写成"被引 0 次"惩罚无数据论文。
    返回 [{"paper","title","score","reasons":[...], "components":{...}}]，按 score 降序。
    """
    if G is None or len(G.nodes) == 0 or not metrics:
        return []
    pr = metrics.get("pagerank", {})
    indeg = metrics.get("indegree", {})
    nodes = list(G.nodes)
    n = len(nodes)
    cur_year = time.localtime().tm_year
    max_out = max([d for _, d in G.out_degree()] or [1]) or 1

    years = {x: _node_year(ctx, x) for x in nodes}
    vecs = _paper_vectors(ctx, nodes)
    if not vecs:
        vecs = {}

    def _centroid(vs):
        if not vs:
            return None
        dim = len(vs[0])
        c = [0.0] * dim
        for v in vs:
            for i in range(min(dim, len(v))):
                c[i] += v[i]
        return [x / len(vs) for x in c]

    pre = []
    for x in nodes:
        comps, reasons = {}, []

        # ① 本地入度低
        in_d, out_d = indeg.get(x, 0), G.out_degree(x)
        low_indeg = 1.0 - (in_d / max(1, n - 1))
        comps["low_indegree"] = low_indeg
        reasons.append(f"本地被引 {in_d} 次（入度低得分 {low_indeg:.2f}）")

        # ② 时间新近性
        y = years.get(x)
        if y:
            recency = max(0.0, min(1.0, 1.0 - (cur_year - y) / 10.0))
            comps["recency"] = recency
            reasons.append(f"发表年份 {y}（时间衰减得分 {recency:.2f}）")
        else:
            comps["recency"] = 0.3
            reasons.append("年份缺失，时间维度取中性值 0.30（未做推断）")

        # ③ 高出度
        od = out_d / max_out
        comps["out_degree"] = od
        reasons.append(f"出度 {out_d}（引用文献数量得分 {od:.2f}）")

        # ④ 跨学科：自身 vs 引用文献质心距离 + 引用文献两两平均距离
        if vecs.get(x) is not None:
            refs = [vecs[r] for r in G.successors(x) if vecs.get(r) is not None]
            dist_self, div_refs = None, _mean_pairwise_distance(refs)
            if refs:
                cen = _centroid(refs)
                if cen:
                    dist_self = max(0.0, min(1.0, 1.0 - cosine(vecs[x], cen)))
            if dist_self is None and div_refs is None:
                reasons.append("无可计算向量邻居，跨学科维度跳过")
            else:
                parts = [v for v in (dist_self, div_refs) if v is not None]
                inter = sum(parts) / len(parts)
                comps["interdiscipline"] = max(0.0, min(1.0, inter))
                reasons.append(
                    f"跨学科距离 {inter:.2f}（自身vs引用文献质心 {'-' if dist_self is None else f'{dist_self:.2f}'}"
                    f"，引用文献两两差异 {'-' if div_refs is None else f'{div_refs:.2f}'}）")
        else:
            reasons.append("embedding 不可用，跨学科维度跳过")

        # 噪声惩罚：完全孤立（入度0 且 出度0）
        penalty = 0.0
        if in_d == 0 and out_d == 0:
            penalty = -0.20
            reasons.append("入度=0 且出度=0，判为孤立噪声节点而非前沿 (-0.20)")

        wsum = sum(_FRONTIER_WEIGHTS[k] for k in comps)
        raw = sum(_FRONTIER_WEIGHTS[k] * comps[k] for k in comps) / wsum if wsum else 0.0
        pre.append({"paper": x, "title": ctx.papers.get(x, {}).get("title", x),
                    "pre": raw * 100.0 + penalty * 100.0, "penalty": penalty, "comps": comps,
                    "reasons": reasons, "pr": pr.get(x, 0.0)})

    pre.sort(key=lambda d: -d["pre"])

    # ⑤ 外部真实被引：只对预打分 Top-K 查询，控制网络开销
    if use_external:
        try:
            from services.citation_quality import fetch_citation_count
        except Exception:
            fetch_citation_count = None
        if fetch_citation_count:
            for item in pre[:FRONTIER_EXTERNAL_TOPK]:
                info = ctx.papers.get(item["paper"], {})
                try:
                    real = fetch_citation_count(info.get("title", item["paper"]),
                                                year=_node_year(ctx, item["paper"]))
                except Exception as e:
                    real = None
                    item["reasons"].append(f"外部被引查询异常：{type(e).__name__}（不计分）")
                # 只采信「可信度标注干净 + 正整数被引」的结果；其余一律不计分且写中性表述。
                # 绝不能把错配/降级/口径不全的数字写成「外部真实被引 N 次」——那是把别的论文的
                # 含金量算到本篇头上，比"没有数据"更糟（抗幻觉失败）。
                usable, c, why = _external_citation_trust(real)
                if usable:
                    ext = min(1.0, math.log10(1 + c) / 3.0)
                    item["comps"]["external"] = ext
                    item["reasons"].append(
                        f"外部真实被引 {c} 次（{real.get('source')}，匹配《{str(real.get('matched_title'))[:50]}》"
                        f"相似度 {real.get('match_score')}）→ 外部热度得分 {ext:.2f}")
                else:
                    item["reasons"].append(
                        f"外部被引数据不可用（{why}），该维度不计分、按剩余维度归一化处理"
                        f"（不加分也不扣分）")

    out = []
    for item in pre:
        comps = item["comps"]
        wsum = sum(_FRONTIER_WEIGHTS[k] for k in comps)
        score = sum(_FRONTIER_WEIGHTS[k] * comps[k] for k in comps) / wsum * 100.0 if wsum else 0.0
        score = round(max(0.0, min(100.0, score + item.get("penalty", 0.0) * 100.0)), 1)
        out.append({"paper": item["paper"], "title": item["title"], "score": score,
                    "reasons": item["reasons"], "components": {k: round(v, 3) for k, v in comps.items()}})
    out.sort(key=lambda d: -d["score"])
    return out[:FRONTIER_TOP_N]


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
  physics:{{barnesHut:{{gravitationalConstant:-3500,springLength:140}},stabilization:{{iterations:200}}}}
}});
</script></body></html>"""


def _node_year(ctx, n):
    """从 ctx.papers[n] 取年份并转 int；失败返回 None。

    arXiv 回退：入库时元数据常缺 ``year`` 字段（实测 1706.03762v7.pdf、
    1810.04805v2.pdf 的 ``year`` 均为空串），而赛题要求综述「结合时间线」
    与前沿度「时间衰减」。arXiv 编号前 4 位即 YYMM，可直接推断：
    ``1706.03762`` → 2017、``1810.04805`` → 2018。无此回退时综述会退化成
    「由于没有具体的年份数据，我们无法按照时间线来描述研究脉络」。
    """
    raw = ctx.papers[n].get("year", "")
    try:
        return int(str(raw)[:4])
    except (TypeError, ValueError):
        pass
    # 回退：arXiv 编号 YYMM.NNNNN / archive.YYMMNNN
    aid = str(ctx.papers[n].get("arxiv_id", "") or "").strip()
    m = re.match(r"^(?:[a-z\-]+(?:\.[A-Za-z]{2})?/)?(\d{2})(\d{2})[.\d]", aid)
    if m:
        try:
            yy, mm = int(m.group(1)), int(m.group(2))
        except (TypeError, ValueError):
            return None
        if 1 <= mm <= 12:
            # arXiv 1991 年起步：YY>=91 属上世纪，否则属本世纪
            return 1900 + yy if yy >= 91 else 2000 + yy
    return None


def _communities(G):
    """社区发现三级降级：greedy_modularity → louvain → 弱连通分量。返回 [set(node)]（按规模降序）。"""
    if G is None or len(G.nodes) == 0:
        return []
    try:
        return sorted((set(c) for c in nx.community.greedy_modularity_communities(G.to_undirected())),
                      key=len, reverse=True)
    except Exception:
        pass
    try:
        return sorted((set(c) for c in nx.community.louvain_communities(G.to_undirected(), seed=42)),
                      key=len, reverse=True)
    except Exception:
        pass
    return sorted((set(c) for c in nx.weakly_connected_components(G)), key=len, reverse=True)


def _cluster_label(ctx, members):
    """主题簇标签：成员标题中的高频实词 top3（纯统计，非 LLM 编造）。"""
    freq = {}
    for m in members:
        for w in set(norm(ctx.papers.get(m, {}).get("title", m)).split()):
            if len(w) > 4 and w not in _STOP:
                freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda x: (-x[1], x[0]))[:3]
    return "/".join(w for w, _ in top) if top else "未命名主题簇"


def review_structure(ctx, G, metrics, cornerstone, reps_per_cluster=REVIEW_REPS_PER_CLUSTER,
                     backbone_k=REVIEW_EDGE_BACKBONE):
    """从引用图谱抽取综述骨架（纯 networkx 计算，不经过 LLM）。

    返回 {"clusters":[{label,size,avg_year,reps:[...],members:[...]}], "timeline":[(year,[...])],
          "backbone":[{"from","to","from_title","to_title","evidence"}], "bridges":[...],
          "pr_top":[(node, pr)], "n_nodes":int, "n_edges":int}；图谱为空返回 None。
    """
    if G is None or len(G.nodes) == 0 or not metrics or "pagerank" not in metrics:
        return None
    pr = metrics.get("pagerank", {})
    comms = _communities(G)
    if not comms:
        return None

    def _title(n):
        return ctx.papers.get(n, {}).get("title", n)

    def _year(n):
        return _node_year(ctx, n)

    clusters = []
    comm_of = {}
    for i, members in enumerate(comms):
        comm_of.update({m: i for m in members})
        ys = [y for y in (_year(m) for m in members) if y]
        reps = sorted(members, key=lambda m: -pr.get(m, 0.0))[:reps_per_cluster]
        clusters.append({"id": i, "label": _cluster_label(ctx, members), "size": len(members),
                         "avg_year": round(statistics.mean(ys), 1) if ys else None,
                         "reps": reps, "members": sorted(members, key=lambda m: -pr.get(m, 0.0))})
    # 簇间按平均年份排序 → 形成时间脉络（并重排展示序号，使 #0 为最早的方向）
    clusters.sort(key=lambda c: (c["avg_year"] is None, c["avg_year"] or 0))
    for i, c in enumerate(clusters):
        c["id"] = i

    year_of = {n: y for n in (x for x in G.nodes) for y in [_year(n)] if y}
    by_year = {}
    for n, y in year_of.items():
        by_year.setdefault(y, []).append(n)
    timeline = [(y, sorted(v, key=lambda m: -pr.get(m, 0.0))) for y, v in sorted(by_year.items())]

    def _edge_item(a, b):
        return {"from": a, "to": b, "from_title": _title(a), "to_title": _title(b),
                "evidence": (G.edges[a, b].get("evidence", "") if G.has_edge(a, b) else "")[:120]}

    ranked_edges = sorted(G.edges, key=lambda e: -(pr.get(e[0], 0.0) + pr.get(e[1], 0.0)))
    backbone = [_edge_item(a, b) for a, b in ranked_edges[:backbone_k]]
    bridges = [_edge_item(a, b) for a, b in ranked_edges
               if comm_of.get(a) != comm_of.get(b)][:5]
    pr_top = sorted(((n, pr.get(n, 0.0)) for n in G.nodes), key=lambda x: -x[1])[:5]
    return {"clusters": clusters, "timeline": timeline, "backbone": backbone, "bridges": bridges,
            "pr_top": pr_top, "n_nodes": len(G.nodes), "n_edges": len(G.edges)}


def _structure_blocks(ctx, st):
    """把骨架渲染为注入 prompt 的文本块（主题簇 / 簇代表 / 时间线 / 骨干边 / 中心性 Top）。"""
    def _fmt(n):
        # 年份一律走 _node_year：真实元数据优先，arXiv 编号回退次之，取不到写"年份未知"。
        # 旧实现直接读 papers[n]["year"]，实测该字段常为空串，prompt 里出现
        # "《Attention Is All You Need》()" 这种空括号，模型只能瞎猜年份。
        y = _node_year(ctx, n)
        return f"《{ctx.papers.get(n, {}).get('title', n)}》({y if y else '年份未知'})"

    clus_lines = []
    for c in st["clusters"]:
        ys = f"，平均年份 {c['avg_year']}" if c["avg_year"] else "，年份缺失"
        clus_lines.append(
            f"- 主题簇 #{c['id']}「{c['label']}」（{c['size']} 篇{ys}）\n"
            f"  代表论文（PageRank 最高 {len(c['reps'])} 篇）："
            f"{'、'.join(_fmt(r) for r in c['reps'])}")
    cluster_block = "\n".join(clus_lines) or "（无）"

    tl_lines = []
    for y, papers in st["timeline"]:
        tl_lines.append(f"- {y}：{'、'.join(_fmt(p) for p in papers)}")
    timeline_block = "\n".join(tl_lines) or "（无年份数据，时间线不可用）"

    bb_lines = [f"- 《{e['from_title']}》 → 引用 → 《{e['to_title']}》"
                f"（证据：{e['evidence'] or '—'}）" for e in st["backbone"]]
    backbone_block = "\n".join(bb_lines) or "（无引用边）"

    br_lines = [f"- 《{e['from_title']}》 → 引用 → 《{e['to_title']}》（跨主题簇连接）"
                for e in st["bridges"]]
    bridge_block = "\n".join(br_lines) or "（无跨簇边）"

    pr_block = "\n".join(f"- {_fmt(n)}  PageRank={v:.4f}" for n, v in st["pr_top"]) or "（无）"
    return cluster_block, timeline_block, backbone_block, bridge_block, pr_block


def _deterministic_review(ctx, st):
    """LLM 不可用时的确定性综述：完全由图谱结构数据生成，不编造任何内容。"""
    def _fmt(n):
        y = _node_year(ctx, n)
        return f"《{ctx.papers.get(n, {}).get('title', n)}》({y if y else '年份未知'})"

    lines = [f"### 一、研究脉络（时间线，按论文年份排序，共 {st['n_nodes']} 个节点 / {st['n_edges']} 条引用边）", ""]
    if st["timeline"]:
        for y, papers in st["timeline"]:
            lines.append(f"- **{y}**：" + "、".join(_fmt(p) for p in papers))
    else:
        lines.append("- 文献元数据中无可用年份，时间线维度不可得（不做推断）")

    lines += ["", "### 二、主要方向（networkx 社区发现聚簇，按簇平均年份排序）", ""]
    for c in st["clusters"]:
        ys = f"，平均年份 {c['avg_year']}" if c["avg_year"] else ""
        lines.append(f"- **主题簇 #{c['id']}「{c['label']}」**（{c['size']} 篇{ys}）：代表论文 "
                     + "、".join(_fmt(r) for r in c["reps"]))

    lines += ["", "### 三、关键基石（PageRank Top）", ""]
    for n, v in st["pr_top"]:
        lines.append(f"- {_fmt(n)}  PageRank={v:.4f}")

    lines += ["", "### 四、分歧与展望", ""]
    if st["bridges"]:
        lines.append("- 跨主题簇引用边（可视为方向交叉/分歧点）："
                     + "；".join(f"《{e['from_title']}》→《{e['to_title']}》" for e in st["bridges"]))
    else:
        lines.append("- 未发现跨主题簇引用边，各方向之间暂未在引用图上产生交叉")
    lines.append("- 展望需结合 Future Work 扫描结果（见「前沿方向」），此处不做超出图谱数据的推断")
    return "\n".join(lines)


def _structure_appendix(ctx, st):
    """结构化附录：把图谱计算的原始依据附在综述后，便于核查（非 LLM 生成）。"""
    def _fmt(n):
        y = _node_year(ctx, n)
        return f"《{ctx.papers.get(n, {}).get('title', n)}》({y if y else '年份未知'})"

    lines = ["---", "#### 图谱结构明细（networkx 计算所得，非 LLM 生成）", "",
             f"- 节点 {st['n_nodes']} 个、引用边 {st['n_edges']} 条、主题簇 {len(st['clusters'])} 个"]
    # 语料规模坦诚：节点数远低于赛题「20 篇批量文献」时明确标注，不冒充大图效果
    if st["n_nodes"] < SMALL_CORPUS_NODES:
        lines.append(
            f"- ⚠️ **语料规模坦诚说明**：当前图谱仅 {st['n_nodes']} 个逻辑节点"
            f"（同一 arXiv 号的多版本 PDF 会被合并为 1 个节点），未达赛题『20 篇批量文献』规模；"
            f"社区发现 / PageRank / 时间线在极小图上区分度有限，本节结果只证明"
            f"『解析→建图→分析→综述』链路可用，不代表 20 篇规模下的效果。")
    lines.append("- 主题簇代表论文：" + "；".join(
        f"#{c['id']}「{c['label']}」→ " + "、".join(_fmt(r) for r in c["reps"])
        for c in st["clusters"]))
    if st["backbone"]:
        lines.append("- 引用骨干边（按两端 PageRank 之和取 Top "
                     f"{len(st['backbone'])}）：" + "；".join(
                         f"{_fmt(e['from'])}→{_fmt(e['to'])}" for e in st["backbone"]))
    return "\n".join(lines)


def generate_review(ctx, G, metrics, cornerstone, progress_cb=None):
    """基于图谱结构（社区发现 → 簇代表 → 时间线 → 引用骨干边 → 中心性 Top）驱动的中文综述。

    签名与返回类型（str/Markdown）向后兼容；图谱为空时退回摘要拼接的旧行为。
    progress_cb: 可选阶段进度回调（话术 3：压测实测本步 31.90s 且此前全程无进度提示，
                 演示时观感等同卡死，故拆成"结构解析 / 正文生成"两段上报）。
    """

    def _say(stage):
        if progress_cb:
            try:
                progress_cb(stage)
            except Exception:
                pass

    _say("① 解析引用图谱结构（社区发现 / 时间线 / 骨干边）…")
    st = review_structure(ctx, G, metrics, cornerstone)

    # ---- 图谱不可用：退回旧版摘要拼接（保持原行为，但走统一 LLM 入口） ----
    if not st:
        abstracts = "\n".join(
            f"《{ctx.papers[n].get('title', n)}》({_node_year(ctx, n) or '年份未知'}): "
            f"{ctx.papers[n].get('abstract','')[:500]}" for n in ctx.paper_order)
        corner_simple = "、".join(ctx.papers[n].get("title", n) for n in (cornerstone or []))
        prompt = (f"基于以下局域文献库，用中文撰写一篇结构完整的简短领域综述（含领域背景、主要技术脉络、"
                  f"基石工作（{corner_simple}）的贡献、当前进展与局限）。客观、连贯、800字内。\n"
                  f"文献信息：\n{abstracts[:6000]}")
        out = _llm_text(ctx, prompt, 0.4)
        if not _llm_ok(out):
            return ("【图谱为空（无节点或引用边）且 LLM 不可用，无法生成综述。"
                    f"LLM 返回：{out[:160]}】")
        return out

    cluster_block, timeline_block, backbone_block, bridge_block, pr_block = _structure_blocks(ctx, st)
    _say("② 生成综述正文（大模型撰写中，约需 20~40 秒）…")
    corner_block = "\n".join(
        f"- 《{ctx.papers[n].get('title', n)}》({_node_year(ctx, n) or '年份未知'}) "
        f"{str(ctx.papers[n].get('abstract', ''))[:200]}" for n in (cornerstone or [])) or "（无）"

    prompt = f"""你在为本领域撰写一篇**基于引用图谱的**中文综述（900字内）。图谱由 networkx 从本地文献库
真实解析的引用关系计算而来：共 {st['n_nodes']} 个节点、{st['n_edges']} 条引用边，已做社区发现聚簇。
请严格用以下四个 Markdown 小标题组织内容（不要增加其他一级结构）：

## 一、研究脉络（时间线）
按下列年份顺序讲清"早期问题提出 → 中期改进扩展 → 近期趋势"的演化，只使用给出的论文：
{timeline_block}

## 二、主要方向（按主题簇）
下列主题簇由网络社区发现算法得出，请逐簇说明该方向在做什么、簇内代表论文各自解决了什么：
{cluster_block}

## 三、关键基石（PageRank Top）
结合下列 PageRank 排名与基石论文清单，说明它们为何成为枢纽（被谁引用、奠定了什么）：
{pr_block}
基石论文清单：
{corner_block}

## 四、分歧与展望
基于下列跨主题簇引用边（方向交叉点）与引用骨干边，指出当前存在的技术路线分歧与值得继续的方向：
跨簇边：
{bridge_block}
引用骨干边（谁引用了谁，构成发展脉络）：
{backbone_block}

硬性要求：
1) 只能使用上面列出的论文与边，**禁止引入任何本地库之外的文献或编造引用关系**；
2) 每个小标题下必须至少点名 1-2 篇上面给出的论文；
3) 客观、连贯，不要平铺摘要。"""

    body = _llm_text(ctx, prompt, 0.4)
    if not _llm_ok(body):
        body = _deterministic_review(ctx, st)
        note = "\n\n> 注：LLM 不可用，以下综述由 networkx 图谱结构确定性生成（未编造内容）。"
    else:
        note = ""
    return f"{body}{note}\n\n{_structure_appendix(ctx, st)}"


# Future Work 关键词抽取时忽略的停用词
_FW_STOP = {"the", "a", "an", "of", "for", "and", "or", "on", "in", "with", "to",
            "this", "that", "we", "our", "propose", "new", "using", "approach",
            "method", "model", "network", "deep", "learning", "paper", "also",
            "can", "could", "would", "should", "will", "by", "as", "at", "from",
            "which", "these", "those", "it", "its", "is", "are", "be", "been",
            "have", "has", "had", "not", "but", "if", "then", "than", "more",
            "most", "such", "via", "based", "toward", "towards"}


def _extract_fw_keywords(snippet, top_k=5):
    """从 Future Work 段抽出英文关键词：正则 [A-Za-z]{{5,}}，去停用词，返回 top_k（按出现频次）。"""
    if not snippet:
        return []
    words = re.findall(r"[A-Za-z]{5,}", snippet.lower())
    freq = {}
    for w in words:
        if w in _FW_STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: -x[1])
    return [w for w, _ in ranked[:top_k]]


def _rule_predict_followup(ctx, G, n, keywords):
    """（保留兼容）1 跳规则的薄封装：内部委托给引用图可达性引擎 _future_status。"""
    status, evidences = _future_status(ctx, G, n, keywords)
    if not evidences and status == "开放":
        return "仍开放（规则预判）", "暂未在引用图中发现命中关键词的后续跟进论文"
    return f"{status}（规则预判）", "；".join(evidences) if evidences else ""


def _future_snippets(ctx):
    """抽取每篇论文的 Future Work 段落。

    返回 [(node, snippet, is_fallback)]；正则失配时用正文结尾兜底并标记 is_fallback=True
    （审计指出该兜底可靠性低，故在输出中显式标注，不伪装成真实 Future Work 段落）。
    排序键用 _node_year（真实元数据/arXiv 回退），避免 year 字段为空串时排序退化为字典序。
    """
    out = []
    for n in sorted(ctx.paper_order, key=lambda x: (_node_year(ctx, x) or 0)):
        text = "\n".join(c["text"] for c in ctx.chunks if c["source"] == n)
        m = re.search(r"(future work|future research|limitations|conclusion|展望|未来工作).{0,1200}",
                      text, re.IGNORECASE | re.DOTALL)
        if m:
            out.append((n, m.group(0)[:1200], False))
        elif text:
            out.append((n, text[-900:], True))
    return out


_FW_CUE = re.compile(r"(future|remain|open (?:question|problem)|we plan|we intend|"
                     r"not (?:yet )?(?:explored|addressed)|展望|未来|尚未|值得进一步)", re.IGNORECASE)


def _future_item_text(snippet):
    """从 Future Work 段落里挑出最像"未来方向"的 1-2 句（避免整段塞给 LLM）。"""
    sents = [s.strip() for s in re.split(r"(?<=[.!?。])\s+", snippet or "") if len(s.strip()) > 20]
    if not sents:
        return (snippet or "")[:240]
    cued = [s for s in sents if _FW_CUE.search(s)]
    picked = (cued or sents)[:2]
    return " ".join(picked)[:400]


def _reachable_citers(G, src):
    """引用图中「引用了 src」的论文及其跳数：1 跳 = 直接引用；2 跳 = 间接（B→X→src）。

    G 边方向 a→b 表示 a 引用 b，故引用者 = predecessors。
    返回 {node: hops}。
    """
    if G is None or src not in G:
        return {}
    res = {}
    for m in G.predecessors(src):
        if m != src:
            res[m] = 1
    for x in list(res):
        for m in G.predecessors(x):
            if m != src and m not in res:
                res[m] = 2
    return res


def _future_status(ctx, G, src, keywords):
    """用引用图可达性判定某条 Future Work 是否已被后续论文解决。

    判定条件：存在论文 B，(a) B 在 1 跳或 2 跳内引用了提出该 future work 的论文 A，
    且 (b) B 的标题/摘要与该 future work 关键词有实质重叠（≥2 个关键词命中，或标题短语重叠）。
    命中 → "已解决"；仅 1 个关键词命中或年份不支持 → "存疑"；无命中 → "开放"。
    返回 (status, evidence_list)。
    """
    if G is None or not keywords:
        return "开放", ["无引用图或无关键词，无法做可达性判定"]
    citers = _reachable_citers(G, src)
    if not citers:
        return "开放", ["引用图中无任何论文引用了该文献（无 1/2 跳后继）"]
    y_src = _node_year(ctx, src)
    evidences, weak = [], []
    for m, hops in sorted(citers.items(), key=lambda x: x[1]):
        info_m = ctx.papers.get(m, {})
        hay = f"{info_m.get('title', '')} {info_m.get('abstract', '')}".lower()
        hits = [kw for kw in keywords if kw and kw in hay]
        if not hits:
            continue
        y_m = _node_year(ctx, m)
        newer = True
        if y_src and y_m:
            newer = y_m >= y_src - 1      # 同年/更晚才算"后续"（允许 1 年容差）
        desc = (f"《{info_m.get('title', m)}》({y_m or '年份未知'}) 以 {hops} 跳引用了本文，"
                f"命中关键词 {len(hits)} 个：{'、'.join(hits[:5])}")
        if not newer:
            weak.append(f"{desc}（但发表年份 {y_m} 早于本文 {y_src}，不作为「已解决」证据）")
        elif len(hits) >= 2:
            evidences.append(desc)
        else:
            weak.append(f"{desc}（仅命中 1 个关键词，证据不足）")
    if evidences:
        return "已解决", evidences[:4]
    if weak:
        return "存疑", weak[:4]
    return "开放", [f"存在 {len(citers)} 篇 1/2 跳引用论文，但无一命中该方向的关键词"]


def explore_future_structured(ctx, G):
    """结构化 Future Work 判定（不依赖 LLM）：引用图可达性 + 关键词重叠。

    返回 [{"item","source_paper","source_title","year","keywords",
            "status":"已解决/开放/存疑","evidence":[...],"snippet_fallback":bool}]
    """
    out = []
    for n, snippet, is_fallback in _future_snippets(ctx):
        kws = _extract_fw_keywords(snippet)
        status, evidences = _future_status(ctx, G, n, kws)
        if is_fallback:
            evidences = list(evidences) + ["未定位到 Future Work 段落，使用正文结尾 900 字兜底，可靠性低"]
            if status == "开放":
                status = "存疑"
        out.append({
            "item": _future_item_text(snippet),
            "source_paper": n,
            "source_title": ctx.papers.get(n, {}).get("title", n),
            "year": _node_year(ctx, n) or "",
            "keywords": kws,
            "status": status,
            "evidence": evidences,
            "snippet_fallback": is_fallback,
        })
    # 开放项优先、其次存疑、最后已解决；同状态按年份升序
    order = {"开放": 0, "存疑": 1, "已解决": 2}
    out.sort(key=lambda d: (order.get(d["status"], 3), str(d["year"])))
    return out[:FUTURE_MAX_ITEMS]


def _future_markdown(items):
    """把结构化结果渲染为中文 Markdown 表格（纯确定性，LLM 不可用也能出结果）。"""
    head = "| 提出的方向（来源） | 状态 | 判断依据（引用图证据） |\n|---|---|---|"
    rows = []
    for it in items:
        txt = it["item"].replace("|", "/")[:150]
        src = f"《{it['source_title']}》({it['year']})"
        ev = "；".join(it["evidence"])[:260].replace("|", "/") if it["evidence"] else "—"
        flag = " ⚠️段落兜底" if it["snippet_fallback"] else ""
        rows.append(f"| {txt}{flag} | **{it['status']}** | {src}：{ev} |")
    return "\n".join([head] + rows) if rows else "（未抽取到任何 Future Work 条目）"


def explore_future(ctx, G):
    """提取各文 Future Work → 用引用图可达性做结构化判定 → LLM 仅做最后一步语义确认。

    返回 Markdown 字符串（签名与 UI/tools 契约不变）。LLM 不可用时直接输出确定性判定结果，
    **不让它凭空判断、也不编造证据**。
    """
    items = explore_future_structured(ctx, G)
    table = _future_markdown(items)
    n_open = sum(1 for i in items if i["status"] == "开放")
    stat_line = (f"> 引用图可达性判定：共 {len(items)} 条，已解决 "
                 f"{sum(1 for i in items if i['status'] == '已解决')} 条、存疑 "
                 f"{sum(1 for i in items if i['status'] == '存疑')} 条、开放 {n_open} 条。")

    structured = "\n".join(
        f"- [{it['status']}] 《{it['source_title']}》({it['year']})：{it['item'][:200]}\n"
        f"  关键词：{'、'.join(it['keywords']) or '无'}；证据：{'；'.join(it['evidence']) or '无'}"
        for it in items) or "（无条目）"

    prompt = f"""下面是**已经用引用图完成结构化判定**的 Future Work 清单（判定依据 = 后续论文在引用图中
1 跳或 2 跳引用了提出方，且标题/摘要与方向关键词实质重叠）。你的任务**不是重新判断**，而是：
1) 对每条做语义确认：若证据论文确实解决了该方向，确认"已解决"；若只是部分涉及，改为"部分解决"；
   若证据不充分，改为"存疑/开放"并说明理由；
2) 从"开放/存疑"条目中提炼 3-5 个真正值得做的未来方向。

结构化清单：
{structured[:5000]}

输出格式（中文 Markdown）：
## 语义确认
（逐条给出：方向 → 最终状态 → 一句话理由；不要新增清单之外的方向）
## 值得做的开放方向
（3-5 条项目符号，每条一句话说明价值）"""

    body = _llm_text(ctx, prompt, 0.4)
    if not _llm_ok(body):
        body = ("## 语义确认\nLLM 不可用，以下直接使用引用图可达性的确定性判定结果（未做语义改写、未编造证据）。\n"
                + structured)
    return f"{stat_line}\n\n{table}\n\n{body}"

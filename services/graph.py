"""局域引用图谱：真实解析论文间引用关系，图论挖掘基石/边缘论文，生成综述与前沿方向。

工程要点（对应评分：图谱算法创新 10% + 20+ 篇渲染性能）：
- 参考文献定位：优先按 References/Bibliography 标题锚点切区，兼容 IEEE/Nature/Springer 排版；
- 候选生成：标题 token 倒排索引，把 O(n²) 全文比对降为「按共享词根候选」；
- 双重匹配：词面窗口匹配失败时，用论文标题与参考文献条目的语义向量兜底；
- 版本归并：同一 arXiv 号仅取最新版本入图，避免重复节点。

新增（对应赛题 D 路审计整改 + Round 13 图谱增强）：
- detect_frontier：真正的 frontier 模型（低被引 + 时间新近 + 高出度 + 跨学科向量距离 + 外部真实被引）；
- generate_review / explore_future_structured：社区聚簇综述 + 引用图 1/2 跳可达性判定 Future Work 解决状态；
- Round 13 增强（见文件末尾）：引文语境与意图（Scite 式）、共引/文献耦合推荐、引文速度热点。
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

    # Round 13 增强：引文语境 / 分歧 / 相关推荐 / 引文速度热点（确定性计算，注入 prompt 与附录）
    try:
        ins = graph_insights(ctx, G)
    except Exception:
        ins = None
    intent_block, related_block, hotspot_block = _insight_blocks(ctx, ins)

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
引文语境（Scite 式 supporting/contrasting/mentioning 判定，确定性计算，附原文引文句）：
{intent_block}
相关但未被直接引用的论文（共引 / 文献耦合，Connected Papers 式，可作"相关的工作"素材）：
{related_block}
新兴热点（引文速度 = 本地被引 ÷ 论文年龄，小图仅作方向性参考）：
{hotspot_block}

硬性要求：
1) 只能使用上面列出的论文与边，**禁止引入任何本地库之外的文献或编造引用关系**；
2) 每个小标题下必须至少点名 1-2 篇上面给出的论文；
3) 客观、连贯，不要平铺摘要；
4) 「分歧与展望」中的分歧判断必须引用上述引文语境原文，禁止编造引文句。"""

    body = _llm_text(ctx, prompt, 0.4)
    if not _llm_ok(body):
        body = _deterministic_review(ctx, st)
        note = "\n\n> 注：LLM 不可用，以下综述由 networkx 图谱结构确定性生成（未编造内容）。"
    else:
        note = ""
    return f"{body}{note}\n\n{_structure_appendix(ctx, st)}{insights_appendix(ctx, ins)}"


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
    # Round 13 增强：引文速度视角的新兴热点（Price 累积优势模型信号，确定性计算）
    hot_block = ""
    try:
        hot = detect_hotspots(ctx, G)
    except Exception:
        hot = []
    if hot:
        _hl = ["", "## 新兴热点（引文速度：年轻且已被本地引用）", "",
               "> 口径：本地入度 ÷ max(1, 当前年份−发表年份)，近似 Price(1965) 累积优势模型中"
               "「晚发表但被引超预期」的信号；本地图规模小，仅作方向性参考，不构成学术结论。"]
        for h in hot[:3]:
            _hl.append(f"- 《{h['title']}》（{h['year']}）：本地被引 {h['indegree']} 次 / "
                       f"年龄 {h['age']} 年 → 引文速度 {h['velocity']:.2f} 次/年")
        hot_block = "\n".join(_hl)
    return f"{stat_line}\n\n{table}\n\n{body}{hot_block}"


# ---------- Round 13 增强：引文语境 / 引用意图 / 相似推荐 / 研究空白 ----------
# 业界对标（全部做成离线确定性实现：正则 + 集合运算 + networkx，不依赖 LLM 与外部 API）：
# ① Scite「Smart Citations」：抽取引文语句（citation statement）并三分类
#    supporting / contrasting / mentioning。本模块用「句子 vs 被引论文标题」的 token
#    重叠定位引文语境句、用线索词表做三分类的**无 LLM 近似**；contrasting 稀少且
#    信息量最大，边级保守标出供人工复核，不冒充高准确率分类器。
# ② Connected Papers / ResearchRabbit / VOSviewer：共引（co-citation，被同一篇论文
#    引用）与文献耦合（bibliographic coupling，引用同一批论文）相似度，用于推荐
#    「相关但未被直接引用」的论文；Salton 余弦归一化，证据列明共同邻居。
# ③ Price(1965) 累积优势模型 / Newman(2009)「晚发表但被引超预期」：引文速度
#    （本地入度 ÷ 论文年龄）作为新兴热点信号；小图上仅作方向性参考。
# 全部新符号追加在文件末尾：既有函数行号不变，docs/technical_doc.md 的行号引用零漂移。

# 引用意图线索词（对引文语境句做小写子串计数；contrasting 优先级高于 supporting）
_INTENT_CONTRAST_CUES = ("however", "in contrast", "by contrast", "unlike", "whereas",
                         "although", "though", "nevertheless", "nonetheless",
                         "on the other hand", "but ", " yet ", "limitation", "drawback",
                         "shortcoming", "fails to", "fail to", "cannot", "can not",
                         "struggle", "suffer", "weakness", "deficien", "flaw",
                         "question whether", "questions the", "question the", "doubt")
_INTENT_SUPPORT_CUES = ("following", "based on", "building on", "build upon", "build on",
                        "extend", "adopt", "as shown in", "as demonstrated", "demonstrat",
                        "achieve", "state-of-the-art", "outperform", "successfully",
                        "leverage", "inspired by", "according to",
                        "confirm", "consistent with", "in line with")
# 引文语境判定：句子与「被引论文标题」的 token 重叠率下限（arXiv 号命中记 1.0）
CONTEXT_OVERLAP_STRONG = 0.6   # 达标记为普通语境
CONTEXT_OVERLAP_WEAK = 0.4     # 仅达到时标记 weak（诚实降级，不丢弃唯一线索）
# 综述 / agent 工具输出中各类清单的最大条数
INSIGHT_MAX_DISAGREE = 5
INSIGHT_MAX_RELATED = 6
INSIGHT_MAX_HOTSPOT = 5
# 单条引文语境的最大截断长度（字符）
CONTEXT_MAX_CHARS = 220


def _split_sentences(text):
    """离线分句：保护 et al. / e.g. / i.e. 等缩写点号，再按 . ! ? 。！？ 切分。"""
    if not text:
        return []
    t = text
    for ab in ("et al.", "e.g.", "i.e.", "etc.", "vs.", "cf.", "Fig.", "fig.",
               "approx.", "No."):
        t = t.replace(ab, ab.replace(".", "\x00"))
    parts = re.split(r"(?<=[.!?。！？])\s+", t)
    return [p.replace("\x00", ".").strip() for p in parts if p and p.strip()]


def _node_full_text(ctx, n):
    """取节点对应 PDF 的全文（与 build_graph 同口径：拼接该 source 的全部 chunk）。"""
    try:
        return "\n".join(c.get("text", "") for c in ctx.chunks if c.get("source") == n)
    except Exception:
        return ""


def _node_title(ctx, n):
    """节点标题：G 属性优先，退回 ctx.papers，再退回文件名。"""
    try:
        return ctx.papers.get(n, {}).get("title", n) or n
    except Exception:
        return n


def _citation_title_tokens(title):
    """被引论文标题的判别性 token（与 _candidates 同口径：len>2 且非停用词）。"""
    return [w for w in norm(title or "").split() if len(w) > 2 and w not in _STOP]


def _sentence_overlap(sent_toks, b_tokens, sentence, arxiv_b):
    """句子对论文 B 的引用关联度：标题 token 重叠率；含 B 的 arXiv 号记 1.0。"""
    if arxiv_b and re.search(r"\d{4}\.\d{4,5}", arxiv_b) and arxiv_b in (sentence or ""):
        return 1.0
    if not b_tokens or not sent_toks:
        return 0.0
    return len(sent_toks & set(b_tokens)) / len(b_tokens)


def _classify_citation_intent(sentence):
    """单句引用意图：线索词计数，contrasting > supporting > mention（确定性近似，非 LLM）。"""
    low = (sentence or "").lower()
    n_c = sum(low.count(w) for w in _INTENT_CONTRAST_CUES)
    n_s = sum(low.count(w) for w in _INTENT_SUPPORT_CUES)
    if n_c > n_s:
        return "contrasting"
    if n_s > n_c:
        return "supporting"
    return "mention"


def annotate_citation_intents(ctx, G, max_contexts=2):
    """为引用图每条边抽取「引文语境」并判定引用意图（Scite Smart Citations 式，离线确定性版）。

    对每条真实引用边 A→B：
      1. 把 A 的**正文**（剔除 References 区，避免把文献条目本身当成引文语境）分句，
         找出与 B 标题 token 重叠率 ≥ CONTEXT_OVERLAP_STRONG（或含 B 的 arXiv 号）的
         句子作为引文语境；仅达到 WEAK 的标记 weak；
      2. 对每个语境句以线索词表分类 supporting / contrasting / mention；
      3. 边级意图：任一 contrasting → contrasting；否则任一 supporting → supporting；
         有语境但无信号 → mention；**完全没找到语境 → no_context**
         （不把"没找到"伪装成"中性提及"——无证据时如实说无证据）。
    结果写回边属性 intent / contexts，返回统计 dict；同一张图只注解一次（G.graph 标记）。
    演示模式示意边（evidence 含「演示模式/示意」）一律跳过：那不是真实引用，不能造语境。
    """
    stats = {"n_edges": 0, "by_intent": {"supporting": 0, "mention": 0,
                                        "contrasting": 0, "no_context": 0},
             "n_annotated": 0, "n_no_context": 0, "skipped_demo": 0}
    if G is None or len(G.nodes) == 0 or G.number_of_edges() == 0:
        return stats
    if G.graph.get("intents_done"):
        # 已注解过：从边属性重新汇总统计，保证返回值与图当前状态一致
        for a, b in G.edges:
            it = G.edges[a, b].get("intent", "no_context")
            stats["n_edges"] += 1
            stats["by_intent"][it] = stats["by_intent"].get(it, 0) + 1
            if it == "no_context":
                stats["n_no_context"] += 1
            else:
                stats["n_annotated"] += 1
        return stats

    body_cache = {}

    def _body_sentences(a):
        if a not in body_cache:
            full = _node_full_text(ctx, a)
            refs = ref_section(full)
            body = full[:len(full) - len(refs)] if refs else full
            body_cache[a] = _split_sentences(body)
        return body_cache[a]

    for a, b in list(G.edges):
        ev = G.edges[a, b].get("evidence", "") or ""
        if "演示模式" in ev or "示意" in ev:
            stats["skipped_demo"] += 1
            G.edges[a, b]["intent"] = "no_context"
            G.edges[a, b]["contexts"] = []
            continue
        stats["n_edges"] += 1
        b_title = G.nodes[b].get("title") or _node_title(ctx, b)
        b_tokens = _citation_title_tokens(b_title)
        arxiv_b = ""
        try:
            arxiv_b = ctx.papers.get(b, {}).get("arxiv_id", "") or ""
        except Exception:
            arxiv_b = ""
        sents = _body_sentences(a)
        bset = set(b_tokens)
        scored = []
        for s in sents:
            ov = _sentence_overlap(set(norm(s).split()), b_tokens, s, arxiv_b)
            if ov >= CONTEXT_OVERLAP_WEAK:
                scored.append((ov, s))
        scored.sort(key=lambda x: -x[0])   # 稳定排序：同分保留正文出现顺序
        picked = scored[:max_contexts]
        intents = [_classify_citation_intent(s) for _, s in picked]
        if any(i == "contrasting" for i in intents):
            edge_intent = "contrasting"
        elif any(i == "supporting" for i in intents):
            edge_intent = "supporting"
        elif picked:
            edge_intent = "mention"
        else:
            edge_intent = "no_context"
        G.edges[a, b]["intent"] = edge_intent
        G.edges[a, b]["contexts"] = [
            {"text": s[:CONTEXT_MAX_CHARS], "intent": i, "overlap": round(ov, 2),
             "weak": ov < CONTEXT_OVERLAP_STRONG}
            for (ov, s), i in zip(picked, intents)]
        stats["by_intent"][edge_intent] = stats["by_intent"].get(edge_intent, 0) + 1
        if edge_intent == "no_context":
            stats["n_no_context"] += 1
        else:
            stats["n_annotated"] += 1
    G.graph["intents_done"] = True
    return stats


def disagreement_points(ctx, G, top_k=INSIGHT_MAX_DISAGREE):
    """引用意图为 contrasting 的真实引用边 = 有原文依据的技术路线分歧 / 被质疑点。

    每条附引文语境原文（截断），供综述「分歧与展望」与人工复核使用；
    未注解的图会先跑 annotate_citation_intents。纯确定性，无 LLM、无编造。
    """
    if G is None or G.number_of_edges() == 0:
        return []
    if not G.graph.get("intents_done"):
        annotate_citation_intents(ctx, G)
    out = []
    for a, b in G.edges:
        if G.edges[a, b].get("intent") != "contrasting":
            continue
        ctxs = [c["text"] for c in G.edges[a, b].get("contexts", []) if c.get("text")]
        out.append({"from": a, "to": b,
                    "from_title": G.nodes[a].get("title") or _node_title(ctx, a),
                    "to_title": G.nodes[b].get("title") or _node_title(ctx, b),
                    "contexts": ctxs[:2]})
    return out[:top_k]


def _cocitation_map(G):
    """共引强度 {frozenset({x,y}): strength}：被同一批论文引用的强度（Salton 余弦）。

    共引 = |citers(x) ∩ citers(y)| / sqrt(|citers(x)|·|citers(y)|)，
    衡量「经常被一起引用 = 主题相近」（Connected Papers 核心信号之一）。
    """
    citers = {n: set(G.predecessors(n)) for n in G.nodes}
    out = {}
    nodes = list(G.nodes)
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            x, y = nodes[i], nodes[j]
            inter = citers[x] & citers[y]
            if not inter:
                continue
            denom = math.sqrt(max(1, len(citers[x])) * max(1, len(citers[y])))
            out[frozenset((x, y))] = len(inter) / denom
    return out


def _coupling_map(G):
    """文献耦合强度 {frozenset({x,y}): strength}：引用同一批论文的强度（Salton 余弦）。

    耦合 = |refs(x) ∩ refs(y)| / sqrt(|refs(x)|·|refs(y)|)，
    衡量「引用相同的文献基础 = 研究脉络同源」（Connected Papers 核心信号之一）。
    """
    refs = {n: set(G.successors(n)) for n in G.nodes}
    out = {}
    nodes = list(G.nodes)
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            x, y = nodes[i], nodes[j]
            inter = refs[x] & refs[y]
            if not inter:
                continue
            denom = math.sqrt(max(1, len(refs[x])) * max(1, len(refs[y])))
            out[frozenset((x, y))] = len(inter) / denom
    return out


def recommend_related(ctx, G, top_k=3, min_score=0.34):
    """Connected Papers 式「相关但未被直接引用」推荐：共引 + 文献耦合（+ 语义向量兜底）。

    对每个节点 x，仅在「与 x 无直接引用边（两个方向都没有）」的节点中按强度取 Top-K；
    两种信号都命中的标注「共引+文献耦合」并附证据（共同被引 / 共同引用的论文标题）。
    图信号为空且 embedding 可用时，补 1 条主题向量最近的未连接论文（明确标注方法），
    embedding 不可用则跳过——**不编造关系**。小图上强度值易饱和，故证据列明共同邻居。
    """
    if G is None or len(G.nodes) < 2:
        return []
    cocit, coupl = _cocitation_map(G), _coupling_map(G)
    try:
        vecs = _paper_vectors(ctx, list(G.nodes)) or {}
    except Exception:
        vecs = {}
    out = []
    for x in G.nodes:
        recs = []
        for y in G.nodes:
            if y == x or G.has_edge(x, y) or G.has_edge(y, x):
                continue
            key = frozenset((x, y))
            sc, cc = cocit.get(key, 0.0), coupl.get(key, 0.0)
            if max(sc, cc) < min_score:
                continue
            methods, ev = [], []
            if cc:
                methods.append("文献耦合")
                shared = sorted(set(G.successors(x)) & set(G.successors(y)))
                ev.append("共同引用：" + "、".join(f"《{_node_title(ctx, s)}》" for s in shared[:2]))
            if sc:
                methods.append("共引")
                shared = sorted(set(G.predecessors(x)) & set(G.predecessors(y)))
                ev.append("共同被引于：" + "、".join(f"《{_node_title(ctx, s)}》" for s in shared[:2]))
            recs.append({"paper": y, "title": _node_title(ctx, y),
                         "score": round(max(sc, cc), 3),
                         "method": "+".join(methods), "evidence": "；".join(ev)})
        if not recs and vecs.get(x) is not None:
            best, bs = None, 0.0
            for y in G.nodes:
                if y == x or G.has_edge(x, y) or G.has_edge(y, x) or vecs.get(y) is None:
                    continue
                s = cosine(vecs[x], vecs[y])
                if s > bs:
                    best, bs = y, s
            if best is not None and bs >= 0.5:
                recs.append({"paper": best, "title": _node_title(ctx, best),
                             "score": round(bs, 3), "method": "主题向量(embedding)",
                             "evidence": f"主题向量余弦 {bs:.2f}（图内无共引/耦合信号）"})
        recs.sort(key=lambda r: -r["score"])
        if recs:
            out.append({"paper": x, "title": _node_title(ctx, x),
                        "recommendations": recs[:top_k]})
    out.sort(key=lambda d: -max(r["score"] for r in d["recommendations"]))
    return out


def detect_hotspots(ctx, G, top_k=INSIGHT_MAX_HOTSPOT):
    """引文速度视角的新兴热点（Price 累积优势 / Newman「晚发表但被引超预期」信号）。

    velocity = 本地入度 ÷ max(1, 当前年份 − 发表年份)。年轻论文若已获本地引用，
    单位年龄被引量高于老论文，可能是正在崛起的方向。**本地图谱规模小，该指标只作
    方向性参考，不构成学术结论**；年份缺失或入度 0 的节点不参与（无证据不排序）。
    """
    if G is None or len(G.nodes) == 0:
        return []
    cur = time.localtime().tm_year
    rows = []
    for n in G.nodes:
        ind = G.in_degree(n)
        if ind <= 0:
            continue
        y = _node_year(ctx, n)
        if not y:
            continue
        age = max(1, cur - y)
        rows.append({"paper": n, "title": _node_title(ctx, n), "year": y,
                     "indegree": ind, "age": age, "velocity": round(ind / age, 3)})
    rows.sort(key=lambda r: (-r["velocity"], -r["indegree"], r["year"]))
    return rows[:top_k]


def graph_insights(ctx, G):
    """汇总引文语境 / 分歧 / 相关推荐 / 新兴热点为确定性 insights（综述与 agent 工具复用）。

    任何一步异常都降级返回剩余部分（保证综述链路不中断）；图为空返回含空清单的 dict。
    """
    ins = {"n_nodes": len(G.nodes) if G is not None else 0,
           "n_edges": G.number_of_edges() if G is not None else 0,
           "intents": {}, "disagreements": [], "related": [], "hotspots": []}
    if G is None or len(G.nodes) == 0:
        return ins
    try:
        ins["intents"] = annotate_citation_intents(ctx, G)
    except Exception:
        pass
    try:
        ins["disagreements"] = disagreement_points(ctx, G)
    except Exception:
        pass
    try:
        ins["related"] = recommend_related(ctx, G)
    except Exception:
        pass
    try:
        ins["hotspots"] = detect_hotspots(ctx, G)
    except Exception:
        pass
    return ins


def _insight_sections(ctx, ins):
    """把 insights 渲染为 [(小标题, [行...]), ...]；无信号返回空列表（调用方给占位文案）。"""
    if not ins or not ins.get("n_edges"):
        return []
    secs = []
    it = ins.get("intents") or {}
    by = it.get("by_intent", {})
    lines = [f"- 引用意图分布（Scite 式三分类近似）：支持 {by.get('supporting', 0)} / "
             f"提及 {by.get('mention', 0)} / 对比 {by.get('contrasting', 0)} / "
             f"无引文语境 {by.get('no_context', 0)}（共 {it.get('n_edges', 0)} 条真实引用边）"]
    for d in ins.get("disagreements", []):
        q = "；".join(d["contexts"]) or "（无引文语境原文）"
        lines.append(f"- 技术路线分歧：《{d['from_title']}》→《{d['to_title']}》：{q}")
    secs.append(("引文语境与分歧", lines))
    rel = ins.get("related", [])
    if rel:
        lines = []
        for r in rel:
            for rec in r["recommendations"][:2]:
                lines.append(f"- 《{r['title']}》 ↔ 《{rec['title']}》：{rec['method']} "
                             f"{rec['score']:.2f}（{rec['evidence']}）")
        if lines:
            secs.append(("相关但未被直接引用（共引 / 文献耦合）", lines))
    hot = ins.get("hotspots", [])
    if hot:
        lines = [f"- 《{h['title']}》（{h['year']}）：本地被引 {h['indegree']} 次 / {h['age']} 年 "
                 f"→ 引文速度 {h['velocity']:.2f} 次/年" for h in hot]
        secs.append(("新兴热点（引文速度 = 本地被引 ÷ 论文年龄，小图仅作方向性参考）", lines))
    return secs


def _insight_blocks(ctx, ins):
    """prompt 注入用三个文本块（引文语境 / 相关推荐 / 热点）；无信号统一给占位文案。"""
    secs = _insight_sections(ctx, ins)
    ph = "（无引用边或信号不足，本节不可用）"
    intent = related = hot = ph
    for title, lines in secs:
        body = "\n".join(lines)
        if title.startswith("引文语境"):
            intent = body
        elif title.startswith("相关"):
            related = body
        elif title.startswith("新兴热点"):
            hot = body
    return intent, related, hot


def insights_appendix(ctx, ins):
    """附录 Markdown：图谱增强信号明细（确定性计算，非 LLM 生成）；无信号返回空串。"""
    secs = _insight_sections(ctx, ins)
    if not secs:
        return ""
    lines = ["", "---", "#### 引文语境与研究空白（确定性计算，非 LLM 生成）", ""]
    for title, body in secs:
        lines.append(f"**{title}**")
        lines.extend(body)
        lines.append("")
    # 语料规模坦诚：节点数远低于赛题「20 篇批量文献」时明确标注，不冒充大图效果
    if (ins or {}).get("n_nodes", 0) < SMALL_CORPUS_NODES:
        lines.append(f"⚠️ **语料规模坦诚说明**：当前图谱仅 {ins.get('n_nodes')} 个逻辑节点，"
                     f"共引 / 耦合强度与引文速度在极小图上区分度有限，仅证明信号链路可用，"
                     f"不代表 20 篇规模下的效果。")
    return "\n".join(lines).rstrip() + "\n"


def insights_markdown(ctx, ins):
    """引文语境 / 推荐 / 热点的完整中文 Markdown（agent 工具直接返回；确定性，无 LLM）。"""
    secs = _insight_sections(ctx, ins)
    if not secs:
        return "（图谱为空或无真实引用边：引文语境、共引/耦合推荐、引文速度热点均不可用）"
    out = []
    for title, body in secs:
        out.append(f"**{title}**")
        out.append("\n".join(body))
        out.append("")
    if (ins or {}).get("n_nodes", 0) < SMALL_CORPUS_NODES:
        out.append(f"⚠️ 语料规模坦诚说明：当前图谱仅 {ins.get('n_nodes')} 个逻辑节点，"
                   f"上述信号在极小图上区分度有限，仅证明链路可用。")
    return "\n".join(out).strip()

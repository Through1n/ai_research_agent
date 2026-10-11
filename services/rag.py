"""抗幻觉 RAG：Dense(向量)+Sparse(BM25) 混合检索（多查询变体）→ RRF 融合 → 可选 LLM 重排
→ 带内联引用的答案 + 引用校验 + 一次自动修复 + 溯源（含段落级坐标）。

对外接口签名保持向后兼容：retrieve(ctx, query, ...) / answer(ctx, question, ...)，
新增参数一律带默认值（source_filter / compare_papers / per_paper / max_repair）。
"""
import re

import numpy as np

from services.state import tokenize
from core.guardrails import (ACADEMIC_SYSTEM, REFUSAL_TEXT, validate_citations,
                             validate_grouped_citations)

# ---------- 本模块自持阈值（不改动 config.py，避免与其他 agent 的独占文件冲突） ----------
RELEVANCE_FLOOR = 0.35      # 拒答闸门：top chunk 相关度低于此值直接拒答，不让 LLM 自由发挥
MAX_REPAIR = 1              # 幻觉修复环最多重跑 1 次（防死循环 + 控成本）
MAX_VARIANT_DENSE = 4       # dense 路最多消费前 N 个查询变体（含原查询）
COMPARE_PER_PAPER = 3       # 跨文献对比时每篇论文至少取到的 chunk 数

_PARA_SPLIT = re.compile(r"\n\s*\n|\n")


# ---------------- 统一 LLM 入口（T6） ----------------

def _llm_complete(ctx, messages, temperature=0.1):
    """走 core.llm.call_llm_with_retry（3 次指数退避 + 失败返回伪响应，不抛异常）。

    复用 ctx.llm 上已配置的 provider key/base_url/model，避免丢失用户在侧边栏的选型；
    取不到时 call_llm_with_retry 会回落到 config 的 .env 配置。
    """
    from core.llm import call_llm_with_retry
    try:
        resp = call_llm_with_retry(
            messages,
            model=getattr(getattr(ctx, "llm", None), "model", None),
            temperature=temperature,
            api_key=getattr(getattr(ctx, "llm", None), "api_key", "") or "",
            base_url=getattr(getattr(ctx, "llm", None), "base_url", None),
        )
        return resp.choices[0].message.content or ""
    except Exception:
        return ""


def _llm_json(ctx, messages, temperature=0.0):
    from core.llm import parse_json
    try:
        return parse_json(_llm_complete(ctx, messages, temperature) or "")
    except Exception:
        return {}


# ---------------- 稀疏 / 稠密检索 ----------------

def sparse_search(ctx, query, k, variants=None, source=None, allow_zero=False):
    """BM25 稀疏检索：对每个查询变体取逐块最大分；source 可锁定单篇；
    allow_zero=True 时放宽阈值，即使得分为 0 也按分数倒序补齐 k 条（防某篇被饿死）。"""
    if ctx.bm25 is None:
        return []
    variants = variants or [query]
    best = None
    for v in variants:
        try:
            scores = ctx.bm25.get_scores(tokenize(v))
        except Exception:
            continue
        best = scores if best is None else np.maximum(best, scores)
    if best is None:
        return []
    order = np.argsort(best)[::-1]
    out = []
    for i in order:
        if not allow_zero and best[i] <= 0:
            break
        try:
            c = ctx.chunks[i]
        except (IndexError, TypeError):
            break
        if source and c.get("source") != source:
            continue
        out.append(c["id"])
        if len(out) >= k:
            break
    return out


def dense_search(ctx, query, k):
    try:
        n = min(k, max(ctx.collection.count(), 1))
        res = ctx.collection.query(query_texts=[query], n_results=n)
        return list(res["ids"][0])
    except Exception:
        return []


def rrf_fuse(rankings, final_k, c=60):
    scores = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0) + 1.0 / (c + rank + 1)
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in ordered[:final_k]]


def llm_rerank(ctx, query, cids, chunk_by_id):
    """让 LLM 按相关性对候选片段重排（cross-encoder 式），返回重排后的 id 顺序。"""
    listing = "\n".join(f"({i}) {chunk_by_id[cid]['text'][:300]}" for i, cid in enumerate(cids))
    prompt = f"""用户问题：{query}
下列是候选文献片段，请按与问题的相关性从高到低排序，只输出 JSON：{{"order":[编号,...]}}
片段：
{listing}"""
    try:
        data = _llm_json(ctx, [{"role": "user", "content": prompt}], temperature=0.0)
        order = data.get("order") or []
        reranked = [cids[i] for i in order if isinstance(i, int) and 0 <= i < len(cids)]
        return reranked + [c for c in cids if c not in reranked]
    except Exception:
        return cids


# ---------------- 段落级坐标 / 相关度打分 ----------------

def _split_paragraphs(text):
    raw = re.split(r"\n\s*\n", text or "")
    paras = [p.strip() for p in raw if p.strip()]
    if len(paras) <= 1:
        paras = [p.strip() for p in _PARA_SPLIT.split(text or "") if p.strip()]
    return paras or ([text.strip()] if (text or "").strip() else [])


def annotate_paragraphs(sources, ctx=None):
    """给来源补段落级坐标 para_idx / para_span（页内第几段，1-based）。

    不改 services/library.py 的 chunk 字段：检索后按 (source, page) 分组，用该页全部
    chunk 的段落数累加推算段序号；返回的是 chunk 的**浅拷贝**，不污染 ctx.chunks / library.json。
    """
    out = [dict(c) for c in sources]
    page_chunks = {}
    try:
        if ctx is not None:
            for c in getattr(ctx, "chunks", []) or []:
                page_chunks.setdefault((c.get("source"), c.get("page")), []).append(c)
    except Exception:
        page_chunks = {}
    for c in out:
        key = (c.get("source"), c.get("page"))
        try:
            seq = page_chunks.get(key) or [c]
            pos = next((i for i, x in enumerate(seq) if x.get("id") == c.get("id")), 0)
            before = sum(len(_split_paragraphs(x.get("text", ""))) for x in seq[:pos])
            n = len(_split_paragraphs(c.get("text", "")))
        except Exception:
            before, n = 0, 1
        c["para_idx"] = int(before) + 1
        c["para_span"] = max(int(n), 1)
    return out


def _cosine(a, b):
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def score_sources(ctx, query, sources, variants=None):
    """相关度打分（0~1）：0.5 × 向量余弦（取所有变体最大值）+ 0.5 × BM25 归一（除以本次最高分）。

    向量不可用时退化为 BM25 归一，保证拒答闸门始终有分可判。
    返回 {chunk_id: score}。
    """
    if not sources:
        return {}
    qs, seen = [], set()
    for v in [query] + list(variants or []):
        v = (v or "").strip()
        if v and v not in seen:
            seen.add(v)
            qs.append(v)
    qs = qs[:MAX_VARIANT_DENSE]

    dense = {}
    try:
        qv = ctx.embed(qs)
        cv = ctx.embed([(c.get("text") or "")[:2000] for c in sources])
        for c, v in zip(sources, cv):
            dense[c["id"]] = max((_cosine(q, v) for q in qv), default=0.0)
    except Exception:
        dense = {}

    sparse = {}
    try:
        if ctx.bm25 is not None:
            by_id = {c["id"]: i for i, c in enumerate(getattr(ctx, "chunks", []) or [])}
            best = None
            for v in qs:
                s = ctx.bm25.get_scores(tokenize(v))
                best = s if best is None else np.maximum(best, s)
            mx = float(np.max(best)) if best is not None and len(best) else 0.0
            for c in sources:
                i = by_id.get(c["id"])
                sparse[c["id"]] = (float(best[i]) / mx) if (i is not None and mx > 0) else 0.0
    except Exception:
        sparse = {}

    out = {}
    for c in sources:
        d, s = dense.get(c["id"]), sparse.get(c["id"])
        if d is None and s is None:
            score = 0.0
        elif d is None:
            score = max(s, 0.0)
        elif s is None:
            score = max(d, 0.0)
        else:
            score = 0.5 * max(d, 0.0) + 0.5 * max(s, 0.0)
        out[c["id"]] = round(min(max(score, 0.0), 1.0), 4)
    return out


# ---------------- 检索 ----------------

def retrieve(ctx, query, dense_k=None, sparse_k=None, final_k=None, use_rerank=False,
             source_filter=None):
    """混合检索。source_filter 传入论文文件名时只在该篇内检索（单篇范围锁定）。

    返回 (sources, debug)；sources 为 chunk 浅拷贝，额外带 score / para_idx / para_span。
    """
    from config import DENSE_K, SPARSE_K, FINAL_K
    dense_k, sparse_k, final_k = dense_k or DENSE_K, sparse_k or SPARSE_K, final_k or FINAL_K
    if source_filter:
        # 过滤后再截断，先把召回面放大，避免锁定单篇后候选不足
        dense_k = max(dense_k, final_k * 3)
        sparse_k = max(sparse_k, final_k * 3)
    by_id = {c["id"]: c for c in ctx.chunks}

    from services.query_expansion import expand_queries
    variants = expand_queries(ctx, query)          # List[str]，首元素为原查询
    if not variants:
        variants = [query]

    d_rankings = []
    for v in variants[:MAX_VARIANT_DENSE]:
        k = dense_k if v == query else max(dense_k // 2, final_k)
        ids = dense_search(ctx, v, k)
        if source_filter:
            ids = [i for i in ids if by_id.get(i, {}).get("source") == source_filter]
        d_rankings.append(ids)
    s_ids = sparse_search(ctx, query, sparse_k, variants=variants, source=source_filter)
    fused = rrf_fuse(d_rankings + [s_ids], final_k)

    if use_rerank and getattr(getattr(ctx, "llm", None), "available", False) and len(fused) > 1:
        fused = llm_rerank(ctx, query, fused, by_id)

    sources = [dict(by_id[cid]) for cid in fused if cid in by_id]
    sources = annotate_paragraphs(sources, ctx)
    scores = score_sources(ctx, query, sources, variants=variants)
    for c in sources:
        c["score"] = scores.get(c["id"], 0.0)

    debug = {"dense": d_rankings[0] if d_rankings else [], "sparse": s_ids, "fused": fused,
             "variants": variants[1:] if len(variants) > 1 else [],
             "dense_variant_ranks": d_rankings,
             "scores": scores,
             "top_score": round(max(scores.values()), 4) if scores else 0.0,
             "source_filter": source_filter}
    return sources, debug


def retrieve_per_paper(ctx, query, papers, per_paper=COMPARE_PER_PAPER, use_rerank=False):
    """跨文献对比检索：按论文分组召回，保证每篇至少 per_paper 条（不足则放宽相似阈值补齐）。"""
    from config import FINAL_K
    groups, debug = [], {"mode": "compare", "per_paper": {}, "scores": {},
                         "top_score": 0.0, "variants": [], "fused": []}
    all_scores = {}
    for name in papers:
        try:
            srcs, dbg = retrieve(ctx, query, final_k=max(per_paper * 2, FINAL_K),
                                 use_rerank=use_rerank, source_filter=name)
        except Exception:
            srcs, dbg = [], {"variants": []}
        variants = [query] + list(dbg.get("variants") or [])
        debug["variants"] = dbg.get("variants") or debug["variants"]
        if len(srcs) < per_paper:
            # 放宽阈值：对该篇全部 chunk 按 BM25 分数倒序补齐（不再要求 score>0）
            have = {c.get("id") for c in srcs}
            for cid in sparse_search(ctx, query, per_paper * 4, variants=variants,
                                     source=name, allow_zero=True):
                if cid in have:
                    continue
                c = next((x for x in ctx.chunks if x.get("id") == cid), None)
                if c:
                    srcs.append(dict(c))
                    have.add(cid)
                if len(srcs) >= per_paper:
                    break
        srcs = annotate_paragraphs(srcs, ctx)
        scores = score_sources(ctx, query, srcs, variants=variants)
        for c in srcs:
            c["score"] = scores.get(c.get("id"), 0.0)
        all_scores.update(scores)
        srcs = srcs[:per_paper]
        groups.append((name, srcs))
        debug["per_paper"][name] = len(srcs)
        debug["fused"].extend(c.get("id") for c in srcs)
    debug["scores"] = all_scores
    debug["top_score"] = round(max(all_scores.values()), 4) if all_scores else 0.0
    return groups, debug


# ---------------- 上下文构建 ----------------

def build_context(sources, ctx=None):
    """扁平上下文：[n] 来源：《X》 第p页第k段\\n文本。ctx 可选，传入时补段落级坐标。"""
    if ctx is not None:
        sources = annotate_paragraphs(sources, ctx)
    blocks = []
    for i, c in enumerate(sources, 1):
        loc = f"第{c.get('page')}页"
        if c.get("para_idx"):
            loc += f"第{c['para_idx']}段"
        blocks.append(f"[{i}] 来源：《{c.get('source', '')}》 {loc}\n{c.get('text', '')}")
    return "\n\n".join(blocks)


def build_grouped_context(groups):
    """跨文献对比上下文：按论文分组编号 [P1-1][P1-2][P2-1]...

    groups: [(paper_name, [chunk, ...]), ...]
    返回 (context_text, index)；index: {"P1-1": {"source","page","para_idx","text"}}
    """
    blocks, index = [], {}
    for pi, (name, chunks) in enumerate(groups, 1):
        blocks.append(f"===== 论文 P{pi}：《{name}》 =====")
        for ci, c in enumerate(chunks, 1):
            tag = f"P{pi}-{ci}"          # 校验用的 key；上下文里写作 [P1-1]
            index[tag] = {"source": c.get("source", name), "page": c.get("page"),
                          "para_idx": c.get("para_idx"), "text": c.get("text", "")}
            blocks.append(f"[{tag}] 《{name}》 第{c.get('page')}页第{c.get('para_idx', 1)}段\n{c.get('text', '')}")
    return "\n\n".join(blocks), index


def qa_messages(question, context, extra_instruction=""):
    sys = ACADEMIC_SYSTEM
    user = (f"参考资料（编号即来源）：\n{context}\n\n"
            f"问题：{question}\n"
            f"请严格依据资料用中文作答，每个事实结论后标注【n】；对比类问题用 Markdown 表格。{extra_instruction}")
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def compare_messages(question, context, papers):
    """跨文献对比模式的 messages：分论文陈述 + 末尾强制 Markdown 对比表。"""
    listing = "；".join(f"P{i}《{n}》" for i, n in enumerate(papers, 1))
    user = (f"参考资料已按论文分组，角标形如【P1-2】（P1=第1篇论文，2=该篇第2个片段）：\n{context}\n\n"
            f"论文编号对照：{listing}\n"
            f"问题：{question}\n"
            f"请严格依据资料用中文作答：\n"
            f"1) 先按论文分别陈述，每篇至少一段，每句事实后标注【Pn-m】角标；\n"
            f"2) 最后必须给出一张 Markdown 对比表格：行为对比维度，列为 {listing}，每格注明角标；\n"
            f"3) 某篇资料不足时明确写“该论文资料不足”，禁止编造。")
    return [{"role": "system", "content": ACADEMIC_SYSTEM}, {"role": "user", "content": user}]


# ---------------- 主问答流程（含拒答闸门 + 一次自动修复） ----------------

def _refuse(reason, sources=None, debug=None):
    return {"answer": f"{REFUSAL_TEXT}（{reason}）", "sources": list(sources or [])[:3],
            "issues": [f"拒答闸门：{reason}"], "debug": debug or {}, "refused": True,
            "top_score": (debug or {}).get("top_score", 0.0)}


def answer(ctx, question, use_rerank=False, history=None, source_filter=None,
           compare_papers=None, per_paper=COMPARE_PER_PAPER, max_repair=MAX_REPAIR):
    """抗幻觉问答主入口：检索 → 拒答闸门 → 生成 → 引用校验（范围 + 内容一致性）→ 最多 1 次自动修复。

    返回 {"answer", "sources", "issues", "debug", "refused", "top_score"}。
    compare_papers 传入论文名列表（≥2）时走按论文分组的对比流程。
    """
    question = (question or "").strip()
    if not question:
        return {"answer": "请输入有效问题。", "sources": [], "issues": ["空问题"],
                "debug": {}, "refused": True, "top_score": 0.0}
    if not getattr(getattr(ctx, "llm", None), "available", False):
        return _refuse("未配置大模型 API Key，无法生成答案，请在左侧填入 Key")

    if compare_papers:
        groups, debug = retrieve_per_paper(ctx, question, list(compare_papers),
                                           per_paper=per_paper, use_rerank=use_rerank)
        flat = [c for _, cs in groups for c in cs]
        if not flat:
            return _refuse("所选论文未检索到任何片段", [], debug)
        context, index = build_grouped_context(groups)
        sources, top = flat, debug["top_score"]
        msgs = compare_messages(question, context, [n for n, _ in groups])
        def check(t):
            return validate_grouped_citations(t, index)
    else:
        sources, debug = retrieve(ctx, question, use_rerank=use_rerank, source_filter=source_filter)
        if not sources:
            return _refuse("文献库为空或检索无命中", [], debug)
        top = max((c.get("score", 0.0) for c in sources), default=0.0)
        context = build_context(sources, ctx)
        msgs = qa_messages(question, context)
        texts = [c.get("text", "") for c in sources]
        def check(t):
            return validate_citations(t, len(sources), source_texts=texts)

    # 拒答闸门：top chunk 相关度不足时直接不作答，避免 LLM 自由发挥
    if top < RELEVANCE_FLOOR:
        return _refuse(f"最高相关度 {top:.2f} 低于阈值 {RELEVANCE_FLOOR}，共 {len(sources)} 个候选片段",
                       sources, debug)

    text = _llm_complete(ctx, msgs, temperature=0.1)
    issues = check(text)

    repairs = 0
    while issues and repairs < max_repair:
        repair = msgs + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "请修正以上回答：确保每条事实都带有效角标且角标指向的资料"
                                        "确实包含该事实；删除资料中没有依据的内容；资料不足处明确说明。"},
        ]
        new_text = _llm_complete(ctx, repair, temperature=0.0)
        if new_text and new_text.strip() and new_text.strip() != text.strip():
            text = new_text
        issues = check(text)
        repairs += 1

    debug = dict(debug)
    debug["repairs"] = repairs
    debug["top_score"] = round(top, 4)
    return {"answer": text, "sources": sources, "issues": issues, "debug": debug,
            "refused": False, "top_score": round(top, 4)}

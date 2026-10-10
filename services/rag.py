"""抗幻觉 RAG：Dense(向量)+Sparse(BM25) 混合检索 → RRF 融合 → 可选 LLM 重排 → 带内联引用的答案与溯源。"""
import numpy as np

from services.state import tokenize
from core.guardrails import ACADEMIC_SYSTEM, validate_citations


def sparse_search(ctx, query, k, variants=None):
    """BM25 稀疏检索：对每个查询变体取逐块最大分（跨语言扩展由 variants 提供）。"""
    if ctx.bm25 is None:
        return []
    variants = variants or [query]
    best = None
    for v in variants:
        scores = ctx.bm25.get_scores(tokenize(v))
        best = scores if best is None else np.maximum(best, scores)
    order = np.argsort(best)[::-1][:k]
    return [ctx.chunks[i]["id"] for i in order if best[i] > 0]


def dense_search(ctx, query, k):
    n = min(k, max(ctx.collection.count(), 1))
    res = ctx.collection.query(query_texts=[query], n_results=n)
    return list(res["ids"][0])


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
        data = ctx.llm.json_chat([{"role": "user", "content": prompt}], temperature=0.0)
        order = data["order"]
        reranked = [cids[i] for i in order if 0 <= i < len(cids)]
        return reranked + [c for c in cids if c not in reranked]
    except Exception:
        return cids


def retrieve(ctx, query, dense_k=None, sparse_k=None, final_k=None, use_rerank=False):
    from config import DENSE_K, SPARSE_K, FINAL_K
    dense_k, sparse_k, final_k = dense_k or DENSE_K, sparse_k or SPARSE_K, final_k or FINAL_K
    by_id = {c["id"]: c for c in ctx.chunks}

    from services.query_expansion import expand_queries
    variants = expand_queries(ctx, query)
    d_ids = dense_search(ctx, query, dense_k)
    s_ids = sparse_search(ctx, query, sparse_k, variants=variants)
    fused = rrf_fuse([d_ids, s_ids], final_k)
    if use_rerank and ctx.llm.available and len(fused) > 1:
        fused = llm_rerank(ctx, query, fused, by_id)
    sources = [by_id[cid] for cid in fused if cid in by_id]
    debug = {"dense": d_ids, "sparse": s_ids, "fused": fused,
             "variants": variants[1:] if len(variants) > 1 else []}
    return sources, debug


def build_context(sources):
    blocks = []
    for i, c in enumerate(sources, 1):
        blocks.append(f"[{i}] 来源：《{c['source']}》 第{c['page']}页\n{c['text']}")
    return "\n\n".join(blocks)


def qa_messages(question, context, extra_instruction=""):
    sys = ACADEMIC_SYSTEM
    user = (f"参考资料（编号即来源）：\n{context}\n\n"
            f"问题：{question}\n"
            f"请严格依据资料用中文作答，每个事实结论后标注【n】；对比类问题用 Markdown 表格。{extra_instruction}")
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def answer(ctx, question, use_rerank=False, history=None):
    sources, debug = retrieve(ctx, question, use_rerank=use_rerank)
    if not sources:
        return {"answer": "文献库为空或未检索到相关内容，请先导入论文。", "sources": [],
                "issues": ["无可用来源"], "debug": debug}
    context = build_context(sources)
    msgs = qa_messages(question, context)
    msg = ctx.llm.chat(msgs, temperature=0.1)
    text = msg.content or ""

    # 引用校验 + 一次自动修复
    issues = validate_citations(text, len(sources))
    if issues:
        repair = msgs + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"请修正以上回答：确保每条事实都带有效【n】角标(范围1~{len(sources)})，"
                                       "删除资料中没有依据的内容；资料不足处明确说明。"},
        ]
        try:
            repaired = ctx.llm.chat(repair, temperature=0.0)
            new_text = repaired.content or text
            if not validate_citations(new_text, len(sources)) or new_text:
                text = new_text
            issues = validate_citations(text, len(sources))
        except Exception:
            pass

    return {"answer": text, "sources": sources, "issues": issues, "debug": debug}

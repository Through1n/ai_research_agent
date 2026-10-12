"""抗幻觉 RAG：Dense(向量)+Sparse(BM25) 混合检索（多查询变体）→ RRF 融合 → 可选 LLM 重排
→ 带内联引用的答案 + 引用校验 + 一次自动修复 + 溯源（含段落级坐标）。

对外接口签名保持向后兼容：retrieve(ctx, query, ...) / answer(ctx, question, ...)，
新增参数一律带默认值（source_filter / compare_papers / per_paper / max_repair / progress）。

Round 9 改动（S1：检索问答链路 + 抗幻觉）：
- 缺陷 A：答案末尾统一过 sanitize_citations——越界角标按内容重定位或标注
  「（未在来源中验证）」，幸存角标按首现顺序重编号为 1..k 并与 sources 一一对应；
- 缺陷 B：原查询双路检索已强命中（top>=STRONG_HIT）时跳过查询扩展 LLM 调用，
  扩展结果按 (用户,查询) 缓存；rerank 保持按需（默认关）；
- 缺陷 E（大书淹没检索 → 假拒答）：库内一篇 541 页大书占 88% chunk 时，
  混合检索 top-k 被单一来源挤满，正确答案所在的短文片段（如 BERT p13 的
  "β1 = 0.9, β2 = 0.999"）进不了上下文，LLM 只能回答"无法确定"。
  修复三连：① RRF 融合池放大到 final_k*4 后按来源截断（MMR/来源均衡的
  轻量实现，每来源上限 max(2, final_k/候选来源数)，单来源池不截断）；
  ② 强命中时离线符号对齐变体照常参与检索（零 LLM 成本，见
  services/query_expansion.py 的 beta1/β1 词表）；③ 变体 dense 召回加深到
  final_k*4（答案片段常排在变体 10+ 名，原深度 6 会漏）；
- 缺陷 C：空库/极少片段时拒答文案给出可操作建议（页面侧另有引导条）；
- 缺陷 F（同论文多版本冲突）：库内 1706.03762 有 v1/v1_img/v7 三份，v1 系全文
  沿用旧值 41.0，v7 只在摘要和 Table 2 改成 41.8——3:1 的信号比让 LLM 稳定
  答错（实测软提示词规则 4/4 进程无效）。修复：融合池内较新版本覆盖
  >= VERSION_DEDUP_MIN 条时丢弃旧版本 chunk（drop_stale_versions），
  实测全库 BLEU 答 28.4/41.8（4/4 进程）；
- 单来源池按相关度分数降序呈现（cap_by_source）：RRF 共识序里泛词 chunk
  挤占【1】【2】时，实测 LLM 稳定把"8 块 P100 GPU"误引到 Adam 优化器段
  （0% 重叠被护栏捕获、修复环纠不动），分数序让最贴题 chunk 居首；
- 抗幻觉：答案中找不到来源依据的数字/实体编码进入 result["unverified"] 并在
  答案末尾附核验说明。
"""
import re
import time

import numpy as np

from services.state import tokenize
from core.guardrails import (ACADEMIC_SYSTEM, REFUSAL_TEXT, sanitize_citations,
                             sanitize_grouped_citations, validate_citations,
                             validate_grouped_citations, find_unverified_numbers,
                             find_unverified_codes)

# ---------- 本模块自持阈值（不改动 config.py，避免与其他 agent 的独占文件冲突） ----------
RELEVANCE_FLOOR = 0.35      # 拒答闸门：top chunk 相关度低于此值直接拒答，不让 LLM 自由发挥
MAX_REPAIR = 1              # 幻觉修复环最多重跑 1 次（防死循环 + 控成本）
MAX_VARIANT_DENSE = 5       # dense 路最多消费前 N 个查询变体（含原查询）；离线词表常产生 3~4 条，放宽到 5 保证符号对齐变体不被挤掉（每条仅多一次本地向量查询，毫秒级）
COMPARE_PER_PAPER = 3       # 跨文献对比时每篇论文至少取到的 chunk 数
STRONG_HIT = 0.55           # 原查询检索 top 相关度达到该值 → 跳过查询扩展（强命中按需，省一次 LLM 调用）
SMALL_LIBRARY_CHUNKS = 8    # 片段数低于该值视为"库内内容极少"，拒答文案给出补充引导
SOURCE_CAP_MIN = 2          # 来源多样性截断：每来源至少保留 2 条（≈ final_k 的 1/3）
POOL_FACTOR = 4             # RRF 融合池 = final_k * 4：答案片段可能排在融合序 10+ 名，池太小则截断无材可取
VARIANT_DENSE_FACTOR = 4    # 变体 dense 召回深度 = final_k * 4：符号对齐变体下答案片段实测排第 5~12 名
VERSION_DEDUP_MIN = 3       # 同论文多版本去重阈值：较新版本在融合池内达到该条数才丢弃旧版本 chunk

_VERSION_RE = re.compile(r"^(?P<base>.+?)v(?P<ver>\d+)(?:_img)?$")


def _paper_version(source):
    """从来源文件名解析 (论文基名, 版本号)；无版本号返回 (去掉后缀的文件名, None)。

    "1706.03762v7.pdf" → ("1706.03762", 7)；"1706.03762v1_img.pdf" → ("1706.03762", 1)；
    "UnderstandingDeepLearning_02_09_26_C.pdf" → (整串, None)（不参与版本去重）。
    """
    stem = (source or "").rsplit(".", 1)[0]
    m = _VERSION_RE.match(stem)
    if m:
        return m.group("base"), int(m.group("ver"))
    return stem, None


def drop_stale_versions(ordered_ids, by_id, min_keep=VERSION_DEDUP_MIN):
    """同论文多版本去重：库内同篇论文常有多个版本（实测 1706.03762 有
    v1/v1_img/v7 三份），旧版数值可能已被作者修订——Transformer v1 全文写
    BLEU 41.0，v7 只在摘要和 Table 2 改成 41.8。两版同时进 top6 时 LLM 实测
    答旧值 41.0（v1 完整句子 vs v7 表格+摘要 2:2 信号比下仍选旧版）。

    规则：某论文较新版本在池内覆盖 >= min_keep 条时，丢弃该论文旧版本的
    chunk；覆盖不足则保留旧版兜底——Adam 问题实测 v7 仅 2 条入池，旧版的
    "β1=0.9, β2=0.98" 是有效佐证，若一并去重则 0.9/0.98 只剩 v7 一票，
    会被 BERT 摘要里的 0.9/0.999 稀释（验收基线不容回归）。
    保持池内原有 RRF 次序，返回新列表。
    """
    groups = {}
    for cid in ordered_ids:
        base, ver = _paper_version(by_id.get(cid, {}).get("source") or "")
        groups.setdefault(base, []).append((cid, ver))
    drop = set()
    for items in groups.values():
        vers = {v for _, v in items if v is not None}
        if len(vers) < 2:
            continue
        newest = max(vers)
        if sum(1 for _, v in items if v == newest) >= min_keep:
            drop.update(cid for cid, v in items if v is not None and v < newest)
    if not drop:
        return list(ordered_ids)
    return [cid for cid in ordered_ids if cid not in drop]

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


def cap_by_source(ordered_ids, by_id, final_k, intent_rankings=None, scores=None):
    """来源多样性截断 + 查询意图覆盖（MMR / 来源均衡的轻量实现，缺陷 E 的核心修复）。

    库内一篇大书可占 88% 的 chunk，RRF 融合序前几名会被单一来源挤满，导致
    正确答案所在的短文片段（如 BERT p13 的 "β1 = 0.9, β2 = 0.999"）永远进不了
    top-k（假拒答）。三步修复：
    1. **来源排序**：默认按融合池首现序；提供 scores 时按该来源最佳片段分数降序。
       RRF 首现是"共识序"，大书凭泛词（learning rate / parameter）在多个变体
       排名重复出现即可劫持前几位；分数直接反映与查询的相关度，让最贴题的
       来源（含其答案片段）率先进入 LLM 视野。
    2. **意图覆盖**：每个变体排名中各来源的最佳 chunk 入座该来源候选首位。
       答案片段只在符号对齐变体下排前列（实测 dense 第 8 / sparse 第 4），
       共识序里排不进来，必须绕过 RRF 直接选它。
    3. **来源截断 + 轮转**：每来源上限 cap = max(SOURCE_CAP_MIN, ceil(final_k/来源数))；
       来源间轮转取值——先保证每个来源各 1 条，再补第 2 条，
       杜绝"前两个来源吃满 final_k"。
    单来源池（source_filter 锁定单篇 / 库内仅一篇）时不截断，仅按相关度分数降序重排
    （防泛词 chunk 挤占【1】【2】导致 LLM 误引，见分支内注释）。

    参数：
      ordered_ids: RRF 融合池（已放大，如 final_k*POOL_FACTOR）；
      intent_rankings: 变体 dense 排名列表（可含融合 sparse 排名），用于意图覆盖；
      scores: 可选 {chunk_id: score}（对融合池打分），用于来源排序；
    返回截断后的 id 列表（长度 <= final_k）。
    """
    if not ordered_ids:
        return []
    src_order, per_src = [], {}
    for cid in ordered_ids:
        s = by_id.get(cid, {}).get("source")
        if s not in per_src:
            per_src[s] = []
            src_order.append(s)
        per_src[s].append(cid)
    if len(src_order) <= 1:
        # 单来源池（source_filter 锁定单篇 / 库内仅一篇）：不截断，但按相关度分数降序呈现。
        # RRF 共识序里泛词 chunk 会挤占【1】【2】——实测问"用了什么 GPU、多少块"时
        # LLM 稳定把答案句误引到【2】=__22（Adam 优化器段，词汇重叠 0%，被护栏捕获，
        # 自动修复环也纠不动），重编号后 sources 被过滤成不含 P100 的 Adam 段。
        # 分数序让最贴题 chunk（__5 "eight P100 GPUs" / __21 "8 NVIDIA P100 GPUs"）
        # 居首，即便误引也落在含同一事实的 chunk 上。稳定排序：同分保持融合池首现序。
        picked = list(ordered_ids[:final_k])
        if scores:
            picked.sort(key=lambda c: scores.get(c, 0.0), reverse=True)
        return picked
    if scores:
        # 稳定排序：同分保持融合池首现序
        src_order.sort(key=lambda s: max((scores.get(c, 0.0) for c in per_src[s]),
                                         default=0.0), reverse=True)
    cap = max(SOURCE_CAP_MIN, -(-final_k // len(src_order)))   # ceil(final_k / n_sources)

    # 每个来源的候选：变体意图最佳在前（按意图序，意图间去重），融合池其余 chunk 在后
    cand = {}
    for s in src_order:
        heads, head_set = [], set()
        for R in (intent_rankings or []):
            for cid in R:
                if by_id.get(cid, {}).get("source") == s:
                    if cid not in head_set:
                        heads.append(cid)
                        head_set.add(cid)
                    break            # 每个意图排名只取该来源的第 1 名
        cand[s] = (heads + [c for c in per_src[s] if c not in head_set])[:cap]

    # 来源间轮转：第 0 轮每个来源各取 1 条，第 1 轮再各取 1 条……
    picked = []
    for i in range(cap):
        for s in src_order:
            if i < len(cand[s]):
                picked.append(cand[s][i])
                if len(picked) >= final_k:
                    return picked
    return picked


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
             source_filter=None, progress=None):
    """混合检索。source_filter 传入论文文件名时只在该篇内检索（单篇范围锁定）。

    三阶段（Round 9）：
      Phase 1 原查询 Dense+BM25 双路检索并打分（零 LLM 调用）；
      Phase 2 按 Phase 1 的 top 相关度决定扩展策略：低于 STRONG_HIT 才调 LLM
              查询扩展（结果按 (用户,查询) 缓存）；强命中则跳过 LLM，但离线
              词表/符号对齐变体照常参与检索（零成本，修符号 mismatch 失配）；
      Phase 3 融合池放大到 final_k*POOL_FACTOR 后按来源截断（cap_by_source），
              防止大书淹没 top-k（缺陷 E）。
    强命中跳过 LLM 扩展：实测原查询 top 已达 0.52~0.67，LLM 扩展仅带来边际增益，
    却要多等 6~13s——用户看到首字/结果的时间因此显著下降。

    返回 (sources, debug)；sources 为 chunk 浅拷贝，额外带 score / para_idx / para_span。
    progress: 可选回调 progress(stage_str)，供 UI 展示阶段进度（服务层不依赖 Streamlit）。
    """
    from config import DENSE_K, SPARSE_K, FINAL_K
    dense_k, sparse_k, final_k = dense_k or DENSE_K, sparse_k or SPARSE_K, final_k or FINAL_K
    if source_filter:
        # 过滤后再截断，先把召回面放大，避免锁定单篇后候选不足
        dense_k = max(dense_k, final_k * 3)
        sparse_k = max(sparse_k, final_k * 3)
    by_id = {c["id"]: c for c in ctx.chunks}
    variant_dense_k = max(dense_k, final_k * VARIANT_DENSE_FACTOR)
    pool_k = max(final_k * POOL_FACTOR, dense_k + sparse_k)

    def _say(stage):
        if progress:
            try:
                progress(stage)
            except Exception:
                pass

    t0 = time.time()
    # ---------- Phase 1：原查询双路检索 + 相关度打分（零 LLM 调用） ----------
    _say("① 原查询混合检索（Dense + BM25）…")
    d0 = dense_search(ctx, query, dense_k)
    if source_filter:
        d0 = [i for i in d0 if by_id.get(i, {}).get("source") == source_filter]
    s0 = sparse_search(ctx, query, sparse_k, source=source_filter)
    fused0 = rrf_fuse([d0, s0], final_k)
    srcs0 = annotate_paragraphs([dict(by_id[cid]) for cid in fused0 if cid in by_id], ctx)
    sc0 = score_sources(ctx, query, srcs0)
    for c in srcs0:
        c["score"] = sc0.get(c["id"], 0.0)
    top0 = max(sc0.values()) if sc0 else 0.0
    t1 = time.time()

    # ---------- Phase 2：弱命中才调 LLM 扩展；强命中只跑离线变体（省一次 LLM 调用） ----------
    from services.query_expansion import expand_queries
    report = {}
    variants = [query]
    if srcs0 and top0 < STRONG_HIT:
        _say("② 相关度偏低，正在做查询扩展（LLM）…")
        variants = expand_queries(ctx, query, report=report) or [query]
    else:
        report = {"mode": "skipped_strong_hit",
                  "note": f"原查询 top={top0:.2f}≥{STRONG_HIT}，已强命中，跳过查询扩展 LLM 调用"
                          if srcs0 else "无候选片段，跳过查询扩展"}
        if srcs0:
            # 强命中 ≠ 答案片段已召回：符号 mismatch（查询 beta1 vs 原文 β1）时
            # 原查询双路会完整错过答案片段。离线变体零 LLM 成本，照常参与检索。
            variants = expand_queries(ctx, query, report=report, allow_llm=False) or [query]
    extra = [v for v in variants[1:] if v and v != query][:MAX_VARIANT_DENSE - 1]
    d_rankings = [d0]
    for v in extra:
        ids = dense_search(ctx, v, variant_dense_k)
        if source_filter:
            ids = [i for i in ids if by_id.get(i, {}).get("source") == source_filter]
        d_rankings.append(ids)
    if extra:
        s_ids = sparse_search(ctx, query, sparse_k, variants=variants, source=source_filter)
        rankings = d_rankings + [s_ids]
    else:
        s_ids, rankings = s0, [d0, s0]

    # ---------- Phase 3：融合池 + 来源多样性截断/意图覆盖（缺陷 E：防大书淹没 top-k） ----------
    fused_pool = rrf_fuse(rankings, pool_k)
    n_pool_rrf = len(fused_pool)
    fused_pool = drop_stale_versions(fused_pool, by_id)
    intents = d_rankings[1:] + ([s_ids] if extra else [])
    pool_chunks = annotate_paragraphs([dict(by_id[cid]) for cid in fused_pool if cid in by_id], ctx)
    pool_scores = score_sources(ctx, query, pool_chunks, variants=variants)
    fused = cap_by_source(fused_pool, by_id, final_k, intent_rankings=intents, scores=pool_scores)
    if use_rerank and getattr(getattr(ctx, "llm", None), "available", False) and len(fused) > 1:
        fused = llm_rerank(ctx, query, fused, by_id)
    sources = annotate_paragraphs([dict(by_id[cid]) for cid in fused if cid in by_id], ctx)
    scores = score_sources(ctx, query, sources, variants=variants)
    for c in sources:
        c["score"] = scores.get(c["id"], 0.0)

    debug = {"dense": d_rankings[0] if d_rankings else [], "sparse": s_ids, "fused": fused,
             "variants": variants[1:] if len(variants) > 1 else [],
             "dense_variant_ranks": d_rankings,
             "scores": scores,
             "top_score": round(max(scores.values()), 4) if scores else 0.0,
             "source_filter": source_filter,
             "expansion_mode": report.get("mode", ""),
             "expand_note": report.get("note", ""),
             "source_cap": {"pool": len(fused_pool), "kept": len(fused),
                            "dropped_stale": n_pool_rrf - len(fused_pool),
                            "per_source": {s: sum(1 for c in fused
                                                  if by_id.get(c, {}).get("source") == s)
                                           for s in {by_id.get(c, {}).get("source") for c in fused}}},
             "timings": {"phase1_ms": int((t1 - t0) * 1000),
                         "expand_ms": int((time.time() - t1) * 1000)}}
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
            f"请严格依据资料用中文作答，每个事实结论后标注【n】；对比类问题用 Markdown 表格；"
            f"资料不足或与问题无关时，只给一句简短说明，禁止附加无关内容、表格或客套话。"
            f"{extra_instruction}")
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


# ---------------- 主问答流程（含拒答闸门 + 一次自动修复 + 引用净化） ----------------

def _library_hint(ctx):
    """库内内容极少时的可操作引导（缺陷 C）；内容丰富时返回空串。"""
    try:
        n = len(getattr(ctx, "chunks", []) or [])
    except Exception:
        n = 0
    if n == 0:
        return ("；当前文献库为空，请先到「📁 文献库管理」上传 PDF，"
                "或点击左侧「🚀 一键加载演示数据」后再提问")
    if n < SMALL_LIBRARY_CHUNKS:
        return (f"；当前文献库仅 {n} 个片段（{len(getattr(ctx, 'files', []) or [])} 篇），"
                f"覆盖面有限，建议补充文献或到「📁 文献库管理」重建索引后重试")
    return ""


def _refuse(reason, sources=None, debug=None):
    return {"answer": f"{REFUSAL_TEXT}（{reason}）", "sources": list(sources or [])[:3],
            "issues": [f"拒答闸门：{reason}"], "debug": debug or {}, "refused": True,
            "top_score": (debug or {}).get("top_score", 0.0), "unverified": []}


def answer(ctx, question, use_rerank=False, history=None, source_filter=None,
           compare_papers=None, per_paper=COMPARE_PER_PAPER, max_repair=MAX_REPAIR,
           progress=None):
    """抗幻觉问答主入口：检索 → 拒答闸门 → 生成 → 引用校验（范围 + 内容一致性）→ 最多 1 次自动修复
    → 引用净化（越界重定位/重编号，保证【n】与 sources 一一对应）。

    返回 {"answer", "sources", "issues", "debug", "refused", "top_score", "unverified"}。
    compare_papers 传入论文名列表（≥2）时走按论文分组的对比流程。
    progress: 可选回调 progress(stage_str)，供 UI 展示阶段进度。
    """
    def _say(stage):
        if progress:
            try:
                progress(stage)
            except Exception:
                pass

    question = (question or "").strip()
    if not question:
        return {"answer": "请输入有效问题。", "sources": [], "issues": ["空问题"],
                "debug": {}, "refused": True, "top_score": 0.0, "unverified": []}
    if not getattr(getattr(ctx, "llm", None), "available", False):
        return _refuse("未配置大模型 API Key，无法生成答案，请在左侧填入 Key")

    if compare_papers:
        groups, debug = retrieve_per_paper(ctx, question, list(compare_papers),
                                           per_paper=per_paper, use_rerank=use_rerank)
        flat = [c for _, cs in groups for c in cs]
        if not flat:
            return _refuse("所选论文未检索到任何片段" + _library_hint(ctx), [], debug)
        context, index = build_grouped_context(groups)
        sources, top = flat, debug["top_score"]
        msgs = compare_messages(question, context, [n for n, _ in groups])
        def check(t):
            return validate_grouped_citations(t, index)
    else:
        sources, debug = retrieve(ctx, question, use_rerank=use_rerank,
                                  source_filter=source_filter, progress=progress)
        if not sources:
            return _refuse("未检索到任何相关片段" + _library_hint(ctx), [], debug)
        top = max((c.get("score", 0.0) for c in sources), default=0.0)
        context = build_context(sources, ctx)
        msgs = qa_messages(question, context)
        texts = [c.get("text", "") for c in sources]
        def check(t):
            return validate_citations(t, len(sources), source_texts=texts)

    # 拒答闸门：top chunk 相关度不足时直接不作答，避免 LLM 自由发挥
    if top < RELEVANCE_FLOOR:
        return _refuse(f"最高相关度 {top:.2f} 低于阈值 {RELEVANCE_FLOOR}，"
                       f"共 {len(sources)} 个候选片段" + _library_hint(ctx),
                       sources, debug)

    _say("③ 生成答案并校验引用…")
    text = _llm_complete(ctx, msgs, temperature=0.1)
    issues = check(text)

    # 修复策略（Round 9）："角标越界"是确定性可修问题——实测 LLM 修复环对越界角标
    # 无效（修复后答案仍含【5】，见复现记录），统一交给末尾 sanitize_citations
    # 重定位/重编号，不再浪费一次 LLM 调用；仅"内容可疑"类问题才值得修复一次。
    repairable = [i for i in issues if "超出有效来源范围" not in i]
    repairs = 0
    while repairable and repairs < max_repair:
        repair = msgs + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "请修正以上回答：确保每条事实都带有效角标且角标指向的资料"
                                        "确实包含该事实；删除资料中没有依据的内容；资料不足处明确说明。"},
        ]
        new_text = _llm_complete(ctx, repair, temperature=0.0)
        if new_text and new_text.strip() and new_text.strip() != text.strip():
            text = new_text
        issues = check(text)
        repairable = [i for i in issues if "超出有效来源范围" not in i]
        repairs += 1

    # ---------- 引用净化（缺陷 A 的最终防线） ----------
    unverified = []
    if compare_papers:
        text, gnotes = sanitize_grouped_citations(text, index)
        issues.extend(gnotes)
        all_texts = [v.get("text", "") for v in index.values()]
        unverified = find_unverified_numbers(text, all_texts) + find_unverified_codes(text, all_texts)
    else:
        texts = [c.get("text", "") for c in sources]
        text, cited, notes = sanitize_citations(text, len(sources), source_texts=texts)
        issues.extend(notes)
        if cited:
            sources = [sources[i] for i in cited]
        unverified = find_unverified_numbers(text, texts) + find_unverified_codes(text, texts)
    # 越界角标已被 sanitize_citations 确定性修复（重定位/重编号/标注），
    # 对应的过期 range 报错从 issues 中移除，保留"引用整理"记录说明做过什么。
    issues = [i for i in issues if "超出有效来源范围" not in i]
    if unverified:
        text = (text.rstrip()
                + "\n\n> ⚠️ 核验说明：以下内容未在来源文本中找到对应依据，请谨慎对待："
                + "、".join(unverified[:8]) + "。")

    debug = dict(debug)
    debug["repairs"] = repairs
    debug["top_score"] = round(top, 4)
    return {"answer": text, "sources": sources, "issues": issues, "debug": debug,
            "refused": False, "top_score": round(top, 4), "unverified": unverified}

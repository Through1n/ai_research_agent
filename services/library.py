"""文献库管理：入库、基于内容 embedding 的语义去重、版本管理与差异对比、删除。"""
import re
import math
import time
import numpy as np

from services import parsing
from config import UPLOAD_DIR


def cosine(a, b):
    a, b = np.asarray(a), np.asarray(b)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def _paper_embedding(ctx, full_text):
    # 取前 4000 字（覆盖摘要/引言/方法主体）做论文级向量
    return ctx.embed([full_text[:4000]])[0]


def _paper_fingerprints(ctx, full_text, k=5, seg=1200):
    """从全文均匀采样 k 个内容片段做指纹向量（跨版本内容匹配的核心）。"""
    if not full_text or len(full_text) < seg:
        return []
    step = max(1, (len(full_text) - seg) // max(k - 1, 1))
    samples = [full_text[i * step: i * step + seg] for i in range(k)]
    if samples[-1] != full_text[-seg:]:
        samples.append(full_text[-seg:])  # 末尾（参考文献区）也取一指纹
    return ctx.embed(samples)


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _seg_sim(new_fps, old_fps):
    """新论文指纹与旧论文指纹逐段最佳匹配，取 top3 平均，返回 (聚合分, 最佳段分)。"""
    if not new_fps or not old_fps:
        return 0.0, 0.0
    per = [max(cosine(nf, of) for of in old_fps) for nf in new_fps]
    per.sort(reverse=True)
    top = per[:3]
    return sum(top) / len(top), per[0]


def check_duplicate(ctx, embedding, threshold=None, arxiv_id=None, fingerprints=None, title=None):
    """内容级语义去重（不依赖文件名）。返回 (dup_name, sim, evidence)。

    判定规则：
    1) 内容相似度（整篇向量 与 分段指纹 top3 均值 取大）≥ 阈值 → 重复；
    2) 标题高度相似(≥0.9) 且内容相似度 ≥0.7 → 判为同一论文不同版本；
    3) arXiv 号相同且内容相似度 ≥0.7 → 判为同一论文不同版本。
    """
    import difflib
    from config import DEDUP_THRESHOLD
    threshold = threshold if threshold is not None else DEDUP_THRESHOLD
    nt = _norm_title(title)
    best, best_sim, best_ev, max_sim = None, 0.0, "", 0.0
    for name in ctx.paper_order:
        p = ctx.papers[name]
        emb = p.get("embedding")
        sim_whole = cosine(embedding, emb) if (emb and embedding is not None) else 0.0
        seg_avg, seg_max = _seg_sim(fingerprints, p.get("fingerprints") or [])
        sim = max(sim_whole, seg_avg)
        max_sim = max(max_sim, sim)
        ev_parts = []
        if seg_avg > sim_whole:
            ev_parts.append(f"分段指纹内容匹配 {seg_avg:.1%}（最佳段 {seg_max:.1%}）")
        elif sim_whole > 0:
            ev_parts.append(f"整篇语义相似度 {sim_whole:.1%}")
        t_sim = difflib.SequenceMatcher(None, nt, _norm_title(p.get("title"))).ratio() if nt else 0.0
        title_hit = bool(nt) and t_sim >= 0.9
        if title_hit:
            ev_parts.append(f"标题高度一致《{p.get('title', name)[:40]}》")
        same_arxiv = bool(arxiv_id) and p.get("arxiv_id") == arxiv_id
        if same_arxiv:
            ev_parts.append("arXiv 号相同")
        is_dup = sim >= threshold or ((title_hit or same_arxiv) and sim >= 0.7)
        if is_dup and sim > best_sim:
            best, best_sim, best_ev = name, sim, "；".join(ev_parts)
    if best:
        return best, best_sim, best_ev
    return None, max_sim, ""


def prepare_pdf(ctx, path=None, data=None, filename="paper.pdf", progress_cb=None,
                timeout_per_page=30, on_page_error=None):
    """读取 PDF（含扫描件 OCR），不做写入。返回待提交数据 + 查重结果。

    长文档增强：
    - timeout_per_page：单页解析耗时阈值（秒）。Windows 无 signal.alarm，不强制中断单页，
      仅在 native 分支逐页用 time.monotonic() 做协作式耗时观测，超阈值时通过 on_page_error 上报；
      OCR 分支由 parsing.ocr_document 内部逐页 try/except 容错。
    - on_page_error(page_idx, err_str)：单页失败或超时时的回调，便于上层记录，不影响主流程。
    - 内存监控：若环境安装了 psutil，返回 peak_mem_mb（处理完成后进程 RSS，单位 MB）；
      psutil 不可用或采样失败时该 key 为 None，不影响主流程。
    """
    # --- 内存监控：开始前确认 psutil 可用（缺失则静默降级）---
    peak_mem_mb = None
    _proc = None
    try:
        import psutil
        _proc = psutil.Process()
        _proc.memory_info().rss  # 首次采样，确认接口可用
    except Exception:
        _proc = None

    raw = bytes(data) if data is not None else open(path, "rb").read()
    doc = parsing.open_doc(raw)
    scanned = parsing.detect_scanned(doc)

    def _safe_progress(r, page):
        """进度回调包一层，避免回调异常打断入库。"""
        if progress_cb:
            try:
                progress_cb(r, page)
            except Exception:
                pass

    if scanned and ctx.llm.available:
        # OCR 分支：parsing.ocr_document 内部已逐页 try/except，这里直接透传进度
        page_texts = parsing.ocr_document(doc, ctx.llm, progress_cb=_safe_progress)
    else:
        # native 分支：逐页解析 + 单页错误恢复 + 每 50 页进度 + 耗时观测
        total = doc.page_count
        page_texts = [""] * total
        for i, page in enumerate(doc):
            t0 = time.monotonic()
            try:
                page_texts[i] = page.get_text()
            except Exception as e:
                # PyMuPDF 单页 get_text 一般不抛，保险起见兜底：失败页置空并上报
                page_texts[i] = ""
                if on_page_error:
                    try:
                        on_page_error(i, str(e))
                    except Exception:
                        pass
            elapsed = time.monotonic() - t0
            if elapsed > timeout_per_page and on_page_error:
                try:
                    on_page_error(i, f"单页解析耗时 {elapsed:.1f}s，超过阈值 {timeout_per_page}s")
                except Exception:
                    pass
            # 每 50 页或最后一页回调一次进度
            if (i + 1) % 50 == 0 or i + 1 == total:
                _safe_progress((i + 1) / max(total, 1), i + 1)

    full_text = "\n\n".join(t for t in page_texts if t)
    doc.close()

    # --- 处理完成后记录一次内存峰值（Python RSS 只增不减，此时近似高水位）---
    if _proc is not None:
        try:
            peak_mem_mb = round(_proc.memory_info().rss / (1024 * 1024), 1)
        except Exception:
            peak_mem_mb = None

    embedding = _paper_embedding(ctx, full_text) if full_text else None
    fingerprints = _paper_fingerprints(ctx, full_text) if full_text else []
    aid = _guess_arxiv(filename)
    dup_name, dup_sim, dup_ev = (check_duplicate(ctx, embedding, arxiv_id=aid, fingerprints=fingerprints)
                                 if embedding is not None else (None, 0.0, ""))
    return {
        "filename": filename, "scanned": scanned, "page_count": len(page_texts),
        "page_texts": page_texts, "full_text": full_text, "embedding": embedding,
        "fingerprints": fingerprints,
        "dup_name": dup_name, "dup_sim": dup_sim, "dup_evidence": dup_ev, "raw": raw,
        "peak_mem_mb": peak_mem_mb,
    }


def commit(ctx, prepared, title=None, authors=None, arxiv_id=None, version=None,
           year=None, github=None, replace=None):
    """把 prepare 的结果正式入库。replace 指定被覆盖的旧文件名。"""
    filename = prepared["filename"]
    if prepared.get("raw"):
        (UPLOAD_DIR / filename).write_bytes(prepared["raw"])
    chunks = parsing.build_chunks_from_texts(prepared["page_texts"])
    arxiv_id = arxiv_id or _guess_arxiv(filename)
    version = version or _guess_version(filename)

    paper = {
        "filename": filename, "title": title or filename,
        "authors": authors or "", "year": year or "",
        "arxiv_id": arxiv_id, "version": version,
        "page_count": prepared["page_count"], "scanned": prepared["scanned"],
        "added_at": time.strftime("%Y-%m-%d %H:%M"),
        "abstract": prepared["full_text"][:600],
        "github": github or [], "embedding": prepared["embedding"],
        "fingerprints": prepared.get("fingerprints") or [],
    }

    # 向量入库（upsert）
    ids, docs, metas = [], [], []
    for i, ch in enumerate(chunks):
        cid = f"{filename}__{i}"
        ids.append(cid); docs.append(ch["text"])
        metas.append({"source": filename, "page": ch["page"], "page_end": ch["page_end"], "index": i})
    if docs:
        ctx.collection.upsert(ids=ids, documents=docs, metadatas=metas)

    # 同步 chunks 到内存持久化（先移除同名旧块）
    ctx.chunks = [c for c in ctx.chunks if c["source"] != filename]
    for cid, ch, m in zip(ids, chunks, metas):
        ctx.chunks.append({"id": cid, "source": filename, "page": ch["page"],
                           "page_end": ch["page_end"], "text": ch["text"]})

    if replace and replace in ctx.papers and replace != filename:
        _delete_paper_records(ctx, replace)

    if filename not in ctx.paper_order:
        ctx.paper_order.append(filename)
    ctx.papers[filename] = paper
    ctx.memory.update_profile(f"{paper['title']} {arxiv_id or ''}")
    ctx.rebuild_bm25()
    ctx.save_library()
    return paper


def add_text_corpus(ctx, filename, full_text, meta=None):
    """用于内置演示语料：直接以文本入库（语义去重同样生效）。"""
    meta = meta or {}
    embedding = _paper_embedding(ctx, full_text) if full_text else None
    fingerprints = _paper_fingerprints(ctx, full_text) if full_text else []
    dup_name, dup_sim, dup_ev = (check_duplicate(ctx, embedding, arxiv_id=meta.get("arxiv_id"),
                                                 fingerprints=fingerprints, title=meta.get("title"))
                                 if embedding is not None else (None, 0.0, ""))
    prepared = {
        "filename": filename, "scanned": False,
        "page_count": len([p for p in full_text.split("\n\n") if p.strip()]) or 1,
        "page_texts": [full_text], "full_text": full_text, "embedding": embedding,
        "fingerprints": fingerprints,
        "dup_name": dup_name, "dup_sim": dup_sim, "dup_evidence": dup_ev,
    }
    return commit(ctx, prepared, title=meta.get("title"), authors=meta.get("authors"),
                  arxiv_id=meta.get("arxiv_id"), version=meta.get("version"), year=meta.get("year"))


def remove_paper(ctx, filename, delete_vector=True):
    if filename not in ctx.papers:
        return False
    _delete_paper_records(ctx, filename)
    ctx.papers.pop(filename, None)
    if filename in ctx.paper_order:
        ctx.paper_order.remove(filename)
    ctx.rebuild_bm25()
    ctx.save_library()
    return True


def _delete_paper_records(ctx, filename):
    ids = [c["id"] for c in ctx.chunks if c["source"] == filename]
    if ids:
        try:
            ctx.collection.delete(ids=ids)
        except Exception:
            pass
    ctx.chunks = [c for c in ctx.chunks if c["source"] != filename]


def group_versions(ctx):
    groups = {}
    for name in ctx.paper_order:
        p = ctx.papers[name]
        key = p.get("arxiv_id") or _guess_arxiv(name)
        groups.setdefault(key, []).append(name)
    return groups


def latest_of(files):
    def vnum(fn):
        m = re.search(r"v(\d+)", fn)
        return int(m.group(1)) if m else 0
    return sorted(files, key=vnum, reverse=True)


def version_diff(ctx, files):
    """让 LLM 以中文表格总结同一论文不同版本差异。"""
    texts = []
    for fn in files:
        t = "\n".join(c["text"] for c in ctx.chunks if c["source"] == fn)
        texts.append(f"【{fn}】\n{t[:3500]}")
    headers = " | ".join([f"版本{i+1}" for i in range(len(files))])
    prompt = f"""请对比同一论文的 {len(files)} 个版本，用中文 Markdown 表格总结差异。
表头列：对比维度 | {headers} | 差异说明
对比维度至少包含：标题/作者、摘要、方法、实验数据(BLEU/参数量等)、参考文献。全部用中文。
{chr(10).join(texts)}
只输出 Markdown 表格。"""
    msg = ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.3)
    return msg.content


def _guess_arxiv(filename):
    m = re.match(r"(\d{4}\.\d{4,5})", filename)
    return m.group(1) if m else filename.replace(".pdf", "")


def _guess_version(filename):
    m = re.search(r"(v\d+)", filename)
    return m.group(1) if m else ""

"""文献库管理：入库、基于内容 embedding 的语义去重、版本管理与差异对比、删除。"""
import io
import math
import queue
import re
import threading
import time

import numpy as np

from core.llm import call_llm_with_retry
from services import parsing
from config import UPLOAD_DIR


PDF_BATCH_SIZE = 20
PDF_PAGE_TIMEOUT_SECONDS = 30
PDF_PROGRESS_INTERVAL = 50


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


def _empty_prepared(filename, raw=b"", error="", reason="corrupt"):
    """构造 prepare_pdf 的兼容空结果，避免文件级异常向上冒泡。

    2026-10 R1 修复：必须带 ``error`` 键（ui/page_library.py 的 ``elif get("error")``
    分支依赖该契约），否则损坏/截断 PDF 会被静默入库成 0 页幽灵文献。
    """
    failed_pages = [{"page": 0, "error": str(error)}] if error else []
    return {
        "filename": filename, "scanned": False, "page_count": 0,
        "page_texts": [], "full_text": "", "embedding": None,
        "fingerprints": [], "dup_name": None, "dup_sim": 0.0,
        "dup_evidence": "", "raw": raw, "peak_mem_mb": None,
        "failed_pages": failed_pages, "toc": [], "_chunks": [],
        "error": str(error or "PDF 解析失败"), "reason": reason,
        "truncated": False, "expected_pages": 0, "extracted_pages": 0,
    }


def _notify_pdf_progress(progress_cb, done, total, msg):
    """优先调用三参数新回调；兼容旧 UI 的 ``callback(ratio, page)``。"""
    if not progress_cb:
        return
    try:
        progress_cb(done, total, msg)
        return
    except TypeError:
        pass
    except Exception:
        return
    try:
        progress_cb(done / max(total, 1), done)
    except Exception:
        pass


def _notify_page_error(on_page_error, page_index, reason):
    if not on_page_error:
        return
    try:
        on_page_error(page_index, reason)
    except Exception:
        pass


def _extract_page_from_raw(raw, page_index, use_ocr, llm):
    """在独立文档实例中解析单页，避免超时线程与主 doc 并发访问。"""
    local_doc = None
    try:
        local_doc = parsing.open_doc(raw)
        page = local_doc[page_index]
        if use_ocr:
            text = parsing.ocr_page_text(page, llm)
            return text, {"lines": [], "text": text}
        text, blocks = parsing.extract_page_text_column_aware(page, return_blocks=True)
        return text, parsing.heading_page_data(blocks, text)
    finally:
        if local_doc is not None:
            try:
                local_doc.close()
            except Exception:
                pass


def _extract_page_with_timeout(raw, page_index, use_ocr, llm, timeout_seconds):
    """用守护线程执行单页解析；超时立即返回并由调用方跳过该页。"""
    result_queue = queue.Queue(maxsize=1)

    def worker():
        try:
            result_queue.put((True, _extract_page_from_raw(raw, page_index, use_ocr, llm)))
        except Exception as exc:
            try:
                result_queue.put((False, str(exc)))
            except Exception:
                pass

    try:
        timeout = max(0.1, float(timeout_seconds or PDF_PAGE_TIMEOUT_SECONDS))
    except Exception:
        timeout = float(PDF_PAGE_TIMEOUT_SECONDS)
    try:
        thread = threading.Thread(target=worker, daemon=True,
                                  name=f"pdf-page-{page_index + 1}")
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            return None, None, f"单页处理超过 {timeout:g} 秒，已跳过"
        try:
            ok, payload = result_queue.get_nowait()
        except queue.Empty:
            return None, None, "单页处理未返回结果"
        if not ok:
            return None, None, str(payload)
        text, heading_data = payload
        return text or "", heading_data or {"lines": [], "text": text or ""}, None
    except Exception as exc:
        return None, None, str(exc)


def _sample_process_memory(process, current_peak):
    if process is None:
        return current_peak
    try:
        current = process.memory_info().rss / (1024 * 1024)
        return round(max(float(current_peak or 0.0), current), 1)
    except Exception:
        return current_peak


def _validate_prepared(prepared, expected_pages, extracted_pages, total_chars):
    """对逐页提取结果做完整性校验；异常时返回带 error 键的结果，正常返回 None。

    2026-10 R1 修复：损坏/截断 PDF 此前会被静默入库（0 页幽灵文献 / 静默丢正文）。
    - 无法打开 / 加密 / 0 页 / 全部页失败 / 文本量过少 → error（UI 不入库）
    - 成功提取页 < 声称页数的 70% → error(reason=truncated)，并带
      truncated/expected_pages/extracted_pages 字段供 UI 做警示
    - 成功页占比 70%~100% 但有缺页 → 仅带 truncated 字段（warning 级提示）
    """
    try:
        expected_pages = int(expected_pages or 0)
        extracted_pages = int(extracted_pages or 0)
        total_chars = int(total_chars or 0)
        prepared["expected_pages"] = expected_pages
        prepared["extracted_pages"] = extracted_pages
        prepared["truncated"] = bool(expected_pages > 0 and extracted_pages < expected_pages)
        if expected_pages <= 0:
            prepared["error"] = "PDF 没有任何可读页面（0 页），未入库。"
            prepared["reason"] = "empty"
            return prepared
        if extracted_pages <= 0:
            if prepared.get("scanned"):
                prepared["error"] = ("检测为扫描件，但当前未启用/不可用 OCR，全部页面均未提取到文本，"
                                     "未入库。请配置支持视觉的模型后重试。")
                prepared["reason"] = "scanned_no_ocr"
            else:
                prepared["error"] = "PDF 所有页面均未提取到有效文本，疑似损坏或纯图片扫描件，未入库。"
                prepared["reason"] = "corrupt"
            return prepared
        if extracted_pages < expected_pages * 0.7:
            prepared["error"] = (f"PDF 疑似截断或损坏：声称 {expected_pages} 页，"
                                 f"实际仅成功提取 {extracted_pages} 页（不足 70%），未入库。"
                                 "请重新下载/上传完整的 PDF。")
            prepared["reason"] = "truncated"
            return prepared
        if total_chars < 200 or extracted_pages < expected_pages * 0.3:
            prepared["error"] = (f"PDF 提取到的有效文本过少（{extracted_pages}/{expected_pages} 页、"
                                 f"共 {total_chars} 字符），疑似截断或损坏，未入库。")
            prepared["reason"] = "truncated"
            return prepared
        return None
    except Exception as exc:
        prepared["error"] = f"PDF 完整性校验失败：{exc}"
        prepared["reason"] = "corrupt"
        return prepared


def prepare_pdf(ctx, path=None, data=None, filename="paper.pdf", progress_cb=None,
                timeout_per_page=PDF_PAGE_TIMEOUT_SECONDS, on_page_error=None):
    """分页读取 PDF（含扫描件 OCR），返回待提交数据和查重结果。

    每 ``PDF_BATCH_SIZE`` 页生成一批 chunks，每 50 页通过
    ``progress_cb(done, total, msg)`` 报告进度；旧的两参数回调仍兼容。单页在独立
    守护线程中执行，超过 ``timeout_per_page`` 秒即跳过并写入 ``failed_pages``。
    """
    raw = b""
    try:
        if data is not None:
            raw = bytes(data)
        else:
            with open(path, "rb") as source_file:
                raw = source_file.read()
    except Exception as exc:
        return _empty_prepared(filename, raw, f"读取 PDF 失败：{exc}")

    peak_mem_mb = None
    process = None
    try:
        import psutil
        process = psutil.Process()
        peak_mem_mb = _sample_process_memory(process, peak_mem_mb)
    except Exception:
        process = None

    doc = None
    try:
        doc = parsing.open_doc(raw)
        try:
            if bool(getattr(doc, "needs_pass", False)):
                raise RuntimeError("PDF 已加密，需要密码才能读取")
        except RuntimeError:
            raise
        except Exception:
            pass
        total = int(doc.page_count)
        if total <= 0:
            raise RuntimeError("PDF 没有任何可读页面（0 页）")
        scanned = bool(parsing.detect_scanned(doc))
        try:
            toc = doc.get_toc() or []
        except Exception:
            toc = []
    except Exception as exc:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
        reason = "encrypted" if "加密" in str(exc) else "corrupt"
        return _empty_prepared(filename, raw, f"打开 PDF 失败：{exc}", reason=reason)
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    try:
        use_ocr = bool(scanned and getattr(ctx.llm, "available", False))
    except Exception:
        use_ocr = False

    page_texts = []
    heading_pages = []
    streamed_chunks = []
    failed_pages = []
    full_text_buffer = io.StringIO()

    for batch_start in range(0, total, PDF_BATCH_SIZE):
        batch_end = min(batch_start + PDF_BATCH_SIZE, total)
        batch_texts = []
        for page_index in range(batch_start, batch_end):
            text, heading_data, error = _extract_page_with_timeout(
                raw, page_index, use_ocr, getattr(ctx, "llm", None), timeout_per_page
            )
            if error:
                reason = str(error)[:300]
                failed_pages.append({"page": page_index + 1, "error": reason})
                _notify_page_error(on_page_error, page_index, reason)
                text = ""
                heading_data = {"lines": [], "text": ""}
            batch_texts.append(text or "")
            heading_pages.append(heading_data)
            if text:
                if full_text_buffer.tell() > 0:
                    full_text_buffer.write("\n\n")
                full_text_buffer.write(text)
            done = page_index + 1
            if done % PDF_PROGRESS_INTERVAL == 0 or done == total:
                _notify_pdf_progress(progress_cb, done, total,
                                     f"已处理 {done}/{total} 页")

        page_texts.extend(batch_texts)
        try:
            batch_chunks = parsing.build_chunks_from_texts(batch_texts, page_offset=batch_start)
            if streamed_chunks and batch_chunks:
                overlap = max(0, int(getattr(parsing, "CHUNK_OVERLAP", 0) or 0))
                if overlap:
                    batch_chunks[0]["text"] = streamed_chunks[-1]["text"][-overlap:] + batch_chunks[0]["text"]
            streamed_chunks.extend(batch_chunks)
        except Exception:
            pass
        peak_mem_mb = _sample_process_memory(process, peak_mem_mb)

    try:
        full_text = full_text_buffer.getvalue()
    except Exception:
        full_text = "\n\n".join(text for text in page_texts if text)
    finally:
        try:
            full_text_buffer.close()
        except Exception:
            pass

    # 完整性校验：损坏/截断/空 PDF 必须带 error 返回，禁止静默入库（R1 缺陷2）
    try:
        extracted_pages = sum(1 for text in page_texts if (text or "").strip())
        total_chars = len(full_text or "")
        prepared_base = {
            "filename": filename, "raw": raw, "failed_pages": failed_pages,
            "scanned": bool(scanned), "peak_mem_mb": peak_mem_mb, "_chunks": streamed_chunks,
            "toc": toc, "page_texts": page_texts, "full_text": full_text,
            "page_count": len(page_texts),
        }
        invalid = _validate_prepared(prepared_base, total, extracted_pages, total_chars)
        if invalid is not None:
            invalid["embedding"] = None
            invalid["fingerprints"] = []
            invalid["dup_name"] = None
            invalid["dup_sim"] = 0.0
            invalid["dup_evidence"] = ""
            return invalid
        if prepared_base.get("truncated"):
            # warning 级：有缺页但未达拦截阈值，放行入库并带上提示字段
            truncated_info = {k: prepared_base[k] for k in ("truncated", "expected_pages", "extracted_pages")}
        else:
            truncated_info = {"truncated": False, "expected_pages": total, "extracted_pages": extracted_pages}
    except Exception as exc:
        return _empty_prepared(filename, raw, f"PDF 完整性校验异常：{exc}", reason="corrupt")

    if not toc:
        try:
            toc = parsing.infer_headings_from_text(heading_pages)
        except Exception:
            toc = []

    try:
        embedding = _paper_embedding(ctx, full_text) if full_text else None
    except Exception:
        embedding = None
    try:
        fingerprints = _paper_fingerprints(ctx, full_text) if full_text else []
    except Exception:
        fingerprints = []
    aid = _guess_arxiv(filename)
    try:
        dup_name, dup_sim, dup_ev = (
            check_duplicate(ctx, embedding, arxiv_id=aid, fingerprints=fingerprints)
            if embedding is not None else (None, 0.0, "")
        )
    except Exception:
        dup_name, dup_sim, dup_ev = None, 0.0, ""
    peak_mem_mb = _sample_process_memory(process, peak_mem_mb)
    return {
        "filename": filename, "scanned": scanned, "page_count": len(page_texts),
        "page_texts": page_texts, "full_text": full_text, "embedding": embedding,
        "fingerprints": fingerprints,
        "dup_name": dup_name, "dup_sim": dup_sim, "dup_evidence": dup_ev, "raw": raw,
        "peak_mem_mb": peak_mem_mb, "failed_pages": failed_pages, "toc": toc,
        "_chunks": streamed_chunks,
        **truncated_info,
    }


def commit(ctx, prepared, title=None, authors=None, arxiv_id=None, version=None,
           year=None, github=None, replace=None):
    """把 prepare 的结果正式入库。replace 指定被覆盖的旧文件名。"""
    if prepared.get("error"):
        # 双保险：带 error 的结果绝不入库（正常流程在 UI 层已被拦截）
        raise ValueError(f"拒绝入库：{prepared.get('error')}")
    filename = prepared["filename"]
    if prepared.get("raw"):
        (UPLOAD_DIR / filename).write_bytes(prepared["raw"])
    chunks = prepared.get("_chunks")
    if chunks is None:
        try:
            chunks = parsing.build_chunks_from_texts(prepared["page_texts"])
        except Exception:
            chunks = []
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
    try:
        llm = getattr(ctx, "llm", None)
        response = call_llm_with_retry(
            [{"role": "user", "content": prompt}],
            model=getattr(llm, "model", None),
            temperature=0.3,
            api_key=getattr(llm, "api_key", None),
            base_url=getattr(llm, "base_url", None),
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        return f"版本差异分析暂不可用：{str(exc)[:120]}"


def _guess_arxiv(filename):
    m = re.match(r"(\d{4}\.\d{4,5})", filename)
    return m.group(1) if m else filename.replace(".pdf", "")


def _guess_version(filename):
    m = re.search(r"(v\d+)", filename)
    return m.group(1) if m else ""

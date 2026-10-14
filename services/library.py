"""文献库管理：入库、基于内容 embedding 的语义去重、版本管理与差异对比、删除。"""
import io
import math
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from core.llm import call_llm_with_retry
from services import parsing
from config import UPLOAD_DIR, PDF_DIR, STORAGE_DIR


PDF_BATCH_SIZE = 20
PDF_PAGE_TIMEOUT_SECONDS = 30
PDF_PROGRESS_INTERVAL = 50
# 扫描件 OCR 并发度：视觉模型调用是网络 I/O 密集型，串行会逐页累加耗时
# （话术 2 压测实测：15 页 111.87s，约 7.5s/页）。并发后近似降到 1/N。
# 取 3 而非更大：兼顾提速与该视觉接口的限流（超并发会触发 429 退避，反而更慢）。
OCR_PARALLEL = 3

# R9 官方测试 PDF 重建清单：scripts/setup_real_library.py 与 UI「一键重建文献库」
# 共用同一份元数据与同一套 rebuild_from_pdf_dir 代码路径，保证任何人可复现。
# 顺序即入库顺序：先 v7 后 v1，可在查重日志复现 v1/v7 语义查重命中。
OFFICIAL_PDF_METADATA = {
    "1706.03762v7.pdf": {
        "title": "Attention Is All You Need",
        "authors": "Ashish Vaswani; Noam Shazeer; Niki Parmar; Jakob Uszkoreit; "
                   "Llion Jones; Aidan N. Gomez; Lukasz Kaiser; Illia Polosukhin",
        "arxiv_id": "1706.03762", "version": "v7", "year": "2017",
    },
    "1706.03762v1.pdf": {
        "title": "Attention Is All You Need",
        "authors": "Ashish Vaswani; Noam Shazeer; Niki Parmar; Jakob Uszkoreit",
        "arxiv_id": "1706.03762", "version": "v1", "year": "2017",
    },
    "1810.04805v2.pdf": {
        "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "authors": "Jacob Devlin; Ming-Wei Chang; Kenton Lee; Kristina Toutanova",
        "arxiv_id": "1810.04805", "version": "v2", "year": "2018",
    },
    # 扫描件：无文本层，走多模态 OCR（需要有效视觉模型 Key）
    "1706.03762v1_img.pdf": {
        "title": "Attention Is All You Need",
        "authors": "Ashish Vaswani; Noam Shazeer; Niki Parmar; Jakob Uszkoreit",
        "arxiv_id": "1706.03762", "version": "v1", "year": "2017",
    },
    # 官方教材：541 页 / 22MB，分 20 页批 + 单页 30s 超时入库
    "UnderstandingDeepLearning_02_09_26_C.pdf": {
        "title": "Understanding Deep Learning",
        "authors": "Simon J.D. Prince", "arxiv_id": "", "version": "", "year": "2026",
    },
}
OFFICIAL_LIBRARY_PDFS = list(OFFICIAL_PDF_METADATA)


def cosine(a, b):
    a, b = np.asarray(a), np.asarray(b)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def _mark_embed_degraded(ctx, exc):
    """把 embedding 失败原因写到 ctx.embed_degraded_reason（首次失败即写入，后续不覆盖）。

    失败语义纪律：降级值（None / []）与「真的没有结果」在下游不可区分，所以降级
    必须同时留下人话原因，让 UI 层如实展示「哪一步降级了、后果是什么」，而不是
    静默返回降级值。读取/写入均**不依赖 Container.__init__ 初始化该字段**
    （services/state.py 由他人维护、禁改）：读走 getattr 默认 None，写走 try 兜底。
    """
    if getattr(ctx, "embed_degraded_reason", None):
        return                      # 只保留首次失败的原因，后续不覆盖
    try:
        detail = (str(exc) or type(exc).__name__)[:200]
    except Exception:                                     # noqa: BLE001
        detail = "未知错误"
    try:
        ctx.embed_degraded_reason = detail
    except Exception:                                     # noqa: BLE001
        pass


def _paper_embedding(ctx, full_text):
    # 取前 4000 字（覆盖摘要/引言/方法主体）做论文级向量
    try:
        return ctx.embed([full_text[:4000]])[0]
    except Exception as exc:
        # 版本匹配 / 跨版本去重是增强能力而非入库硬前提：embedding 失败不拦入库，
        # 返回降级值 None（下游已能处理，见 prepare_pdf 的 `if full_text else None`
        # 既有写法），但必须记录人话原因供 UI 展示——绝不静默 None。
        _mark_embed_degraded(ctx, exc)
        return None


def _paper_fingerprints(ctx, full_text, k=5, seg=1200):
    """从全文均匀采样 k 个内容片段做指纹向量（跨版本内容匹配的核心）。"""
    if not full_text or len(full_text) < seg:
        return []
    step = max(1, (len(full_text) - seg) // max(k - 1, 1))
    samples = [full_text[i * step: i * step + seg] for i in range(k)]
    if samples[-1] != full_text[-seg:]:
        samples.append(full_text[-seg:])  # 末尾（参考文献区）也取一指纹
    try:
        return ctx.embed(samples)
    except Exception as exc:
        # 同 _paper_embedding：降级为 [] + 记录人话原因，不静默返回空列表
        _mark_embed_degraded(ctx, exc)
        return []


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


def _version_key(value):
    """从 v1 / V7 / version 2 等字符串取版本号数字，取不到按 0 处理。"""
    match = re.search(r"v\s*(\d+)", str(value or ""), re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return 0
    match = re.search(r"(\d+)", str(value or ""))
    try:
        return int(match.group(1)) if match else 0
    except Exception:
        return 0


def _version_relation(new_value, old_value):
    """返回 (是否可比, 新版本是否更新)；版本号缺失时不可比。"""
    new_key, old_key = _version_key(new_value), _version_key(old_value)
    if not new_key or not old_key or new_key == old_key:
        return False, False
    return True, new_key > old_key


_BOILERPLATE_RE = re.compile(
    r"^(arxiv\s*:|provided proper attribution|preprint|under review|to appear|"
    r"copyright|all rights reserved|permission to reproduce|abstract\b)",
    re.IGNORECASE,
)

# ---------- 摘要定位用的预编译正则（原 extract_abstract 内联编译，提升为模块级常量） ----------
# 与原始内联写法 (?:?im) 等价的标志位，输出完全等价。
_ABSTRACT_LINE_RE = re.compile(r"^\s*abstract\s*[:\-–—.]?\s*$", re.IGNORECASE | re.MULTILINE)
_ABSTRACT_INLINE_RE = re.compile(r"\babstract\b\s*[:\-–—.\s]", re.IGNORECASE)
_ABSTRACT_ANY_RE = re.compile(r"\babstract\b", re.IGNORECASE)
_ABSTRACT_BOILER_RE = re.compile(
    r"^.{0,400}?(?:permission to reproduce|all rights reserved|"
    r"this paper is provided|copyright)\b.{0,200}?\n",
    re.IGNORECASE | re.DOTALL,
)
# 首页样板常跨行断句：以连接词/冠词结尾的行几乎不可能是标题。
# 注意：这里**不能**收录 for / of / to / in 等介词——学术标题里极其常见
# （"Transformers for Language Understanding"、"Attention for Vision"），
# 2026-10 R7 修复前正则含 for，导致 BERT 标题首行被整行丢弃。
_DANGLING_RE = re.compile(
    r"(?:\b(or|and|the|a|an|in|of|to|solely|this)\s*)$", re.IGNORECASE)
# 宽口径悬垂词：用于判断"标题是否跨行没说完、需要拼接下一行"。
# for/of/to/in/on 等介词只作为"需要合并"的信号，不作为"整行丢弃"的判据。
_DANGLING_LOOSE_RE = re.compile(
    r"(?:\b(or|and|the|a|an|solely|this|for|of|to|in|on|at|by|with|from|as|via|"
    r"using|towards?|into|over|under|per|than|through|without|within|upon)\s*)$",
    re.IGNORECASE,
)
# 作者/机构/邮箱行：绝不能并进标题
_AFFILIATION_RE = re.compile(
    r"@|\{|\}|(?i:\barxiv\b)|(?i:\b(?:university|institute|department|laboratories|"
    r"laboratory|inc\.|corp\.|ltd\.|college|school|center|centre|academy|research|"
    r"google|microsoft|facebook|deepmind|amazon|apple|mit|stanford)\b)",
    re.IGNORECASE,
)


def _title_case_ratio(line):
    """首字母大写的词占比：学术标题多为 Title Case，首页样板则是普通句子。

    仅用于给"悬垂行是否值得向后拼接"设一道门槛，避免把
    "This paper is provided solely" 这类样板残片与真正的标题拼在一起。
    """
    words = re.findall(r"[A-Za-z][\w'’\-]*", line)
    if not words:
        return 0.0
    return sum(1 for w in words if w[0].isupper()) / len(words)


def _is_title_continuation(line):
    """判断 line 能否作为被截断标题行的续行拼接进来。"""
    if not line or len(line) > 80:
        return False
    if _BOILERPLATE_RE.match(line):
        return False
    if line.endswith((".", "!", "?", ";", ":")):   # 自成一体的句子，不是续行
        return False
    if _AFFILIATION_RE.search(line):               # 作者/机构/邮箱行
        return False
    if re.match(r"(?i)^abstract\b", line):
        return False
    return True


def _merge_dangling_title(lines, index, max_extra=2):
    """悬垂行与其后续续行拼接；拼不出完整标题时返回 ''。

    只在**当前行确实以虚词结尾**时才向后拼接，因此不会把紧跟完整标题的
    作者/机构行误并进来（BERT 首行 "... Transformers for" + "Language Understanding"）。
    """
    parts = [lines[index]]
    for step in range(1, max_extra + 1):
        nxt_index = index + step
        if nxt_index >= len(lines):
            break
        nxt = lines[nxt_index]
        if not _is_title_continuation(nxt):
            break
        parts.append(nxt)
        merged = re.sub(r"\s+", " ", " ".join(parts)).strip(" .:;,-–—")
        if not _DANGLING_LOOSE_RE.search(merged):
            return merged
    return ""


def guess_title_from_text(full_text, fallback=""):
    """从正文最前面取一个像标题的完整行（不依赖文件名）。

    学术论文首页常有跨行标题（"BERT: Pre-training of ... / Language Understanding"）
    和版权样板（"Provided proper attribution ... / scholarly works."），因此这里
    会合并紧邻的续行并剔除样板行与以句号结尾的句子片段。

    悬垂行处理（2026-10 R7）：以虚词结尾的标题行说明跨行没说完，**先尝试向后
    拼接续行**；只有拼不出完整标题且以连词/冠词等强悬垂词结尾时，才判定为
    首页样板断句残片并丢弃。for/of/to 等介词在学术标题中极常见，绝不整行丢弃。
    """
    try:
        lines = []
        for raw_line in str(full_text or "").splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line:
                lines.append(line)
        for index, line in enumerate(lines[:40]):
            if len(line) < 8 or len(line) > 160:
                continue
            if _BOILERPLATE_RE.match(line):
                continue
            if line.endswith((".", "!", "?", ",")):   # 句子片段，不是标题
                continue
            title = line
            if _DANGLING_LOOSE_RE.search(line):
                # 标题跨行没说完：优先拼接续行，而不是把整行丢掉。
                # 门槛：仅 Title Case 的行才拼接（样板句子残片大小写比例很低）
                merged = (_merge_dangling_title(lines, index)
                          if _title_case_ratio(line) >= 0.5 else "")
                if merged:
                    title = merged
                elif _DANGLING_RE.search(line):
                    continue    # 强悬垂且拼不出完整标题 → 首页样板残片
            elif line.endswith((":", "-", "–", "—")) and index + 1 < len(lines):
                nxt = lines[index + 1]
                if len(nxt) <= 80 and not nxt.endswith((".", ":")):
                    title = f"{line} {nxt}".replace(" :", ":").strip()
            title = re.sub(r"\s+", " ", title).strip(" .:;,-–—")
            if 8 <= len(title) <= 160 and not _DANGLING_RE.search(title):
                return title
    except Exception:
        pass
    return fallback


def extract_abstract(full_text, limit=600):
    """尽力定位 Abstract 之后的正文；无 Abstract 标记时跳过首页版权声明再取。"""
    text = str(full_text or "")
    head = text[:2500]
    # 学术论文首页的 "Abstract" 通常单独成行或后接换行/冒号；宽松匹配多种排版
    matches = list(_ABSTRACT_LINE_RE.finditer(head))
    if not matches:
        matches = list(_ABSTRACT_INLINE_RE.finditer(head))
    if not matches:
        matches = list(_ABSTRACT_ANY_RE.finditer(head))
    match = matches[-1] if matches else None
    if match:
        body = text[match.end():match.end() + limit]
    else:
        # 无 Abstract 标记：跳过以版权/许可声明开头的首页样板
        stripped = _ABSTRACT_BOILER_RE.sub("", text, count=1)
        body = stripped if len(stripped.strip()) > limit // 2 else text
        body = body[:limit]
    return re.sub(r"\s+", " ", body).strip()


def duplicate_advice(ctx, dup_name, new_version=None, new_title=""):
    """为命中查重的结果给出保留建议。"""
    if not dup_name:
        return ""
    old = ctx.papers.get(dup_name) or {}
    old_version = old.get("version") or _guess_version(dup_name)
    comparable, newer = _version_relation(new_version, old_version)
    if comparable and not newer:
        return (f"库中已有较新版本《{old.get('title', dup_name)}》（{old_version}），"
                f"当前上传为 {new_version}；建议保留库中较新版本，或覆盖为旧版后重新核对引用。")
    if comparable and newer:
        return (f"库中已有较旧版本《{old.get('title', dup_name)}》（{old_version}），"
                f"当前上传为 {new_version}；建议覆盖旧版以保持时效性。")
    return (f"库中已有内容高度相似文献《{old.get('title', dup_name)}》"
            f"（{old_version or '版本未知'}），请确认是否为重复入库；"
            "若为同一论文建议使用「覆盖旧版」避免检索结果重复。")


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
        "fingerprints": [],         "dup_name": None, "dup_sim": 0.0,
        "dup_evidence": "", "raw": raw, "peak_mem_mb": None,
        "dup_advice": "", "title": "", "version": _guess_version(filename), "ocr_error": str(error or ""),
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


def _close_batch_doc(doc_cache):
    """关闭批次复用的文档实例。

    若仍有超时线程持有该实例，则移交给线程自行关闭（owned=True），
    避免解析线程在已关闭的文档上继续取页。
    """
    try:
        doc = doc_cache.get("doc") if isinstance(doc_cache, dict) else None
    except Exception:
        doc = None
    if doc is None:
        return
    try:
        if doc_cache.get("abandoned"):
            return
    except Exception:
        return
    try:
        doc.close()
    except Exception:
        pass


def _extract_page_from_raw(raw, page_index, use_ocr, llm, doc_cache=None):
    """解析单页；同一批次复用一个独立文档实例，避免超时线程与主 doc 并发访问。"""
    local_doc = None
    owned = False
    try:
        if isinstance(doc_cache, dict) and not doc_cache.get("abandoned"):
            local_doc = doc_cache.get("doc")
            if local_doc is None:
                local_doc = parsing.open_doc(raw)
                doc_cache["doc"] = local_doc
        else:
            # 批次已因单页超时被放弃（abandoned）：绝不再与卡死的超时线程共用
            # 同一文档实例，改为逐页开独立文档（owned=True，用完即关）。
            # 2026-10 R9 修复：旧代码在 abandoned 后仍复用 doc_cache["doc"]，
            # 后续页与超时线程并发访问同一 PyMuPDF 文档，可能整批连环失败。
            local_doc = parsing.open_doc(raw)
            owned = True
        page = local_doc[page_index]
        if use_ocr:
            text = parsing.ocr_page_text(page, llm)
            return text, {"lines": [], "text": text}
        text, blocks = parsing.extract_page_text_column_aware(page, return_blocks=True)
        return text, parsing.heading_page_data(blocks, text)
    finally:
        # 批次已被放弃（发生超时）时由本线程负责关闭，避免访问已关闭文档
        if owned or (isinstance(doc_cache, dict) and doc_cache.get("abandoned")):
            if local_doc is not None:
                try:
                    local_doc.close()
                except Exception:
                    pass


def _extract_page_with_timeout(raw, page_index, use_ocr, llm, timeout_seconds,
                               doc_cache=None):
    """用守护线程执行单页解析；超时立即返回并由调用方跳过该页。"""
    result_queue = queue.Queue(maxsize=1)

    def worker():
        try:
            result_queue.put(
                (True, _extract_page_from_raw(raw, page_index, use_ocr, llm, doc_cache))
            )
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
            try:
                doc_cache["abandoned"] = True       # 交权给线程关闭，防悬空文档
            except Exception:
                pass
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


def _validate_prepared(prepared, expected_pages, extracted_pages, total_chars,
                       ocr_error=""):
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
                detail = ("（%s）" % ocr_error[:120]) if ocr_error else ""
                prepared["error"] = ("检测为扫描件，但当前未启用/不可用 OCR，全部页面均未提取到文本，"
                                     "未入库。请配置支持视觉的模型/有效 Key 后重试。" + detail)
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
        use_ocr = bool(
            scanned
            and getattr(ctx.llm, "available", False)
            and parsing.ocr_supported(getattr(ctx, "llm", None))
        )
    except Exception:
        use_ocr = False
    ocr_error = ""

    page_texts = []
    heading_pages = []
    streamed_chunks = []
    failed_pages = []
    full_text_buffer = io.StringIO()

    for batch_start in range(0, total, PDF_BATCH_SIZE):
        batch_end = min(batch_start + PDF_BATCH_SIZE, total)
        batch_texts = []
        doc_cache = {}
        page_indexes = list(range(batch_start, batch_end))
        # 小文档（≤100 页）每页上报进度，保证扫描件 OCR 等慢页也有真实进度；
        # 大文档保持每 50 页，避免高频回调拖慢 UI。（R9）
        # 话术 3：大文档进度粒度由固定 50 页改为自适应约 50 档
        # （541 页实测由 11 次回调提升到约 54 次，几乎不再出现长时间无反馈的观感卡顿）。
        interval = 1 if total <= 2 * PDF_PROGRESS_INTERVAL else max(10, total // 50)
        results = {}
        done_cnt = 0

        def _handle(pi, text, heading_data, error):
            """单页结果统一处理：错误归类 + 进度上报，按页号存回 results 保证顺序。"""
            nonlocal ocr_error, done_cnt
            if not error and parsing.is_ocr_error_text(text):
                error = str(text).strip("[]")[:300]
                if not ocr_error:
                    ocr_error = str(text)
            if error:
                reason = str(error)[:300]
                failed_pages.append({"page": pi + 1, "error": reason})
                _notify_page_error(on_page_error, pi, reason)
                text, heading_data = "", {"lines": [], "text": ""}
            results[pi] = (text or "", heading_data or {"lines": [], "text": ""})
            done_cnt += 1
            shown = min(batch_start + done_cnt, total)
            if done_cnt % interval == 0 or done_cnt == len(page_indexes):
                _notify_pdf_progress(progress_cb, shown, total,
                                     f"已处理 {shown}/{total} 页")

        if use_ocr and len(page_indexes) > 1:
            # 扫描件 OCR 并发（话术 3）：视觉调用是网络 I/O 密集型，串行逐页累加。
            # 每页传 doc_cache=None，让各线程开独立文档实例，
            # 避免多线程并发访问同一个 PyMuPDF 文档（非线程安全）。
            with ThreadPoolExecutor(max_workers=OCR_PARALLEL) as pool:
                futures = {
                    pool.submit(_extract_page_with_timeout, raw, pi, use_ocr,
                                getattr(ctx, "llm", None), timeout_per_page, None): pi
                    for pi in page_indexes
                }
                for fut in as_completed(futures):
                    pi = futures[fut]
                    try:
                        text, heading_data, error = fut.result()
                    except Exception as exc:
                        text, heading_data, error = "", {"lines": [], "text": ""}, str(exc)[:300]
                    _handle(pi, text, heading_data, error)
        else:
            for page_index in page_indexes:
                text, heading_data, error = _extract_page_with_timeout(
                    raw, page_index, use_ocr, getattr(ctx, "llm", None), timeout_per_page,
                    doc_cache=doc_cache,
                )
                _handle(page_index, text, heading_data, error)

        for pi in page_indexes:
            text, heading_data = results[pi]
            batch_texts.append(text)
            heading_pages.append(heading_data)
            if text:
                if full_text_buffer.tell() > 0:
                    full_text_buffer.write("\n\n")
                full_text_buffer.write(text)

        _close_batch_doc(doc_cache)
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
        invalid = _validate_prepared(prepared_base, total, extracted_pages, total_chars,
                                     ocr_error=ocr_error)
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
    title_guess = guess_title_from_text(full_text, "")
    new_version = _guess_version(filename)
    try:
        dup_name, dup_sim, dup_ev = (
            check_duplicate(ctx, embedding, arxiv_id=aid, fingerprints=fingerprints,
                            title=title_guess)
            if embedding is not None else (None, 0.0, "")
        )
    except Exception:
        dup_name, dup_sim, dup_ev = None, 0.0, ""
    try:
        dup_advice = duplicate_advice(ctx, dup_name, new_version, title_guess) if dup_name else ""
    except Exception:
        dup_advice = ""
    peak_mem_mb = _sample_process_memory(process, peak_mem_mb)
    return {
        "filename": filename, "scanned": scanned, "page_count": len(page_texts),
        "page_texts": page_texts, "full_text": full_text, "embedding": embedding,
        "fingerprints": fingerprints,
        "dup_name": dup_name, "dup_sim": dup_sim, "dup_evidence": dup_ev, "raw": raw,
        "dup_advice": dup_advice, "title": title_guess, "version": new_version,
        "ocr_error": ocr_error,
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
    title = title or (prepared.get("title") or "").strip() or filename
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
        "filename": filename, "title": title,
        "authors": authors or "", "year": year or "",
        "arxiv_id": arxiv_id, "version": version,
        "page_count": prepared["page_count"], "scanned": prepared["scanned"],
        "added_at": time.strftime("%Y-%m-%d %H:%M"),
        "abstract": extract_abstract(prepared["full_text"]),
        "toc": prepared.get("toc") or [],
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


def _real_pdf_page_count(filename):
    """按 上传目录 → 官方 pdf 目录 解析同名 PDF 的真实页数；找不到返回 0。

    2026-10 R9：add_text_corpus（内置演示语料）原先用 ``full_text.split("\\n\\n")``
    的段数估算页数（v7 得 15，与真实 PDF 页数无关），导致页数字失真。现在只要
    磁盘上存在同名 PDF（storage/uploads 或 pdf/），一律以 PDF 真实页数为准。
    """
    try:
        for base in (UPLOAD_DIR, PDF_DIR):
            try:
                candidate = base / filename
            except Exception:
                continue
            try:
                if not candidate.exists():
                    continue
                doc = parsing.open_doc(str(candidate))
                try:
                    return int(doc.page_count)
                finally:
                    doc.close()
            except Exception:
                continue
    except Exception:
        pass
    return 0


def add_text_corpus(ctx, filename, full_text, meta=None):
    """用于内置演示语料：直接以文本入库（语义去重同样生效）。"""
    meta = meta or {}
    embedding = _paper_embedding(ctx, full_text) if full_text else None
    fingerprints = _paper_fingerprints(ctx, full_text) if full_text else []
    dup_name, dup_sim, dup_ev = (check_duplicate(ctx, embedding, arxiv_id=meta.get("arxiv_id"),
                                                 fingerprints=fingerprints, title=meta.get("title"))
                                 if embedding is not None else (None, 0.0, ""))
    # 页数优先取同名真实 PDF 的页数；无 PDF 时才退回归一化段数估算（R9 T3）
    page_count = _real_pdf_page_count(filename) or \
        len([p for p in full_text.split("\n\n") if p.strip()]) or 1
    prepared = {
        "filename": filename, "scanned": False,
        "page_count": page_count,
        "page_texts": [full_text], "full_text": full_text, "embedding": embedding,
        "fingerprints": fingerprints,
        "dup_name": dup_name, "dup_sim": dup_sim, "dup_evidence": dup_ev,
    }
    return commit(ctx, prepared, title=meta.get("title"), authors=meta.get("authors"),
                  arxiv_id=meta.get("arxiv_id"), version=meta.get("version"), year=meta.get("year"))


def _reset_library(ctx):
    """清空文献库（papers/chunks/likes + 向量库），供全新重建使用。"""
    try:
        ids = ctx.collection.get().get("ids", [])
        if ids:
            ctx.collection.delete(ids=ids)
    except Exception:
        pass
    ctx.papers, ctx.paper_order, ctx.chunks, ctx.likes = {}, [], [], {}
    ctx.rebuild_bm25()
    ctx.save_library()


def _backup_storage(storage_dir=None):
    """重建前把 library.json 与 chroma/ 备份到 storage/backup_<时间戳>/（最佳努力）。

    chroma/ 可能被进程持有导致拷贝失败——不影响重建（向量按 id 覆盖重写），
    失败原因写入 warnings 供调用方展示。
    """
    import shutil
    from pathlib import Path

    base = Path(storage_dir) if storage_dir else STORAGE_DIR
    backup_dir = base / ("backup_" + time.strftime("%Y%m%d_%H%M%S"))
    copied, warnings = [], []
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return "", [], [f"创建备份目录失败：{exc}"]
    lib = base / "library.json"
    if lib.exists():
        try:
            shutil.copy2(lib, backup_dir / "library.json")
            copied.append("library.json")
        except Exception as exc:
            warnings.append(f"library.json 备份失败：{exc}")
    chroma = base / "chroma"
    if chroma.exists():
        try:
            shutil.copytree(chroma, backup_dir / "chroma")
            copied.append("chroma/")
        except Exception as exc:
            warnings.append(f"chroma/ 备份失败（向量将按 id 覆盖重写，不影响重建）：{exc}")
    return str(backup_dir), copied, warnings


def library_health(ctx):
    """文献库健康诊断（首跑自愈 R9）：识别「空库 / 内容退化」两种不可用状态。

    退化判定：有文献但每篇 chunk 数中位数 < 3，或超过一半 chunk 集中在第 1 页。
    典型成因是演示文本语料覆盖了真实 PDF 入库结果——用户只能检索到首页片段，
    问答/图谱/写作/对比质量极差。返回 dict 供 UI 展示醒目提示与一键重建入口。
    """
    try:
        papers = list(getattr(ctx, "files", []) or [])
    except Exception:
        papers = []
    per_paper = {}
    for fn in papers:
        try:
            per_paper[fn] = sum(1 for c in ctx.chunks if c.get("source") == fn)
        except Exception:
            per_paper[fn] = 0
    total_chunks = len(ctx.chunks)
    try:
        page1 = sum(1 for c in ctx.chunks if int(c.get("page") or 1) <= 1)
    except Exception:
        page1 = total_chunks
    ratio1 = (page1 / total_chunks) if total_chunks else 0.0
    counts = sorted(per_paper.values())
    if counts:
        mid = len(counts) // 2
        median = counts[mid] if len(counts) % 2 else (counts[mid - 1] + counts[mid]) / 2.0
    else:
        median = 0.0
    reasons = []
    if not papers:
        reasons.append("文献库为空")
    else:
        if median < 3:
            reasons.append(f"每篇文献 chunk 数中位数仅 {median:g}（健康库应 ≥ 3）")
        if total_chunks and ratio1 > 0.5:
            reasons.append(f"{ratio1:.0%} 的 chunk 集中在第 1 页，后续页面内容缺失")
    return {
        "papers": len(papers), "chunks": total_chunks,
        "chunks_per_paper": per_paper, "median_chunks_per_paper": median,
        "page1_ratio": ratio1,
        "empty": not papers, "degraded": bool(papers) and bool(reasons),
        "unhealthy": (not papers) or bool(reasons),
        "reasons": reasons,
    }


def rebuild_from_pdf_dir(ctx, pdf_dir=None, filenames=None, reset_first=False,
                         progress_cb=None, page_error_cb=None):
    """从 PDF 目录重建文献库：UI「一键重建」与 scripts/setup_real_library.py 的唯一代码路径。

    - 仅重建 ``filenames``（默认官方 5 件，见 OFFICIAL_LIBRARY_PDFS），按
      OFFICIAL_PDF_METADATA 补标题/作者等元数据，其余字段仍按文件名/正文推断；
    - 同名文献直接覆盖（commit 先删同名旧块再 upsert），不影响用户其它上传；
    - ``reset_first=True`` 先清空全库并自动备份 storage/（脚本全新重建用）；
    - 进度经 ``progress_cb(done, total, msg)`` 上报（done/total 为跨文件累计页数，
      预扫描各 PDF 真实页数得到总数）；单页失败经 ``page_error_cb(page, reason)`` 上报；
    - 返回实测报告：每篇页数/是否扫描件/chunk 数/覆盖页范围/失败页/耗时。
    """
    from pathlib import Path

    base_dir = Path(pdf_dir) if pdf_dir else PDF_DIR
    names = list(filenames) if filenames else list(OFFICIAL_LIBRARY_PDFS)
    report = {"results": [], "skipped": [], "backup": "", "backup_files": [],
              "backup_warnings": [], "elapsed": 0.0, "total_chunks": 0, "ok": True}
    t0 = time.time()

    if reset_first:
        backup_dir, copied, warnings = _backup_storage(STORAGE_DIR)
        report["backup"], report["backup_files"], report["backup_warnings"] = \
            backup_dir, copied, warnings
        _reset_library(ctx)

    # 预扫描各 PDF 真实页数：累计进度条需要真实总数（打开失败在此阶段跳过）
    page_totals = {}
    for name in names:
        path = base_dir / name
        if not path.exists():
            report["skipped"].append({"filename": name, "error": f"文件不存在：{path}"})
            report["ok"] = False
            continue
        try:
            doc = parsing.open_doc(str(path))
            try:
                page_totals[name] = int(doc.page_count)
            finally:
                doc.close()
        except Exception as exc:
            report["skipped"].append({"filename": name, "error": f"打开失败：{exc}"})
            report["ok"] = False
    grand_total = max(sum(page_totals.values()), 1)
    done_pages = 0

    for name in names:
        if name not in page_totals:
            continue
        path = base_dir / name
        meta = OFFICIAL_PDF_METADATA.get(name, {})
        file_total = page_totals[name]
        file_t0 = time.time()
        failed_here = []

        def _cb(done, total, msg, _name=name, _base=done_pages, _ftotal=file_total,
                _t0=file_t0):
            _notify_pdf_progress(
                progress_cb, _base + done, grand_total,
                f"{_name} {msg}（本篇 {done}/{_ftotal} 页，已用 {time.time() - _t0:.0f}s，"
                f"失败 {len(failed_here)} 页）")

        def _perr(page_index, reason, _name=name):
            failed_here.append({"page": page_index + 1, "error": str(reason)[:200]})
            _notify_page_error(page_error_cb, page_index, reason)

        prepared = prepare_pdf(ctx, path=str(path), filename=name,
                               progress_cb=_cb, on_page_error=_perr)
        done_pages += file_total
        elapsed = time.time() - file_t0
        if prepared.get("error"):
            report["results"].append({
                "filename": name, "ok": False, "error": prepared.get("error", ""),
                "reason": prepared.get("reason", ""),
                "page_count": int(prepared.get("page_count") or 0),
                "scanned": bool(prepared.get("scanned")), "chunks": 0, "pages": [],
                "failed_pages": prepared.get("failed_pages") or failed_here,
                "elapsed": elapsed, "dup_name": None, "dup_sim": 0.0,
            })
            report["ok"] = False
            continue
        paper = commit(ctx, prepared, title=meta.get("title"), authors=meta.get("authors"),
                       arxiv_id=meta.get("arxiv_id"), version=meta.get("version"),
                       year=meta.get("year"))
        chunks = [c for c in ctx.chunks if c.get("source") == name]
        pages = sorted({int(c.get("page") or 0) for c in chunks}
                       | {int(c.get("page_end") or 0) for c in chunks})
        report["results"].append({
            "filename": name, "ok": True, "error": "",
            "reason": "", "page_count": int(paper.get("page_count") or 0),
            "scanned": bool(paper.get("scanned")), "chunks": len(chunks),
            "pages": pages, "failed_pages": prepared.get("failed_pages") or failed_here,
            "elapsed": elapsed, "dup_name": prepared.get("dup_name"),
            "dup_sim": float(prepared.get("dup_sim") or 0.0),
        })

    report["elapsed"] = time.time() - t0
    report["total_chunks"] = len(ctx.chunks)
    return report


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
    # 话术 7：chunk 正文向量缓存按 (id, 长度) 索引，删文后同 id 可能被后续
    # 上传的论文复用，必须失效，否则新 chunk 会拿到旧论文的向量。
    try:
        from services.rag import clear_embed_cache
        clear_embed_cache()
    except Exception:
        pass


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


def _abstract_of(ctx, filename, chunks, limit=600):
    """优先用入库时保存的摘要（基于全文抽取，标题行可能被切块过滤掉）。"""
    stored = str((ctx.papers.get(filename) or {}).get("abstract") or "").strip()
    if stored:
        return stored
    return extract_abstract("\n".join(str(item.get("text", "")) for item in chunks), limit)


def _version_stats(ctx, files, toc_by_file=None):
    """离线可复现的版本对比统计（不依赖 LLM）。"""
    toc_by_file = toc_by_file or {}
    stats = {}
    for filename in files:
        chunks = [item for item in ctx.chunks if item["source"] == filename]
        text = "\n".join(str(item.get("text", "")) for item in chunks)
        pages = [int(item.get("page_end") or item.get("page") or 0) for item in chunks]
        paper = ctx.papers.get(filename) or {}
        chapters = _titles_of(toc_by_file.get(filename) or paper.get("toc"))
        stats[filename] = {
            "chunks": len(chunks),
            "chars": len(text),
            "pages": max(pages) if pages else int(paper.get("page_count") or 0),
            "refs": len(set(re.findall(r"\[\d{1,3}\]|arXiv:\d{4}\.\d{4,5}", text))),
            "abstract": _abstract_of(ctx, filename, chunks),
            "chapters": _titles_of(toc_by_file.get(filename) or paper.get("toc")),
        }
    return stats


def _titles_of(toc, level=1, limit=8):
    """从 [level, title, page] 形式的 toc 中取指定层级标题。"""
    out = []
    for item in toc or []:
        try:
            if len(item) > 2 and int(item[0]) == level:
                title = str(item[1]).strip()
                if title:
                    out.append(title)
        except Exception:
            continue
    return out[:limit]


def _offline_version_report(ctx, files, toc_by_file=None):
    toc_by_file = toc_by_file or {}
    stats = _version_stats(ctx, files, toc_by_file=toc_by_file)
    rows = ["| 对比维度 | " + " | ".join(f"版本{i + 1}（{name}）" for i, name in enumerate(files))
            + " | 差异说明 |"]
    rows.append("|" + "---|" * (len(files) + 2))

    def row(dimension, getter, note=""):
        cells = [str(getter(name)).replace("|", "/") or "-" for name in files]
        rows.append(f"| {dimension} | " + " | ".join(cells) + f" | {note} |")

    row("文档规模（页数）", lambda name: stats[name]["pages"], "按 chunk 的最大页码统计")
    row("切块数", lambda name: stats[name]["chunks"], "入库 chunk 数量")
    row("正文字数", lambda name: f"{stats[name]['chars']:,}", "全部 chunk 字符合计")
    row("参考文献条目", lambda name: stats[name]["refs"], "按 [n] 与 arXiv 编号去重计数")
    row("摘要首句", lambda name: stats[name]["abstract"][:80], "摘要起始 80 字")
    for index in range(max((len(stats[name]["chapters"]) for name in files), default=0)):
        cells = []
        for name in files:
            chapters = stats[name]["chapters"]
            cells.append(str(chapters[index]).replace("|", "/") if index < len(chapters) else "-")
        rows.append(f"| 第 {index + 1} 个一级章节 | " + " | ".join(cells) + " | 章节标题逐项对比 |")
    return "\n".join(rows)


def version_diff(ctx, files):
    """让 LLM 以中文表格总结同一论文不同版本差异。

    LLM 不可用（无 Key / 网络受限 / 返回错误占位）时，降级为**离线统计表**：
    页数、切块数、正文字数、参考文献条目、摘要首句、一级章节逐项对比，
    保证官方 v1/v7 用例在任何环境下都能输出可复核的差异。
    """
    texts = []
    for fn in files:
        t = "\n".join(c["text"] for c in ctx.chunks if c["source"] == fn)
        texts.append(f"【{fn}】\n{t[:3500]}")
    headers = " | ".join([f"版本{i+1}" for i in range(len(files))])
    offline = _offline_version_report(ctx, files)
    prompt = f"""请对比同一论文的 {len(files)} 个版本，用中文 Markdown 表格总结差异。
表头列：对比维度 | {headers} | 差异说明
对比维度至少包含：标题/作者、摘要、方法、实验数据(BLEU/参数量等)、参考文献。全部用中文。
以下是已离线统计的客观数据，请直接采用，不要编造：
{offline}

两版正文前 3500 字：
{chr(10).join(texts)}
只输出 Markdown 表格。"""
    try:
        llm = getattr(ctx, "llm", None)
        if not getattr(llm, "available", False):
            raise RuntimeError("当前 Provider 不可用（可能缺少有效 API Key）")
        response = call_llm_with_retry(
            [{"role": "user", "content": prompt}],
            model=getattr(llm, "model", None),
            temperature=0.3,
            api_key=getattr(llm, "api_key", None),
            base_url=getattr(llm, "base_url", None),
        )
        content = (response.choices[0].message.content or "").strip()
        if not content or "【LLM 暂时不可用" in content or "错误摘要" in content:
            raise RuntimeError(content[:80] or "LLM 返回空结果")
        return ("#### 离线客观统计（可直接复核）\n\n" + offline
                + "\n\n#### 大模型语义对比\n\n" + content)
    except Exception:
        return ("#### 离线客观统计（LLM 不可用，已降级为离线对比）\n\n" + offline)


def _guess_arxiv(filename):
    m = re.match(r"(\d{4}\.\d{4,5})", filename)
    return m.group(1) if m else filename.replace(".pdf", "")


def _guess_version(filename):
    m = re.search(r"(v\d+)", filename)
    return m.group(1) if m else ""

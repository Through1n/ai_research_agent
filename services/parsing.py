"""PDF 解析引擎：扫描件检测/OCR、版面文本、语义切块、标题树/表格/图表/公式/参考文献抽取。"""
import base64
import io
import re
from collections import Counter

import pymupdf as fitz
from PIL import Image

from config import CHUNK_SIZE, CHUNK_OVERLAP
from core.llm import call_llm_with_retry


_COLUMN_MIN_SEPARATION_RATIO = 0.18
# 双栏判定的必要条件阈值（2026-10 R1 修复：单栏论文被大规模误判为双栏）
_COLUMN_MIN_BLOCKS_PER_SIDE = 3      # 左右两侧各自至少要有这么多文本块
_COLUMN_STRADDLE_MIN_OVERLAP = 5.0   # 块横跨 split_x 每侧重叠超过该 pt 数即否决双栏
_COLUMN_GUTTER_TOL = 3.0             # 中缝空白带采样时块边缘的容差(pt)
_COLUMN_GUTTER_MIN_RATIO = 0.6       # split_x 处竖直空白带占比必须超过该值
_COLUMN_MIN_WIDTH_RATIO = 0.3        # 较窄栏宽/较宽栏宽 不能低于该值
_COLUMN_GUTTER_SAMPLES = 60
_CAPTION_RE = re.compile(
    r"^\s*(?:figure|fig\.?|图)\s*[A-Za-z]?\s*\d+(?:[.-]\d+)?[A-Za-z]?\b",
    re.IGNORECASE,
)
_HEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})[.)]?\s+\S")
_HEADING_CN_RE = re.compile(r"^\s*第\s*([一二三四五六七八九十百零〇\d]+)\s*(章|节|篇|部分)\s*\S*")


def open_doc(path_or_bytes, filetype="pdf"):
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return fitz.open(stream=bytes(path_or_bytes), filetype=filetype)
    return fitz.open(path_or_bytes)


def detect_scanned(doc, sample=3):
    try:
        chars = sum(len(doc[i].get_text().strip()) for i in range(min(sample, doc.page_count)))
        return chars < 100
    except Exception:
        return False


def is_ocr_error_text(text):
    """判断文本是否为 OCR 失败/降级的占位符（不能当作正文入库）。"""
    text = (text or "").strip()
    return text.startswith("[OCR") or text.startswith("[扫描件 OCR 降级提示]")


def _line_record(line):
    """把 PyMuPDF dict line 压缩为标题推断所需的轻量元数据。"""
    try:
        spans = line.get("spans") or []
        text = "".join(str(span.get("text", "")) for span in spans).strip()
        sizes = [float(span.get("size", 0.0) or 0.0) for span in spans if span.get("text", "").strip()]
        flags = 0
        fonts = []
        for span in spans:
            flags |= int(span.get("flags", 0) or 0)
            font = str(span.get("font", "") or "")
            if font:
                fonts.append(font)
        font_text = " ".join(fonts)
        return {
            "text": text,
            "bbox": tuple(line.get("bbox") or (0.0, 0.0, 0.0, 0.0)),
            "size": max(sizes, default=0.0),
            "flags": flags,
            "font": font_text,
            "bold": bool(flags & 16) or "bold" in font_text.lower(),
        }
    except Exception:
        return {"text": "", "bbox": (0.0, 0.0, 0.0, 0.0), "size": 0.0,
                "flags": 0, "font": "", "bold": False}


def _dict_text_blocks(page):
    """读取带 bbox、行和字体信息的文本块；失败时返回空列表。"""
    try:
        page_dict = page.get_text("dict") or {}
        blocks = []
        for order, block in enumerate(page_dict.get("blocks") or []):
            if block.get("type") != 0:
                continue
            lines = [_line_record(line) for line in (block.get("lines") or [])]
            lines = [line for line in lines if line["text"]]
            if not lines:
                continue
            lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
            text = "\n".join(line["text"] for line in lines).strip()
            if not text:
                continue
            bbox = tuple(block.get("bbox") or (0.0, 0.0, 0.0, 0.0))
            blocks.append({
                "bbox": bbox,
                "text": text,
                "block_no": int(block.get("number", order) if block.get("number") is not None else order),
                "type": 0,
                "order": order,
                "lines": lines,
                "column": 0,
            })
        return blocks
    except Exception:
        return []


def _gutter_free_ratio(bboxes, split_x, y_lo, y_hi, tol=_COLUMN_GUTTER_TOL,
                       samples=_COLUMN_GUTTER_SAMPLES):
    """沿 y 采样统计 split_x 处竖直空白带占比：无任何块覆盖该竖直线的采样点比例。"""
    try:
        if y_hi - y_lo <= 1.0 or samples <= 0:
            return 0.0
        free = 0
        for i in range(samples):
            y = y_lo + (y_hi - y_lo) * (i + 0.5) / samples
            covered = False
            for x0, y0, x1, y1 in bboxes:
                if y0 - 0.5 <= y <= y1 + 0.5 and (x0 - tol) <= split_x <= (x1 + tol):
                    covered = True
                    break
            if not covered:
                free += 1
        return free / samples
    except Exception:
        return 0.0


def _detect_two_columns(page, blocks):
    """用一维双峰聚类检测双栏，返回 ``(is_two_column, split_x)``。

    判定为双栏必须同时满足以下必要条件（任一不满足即判单栏）：
    1) 左右两侧各有 >= 3 个文本块；
    2) 横跨否决：任何文本块（含被剔除出候选集的"宽块"）的 bbox 横跨候选
       split_x 且每侧重叠 > 5pt，即判单栏——单栏论文的长正文行必然横跨中线，
       这是区分单/双栏最可靠的证据；
    3) split_x 附近存在连续竖直空白带（沿候选块覆盖的 y 区间采样，无块覆盖
       比例 > 0.6）；
    4) 左右栏宽度不能过于悬殊（窄栏/宽栏 >= 0.3）且两栏 y 区间有重叠。
    """
    try:
        width = float(page.rect.width)
        if width <= 0:
            return False, width / 2.0
        candidates = []
        for block in blocks:
            x0, y0, x1, y1 = block["bbox"]
            block_width = max(0.0, x1 - x0)
            if block_width <= 0 or block_width > width * 0.62 or not block["text"].strip():
                continue
            candidates.append((block, (x0 + x1) / 2.0))
        if len(candidates) < _COLUMN_MIN_BLOCKS_PER_SIDE * 2:
            return False, width / 2.0

        centers = [center for _, center in candidates]
        left_mean, right_mean = min(centers), max(centers)
        for _ in range(12):
            left_group = [c for c in centers if abs(c - left_mean) <= abs(c - right_mean)]
            right_group = [c for c in centers if abs(c - left_mean) > abs(c - right_mean)]
            if not left_group or not right_group:
                return False, width / 2.0
            new_left = sum(left_group) / len(left_group)
            new_right = sum(right_group) / len(right_group)
            if abs(new_left - left_mean) + abs(new_right - right_mean) < 0.5:
                left_mean, right_mean = new_left, new_right
                break
            left_mean, right_mean = new_left, new_right
        if left_mean > right_mean:
            left_mean, right_mean = right_mean, left_mean
        split_x = (left_mean + right_mean) / 2.0
        left_blocks = [block for block, center in candidates if center < split_x]
        right_blocks = [block for block, center in candidates if center >= split_x]
        if len(left_blocks) < _COLUMN_MIN_BLOCKS_PER_SIDE or len(right_blocks) < _COLUMN_MIN_BLOCKS_PER_SIDE:
            return False, width / 2.0
        if right_mean - left_mean < width * _COLUMN_MIN_SEPARATION_RATIO:
            return False, width / 2.0
        if left_mean >= width * 0.48 or right_mean <= width * 0.52:
            return False, width / 2.0
        if not width * 0.28 <= split_x <= width * 0.72:
            return False, width / 2.0

        left_y0 = min(block["bbox"][1] for block in left_blocks)
        left_y1 = max(block["bbox"][3] for block in left_blocks)
        right_y0 = min(block["bbox"][1] for block in right_blocks)
        right_y1 = max(block["bbox"][3] for block in right_blocks)
        vertical_overlap = min(left_y1, right_y1) - max(left_y0, right_y0)
        if vertical_overlap <= 0:
            return False, width / 2.0

        # 用两栏真实边缘的中点重估 split_x（比聚类中心中点更贴近实际中缝）
        left_edge = max(block["bbox"][2] for block in left_blocks)
        right_edge = min(block["bbox"][0] for block in right_blocks)
        if right_edge > left_edge:
            split_x = (left_edge + right_edge) / 2.0

        # 必要条件：横跨否决（对全部块生效，含被剔除出候选集的宽块/跨栏块）。
        # 旧版在剔除宽块后不再检查它们是否横跨 split_x，导致单栏论文被误判双栏。
        for block in blocks:
            x0, _, x1, _ = block["bbox"]
            if min(split_x - x0, x1 - split_x) > _COLUMN_STRADDLE_MIN_OVERLAP:
                return False, width / 2.0

        # 必要条件：split_x 处存在连续竖直空白带
        cand_y0 = min(block["bbox"][1] for block, _ in candidates)
        cand_y1 = max(block["bbox"][3] for block, _ in candidates)
        bboxes = [block["bbox"] for block in blocks]
        if _gutter_free_ratio(bboxes, split_x, cand_y0, cand_y1) <= _COLUMN_GUTTER_MIN_RATIO:
            return False, width / 2.0

        # 必要条件：左右栏宽度不能过于悬殊
        left_width = max(block["bbox"][2] for block in left_blocks) - \
            min(block["bbox"][0] for block in left_blocks)
        right_width = max(block["bbox"][2] for block in right_blocks) - \
            min(block["bbox"][0] for block in right_blocks)
        wider = max(left_width, right_width, 1e-6)
        if min(left_width, right_width) / wider < _COLUMN_MIN_WIDTH_RATIO:
            return False, width / 2.0
        return True, split_x
    except Exception:
        try:
            return False, float(page.rect.width) / 2.0
        except Exception:
            return False, 0.0


def extract_page_blocks_column_aware(page):
    """返回按阅读顺序排列的文本块，每块包含 bbox、文本、字体行信息和 ``column``。

    双栏页面使用文本块中心的双峰分布估计分栏线，并按左栏自上而下、右栏
    自上而下排序。单栏页面保留 PyMuPDF 原始 block 顺序。``column`` 在单栏时
    全部为 0，双栏时为 0（左）或 1（右）。
    """
    try:
        blocks = _dict_text_blocks(page)
        is_two_column, split_x = _detect_two_columns(page, blocks)
        if not is_two_column:
            for block in blocks:
                block["column"] = 0
            return sorted(blocks, key=lambda block: block["order"])
        for block in blocks:
            x0, _, x1, _ = block["bbox"]
            block["column"] = 0 if (x0 + x1) / 2.0 < split_x else 1
        return sorted(blocks, key=lambda block: (
            block["column"], block["bbox"][1], block["bbox"][0], block["order"]
        ))
    except Exception:
        return []


def extract_page_text_column_aware(page, return_blocks=False):
    """提取单页栏感知文本；``return_blocks=True`` 时同时返回带 column 的块。"""
    try:
        blocks = extract_page_blocks_column_aware(page)
        text = "\n\n".join(block["text"] for block in blocks if block["text"].strip()).strip()
        if return_blocks:
            return text, blocks
        return text
    except Exception:
        try:
            text = page.get_text()
        except Exception:
            text = ""
        return (text, []) if return_blocks else text


def native_page_texts(doc):
    texts = []
    try:
        for page in doc:
            texts.append(extract_page_text_column_aware(page))
    except Exception:
        pass
    return texts


def _llm_response_text(response):
    try:
        return response.choices[0].message.content or ""
    except Exception:
        return "【LLM 响应解析失败】"


def _llm_kwargs(llm):
    try:
        return {
            "api_key": getattr(llm, "api_key", None),
            "base_url": getattr(llm, "base_url", None),
        }
    except Exception:
        return {}


def ocr_supported(llm):
    """是否具备可用 OCR 能力：有视觉模型、Key 非空且能被 HTTP 头安全编码。"""
    try:
        key = str(getattr(llm, "api_key", "") or "").strip()
    except Exception:
        key = ""
    if not str(getattr(llm, "vl_model", "") or "").strip() or len(key) < 20:
        return False
    try:
        key.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def ocr_page_text(page, llm, zoom=2.0):
    """OCR 单页并返回文本；统一通过 call_llm_with_retry 调用视觉模型。"""
    try:
        if not getattr(llm, "vl_model", ""):
            model_name = getattr(llm, "model", "未知 Provider")
            return ("[扫描件 OCR 降级提示] 当前 Provider（%s）不支持多模态视觉模型，"
                    "无法执行 OCR。请切换到支持视觉的 Provider 后重新入库本页。" % model_name)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB)
        image_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
        prompt = ("识别这页学术论文的全部文字，按原始阅读顺序输出，保留标题层级；"
                  "数学公式用 LaTeX；表格用 Markdown；只输出识别内容，不要解释。")
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }]
        response = call_llm_with_retry(
            messages,
            model=getattr(llm, "vl_model", None),
            temperature=0.1,
            **_llm_kwargs(llm),
        )
        return _llm_response_text(response)
    except Exception as exc:
        return f"[OCR 失败: {str(exc)[:160]}]"


def ocr_document(doc, llm, pages=None, progress_cb=None, zoom=2.0):
    """对无文本层页面逐页渲染并调用多模态模型 OCR，单页失败不影响后续页。"""
    try:
        idxs = list(pages) if pages is not None else list(range(doc.page_count))
        texts = [""] * doc.page_count
    except Exception:
        return []
    total = len(idxs)
    for done, i in enumerate(idxs, start=1):
        try:
            texts[i] = ocr_page_text(doc[i], llm, zoom=zoom)
        except Exception as exc:
            texts[i] = f"[OCR 第{i + 1}页失败: {exc}]"
        if progress_cb:
            try:
                progress_cb(done / max(total, 1), i + 1)
            except Exception:
                pass
    return texts


def _sorted_blocks_by_column(page, raw_blocks):
    """兼容旧调用：对 ``get_text('blocks')`` 结果做栏感知排序。"""
    try:
        wrapped = []
        for order, block in enumerate(raw_blocks):
            if len(block) < 7 or block[6] != 0:
                continue
            wrapped.append({
                "bbox": tuple(block[:4]), "text": str(block[4] or ""),
                "order": order, "raw": block, "column": 0,
            })
        is_two_column, split_x = _detect_two_columns(page, wrapped)
        if not is_two_column:
            return [block["raw"] for block in wrapped]
        for block in wrapped:
            x0, _, x1, _ = block["bbox"]
            block["column"] = 0 if (x0 + x1) / 2.0 < split_x else 1
        wrapped.sort(key=lambda block: (block["column"], block["bbox"][1], block["bbox"][0]))
        return [block["raw"] for block in wrapped]
    except Exception:
        return [block for block in raw_blocks if len(block) >= 7 and block[6] == 0]


def _blocks(doc):
    """兼容返回 ``[(page_idx, text)]``；块级 column 信息由新提取函数提供。"""
    out = []
    try:
        for pi, page in enumerate(doc):
            for block in extract_page_blocks_column_aware(page):
                text = block["text"].strip()
                if len(text) > 12 and not re.match(r"^[\W\d_]+$", text):
                    out.append((pi, text))
    except Exception:
        pass
    return out


def build_chunks_from_texts(page_texts, page_offset=0):
    """按文本块打包成 ~CHUNK_SIZE 的分块，记录起止页码；超长块按窗口切分并带重叠。"""
    chunks, buf, buf_pages = [], [], []

    def flush():
        try:
            if not buf:
                return
            text = "\n\n".join(buf).strip()
            if text:
                chunks.append({"text": text, "page": min(buf_pages) + 1 + page_offset,
                               "page_end": max(buf_pages) + 1 + page_offset})
            buf.clear()
            buf_pages.clear()
        except Exception:
            buf.clear()
            buf_pages.clear()

    try:
        for pi, raw in enumerate(page_texts):
            raw = raw or ""
            blocks = [block.strip() for block in raw.split("\n\n") if len(block.strip()) > 12]
            if not blocks:
                blocks = [raw.strip()] if raw.strip() else []
            for block in blocks:
                if len(block) > CHUNK_SIZE:
                    flush()
                    start = 0
                    step = max(1, CHUNK_SIZE - CHUNK_OVERLAP)
                    while start < len(block):
                        piece = block[start:start + CHUNK_SIZE]
                        chunks.append({"text": piece.strip(), "page": pi + 1 + page_offset,
                                       "page_end": pi + 1 + page_offset})
                        start += step
                    continue
                if sum(len(item) for item in buf) + len(block) > CHUNK_SIZE:
                    flush()
                buf.append(block)
                buf_pages.append(pi)
        flush()
        if CHUNK_OVERLAP > 0:
            for i in range(1, len(chunks)):
                tail = chunks[i - 1]["text"][-CHUNK_OVERLAP:]
                chunks[i]["text"] = tail + chunks[i]["text"]
    except Exception:
        flush()
    return chunks


def heading_page_data(blocks, fallback_text=""):
    """从栏感知 blocks 生成 infer_headings_from_text 可消费的轻量页数据。"""
    try:
        lines = []
        for block in blocks or []:
            lines.extend(block.get("lines") or [])
        return {"lines": lines, "text": fallback_text}
    except Exception:
        return {"lines": [], "text": fallback_text or ""}


def _normalise_heading_pages(page_texts):
    pages = []
    try:
        for item in page_texts or []:
            if isinstance(item, dict):
                lines = item.get("lines") or []
                if lines:
                    pages.append(lines)
                    continue
                item = item.get("text", "")
            if isinstance(item, (list, tuple)) and item and isinstance(item[0], dict):
                pages.append(list(item))
                continue
            text = str(item or "")
            pages.append([{
                "text": line.strip(), "bbox": (0.0, 0.0, 0.0, 0.0), "size": 0.0,
                "flags": 0, "font": "", "bold": False,
            } for line in text.splitlines() if line.strip()])
    except Exception:
        return []
    return pages


def infer_headings_from_text(page_texts):
    """按字号、粗体、短行和章节编号推断目录，返回 ``[(level, title, page), ...]``。

    ``page_texts`` 可传普通页面字符串，也可传 ``heading_page_data`` 生成的字体元数据。
    有字体元数据时，以字符加权的正文字号众数为基准；无元数据时仍可依据 ``1.``、
    ``1.1``、``第1章`` 等编号回退识别。
    """
    try:
        pages = _normalise_heading_pages(page_texts)
        size_counter = Counter()
        for lines in pages:
            for line in lines:
                text = str(line.get("text", "") or "").strip()
                size = float(line.get("size", 0.0) or 0.0)
                if text and size > 0:
                    size_counter[round(size * 2.0) / 2.0] += max(1, len(text))
        body_size = size_counter.most_common(1)[0][0] if size_counter else 0.0

        candidates = []
        for page_no, lines in enumerate(pages, start=1):
            for order, line in enumerate(lines):
                title = re.sub(r"\s+", " ", str(line.get("text", "") or "")).strip()
                if not title or len(title) >= 80 or re.match(r"^[\W_]+$", title):
                    continue
                size = float(line.get("size", 0.0) or 0.0)
                bold = bool(line.get("bold")) or bool(int(line.get("flags", 0) or 0) & 16)
                bold = bold or "bold" in str(line.get("font", "")).lower()
                number_match = _HEADING_NUMBER_RE.match(title)
                cn_match = _HEADING_CN_RE.match(title)
                numbered = bool(number_match or cn_match)
                larger = bool(body_size and size >= body_size + max(0.8, body_size * 0.08))
                bold_heading = bool(bold and (not body_size or size >= body_size * 0.95) and len(title) <= 60)
                if not (larger or bold_heading or numbered):
                    continue
                candidates.append({
                    "title": title, "page": page_no, "order": order, "size": size,
                    "number_match": number_match, "cn_match": cn_match,
                })

        ranked_sizes = sorted({item["size"] for item in candidates if item["size"] > body_size}, reverse=True)
        inferred = []
        seen = set()
        for item in candidates:
            title = item["title"]
            key = (item["page"], title.casefold())
            if key in seen:
                continue
            seen.add(key)
            if item["number_match"]:
                level = min(6, item["number_match"].group(1).count(".") + 1)
            elif item["cn_match"]:
                level = 1 if item["cn_match"].group(2) in ("章", "篇", "部分") else 2
            elif item["size"] in ranked_sizes:
                level = min(6, ranked_sizes.index(item["size"]) + 1)
            else:
                level = 2
            inferred.append((level, title, item["page"]))
        return inferred
    except Exception:
        return []


def extract_toc(doc):
    """优先读取 PDF 书签；无书签或读取失败时按正文排版推断标题树。"""
    try:
        toc = doc.get_toc() or []
        if toc:
            return toc
    except Exception:
        pass
    page_data = []
    try:
        for page in doc:
            text, blocks = extract_page_text_column_aware(page, return_blocks=True)
            page_data.append(heading_page_data(blocks, text))
    except Exception:
        pass
    return infer_headings_from_text(page_data)


def extract_tables(doc):
    tables = []
    try:
        for pi, page in enumerate(doc):
            try:
                found = page.find_tables()
                page_tables = found.tables if found else []
            except Exception:
                continue
            for ti, tab in enumerate(page_tables):
                try:
                    df = tab.to_pandas()
                    cols = [str(column).strip() for column in df.columns]
                    total = len(df) * len(df.columns)
                    nan_ratio = df.isna().sum().sum() / max(total, 1)
                    bad_prefix = any(re.match(r"^(Col\d+|None|\d+-)$", column) for column in cols)
                    if bad_prefix or nan_ratio > 0.4 or total < 8 or len(df.columns) < 2 or len(df) < 2:
                        continue
                    tables.append({"page": pi + 1, "index": ti + 1, "df": df})
                except Exception:
                    continue
    except Exception:
        pass
    return tables


def _caption_blocks(page):
    captions = []
    try:
        for block in page.get_text("blocks") or []:
            if len(block) < 7 or block[6] != 0:
                continue
            text = re.sub(r"\s+", " ", str(block[4] or "")).strip()
            if text and _CAPTION_RE.match(text):
                captions.append({"bbox": tuple(block[:4]), "text": text})
    except Exception:
        return []
    return captions


def _caption_distance(image_bbox, caption_bbox):
    try:
        ix0, iy0, ix1, iy1 = image_bbox
        cx0, cy0, cx1, cy1 = caption_bbox
        overlap = max(0.0, min(ix1, cx1) - max(ix0, cx0))
        min_width = max(1.0, min(ix1 - ix0, cx1 - cx0))
        if overlap / min_width < 0.15:
            return None
        if cy0 >= iy1:
            return cy0 - iy1
        if iy0 >= cy1:
            return iy0 - cy1
        return 0.0
    except Exception:
        return None


def _nearest_caption(image_rects, captions, used_caption_indexes):
    best = None
    try:
        for rect in image_rects:
            bbox = tuple(rect)
            for ci, caption in enumerate(captions):
                if ci in used_caption_indexes:
                    continue
                distance = _caption_distance(bbox, caption["bbox"])
                if distance is None:
                    continue
                candidate = (distance, ci, caption["text"], bbox)
                if best is None or candidate[0] < best[0]:
                    best = candidate
        if best is None:
            return "", None, None
        _, caption_index, text, bbox = best
        return text, bbox, caption_index
    except Exception:
        return "", None, None


def extract_figures(doc, out_dir):
    import os

    figures = []
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        return figures
    seen = set()
    try:
        for pi, page in enumerate(doc):
            captions = _caption_blocks(page)
            used_captions = set()
            try:
                images = page.get_images(full=True) or []
            except Exception:
                images = []
            for ii, img in enumerate(images):
                try:
                    xref = img[0]
                    if (pi, xref) in seen:
                        continue
                    seen.add((pi, xref))
                    data = doc.extract_image(xref)
                    image = Image.open(io.BytesIO(data["image"]))
                    if image.width < 150 or image.height < 150:
                        continue
                    try:
                        rects = page.get_image_rects(xref) or []
                    except Exception:
                        rects = []
                    caption, image_bbox, caption_index = _nearest_caption(rects, captions, used_captions)
                    if caption_index is not None:
                        used_captions.add(caption_index)
                    if image.mode in ("RGBA", "LA", "P"):
                        bg = Image.new("RGB", image.size, (255, 255, 255))
                        mask = image.split()[-1] if image.mode in ("RGBA", "LA") else None
                        bg.paste(image, mask=mask)
                        image = bg
                    elif image.mode != "RGB":
                        image = image.convert("RGB")
                    fp = os.path.join(out_dir, f"fig_p{pi + 1}_{ii + 1}.png")
                    image.save(fp, "PNG")
                    figures.append({
                        "page": pi + 1,
                        "index": len(figures) + 1,
                        "path": fp,
                        "desc": "",
                        "caption": caption or "",
                        "bbox": image_bbox,
                    })
                except Exception:
                    continue
    except Exception:
        pass
    return figures


def describe_figures(figures, llm, progress_cb=None):
    try:
        has_vision = bool(getattr(llm, "vl_model", ""))
    except Exception:
        has_vision = False
    if not has_vision:
        for fig in figures:
            fig["desc"] = "（当前 Provider 无视觉模型，已跳过图表理解；切换多模态 Provider 后重新提取可补全）"
        return figures
    for i, fig in enumerate(figures):
        try:
            with open(fig["path"], "rb") as image_file:
                image_b64 = base64.b64encode(image_file.read()).decode("ascii")
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "用一句专业、简洁的中文描述这张学术图表的内容。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            }]
            response = call_llm_with_retry(
                messages,
                model=getattr(llm, "vl_model", None),
                temperature=0.1,
                **_llm_kwargs(llm),
            )
            fig["desc"] = _llm_response_text(response)
        except Exception as exc:
            fig["desc"] = f"（理解失败：{str(exc)[:50]}）"
        if progress_cb:
            try:
                progress_cb((i + 1) / max(len(figures), 1))
            except Exception:
                pass
    return figures


def full_text(doc):
    parts = []
    try:
        for page in doc:
            parts.append(extract_page_text_column_aware(page))
    except Exception:
        pass
    return "\n\n".join(part for part in parts if part)


def extract_references_raw(doc, tail_chars=22000):
    try:
        return full_text(doc)[-tail_chars:]
    except Exception:
        return ""


def split_reference_entries(text):
    """切分并清洗参考文献条目。

    支持 ``[1]``、``1.``/``1)``、行首 ``(2020)``、``Author (2020)``（含年份
    后缀字母）以及 ``arXiv:2401.xxxxx`` 起始格式。没有显式编号时以作者-年份模式
    识别新条目；条目内部的换行和缩进会合并为空格，保留为同一条参考文献。
    """
    try:
        source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        source = re.sub(r"(?im)^\s*(?:references|bibliography|works\s+cited|参考文献)\s*$", "", source)
        start_re = re.compile(
            r"(?im)^[ \t]*(?:"
            r"\[\d{1,4}\][ \t]+|"
            r"\d{1,4}[.)][ \t]+|"
            r"\((?:19|20)\d{2}[a-z]?\)[ \t]+|"
            r"arXiv\s*:\s*\d{4}\.\d{4,5}(?:v\d+)?\b|"
            r"(?:[A-ZÀ-ÖØ-Þ][\wÀ-öø-ÿ'’\-]+(?:\s*,?\s*(?:[A-Z]\.?|[A-ZÀ-ÖØ-Þ][\wÀ-öø-ÿ'’\-]+)){0,5}"
            r"(?:\s+(?:et\s+al\.?|and\s+[A-ZÀ-ÖØ-Þ][\wÀ-öø-ÿ'’\-]+))?)"
            r"[.,]?[ \t]+\((?:19|20)\d{2}[a-z]?\)"
            r")"
        )
        matches = list(start_re.finditer(source))
        entries = []
        if matches:
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
                entry = source[match.start():end]
                entry = re.sub(r"\s*\n\s*", " ", entry)
                entry = re.sub(r"[ \t]+", " ", entry).strip(" \t\n;,")
                if entry:
                    entries.append(entry)
        else:
            for paragraph in re.split(r"\n\s*\n+", source):
                entry = re.sub(r"\s*\n\s*", " ", paragraph)
                entry = re.sub(r"[ \t]+", " ", entry).strip(" \t\n;,")
                if entry:
                    entries.append(entry)
        cleaned = []
        seen = set()
        for entry in entries:
            key = entry.casefold()
            if key not in seen:
                seen.add(key)
                cleaned.append(entry)
        return cleaned
    except Exception:
        return []

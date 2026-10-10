"""PDF 解析引擎：扫描件检测/OCR、版面文本、语义切块、标题树/表格/图表/公式/参考文献抽取。"""
import io
import re
import pymupdf as fitz
from PIL import Image

from config import CHUNK_SIZE, CHUNK_OVERLAP


def open_doc(path_or_bytes, filetype="pdf"):
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return fitz.open(stream=bytes(path_or_bytes), filetype=filetype)
    return fitz.open(path_or_bytes)


def detect_scanned(doc, sample=3):
    chars = sum(len(doc[i].get_text().strip()) for i in range(min(sample, doc.page_count)))
    return chars < 100


def native_page_texts(doc):
    return [page.get_text() for page in doc]


def ocr_document(doc, llm, pages=None, progress_cb=None, zoom=2.0):
    """对无文本层页面逐页渲染并调用多模态模型 OCR。

    所选 Provider 无视觉模型时（DeepSeek/Ollama），优雅降级：
    返回占位文本并附说明，不抛出异常、不中断入库流程。
    """
    idxs = pages if pages is not None else range(doc.page_count)
    texts = [""] * doc.page_count
    if not getattr(llm, "vl_model", ""):
        degrade = ("[扫描件 OCR 降级提示] 当前 Provider（%s）不支持多模态视觉模型，"
                   "无法执行 OCR。请在侧边栏切换到智谱 GLM / 通义千问 / OpenAI 等"
                   "支持视觉的 Provider 后重新入库本页。" % (llm.model,))
        for i in idxs:
            texts[i] = degrade
        if progress_cb:
            progress_cb(1.0, doc.page_count)
        return texts
    mat = fitz.Matrix(zoom, zoom)
    prompt = ("识别这页学术论文的全部文字，按原始阅读顺序输出，保留标题层级；"
              "数学公式用 LaTeX；表格用 Markdown；只输出识别内容，不要解释。")
    done = 0
    total = len(idxs) if hasattr(idxs, "__len__") else doc.page_count
    for i in idxs:
        pix = doc[i].get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        try:
            texts[i] = llm.vision(prompt, pix.tobytes("png"), "png")
        except Exception as e:
            texts[i] = f"[OCR 第{i+1}页失败: {e}]"
        done += 1
        if progress_cb:
            progress_cb(done / max(total, 1), i + 1)
    return texts


def _sorted_blocks_by_column(page, raw_blocks):
    """按阅读顺序对一页内的文本块排序。

    双栏判定（简化版）：取竖直中线 x_mid = page.rect.width / 2，
    若所有 btype==0 的文本块都不跨中线（即没有 block 满足 x0 < x_mid < x1），
    且文本块数 >= 4，则判定为双栏版面；否则按单栏处理。
    双栏时先输出左栏（x0 < x_mid）、再输出右栏（x0 >= x_mid），各自按 y0 升序；
    单栏时直接按 y0 升序返回，保持原有阅读顺序。
    """
    x_mid = page.rect.width / 2.0
    text_blocks = [b for b in raw_blocks if b[6] == 0]
    # 文本块过少，无法可靠判定双栏，按单栏处理
    if len(text_blocks) < 4:
        return sorted(text_blocks, key=lambda b: b[1])
    # 存在文本块跨竖直中线 → 单栏排版
    crossed = any(b[0] < x_mid < b[2] for b in text_blocks)
    if crossed:
        return sorted(text_blocks, key=lambda b: b[1])
    # 双栏：左栏在前、右栏在后，各自按垂直位置升序
    left = sorted([b for b in text_blocks if b[0] < x_mid], key=lambda b: b[1])
    right = sorted([b for b in text_blocks if b[0] >= x_mid], key=lambda b: b[1])
    return left + right


def _blocks(doc):
    """返回 [(page_idx, text)]，按阅读顺序的文本块（双栏版面自动按 x 坐标分栏）。"""
    out = []
    for pi, page in enumerate(doc):
        raw = page.get_text("blocks")
        for b in _sorted_blocks_by_column(page, raw):
            x0, y0, x1, y1, text, bno, btype = b
            if btype != 0:
                continue
            t = text.strip()
            if len(t) > 12 and not re.match(r"^[\W\d_]+$", t):
                out.append((pi, t))
    return out


def build_chunks_from_texts(page_texts):
    """按文本块打包成 ~CHUNK_SIZE 的分块，记录起止页码；超长块按窗口切分并带重叠。"""
    chunks, buf, buf_pages = [], [], []

    def flush():
        if not buf:
            return
        text = "\n\n".join(buf).strip()
        if text:
            chunks.append({"text": text, "page": min(buf_pages) + 1, "page_end": max(buf_pages) + 1})
        buf.clear()
        buf_pages.clear()

    for pi, raw in enumerate(page_texts):
        blocks = [b.strip() for b in raw.split("\n\n") if len(b.strip()) > 12]
        if not blocks:
            # 退化：整页作为一个块
            blocks = [raw.strip()] if raw.strip() else []
        for b in blocks:
            if len(b) > CHUNK_SIZE:
                flush()
                start = 0
                while start < len(b):
                    piece = b[start:start + CHUNK_SIZE]
                    chunks.append({"text": piece.strip(), "page": pi + 1, "page_end": pi + 1})
                    start += CHUNK_SIZE - CHUNK_OVERLAP
                continue
            if sum(len(x) for x in buf) + len(b) > CHUNK_SIZE:
                flush()
            buf.append(b)
            buf_pages.append(pi)
    flush()

    # 为相邻块补充重叠（携带上一块尾部，增强跨段检索）
    if CHUNK_OVERLAP > 0:
        for i in range(1, len(chunks)):
            tail = chunks[i - 1]["text"][-CHUNK_OVERLAP:]
            chunks[i]["text"] = tail + chunks[i]["text"]
    return chunks


def extract_toc(doc):
    return doc.get_toc()


def extract_tables(doc):
    tables = []
    for pi, page in enumerate(doc):
        for ti, tab in enumerate(page.find_tables().tables):
            try:
                df = tab.to_pandas()
            except Exception:
                continue
            cols = [str(c).strip() for c in df.columns]
            total = len(df) * len(df.columns)
            nan_ratio = df.isna().sum().sum() / max(total, 1)
            bad_prefix = any(re.match(r"^(Col\d+|None|\d+-)$", c) for c in cols)
            if bad_prefix or nan_ratio > 0.4 or total < 8 or len(df.columns) < 2 or len(df) < 2:
                continue
            tables.append({"page": pi + 1, "index": ti + 1, "df": df})
    return tables


def extract_figures(doc, out_dir):
    import os
    os.makedirs(out_dir, exist_ok=True)
    figures, seen = [], set()
    for pi, page in enumerate(doc):
        for ii, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                data = doc.extract_image(xref)
                image = Image.open(io.BytesIO(data["image"]))
                if image.width < 150 or image.height < 150:
                    continue
                key = (image.width, image.height)
                if key in seen:
                    continue
                seen.add(key)
                if image.mode in ("RGBA", "LA", "P"):
                    bg = Image.new("RGB", image.size, (255, 255, 255))
                    mask = image.split()[-1] if image.mode in ("RGBA", "LA") else None
                    bg.paste(image, mask=mask)
                    image = bg
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                fp = os.path.join(out_dir, f"fig_p{pi+1}_{ii+1}.png")
                image.save(fp, "PNG")
                figures.append({"page": pi + 1, "index": len(figures) + 1, "path": fp, "desc": ""})
            except Exception:
                continue
    return figures


def describe_figures(figures, llm, progress_cb=None):
    if not getattr(llm, "vl_model", ""):
        for fig in figures:
            fig["desc"] = "（当前 Provider 无视觉模型，已跳过图表理解；切换多模态 Provider 后重新提取可补全）"
        return figures
    for i, fig in enumerate(figures):
        try:
            with open(fig["path"], "rb") as f:
                fig["desc"] = llm.vision("用一句专业、简洁的中文描述这张学术图表的内容。", f.read(), "png")
        except Exception as e:
            fig["desc"] = f"（理解失败：{str(e)[:50]}）"
        if progress_cb:
            progress_cb((i + 1) / max(len(figures), 1))
    return figures


def full_text(doc):
    return "".join(p.get_text() for p in doc)


def extract_references_raw(doc, tail_chars=22000):
    return full_text(doc)[-tail_chars:]

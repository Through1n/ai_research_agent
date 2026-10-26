"""将矢量版 v1 逐页渲染为图片，再拼装成无文本层的"扫描件"PDF（模拟 1706.03762v1_img.pdf）。

路径按仓库根目录推导，不写死机器相关的绝对路径——此前是
``r"C:\\Users\\<用户名>\\ai_research_agent\\论文样本\\..."``，而 scripts/ 会随镜像/仓库
一起分发，等于把本机用户名公开出去（同 make_longbook.py 已修的问题）。
"""
import pymupdf as fitz
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SRC = str(_ROOT / "论文样本" / "1706.03762v1.pdf")
DST = str(_ROOT / "论文样本" / "1706.03762v1_img.pdf")

if not Path(SRC).is_file():
    raise SystemExit(
        f"缺少源文件 {SRC}\n"
        "先运行 `sh scripts/fetch_pdfs.sh` 拉取 arXiv 三件，再重新生成扫描件。")

src = fitz.open(SRC)
out = fitz.open()
zoom = 2.0  # ~144 DPI
mat = fitz.Matrix(zoom, zoom)
for i in range(src.page_count):
    pix = src[i].get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    page = out.new_page(width=src[i].rect.width, height=src[i].rect.height)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
out.save(DST, deflate=True)
out.close(); src.close()

chk = fitz.open(DST)
txt = "".join(p.get_text() for p in chk)
print(f"扫描件生成完成: {chk.page_count} 页, 文本层字符数={len(txt.strip())} (应为0), 大小={__import__('os').path.getsize(DST)/1024/1024:.2f} MB")
chk.close()

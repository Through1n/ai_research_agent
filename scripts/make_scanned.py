"""将矢量版 v1 逐页渲染为图片，再拼装成无文本层的"扫描件"PDF（模拟 1706.03762v1_img.pdf）。"""
import pymupdf as fitz

SRC = r"C:\Users\Administrator\ai_research_agent\pdf\1706.03762v1.pdf"
DST = r"C:\Users\Administrator\ai_research_agent\pdf\1706.03762v1_img.pdf"

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

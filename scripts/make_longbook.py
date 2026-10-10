"""生成 540 页的长文档压测 PDF（用于验证切块/索引/内存控制；非官方教材，属鲁棒性测试夹具）。"""
import pymupdf as fitz

DST = r"C:\Users\Administrator\ai_research_agent\pdf\LongBook_540p_fixture.pdf"
PAGES = 540

doc = fitz.open()
base = ("Chapter {ch}  Deep Learning Foundations and Agent Systems\n"
        "This is page {pg}. We consider attention mechanisms, retrieval augmented generation, "
        "multi-agent orchestration, function calling, citation graph mining and hallucination "
        "guardrails. The transformer scales dot product attention as softmax(QK^T/sqrt(d_k))V. "
        "Empirical studies report BLEU improvements and parameter counts across model families. "
        "Future work includes self-evolving agents and reproducible research workflows.\n"
        "Equation: y = W x + b ;  L = -sum y log p ;  Attention(Q,K,V)=softmax(QK^T/sqrt(d_k))V\n")

for i in range(PAGES):
    page = doc.new_page(width=612, height=792)
    ch = i // 30 + 1
    text = base.format(ch=ch, pg=i + 1)
    # 重复以填满页面（约 45 行）
    body = (text + "\n") * 6
    page.insert_text((50, 60), body, fontsize=10, fontname="helv", color=(0, 0, 0))

doc.save(DST, deflate=True)
doc.close()

chk = fitz.open(DST)
chars = sum(len(p.get_text()) for p in chk)
print(f"长文档夹具: {chk.page_count} 页, 总字符 {chars:,}, 大小 {__import__('os').path.getsize(DST)/1024/1024:.2f} MB")
chk.close()

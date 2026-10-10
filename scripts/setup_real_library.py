"""用真实 PDF 重建文献库，并验证：真实 v1/v7 语义去重、扫描件检测、长文档鲁棒性、真实引用图谱。"""
import sys
import time
import os

sys.path.insert(0, r"C:\Users\Administrator\ai_research_agent")

from services.state import Container
from services import library
from services import graph as gsvc


def reset(ctx):
    ids = ctx.collection.get().get("ids", [])
    if ids:
        ctx.collection.delete(ids=ids)
    ctx.papers, ctx.paper_order, ctx.chunks, ctx.likes = {}, [], [], {}
    ctx.rebuild_bm25(); ctx.save_library()


ctx = Container()
t0 = time.time()
reset(ctx)

PDF = r"C:\Users\Administrator\ai_research_agent\pdf"
DEMO = r"C:\Users\Administrator\ai_research_agent\demo_data"

AUTH = ("Ashish Vaswani; Noam Shazeer; Niki Parmar; Jakob Uszkoreit; Llion Jones; "
        "Aidan N. Gomez; Lukasz Kaiser; Illia Polosukhin")


def ingest(path, **meta):
    prepared = library.prepare_pdf(ctx, path=path, filename=meta["filename"])
    dup = f"查重命中 {prepared['dup_name']} (sim {prepared['dup_sim']:.3f})" if prepared["dup_name"] else "无重复"
    paper = library.commit(ctx, prepared, title=meta.get("title"), authors=meta.get("authors"),
                           arxiv_id=meta.get("arxiv_id"), version=meta.get("version"), year=meta.get("year"))
    nch = len([c for c in ctx.chunks if c["source"] == paper["filename"]])
    print(f"- {meta['filename']:28s} {paper['page_count']:>3}页 扫描={paper['scanned']} 分块={nch:>3}  {dup}")
    return paper


print("===== 真实 PDF 入库 =====")
ingest(os.path.join(DEMO, "1706.03762v7.pdf"), filename="1706.03762v7.pdf",
       title="Attention Is All You Need", authors=AUTH, arxiv_id="1706.03762", version="v7", year="2017")
ingest(os.path.join(PDF, "1706.03762v1.pdf"), filename="1706.03762v1.pdf",
       title="Attention Is All You Need", authors=AUTH, arxiv_id="1706.03762", version="v1", year="2017")
ingest(os.path.join(DEMO, "1810.04805v2.pdf"), filename="1810.04805v2.pdf",
       title="BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
       authors="Jacob Devlin; Ming-Wei Chang; Kenton Lee; Kristina Toutanova",
       arxiv_id="1810.04805", version="v2", year="2018")
ingest(os.path.join(PDF, "LongBook_540p_fixture.pdf"), filename="LongBook_540p_fixture.pdf",
       title="Long Document Robustness Fixture", authors="Generated",
       arxiv_id="longbook-fixture", version="", year="2026")

print(f"\n总文件 {len(ctx.files)}，总分块 {len(ctx.chunks)}，耗时 {time.time()-t0:.1f}s")
try:
    import psutil
    print(f"进程内存 {psutil.Process(os.getpid()).memory_info().rss/1024/1024:.0f} MB")
except Exception:
    pass

print("\n===== 扫描件检测（v1_img，无Key不OCR） =====")
prep_img = library.prepare_pdf(ctx, path=os.path.join(PDF, "1706.03762v1_img.pdf"),
                               filename="1706.03762v1_img.pdf")
print(f"scanned={prep_img['scanned']}，页面 {prep_img['page_count']}，无Key时可提取文本长度 {len(prep_img['full_text'])}（填入Key后将逐页OCR）")

print("\n===== 真实引用图谱 =====")
G, edges = gsvc.build_graph(ctx)
metrics = gsvc.analyze(G)
corner, edge = gsvc.classify_papers(G, metrics)
print(f"逻辑节点 {len(G.nodes)}，引用边 {len(edges)}")
for a, b, ev in edges:
    print(f"  《{ctx.papers[a]['title'][:30]}》 → 《{ctx.papers[b]['title'][:30]}》")
print("基石:", [ctx.papers[n]["title"][:40] for n in corner])
print("边缘:", [ctx.papers[n]["title"][:40] for n in edge])
print("\n✅ 真实文献库重建完成")

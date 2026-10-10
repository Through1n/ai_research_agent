"""真实官方 PDF 验证：内容级语义去重（含改名场景）、扫描件检测、长文档工程指标。

不依赖 API Key（走原生文本层）；输出可直接引用到技术文档的实测数字。
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz
from services.state import Container
from services import library, parsing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)

ctx = Container()
# 隔离：清空运行库（仅影响 storage，不动 pdf/ 源文件）
for fn in list(ctx.paper_order):
    library.remove_paper(ctx, fn)

def load(path, name):
    data = open(path, "rb").read()
    pr = library.prepare_pdf(ctx, data=data, filename=name)
    return pr

print("========== A) 官方真实 PDF 语义去重 ========== ")
v7 = load(P("demo_data", "1706.03762v7.pdf"), "1706.03762v7.pdf")
print(f"v7 入库  查重={v7['dup_name']} sim={v7['dup_sim']:.3f}")
library.commit(ctx, v7, title="Attention Is All You Need", arxiv_id="1706.03762", version="v7")

v1 = load(P("pdf", "1706.03762v1.pdf"), "1706.03762v1.pdf")
print(f"v1 入库  查重={v1['dup_name']} sim={v1['dup_sim']:.3f}")
print(f"          依据：{v1['dup_evidence']}")
library.commit(ctx, v1, title="Attention Is All You Need", arxiv_id="1706.03762", version="v1")

bert = load(P("demo_data", "1810.04805v2.pdf"), "1810.04805v2.pdf")
print(f"BERT 入库 查重={bert['dup_name']} sim={bert['dup_sim']:.3f}（应无命中）")
library.commit(ctx, bert, title="BERT: Pre-training of Deep Bidirectional Transformers",
               arxiv_id="1810.04805", version="v2")

print("\n------ B) 改名场景：证明不依赖文件名 ------")
renamed = load(P("pdf", "1706.03762v1.pdf"), "my_saved_paper_final.pdf")
print(f"重命名后上传 → 查重={renamed['dup_name']} sim={renamed['dup_sim']:.3f}")
print(f"              依据：{renamed['dup_evidence']}")

print("\n------ C) 扫描件检测 ------")
sc = parsing.open_doc(P("pdf", "1706.03762v1_img.pdf"))
print(f"1706.03762v1_img.pdf 页数={sc.page_count} 判定扫描件={parsing.detect_scanned(sc)}"
      f" 原生文本字符数={sum(len(sc[i].get_text().strip()) for i in range(min(3, sc.page_count)))}")
sc.close()

print("\n------ D) 超长文档工程指标 ------")
long_path = P("pdf", "UnderstandingDeepLearning_02_09_26_C.pdf")
if os.path.exists(long_path):
    t0 = time.time()
    lp = load(long_path, "UnderstandingDeepLearning.pdf")
    n_chunk = len([c for c in lp["page_texts"] if c.strip()])
    library.commit(ctx, lp, title="Understanding Deep Learning")
    import psutil
    mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    chunks = len([c for c in ctx.chunks if c["source"] == "UnderstandingDeepLearning.pdf"])
    print(f"页数={lp['page_count']} 字数={len(lp['full_text']):,} 分块={chunks} "
          f"入库耗时={time.time() - t0:.1f}s 进程内存={mem:.0f}MB")
else:
    lp = load(P("pdf", "LongBook_540p_fixture.pdf"), "LongBook_540p.pdf")
    library.commit(ctx, lp, title="Long Book Fixture")
    import psutil
    chunks = len([c for c in ctx.chunks if c["source"] == "LongBook_540p.pdf"])
    print(f"夹具 页数={lp['page_count']} 字数={len(lp['full_text']):,} 分块={chunks} "
          f"内存={psutil.Process(os.getpid()).memory_info().rss/1024/1024:.0f}MB")

print("\n------ E) 真实引用图谱（官方 PDF）------")
from services import graph as gsvc
G, edges = gsvc.build_graph(ctx)
m = gsvc.analyze(G)
corner, edge = gsvc.classify_papers(G, m)
print(f"节点={len(G.nodes)} 真实引用边={len(edges)}")
for a, b, ev in edges:
    print(f"  {a} -> {b}\n     证据: {ev[:100]}")
print("基石论文:", [ctx.papers[n].get("title", n) for n in corner])

for fn in list(ctx.paper_order):
    library.remove_paper(ctx, fn)
print("\n（已清理测试期间的库数据）")

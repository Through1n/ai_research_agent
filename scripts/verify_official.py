"""真实官方 PDF 验证：内容级语义去重（含改名场景）、扫描件检测、长文档工程指标。

不依赖 API Key（走原生文本层）；输出可直接引用到技术文档的实测数字。

⚠️ 本脚本靠**清空文献库**来保证重跑的起点一致，因此默认拒绝在非空库上运行：
   要么先备份（`cp -r storage storage.bak`）再加 `--force`，要么换一台干净的机器跑。
   不做这道闸门就等于「跑一次指标验证，用户库里的论文全没了」——而它不会备份。
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

# 安全闸门：本脚本会清空文献库，绝不允许静默执行。
# 判据是「库里有没有东西」，不是「有没有传 --force」——两者都要满足才继续。
if list(ctx.paper_order) and "--force" not in sys.argv:
    print("⚠️ 这个脚本为做实测会先清空文献库，而它自己不做备份。")
    print(f"   当前库里有 {len(ctx.paper_order)} 篇论文：")
    for _fn in list(ctx.paper_order)[:10]:
        print(f"     - {_fn}")
    if len(ctx.paper_order) > 10:
        print(f"     … 共 {len(ctx.paper_order)} 篇")
    print("\n先备份，再加 --force 重跑：")
    print(f"    cp -r {P('storage')} {P('storage')}.bak")
    print("    python scripts/verify_official.py --force")
    sys.exit(2)

# 隔离：清空运行库（仅影响 storage，不动 论文样本/ 源文件）
for fn in list(ctx.paper_order):
    library.remove_paper(ctx, fn)

def load(path, name):
    data = open(path, "rb").read()
    pr = library.prepare_pdf(ctx, data=data, filename=name)
    return pr

print("========== A) 官方真实 PDF 语义去重 ========== ")
v7 = load(P("演示语料", "1706.03762v7.pdf"), "1706.03762v7.pdf")
print(f"v7 入库  查重={v7['dup_name']} sim={v7['dup_sim']:.3f}")
library.commit(ctx, v7, title="Attention Is All You Need", arxiv_id="1706.03762", version="v7")

v1 = load(P("论文样本", "1706.03762v1.pdf"), "1706.03762v1.pdf")
print(f"v1 入库  查重={v1['dup_name']} sim={v1['dup_sim']:.3f}")
print(f"          依据：{v1['dup_evidence']}")
library.commit(ctx, v1, title="Attention Is All You Need", arxiv_id="1706.03762", version="v1")

bert = load(P("演示语料", "1810.04805v2.pdf"), "1810.04805v2.pdf")
print(f"BERT 入库 查重={bert['dup_name']} sim={bert['dup_sim']:.3f}（应无命中）")
library.commit(ctx, bert, title="BERT: Pre-training of Deep Bidirectional Transformers",
               arxiv_id="1810.04805", version="v2")

print("\n------ B) 改名场景：证明不依赖文件名 ------")
renamed = load(P("论文样本", "1706.03762v1.pdf"), "my_saved_paper_final.pdf")
print(f"重命名后上传 → 查重={renamed['dup_name']} sim={renamed['dup_sim']:.3f}")
print(f"              依据：{renamed['dup_evidence']}")

print("\n------ C) 扫描件检测 ------")
sc = parsing.open_doc(P("论文样本", "1706.03762v1_img.pdf"))
print(f"1706.03762v1_img.pdf 页数={sc.page_count} 判定扫描件={parsing.detect_scanned(sc)}"
      f" 原生文本字符数={sum(len(sc[i].get_text().strip()) for i in range(min(3, sc.page_count)))}")
sc.close()

print("\n------ D) 超长文档工程指标 ------")
long_path = P("论文样本", "UnderstandingDeepLearning_02_09_26_C.pdf")
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
    lp = load(P("论文样本", "LongBook_540p_fixture.pdf"), "LongBook_540p.pdf")
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

"""图谱规模压测：构造 20 / 40 篇带真实引用关系的合成文献，测量建图与 HTML 渲染耗时。

对应评分项：全栈工程质量与系统鲁棒性 ——「大图谱（20+ 论文）节点渲染流畅」。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.state import Container
from services import library, graph as gsvc


def make_corpus(ctx, n=20):
    """生成 n 篇合成论文：每篇引用比它早的 2-4 篇，参考文献条目含被引标题全文。"""
    for fn in list(ctx.paper_order):
        library.remove_paper(ctx, fn)
    words = ("quantum lattice graph speech vision protein climate kernel tensor "
             "diffusion memory attention routing fusion sampling gradient clustering "
             "parsing auditing welding brewing weaving carving milking baking forging "
             "sailing rowing diving cycling skating skiing hiking climbing rowing2 "
             "singing ringing winding binding finding grinding blending spending").split()
    titles = [f"{words[i % len(words)]} {words[(i * 7 + 3) % len(words)]} "
              f"{words[(i * 13 + 5) % len(words)]} study {i}" for i in range(n)]
    assert len(set(titles)) == n, "标题需互不相同以避免词面误匹配"
    for i in range(n):
        body = (f"{titles[i]} Abstract This paper presents method {i}. " + "detail. " * 120)
        refs = []
        k = min(i, 4)
        for j in range(i - k, i):
            refs.append(f"[{j + 1}] Prince S. {titles[j]}. 20{15 + j}.")
        full = body + "\n\nReferences\n" + "\n".join(refs)
        library.add_text_corpus(ctx, f"paper_{i:02d}.pdf", full,
                                {"title": titles[i], "year": str(2015 + i)})
    return n


def bench(n):
    ctx = Container()
    make_corpus(ctx, n)
    t0 = time.time()
    G, edges = gsvc.build_graph(ctx)
    t_build = time.time() - t0
    t1 = time.time()
    m = gsvc.analyze(G)
    corner, edge = gsvc.classify_papers(G, m)
    t_analyze = time.time() - t1
    t2 = time.time()
    html = gsvc.graph_html(G, m, corner)
    t_html = time.time() - t2
    print(f"[n={n:>3}] 分块 {len(ctx.chunks):>5} | 建图 {t_build:6.2f}s "
          f"(节点 {len(G.nodes)}, 边 {len(edges)}) | 图论 {t_analyze:5.2f}s "
          f"| 渲染HTML {t_html:4.2f}s | HTML {len(html)/1024:.0f}KB")
    for fn in list(ctx.paper_order):
        library.remove_paper(ctx, fn)
    return t_build, len(edges)


print("========== 引用图谱规模压测（倒排索引候选 + 语义兜底）==========")

# 安全闸门：make_corpus() 会清空文献库，绝不允许静默执行。
if "--force" not in sys.argv:
    _probe = Container()
    _n = len(_probe.paper_order)
    if _n:
        print(f"⚠️ 压测会清空文献库（当前 {_n} 篇），脚本不做备份。")
        print("   先备份再重跑：cp -r storage storage.bak && python scripts/bench_graph.py --force")
        sys.exit(2)

base = None
for n in (20, 40):
    t, e = bench(n)
    if base is None:
        base = t

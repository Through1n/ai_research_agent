"""离线自测（无需 API Key）：验证 语义去重 / 混合检索 / 真实引用图谱 / Agent 工具编排闭环。"""
import sys
import types
import json

sys.path.insert(0, r"C:\Users\Administrator\ai_research_agent")

from services.state import Container
from services import library, rag
from services import graph as gsvc
from config import DEMO_PAPERS


def reset(ctx):
    ids = ctx.collection.get().get("ids", [])
    if ids:
        ctx.collection.delete(ids=ids)
    ctx.papers, ctx.paper_order, ctx.chunks, ctx.likes = {}, [], [], {}
    ctx.rebuild_bm25(); ctx.save_library()


ctx = Container()
reset(ctx)

print("========== 1) 加载演示语料 + 内容级语义去重 ==========")
order = ["1706.03762v7.pdf", "1706.03762v1.pdf", "1810.04805v2.pdf"]
for fn in order:
    info = DEMO_PAPERS[fn]
    emb = ctx.embed([info["text"][:4000]])[0]
    fps = library._paper_fingerprints(ctx, info["text"])
    dup, sim, ev = library.check_duplicate(ctx, emb, arxiv_id=info.get("arxiv_id"),
                                           fingerprints=fps, title=info.get("title"))
    print(f"{fn:18s} 查重命中={dup} 相似度={sim:.3f}" + (f"\n{'':18s} 依据：{ev}" if ev else ""))
    library.add_text_corpus(ctx, fn, info["text"], info)

print("\n------ 1b) 改名后仍按内容去重（不依赖文件名）------")
renamed = "my_notes_attention_paper.pdf"
emb2 = ctx.embed([DEMO_PAPERS["1706.03762v1.pdf"]["text"][:4000]])[0]
fps2 = library._paper_fingerprints(ctx, DEMO_PAPERS["1706.03762v1.pdf"]["text"])
dup2, sim2, ev2 = library.check_duplicate(ctx, emb2, fingerprints=fps2)
assert dup2, "内容级去重失败：改名论文未被识别"
print(f"✅ 无 arXiv 文件名 `{renamed}` 仍命中 → {dup2}（{sim2:.1%}）依据：{ev2[:80]}")

print("\n========== 2) 混合检索（中文查询需 BM25 真实生效）==========")
for q in ["训练用了什么 GPU", "BLEU 分数", "BERT 参数量"]:
    src, dbg = rag.retrieve(ctx, q)
    print(f"\n问题：{q}")
    print(f"  向量召回 {len(dbg['dense'])}，BM25 召回 {len(dbg['sparse'])}，RRF 融合后 {len(dbg['fused'])}")
    if dbg.get("variants"):
        print(f"  跨语言扩展：{dbg['variants'][0][:70]}")
    assert len(dbg["sparse"]) > 0, f"BM25 稀疏召回为 0，混合检索退化为单路：{q}"
    for c in src[:3]:
        print(f"   - {c['source']} p{c['page']}: {c['text'][:70].replace(chr(10),' ')}")
print("\n✅ 三组中文查询 BM25 均有召回，混合检索两路真实生效")

print("\n========== 3) 真实引用图谱 ==========")
G, edges = gsvc.build_graph(ctx)
metrics = gsvc.analyze(G)
corner, edge = gsvc.classify_papers(G, metrics)
print(f"节点 {len(G.nodes)}，真实引用边 {len(edges)}")
for a, b, ev in edges:
    print(f"  {a}  ->  {b}   [{ev[:60]}]")
print("基石论文:", [ctx.papers[n]['title'] for n in corner])
print("边缘衍生:", [ctx.papers[n]['title'] for n in edge])

print("\n========== 4) Mock LLM 跑 Agent 工具闭环 ==========")
class MockLLM:
    available = True; api_key = "mock"
    def json_chat(self, messages, temperature=0.0):
        p = messages[-1]["content"]
        if "专家" in p: return {"expert": "qa", "reason": "mock"}
        if "子任务" in p: return {"plan": ["检索带页码证据", "综合并标注来源"]}
        return {}
    def chat(self, messages, temperature=0.2, tools=None):
        if not any(m["role"] == "tool" for m in messages):
            tc = types.SimpleNamespace(
                id="call_1",
                function=types.SimpleNamespace(name="search_literature",
                                               arguments=json.dumps({"query": "训练 GPU"})))
            return types.SimpleNamespace(content=None, tool_calls=[tc])
        return types.SimpleNamespace(
            content="根据文献【1】，Transformer 使用 8 块 NVIDIA P100 GPU 训练。", tool_calls=None)

ctx.llm = MockLLM()
from core import agent
trace = agent.run(ctx, "Transformer 训练用了什么 GPU？")
for ev in trace.to_dicts():
    line = f"[{ev['kind']}] {ev['title']}"
    if ev["kind"] == "tool":
        line += f" name={ev['data']['name']}"
    if ev["kind"] == "final":
        line += f" => {ev['content']}"
    print(" ", line)

print("\n========== 5) 代码执行护栏 ==========")
from core.sandbox import validate_code, SandboxViolation
for bad, why in [("import os; os.system('dir')", "危险系统调用"),
                 ("__import__('subprocess')", "危险导入"),
                 ("x = 1\nprint(eval('2+2'))", "禁用 eval")]:
    try:
        validate_code(bad)
        raise AssertionError(f"护栏失效：{why} 未被拦截")
    except SandboxViolation as sv:
        print(f"✅ 已拦截（{why}）：{sv}")
validate_code("import numpy as np\nprint(np.mean([1,2,3]))")
print("✅ 正常数据科学代码放行")

print("\n========== 6) arXiv 追踪增量检测 ==========")
from services import track
track.reset_checkpoints(ctx)
items = [{"arxiv_id": "2510.00001", "title": "A"}, {"arxiv_id": "2510.00002", "title": "B"}]
new1 = track.diff_new(ctx, "cs.AI", items)
new2 = track.diff_new(ctx, "cs.AI", items)
assert len(new1) == 2 and len(new2) == 0, "追踪增量检测失败"
print("✅ 首轮识别 2 篇新论文，二轮去重后 0 篇（检查点生效）")

print("\n✅ 离线自测全部通过")

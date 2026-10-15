"""Round 13 模块⑤（引用图谱与综述）增强的单元测试。

覆盖 services/graph.py 末尾新增的确定性能力：
- 引文语境抽取 + 引用意图三分类（Scite Smart Citations 式离线近似）；
- 演示模式示意边不造语境、参考文献条目句不进语境、无语境如实报 no_context；
- 共引 + 文献耦合推荐（Connected Papers 式，「相关但未被直接引用」）；
- 引文速度热点（Price 累积优势模型信号）；
- generate_review / explore_future 的增强接线（确定性与 prompt 两条路径）；
- citation_insights agent 工具的注册与执行。

运行：python -m unittest discover -s tests   （零外部服务依赖，全部离线）
"""
import os
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import networkx as nx

from services import graph as gsvc


def fake_embed(texts):
    """确定性字符三元组哈希向量，替代真实 embedding 以便离线测试。"""
    dim = 64
    out = []
    for t in texts:
        v = [0.0] * dim
        low = (t or "").lower()
        for i in range(len(low) - 2):
            v[(hash(low[i:i + 3]) & 0x7fffffff) % dim] += 1.0
        n = sum(x * x for x in v) ** 0.5
        out.append([x / n if n else x for x in v])
    return out


class FakeCtx:
    """图谱模块可用的最小服务容器（不依赖 Streamlit 会话与网络）。"""

    def __init__(self, texts, titles, years, arxivs=None):
        self.chunks = []
        self.papers = {}
        self.paper_order = []
        self.llm = None
        self.embed = fake_embed
        arxivs = arxivs or {}
        for name, text in texts.items():
            for i in range(0, max(len(text), 1), 400):
                seg = text[i:i + 400]
                if seg.strip():
                    self.chunks.append({"id": f"{name}__{len(self.chunks)}", "source": name,
                                        "page": 1, "page_end": 1, "text": seg})
            self.papers[name] = {"filename": name, "title": titles[name],
                                 "abstract": text[:200], "year": years[name],
                                 "arxiv_id": arxivs.get(name, ""), "version": ""}
            self.paper_order.append(name)


# 四篇论文的测试语料：att 被三篇引用；crit 以转折语气引用 att（contrasting）；
# build 以"基于/扩展"语气引用 att（supporting）；survey 引用全部三篇但引用 att 时
# 正文无标题词重叠（no_context，考察"没找到语境"的诚实路径）。
T_ATT = ("Attention Is All You Need. We propose the Transformer based on attention. "
         "The model achieves 28.4 BLEU on WMT 2014. " + "training details. " * 30 +
         "References [13] Vaswani et al. Attention is all you need. NIPS 2017.")
T_CRIT = ("Sparse Attention Patterns Critique Study. We study transformer efficiency. "
          "Attention Is All You Need achieves strong translation quality. "
          "However, the quadratic cost of Attention Is All You Need is prohibitive for long sequences. "
          + "efficiency analysis. " * 30 +
          "References [13] Vaswani et al. Attention is all you need. NIPS 2017.")
T_BUILD = ("Attention Reuse Extension For Vision. We revisit attention mechanisms. "
           "Attention Is All You Need demonstrates that recurrence is unnecessary. "
           "We extend this line of work to image recognition. " + "vision details. " * 30 +
           "References [13] Vaswani et al. Attention is all you need. NIPS 2017.")
T_SURVEY = ("Transformer Literature Survey Overview Study. This survey reviews the field. "
            "Sparse Attention Patterns Critique Study analyzes efficiency concerns. "
            "Attention Reuse Extension For Vision generalizes attention to images. "
            + "survey notes. " * 30 +
            "References [13] Vaswani et al. Attention is all you need. NIPS 2017. "
            "[1] Sparse Attention Patterns Critique Study. 2020. "
            "[2] Attention Reuse Extension For Vision. 2019.")

TITLES = {"att.pdf": "Attention Is All You Need",
          "crit.pdf": "Sparse Attention Patterns Critique Study",
          "build.pdf": "Attention Reuse Extension For Vision",
          "survey.pdf": "Transformer Literature Survey Overview Study"}
YEARS = {"att.pdf": "2017", "crit.pdf": "2020", "build.pdf": "2019", "survey.pdf": "2021"}
ARXIVS = {"att.pdf": "1706.03762", "crit.pdf": "2101.00001",
          "build.pdf": "1902.00002", "survey.pdf": "2203.00003"}


def make_corpus_ctx():
    return FakeCtx({"att.pdf": T_ATT, "crit.pdf": T_CRIT,
                    "build.pdf": T_BUILD, "survey.pdf": T_SURVEY},
                   TITLES, YEARS, ARXIVS)


def _edge_ctx_texts(G):
    return {e: G.edges[e].get("intent") for e in G.edges}


class TestCitationIntentAnnotation(unittest.TestCase):
    """引文语境抽取与意图三分类（手工构图，完全隔离）。"""

    def _ctx_with(self, body):
        full = body + " " + "filler text. " * 20 + "References [13] Vaswani et al. Attention is all you need."
        return FakeCtx({"a.pdf": full}, {"a.pdf": "Alpha Beta Gamma Delta Paper"}, {"a.pdf": "2020"})

    def _graph(self):
        G = nx.DiGraph()
        G.add_node("a.pdf", title="Alpha Beta Gamma Delta Paper")
        G.add_node("b.pdf", title="Attention Is All You Need")
        G.add_edge("a.pdf", "b.pdf", evidence="参考文献条目高度匹配（100%）：Vaswani et al.")
        return G

    def test_contrasting_intent_with_context(self):
        ctx = self._ctx_with("Attention Is All You Need achieves strong translation quality. "
                             "However, the quadratic cost of Attention Is All You Need is prohibitive.")
        G = self._graph()
        stats = gsvc.annotate_citation_intents(ctx, G)
        self.assertEqual(G.edges["a.pdf", "b.pdf"]["intent"], "contrasting")
        texts = [c["text"] for c in G.edges["a.pdf", "b.pdf"]["contexts"]]
        self.assertTrue(any("However" in t for t in texts))
        # 参考文献条目本身不得被当成引文语境
        self.assertFalse(any("Vaswani" in t for t in texts))
        self.assertEqual(stats["by_intent"]["contrasting"], 1)

    def test_supporting_intent(self):
        ctx = self._ctx_with("Attention Is All You Need demonstrates that recurrence is unnecessary.")
        G = self._graph()
        gsvc.annotate_citation_intents(ctx, G)
        self.assertEqual(G.edges["a.pdf", "b.pdf"]["intent"], "supporting")

    def test_no_context_is_honest(self):
        """正文没有任何句子与被引标题实质重叠 → no_context，不伪装成 mention。"""
        ctx = self._ctx_with("We study an unrelated topic about protein folding structures.")
        G = self._graph()
        stats = gsvc.annotate_citation_intents(ctx, G)
        self.assertEqual(G.edges["a.pdf", "b.pdf"]["intent"], "no_context")
        self.assertEqual(G.edges["a.pdf", "b.pdf"]["contexts"], [])
        self.assertEqual(stats["by_intent"]["no_context"], 1)

    def test_demo_edge_never_gets_context(self):
        """演示模式示意边不是真实引用，一律跳过、不造引文语境。"""
        ctx = self._ctx_with("Attention Is All You Need achieves strong translation quality. "
                             "However, the quadratic cost of Attention Is All You Need is prohibitive.")
        G = self._graph()
        G.edges["a.pdf", "b.pdf"]["evidence"] = "演示模式：主题相似度 62%（示意关系，非真实引用）"
        stats = gsvc.annotate_citation_intents(ctx, G)
        self.assertEqual(stats["skipped_demo"], 1)
        self.assertEqual(G.edges["a.pdf", "b.pdf"]["intent"], "no_context")
        self.assertEqual(G.edges["a.pdf", "b.pdf"]["contexts"], [])

    def test_annotation_is_idempotent(self):
        ctx = self._ctx_with("Attention Is All You Need demonstrates that recurrence is unnecessary.")
        G = self._graph()
        s1 = gsvc.annotate_citation_intents(ctx, G)
        s2 = gsvc.annotate_citation_intents(ctx, G)
        self.assertEqual(s1, s2)
        self.assertTrue(G.graph.get("intents_done"))


class TestGraphInsightsIntegration(unittest.TestCase):
    """走真实 build_graph 的集成断言：意图分布 / 分歧 / 推荐 / 热点。"""

    def setUp(self):
        self.ctx = make_corpus_ctx()
        self.G, self.edges = gsvc.build_graph(self.ctx)

    def test_all_five_edges_detected(self):
        pairs = {(a, b) for a, b, _ in self.edges}
        self.assertEqual(pairs, {("crit.pdf", "att.pdf"), ("build.pdf", "att.pdf"),
                                 ("survey.pdf", "crit.pdf"), ("survey.pdf", "build.pdf"),
                                 ("survey.pdf", "att.pdf")})

    def test_intent_distribution(self):
        stats = gsvc.annotate_citation_intents(self.ctx, self.G)
        by = stats["by_intent"]
        self.assertEqual(by["contrasting"], 1)   # crit → att（However…）
        self.assertEqual(by["supporting"], 1)    # build → att（demonstrates…）
        self.assertEqual(by["mention"], 2)       # survey → crit / build
        self.assertEqual(by["no_context"], 1)    # survey → att（正文无标题词重叠）
        self.assertEqual(stats["n_edges"], 5)

    def test_disagreement_points_quote_original_sentence(self):
        ds = gsvc.disagreement_points(self.ctx, self.G)
        self.assertEqual(len(ds), 1)
        d = ds[0]
        self.assertEqual((d["from"], d["to"]), ("crit.pdf", "att.pdf"))
        self.assertTrue(any("However" in c for c in d["contexts"]))

    def test_cocitation_and_coupling_recommendation(self):
        recs = {r["paper"]: r["recommendations"] for r in gsvc.recommend_related(self.ctx, self.G)}
        # crit 与 build 无直接边，但共被 survey 引用且共同引用 att → 双向推荐
        self.assertIn("crit.pdf", recs)
        top = recs["crit.pdf"][0]
        self.assertEqual(top["paper"], "build.pdf")
        self.assertIn("共引", top["method"])
        self.assertIn("文献耦合", top["method"])
        self.assertIn("Attention Is All You Need", top["evidence"])
        # 已有直接引用边的 pair 不出现在图信号推荐里
        self.assertNotIn("att.pdf", recs)

    def test_hotspot_velocity(self):
        hot = gsvc.detect_hotspots(self.ctx, self.G)
        self.assertEqual(hot[0]["paper"], "att.pdf")     # 2017 年、被引 3 次 → 速度最高
        cur = time.localtime().tm_year
        self.assertEqual(hot[0]["velocity"], round(3 / max(1, cur - 2017), 3))
        self.assertEqual(len(hot), 3)                    # survey 入度 0，不参与
        self.assertNotIn("survey.pdf", [h["paper"] for h in hot])

    def test_graph_insights_degraded_gracefully(self):
        """recommend_related 内部异常不应炸掉整个 insights（综述链路优先）。"""
        ins = gsvc.graph_insights(self.ctx, self.G)
        self.assertEqual(ins["n_edges"], 5)
        self.assertTrue(ins["disagreements"])
        self.assertTrue(ins["hotspots"])
        md = gsvc.insights_markdown(self.ctx, ins)
        self.assertIn("引文语境", md)
        self.assertIn("语料规模坦诚说明", md)   # 4 节点 < SMALL_CORPUS_NODES，必须坦诚标注


class TestEnhancedReviewAndFuture(unittest.TestCase):
    """generate_review / explore_future 的 Round 13 接线（两条路径）。"""

    def setUp(self):
        self.ctx = make_corpus_ctx()
        self.G, _ = gsvc.build_graph(self.ctx)
        self.metrics = gsvc.analyze(self.G)
        self.corner, _ = gsvc.classify_papers(self.G, self.metrics)

    def test_deterministic_review_includes_insights(self):
        """LLM 不可用 → 确定性综述 + 引文语境附录，且不出现伪 LLM 文案。"""
        with mock.patch.object(gsvc, "_llm_text",
                               return_value="【LLM 调用失败：测试桩，无 Key】"):
            md = gsvc.generate_review(self.ctx, self.G, self.metrics, self.corner)
        self.assertNotIn("【LLM", md)
        self.assertIn("### 一、研究脉络", md)               # 原四段式综述仍在
        self.assertIn("引文语境与研究空白", md)             # 新增确定性附录
        self.assertIn("However", md)                       # contrasting 引文语境原文被引用
        self.assertIn("文献耦合", md)                       # 共引/耦合推荐
        self.assertIn("引文速度", md)                       # 新兴热点

    def test_review_prompt_includes_insight_blocks(self):
        """LLM 可用 → prompt 必须注入引文语境/推荐/热点，并禁止编造引文句。"""
        captured = {}

        def fake_llm(ctx_, prompt, temperature=0.4):
            captured["prompt"] = prompt if isinstance(prompt, str) else str(prompt)
            return "## 一、研究脉络\n（模拟综述正文）"

        with mock.patch.object(gsvc, "_llm_text", side_effect=fake_llm):
            md = gsvc.generate_review(self.ctx, self.G, self.metrics, self.corner)
        p = captured["prompt"]
        self.assertIn("引文语境", p)
        self.assertIn("新兴热点", p)
        self.assertIn("禁止编造引文句", p)   # 抗幻觉硬约束
        self.assertIn("（模拟综述正文）", md)
        self.assertIn("引文语境与研究空白", md)   # 附录同样附上

    def test_explore_future_includes_hotspots(self):
        with mock.patch.object(gsvc, "_llm_text",
                               return_value="【LLM 调用失败：测试桩，无 Key】"):
            md = gsvc.explore_future(self.ctx, self.G)
        self.assertNotIn("【LLM", md)
        self.assertIn("新兴热点", md)
        self.assertIn("引文速度", md)
        self.assertIn("Price", md)   # 口径说明（Price 累积优势模型）


class TestCitationInsightsTool(unittest.TestCase):
    """citation_insights agent 工具：注册 + 离线可执行。"""

    def test_tool_registered(self):
        from tools.definitions import build_registry
        r = build_registry()
        self.assertIn("citation_insights", r.names())

    def test_tool_runs_offline(self):
        from tools.definitions import t_citation_insights
        out = t_citation_insights(make_corpus_ctx())
        self.assertEqual(out["summary"]["edges"], 5)
        self.assertEqual(out["summary"]["intent_counts"]["contrasting"], 1)
        self.assertEqual(out["summary"]["n_disagreements"], 1)
        self.assertIn("引文语境", out["insights_markdown"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

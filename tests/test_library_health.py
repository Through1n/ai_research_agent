# -*- coding: utf-8 -*-
"""文献库健康诊断（`services.library.library_health`）单测。

为什么单独测：这个函数是「首跑自愈」的判据，2026-10-25 前后出过一次误判——
内置演示语料只有 1 个合成"页"，chunk 必然 100% 落在 page 1，与「PDF 解析失败
只剩首页」在数据上完全同形，于是**设计态的演示库被当成故障**，评审打开文献库页
第一眼就是红色「内容异常」横幅。

修法不是放宽阈值（那会让真故障漏网），而是给摘录语料打 `corpus="text"` 标记，
整库都是摘录语料时报 demo_only 并豁免；一旦混入真实 PDF 解析结果，照旧判定。
下面两组用例分别钉住「豁免」和「不能连真故障一起豁免」。
"""

import unittest

from services.library import library_health


class _Ctx:
    """最小 ctx 替身：只带 library_health 会用到的三个属性。"""

    def __init__(self, papers, chunks):
        self.papers = papers          # {filename: paper_dict}
        self.chunks = chunks          # [{"source": fn, "page": n, "text": "..."}]

    @property
    def files(self):
        return list(self.papers.keys())


def _chunks(fn, pages):
    return [{"source": fn, "page": p, "text": "t"} for p in pages]


def _paper(corpus=""):
    return {"filename": "x", "corpus": corpus}


class TestLibraryHealth(unittest.TestCase):
    def test_empty_library(self):
        h = library_health(_Ctx({}, []))
        self.assertTrue(h["empty"])
        self.assertTrue(h["unhealthy"])
        self.assertIn("文献库为空", h["reasons"])

    def test_demo_corpus_only_is_not_degraded(self):
        """整库都是摘录语料 → 设计态，不报退化。"""
        papers = {"a.pdf": _paper("text"), "b.pdf": _paper("text")}
        chunks = _chunks("a.pdf", [1, 1]) + _chunks("b.pdf", [1])
        h = library_health(_Ctx(papers, chunks))
        self.assertTrue(h["demo_only"])
        self.assertFalse(h["degraded"])
        self.assertFalse(h["unhealthy"])
        self.assertEqual(h["reasons"], [])
        self.assertEqual(h["page1_ratio"], 1.0)          # 事实照实报，只是不判故障
        self.assertEqual(sorted(h["corpus_papers"]), ["a.pdf", "b.pdf"])

    def test_real_pdf_parsed_normally_is_healthy(self):
        papers = {"r.pdf": _paper("")}
        chunks = _chunks("r.pdf", [1, 2, 3, 4])
        h = library_health(_Ctx(papers, chunks))
        self.assertFalse(h["demo_only"])
        self.assertFalse(h["unhealthy"])

    def test_real_pdf_stuck_on_page_one_still_flagged(self):
        """真 PDF 只解析出首页——这是真故障，豁免不能把它一起放过。"""
        papers = {"r.pdf": _paper("")}
        chunks = _chunks("r.pdf", [1, 1, 1])
        h = library_health(_Ctx(papers, chunks))
        self.assertFalse(h["demo_only"])
        self.assertTrue(h["degraded"])
        self.assertTrue(any("第 1 页" in r for r in h["reasons"]))

    def test_mixed_library_does_not_get_exemption(self):
        """混入真实 PDF 后不再豁免：演示语料确实会拉低检索质量。"""
        papers = {"a.pdf": _paper("text"), "r.pdf": _paper("")}
        chunks = _chunks("a.pdf", [1, 1, 1, 1]) + _chunks("r.pdf", [1, 1])
        h = library_health(_Ctx(papers, chunks))
        self.assertFalse(h["demo_only"])
        self.assertTrue(h["degraded"])

    def test_legacy_paper_without_corpus_field(self):
        """旧库记录没有 corpus 字段 → 按真实 PDF 处理，不误豁免。"""
        papers = {"old.pdf": {"filename": "old.pdf"}}
        chunks = _chunks("old.pdf", [1, 1])
        h = library_health(_Ctx(papers, chunks))
        self.assertFalse(h["demo_only"])
        self.assertTrue(h["degraded"])

    def test_median_chunks_threshold(self):
        """中位数 < 3 也算退化（即使 page 分布正常）。"""
        papers = {"r.pdf": _paper("")}
        chunks = _chunks("r.pdf", [1, 2])
        h = library_health(_Ctx(papers, chunks))
        self.assertTrue(h["degraded"])
        self.assertLess(h["median_chunks_per_paper"], 3)


if __name__ == "__main__":
    unittest.main()

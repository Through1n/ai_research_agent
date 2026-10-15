"""单元/可量化测试：PDF 解析与文献库去重的优化点（零外部服务依赖）。

运行：
    python -m unittest tests.test_parsing_library -v
覆盖：
  - OCR 文本后处理（折词修复 / 空白归一化 / 空结果降级）—— 对应 v1_img 扫描件用例
  - 离线 MinHash 内容指纹去重（同文≈1.0、异文≈低、近版 v1/v7 高相似）—— 对应 v1/v7 用例
  - 插图↔图注就近关联（超距/侧向不误配）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import parsing, library


class OCRPostprocessTest(unittest.TestCase):
    def test_hyphen_break_fixed(self):
        # 跨行被硬断开的英文折词（下个词首字母小写）应拼接
        text = "the atten-\ntion mechanism is used"
        out = parsing._postprocess_ocr_text(text)
        self.assertIn("attention", out)
        self.assertNotIn("atten-\n", out)

    def test_hyphen_not_merged_when_title_like(self):
        # 下一个词首字母大写（疑似标题/缩写断行）不强行合并
        text = "BERT-\nPre training"
        out = parsing._postprocess_ocr_text(text)
        self.assertIn("BERT-", out)

    def test_unicode_nfkc_normalized(self):
        text = "Ａｔｔｅｎｔｉｏｎ　ｉｓ　Ａｌｌ"
        out = parsing._postprocess_ocr_text(text)
        self.assertIn("Attention", out)
        self.assertNotIn("Ａ", out)

    def test_whitespace_and_blankline_normalized(self):
        text = "word1    word2\n\n\n\nword3"
        out = parsing._postprocess_ocr_text(text)
        self.assertNotIn("    ", out)
        self.assertNotIn("\n\n\n", out)

    def test_empty_result_marker(self):
        # 去噪后无有效字符 → 返回 [OCR 结果为空] 降级占位（is_ocr_error_text 可拦截）
        out = parsing._postprocess_ocr_text("…  ___  …")
        self.assertTrue(out.startswith("[OCR"))


class MinHashDedupTest(unittest.TestCase):
    def test_identical_text_high_jaccard(self):
        t = "Attention is all you need. We propose a new simple network architecture."
        a, b = library._minhash_signature(t), library._minhash_signature(t)
        self.assertAlmostEqual(library.minhash_jaccard(a, b), 1.0, delta=0.05)

    def test_different_text_low_jaccard(self):
        a = library._minhash_signature("The quick brown fox jumps over the lazy dog.")
        b = library._minhash_signature("Quantum computing leverages superposition for speedup.")
        self.assertLess(library.minhash_jaccard(a, b), 0.3)

    def test_near_version_high_jaccard(self):
        # 模拟 v1 与 v7：构造大体积、唯一词级 4-gram 集合的 base（模拟长篇论文），
        # v7 仅改写约 2% 的词（真实同论文不同版本的微小改稿比例）。
        base_words = [f"w{i}" for i in range(5000)]
        base = " ".join(base_words)
        v7_words = list(base_words)
        for i in range(0, 5000, 50):           # 改写约 2% 的词
            v7_words[i] = f"c{i}"
        v7 = " ".join(v7_words)
        a, b = library._minhash_signature(base), library._minhash_signature(v7)
        sim = library.minhash_jaccard(a, b)
        # 微小改稿下，同论文不同版本内容指纹应明显高于 0.7（与 same_arxiv 路径共同兜底）
        self.assertGreater(sim, 0.7)

    def test_empty_signature(self):
        self.assertEqual(library._minhash_signature(""), [])
        self.assertEqual(library.minhash_jaccard([], [1, 2, 3]), 0.0)

    def test_signature_deterministic(self):
        t = "deterministic signature should be stable across calls"
        self.assertEqual(library._minhash_signature(t), library._minhash_signature(t))


class OfflineDedupTriggerTest(unittest.TestCase):
    """直接验证官方 v1/v7 用例：离线（无 embedding）时仍能命中内容级去重。"""

    def _fake_ctx(self, papers):
        class Ctx:
            pass
        ctx = Ctx()
        ctx.paper_order = list(papers.keys())
        ctx.papers = papers
        return ctx

    def test_offline_v1_v7_detected(self):
        # 库中已有 v7（带 minhash 签名与相同 arXiv 号），新上传 v1，无 embedding
        v7_text = ("We propose the Transformer, a model relying entirely on attention. "
                   "It achieves superior quality while being more parallelizable. "
                   "The Transformer follows an encoder decoder structure with self attention." * 100)
        v1_text = (v7_text[:len(v7_text) // 2]
                   + " In this revision we add more experiments and clearer training details. "
                   + v7_text[len(v7_text) // 2:]
                   + " We also fix minor typos and improve the related work section.")
        v7_sig = library._minhash_signature(v7_text)
        v1_sig = library._minhash_signature(v1_text)
        ctx = self._fake_ctx({
            "1706.03762v7.pdf": {
                "title": "Attention Is All You Need", "arxiv_id": "1706.03762",
                "embedding": None, "fingerprints": [], "minhash": v7_sig,
            },
        })
        # 离线：embedding=None，但提供 new_minhash
        dup_name, sim, ev = library.check_duplicate(
            ctx, None, arxiv_id="1706.03762", fingerprints=[],
            title="Attention Is All You Need", new_minhash=v1_sig)
        self.assertEqual(dup_name, "1706.03762v7.pdf")
        self.assertGreaterEqual(sim, 0.5)
        self.assertIn("arXiv 号相同", ev)

    def test_offline_different_paper_not_dup(self):
        a_text = "Quantum computing leverages superposition and entanglement for speedup." * 50
        b_text = "Classical thermodynamics studies heat, work and entropy in macroscopic systems." * 50
        ctx = self._fake_ctx({
            "paperA.pdf": {"title": "Quantum", "arxiv_id": "1111.0001",
                           "embedding": None, "fingerprints": [],
                           "minhash": library._minhash_signature(a_text)},
        })
        dup_name, sim, ev = library.check_duplicate(
            ctx, None, arxiv_id="2222.0002", fingerprints=[],
            title="Thermo", new_minhash=library._minhash_signature(b_text))
        self.assertIsNone(dup_name)


if __name__ == "__main__":
    unittest.main()
    def test_near_caption_associated(self):
        image = [(100, 100, 200, 300)]  # 图：y 100~300
        captions = [{"bbox": (100, 305, 300, 320), "text": "Figure 1: example"}]
        cap, bbox, idx = parsing._nearest_caption(image, captions, set(), max_gap=100)
        self.assertEqual(idx, 0)
        self.assertIn("Figure 1", cap)

    def test_far_caption_rejected_by_max_gap(self):
        image = [(100, 100, 200, 300)]
        captions = [{"bbox": (100, 800, 300, 815), "text": "Figure 9: unrelated"}]
        cap, bbox, idx = parsing._nearest_caption(image, captions, set(), max_gap=100)
        # 间距 500 超过 max_gap=100 → 不应误关联
        self.assertEqual(idx, None)
        self.assertEqual(cap, "")

    def test_side_caption_not_overlapping(self):
        image = [(100, 100, 200, 300)]
        # 完全在右侧、与图横向几乎无重叠 → _caption_distance 返回 None
        captions = [{"bbox": (500, 305, 600, 320), "text": "Figure 2: side"}]
        cap, bbox, idx = parsing._nearest_caption(image, captions, set(), max_gap=100)
        self.assertEqual(idx, None)

    def test_used_caption_skipped(self):
        image = [(100, 100, 200, 300)]
        captions = [{"bbox": (100, 305, 300, 320), "text": "Figure 1: a"},
                    {"bbox": (100, 330, 300, 345), "text": "Figure 2: b"}]
        cap, bbox, idx = parsing._nearest_caption(image, captions, {0}, max_gap=100)
        self.assertEqual(idx, 1)


if __name__ == "__main__":
    unittest.main()

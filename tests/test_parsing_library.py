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


class PreparePdfMinHashRegressionTest(unittest.TestCase):
    """2026-10-22 事故回归：prepare_pdf 曾在完整性校验构造 prepared_base 时
    引用尚未赋值的 minhash（赋值在完整性校验之后），每次调用必抛
    UnboundLocalError，被外层 except 吞成「PDF 完整性校验异常：cannot access
    local variable 'minhash'」——所有 PDF 解析 100% 失败，且报错指错方向
    （用户以为"论文不适配"）。本测试用真实 PDF 走完整 prepare_pdf 链路。
    """

    def _make_pdf(self, pages=3):
        import fitz
        doc = fitz.open()
        for i in range(pages):
            page = doc.new_page()
            # 用 insert_textbox 而非 insert_text：后者写不换行的单行，超出页宽部分
            # 会被裁掉（实测 3 页样本每页只剩约 114 字符，单页用例因此撞上
            # 完整性校验的 200 字符下限，撞出一场与回归目标无关的 FAIL）。
            page.insert_textbox(fitz.Rect(72, 72, page.rect.width - 72, page.rect.height - 72),
                                f"Page {i + 1}. " + "Attention is all you need. " * 40,
                                fontsize=11)
        raw = doc.write()
        doc.close()
        return raw
    def _fake_ctx(self):
        class Ctx:
            pass
        ctx = Ctx()

        class _LLM:
            available = False
        ctx.llm = _LLM()
        ctx.paper_order = []
        ctx.papers = {}
        return ctx

    def test_real_pdf_no_unbound_minhash(self):
        prepared = library.prepare_pdf(self._fake_ctx(), data=self._make_pdf(),
                                       filename="regression_minhash.pdf")
        self.assertIsInstance(prepared, dict)
        # 关键断言一：不得再出现被吞掉的 UnboundLocalError 假错误
        self.assertFalse(prepared.get("error"), prepared.get("error"))
        self.assertNotIn("minhash", str(prepared.get("error") or ""))
        self.assertNotIn("完整性校验异常", str(prepared.get("error") or ""))
        # 关键断言二：minhash 签名被真实算出（完整性校验与查重都依赖它）
        self.assertTrue(prepared.get("minhash"))
        self.assertEqual(prepared.get("page_count"), 3)
        self.assertIn("Attention", prepared.get("full_text") or "")

    def test_single_page_pdf_ok(self):
        # 单页 PDF 同样不得触发该回归（detect_scanned 单页退化路径）
        prepared = library.prepare_pdf(self._fake_ctx(), data=self._make_pdf(pages=1),
                                       filename="single.pdf")
        self.assertFalse(prepared.get("error"), prepared.get("error"))
        self.assertTrue(prepared.get("minhash"))


class OpenDocCompatTest(unittest.TestCase):
    """open_doc 适配加固（2026-10 R19）：空密码认证 + 既有兼容性不回归。

    实证基线（PyMuPDF 1.28.2）：MuPDF 本身已容忍头部垃圾前缀 / 尾部垃圾 /
    仅所有者密码的 PDF——这些是回归哨兵，不是新功能；新增能力只有
    needs_pass 时的 authenticate("") 分支（stub 直接覆盖）。
    """

    def _pdf_bytes(self, text="compat probe text " * 20):
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 500, 700), text, fontsize=11)
        raw = doc.write()
        doc.close()
        return raw

    def test_normal_pdf_opens(self):
        doc = parsing.open_doc(self._pdf_bytes())
        try:
            self.assertGreaterEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_garbage_prefix_tolerated(self):
        # MuPDF 自愈：头部垃圾前缀不应成为拒收理由（回归哨兵）
        raw = b"GARBAGE-PREFIX\n" + self._pdf_bytes()
        doc = parsing.open_doc(raw)
        try:
            self.assertEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_trailing_garbage_tolerated(self):
        raw = self._pdf_bytes() + b"\nTRAILING-GARBAGE"
        doc = parsing.open_doc(raw)
        try:
            self.assertEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_owner_only_password_opens(self):
        # 仅设所有者密码（用户密码为空）的出版社 PDF：应直接可读
        import fitz
        enc = fitz.open()
        p = enc.new_page()
        p.insert_textbox(fitz.Rect(72, 72, 500, 700), "owner only password " * 20, fontsize=11)
        raw = enc.write(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw="")
        enc.close()
        doc = parsing.open_doc(raw)
        try:
            self.assertEqual(doc.page_count, 1)
            self.assertIn("owner only", doc[0].get_text())
        finally:
            doc.close()

    def test_real_user_password_rejected(self):
        import fitz
        enc = fitz.open()
        p = enc.new_page()
        p.insert_text((72, 72), "needs real password")
        raw = enc.write(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="realuser")
        enc.close()
        with self.assertRaises(RuntimeError) as cm:
            parsing.open_doc(raw)
        self.assertIn("加密", str(cm.exception))

    def test_empty_password_auth_branch(self):
        """stub 直接覆盖新增分支：needs_pass=True 但空密码可认证 → 应放行。"""
        class _FakeDoc:
            needs_pass = True
            def authenticate(self, pw):
                return 2 if pw == "" else 0
        orig_open = parsing.fitz.open
        parsing.fitz.open = lambda *a, **k: _FakeDoc()
        try:
            doc = parsing.open_doc(b"%PDF-fake")
            self.assertIsInstance(doc, _FakeDoc)   # 认证成功 → 返回文档而非抛错
        finally:
            parsing.fitz.open = orig_open

    def test_extract_falls_back_to_plain_text(self):
        """栏感知产出空文本时回退纯 get_text（新分支，stub 制造空块场景）。"""
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 500, 700), "fallback probe content " * 10, fontsize=11)
        try:
            orig = parsing.extract_page_blocks_column_aware
            parsing.extract_page_blocks_column_aware = lambda p: []   # 栏感知全被滤空
            try:
                text = parsing.extract_page_text_column_aware(page)
            finally:
                parsing.extract_page_blocks_column_aware = orig
            self.assertIn("fallback probe content", text)
        finally:
            doc.close()

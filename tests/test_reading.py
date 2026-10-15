"""模块三（版式感知阅读与翻译）Round13 新增能力单测：句级双语对齐 / 术语一致性 /
图文保位 / 划词上下文扩展。

全部离线确定性：不调 LLM、不触网、不依赖 API Key。
运行：python -m unittest tests.test_reading -v
"""
import os
import shutil
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.translate import (  # noqa: E402
    DEFAULT_GLOSSARY_TEXT,
    _figure_safe_top,
    _figures_html,
    _glossary_apply,
    _glossary_inject,
    _glossary_mark,
    align_pairs,
    apply_glossary,
    bilingual_html,
    extract_terms,
    glossary_prompt_block,
    native_page_html,
    parse_glossary,
    page_image_rects,
    split_sentences,
)


class _Rect:
    def __init__(self, w, h):
        self.width = float(w)
        self.height = float(h)


class _StubPage:
    """最小 page 桩：只实现 get_image_info / get_images / rect。"""

    def __init__(self, infos=None, raises=False, images=None, rect=(612.0, 792.0)):
        self._infos = infos or []
        self._raises = raises
        self._images = images or []
        self.rect = _Rect(*rect)

    def get_image_info(self, xrefs=False):
        if self._raises:
            raise RuntimeError("boom")
        return [dict(i) for i in self._infos]

    def get_images(self, full=True):
        return list(self._images)


class SplitSentencesTest(unittest.TestCase):
    def test_en_basic(self):
        self.assertEqual(split_sentences("Hello world. Second one!"),
                         ["Hello world.", "Second one!"])

    def test_en_abbrev_not_split(self):
        # et al. / Fig. 1 / U.S. 不断句；句末数字后的句点要断
        s = split_sentences("We use Adam. The rate is 0.001. "
                            "See Fig. 1 for details (Vaswani et al., 2017).")
        self.assertEqual(len(s), 3, s)
        self.assertIn("Vaswani et al., 2017).", s[-1])

    def test_en_decimal_not_split(self):
        self.assertEqual(split_sentences("Pi is 3.14 and U.S. teams win. Really!"),
                         ["Pi is 3.14 and U.S. teams win.", "Really!"])

    def test_zh_punctuation(self):
        self.assertEqual(split_sentences("这是一个句子。第二句！第三句？"),
                         ["这是一个句子。", "第二句！", "第三句？"])

    def test_empty_and_garbage(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences(None), [])
        self.assertEqual(split_sentences("   \n  "), [])

    def test_closing_quote_stays_with_sentence(self):
        # 句末闭合引号/括号归入本句，不孤零零挂到下一句开头（Round 16 父代修复）
        self.assertEqual(split_sentences('He said "it works." Then he left.'),
                         ['He said "it works."', "Then he left."])
        self.assertEqual(split_sentences('"Hello." "Goodbye."'),
                         ['"Hello."', '"Goodbye."'])
        self.assertEqual(split_sentences("End here.) Next starts."),
                         ["End here.)", "Next starts."])
        # 缩写路径不受影响：引号在句中不断句
        self.assertEqual(split_sentences('See "Fig. 1" for details. Thanks.'),
                         ['See "Fig. 1" for details.', "Thanks."])


class AlignPairsTest(unittest.TestCase):
    EN3 = ("We propose a new method. It uses attention. "
           "The model achieves 32.4 BLEU points.")
    ZH3 = "我们提出一种新方法。该方法使用注意力机制。该模型取得 32.4 BLEU 分数。"

    def test_one_to_one(self):
        pairs = align_pairs(self.EN3, self.ZH3)
        self.assertEqual(len(pairs), 3)
        self.assertIn("We propose", pairs[0][0])
        self.assertIn("我们提出", pairs[0][1])

    def test_merge_two_en_one_zh(self):
        # 译文并句：两个英文句 → 一个中文句，DP 必须识别为 2:1 而不是错位 1:1
        en = "We use Adam. The learning rate is 0.001."
        zh = "我们使用 Adam，学习率为 0.001。"
        pairs = align_pairs(en, zh)
        self.assertEqual(len(pairs), 1, pairs)
        self.assertIn("We use Adam.", pairs[0][0])
        self.assertIn("0.001", pairs[0][1])

    def test_anchor_number_alignment(self):
        # 数字锚点：含 32.4 的中文句必须对上含 32.4 的英文句（长度相近的干扰句存在）
        en = ("We train widely on many GPUs. The model achieves 32.4 BLEU "
              "points on the test set. Results look fine overall.")
        zh = "我们在许多 GPU 上广泛训练。测试集上该模型取得 32.4 BLEU 分数。整体结果不错。"
        pairs = align_pairs(en, zh)
        self.assertEqual(len(pairs), 3)
        hit = [p for p in pairs if "32.4" in p[1]]
        self.assertTrue(hit, pairs)
        self.assertIn("32.4", hit[0][0])

    def test_count_mismatch_still_covers_all(self):
        # 句数不匹配：3 英 ↔ 5 中，输出必须覆盖全部句子（漏译侧为空串）
        en = "One. Two. Three."
        zh = "一。二。三。四。五。"
        pairs = align_pairs(en, zh)
        joined_en = " ".join(p[0] for p in pairs)
        joined_zh = "".join(p[1] for p in pairs)
        for frag in ("One", "Two", "Three"):
            self.assertIn(frag, joined_en)
        for frag in ("一", "五"):
            self.assertIn(frag, joined_zh)

    def test_empty_side(self):
        self.assertEqual(align_pairs("", "只有中文。"), [("", "只有中文。")])
        self.assertEqual(align_pairs("Only English.", ""), [("Only English.", "")])
        self.assertEqual(align_pairs("", ""), [])

    def test_oversized_fallback_ordinal(self):
        # 超限（>160 句）退化为序号 1:1，不抛异常且有确定输出
        en = " ".join("Sentence number %d here." % i for i in range(200))
        zh = "".join("第 %d 句。" % i for i in range(200))
        pairs = align_pairs(en, zh)
        self.assertEqual(len(pairs), 200)


class GlossaryTest(unittest.TestCase):
    def test_parse_formats(self):
        text = ("# 注释\n\nattention = 注意力\ntransformer|变换器\n"
                "encoder：编码器\nlearning rate, 学习率\n坏行没有分隔符\n")
        gl = parse_glossary(text)
        got = {e: (z, v) for e, z, v in gl}
        self.assertEqual(got["attention"], ("注意力", []))
        self.assertEqual(got["transformer"], ("变换器", []))
        self.assertEqual(got["encoder"], ("编码器", []))
        self.assertEqual(got["learning rate"], ("学习率", []))

    def test_parse_variants_and_stopwords(self):
        gl = parse_glossary("dropout = 随机失活; 丢弃; 流失\nthe = 这\nit = 它\n")
        got = {e: (z, v) for e, z, v in gl}
        self.assertEqual(got["dropout"], ("随机失活", ["丢弃", "流失"]))
        self.assertNotIn("the", got)
        self.assertNotIn("it", got)

    def test_default_glossary_parses(self):
        gl = parse_glossary(DEFAULT_GLOSSARY_TEXT)
        self.assertGreaterEqual(len(gl), 25)
        self.assertTrue(all(z for _e, z, _v in gl))

    def test_prompt_block(self):
        gl = parse_glossary("attention = 注意力")
        block = glossary_prompt_block(gl)
        self.assertIn("术语表", block)
        self.assertIn("attention → 注意力", block)
        self.assertEqual(glossary_prompt_block([]), "")

    def test_inject(self):
        self.assertEqual(_glossary_inject("hello", []), "hello")
        out = _glossary_inject("hello", [("attention", "注意力", [])])
        self.assertTrue(out.startswith("【术语表】"))
        self.assertTrue(out.endswith("hello"))

    def test_apply_untranslated_term(self):
        zh = "该 encoder 与 Transformer 很重要"
        out = apply_glossary(zh, [("encoder", "编码器", []),
                                  ("transformer", "变换器", [])])
        self.assertEqual(out, "该 编码器 与 变换器 很重要")

    def test_apply_word_boundary(self):
        # 整词匹配：encoders 不被 encoder 规则误伤
        out = apply_glossary("encoders 很多", [("encoder", "编码器", [])])
        self.assertEqual(out, "encoders 很多")

    def test_apply_variants(self):
        out = apply_glossary("使用了丢弃法", [("dropout", "随机失活", ["丢弃法"])])
        self.assertEqual(out, "使用了随机失活")

    def test_apply_noop(self):
        self.assertEqual(apply_glossary("原样返回", []), "原样返回")

    def test_apply_alias_wrapper(self):
        self.assertEqual(_glossary_apply("该 encoder 重要", [("encoder", "编码器", [])]),
                         "该 编码器 重要")

    def test_mark(self):
        zh = "我们使用注意力机制与编码器。"
        out = _glossary_mark(zh, [("attention mechanism", "注意力机制", [])])
        self.assertIn('<mark class="gt"', out)
        self.assertIn(">注意力机制</mark>", out)
        # 长术语套短术语不二次包裹
        out2 = _glossary_mark("零样本与少样本", [("zero-shot", "零样本", []),
                                               ("样本", "样本", [])])
        self.assertEqual(out2.count("<mark"), 2)

    def test_extract_terms(self):
        text = ("The Transformer (TR) is great. We use the Transformer daily.\n"
                "BERT beats it. The Transformer wins.\n"
                "ImageNet helps. ImageNet helps again.\n")
        terms = dict(extract_terms(text))
        self.assertIn("Transformer", terms)          # 括号定义式 + 行内重复
        self.assertIn("ImageNet", terms)             # 行内重复 ≥2
        self.assertNotIn("The", terms)               # 停用词
        self.assertNotIn("The Transformer", terms)   # 前导冠词被剥离
        self.assertNotIn("We", terms)

    def test_extract_terms_empty(self):
        self.assertEqual(extract_terms(""), [])
        self.assertEqual(extract_terms("   "), [])


class FigureDirTest(unittest.TestCase):
    """fig_* 临时目录：项目私有位置 + 可验证清理 + 双位置陈旧清理（Round 16 父代修复）。"""

    def test_new_dir_in_project_scratch(self):
        import ui.page_reading as pr
        d = pr._new_figure_dir()
        try:
            self.assertTrue(os.path.isdir(d))
            self.assertIn(os.path.join("storage", "scratch"), os.path.abspath(d))
            self.assertTrue(os.path.basename(d).startswith("fig_"))
        finally:
            pr._remove_dir_verified(d)

    def test_remove_dir_verified_reports_failure(self):
        import ui.page_reading as pr
        # 已不存在的路径 → True（幂等：调用方只关心目录最终消失）
        missing = os.path.join(ROOT, "storage", "scratch",
                               "fig_definitely_absent_%d" % os.getpid())
        self.assertTrue(pr._remove_dir_verified(missing))
        # rmtree 删不掉的路径（文件而非目录）→ False：验证机制必须抓住静默失败
        probe = os.path.join(ROOT, "storage", "scratch",
                             "fig_notadir_%d" % os.getpid())
        os.makedirs(os.path.dirname(probe), exist_ok=True)
        with open(probe, "w") as fh:
            fh.write("x")
        try:
            self.assertFalse(pr._remove_dir_verified(probe))
        finally:
            os.remove(probe)

    def test_cleanup_covers_both_locations(self):
        import tempfile as _tf
        import ui.page_reading as pr
        old = time.time() - 48 * 3600
        made = []
        try:
            legacy = _tf.mkdtemp(prefix="fig_legacy_")
            os.utime(legacy, (old, old))
            made.append(legacy)
            scratch = pr._figure_scratch_root()
            fresh_root = _tf.mkdtemp(prefix="fig_newroot_", dir=scratch)
            os.utime(fresh_root, (old, old))
            made.append(fresh_root)
            removed = pr.cleanup_stale_figure_dirs(24)
            self.assertGreaterEqual(removed, 2, removed)
            self.assertFalse(os.path.exists(legacy))
            self.assertFalse(os.path.exists(fresh_root))
        finally:
            for p in made:
                shutil.rmtree(p, ignore_errors=True)

    def test_fresh_dir_survives(self):
        import tempfile as _tf
        import ui.page_reading as pr
        d = _tf.mkdtemp(prefix="fig_freshkeep_", dir=pr._figure_scratch_root())
        try:
            pr.cleanup_stale_figure_dirs(24)
            self.assertTrue(os.path.exists(d))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class BilingualHtmlTest(unittest.TestCase):
    def test_sentence_spans(self):
        b = bilingual_html([("First one. Second sentence here.", "第一句。第二句。")])
        self.assertEqual(b.count('class="sn"'), 4)        # 左右各 2 句
        self.assertIn("data-sidx", b)
        self.assertIn(".sn.on", b)                        # 句级联动样式注入

    def test_backward_compat_invariants(self):
        # 冒烟脚本既有断言必须继续成立（比例同步 / isSyncing / rAF / data-index）
        b = bilingual_html([("Hello world.", "你好世界。"), ("Second para.", "第二段。")])
        self.assertIn("scrollHeight - el.clientHeight", b)
        self.assertIn("ratio(src) * m", b)
        self.assertGreaterEqual(b.count("isSyncing"), 4)
        self.assertIn("requestAnimationFrame", b)
        self.assertIn("m <= 0", b)
        self.assertIn("getAttribute('data-index')", b)
        self.assertIn('p[data-index="', b)
        self.assertNotIn("（待翻译）", b)

    def test_pending_translation_placeholder(self):
        b = bilingual_html([("Hello world.", "")])
        self.assertIn("（待翻译）", b)
        self.assertNotIn('class="sn"', b)      # 无译文不做句级对齐

    def test_glossary_mark_in_html(self):
        b = bilingual_html([("Attention is all you need.", "注意力就是你所需要的一切。")],
                           glossary=[("attention", "注意力", [])])
        self.assertIn('<mark class="gt"', b)

    def test_no_unreplaced_template(self):
        b = bilingual_html([("A. B.", "甲。乙。")])
        self.assertNotIn("{sel}", b)


class FiguresTest(unittest.TestCase):
    def test_page_image_rects_sorted_and_filtered(self):
        page = _StubPage(infos=[
            {"bbox": [300, 100, 450, 210], "xref": 7},
            {"bbox": [60, 700, 200, 800], "xref": 9},
            {"bbox": [0, 0, 612, 792], "xref": 11},     # 整页背景图：过滤
            {"bbox": [10, 10, 12, 12], "xref": 12},     # 碎屑：过滤
        ])
        rects = page_image_rects(page, limit=8)
        self.assertEqual(len(rects), 2)
        self.assertEqual(rects[0][1][0], 300.0)         # 面积大者在前
        self.assertNotIn(11, [x for x, _r in rects])

    def test_page_image_rects_broken_page(self):
        self.assertEqual(page_image_rects(_StubPage(raises=True)), [])
        self.assertEqual(page_image_rects(_StubPage()), [])

    def test_figure_safe_top_no_images(self):
        self.assertAlmostEqual(_figure_safe_top(_StubPage(), 842.0), 842.0 * 0.74)

    def test_figure_safe_top_bottom_figure_moves_up(self):
        # 底部条带被图片占：译文框上移到不碰任何图片的条带（216 = 上图下沿+6）
        page = _StubPage(infos=[
            {"bbox": [300, 100, 450, 210], "xref": 7},
            {"bbox": [60, 700, 200, 800], "xref": 9},
        ])
        top = _figure_safe_top(page, 842.0)
        self.assertAlmostEqual(top, 216.0)
        self.assertNotAlmostEqual(top, 842.0 * 0.74)

    def test_figure_safe_top_clean_bottom(self):
        page = _StubPage(infos=[{"bbox": [60, 100, 200, 200], "xref": 7}])
        self.assertAlmostEqual(_figure_safe_top(page, 842.0), 842.0 * 0.74)

    def test_figures_html_stub_no_images(self):
        self.assertEqual(_figures_html(None, _StubPage(), 595.0, 842.0), "")
        self.assertEqual(_figures_html(object(), _StubPage(raises=True), 595.0, 842.0), "")


class FiguresRealPdfTest(unittest.TestCase):
    """真实 PDF 夹具：整页扫描图不当插图渲染；带真实插图的页面按坐标保位。"""

    def test_scan_fixture_not_rendered_as_figure(self):
        import pymupdf
        path = os.path.join(ROOT, "pdf", "1706.03762v1_img.pdf")
        if not os.path.exists(path):
            self.skipTest("img 夹具缺失")
        d = pymupdf.open(path)
        try:
            self.assertEqual(page_image_rects(d[0]), [])     # 整页扫描图被过滤
            h, _pw, _ph = native_page_html(d, 1)
            self.assertNotIn('class="fig"', h)               # 不盖住文字层
        finally:
            d.close()

    def test_synthetic_figure_rendered_in_place(self):
        import pymupdf
        doc = pymupdf.open()
        pg = doc.new_page(width=595, height=842)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40))
        pix.set_rect(pix.irect, (200, 80, 60))
        pg.insert_text((60, 60), "A page with a real figure.", fontsize=10, fontname="helv")
        pg.insert_image(pymupdf.Rect(300, 100, 450, 220), pixmap=pix)
        try:
            rects = page_image_rects(doc[0])
            self.assertEqual(len(rects), 1)
            _xref, (x0, y0, x1, y1) = rects[0]
            h, _pw, _ph = native_page_html(doc, 1)
            self.assertEqual(h.count('class="fig"'), 1)
            self.assertIn("data:image/", h)
            self.assertIn("left:%.1fpx" % x0, h)             # 按 PyMuPDF 回报的坐标保位
            self.assertIn("top:%.1fpx" % y0, h)
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
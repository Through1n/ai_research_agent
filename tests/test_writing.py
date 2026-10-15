"""services/writing.py R17 新增能力的单元测试（零网络 / 零 LLM / 零 API Key）。

覆盖：
- claim_evidence_audit    论断-证据对齐（Deterministic Coverage Check 离线版）
- citation_correspondence_audit  引用-参考文献双向核对（Paperpal 式）
- consistency_audit       文档内自一致性（章节漂移 / 术语变体 / 缩略语冲突）
- paragraph_skeleton      大纲→段落「论点句+证据+过渡」论证单元骨架
- writing_quality_audit   三审计组合入口

运行：python -m unittest tests.test_writing -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import writing


def _ref(i, title, year="2020", venue="NeurIPS"):
    return {"id": i, "authors": "Author%d et al." % i, "title": title,
            "year": year, "venue": venue,
            "raw": "Author%d et al. %s. %s, %s." % (i, title, venue, year)}


REFS = [
    _ref(1, "Dense Passage Retrieval for Open-Domain QA", "2020", "EMNLP"),
    _ref(2, "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"),
    _ref(3, "Language Models are Few-Shot Learners"),
]

# 完整大纲（8 个固定章节齐全、顺序正确；引用 [1][2]，[3] 漏引）
OUTLINE_FULL = """# Title: A Hybrid Retrieval Agent for Long-Document QA

## Abstract
We propose a hybrid retrieval agent for long-document question answering.

## Introduction
Existing RAG systems degrade on long documents [1]. We propose a three-stage pipeline.
Prior work has shown that chunking hurts recall.

## Related Work
Dense retrievers have been widely studied [2].

## Method
Our method combines sparse and dense retrieval.

## Experiments
Results show a significant improvement over the baseline.

## Threats to Validity
We only evaluate on English corpora.

## Conclusion
We presented a hybrid retrieval agent.
"""

# 缺 Threats to Validity 的大纲（用于缺失章节测试）
OUTLINE_MISSING = OUTLINE_FULL.replace("## Threats to Validity\nWe only evaluate on English corpora.\n\n", "")


class TestClaimEvidenceAudit(unittest.TestCase):
    """论断-证据对齐：有引用的论断=supported；有论断无证据=needs_evidence。"""

    def test_cited_claim_is_supported(self):
        r = writing.claim_evidence_audit(OUTLINE_FULL, REFS)
        intro = [s for s in r["sections"] if s["section"].startswith("Introduction")][0]
        self.assertGreaterEqual(intro["n_supported"], 1)      # "Existing RAG systems … [1]"
        self.assertTrue(r["ok"])

    def test_uncited_empirical_claim_flagged(self):
        r = writing.claim_evidence_audit(OUTLINE_FULL, REFS)
        texts = [i["text"] for i in r["needs_evidence"]]
        self.assertEqual(r["n_needs_evidence"], 2)
        self.assertTrue(any("Prior work has shown" in t for t in texts))
        self.assertTrue(any("Results show a significant improvement" in t for t in texts))
        # 每条待补依据都带章节与行号，UI 可直接定位
        for it in r["needs_evidence"]:
            self.assertTrue(it["section"] and it["line_no"] > 0 and it["reason"])

    def test_contribution_statement_not_flagged(self):
        # "We propose X" 是贡献声明而非经验论断，不应判为缺证据（避免审计噪音）
        r = writing.claim_evidence_audit(OUTLINE_FULL, REFS)
        self.assertFalse(any("We propose" in i["text"] for i in r["needs_evidence"]))

    def test_references_section_excluded(self):
        full = OUTLINE_FULL + "\n# References\n\n[1] Author1 et al. Dense Passage Retrieval. EMNLP, 2020.\n"
        r = writing.claim_evidence_audit(full, REFS)
        self.assertEqual(r["n_units"], 8)                     # References 条目不算论断单元
        self.assertFalse(any("Dense Passage Retrieval" in i["text"] for i in r["needs_evidence"]))

    def test_out_of_range_citation_is_error(self):
        r = writing.claim_evidence_audit("## Introduction\nExisting systems degrade [9] badly.\n", REFS)
        self.assertFalse(r["ok"])
        self.assertEqual(r["n_bad_citation"], 1)

    def test_without_whitelist_any_citation_supports(self):
        r = writing.claim_evidence_audit("Existing systems degrade [9].\n", None)
        self.assertTrue(r["ok"])
        self.assertEqual(r["n_supported"], 1)

    def test_evidence_pointer_counts_as_support(self):
        r = writing.claim_evidence_audit("Results show a large gap, as shown in Table 2.\n", REFS)
        self.assertEqual(r["n_supported"], 1)
        self.assertEqual(r["n_needs_evidence"], 0)

    def test_empty_text_is_safe(self):
        r = writing.claim_evidence_audit("", REFS)
        self.assertTrue(r["ok"])
        self.assertEqual(r["n_units"], 0)


class TestCitationCorrespondence(unittest.TestCase):
    """引用-参考文献双向核对：漏引 / 越界 / 格式混用 / [?] 打码。"""

    def test_uncited_refs_detected(self):
        r = writing.citation_correspondence_audit(OUTLINE_FULL, REFS)
        self.assertEqual([u["id"] for u in r["uncited_refs"]], [3])   # [3] 从未被正文引用
        self.assertEqual(r["cited_ids"], [1, 2])
        self.assertTrue(r["ok"])

    def test_out_of_range_flagged(self):
        r = writing.citation_correspondence_audit("See [7] for details.\n", REFS)
        self.assertFalse(r["ok"])
        self.assertEqual(r["out_of_range"], [7])

    def test_mixed_style_flagged(self):
        r = writing.citation_correspondence_audit("Prior work [1, 2] and others [3][1].\n", REFS)
        self.assertTrue(r["mixed_style"])
        self.assertGreaterEqual(len(r["styles"]), 2)

    def test_removed_marks_counted(self):
        r = writing.citation_correspondence_audit("Some claim [?] and another [?].\n", REFS)
        self.assertEqual(r["removed_marks"], 2)

    def test_references_entries_not_counted_as_citations(self):
        full = OUTLINE_FULL + "\n# References\n\n" + writing.build_references_section(REFS)
        r = writing.citation_correspondence_audit(full, REFS)
        self.assertEqual(r["cited_ids"], [1, 2])              # 条目编号不算正文引用


class TestConsistencyAudit(unittest.TestCase):
    """自一致性：章节缺失 / 乱序 / 术语变体 / 缩略语冲突 / 编号 / 引号。"""

    def test_complete_outline_no_error(self):
        r = writing.consistency_audit(OUTLINE_FULL)
        self.assertTrue(r["ok"])
        self.assertEqual(r["missing_sections"], [])
        self.assertEqual(r["n_error"], 0)

    def test_missing_section_is_error(self):
        r = writing.consistency_audit(OUTLINE_MISSING)
        self.assertFalse(r["ok"])
        self.assertIn("Threats to Validity", r["missing_sections"])
        self.assertTrue(any(i["kind"] == "section_missing" for i in r["issues"]))

    def test_out_of_order_warned(self):
        t = ("# Title\n## Abstract\n## Introduction\n## Related Work\n"
             "## Experiments\n## Method\n## Threats to Validity\n## Conclusion\n")
        r = writing.consistency_audit(t)
        self.assertTrue(any(i["kind"] == "section_order" for i in r["issues"]))
        self.assertEqual(r["n_error"], 0)                     # 乱序只是 warning

    def test_term_variant_mix(self):
        r = writing.consistency_audit("We use fine-tuning and finetuning interchangeably.\n")
        self.assertTrue(any(i["kind"] == "term_variant" for i in r["issues"]))

    def test_single_term_variant_no_false_positive(self):
        r = writing.consistency_audit("We use fine-tuning throughout the paper.\n")
        self.assertFalse(any(i["kind"] == "term_variant" for i in r["issues"]))

    def test_acronym_conflict(self):
        t = "RAG (Retrieval-Augmented Generation) is great. RAG (Retrieval-Augmented Generator) too.\n"
        r = writing.consistency_audit(t)
        self.assertTrue(any(i["kind"] == "acronym_conflict" for i in r["issues"]))

    def test_same_acronym_same_expansion_no_false_positive(self):
        t = "RAG (Retrieval-Augmented Generation) is great. RAG helps a lot.\n"
        r = writing.consistency_audit(t)
        self.assertFalse(any(i["kind"] == "acronym_conflict" for i in r["issues"]))

    def test_clean_text_no_false_positive(self):
        t = OUTLINE_FULL + "All sections are present and consistent.\n"
        r = writing.consistency_audit(t)
        self.assertEqual(r["n_error"], 0)
        self.assertEqual(r["n_warning"], 0)

    def test_required_sections_can_be_disabled(self):
        r = writing.consistency_audit("just some prose", required_sections=[])
        self.assertTrue(r["ok"])
        self.assertEqual(r["missing_sections"], [])

    def test_empty_text_is_safe(self):
        r = writing.consistency_audit("")
        self.assertFalse(r["ok"])                             # 空文本缺全部章节 = error
        self.assertEqual(len(r["missing_sections"]), 8)


class TestParagraphSkeleton(unittest.TestCase):
    """论证单元骨架：槽位结构 / 证据映射 / 待补依据 / 中英脚手架 / Markdown 块输入。"""

    def test_structure_and_evidence_mapping(self):
        r = writing.paragraph_skeleton("Introduction",
                                       ["Existing RAG systems degrade [1].",
                                        "We propose a three-stage pipeline."], REFS)
        self.assertEqual(r["n_units"], 2)
        self.assertEqual(r["n_with_evidence"], 1)
        self.assertEqual(r["n_pending"], 1)
        self.assertIn("Dense Passage Retrieval", r["markdown"])   # 白名单文献进证据槽
        self.assertIn("Pending evidence", r["markdown"])          # 无引用 → 待补依据
        self.assertIn("Topic sentence", r["markdown"])
        self.assertIn("Transition", r["markdown"])

    def test_accepts_markdown_block(self):
        r = writing.paragraph_skeleton("Method", "- First bullet\n- Second bullet\n* Third\n", REFS)
        self.assertEqual(r["n_units"], 3)

    def test_out_of_range_citation_marked(self):
        r = writing.paragraph_skeleton("Introduction", ["Some claim [9]."], REFS)
        self.assertIn("超出白名单范围", r["markdown"])
        self.assertEqual(r["n_with_evidence"], 0)

    def test_english_scaffold(self):
        r = writing.paragraph_skeleton("Introduction", ["Existing systems degrade [1]."], REFS)
        self.assertIn("Topic sentence", r["markdown"])
        self.assertIn("Evidence", r["markdown"])

    def test_chinese_scaffold(self):
        r = writing.paragraph_skeleton("引言", ["现有系统在长文档上召回率下降 [1]。"], REFS)
        self.assertIn("论点句", r["markdown"])

    def test_empty_input_is_safe(self):
        r = writing.paragraph_skeleton("Method", [], REFS)
        self.assertEqual(r["n_units"], 0)
        self.assertTrue(r["markdown"])


class TestWritingQualityAudit(unittest.TestCase):
    """组合入口：三个子审计齐备 + 汇总语义 + 最终全文形态。"""

    def test_combined_report_on_final_text(self):
        full = OUTLINE_FULL + "\n# References\n\n" + writing.build_references_section(REFS)
        r = writing.writing_quality_audit(full, REFS)
        self.assertTrue(r["ok"])
        self.assertEqual(r["n_error"], 0)
        self.assertGreaterEqual(r["n_warning"], 1)            # 漏引 [3] + 2 条待补依据
        self.assertIsNotNone(r["claim_evidence"])
        self.assertIsNotNone(r["citation_correspondence"])
        self.assertIsNotNone(r["consistency"])
        self.assertIn("写作质量审计", r["report"])
        # References 段不影响论断单元计数
        self.assertEqual(r["claim_evidence"]["n_units"], 8)

    def test_missing_section_makes_combined_fail(self):
        r = writing.writing_quality_audit(OUTLINE_MISSING, REFS)
        self.assertFalse(r["ok"])
        self.assertEqual(r["n_error"], 1)

    def test_no_whitelist_skips_correspondence(self):
        r = writing.writing_quality_audit(OUTLINE_FULL, None)
        self.assertIsNone(r["citation_correspondence"])
        self.assertTrue(r["ok"])

    def test_empty_text_is_safe(self):
        r = writing.writing_quality_audit("", REFS)
        self.assertFalse(r["ok"])
        self.assertTrue(r["report"])


class TestFullWidthCitations(unittest.TestCase):
    """全角【n】角标兼容（系统规范角标为全角，core/agent.py:677 两种都认的先例）。

    独立验证曾抓到：只用半角正则在真实 Agent 文本上整套审计假阳性。
    """

    def test_fullwidth_cited_claim_supported(self):
        r = writing.claim_evidence_audit("该方法取得 28.4 BLEU【1】，显著优于基线【2】。\n", REFS)
        self.assertEqual(r["n_supported"], 1)
        self.assertEqual(r["n_needs_evidence"], 0)
        self.assertTrue(r["ok"])

    def test_fullwidth_out_of_range_is_error(self):
        r = writing.claim_evidence_audit("结论见【9】，越界角标。\n", REFS)
        self.assertFalse(r["ok"])
        self.assertEqual(r["n_bad_citation"], 1)            # 原来误判 background、抓不到

    def test_fullwidth_correspondence_counts(self):
        t = "该方法取得 28.4 BLEU【1】，显著优于基线【2】。\n参考文献\n[1] Vaswani 2017.\n[2] Devlin 2019."
        r = writing.citation_correspondence_audit(t, ["Vaswani 2017.", "Devlin 2019."])
        self.assertEqual(r["cited_ids"], [1, 2])
        self.assertEqual(r["n_cited"], 2)
        self.assertEqual(r["uncited_refs"], [])              # 不再假阳性「漏引 2 条」
        self.assertTrue(r["ok"])
        self.assertIn("正文实际引用 2 条", r["report"])

    def test_fullwidth_out_of_range_correspondence(self):
        r = writing.citation_correspondence_audit("结论见【9】。\n", REFS)
        self.assertFalse(r["ok"])
        self.assertEqual(r["out_of_range"], [9])

    def test_mixed_width_both_recognized(self):
        t = "半角引用 [1] 与全角引用【2】并存，混排【3】。\n"
        r = writing.citation_correspondence_audit(t, REFS)
        self.assertEqual(r["cited_ids"], [1, 2, 3])
        r2 = writing.claim_evidence_audit(t, REFS)
        self.assertEqual(r2["n_supported"], 1)
        self.assertTrue(r2["ok"])

    def test_fullwidth_group_and_adjacent_styles(self):
        r = writing.citation_correspondence_audit("前人工作【1, 2】与 [3][1]。\n", REFS)
        self.assertTrue(r["mixed_style"])
        self.assertTrue(any("成组" in s for s in r["styles"]))
        self.assertTrue(any("并排" in s for s in r["styles"]))

    def test_fullwidth_skeleton_evidence(self):
        r = writing.paragraph_skeleton("引言", ["现有系统召回率下降【1】。"], REFS)
        self.assertEqual(r["n_with_evidence"], 1)
        self.assertEqual(r["n_pending"], 0)
        self.assertIn("Dense Passage Retrieval", r["markdown"])

    def test_fullwidth_out_of_range_skeleton(self):
        r = writing.paragraph_skeleton("引言", ["某结论见【9】。"], REFS)
        self.assertEqual(r["n_with_evidence"], 0)
        self.assertIn("超出白名单范围", r["markdown"])

    def test_legacy_halfwidth_behavior_unchanged(self):
        # _sanitize_citations / validate_reference_whitelist 仍走半角 _CITE_MARK_RE
        s, k = writing._sanitize_citations("见 [5] 与 [1]。", {1})
        self.assertEqual(s, "见 [?] 与 [1]。")
        self.assertEqual(k, 1)


class TestBareLineHeadings(unittest.TestCase):
    """consistency_audit 裸行/编号行章节名识别（严格相等，正文句子不误判）。"""

    def test_bare_line_sections_recognized(self):
        t = ("Title\nAbstract\nIntroduction\nRelated Work\nMethod\n"
             "Experiments\nThreats to Validity\nConclusion\n")
        r = writing.consistency_audit(t)
        self.assertTrue(r["ok"])
        self.assertEqual(r["missing_sections"], [])

    def test_numbered_bare_sections_recognized(self):
        t = "1. Introduction\n2. Method\n3. Experiments\n"
        r = writing.consistency_audit(t)
        for name in ("Introduction", "Method", "Experiments"):
            self.assertNotIn(name, r["missing_sections"])

    def test_sentence_line_not_treated_as_heading(self):
        t = "Introduction is the first section of the paper.\nMethod is described below.\n"
        r = writing.consistency_audit(t)
        self.assertIn("Introduction", r["missing_sections"])
        self.assertIn("Method", r["missing_sections"])

    def test_mixed_heading_formats(self):
        t = ("# Title\n## Abstract\n**Introduction**\n1. Related Work\n"
             "Method\n## Experiments\n## Threats to Validity\n## Conclusion\n")
        r = writing.consistency_audit(t)
        self.assertTrue(r["ok"])
        self.assertEqual(r["missing_sections"], [])


if __name__ == "__main__":
    unittest.main()

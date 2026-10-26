# -*- coding: utf-8 -*-
"""「参考文献结构化」根因修复的回归测试（2026-10-26）。

事故：交付截图 ``资料/截图/p0.png`` 顶部带着红色错误横幅
「参考文献结构化失败 · Request timed out」。根因不是网络抖动，而是产品缺陷：

  旧实现 ``parsing.extract_references_raw(doc)[:8000]`` 取的是「全文尾部 22000 字符」
  的**前 8000 字符** —— 对一篇 15 页论文来说落在**正文里**（实测取到的是
  "[38] and byte-pair [31] representations" 这种正文句子），根本不是文献区。
  模型拿着正文去「提取 12 条参考文献」只能编造，且 5000 字符输入 + 12×5 字段输出
  把生成拖到 47s > ``LLM_TIMEOUT``=45s，于是**必然**超时并弹出页面级红条。

修法（三层）：
  ① 输入取真的 References 区（``services.writing.extract_references_with_quality``，
     定位不到就返回空而不是拿正文凑数）；
  ② 大模型只精修前 8 条真实条目（12 条实测 46.5s 会撞 45s 超时）；
  ③ 大模型超时 / 降级 / 返回非 JSON / 条数对不上 → 回落确定性基线，
     **功能照常交付**，只降级不报错。

下面四组用例分别钉住这四层；每组都做了**反向验证**（构造坏输入 → 用例翻红）。
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui import page_library


class _FakeLLM:
    """最小 llm 替身：记录收到的 prompt，可指定返回内容或抛错。"""

    def __init__(self, reply=None, raises=None):
        self.reply = reply
        self.raises = raises
        self.prompts = []

    def chat(self, messages, model=None, temperature=0.2, tools=None,
             tool_choice="auto", json_mode=False):
        self.prompts.append(messages[0]["content"])
        if self.raises is not None:
            raise self.raises
        return types.SimpleNamespace(content=self.reply)

    def json_chat(self, messages, model=None, temperature=0.1):
        raise AssertionError("新实现不应再走 json_chat（json_mode 对象输出更快更稳）")


def _ctx(llm):
    return types.SimpleNamespace(llm=llm)


def _entries(n=3):
    return [{"id": str(i), "authors": "A%d" % i, "title": "T%d" % i,
             "year": "2020", "venue": "V%d" % i, "raw": "raw-%d" % i}
            for i in range(1, n + 1)]


class RefWorkerFallbackTest(unittest.TestCase):
    """第 ③ 层：大模型不可用时必须**仍然交付**结果，而不是抛错。"""

    def _run(self, llm):
        captured = {}

        def fake_quality(path, max_entries=None):
            captured["max_entries"] = max_entries
            return {"entries": _entries(3),
                    "quality": {"count": 3, "heading_found": True,
                                "low_confidence": False, "reasons": []}}

        orig = page_library.writing.extract_references_with_quality
        page_library.writing.extract_references_with_quality = fake_quality
        try:
            return page_library._ref_worker(_ctx(llm), "x.pdf", lambda *a: None)
        finally:
            page_library.writing.extract_references_with_quality = orig

    def test_llm_raises_still_delivers(self):
        llm = _FakeLLM(raises=RuntimeError("Request timed out."))
        res = self._run(llm)
        self.assertEqual(res["source"], "local")
        self.assertEqual(len(res["items"]), 3)
        self.assertTrue(res["note"])
        # 降级必须说人话，不能把原始异常直接甩给用户
        self.assertNotIn("Traceback", res["note"])

    def test_llm_bad_json_falls_back(self):
        llm = _FakeLLM(reply="这不是 JSON")
        res = self._run(llm)
        self.assertEqual(res["source"], "local")
        self.assertEqual(len(res["items"]), 3)

    def test_llm_count_mismatch_falls_back(self):
        llm = _FakeLLM(reply='{"refs": [{"id": 1, "title": "x"}]}')   # 只回 1 条，送了 3 条
        res = self._run(llm)
        self.assertEqual(res["source"], "local")
        self.assertEqual(len(res["items"]), 3)
        self.assertIn("条数", res["note"])

    def test_llm_ok_merges_by_position(self):
        llm = _FakeLLM(reply='{"refs": [{"id":1,"authors":"AA","title":"TT","year":"1999","venue":"VV"},'
                                '{"id":2},{"id":3}]}')
        res = self._run(llm)
        self.assertEqual(res["source"], "llm")
        self.assertEqual(len(res["items"]), 3)          # 不能因 zip 截断而丢条目
        self.assertEqual(res["items"][0]["title"], "TT")
        self.assertEqual(res["items"][2]["title"], "T3")  # 未精修的保留基线

    def test_llm_unrecognized_field_does_not_overwrite(self):
        llm = _FakeLLM(reply='{"refs": [{"id":1,"title":"未识别"},{"id":2},{"id":3}]}')
        res = self._run(llm)
        self.assertEqual(res["source"], "llm")
        self.assertEqual(res["items"][0]["title"], "T1")  # "未识别" 不覆盖基线


class RefWorkerInputTest(unittest.TestCase):
    """第 ① ② 层：喂给大模型必须是**真实文献条目**，条数必须封顶。"""

    def test_prompt_contains_real_entries_only(self):
        llm = _FakeLLM(reply='{"refs": []}')
        captured = {}

        def fake_quality(path, max_entries=None):
            captured["max_entries"] = max_entries
            return {"entries": _entries(3),
                    "quality": {"count": 3, "heading_found": True,
                                "low_confidence": False, "reasons": []}}

        orig = page_library.writing.extract_references_with_quality
        page_library.writing.extract_references_with_quality = fake_quality
        try:
            page_library._ref_worker(_ctx(llm), "x.pdf", lambda *a: None)
        finally:
            page_library.writing.extract_references_with_quality = orig
        prompt = llm.prompts[0]
        self.assertIn("raw-1", prompt)
        self.assertIn("raw-3", prompt)
        # 条数上限必须传给抽取层（12 条 → 大模型只精修 8 条）
        self.assertEqual(captured["max_entries"], page_library._REF_MAX_ENTRIES)
        self.assertLessEqual(page_library._REF_LLM_MAX, page_library._REF_MAX_ENTRIES)

    def test_llm_never_receives_more_than_llm_max(self):
        many = _entries(page_library._REF_MAX_ENTRIES + 5)
        llm = _FakeLLM(reply='{"refs": []}')
        captured = {}

        def fake_quality(path, max_entries=None):
            return {"entries": many, "quality": {"count": len(many), "heading_found": True,
                                                 "low_confidence": False, "reasons": []}}

        orig = page_library.writing.extract_references_with_quality
        page_library.writing.extract_references_with_quality = fake_quality
        try:
            page_library._ref_worker(_ctx(llm), "x.pdf", lambda *a: None)
        finally:
            page_library.writing.extract_references_with_quality = orig
        prompt = llm.prompts[0]
        self.assertIn("raw-%d" % page_library._REF_LLM_MAX, prompt)
        self.assertNotIn("raw-%d" % (page_library._REF_LLM_MAX + 1), prompt)

    def test_no_reference_section_raises_human_error(self):
        def fake_quality(path, max_entries=None):
            return {"entries": [], "quality": {"count": 0, "heading_found": False,
                                               "low_confidence": True,
                                               "reasons": ["PDF 中未定位到 References/Bibliography 标题"]}}

        orig = page_library.writing.extract_references_with_quality
        page_library.writing.extract_references_with_quality = fake_quality
        try:
            with self.assertRaises(ValueError) as cm:
                page_library._ref_worker(_ctx(_FakeLLM()), "x.pdf", lambda *a: None)
        finally:
            page_library.writing.extract_references_with_quality = orig
        self.assertIn("References", str(cm.exception))


class ConsumeRefTest(unittest.TestCase):
    """交付层：降级交付不能变成页面级红色报错条。"""

    def _consume(self, task):
        flashes = []
        orig = page_library.set_flash
        page_library.set_flash = lambda level, msg: flashes.append((level, msg))
        try:
            page_library._consume_ref(None, page_library._LIB_REF_KIND, task)
        finally:
            page_library.set_flash = orig
        return flashes

    def test_local_source_is_success_not_error(self):
        flashes = self._consume({"state": "done", "name": "参考文献结构化：a.pdf",
                                 "result": {"items": _entries(2), "source": "local",
                                            "note": "大模型暂时不可用"}})
        self.assertEqual(len(flashes), 1)
        level, msg = flashes[0]
        self.assertEqual(level, "success")
        self.assertIn("2 条", msg)

    def test_llm_source_is_success(self):
        flashes = self._consume({"state": "done", "name": "参考文献结构化：a.pdf",
                                 "result": {"items": _entries(2), "source": "llm", "note": ""}})
        self.assertEqual(flashes[0][0], "success")

    def test_hard_error_still_reported(self):
        flashes = self._consume({"state": "error", "name": "参考文献结构化：a.pdf",
                                 "error": "PDF 损坏"})
        self.assertEqual(flashes[0][0], "error")


class ShotCleanGateTest(unittest.TestCase):
    """交付图门禁：截图里出现运行时错误横幅必须被检出（反向验证用）。"""

    def setUp(self):
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        from _verify_shots_clean import scan_image, MAX_BG_RATIO, MAX_TEXT_PX
        self.scan = scan_image
        self.max_bg = MAX_BG_RATIO
        self.max_tx = MAX_TEXT_PX

    def _write(self, arr):
        import numpy as np
        from PIL import Image
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        Image.fromarray(arr).save(path)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_detects_error_banner(self):
        import numpy as np
        arr = np.full((400, 600, 3), 255, dtype=np.uint8)
        arr[0:200, 0:600] = (250, 228, 228)                 # 错误条底色
        arr[50:150, 20:580] = (189, 64, 67)                # 错误条文字
        res = self.scan(self._write(arr))
        self.assertFalse(res["ok"])
        self.assertTrue(res["reasons"])

    def test_clean_image_passes(self):
        import numpy as np
        arr = np.full((400, 600, 3), 255, dtype=np.uint8)
        arr[0:200, 0:600] = (245, 245, 246)
        arr[50:150, 20:580] = (31, 95, 174)                 # 强调色（非错误红）
        res = self.scan(self._write(arr))
        self.assertTrue(res["ok"], res["reasons"])

    def test_threshold_not_too_loose(self):
        # 阈值必须严到能抓住真事故：事故图底色 1.12%、文字 11992 px
        self.assertLess(self.max_bg, 0.001)
        self.assertLess(self.max_tx, 1000)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""`core.llm.parse_json` 稳健性单测（2026-10-24 加固的回归网）。

全部用例都来自真机或真实模型输出形态，不是编造的理想输入：

* `test_fenced_without_closing` —— 输出被 max_tokens 截断时只有开头 ```json
  没有结尾 ```，旧实现的正则要求成对围栏 → 判定「无法解析为 JSON」。
* `test_latex_single_backslash` —— 模型把 LaTeX 写成 `\\sqrt{x}`（JSON 里是非法
  转义 `\\s`），旧实现直接抛错；这正是「LaTeX 公式抽取」任务失败的形态之一。
* `test_truncated_array` —— 数组没闭合，靠括号栈补齐后仍能取出已生成的部分。
* `test_degraded_text_still_raises` —— 降级占位文案必须继续抛 ValueError：
  调用方的 except 分支依赖它，绝不能把占位内容当数据返回。
"""

import unittest

from core.llm import parse_json


class TestParseJson(unittest.TestCase):
    def test_plain_array(self):
        self.assertEqual(parse_json('[{"name":"a","latex":"x"}]'),
                         [{"name": "a", "latex": "x"}])

    def test_plain_object(self):
        self.assertEqual(parse_json('{"n": 2}'), {"n": 2})

    def test_pass_through_containers(self):
        obj = [{"a": 1}]
        self.assertIs(parse_json(obj), obj)

    def test_fenced_with_closing(self):
        txt = '```json\n[{"name":"BLEU","latex":"BLEU"}]\n```'
        self.assertEqual(parse_json(txt), [{"name": "BLEU", "latex": "BLEU"}])

    def test_fenced_without_closing(self):
        """截断输出：只有开头围栏，没有结尾围栏。"""
        txt = '```json\n[{"name":"BLEU","latex":"BLEU"},\n {"name":"WMT"'
        got = parse_json(txt)
        self.assertEqual(got[0]["name"], "BLEU")
        self.assertEqual(got[1]["name"], "WMT")

    def test_prose_around_array(self):
        txt = '以下是抽取结果：\n[{"name":"a"}]\n以上共 1 条，仅供参考。'
        self.assertEqual(parse_json(txt), [{"name": "a"}])

    def test_latex_single_backslash(self):
        """模型输出单反斜杠 LaTeX（`\\s` 是非法 JSON 转义）→ 补转义后解析。"""
        txt = r'[{"name":"根号","latex":"\sqrt{x}"}]'
        got = parse_json(txt)
        self.assertEqual(got[0]["latex"], r"\sqrt{x}")

    def test_latex_frac_is_left_to_caller(self):
        r"""`\f` 是合法 JSON 转义 → 解析器不越权改写，交给调用方（page_library）还原。

        这里固化边界：parse_json 只负责「解析得出来」，不猜业务语义；
        公式字段的控制字符还原在 `ui.page_library._fix_latex_controls`，
        由 `tests/test_latex_controls.py` 覆盖。
        """
        got = parse_json(r'[{"latex":"\frac{a}{b}"}]')
        self.assertEqual(got[0]["latex"], "\x0crac{a}{b}")

    def test_truncated_array(self):
        txt = '[{"name":"a","latex":"x"},{"name":"b","latex":"y"'
        got = parse_json(txt)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[1]["name"], "b")

    def test_truncated_keeps_valid_strings(self):
        txt = '[{"note":"带\\\\反斜杠的合法字符串"},{"name":"c"'
        got = parse_json(txt)
        self.assertEqual(got[0]["note"], "带\\反斜杠的合法字符串")

    def test_degraded_text_still_raises(self):
        """降级占位文案不是 JSON，必须抛 ValueError（调用方据此走进降级分支）。"""
        txt = "【LLM 暂时不可用，请稍后重试。⚠️ 离线占位响应：LLM 不可用】"
        with self.assertRaises(ValueError) as cm:
            parse_json(txt)
        self.assertIn("无法解析为 JSON", str(cm.exception))

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            parse_json("")


if __name__ == "__main__":
    unittest.main()

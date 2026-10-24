# -*- coding: utf-8 -*-
"""LaTeX 控制字符还原（`ui.page_library._fix_latex_controls`）单测。

为什么单独测：`parse_json` 不猜业务语义，`\f`/`\b`/`\r`/`\n`/`\t` 在 JSON 里都是
**合法转义**，模型把 LaTeX 写成单反斜杠时，json.loads 会静默把它们变成控制字符
（`\frac{a}{b}` → 换页符 + "rac{a}{b}"）——解析不报错，但公式渲染成乱码。
真机依据：2026-10-24 补拍 p0 时公式抽取任务反复失败，追到的形态之一就是这样。
"""

import unittest

from ui.page_library import _fix_latex_controls


class TestFixLatexControls(unittest.TestCase):
    def test_frac_formfeed(self):
        items = [{"name": "注意力", "latex": "\x0crac{a}{b}"}]
        got = _fix_latex_controls(items)
        self.assertEqual(got[0]["latex"], r"\frac{a}{b}")

    def test_beta_backspace(self):
        items = [{"name": "beta", "latex": "\x08eta_{1} = 0.9"}]
        got = _fix_latex_controls(items)
        self.assertEqual(got[0]["latex"], r"\beta_{1} = 0.9")

    def test_theta_and_nabla(self):
        items = [{"latex": "\theta"}, {"latex": "\nabla"}]
        got = _fix_latex_controls(items)
        self.assertEqual(got[0]["latex"], r"\theta")
        self.assertEqual(got[1]["latex"], r"\nabla")

    def test_untouched_when_clean(self):
        items = [{"latex": r"\sqrt{d_k}"}]
        got = _fix_latex_controls(items)
        self.assertEqual(got[0]["latex"], r"\sqrt{d_k}")

    def test_dict_input(self):
        got = _fix_latex_controls({"latex": "\x0crac{1}{2}"})
        self.assertEqual(got[0]["latex"], r"\frac{1}{2}")

    def test_non_list_passthrough(self):
        self.assertIsNone(_fix_latex_controls(None))
        self.assertEqual(_fix_latex_controls("x"), "x")

    def test_other_fields_untouched(self):
        items = [{"name": "a\nb", "latex": "x"}]
        got = _fix_latex_controls(items)
        self.assertEqual(got[0]["name"], "a\nb")


if __name__ == "__main__":
    unittest.main()

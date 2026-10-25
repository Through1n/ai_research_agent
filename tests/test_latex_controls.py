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


class TestLatexStructureRepair(unittest.TestCase):
    """hat / 括号两类结构性退化（2026-10-25 换真公式语料后实测出来的形态）。

    真机依据：语料里 m̂ / v̂ / x̂ 是 Unicode 组合字符，glm-4-flash 转 LaTeX 时
    帽子挂空（`m^{\\hat{}}`）或套在角标上（`x^\\hat{(k)}`），渲染出来是一个悬空
    小帽或错位的帽子。
    """

    def _one(self, latex):
        return _fix_latex_controls([{"latex": latex}])[0]["latex"]

    def test_hat_empty_group(self):
        # 帽子挂在空组上 → 扶到字母上
        self.assertEqual(self._one(r"m^{\hat{}}_t = \frac{m_t}{1 - \beta_1^t}"),
                         r"\hat{m}_t = \frac{m_t}{1 - \beta_1^t}")

    def test_hat_missing_braces(self):
        # 同一形态的不同写法（模型时而省略花括号）
        self.assertEqual(self._one(r"v^\hat{}_t"), r"\hat{v}_t")

    def test_hat_over_subscript(self):
        # 帽子套在角标上 → 帽子归字母，角标归上标
        self.assertEqual(self._one(r"x^\hat{(k)} = \gamma(k)"), r"\hat{x}^{(k)} = \gamma(k)")

    def test_hat_clean_latex_untouched(self):
        # 已经写对的 \hat{m} 不能被二次改写
        self.assertEqual(self._one(r"\hat{m}_t"), r"\hat{m}_t")

    def test_extra_closing_paren_removed(self):
        # \sqrt{v̂_t + ϵ)} —— 一个左括号都没写，右括号却多一个
        self.assertEqual(self._one(r"\sqrt{v_t + \epsilon)}"), r"\sqrt{v_t + \epsilon}")

    def test_balanced_parens_untouched(self):
        # 成对括号（含 KL 那种嵌套很深的）一律不动
        s = r"DKL(q_{\varphi}(z|x(i)) || p_{\theta}(z|x(i)))"
        self.assertEqual(self._one(s), s)

    def test_missing_closing_brace_appended(self):
        # \frac{a}{\sqrt{b} 少一个收尾花括号 → 末尾补齐
        self.assertEqual(self._one(r"\frac{a}{\sqrt{b}"), r"\frac{a}{\sqrt{b}}")

    def test_balanced_braces_untouched(self):
        s = r"\frac{\hat{m}_t}{\sqrt{\hat{v}_t + \epsilon}}"
        self.assertEqual(self._one(s), s)

    def test_backslash_minus_normalized(self):
        # 模型把负号写成 "\-"（LaTeX 里的连字符），会渲染成短横而不是减号
        self.assertEqual(self._one(r"L = \-log q"), r"L = -log q")


if __name__ == "__main__":
    unittest.main()

"""UI 公共组件单测（Round 17 P7 UI/UX 优化）：教学式空状态与 KPI 指标条。

只覆盖**不依赖 Streamlit 运行时**的纯函数部分（HTML 构建 / 归一化 / 跳转载荷解析）：
转义防注入、结构完整性、非法输入容错。
交互点击行为（按钮真的点下去会发生什么）由 scripts/_apptest_ui_interact.py
用 AppTest 模拟点击验证——渲染冒烟通过 != 交互正确，两者互补。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.common import (_empty_state_card_html, _metric_card_html, _normalize_metrics)
from ui.page_qa import _jump_payload


class TestEmptyStateCard(unittest.TestCase):
    """教学式空状态卡片：结构完整 + 输入全部转义（防 XSS / 结构破坏）。"""

    def test_contains_title_desc_actions_footer(self):
        html = _empty_state_card_html(
            "还没有执行记录", desc="答案会出现在这里",
            actions=["点上方示例任务", "或在输入框描述任务"],
            icon="🧭", footer="口径说明：小语料仅供参考")
        self.assertIn("还没有执行记录", html)
        self.assertIn("答案会出现在这里", html)
        self.assertIn("点上方示例任务", html)
        self.assertIn("或在输入框描述任务", html)
        self.assertIn("口径说明：小语料仅供参考", html)
        self.assertIn("🧭", html)
        # 序号动作从 1 开始
        self.assertIn(">1.</span>", html)
        self.assertIn(">2.</span>", html)

    def test_escapes_html_injection(self):
        html = _empty_state_card_html(
            "<script>alert(1)</script>",
            desc="<img src=x>",
            actions=['<a onclick="hack()">点我</a>'])
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        # 真实危险模式（未转义的标签/属性引号）不得出现；
        # 转义后的纯文本子串（onclick=&quot;）无害，不作为断言对象
        self.assertNotIn('onclick="', html)
        self.assertIn("&lt;script&gt;", html)

    def test_empty_actions_and_optional_fields(self):
        html = _empty_state_card_html("空", desc="", actions=(), footer="")
        self.assertIn("空", html)
        # 没有序号行、没有描述块、没有页脚分隔线
        self.assertNotIn("</span> 1.", html)
        self.assertNotIn("border-top:1px dashed", html)

    def test_blank_actions_filtered(self):
        html = _empty_state_card_html("标题", actions=["  ", "", "有效动作"])
        self.assertIn("有效动作", html)
        self.assertEqual(html.count("</span> 1."), 0)  # 空串被过滤，序号从 1 且仅一条


class TestMetricStrip(unittest.TestCase):
    """KPI 指标条：归一化容错 + 卡片结构 + 转义。"""

    def test_normalize_metrics_ok(self):
        norm = _normalize_metrics([("逻辑节点", 2), ("引用边", 1, "真实引用关系")])
        self.assertEqual(norm, [("逻辑节点", "2", ""), ("引用边", "1", "真实引用关系")])

    def test_normalize_metrics_skips_bad_items(self):
        norm = _normalize_metrics([("只有一项",), 42, None, ("合法", 5, "小字")])
        self.assertEqual(norm, [("合法", "5", "小字")])

    def test_normalize_metrics_none(self):
        self.assertEqual(_normalize_metrics(None), [])
        self.assertEqual(_normalize_metrics([]), [])

    def test_metric_card_html_structure(self):
        html = _metric_card_html("逻辑节点", 3, "同一 arXiv 多版本已合并")
        self.assertIn("逻辑节点", html)
        self.assertIn(">3<", html)
        self.assertIn("同一 arXiv 多版本已合并", html)
        # 数值用等宽数字（表格列对齐），大号加粗
        self.assertIn("font-variant-numeric:tabular-nums", html)

    def test_metric_card_escapes(self):
        html = _metric_card_html("<b>label</b>", "<i>1</i>")
        self.assertNotIn("<b>label</b>", html)
        self.assertIn("&lt;b&gt;", html)


class TestJumpPayload(unittest.TestCase):
    """问答页跨页跳转载荷归一化（page_qa._jump_payload）。"""

    def test_valid_payload(self):
        info = _jump_payload({"source": "1706.03762v7.pdf", "page": 3, "para_idx": 2})
        self.assertEqual(info, {"source": "1706.03762v7.pdf", "page": 3, "para": 2})

    def test_invalid_payloads_return_none(self):
        self.assertIsNone(_jump_payload(None))
        self.assertIsNone(_jump_payload({}))
        self.assertIsNone(_jump_payload({"page": 3}))          # 缺 source
        self.assertIsNone(_jump_payload("not-a-dict"))

    def test_bad_numbers_fall_back(self):
        info = _jump_payload({"source": "a.pdf", "page": "abc", "para_idx": "x"})
        self.assertEqual(info["page"], 1)
        self.assertIsNone(info["para"])
        info2 = _jump_payload({"source": "a.pdf", "page": None, "para_idx": 0})
        self.assertEqual(info2["page"], 1)
        self.assertIsNone(info2["para"])


if __name__ == "__main__":
    unittest.main()

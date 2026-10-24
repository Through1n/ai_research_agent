# -*- coding: utf-8 -*-
"""回归用例：令牌 / 构件 CSS 必须在**每一次重跑**都重新注入。

事故背景（2026-10-24，用户可见症状「重启后没啥变化」）：
注入曾用**会话级幂等**旗标（`session_state[flag]=True` 后永久跳过）。而 Streamlit
每次 rerun 都会**重建整个元素树**，上一轮 `st.markdown` 注入的 `<style>` 不会保留。
于是第二轮起（用户第一次点侧栏/按钮）令牌 CSS 整体消失，界面静默退回 Streamlit
默认外观——合成 DOM 类门禁（直接灌 CSS）永远测不到这个语义，属典型「门禁看不见」。

修法：app.py 每轮维护 `_run_seq`（+1），`design.apply()` 与
`common._inject_css_once` 以「本轮是否已注入」为幂等粒度，跨轮必重新注入。

本用例用 AppTest 跑**真实 app.py**，连续 run 两次（第二次即模拟交互后的重跑），
断言第二轮仍能在元素树里查到令牌与构件类。反向验证方法：把任一注入点改回
会话级旗标，本用例必须翻红。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")


def _markdown_text(at) -> str:
    return "\n".join(str(getattr(m, "value", "")) for m in at.markdown)


class TestCssPerRunInjection(unittest.TestCase):
    """两轮运行都必须带令牌 CSS。"""

    @classmethod
    def setUpClass(cls):
        cls.at = AppTest.from_file(APP, default_timeout=180)
        cls.at.run()

    def test_run_seq_increments(self):
        """_run_seq 每次运行加 1（注入幂等的锚点本身要可靠）。"""
        seq1 = self.at.session_state["_run_seq"]
        self.assertIsInstance(seq1, int)
        self.assertGreaterEqual(seq1, 1)
        self.at.run()
        seq2 = self.at.session_state["_run_seq"]
        self.assertEqual(seq2, seq1 + 1, "_run_seq 未随重跑递增，注入幂等将失效")

    def test_tokens_present_in_both_runs(self):
        """令牌 CSS（--c-on-accent）与构件类（.ai-page-head）在两轮里都在场。"""
        first = _markdown_text(self.at)
        self.assertIn("--c-on-accent", first, "首轮未注入令牌 CSS")
        self.assertIn(".ai-page-head", first, "首轮未注入构件 CSS")

        self.at.run()                              # 模拟交互后的重跑
        second = _markdown_text(self.at)
        self.assertIn("--c-on-accent", second,
                      "重跑后令牌 CSS 丢失——注入又退回会话级幂等（历史事故回归）")
        self.assertIn(".ai-section-title", second,
                      "重跑后构件 CSS 丢失——_inject_css_once 未按本轮序号重新注入")
        self.assertIn("--fs-display", second, "重跑后字阶令牌丢失")


class TestHeaderIconNotDuplicated(unittest.TestCase):
    """页头不得渲染出「🤖 🤖 标题」这种图标重复。

    事故（2026-10-24 视觉快照实测）：6 个页面同时传「标题里带 emoji」和 `icon=同一 emoji`，
    而 page_header 旧实现无条件拼接 → 首屏标题变成「🤖 🤖 智能总助手（Agent）」。
    断言落在**渲染产物**上（不是断言辅助函数存在），改回去就会翻红。
    """

    def test_no_duplicated_leading_token(self):
        at = AppTest.from_file(APP, default_timeout=180)
        at.run()
        heads = [str(m.value) for m in at.markdown if 'ai-page-title">' in str(m.value)]
        self.assertTrue(heads, "页头未渲染，用例失效")
        for h in heads:
            m = re.search(r'ai-page-title">([^<]*)<', h)
            self.assertIsNotNone(m, f"页头结构变了，用例需同步：{h[:80]}")
            txt = m.group(1)
            self.assertIsNone(
                re.match(r"^(\S+)\s+\1(\s|$)", txt),
                f"页头图标重复渲染：{txt!r}",
            )


if __name__ == "__main__":
    unittest.main()

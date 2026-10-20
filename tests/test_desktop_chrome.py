"""桌面外壳 CSS 单测（Round 13 侧边栏展开入口事故修复）。

背景：ui/desktop_chrome.py 为桌面模式注入的 CSS 曾整段隐藏 stHeader/stToolbar，
而侧边栏收起后唯一的图形展开入口（stExpandSidebarButton，Streamlit 1.63；
旧版 testid stSidebarCollapsedControl）正渲染在 stHeader → stToolbar 内，
被连带埋掉 → 收起即永久失去入口（用户实测「侧边栏收起之后还是没找到展开的选项」）。

本测试守三条不变量：
1. 展开按钮必须被显式放行（visibility: visible !important，双 testid 兼容）；
2. header/toolbar 的隐藏规则必须仍在——修复不是「干脆不隐藏了」；
3. 隐藏必须用 visibility 而非 display：display 不继承、后代无法覆盖，
   谁把上面两条改成 display:none，这个修复会静默死亡（回归测试钉死这一点）。
另覆盖 apply() 的桌面 / 开发双模式门控。
真实浏览器里的计算样式与可点击性由 scripts/_verify_sidebar_expand.py 负责。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui.desktop_chrome as dc


def _css_rules_only(css: str = None) -> str:
    """剥离 /* */ 注释后的 CSS——断言只作用于真实规则，注释里的举例文字不算数。"""
    if css is None:
        css = dc._CSS
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


class TestSidebarExpandReveal(unittest.TestCase):
    """放行规则的存在性与形式。"""

    def test_reveals_expand_button_with_important(self):
        # 双 testid：stExpandSidebarButton 为 1.63 现行，stSidebarCollapsedControl 为旧版残留
        self.assertIn('[data-testid="stExpandSidebarButton"]', dc._CSS)
        self.assertIn('[data-testid="stSidebarCollapsedControl"]', dc._CSS)
        # 必须带 !important： Streamlit 自身 emotion 样式优先级可能高于我们的单条规则
        self.assertIn('visibility: visible !important;', dc._CSS)

    def test_hidden_ancestors_use_visibility_not_display(self):
        # display:none 不参与继承 → 后代 visible 覆盖无效 → 修复静默失效
        rules = _css_rules_only()
        self.assertNotIn('display: none', rules)
        self.assertNotIn('display:none', rules)
        # 隐藏规则仍在（防止有人「修 bug」时把整段隐藏删掉）
        self.assertIn('header[data-testid="stHeader"] {visibility: hidden;}', dc._CSS)
        self.assertIn('[data-testid="stToolbar"] {visibility: hidden;}', dc._CSS)

    def test_hover_affordance_uses_theme_primary_color(self):
        # 默认只是个灰色箭头，用户实测「找不到」→ 悬停给可点击暗示
        self.assertIn(':hover', dc._CSS)
        self.assertIn('rgba(31, 95, 174, 0.08)', dc._CSS)  # .streamlit/config.toml primaryColor #1f5fae

    def test_comment_stripper_both_directions(self):
        """反测校验器自身：注释必须剥掉、规则必须原样保留。

        「display:none 不得出现在规则里」这条断言依赖剥离函数——
        若剥离失效（比如正则写错），注释里的举例文字会造成假 FAIL；
        若剥离过头（吃掉规则体），真 display:none 会漏报成假 PASS。两个方向都要钉死。
        """
        self.assertEqual(_css_rules_only("/* display:none */"), "")
        self.assertEqual(_css_rules_only("a {color: red} /* 注释 */ b {color: blue}"),
                         "a {color: red}  b {color: blue}")
        # 注释跨行也要剥（CSS 注释含换行是常态）
        self.assertEqual(_css_rules_only("/* 第一行\n第二行 display:none */"), "")
        # 真实 _CSS 剥离后：放行规则仍在、注释文字不在
        stripped = _css_rules_only()
        self.assertIn('visibility: visible !important;', stripped)
        self.assertNotIn('绝不可把上面两条改成', stripped)


class TestApplyGating(unittest.TestCase):
    """apply() 仅在桌面模式注入；开发浏览器模式零影响。"""

    def setUp(self):
        self._saved_env = os.environ.pop("AI4S_DESKTOP", None)
        self._orig_markdown = dc.st.markdown
        self._orig_desktop = dc.DESKTOP_MODE
        self.calls = []
        dc.st.markdown = lambda *a, **k: self.calls.append((a, k))
        # 与进程导入时的环境解耦：两条路径都显式设定，测试结果确定
        dc.DESKTOP_MODE = False

    def tearDown(self):
        dc.st.markdown = self._orig_markdown
        dc.DESKTOP_MODE = self._orig_desktop
        if self._saved_env is not None:
            os.environ["AI4S_DESKTOP"] = self._saved_env

    def test_dev_mode_no_injection(self):
        dc.apply()
        self.assertEqual(self.calls, [])

    def test_env_flag_injects(self):
        # config 导入失败退化为环境变量判定的路径
        os.environ["AI4S_DESKTOP"] = "1"
        dc.apply()
        self.assertEqual(len(self.calls), 1)
        args, kwargs = self.calls[0]
        self.assertIs(args[0], dc._CSS)
        self.assertTrue(kwargs.get("unsafe_allow_html"))

    def test_frozen_mode_injects_without_env(self):
        # PyInstaller 冻结态：DESKTOP_MODE=True，环境变量不设也要注入
        dc.DESKTOP_MODE = True
        dc.apply()
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""回归用例：`st.tabs(...)` 必须带 `key=`，否则页签选中态会被重跑重置。

血泪来源（2026-10-24 真机实测）：
在「📖 论文检索与追踪」页切到「检索与下载」页签、填关键词、点「🔍 搜索 arXiv」，
**一点就跳回「订阅与推荐」**——提交后台任务触发的重跑让 Streamlit 重建页签组件，
无 key 时选中态回落默认（第一个页签）。用户刚填的词、刚看的结果当场消失；
真机截图脚本也因此在 p6 卡死（明明已切到「引用含金量」，textarea 却判不可见）。

修法：`st.tabs([...], key="…")`——1.63 起支持受控 key，选中态存进 session_state，
跨重跑保持。本用例静态断言 ui/ 下每个 st.tabs 调用都传了 key。
"""
import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "ui")


def scan(path):
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "tabs" and isinstance(node.func.value, ast.Name):
            has_key = any(kw.arg == "key" and isinstance(kw.value, ast.Constant)
                          and kw.value.value for kw in node.keywords)
            if not has_key:
                bad.append(node.lineno)
    return bad


class TestTabsHaveKey(unittest.TestCase):
    def test_all_tabs_have_key(self):
        bad = []
        for name in sorted(os.listdir(UI_DIR)):
            if not name.endswith(".py"):
                continue
            for line in scan(os.path.join(UI_DIR, name)):
                bad.append(f"ui/{name}:{line} 的 st.tabs(...) 没有 key=")
        self.assertEqual(
            bad, [],
            "无 key 的页签在重跑后会跳回第一个（提交后台任务就触发），"
            "用户输入与结果当场丢失：\n  " + "\n  ".join(bad),
        )


if __name__ == "__main__":
    unittest.main()

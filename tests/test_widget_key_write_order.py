# -*- coding: utf-8 -*-
"""禁用规则回归：**widget 实例化之后，不得写它自己的 key**。

血泪来源（2026-10-24 真机实测，补拍脚本连续两次卡在 p2）：
`ui/page_qa.py:429` 在提交成功后执行 `st.session_state["qa_input"] = ""`，而带
`key="qa_input"` 的 st.text_input 早在同一轮 343 行就已实例化 → Streamlit 抛
`StreamlitWidgetAlreadyInstantiatedError`，被 safe_render 兜住后**整页变成错误卡片**：
用户提问 → 答案流式出现 → 一两秒后整页崩掉。AppTest 与 smoke 都测不出来（它们
不模拟"提交完成后再渲染"这条时序），只有真浏览器能看见。

本用例用 AST 做静态时序检查：同一文件里，若某个 `st.session_state["K"] = …`
出现在「声明了 key="K" 的 widget 调用」**之后**，就判违规（前者应改成置标记位，
由下一次运行在 widget 实例化之前消费）。
"""
import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "ui")

# 任何带 type="…" 以外的 key= 参数的 st.* 调用都视为 widget（覆盖 input/button/
# selectbox/multiselect/radio/checkbox/pills/slider/form_submit_button…）。
WIDGET_HINTS = ("input", "button", "select", "checkbox", "radio", "pills", "slider",
                "file_uploader", "toggle", "date", "color_picker", "download",
                "chat_input", "data_editor", "textarea", "number", "camera", "audio",
                "video", "segmented", "feedback", "form")


def _is_st(node):
    """st.xxx(...) 或 st_mod.xxx(...)"""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
        and isinstance(node.func.value, ast.Name)


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def scan(path):
    """返回 [(widget_key, widget_lineno, [晚于实例化的写入行号…])]"""
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    widgets = {}          # key -> 最早的实例化行号
    writes = []           # (key, lineno)
    for node in ast.walk(tree):
        if _is_st(node):
            for kw in node.keywords:
                if kw.arg != "key":
                    continue
                k = _const_str(kw.value)
                if not k:
                    continue
                # 只认控件类调用（函数名命中提示词），避免误判 st.session_state 之类
                fn = node.func.attr.lower()
                if not any(h in fn for h in WIDGET_HINTS):
                    continue
                widgets[k] = min(widgets.get(k, 10 ** 9), node.lineno)
        # st.session_state["K"] = …
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Attribute) \
                    and tgt.value.attr == "session_state":
                k = _const_str(tgt.slice)
                if k:
                    writes.append((k, node.lineno))
    out = []
    for k, wline in writes:
        if k in widgets and wline > widgets[k]:
            out.append((k, widgets[k], wline))
    return out


class TestNoWriteWidgetKeyAfterInstantiation(unittest.TestCase):
    def test_no_late_writes(self):
        bad = []
        for name in sorted(os.listdir(UI_DIR)):
            if not name.endswith(".py"):
                continue
            p = os.path.join(UI_DIR, name)
            for k, wline, aline in scan(p):
                bad.append(f"ui/{name}:{aline} 写了 key={k!r}，但该 widget 在 {wline} 行已实例化")
        self.assertEqual(
            bad, [],
            "widget 实例化后写自己的 key 会抛 StreamlitWidgetAlreadyInstantiatedError"
            "（整页崩成错误卡片）。改成置标记位、下一轮实例化前消费：\n  "
            + "\n  ".join(bad),
        )


if __name__ == "__main__":
    unittest.main()

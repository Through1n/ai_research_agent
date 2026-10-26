"""界面「杂乱度」客观度量：把「界面很杂乱」这种主观感受变成可比的数字。

为什么必须动静结合（这是踩过坑后改的第二版）：
  * 动态（AppTest 渲染计数）—— 能拿到 info/warning/caption 的**真实渲染条数**，
    不会把条件分支里根本没出现的提示算进去，比读源码 grep 准得多。
  * 静态（源码 grep）—— AppTest **不暴露** st.divider / st.tabs 两种元素类型
    （实测 at.get("divider") 与 at.get("tabs") 都取不到），只能回源码数。
    分隔线恰恰是最主要的视觉噪音之一，不能因为测不到就不算。

指标说明：
  hr      —— 分隔线（st.divider() / st.markdown("---") 等横线），打断视觉连续性
  note    —— info/warning/success/error 提示块，过多则满屏色块
  caption —— 灰色小字说明，过多说明信息噪音大
  button  —— 按钮（交互项不算杂乱，但过多会显得堆砌）
  tabs    —— 分区 tab 数。tab 越多，同一时刻**可见**的元素越少（Streamlit
             一次只渲染选中 tab 的视觉内容），是降杂乱的主要手段之一。
noise = hr*2 + note*2 + caption —— 越低越简洁。
"""
import os
import re
import sys

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")

MENU = [
    ("智能总助手", "ui/page_agent.py"),
    ("文献库管理", "ui/page_library.py"),
    ("跨文献问答与溯源", "ui/page_qa.py"),
    ("版式阅读与智能翻译", "ui/page_reading.py"),
    ("论文检索与追踪", "ui/page_search.py"),
    ("引用图谱与综述", "ui/page_graph.py"),
    ("写作辅助与可视化", "ui/page_writing.py"),
    ("系统架构设计", "ui/page_misc.py"),
    ("技术创新亮点", "ui/page_misc.py"),
]

_KINDS = ("markdown", "caption", "text", "button", "info", "warning",
          "error", "success")

# 两种分隔线写法都要抓：st.divider()（无参，后面是右括号不是引号）
# 和 st.markdown("---")。只写一种会漏掉另一种，实测两种都在用。
_DIV_RE = re.compile(
    r'st\.divider\s*\(\s*\)'
    r'|st\.markdown\s*\(\s*(?:f?)["\'](?:---|\*\*\*|___|- - -)')
_TAB_RE = re.compile(r'st\.tabs\(')


def static_counts(rel):
    """源码里数分隔线与 tab（AppTest 取不到这两种元素）。"""
    p = os.path.join(ROOT, rel)
    try:
        src = open(p, encoding="utf-8").read()
    except Exception:
        return 0, 0
    hr = len(_DIV_RE.findall(src))
    tabs = len(_TAB_RE.findall(src))
    return hr, tabs


def _len(scope, kind):
    try:
        return len(scope.get(kind))
    except Exception:
        return 0


def measure(at, hr=0, tabs=0):
    # 只统计主区：侧边栏对所有页面都一样，算进每页会让页面之间失去可比性，
    # 也会把侧边栏基准值重复计入 9 次、稀释单页优化的收益。
    # hr / tabs 由静态源码统计传入（AppTest 取不到这两种元素类型）。
    scope = at.main
    d = {k: _len(scope, k) for k in _KINDS}
    try:
        d["hr_dyn"] = sum(1 for m in scope.get("markdown")
                          if str(getattr(m, "value", "")).strip()
                          in ("---", "***", "___", "- - -"))
    except Exception:
        d["hr_dyn"] = 0
    d["note"] = d["info"] + d["warning"] + d["error"] + d["success"]
    d["hr"] = hr
    d["tabs"] = tabs
    # 分隔线以静态为准（动态只能抓到 markdown 形式，抓不到 st.divider()）
    d["noise"] = hr * 2 + d["note"] * 2 + d["caption"]
    d["total"] = (d["markdown"] + d["caption"] + d["text"] + d["button"]
                  + d["note"] + hr)
    return d


def main():
    at = AppTest.from_file(APP, default_timeout=180).run()
    rows = []
    for m, rel in MENU:
        hr, tabs = static_counts(rel)
        at.session_state["menu"] = m
        at.run()
        d = measure(at, hr=hr, tabs=tabs)
        rows.append((m, d))
    print("%-24s %4s %5s %5s %6s %5s %6s" %
          ("页面", "hr", "note", "cap", "button", "tabs", "noise"))
    print("-" * 62)
    tot = {"noise": 0, "total": 0, "hr": 0, "note": 0, "caption": 0, "tabs": 0}
    for m, d in rows:
        print("%-24s %4d %5d %5d %6d %5d %6d" %
              (m[:24], d["hr"], d["note"], d["caption"], d["button"],
               d["tabs"], d["noise"]))
        for k in tot:
            tot[k] += d[k]
    print("-" * 62)
    print("%-24s %4d %5d %5d %6s %5d %6d" %
          ("合计(9页)", tot["hr"], tot["note"], tot["caption"], "",
           tot["tabs"], tot["noise"]))
    print("noise = hr*2 + note*2 + caption（越低越简洁）；"
          "tabs 不计入 noise，但同一时刻只显示一个 tab。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

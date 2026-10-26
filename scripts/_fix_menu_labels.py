# -*- coding: utf-8 -*-
"""一次性：把脚本里定位侧栏菜单的旧文案（emoji + 序号）改成当前真实文案。

背景：ui/sidebar.py 的 MENU 早已改成「文案即标识」（去掉 emoji 前缀与序号），
但若干脚本仍用旧文案做 Playwright / AppTest 定位 ⇒ 定位器静默失配（脚本报
Timeout 或找不到按钮），看起来像功能坏了，其实只是文案漂移。

本脚本只做「精确字符串替换」，不触碰路径类标识。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAP = [
    ("\U0001F916 0. 智能总助手", "智能总助手"),
    ("\U0001F4C1 1. 文献库管理", "文献库管理"),
    ("\U0001F4AC 2. 跨文献问答与溯源", "跨文献问答与溯源"),
    ("\U0001F4D6 3. 版式阅读与智能翻译", "版式阅读与智能翻译"),
    ("\U0001F50D 4. 论文自动检索与追踪", "论文检索与追踪"),
    ("\U0001F50D 4. 论文检索与追踪", "论文检索与追踪"),
    ("\U0001F578\uFE0F 5. 引用图谱与综述", "引用图谱与综述"),
    ("\u270D\uFE0F 6. 写作辅助与可视化", "写作辅助与可视化"),
    ("\U0001F3D7\uFE0F A. 系统架构设计", "系统架构设计"),
    ("\U0001F3D7\uFE0F 系统架构设计", "系统架构设计"),
    ("\U0001F4A1 B. 技术创新亮点", "技术创新亮点"),
    ("\U0001F4A1 技术创新亮点", "技术创新亮点"),
]

TARGETS = [
    "scripts/apptest_check.py",
    "scripts/_accept_online.py",
    "scripts/_degrade_drill_file.py",
    "scripts/_probe_js_errors.py",
    "scripts/_reshoot_shots.py",
    "scripts/_reshoot_fixups.py",
    "scripts/_reshoot_p3dual.py",
    "scripts/_test_coldstart_firstframe.py",
    "scripts/_ui_clutter_metrics.py",
]


def main():
    total = 0
    for rel in TARGETS:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            print("skip (missing): %s" % rel)
            continue
        with io.open(p, "r", encoding="utf-8") as f:
            src = f.read()
        out = src
        hits = []
        for old, new in MAP:
            n = out.count(old)
            if n:
                hits.append("%s x%d" % (old, n))
                out = out.replace(old, new)
        if out != src:
            with io.open(p, "w", encoding="utf-8", newline="") as f:
                f.write(out)
            total += 1
            print("[changed] %s :: %s" % (rel, "; ".join(hits)))
        else:
            print("[no-op]   %s" % rel)
    print("\n%d file(s) rewritten" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())

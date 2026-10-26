"""使用 Streamlit AppTest 在无浏览器情况下运行整个应用，验证渲染无异常，并模拟离线流程。

导航定位：侧边栏现有「接入方式」与「导航菜单」两个 radio，按标签/选项自动匹配，
避免依赖 radio 出现顺序。
"""
import sys
from streamlit.testing.v1 import AppTest

APP = r"C:\Users\Administrator\ai_research_agent\app.py"


def show(tag, at):
    if at.exception:
        for e in at.exception:
            print(f"❌ 异常[{tag}]：", e.value)
        return False
    print(f"✅ [{tag}] 渲染正常")
    return True


def nav(at, label):
    """按标签定位「导航菜单」radio 并切换页面。"""
    for r in at.sidebar.radio:
        if any(str(o).startswith(label[:2]) for o in r.options):
            r.set_value(label).run()
            return True
    print(f"❌ 未找到导航项 radio：{label}")
    return False


at = AppTest.from_file(APP, default_timeout=90)
at.run()
ok = show("初始加载(默认Agent页)", at)

# 0) 接入方式双分支渲染（预设平台 / 自定义端点）
for r in at.sidebar.radio:
    if "接入方式" in (r.label or ""):
        r.set_value("➕ 自定义端点（任意模型）").run()
        ok = show("侧边栏-自定义端点表单", at) and ok
        r2 = next((x for x in at.sidebar.radio if "接入方式" in (x.label or "")), None)
        r2.set_value("🏢 预设平台").run()
        ok = show("侧边栏-预设平台选择", at) and ok
        break

# 1) 侧边栏加载演示数据
btn = next((b for b in at.sidebar.button if "一键加载演示数据" in b.label), None)
if btn:
    btn.click().run()
    ok = show("加载演示数据 6 篇", at) and ok

# 2) 各页面逐一渲染
for label, tag in [
    ("文献库管理", "文献库管理页"),
    ("引用图谱与综述", "引用图谱页"),
    ("写作辅助与可视化", "写作辅助页"),
    ("论文检索与追踪", "检索追踪页"),
    ("版式阅读与智能翻译", "版式阅读页"),
    ("系统架构设计", "系统架构页"),
    ("技术创新亮点", "技术创新页"),
    ("跨文献问答与溯源", "跨文献问答页"),
]:
    if not nav(at, label):
        ok = False
        continue
    ok = show(tag, at) and ok

# 3) 图谱页离线构建
if nav(at, "引用图谱与综述"):
    gb = next((b for b in at.button if "构建引用图谱" in b.label), None)
    if gb:
        gb.click().run()
        ok = show("离线构建引用图谱", at) and ok

print("\n" + ("🎉 AppTest 全部页面/离线流程通过" if ok else "⚠️ 存在异常，需修复"))
sys.exit(0 if ok else 1)

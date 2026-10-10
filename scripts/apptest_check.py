"""使用 Streamlit AppTest 在无浏览器情况下运行整个应用，验证渲染无异常，并模拟离线流程。"""
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


at = AppTest.from_file(APP, default_timeout=90)
at.run()
ok = show("初始加载(默认Agent页)", at)

# 1) 侧边栏加载演示数据
btn = next((b for b in at.sidebar.button if "一键加载演示数据" in b.label), None)
if btn:
    btn.click().run()
    ok = show("加载演示数据 v1/v7/BERT", at) and ok

# 2) 切到文献库页面
at.sidebar.radio[0].set_value("📁 1. 文献库管理").run()
ok = show("文献库管理页", at) and ok

# 3) 切到引用图谱并离线构建（无需 LLM）
at.sidebar.radio[0].set_value("🕸️ 5. 引用图谱与综述").run()
gb = next((b for b in at.button if "构建引用图谱" in b.label), None)
if gb:
    gb.click().run()
    ok = show("离线构建引用图谱", at) and ok

# 4) 切到写作页（CSV 未上传，应正常渲染控件）
at.sidebar.radio[0].set_value("✍️ 6. 写作辅助与可视化").run()
ok = show("写作辅助页", at) and ok

# 5) 检索页
at.sidebar.radio[0].set_value("🔍 4. 论文自动检索与追踪").run()
ok = show("检索追踪页", at) and ok

# 6) 阅读页
at.sidebar.radio[0].set_value("📖 3. 版式阅读与智能翻译").run()
ok = show("版式阅读页", at) and ok

# 7) 架构 / 创新页
at.sidebar.radio[0].set_value("🏗️ 系统架构设计").run()
ok = show("系统架构页", at) and ok
at.sidebar.radio[0].set_value("💡 技术创新亮点").run()
ok = show("技术创新页", at) and ok

# 8) 问答页（未提问，应正常）
at.sidebar.radio[0].set_value("💬 2. 跨文献问答与溯源").run()
ok = show("跨文献问答页", at) and ok

print("\n" + ("🎉 AppTest 全部页面/离线流程通过" if ok else "⚠️ 存在异常，需修复"))
sys.exit(0 if ok else 1)

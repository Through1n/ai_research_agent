"""AI4S 科研助手 Agent —— Streamlit 入口。

运行：streamlit run app.py   （或双击「启动科研助手.bat」）
架构：交互层(ui) → Agent 内核(core) → 工具/服务层(tools/services) → 存储层(storage)。

演示加固：依赖导入 / 容器初始化 / 侧边栏 / 页面路由全部有异常兜底，
任何单页异常都不会把整个应用打成红色 Traceback（见 ui/common.py safe_render）。
"""
import traceback

import streamlit as st

st.set_page_config(page_title="AI4S 科研助手 Agent", page_icon="🔬", layout="wide")

# ---------- 依赖导入兜底：环境缺依赖时给中文提示而不是崩溃 ----------
try:
    from services.state import get_container
    from ui.common import ensure_state, safe_render, llm_failure_banner
    from ui.sidebar import render as render_sidebar, render_flash
    from ui import (page_agent, page_library, page_qa, page_reading,
                    page_search, page_graph, page_writing, page_misc)
    from ui import desktop_chrome
except Exception as e:                                   # pragma: no cover
    st.error(f"❌ 依赖模块加载失败：{str(e)[:200]}")
    st.caption("🩺 可能原因：依赖未安装或版本不匹配（requirements.txt）。")
    st.caption("🛠️ 怎么办：在项目环境执行 `pip install -r requirements.txt` 后重启。")
    with st.expander("🔧 技术细节（供排查）", expanded=False):
        st.code(traceback.format_exc(limit=6))
    st.stop()

try:
    desktop_chrome.apply()
except Exception:                                        # pragma: no cover
    pass

# ---------- 全局 session_state 集中初始化：先初始化再读，杜绝 KeyError ----------
# 各页面自身的私有 key 仍在页面顶部 ensure_state 中声明，此处只放跨页共享/高频 key。
ensure_state({
    # 侧边栏
    "saved_api_key": "",
    # 📁 文献库管理（ui/page_library.py）
    "pending": None,
    "struct": None,
    "meta_view": None,
    "formula_view": None,
    "ref_view": None,
    # 🔍 检索与追踪（ui/page_search.py）
    "arxiv_res": list,
    "pm": list,
    "web": list,
    "rec_rows": list,
    "trk_report": list,
    # 🕸️ 引用图谱（ui/page_graph.py）
    "graph_pack": None,
    "review": "",
    "future": "",
    # 🤖 Agent 总助手（ui/page_agent.py）
    "agent_runs": list,
    # 💬 问答页写入的跨页跳转标记（ui/page_qa.py 写入，公共层只读）
    "qa_jump_page": None,
    # 跨 rerun 存活的 flash 提示（set_flash 写入 / render_flash 消费并清空）
    "_flash": None,
})

# ---------- 服务容器初始化兜底 ----------
try:
    ctx = get_container()
except Exception as e:                                   # pragma: no cover
    st.error(f"❌ 服务容器初始化失败：{str(e)[:200]}")
    st.caption("🩺 可能原因：向量库目录不可写 / storage 损坏 / 磁盘权限不足。")
    st.caption("🛠️ 怎么办：删除 storage/chroma 目录后重启即可自动重建。")
    with st.expander("🔧 技术细节（供排查）", expanded=False):
        st.code(traceback.format_exc(limit=6))
    st.stop()

# ---------- 侧边栏兜底：侧边栏异常不应导致主体不可用 ----------
try:
    menu = render_sidebar(ctx)
except Exception as e:                                   # pragma: no cover
    st.sidebar.error(f"⚠️ 侧边栏渲染异常：{str(e)[:160]}")
    st.sidebar.caption("🛠️ 怎么办：已回退到默认页面，主体功能仍可使用。")
    menu = "🤖 0. Agent 总助手"

_PAGES = {
    "🤖": (page_agent.render, "Agent 总助手"),
    "📁": (page_library.render, "文献库管理"),
    "💬": (page_qa.render, "问答与溯源"),
    "📖": (page_reading.render, "版式阅读与翻译"),
    "🔍": (page_search.render, "检索与追踪"),
    "🕸️": (page_graph.render, "引用图谱与综述"),
    "✍️": (page_writing.render, "写作辅助与可视化"),
    "🏗️": (page_misc.render_arch, "系统架构"),
    "💡": (page_misc.render_innov, "创新亮点"),
}

# ---------- flash 提示统一渲染（主体区顶部，跨 rerun 存活） ----------
# 侧边栏/页面在 st.rerun() 前调用 set_flash()，rerun 后由这里重放，避免提示被吞。
try:
    render_flash()
except Exception:                                        # pragma: no cover
    pass

# ---------- LLM 失败横幅（框架层统一消费 core/llm.py 的 last_error） ----------
# Key 无效 / 限流 429 / 超时 / 断网时，core/llm.py 只记录 last_error 并返回伪响应，
# 各页面会把伪响应当答案渲染——用户既看到"假答案"又无从排查。这里在主体区顶部
# 按错误类型给出人话指引 + 日志位置；下次调用成功后 last_error 清空，横幅自动消失。
try:
    llm_failure_banner(ctx)
except Exception:                                        # pragma: no cover
    pass

_hit = False
for prefix, (fn, name) in _PAGES.items():
    if str(menu).startswith(prefix):
        safe_render(fn, ctx, name)
        _hit = True
        break
if not _hit:
    st.info("📭 未匹配到页面路由，已在左侧导航重新选择。")

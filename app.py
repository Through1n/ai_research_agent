"""AI4S 科研助手 Agent —— Streamlit 入口。

运行：streamlit run app.py   （或双击「启动科研助手.bat」）
架构：交互层(ui) → Agent 内核(core) → 工具/服务层(tools/services) → 存储层(storage)。

演示加固：依赖导入 / 容器初始化 / 侧边栏 / 页面路由全部有异常兜底，
任何单页异常都不会把整个应用打成红色 Traceback（见 ui/common.py safe_render）。
"""
import os
import time
import traceback

import streamlit as st

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "errors.log")


def _log_error(what, exc):
    """异常落盘（不依赖 ui.common，供依赖导入失败等早期场景使用）。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s | %s: %s\n%s\n%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), what, type(exc).__name__, exc,
                tb[-4000:], "-" * 60))
    except Exception:
        pass

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
    _log_error("加载依赖模块", e)
    st.error("⚠️ 操作失败：加载依赖模块。" + ("storage/ 目录不可写（容器挂载属主不对）" if isinstance(e, OSError) else "依赖未安装或版本不匹配，应用无法启动。"))
    st.caption("🩺 可能原因：① 依赖缺失或版本不匹配（requirements.txt）；② storage/ 不可写——常见于挂载卷属主为 root、而容器以 uid 10001 运行。")
    st.caption("🛠️ 怎么办：① `pip install -r requirements.txt` 后重启；② 宿主机执行 `chown -R 10001:10001 ./storage ./logs` 后重启。")
    with st.expander("🔍 查看技术细节"):
        st.code(traceback.format_exc(limit=6))
    st.stop()

try:
    desktop_chrome.apply()
except Exception:                                        # pragma: no cover
    pass

# ---------- 每次重跑的唯一序号：CSS 注入的「本轮幂等」锚点 ----------
# 为什么需要（2026-10-24 实测事故）：Streamlit 每次 rerun 都会**重建整个元素树**，
# 上一轮 st.markdown 注入的 <style> 不会保留下来。若注入用「会话级旗标」跳过重跑，
# 则用户第一次点击（侧栏导航/任意按钮）后全站令牌 CSS 整体消失，界面静默退回
# Streamlit 默认外观（实测：导航后 document 内 hasTokens=False、侧栏圆点复现）。
# 故幂等粒度必须是「每次运行注入一次」，用递增序号做锚点；design.apply() 与
# ui/common.py::_inject_css_once 都按这个序号判定本轮是否已注入。
st.session_state["_run_seq"] = int(st.session_state.get("_run_seq", 0)) + 1

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
    _log_error("初始化服务容器", e)
    st.error("⚠️ 操作失败：初始化服务容器。存储目录不可写或已损坏，应用无法启动。")
    st.caption("🩺 可能原因：向量库目录不可写 / storage 损坏 / 磁盘权限不足。")
    st.caption("🛠️ 怎么办：删除 storage/chroma 目录后重启即可自动重建。")
    with st.expander("🔍 查看技术细节"):
        st.code(traceback.format_exc(limit=6))
    st.stop()

def _llm_err_seq():
    """读取 LLM 失败序号（core/llm.py 每写一次 last_error 就 +1）。

    用它而不是用错误字符串做比对：两个不同页面完全可能报出**一模一样**的
    错误串（同一个 401），字符串比对分不清"这次是哪个页面新写的"。
    """
    try:
        return int(getattr(getattr(ctx, "llm", None), "last_error_seq", 0) or 0)
    except Exception:
        return 0


# F7 基准快照：**侧边栏渲染之前**取。侧边栏的「🔌 测试连通性」也是一次真实调用，
# 它失败时同样应该让横幅出现在当前页（否则 degrade-api 的实测 D/F 会翻回「无」）。
_SEQ_BEFORE = _llm_err_seq()

# ---------- 侧边栏兜底：侧边栏异常不应导致主体不可用 ----------
try:
    menu = render_sidebar(ctx)
except Exception as e:                                   # pragma: no cover
    _log_error("渲染侧边栏", e)
    st.sidebar.error("⚠️ 操作失败：渲染侧边栏。已回退到默认页面，主体功能仍可使用。")
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

_page_name = None
for prefix, (fn, _n) in _PAGES.items():
    if str(menu).startswith(prefix):
        _page_name = _n
        break

_hit = False
for prefix, (fn, name) in _PAGES.items():
    if str(menu).startswith(prefix):
        safe_render(fn, ctx, name)
        _hit = True
        break
if not _hit:
    st.info("📭 未匹配到页面路由，已在左侧导航重新选择。")
_seq_after = _llm_err_seq()

# ---------- LLM 失败横幅（框架层统一消费 core/llm.py 的 last_error） ----------
# Key 无效 / 限流 429 / 超时 / 断网时，core/llm.py 只记录 last_error 并返回伪响应，
# 各页面会把伪响应当答案渲染——用户既看到"假答案"又无从排查。这里按错误类型给出
# 人话指引 + 日志位置；下次调用成功后 last_error 清空，横幅自动消失。
#
# 为什么放在页面渲染**之后**（Round 12 F2）：last_error 是页面内那次 LLM 调用
# 失败时才被写上的。横幅若在页面之前渲染，读到的永远是"上一轮"的状态——
# 失败当轮看不到横幅，要等下一次任意交互触发 rerun 才出现，等于"横幅永不出现"。
# 挪到页面之后，失败当轮即可见。（代价：横幅显示在主体区底部而非顶部。）
#
# F7 收敛：横幅只挂在**产生它的那一页**。last_error 一旦被写，直到下一次调用
# 成功才清空，所以"在问答页失败 → 切到文献库还挂着"会被读成"这一页坏了"。
# 判据 = 本轮本页是否新写过 last_error（序号变了），**不用页面清单**（清单会随
# 功能漂移，写错等于把真横幅藏掉）。
# 基准快照取在侧边栏之前，所以侧边栏「🔌 测试连通性」失败也算"本页本轮产生"。
try:
    _err = getattr(getattr(ctx, "llm", None), "last_error", None)
    if not _err:
        st.session_state.pop("_llm_err_page", None)
    elif _page_name and _seq_after != _SEQ_BEFORE:
        st.session_state["_llm_err_page"] = _page_name
    if _page_name and st.session_state.get("_llm_err_page") == _page_name:
        llm_failure_banner(ctx)
except Exception:                                        # pragma: no cover
    pass

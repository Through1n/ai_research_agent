"""共享 UI 组件：页面头部、美化表格、空状态、错误边界、执行轨迹时间线、溯源卡片。

UI 一致性规范（话术 3）——本模块是全部页面的公共层，提供：
  1. page_header()          统一页面顶部（浅色渐变卡片）
  2. pretty_table()         统一表格渲染（浅蓝/浅紫渐变表头 + 斑马纹）
  3. ensure_state()         session_state 集中初始化，避免 KeyError
  4. empty_state()          空数据友好提示 + 引导操作
  5. friendly_error() / net_error() / llm_guard()   API 调用失败的统一降级话术
  6. long_block()           长文本自动折叠
  7. safe_render() / safe_call()  统一异常边界（增强版，保持旧调用签名）
  8. jump_banner()          跨页跳转提示条（消费 page_qa 写入的 qa_jump_page）

对外历史函数（render_styled_table / html_table / source_card / render_trace /
memory_mb / require_library / resolve_pdf / embed_html）全部保留，签名不变，
以免破坏其他页面（page_qa / page_reading / page_writing）的既有调用。
"""
import json
import traceback
from html import escape as _escape

import pandas as pd
import streamlit as st

# 长文本折叠阈值：超过该字数或 15 行一律折叠（UI 规范 5）
LONG_TEXT_LIMIT = 300
LONG_LINE_LIMIT = 15

# 跨页跳转 session_state key（由 ui/page_qa.py 写入，本模块只读）
QA_JUMP_KEY = "qa_jump_page"
# 📖 版式阅读页在 ui/sidebar.py MENU / app.py _PAGES 中的索引
READING_MENU_IDX = 3


# ============================ 基础工具 ============================
def _esc(x) -> str:
    """统一转义，防止用户输入/PDF 文本里的 HTML 破坏页面结构或造成注入。"""
    return _escape(str(x if x is not None else ""), quote=True)


def step_text(action: str, cur=None, total=None) -> str:
    """生成带进度的 spinner 文案：`正在检索 3/8 …`（UI 规范 6）。"""
    if total and cur is not None:
        return f"{action} {int(cur)}/{int(total)} …"
    return f"{action} …"


def at(items, idx, default=None):
    """安全下标读取：越界或非法索引返回 default，杜绝 IndexError（演示加固）。"""
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return default
    try:
        if 0 <= i < len(items):
            return items[i]
    except TypeError:
        return default
    return default


# ============================ UI 规范 1：页面顶部 ============================
def page_header(title: str, subtitle: str = "", icon: str = ""):
    """统一页面顶部：渐变浅色卡片 + 标题 + 一句话说明。各 page.render 第一行调用。

    向后兼容：旧调用 `page_header("📁 文献库管理", "说明")` 仍可用（positional）。
    """
    head = f"{icon} {title}".strip() if icon else str(title or "").strip()
    sub = str(subtitle or "").strip()
    st.markdown(
        f"""<div style="background:linear-gradient(90deg,#eef4fc 0%,#f6f1fd 100%);
border:1px solid #d9e3f2;border-left:4px solid #3b6fb6;border-radius:10px;
padding:12px 16px;margin:2px 0 14px;box-shadow:0 1px 3px rgba(23,58,99,.06);">
<div style="font-size:20px;font-weight:700;color:#173a63;line-height:1.35;">{_esc(head)}</div>
{"" if not sub else f'<div style="margin-top:5px;font-size:13px;color:#4a5a70;line-height:1.65;">{_esc(sub)}</div>'}
</div>""",
        unsafe_allow_html=True,
    )


# ============================ UI 规范 4：统一表格 ============================
_PRETTY_CSS = """<style>
.ptab-wrap{overflow-x:auto;margin:8px 0 12px;border:1px solid #dde5f0;border-radius:10px;}
table.ptab{border-collapse:collapse;width:100%;font-size:13px;
font-family:'Segoe UI','Microsoft YaHei',sans-serif;}
.ptab th{background:linear-gradient(90deg,#e2edfb 0%,#efe7fb 100%);color:#17325a;
font-weight:700;padding:9px 12px;text-align:left;border-bottom:1.5px solid #bfd2ee;white-space:nowrap;}
.ptab td{padding:8px 12px;border-bottom:1px solid #e9eef5;color:#26313f;
vertical-align:top;line-height:1.6;}
.ptab tr:nth-child(even) td{background:#f7fafd;}
.ptab tr:hover td{background:#eef4fc;}
</style>"""


def _to_frame(df_or_rows, columns=None) -> pd.DataFrame:
    """把 DataFrame / dict / list[dict] / list[list|tuple] 归一化成 DataFrame。"""
    if df_or_rows is None:
        return pd.DataFrame()
    if isinstance(df_or_rows, pd.DataFrame):
        df = df_or_rows.copy()
    else:
        try:
            df = pd.DataFrame(df_or_rows)
        except Exception:
            return pd.DataFrame()
    if columns:
        try:
            missing = [c for c in columns if c in df.columns]
            df = df[missing] if missing else df
        except Exception:
            pass
    return df


def pretty_table(df_or_rows, columns=None, title: str = "", max_rows: int = 200):
    """全项目统一表格：浅色渐变表头 + 深色文字 + 细边框 + 斑马纹。

    支持 DataFrame / list[dict] / list[list]。空表自动给出友好提示而非渲染空白。
    """
    if title:
        st.markdown(f"##### {title}")
    try:
        df = _to_frame(df_or_rows, columns)
    except Exception as e:
        st.warning(f"⚠️ 表格数据解析失败，已跳过渲染：{str(e)[:120]}")
        return
    if df is None or df.empty or len(df.columns) == 0:
        st.info("📭 暂无数据可展示。")
        return
    if len(df) > max_rows:
        st.caption(f"共 {len(df)} 行，仅展示前 {max_rows} 行。")
        df = df.head(max_rows)
    try:
        body = df.to_html(index=False, classes="ptab", border=0, escape=True)
        st.markdown(_PRETTY_CSS + f'<div class="ptab-wrap">{body}</div>', unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"⚠️ 表格渲染失败，已降级为原生表格：{str(e)[:120]}")
        st.dataframe(df, use_container_width=True, hide_index=True)


# ============================ UI 规范 3：统一输出分级 ============================
def empty_state(text: str, hints=(), icon: str = "📭"):
    """空状态统一话术：说明 + 引导操作列表（演示加固：绝不渲染空白）。"""
    st.info(f"{icon} {text}")
    hints = [h for h in (hints or ()) if h]
    if hints:
        st.caption("👉 " + "；".join(f"**{h}**" if not h.startswith(("👉", "·")) else h for h in hints))


def friendly_error(what: str, exc, reasons=(), actions=()):
    """失败统一话术：错误 + 可能原因 + 怎么办（演示加固：绝不出现红色 Traceback）。"""
    st.error(f"❌ {what}失败：{str(exc)[:180]}")
    if reasons:
        st.caption("🩺 可能原因：" + "；".join(str(r) for r in reasons if r))
    if actions:
        st.caption("🛠️ 怎么办：" + "；".join(str(a) for a in actions if a))


_NET_REASONS = ["演示现场外网不可达或被代理拦截", "arXiv / PubMed 接口限流或临时不可用"]
_NET_ACTIONS = ["重试一次", "点击左侧「🚀 一键加载演示数据」使用离线数据继续演示",
                "检查网络/代理后再试"]


def net_error(what: str, exc, fallback_hint: str = ""):
    """网络类 API 失败的统一降级提示（页面 4 检索页专用话术）。"""
    actions = list(_NET_ACTIONS)
    if fallback_hint:
        actions.insert(1, fallback_hint)
    friendly_error(what, exc, reasons=_NET_REASONS, actions=actions)


def llm_guard(ctx, action: str = "该操作") -> bool:
    """大模型可用性守卫：未配置 Key 时给中文引导，而不是静默无反应。"""
    try:
        if getattr(ctx, "llm", None) is not None and ctx.llm.available:
            return True
    except Exception:
        pass
    st.warning(f"⚠️ {action}需要大模型，但未检测到可用 API Key。")
    st.caption("🛠️ 怎么办：在左侧「接入方式」选择平台并填写 Key；"
               "无 Key 时仍可使用本地演示数据与离线功能（解析/检索/图谱检索式演示）。")
    return False


# LLM 失败分类话术：命中 core/llm.py 落盘的 last_error 后按类型给人话指引
_LLM_ERR_KINDS = (
    ("key", ("401", "invalid_api_key", "令牌", "验证不正确", "api key", "apikey", "unauthorized"),
     "API Key 无效或已过期",
     ["Key 填写有误 / 已过期 / 被平台重置", "平台账号额度已用完"],
     ["在左侧「接入方式」重新填写正确的 Key，再点「🔌 测试连通性」验证",
      "切换到其他预设平台或本地 Ollama 继续演示"]),
    ("rate", ("429", "rate limit", "too many requests", "限流", "频率"),
     "触发平台限流（429）",
     ["请求过于频繁，平台暂时限流", "当前 Key 的并发/额度已打满"],
     ["等待 30 秒到 1 分钟后重试", "减少连续操作，或切换其他平台"]),
    ("timeout", ("timeout", "timed out", "超时"),
     "大模型调用超时",
     ["平台响应慢或网络抖动（已自动重试 3 次）", "生成内容过长导致超时"],
     ["重试一次；仍超时可换更短的问题", "检查网络/代理后重试"]),
    ("network", ("connection", "connect", "network", "网络", "ssl", "proxy"),
     "无法连接大模型服务",
     ["本机网络不可达或代理拦截了出网请求", "自定义端点 Base URL 填写有误"],
     ["检查网络/代理设置后重试", "自定义端点请核对 Base URL 与服务是否已启动"]),
)


def classify_llm_error(err) -> tuple:
    """把原始错误文本分类为 (人话摘要, 可能原因 list, 怎么办 list)；未命中给通用兜底。"""
    s = str(err or "")
    low = s.lower()
    for _kind, keys, what, reasons, actions in _LLM_ERR_KINDS:
        if any(k in low for k in keys):
            return what, list(reasons), list(actions)
    return ("大模型调用失败", ["平台返回了未分类的错误"], ["重试一次"])


def llm_failure_banner(ctx, action: str = "大模型调用"):
    """把 ctx.llm.last_error 渲染成人话警告（框架层统一消费，惠及所有页面）。

    背景：core/llm.py 调用失败（重试后仍失败，或 401/400 等不必重试的错误）
    不抛异常，只把原因写进 llm.last_error 并返回伪响应——用户看到的是
    「【LLM 暂时不可用…】」混在答案里，既不知道该怎么办，也不知道日志在哪。
    本函数在主体区顶部按错误类型给出「人话 + 可能原因 + 怎么办 + 日志位置」。
    last_error 在下一次调用成功时自动清空，横幅随之消失，无需手动关闭。
    """
    try:
        err = getattr(getattr(ctx, "llm", None), "last_error", None)
    except Exception:
        err = None
    if not err:
        return
    what, reasons, actions = classify_llm_error(err)
    try:
        from core.llm import FAIL_LOG_PATH_HINT
    except Exception:
        FAIL_LOG_PATH_HINT = "storage/llm_failures.log"
    st.warning(f"⚠️ {action}失败：{what}（点击页面内其他功能仍可继续使用）")
    st.caption("🩺 可能原因：" + "；".join(reasons))
    st.caption("🛠️ 怎么办：" + "；".join(actions) + f"；完整错误已记录到 {FAIL_LOG_PATH_HINT}")
    with st.expander("🔧 错误摘要（供排查，已自动脱敏）", expanded=False):
        st.code(str(err)[:300])


# ============================ UI 规范 5：长文本折叠 ============================
def long_block(text, title: str = "查看完整内容", limit: int = LONG_TEXT_LIMIT,
               expander_label: str = "📄"):
    """超过 ~300 字或 15 行的内容自动折叠；短内容直接展示。"""
    t = "" if text is None else str(text)
    if not t.strip():
        st.info("📭 暂无内容。")
        return
    if len(t) <= limit and t.count("\n") < LONG_LINE_LIMIT:
        st.markdown(t)
        return
    st.markdown(t[:limit].rstrip() + " …")
    with st.expander(f"{expander_label} {title}（共 {len(t)} 字）", expanded=False):
        st.markdown(t)


# ============================ state / 异常边界 ============================
def ensure_state(defaults: dict):
    """集中初始化 session_state key：先初始化再读，杜绝 KeyError（演示加固）。

    value 为 callable 时调用它取默认值；list/dict/set 自动 copy，避免多会话共享可变对象。
    """
    ss = st.session_state
    for k, v in (defaults or {}).items():
        if k in ss:
            continue
        try:
            val = v() if callable(v) else v
            ss[k] = val.copy() if isinstance(val, (dict, list, set)) else val
        except Exception:
            ss[k] = None


def safe_call(what, fn, *args, default=None, reasons=(), actions=(), **kwargs):
    """函数级异常边界：返回 (ok, value)；失败时渲染友好错误并返回 (False, default)。"""
    try:
        return True, fn(*args, **kwargs)
    except Exception as e:
        friendly_error(what, e, reasons=reasons, actions=actions)
        return False, default


def safe_render(fn, ctx, what="该功能"):
    """页面级错误边界（增强版）：捕获异常并优雅降级，避免整页白屏崩溃。

    保持 ui/common.py 的旧调用签名 `safe_render(fn, ctx, what)`，app.py 无需改动。
    """
    try:
        fn(ctx)
    except Exception as e:
        st.error(f"⚠️ 「{what}」页面暂时不可用：{str(e)[:200]}")
        st.caption("🩺 可能原因：该项依赖的外部服务未就绪 / 数据尚未加载 / 网络受限。")
        st.caption("🛠️ 怎么办：点击左侧「🚀 一键加载演示数据」后重试；"
                   "或切换到其他页面继续演示，其余功能不受影响。")
        with st.expander("🔧 技术细节（供排查）", expanded=False):
            st.code(traceback.format_exc(limit=6))


def require_library(ctx):
    """空库守卫：文献库为空时给出引导并返回 False（页面统一调用，保持旧签名）。"""
    try:
        ok = bool(getattr(ctx, "files", None))
    except Exception:
        ok = False
    if not ok:
        empty_state(
            "文献库为空，暂无内容可展示。",
            hints=["点击左侧「🚀 一键加载演示数据（v1/v7/BERT）」",
                   "或到「📁 文献库管理」上传 PDF"],
        )
        return False
    return True


# ============================ 任务组 C：跨页跳转提示条 ============================
def _jump_target_label(source: str) -> str:
    """把文件名裁剪成可读标题（去掉 .pdf 后缀）。"""
    s = str(source or "")
    return s[:-4] if s.lower().endswith(".pdf") else s


def jump_banner(show_goto: bool = True):
    """消费 ui/page_qa.py 写入的 qa_jump_page，在侧边栏显示「→ 跳转到《X》第 N 页」提示条。

    只读该 key，不修改 page_qa.py；跳转动作通过 session_state["menu"] + query_params 完成。
    """
    payload = st.session_state.get(QA_JUMP_KEY)
    if not isinstance(payload, dict) or not payload.get("source"):
        return
    try:
        page = int(payload.get("page", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    para = payload.get("para_idx")
    label = _jump_target_label(payload.get("source"))
    tail = f"（第 {para} 段）" if para else ""
    with st.container(border=True):
        st.info(f"🎯 问答溯源待跳转：《{label}》第 {page} 页{tail}")
        if show_goto:
            c1, c2 = st.columns([1, 1])
            if c1.button("🗺️ 前往该页", key="jump_go"):
                try:
                    from ui.sidebar import MENU
                    target = at(MENU, READING_MENU_IDX, MENU[0] if MENU else "🤖 0. Agent 总助手")
                    st.session_state["menu"] = target
                    st.query_params["p"] = str(READING_MENU_IDX)
                    st.query_params["jump_page"] = str(page)
                    st.query_params["paper"] = str(payload.get("source"))
                    st.rerun()
                except Exception as e:
                    st.caption(f"跳转失败，请手动切换左侧导航：{str(e)[:100]}")
            if c2.button("✅ 已查看", key="jump_done"):
                st.session_state[QA_JUMP_KEY] = None
                try:
                    st.rerun()
                except Exception:
                    pass


# ============================ 历史组件（签名保持不变） ============================
def render_styled_table(df, title: str = ""):
    """轻量美化表格：title 可选，统一走 pretty_table 的浅色渐变样式。"""
    pretty_table(df, title=title)


def embed_html(html, height=600, scrolling=True):
    """HTML 嵌入兼容层：优先 components.html，移除后自动降级 st.iframe。"""
    import streamlit as _st
    try:
        import streamlit.components.v1 as _c
        _c.html(html, height=height, scrolling=scrolling)
    except Exception:
        try:
            _st.iframe(html, height=height)
        except Exception as e:
            st.warning(f"⚠️ 可视化组件渲染失败：{str(e)[:120]}")
            st.caption("🛠️ 怎么办：刷新页面重试；其余功能不受影响。")


def html_table(df):
    """旧版 HTML 表格（深色表头），保留供历史调用；新代码请统一用 pretty_table。"""
    css = """<style>.ptable{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;}
.ptable th{background:linear-gradient(135deg,#3b6fb6,#2f5a96);color:#fff;padding:9px 12px;text-align:left;}
.ptable td{padding:8px 12px;border-bottom:1px solid #e8edf3;}
.ptable tr:nth-child(even){background:#f6f9fd;}.ptable tr:hover{background:#eef4fb;}</style>"""
    try:
        body = df.to_html(index=False, classes="ptable", border=0, escape=False)
    except Exception as e:
        pretty_table(df)
        return f"<!-- 旧表格降级：{_esc(str(e)[:80])} -->"
    return css + f'<div style="overflow-x:auto;">{body}</div>'


def source_card(source, page, text, n=None, snippet_len=520):
    """溯源卡片：页码徽章可点击跳转阅读页（?p=3&jump_page=N&paper=文件名）。"""
    tag = f"【{n}】 " if n else ""
    t = str(text or "")[:snippet_len]
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 阅读页在 MENU 中的索引为 3（📖 版式阅读与智能翻译）
    safe_src = str(source).replace('"', "%22")
    badge = (f'<a href="?p={READING_MENU_IDX}&jump_page={page}&paper={safe_src}" target="_self" '
             f'style="text-decoration:none;color:#fff;display:inline-block;">第 {page} 页</a>')
    return f"""<div style="background:#f7faff;border-left:4px solid #3b6fb6;border-radius:8px;
padding:12px 16px;margin:8px 0;box-shadow:0 1px 2px rgba(0,0,0,.05);">
<div style="display:flex;justify-content:space-between;margin-bottom:6px;">
<strong style="color:#27518a;font-size:13.5px;">{tag}📄 {_esc(source)}</strong>
<span style="background:#3b6fb6;color:#fff;padding:1px 10px;border-radius:12px;font-size:11.5px;">{badge}</span></div>
<div style="color:#333;font-size:12.5px;line-height:1.75;white-space:pre-wrap;">{t}…</div></div>"""


_ICON = {
    "plan": "🗂️", "route": "🧭", "thought": "💭", "tool": "🔧",
    "observation": "📥", "final": "✅", "guard": "🛡️", "error": "⚠️",
}


def render_trace(trace, in_expander=False):
    """把 Agent Trace 渲染为带样式的时间线。"""
    try:
        dicts = trace.to_dicts()
    except Exception as e:
        st.warning(f"⚠️ 执行轨迹解析失败：{str(e)[:120]}")
        return
    body = st.expander("🧭 Agent 执行轨迹（规划 / 路由 / 工具 / 思考）",
                       expanded=not in_expander) if in_expander else st.container(border=True)
    with body:
        for ev in dicts:
            try:
                kind = ev.get("kind", "")
                icon = _ICON.get(kind, "•")
                if kind == "plan":
                    st.markdown(f"**{icon} {ev.get('title','')}**")
                    for i, s in enumerate((ev.get("data") or {}).get("steps", []), 1):
                        st.markdown(f"<div style='margin-left:18px;color:#444;font-size:13px;'>{i}. {_esc(s)}</div>",
                                    unsafe_allow_html=True)
                elif kind == "route":
                    st.markdown(f"{icon} **{ev.get('title','')}** → `{(ev.get('data') or {}).get('expert','')}`  "
                                f"<span style='color:#888;font-size:12px;'>{_esc(ev.get('content',''))}</span>",
                                unsafe_allow_html=True)
                elif kind == "thought":
                    st.markdown(f"<div style='background:#f4f6fa;border-left:3px solid #9aa7bd;padding:8px 12px;"
                                f"border-radius:6px;color:#444;font-size:12.5px;white-space:pre-wrap;'>"
                                f"{icon} {_esc(ev.get('content',''))}</div>", unsafe_allow_html=True)
                elif kind == "tool":
                    d = ev.get("data") or {}
                    st.markdown(f"{icon} **调用工具 `{d.get('name')}`**")
                    args = d.get("args")
                    if args:
                        try:
                            st.code(json.dumps(args, ensure_ascii=False, indent=2)
                                    if not isinstance(args, str) else args, language="json")
                        except Exception:
                            st.code(str(args)[:800])
                    if ev.get("content"):
                        st.caption(f"返回：{str(ev['content'])[:240]}")
                elif kind == "guard":
                    st.warning(f"{icon} {ev.get('content','')}")
                elif kind == "error":
                    st.error(f"{icon} {ev.get('content','')}")
            except Exception as e:
                st.caption(f"（轨迹条目渲染跳过：{str(e)[:100]}）")
        finals = [e for e in dicts if e.get("kind") == "final"]
        if finals:
            st.markdown("##### ✅ 最终答案")
            st.success(finals[-1].get("content", ""))


def memory_mb():
    try:
        import psutil
        import os
        return f"{psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024:.0f} MB"
    except Exception:
        return "—"


def resolve_pdf(filename):
    """按 上传目录 → 官方测试 pdf 目录 顺序解析论文原始文件；找不到返回 None。"""
    import os
    try:
        from config import UPLOAD_DIR, PDF_DIR
    except Exception:
        return None
    try:
        for base in (UPLOAD_DIR, PDF_DIR):
            p = str(base / filename)
            if os.path.exists(p):
                return p
    except Exception:
        return None
    return None

"""共享 UI 组件：执行轨迹时间线、溯源卡片、HTML 表格、Agent 头像样式。"""
import json
import streamlit as st


def embed_html(html, height=600, scrolling=True):
    """HTML 嵌入兼容层：优先 components.html，移除后自动降级 st.iframe。"""
    import streamlit as _st
    try:
        import streamlit.components.v1 as _c
        _c.html(html, height=height, scrolling=scrolling)
    except Exception:
        _st.iframe(html, height=height)


def html_table(df):
    css = """<style>.ptable{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;}
.ptable th{background:linear-gradient(135deg,#3b6fb6,#2f5a96);color:#fff;padding:9px 12px;text-align:left;}
.ptable td{padding:8px 12px;border-bottom:1px solid #e8edf3;}
.ptable tr:nth-child(even){background:#f6f9fd;}.ptable tr:hover{background:#eef4fb;}</style>"""
    return css + f'<div style="overflow-x:auto;">{df.to_html(index=False,classes="ptable",border=0,escape=False)}</div>'


def source_card(source, page, text, n=None, snippet_len=520):
    tag = f"【{n}】 " if n else ""
    t = text[:snippet_len].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<div style="background:#f7faff;border-left:4px solid #3b6fb6;border-radius:8px;
padding:12px 16px;margin:8px 0;box-shadow:0 1px 2px rgba(0,0,0,.05);">
<div style="display:flex;justify-content:space-between;margin-bottom:6px;">
<strong style="color:#27518a;font-size:13.5px;">{tag}📄 {source}</strong>
<span style="background:#3b6fb6;color:#fff;padding:1px 10px;border-radius:12px;font-size:11.5px;">第 {page} 页</span></div>
<div style="color:#333;font-size:12.5px;line-height:1.75;white-space:pre-wrap;">{t}…</div></div>"""


_ICON = {
    "plan": "🗂️", "route": "🧭", "thought": "💭", "tool": "🔧",
    "observation": "📥", "final": "✅", "guard": "🛡️", "error": "⚠️",
}


def render_trace(trace, in_expander=False):
    """把 Agent Trace 渲染为带样式的时间线。"""
    body = st.expander("Agent 执行轨迹（规划 / 路由 / 工具 / 思考）", expanded=not in_expander) if in_expander else st.container(border=True)
    with body:
        for ev in trace.to_dicts():
            kind = ev["kind"]
            icon = _ICON.get(kind, "•")
            if kind == "plan":
                st.markdown(f"**{icon} {ev['title']}**")
                for i, s in enumerate(ev["data"].get("steps", []), 1):
                    st.markdown(f"<div style='margin-left:18px;color:#444;font-size:13px;'>{i}. {s}</div>",
                                unsafe_allow_html=True)
            elif kind == "route":
                st.markdown(f"{icon} **{ev['title']}** → `{ev['data'].get('expert','')}`  "
                            f"<span style='color:#888;font-size:12px;'>{ev.get('content','')}</span>",
                            unsafe_allow_html=True)
            elif kind == "thought":
                st.markdown(f"<div style='background:#f4f6fa;border-left:3px solid #9aa7bd;padding:8px 12px;"
                            f"border-radius:6px;color:#444;font-size:12.5px;white-space:pre-wrap;'>{icon} {ev['content']}</div>",
                            unsafe_allow_html=True)
            elif kind == "tool":
                d = ev["data"]
                st.markdown(f"{icon} **调用工具 `{d.get('name')}`**")
                args = d.get("args")
                if args:
                    st.code(json.dumps(args, ensure_ascii=False, indent=2) if not isinstance(args, str)
                            else args, language="json")
                if ev.get("content"):
                    st.caption(f"返回：{ev['content'][:240]}")
            elif kind == "guard":
                st.warning(f"{icon} {ev['content']}")
            elif kind == "error":
                st.error(f"{icon} {ev['content']}")
        # final 单独高亮
        finals = [e for e in trace.to_dicts() if e["kind"] == "final"]
        if finals:
            st.markdown("##### ✅ 最终答案")
            st.success(finals[-1]["content"])


def memory_mb():
    try:
        import psutil
        import os
        return f"{psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024:.0f} MB"
    except Exception:
        return "—"


def safe_render(fn, ctx, what="该功能"):
    """页面级错误边界：捕获异常并优雅降级，避免整页白屏崩溃。"""
    import traceback
    try:
        fn(ctx)
    except Exception as e:
        st.error(f"⚠️ {what}暂时不可用：{str(e)[:200]}")
        st.caption("系统已捕获该异常，其余功能不受影响。可返回其他页面继续演示。")
        with st.expander("🔧 技术细节（供排查）"):
            st.code(traceback.format_exc(limit=6))


def require_library(ctx):
    """空库守卫：文献库为空时给出引导并返回 False。"""
    if not ctx.files:
        st.info("📭 文献库为空。请先在左侧点击「🚀 一键加载演示数据」，或到「📁 文献库管理」上传 PDF。")
        return False
    return True

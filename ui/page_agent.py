"""页面 0：Agent 总助手。对话式驱动完整 Agent（规划→路由→逐步下钻→流式综合），实时渲染执行轨迹。

Round4 Worker8（3% 思考与流式渲染体验）：
* **Token 级流式**：最终答案通过 `core/llm.stream()` 逐 token 吐出，用 `st.empty()` + 累积
  markdown 增量渲染（光标 ▌），不再等全部生成完才显示。
* **思考过程可视化**：可折叠的「🧠 思考过程」区域逐条追加
  第 N 轮 → 当前专家（含命中关键词/可用工具）→ 决定调用的工具及参数 → 工具返回摘要 → 下一步判断，
  每步带状态图标与耗时；子任务计划区实时显示 ⏳/✅/⚠️/❌。
* **错误边界**：工具失败在思考流里显示 ❌ 与失败原因并继续走备选方案；流式中断时兜底展示已生成部分。
"""
import html
import json
import re
import time

import streamlit as st

from core import agent
from ui.common import (render_trace, page_header, ensure_state, friendly_error, empty_state,
                       llm_guard, long_block, step_text, source_card)

# 截断预览里「完整的来源条目」匹配：扁平 JSON 对象且含 ref/file/source 字段
_SRC_ITEM_RE = re.compile(r"\{[^{}]*\"(?:ref|file|source)\"\s*:\s*\"[^\"]+\"[^{}]*\}")

SUGGEST = [
    "🤖 对比 Transformer 和 BERT 的架构承袭关系，用表格",
    "🧮 Transformer 训练用了什么 GPU？BLEU 是多少？",
    "🕸️ 帮我构建引用图谱并找出基石论文",
    "🔍 检索最新的 arXiv cs.AI 论文并推荐有开源代码的",
    "✍️ 根据 Idea 生成论文框架和 Mermaid 架构图",
]

# 顶栏快捷示例的列数（统一列布局：3 列均分）
SUGGEST_COLS = 3
# 历史记录最多回放条数
MAX_HISTORY = 10

# 子任务状态图标（与 core/planner.STATUS_ICON 保持一致，UI 侧独立一份避免循环依赖风险）
_STATUS_ICON = {"pending": "⏳", "running": "⏳", "done": "✅", "skipped": "⚠️", "failed": "❌"}
_STATUS_TEXT = {"pending": "待执行", "running": "进行中", "done": "完成",
                "skipped": "跳过", "failed": "失败"}
_KIND_ICON = {"plan": "🧭", "route": "🧭", "thought": "💭", "tool": "🛠",
              "guard": "🛡", "error": "❌", "final": "✅"}


def _e(s):
    """HTML 转义，避免轨迹内容破坏页面结构。"""
    try:
        return html.escape(str(s if s is not None else ""))
    except Exception:
        return ""


def _plan_block(trace):
    """渲染子任务计划及其状态（⏳/✅/⚠️/❌ + 耗时）。"""
    tasks = getattr(trace, "tasks", None) or []
    if not tasks:
        return
    st.markdown("**① 任务规划 Task Planning**（逐步下钻，状态实时更新）")
    for t in tasks:
        st_ = t.get("status", "pending")
        icon = _STATUS_ICON.get(st_, "⏳")
        ms = int(t.get("elapsed_ms") or 0)
        tail = f" · <span style='color:#888'>{ms}ms</span>" if ms else ""
        tool = f" · 工具 `{_e(t.get('tool'))}`" if t.get("tool") else ""
        note = f" · <span style='color:#888'>{_e(t.get('note'))}</span>" if t.get("note") else ""
        st.markdown(f"<div style='margin-left:10px;font-size:13px;'>{icon} <b>{_e(t.get('id'))}</b> "
                    f"{_e(t.get('title'))}（{_STATUS_TEXT.get(st_, '待执行')}{tool}{tail}）{note}</div>",
                    unsafe_allow_html=True)


def _handoff_block(trace):
    """渲染多智能体路由转交链：哪个专家 → 为什么 → 可用工具。"""
    chain = getattr(trace, "handoff_chain", None) or []
    if not chain:
        return
    st.markdown("**② 多智能体路由 Multi-Agent Routing**")
    for c in chain:
        frm = c.get("from") or "（初始）"
        st.markdown(f"<div style='margin-left:10px;font-size:13px;'>🤖 <code>{_e(frm)}</code> → "
                    f"<code>{_e(c.get('to'))}</code> "
                    f"<span style='color:#888'>{_e(c.get('reason'))}"
                    f"{'｜触发工具：' + _e(c.get('tool')) if c.get('tool') else ''}"
                    f"｜{_e(c.get('ts'))}</span></div>", unsafe_allow_html=True)
    if len(chain) > 1:
        st.caption("转交链：" + " → ".join(str(c.get("to", "")) for c in chain))


def _timeline_block(trace, limit=200):
    """渲染思考时间线：第 N 轮 → 专家 → 工具及参数 → 返回摘要 → 下一步判断。"""
    try:
        evs = trace.to_dicts()
    except Exception:
        return []
    shown = [e for e in evs if e.get("kind") in ("route", "thought", "tool", "guard", "error")]
    st.markdown(f"**③ 思考过程 Reasoning**（{len(shown)} 条，实时追加）")
    for e in shown[-limit:]:
        kind = e.get("kind", "")
        icon = _KIND_ICON.get(kind, "•")
        data = e.get("data") or {}
        extra = ""
        if kind == "tool":
            try:
                import json as _json
                args = data.get("args")
                extra = f"｜参数 `{_e(_json.dumps(args, ensure_ascii=False)[:120])}`"
            except Exception:
                extra = ""
            extra += f"｜{_e(data.get('expert'))}｜⏱{int(data.get('elapsed_ms') or 0)}ms"
        elif kind == "route" and data.get("matched"):
            extra = f"｜命中关键词 {_e(data.get('matched'))}"
        st.markdown(f"<div style='margin-left:10px;font-size:12.5px;color:#333;'>"
                    f"{icon} <span style='color:#888'>{_e(e.get('ts'))}</span> "
                    f"{_e(e.get('content') or e.get('title'))}{extra}</div>",
                    unsafe_allow_html=True)
    return evs


def _render_thinking(ph, trace, streaming=False):
    """把「🧠 思考过程」增量渲染到占位符 ph（每次 live 回调重绘，内容单调增长）。"""
    with ph.container():
        with st.expander("🧠 思考过程（实时）", expanded=True):
            _plan_block(trace)
            _handoff_block(trace)
            _timeline_block(trace)
            if streaming:
                st.caption("✍️ 正在流式生成最终答案…")


def _collect_sources(trace, limit=6):
    """从 Trace 收集真实返回过的来源（ref/file + page + 摘要），双通道：

    通道 1（优先）：trace.final_messages 里 role=="tool" 的完整工具返回
      （agent.run 收尾时挂载，JSON 完整未截断）；
    通道 2（兜底）：Trace 工具事件 content 只是 ~240 字符的截断预览，
      json.loads 必然失败，改用正则只提取其中完整的条目对象。

    修复悬挂【n】角标：系统提示要求答案用【n】标注来源，但从未把编号对照表
    交给用户，页面上只看到「《…》【n】。」这种无从核对的角标。
    """
    seen, out = set(), []

    def _add(ref, page, snippet):
        ref = str(ref or "").strip()
        if not ref:
            return
        k = (ref, page)
        if k in seen:
            return
        seen.add(k)
        out.append({"ref": ref, "page": page, "snippet": str(snippet or "")[:400]})

    def _rows_from_obj(obj):
        """递归找出 JSON 里任何带 ref/file/source 字段的扁平条目。"""
        rows = []

        def walk(v):
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and any(kk in it for kk in ("ref", "file", "source")):
                        rows.append(it)
                    else:
                        walk(it)
            elif isinstance(v, dict):
                for vv in v.values():
                    walk(vv)
        walk(obj)
        return rows

    def _row_ref_page(r):
        ref = r.get("ref") or r.get("file") or r.get("source") or ""
        if r.get("page") in (None, ""):
            page = None
        else:
            try:
                page = int(r.get("page"))
            except (TypeError, ValueError):
                page = None
        snippet = r.get("snippet") or r.get("text") or r.get("title") or ""
        return ref, page, snippet

    # ---------- 通道 1：完整工具返回 ----------
    try:
        msgs = getattr(trace, "final_messages", None) or []
    except Exception:
        msgs = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        raw = m.get("content")
        if not isinstance(raw, str) or not raw.strip().startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        for r in _rows_from_obj(obj):
            ref, page, snippet = _row_ref_page(r)
            _add(ref, page, snippet)
            if len(out) >= limit:
                return out

    # ---------- 通道 2：截断预览的正则兜底 ----------
    if out:
        return out
    try:
        dicts = trace.to_dicts()
    except Exception:
        dicts = []
    for ev in dicts:
        if ev.get("kind") != "tool":
            continue
        raw = ev.get("content")
        if not isinstance(raw, str):
            continue
        for m in _SRC_ITEM_RE.finditer(raw):
            try:
                r = json.loads(m.group(0))
            except Exception:
                continue
            if not isinstance(r, dict):
                continue
            ref, page, snippet = _row_ref_page(r)
            _add(ref, page, snippet)
            if len(out) >= limit:
                return out
    return out


def _render_sources(trace):
    """在最终答案下方渲染「📚 答案来源」（与 ui/page_qa.py 溯源卡片同风格）。

    有页码的来源用 source_card（页码徽章可点）；无页码的（如 list_papers）
    用简化行，避免伪造「第 1 页」。
    """
    srcs = _collect_sources(trace)
    if not srcs:
        return
    st.markdown(f"**📚 答案来源**（{len(srcs)} 条，工具真实返回；可与答案中的【n】角标对照核对）")
    for s in srcs:
        if s["page"]:
            st.markdown(source_card(s["ref"], s["page"], s["snippet"]),
                        unsafe_allow_html=True)
        else:
            st.caption(f"📄 {_e(s['ref'])}｜{_e(s['snippet'][:120])}")


def render(ctx):
    page_header("🤖 Agent 总助手",
                "意图驱动：自动任务规划、多智能体路由、Function Calling 工具闭环，答案带内联引用与溯源。",
                icon="🤖")
    ensure_state({"agent_runs": list})

    cols = st.columns(SUGGEST_COLS)
    goal_pending = None
    for i, s in enumerate(SUGGEST):
        try:
            if cols[i % SUGGEST_COLS].button(s, key=f"sug{i}"):
                # 去掉按钮文案开头的 emoji，只把自然语言任务写进输入框
                goal_pending = s.split(" ", 1)[1] if " " in s else s
        except Exception:
            continue

    # 历史记录
    runs = st.session_state.get("agent_runs") or []
    if not runs:
        empty_state("还没有执行记录。",
                    hints=["点上方示例问题", "或在下方输入框描述你的科研任务"])
    for entry in runs[-MAX_HISTORY:]:
        if not isinstance(entry, dict):
            continue
        st.markdown(f"#### 🙋 {entry.get('goal','')}")
        tr = entry.get("trace")
        finals = []
        try:
            finals = [e for e in tr.to_dicts() if e.get("kind") == "final"]
        except Exception:
            finals = []
        if finals:
            st.success("✅ 最终答案")
            long_block(finals[-1].get("content", ""), title="最终答案全文", limit=200)
            _render_sources(tr)
        else:
            st.info("📭 该次执行未产出最终答案（可能被 Guardrails 拦截或工具调用失败）。")
        with st.expander("🧠 计划 / 路由 / 思考过程", expanded=False):
            try:
                _plan_block(tr)
                _handoff_block(tr)
                _timeline_block(tr)
            except Exception as e:
                st.caption(f"（过程渲染失败：{str(e)[:120]}）")
        with st.expander("🔍 查看 Agent 执行轨迹", expanded=False):
            try:
                render_trace(tr)
            except Exception as e:
                st.caption(f"（轨迹渲染失败：{str(e)[:120]}）")
        st.markdown("---")

    with st.form("agent_form", clear_on_submit=True):
        goal = st.text_input("描述你的科研任务", value=goal_pending or "")
        submitted = st.form_submit_button("🚀 执行")

    if submitted and goal.strip():
        if not llm_guard(ctx, "Agent 执行"):
            return
        think_ph = st.empty()          # 思考过程（增量重绘）
        ans_ph = st.empty()            # 最终答案（Token 级流式）
        buf = {"text": "", "t0": 0.0, "n": 0}

        def live(trace):
            """Agent 每产生一条 Trace 就把思考过程重绘一次（单调增长 = 逐条追加效果）。"""
            try:
                _render_thinking(think_ph, trace, streaming=bool(buf["text"]))
            except Exception:
                pass

        def on_token(tok):
            """Token 级流式：每到一个 token 就累积并增量渲染（带光标）。"""
            if not tok:
                return
            if buf["n"] == 0:
                buf["t0"] = time.time()
            buf["n"] += 1
            buf["text"] += tok
            try:
                ans_ph.markdown(buf["text"] + "▌")
            except Exception:
                pass

        try:
            with st.spinner(step_text("🤖 Agent 正在规划并执行任务")):
                tr = agent.run(ctx, goal.strip(), user=ctx.memory.user,
                               live=live, on_token=on_token)
            # 收尾：去掉流式光标，定格最终文本
            try:
                if buf["text"]:
                    ans_ph.markdown(buf["text"])
            except Exception:
                pass
            # 悬挂【n】角标修复：答案下方给出工具真实返回的来源对照
            _render_sources(tr)
            st.session_state.agent_runs.append({"goal": goal.strip(), "trace": tr})
            try:
                think_ph.empty()
            except Exception:
                pass
            st.rerun()
        except Exception as e:
            # 错误边界：流式中断也要保留已生成的部分
            if buf["text"]:
                st.warning(f"⚠️ 生成被中断（已输出 {buf['n']} 个 token / {len(buf['text'])} 字），"
                           f"以下为已生成部分：")
                st.markdown(buf["text"])
            try:
                think_ph.empty()
            except Exception:
                pass
            friendly_error("Agent 执行", e,
                           reasons=["大模型调用超时 / 额度不足", "工具执行异常（网络或磁盘）"],
                           actions=["重试一次", "换个更简单的任务",
                                    "或加载演示数据后用示例问题演示"])

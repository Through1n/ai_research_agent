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
from services import tasks as task_center
from ui.common import (render_trace, page_header, ensure_state, friendly_error, empty_state,
                       empty_state_card, llm_guard, long_block, step_text, source_card,
                       render_task_panel, is_llm_degraded, llm_degraded_banner)

# 截断预览里「完整的来源条目」匹配：扁平 JSON 对象且含 ref/file/source 字段
_SRC_ITEM_RE = re.compile(r"\{[^{}]*\"(?:ref|file|source)\"\s*:\s*\"[^\"]+\"[^{}]*\}")

# 示例任务：图标 + 短标题 + 一行灰字描述（卡片化）。
# goal 为真实提交给后台的自然语言文本（不含 emoji 前缀），
# 保证提交内容仍是中文自然语言，_submit_goal / task_center.submit 逻辑不变。
SUGGEST = [
    {"title": "对比架构承袭", "desc": "Transformer vs BERT 架构关系，用表格",
     "goal": "对比 Transformer 和 BERT 的架构承袭关系，用表格"},
    {"title": "训练算力与指标", "desc": "用了什么 GPU？BLEU 是多少？",
     "goal": "Transformer 训练用了什么 GPU？BLEU 是多少？"},
    {"title": "引用图谱", "desc": "构建引用图谱并找出基石论文",
     "goal": "帮我构建引用图谱并找出基石论文"},
    {"title": "最新论文检索", "desc": "检索 arXiv cs.AI 并推荐开源代码",
     "goal": "检索最新的 arXiv cs.AI 论文并推荐有开源代码的"},
    {"title": "论文框架生成", "desc": "根据 Idea 出框架 + Mermaid 架构图",
     "goal": "根据 Idea 生成论文框架和 Mermaid 架构图"},
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
        tail = f" · <span style='color:var(--c-text-3)'>{ms}ms</span>" if ms else ""
        tool = f" · 工具 `{_e(t.get('tool'))}`" if t.get("tool") else ""
        note = f" · <span style='color:var(--c-text-3)'>{_e(t.get('note'))}</span>" if t.get("note") else ""
        st.markdown(f"<div style='margin-left:var(--s-2);font-size:var(--fs-body);'>{icon} <b>{_e(t.get('id'))}</b> "
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
        st.markdown(f"<div style='margin-left:var(--s-2);font-size:var(--fs-body);'>🤖 <code>{_e(frm)}</code> → "
                    f"<code>{_e(c.get('to'))}</code> "
                    f"<span style='color:var(--c-text-3)'>{_e(c.get('reason'))}"
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
        st.markdown(f"<div style='margin-left:var(--s-2);font-size:var(--fs-caption);color:var(--c-text);'>"
                    f"{icon} <span style='color:var(--c-text-3)'>{_e(e.get('ts'))}</span> "
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


def _agent_worker(ctx, goal, progress):
    """后台线程内执行 Agent 任务；只允许写 progress 回调，禁止任何 st.* 调用。"""
    step = {"n": 0}
    tokens = {"n": 0}

    def live(trace):
        step["n"] += 1
        progress("执行中 · 第 %d 步（规划 / 路由 / 工具调用）" % step["n"])

    def on_token(tok):
        if not tok:
            return
        tokens["n"] += 1
        if tokens["n"] == 1:
            progress("正在生成最终答案（流式）…")

    progress("① 意图识别与任务规划")
    tr = agent.run(ctx, goal, user=ctx.memory.user, live=live, on_token=on_token)
    # token 数为 0 说明一个字都没吐出来（大模型不可用 / 被拒答），此时不能报「✅ 已完成」
    if tokens["n"]:
        progress("✅ 已完成（%d 步 / %d 个 token）" % (step["n"], tokens["n"]))
    else:
        progress("⚠️ 已结束，但大模型没有输出任何内容（%d 步 / 0 个 token）" % step["n"])
    return tr


def _submit_goal(ctx, goal):
    """一键提交：示例按钮与输入框共用此入口（问题一：此前点击示例只填输入框，
    且填入值活不过一次重绘——按钮在页顶、执行按钮在页底，视觉上毫无反馈）。"""
    goal = (goal or "").strip()
    if not goal:
        return
    if not llm_guard(ctx, "Agent 执行"):
        return
    task_center.submit("Agent：" + goal[:40],
                       lambda progress, _g=goal: _agent_worker(ctx, _g, progress),
                       kind="agent", goal=goal)
    try:
        st.toast("任务已提交后台执行，进度见左侧任务中心，可随时切换其他页面", icon="🚀")
    except Exception:
        pass


def render(ctx):
    page_header("智能总助手（Agent）",
                "一句需求，自动完成规划、路由与工具调用，答案带内联引用与溯源。")
    ensure_state({"agent_runs": list})

    # 新手引导：只在「还没跑过任何任务」时出现，且压缩成一行——详细步骤
    # 已在页面底部空状态卡片里逐条给出，这里不再重复整段说明（首屏减法）。
    if not st.session_state.get("agent_runs"):
        st.caption("三步上手：左侧一键加载演示数据 → 点下方任意示例任务 → 底部输入框提交自己的任务。")
    # 库为空时的针对性提示：检索/图谱类示例在空库下答案会很有限，先引导加载演示数据
    if not getattr(ctx, "files", None):
        st.caption("文献库为空：示例里的检索 / 图谱类任务将返回有限结果，"
                   "建议先点左侧「一键加载演示数据（6 篇）」再试。")

    # --- 示例任务：紧凑卡片（图标 + 短标题 + 一行灰字描述），点击即提交后台执行 ---
    st.markdown("**💡 试试这些示例任务**（点击即后台运行，约 10–30 秒返回带引用答案）")
    cols = st.columns(SUGGEST_COLS)
    for i, s in enumerate(SUGGEST):
        try:
            with cols[i % SUGGEST_COLS]:
                with st.container(border=True):
                    st.markdown(f"**{_e(s['title'])}**")
                    st.caption(s["desc"])
                    if st.button("用此任务 ▶", key=f"sug{i}", use_container_width=True):
                        # 提交的是纯中文自然语言目标（已无 emoji 前缀），后台逻辑不变
                        _submit_goal(ctx, s["goal"])
        except Exception:
            continue
    # 全站能力速览（评委视角：让新用户一屏看懂整个产品能做什么，明确"使用起来怎么样"）
    with st.expander("全站能力速览：这 7 大功能分别能帮你做什么？", expanded=False):
        st.markdown(
            "左侧导航对应 7 个工作页，一句话能力地图：\n\n"
            "| 功能 | 你给它什么 | 它产出什么 |\n"
            "|---|---|---|\n"
            "| 🤖 智能总助手 | 一句科研需求 | 自动规划→路由→调工具，带【n】引用的答案 |\n"
            "| 文献库管理 | PDF | 语义查重 / 扫描件 OCR / 结构解析 / 版本对比 |\n"
            "| 💬 跨文献问答 | 一个问题 | 基于本地库的答案 + 可点开的溯源卡片 |\n"
            "| 📖 版式阅读 | PDF | 双栏原生渲染 / 中英对照 / 划词翻译 / 引用双向跳转 |\n"
            "| 🔍 检索与追踪 | 关键词 / 话题 | arXiv·PubMed·网页检索 + 增量订阅推送 |\n"
            "| 🕸️ 引用图谱 | 文献库 | 引用网络图 + 基石论文 + 一键综述 |\n"
            "| ✍️ 写作辅助 | CSV / 素材 | 自动出图 + 无幻觉论文框架 + 架构图 + 翻译 |\n"
        )

    # 历史记录（卡片化：标题 + 答案摘要 + 来源数；思考/轨迹默认折叠，首屏更清爽）
    runs = st.session_state.get("agent_runs") or []
    if not runs:
        # 教学式空状态（Round 17 UI/UX）：讲清答案会长什么样、点哪里开始，
        # 而不是一句「还没有执行记录」把用户晾在原地。
        empty_state_card(
            "还没有执行记录",
            desc="提交任务后，带【n】引用的答案、思考过程与执行轨迹会出现在这里，可随时回看。",
            actions=["点上方任意示例任务卡片，一键后台运行（约 10–30 秒返回）",
                     "或在下方输入框描述你的科研任务，点「🚀 执行」提交到后台",
                     "实时进度见左侧「任务中心」，运行中可随时切换其他页面"],
            icon="🧭",
        )
    for entry in runs[-MAX_HISTORY:]:
        if not isinstance(entry, dict):
            continue
        goal = entry.get("goal", "")
        tr = entry.get("trace")
        finals = []
        try:
            finals = [e for e in tr.to_dicts() if e.get("kind") == "final"] if tr else []
        except Exception:
            finals = []
        n_src = len(_collect_sources(tr)) if tr else 0
        with st.container(border=True):
            st.markdown(f"**🙋 {_e(goal)}**")
            if finals:
                # 离线占位不能顶着「✅ 已完成」出现（Round 12 ②残留，与 page_qa 同一套判定）
                _ans = finals[-1].get("content", "")
                if is_llm_degraded(_ans):
                    llm_degraded_banner()
                else:
                    st.success("✅ 已完成" + (f" · 📚 {n_src} 条来源" if n_src else ""))
                long_block(_ans, title="最终答案全文", limit=200)
                if n_src:
                    _render_sources(tr)
            else:
                st.info("📭 该次执行未产出最终答案（可能被 Guardrails 拦截或工具调用失败）。")
            # 思考 / 执行轨迹默认折叠：首屏只留「问题 + 答案」，减少信息噪音
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

    with st.form("agent_form", clear_on_submit=True):
        goal = st.text_input("描述你的科研任务，提交后后台执行",
                             key="agent_goal_input",
                             placeholder="例：对比 Transformer 和 BERT 的架构承袭关系，用表格")
        submitted = st.form_submit_button("🚀 执行（后台运行，可继续其他操作）")

    if submitted and goal and goal.strip():
        _submit_goal(ctx, goal)

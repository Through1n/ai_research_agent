"""页面 0：Agent 总助手。对话式驱动完整 Agent（规划→路由→函数调用→溯源答案），实时渲染执行轨迹。"""
import streamlit as st

from core import agent
from ui.common import (render_trace, page_header, ensure_state, friendly_error, empty_state,
                       llm_guard, long_block, step_text)

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
        else:
            st.info("📭 该次执行未产出最终答案（可能被 Guardrails 拦截或工具调用失败）。")
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
        ph = st.empty()

        def live(trace):
            try:
                with ph.container():
                    render_trace(trace)
            except Exception:
                pass

        try:
            with st.spinner(step_text("🤖 Agent 正在规划并执行任务")):
                tr = agent.run(ctx, goal.strip(), user=ctx.memory.user, live=live)
            st.session_state.agent_runs.append({"goal": goal.strip(), "trace": tr})
            try:
                ph.empty()
            except Exception:
                pass
            st.rerun()
        except Exception as e:
            try:
                ph.empty()
            except Exception:
                pass
            friendly_error("Agent 执行", e,
                           reasons=["大模型调用超时 / 额度不足", "工具执行异常（网络或磁盘）"],
                           actions=["重试一次", "换个更简单的任务",
                                    "或加载演示数据后用示例问题演示"])

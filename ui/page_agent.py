"""页面 0：Agent 总助手。对话式驱动完整 Agent（规划→路由→函数调用→溯源答案），实时渲染执行轨迹。"""
import streamlit as st
from core import agent
from ui.common import render_trace


SUGGEST = [
    "对比 Transformer 和 BERT 的架构承袭关系，用表格",
    "Transformer 训练用了什么 GPU？BLEU 是多少？",
    "帮我构建引用图谱并找出基石论文",
    "检索最新的 arXiv cs.AI 论文并推荐有开源代码的",
    "根据 Idea 生成论文框架和 Mermaid 架构图",
]


def render(ctx):
    st.header("🤖 Agent 总助手")
    st.caption("意图驱动：自动任务规划、多智能体路由、Function Calling 工具闭环，答案带内联引用与溯源。")

    if "agent_runs" not in st.session_state:
        st.session_state.agent_runs = []

    cols = st.columns(len(SUGGEST))
    goal_pending = None
    for i, s in enumerate(SUGGEST):
        if cols[i].button(s, key=f"sug{i}"):
            goal_pending = s

    # 历史记录
    for entry in st.session_state.agent_runs:
        st.markdown(f"#### 🙋 {entry['goal']}")
        tr = entry["trace"]
        finals = [e for e in tr.to_dicts() if e["kind"] == "final"]
        if finals:
            st.markdown(finals[-1]["content"])
        with st.expander("🔍 查看 Agent 执行轨迹"):
            render_trace(tr)
        st.markdown("---")

    with st.form("agent_form", clear_on_submit=True):
        goal = st.text_input("描述你的科研任务", value=goal_pending or "")
        submitted = st.form_submit_button("🚀 执行")

    if submitted and goal.strip():
        if not ctx.llm.available:
            st.warning("请先在左侧填入 API Key（或先一键加载演示数据后填 Key）。")
            return
        ph = st.empty()

        def live(trace):
            with ph.container():
                render_trace(trace)

        with st.spinner("Agent 执行中…"):
            tr = agent.run(ctx, goal.strip(), user=ctx.memory.user, live=live)
        st.session_state.agent_runs.append({"goal": goal.strip(), "trace": tr})
        ph.empty()
        st.rerun()

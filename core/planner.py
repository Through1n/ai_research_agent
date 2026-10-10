"""Task Planning：把高层目标动态拆解为有序、可执行的子任务（供 Agent 思考过程可视化）。"""
from core.router import EXPERTS


def fallback_plan(goal, expert):
    label = EXPERTS[expert]["label"]
    return [
        f"由「{label}」接管，明确目标与所需资料：{goal[:40]}",
        "检索/调用相应工具收集带出处的证据（文献片段、外部接口或代码执行）",
        "基于证据进行推理、综合，必要时跨多篇文献关联",
        "生成带内联引用的结果，并做抗幻觉与一致性校验",
    ]


def plan(ctx, goal, expert):
    """返回步骤字符串列表。"""
    if ctx.llm.available:
        tools = ", ".join(EXPERTS[expert]["tools"])
        prompt = f"""你是 Agent 的任务规划器。请把以下目标拆解为 3-6 个有序、可执行的子任务步骤，
每步说明要做什么、可能调用哪些工具。只输出 JSON：{{"plan":["步骤1","步骤2",...]}}
负责专家：{EXPERTS[expert]["label"]}；可用工具：{tools}
用户目标：{goal}"""
        try:
            data = ctx.llm.json_chat([{"role": "user", "content": prompt}], temperature=0.2)
            steps = data.get("plan")
            if steps and isinstance(steps, list):
                return [str(s) for s in steps][:6]
        except Exception:
            pass
    return fallback_plan(goal, expert)

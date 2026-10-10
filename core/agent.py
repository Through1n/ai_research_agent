"""Agent 主循环：意图路由 → 任务规划 → Function Calling 工具闭环 → 抗幻觉综合，全程记录 Trace。"""
import json
import re

from config import AGENT_MAX_ITERS
from core.trace import Trace
from core.router import route, allowed_tools, EXPERTS
from core.planner import plan
from core.guardrails import check_injection, redact_secrets, ACADEMIC_SYSTEM
from tools.definitions import build_registry


def _assistant_dict(msg):
    d = {"role": "assistant", "content": msg.content or ""}
    if getattr(msg, "tool_calls", None):
        d["tool_calls"] = [{
            "id": tc.id, "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        } for tc in msg.tool_calls]
    return d


def _first_param(reg, name):
    sch = reg.get(name).parameters
    req = sch.get("required")
    if req:
        return req[0]
    props = list(sch.get("properties", {}))
    return props[0] if props else "query"


def parse_action(content):
    """ReAct 兜底：从文本解析 动作名 + 参数。"""
    m = re.search(r"action\s*[:：]\s*([a-z_]+)", content, re.I)
    if m:
        name = m.group(1)
        mi = re.search(r"action input\s*[:：]\s*(.*)", content, re.I | re.DOTALL)
        args = {}
        if mi:
            raw = mi.group(1).strip().strip("`")
            try:
                args = json.loads(raw)
            except Exception:
                args = {"__raw": raw}
        return name, args
    for cand in re.findall(r"\{.*\}", content, re.DOTALL):
        try:
            d = json.loads(cand)
            if "action" in d:
                return d["action"], d.get("args", {})
        except Exception:
            pass
    return None, None


def run(ctx, goal, user="user_001", live=None, registry=None):
    trace = Trace(goal, user)

    def emit():
        if live:
            live(trace)

    # 1) 注入检测
    flagged, _ = check_injection(goal)
    if flagged:
        trace.guard("检测到疑似提示注入，已拒绝该指令。")
        trace.final("抱歉，请求中包含试图覆盖系统指令的内容，我不能执行。")
        emit()
        return trace

    # 2) 路由
    expert, reason = route(ctx, goal)
    trace.route(EXPERTS[expert]["label"], reason)
    emit()

    # 3) 规划
    trace.plan(plan(ctx, goal, expert))
    emit()

    if not ctx.llm.available:
        trace.final("当前未配置 API Key，无法驱动 Agent。请在左侧填入 API Key，或使用各功能页的离线演示。")
        emit()
        return trace

    # 4) 工具闭环
    reg = registry or build_registry()
    allowed = set(allowed_tools(expert))
    schemas = [s for s in reg.schemas() if s["function"]["name"] in allowed]
    system = (ACADEMIC_SYSTEM
              + f"\n你当前的角色是「{EXPERTS[expert]['label']}」。需要外部信息或操作时，必须通过 "
                "function calling 调用工具；工具返回内容才是可靠事实，不要臆测。证据充分后再给出"
                "最终中文答案，事实后用【n】标注来源（n 为工具结果中的编号）。")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": goal}]

    final_text = ""
    for _ in range(AGENT_MAX_ITERS):
        msg = ctx.llm.chat(messages, temperature=0.2, tools=schemas)
        tool_calls = getattr(msg, "tool_calls", None)

        if tool_calls:
            messages.append(_assistant_dict(msg))
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                result = reg.execute(name, args, ctx)
                obs = redact_secrets(json.dumps(result, ensure_ascii=False))
                trace.tool(name, args, obs[:180])
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": obs[:3500]})
            emit()
            continue

        content = msg.content or ""
        an, aa = parse_action(content)
        if an and reg.get(an) and an in allowed:
            trace.thought(content[:300])
            if isinstance(aa, dict) and aa.get("__raw") is not None:
                aa = {_first_param(reg, an): aa["__raw"]}
            result = reg.execute(an, aa, ctx)
            obs = redact_secrets(json.dumps(result, ensure_ascii=False))
            trace.tool(an, aa, obs[:180])
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"工具 {an} 返回：\n{obs[:3500]}"})
            emit()
            continue

        final_text = redact_secrets(content)
        break

    if not final_text:
        msg = ctx.llm.chat(messages + [{"role": "user", "content": "请基于以上信息直接给出最终中文答案。"}],
                           temperature=0.2)
        final_text = redact_secrets(msg.content or "")

    trace.final(final_text)
    emit()
    return trace

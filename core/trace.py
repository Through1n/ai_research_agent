"""Agent 执行轨迹（Trace）：记录规划、路由、思考、工具调用、观察与最终答案，供前端可视化。"""
import time
import uuid


class Trace:
    def __init__(self, goal="", user="user_001"):
        self.id = uuid.uuid4().hex[:10]
        self.goal = goal
        self.user = user
        self.events = []
        self.created = time.time()

    def add(self, kind, title="", content="", data=None):
        self.events.append({
            "kind": kind,            # plan/route/thought/tool/observation/final/error/guard
            "title": title,
            "content": content,
            "data": data or {},
            "ts": time.strftime("%H:%M:%S"),
        })
        return self

    def plan(self, steps):
        self.add("plan", "任务规划 Task Planning", "将目标拆解为可执行步骤", {"steps": steps})

    def route(self, expert, reason):
        self.add("route", "多智能体路由 Routing", f"选择专家：{expert}", {"expert": expert, "reason": reason})

    def thought(self, text):
        self.add("thought", "思考 Thought", text)

    def tool(self, name, args, obs_preview=""):
        self.add("tool", f"工具调用 · {name}", obs_preview, {"name": name, "args": args})

    def final(self, text):
        self.add("final", "最终答案 Final Answer", text)

    def guard(self, text):
        self.add("guard", "安全防护 Guardrails", text)

    def error(self, text):
        self.add("error", "异常 Error", text)

    def to_dicts(self):
        return list(self.events)

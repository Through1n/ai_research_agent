"""工具注册中心：把 Python 函数封装为 OpenAI Function 规范，并按名称执行。"""


class Tool:
    def __init__(self, name, description, parameters, func):
        self.name = name
        self.description = description
        self.parameters = parameters          # JSON Schema
        self.func = func

    def openai_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Registry:
    def __init__(self):
        self._tools = {}

    def register(self, name, description, parameters, func):
        self._tools[name] = Tool(name, description, parameters, func)
        return self

    def get(self, name):
        return self._tools.get(name)

    def schemas(self):
        return [t.openai_schema() for t in self._tools.values()]

    def names(self):
        return list(self._tools)

    def execute(self, name, args, ctx):
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"未知工具 {name}"}
        try:
            args = args or {}
            return tool.func(ctx, **args)
        except TypeError as e:
            return {"error": f"参数错误: {e}"}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

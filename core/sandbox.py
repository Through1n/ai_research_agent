"""代码执行护栏：用 AST 静态检查拦截危险 import 与调用，仅放行数据科学白名单。

设计目标（对应赛题 Guardrails 考察点）：
- 静态检查先于执行：违规代码直接拒绝，不进 subprocess；
- 白名单 import：numpy/pandas/matplotlib/plotly/sklearn 等学术常用库；
- 拦截危险面：os.system、subprocess、socket、eval/exec、dunder 属性逃逸等。
"""
import ast

ALLOWED_MODULES = {
    "math", "random", "statistics", "itertools", "functools", "collections",
    "json", "csv", "re", "datetime", "time",
    "numpy", "pandas", "scipy", "sklearn", "matplotlib", "plotly", "seaborn",
}

# 禁止的属性/调用名（典型逃逸路径）
_FORBIDDEN_ATTRS = {"system", "popen", "execv", "spawn", "kill", "remove", "rmtree",
                    "urlopen", "socket", "connect", "startfile"}
_FORBIDDEN_FUNCS = {"eval", "exec", "compile", "__import__", "globals", "locals",
                    "breakpoint", "input", "open"}


class SandboxViolation(Exception):
    pass


def validate_code(code):
    """静态校验代码；违规抛 SandboxViolation（可读中文原因），通过返回 None。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxViolation(f"代码存在语法错误：{e}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root not in ALLOWED_MODULES:
                    raise SandboxViolation(f"不允许导入模块 `{a.name}`（白名单：数据科学与标准安全库）")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_MODULES:
                raise SandboxViolation(f"不允许从 `{node.module}` 导入")
        elif isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_ATTRS or (node.attr.startswith("__") and node.attr.endswith("__")):
                raise SandboxViolation(f"禁止访问 `{node.attr}`（疑似逃逸/系统调用）")
        elif isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name in _FORBIDDEN_FUNCS:
                raise SandboxViolation(f"禁止调用 `{name}()`")
    return None

"""学术写作 Copilot：无幻觉论文框架（引用本地真实文献）、拓扑图脚本、Figure Caption、中译英。"""
import re


def _real_references(ctx):
    lines = []
    for i, n in enumerate(ctx.paper_order, 1):
        p = ctx.papers[n]
        authors = p.get("authors") or "（作者见原文）"
        lines.append(f"[{i}] {authors}. {p.get('title', n)}. {p.get('year','')}.")
    return "\n".join(lines)


def generate_outline(ctx, idea):
    refs = _real_references(ctx)
    ref_rule = (f"Related Work 与 References 只能引用下列本地库中真实存在的文献，按 [n] 编号，禁止编造其它文献；"
                f"若角度不足，用 [待补充真实文献] 占位。\n可用真实文献：\n{refs}") if refs else \
               "当前文献库为空，References 部分一律用 [待补充真实文献] 占位，严禁编造引用。"
    prompt = f"""围绕以下研究 Idea，输出一篇学术论文的完整写作框架，包含各节要点与字数提示：
Title / Abstract（拟写，标注关键结论占位）/ Introduction（动机与贡献点）/ Related Work /
Method（含拟采用的模型/Agent 架构与公式占位）/ Experiments（数据集、指标、基线、消融）/
Threats to Validity / Conclusion / References。
{ref_rule}
Idea：{idea}"""
    return ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.4).content


def diagram(ctx, kind, topic):
    kinds = {
        "mermaid": ("Mermaid flowchart", "```mermaid"),
        "tikz": ("LaTeX TikZ", "```latex"),
        "graphviz": ("Graphviz DOT", "```dot"),
        "matplotlib": ("Python Matplotlib", "```python"),
    }
    name, fence = kinds[kind]
    prompt = f"""请为主题“{topic}”生成可直接运行的 {name} 代码，用于绘制系统/架构拓扑图。
要求节点布局清晰、标注准确；只输出代码，不要解释。"""
    raw = ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.2).content or ""
    raw = raw.replace("```mermaid", "").replace("```latex", "").replace("```dot", "")
    raw = raw.replace("```python", "").replace("```", "").strip()
    return raw


def figure_caption(ctx, df, chart_desc=""):
    summary = df.describe(include="all").to_string()
    prompt = f"""根据以下数据统计与图表信息，用英文撰写一条符合学术规范的 Figure Caption（句式：Figure X. 
It shows/illustrates ...；需点明核心趋势、对比与结论，客观简洁，2-3 句）。
图表类型：{chart_desc}
数据统计：
{summary[:2500]}"""
    return ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.3).content


def translate_to_english(ctx, text):
    prompt = f"""请将以下中文学术内容翻译为符合国际期刊/会议规范的英文：术语准确、句式客观、逻辑严密，
保留数学公式与引用标记；只输出英文译文，不要解释。
内容：
{text}"""
    return ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.2).content


def translate_to_chinese(ctx, text):
    prompt = f"""请将以下英文学术内容精准翻译为中文：保留专业术语（首次出现时可在括号内标注原文）、
公式与引用标记，保持段落结构；只输出中文译文。
内容：
{text}"""
    return ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.2).content

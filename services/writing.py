"""学术写作 Copilot：无幻觉论文框架（引用本地真实文献）、拓扑图脚本、Figure Caption、中译英。"""
import re

from services import graph


# LLM 输出中可能出现的 [n] 引用角标
_CITE_MARK_RE = re.compile(r"\[(\d+)\]")


def _pdf_reference_entries(ctx):
    """优先从当前写作论文的原文 chunks 抽 References 条目。

    复用 graph.ref_section / graph._ref_entries 切分；条目数 < 3 或任何异常时返回 []，
    由调用方 fallback 到本地库元数据，绝不影响写作主链路。
    """
    try:
        filename = getattr(ctx, "current_paper", None)
        if not filename and getattr(ctx, "paper_order", None):
            filename = ctx.paper_order[0]
        if not filename:
            return []
        full_text = "\n".join(
            c.get("text", "") for c in getattr(ctx, "chunks", []) if c.get("source") == filename)
        if len(full_text) < 500:
            return []
        ref_text = graph.ref_section(full_text)
        entries = graph._ref_entries(ref_text)
        entries = [e.strip() for e in entries if isinstance(e, str) and e.strip()]
        return entries if len(entries) >= 3 else []
    except Exception:
        return []


def _real_references(ctx):
    """返回 (真实参考文献白名单文本, 允许出现的 [n] 下标集合)。

    优先用 PDF References 段真实条目（保留原文）；不足时回退本地库元数据拼 [n] 列表。
    """
    pdf_entries = _pdf_reference_entries(ctx)
    if pdf_entries:
        lines = [f"[{i}] {e}" for i, e in enumerate(pdf_entries, 1)]
        return "\n".join(lines), set(range(1, len(lines) + 1))

    lines = []
    for i, n in enumerate(ctx.paper_order, 1):
        p = ctx.papers[n]
        authors = p.get("authors") or "（作者见原文）"
        lines.append(f"[{i}] {authors}. {p.get('title', n)}. {p.get('year','')}.")
    return "\n".join(lines), set(range(1, len(lines) + 1))


def _sanitize_citations(text, whitelist):
    """把不在白名单内的 [n] 角标替换为 [?]。返回 (清洗后文本, 被替换个数)。"""
    if not text:
        return text or "", 0
    replaced = 0

    def _sub(m):
        nonlocal replaced
        if int(m.group(1)) in whitelist:
            return m.group(0)
        replaced += 1
        return "[?]"

    return _CITE_MARK_RE.sub(_sub, text), replaced


def generate_outline(ctx, idea):
    refs, whitelist = _real_references(ctx)
    ref_rule = (f"Related Work 与 References 只能引用下列本地库中真实存在的文献，按 [n] 编号，禁止编造其它文献；"
                f"若角度不足，用 [待补充真实文献] 占位。\n可用真实文献：\n{refs}") if refs else \
               "当前文献库为空，References 部分一律用 [待补充真实文献] 占位，严禁编造引用。"
    prompt = f"""围绕以下研究 Idea，输出一篇学术论文的完整写作框架，包含各节要点与字数提示：
Title / Abstract（拟写，标注关键结论占位）/ Introduction（动机与贡献点）/ Related Work /
Method（含拟采用的模型/Agent 架构与公式占位）/ Experiments（数据集、指标、基线、消融）/
Threats to Validity / Conclusion / References。
{ref_rule}
Idea：{idea}"""
    raw = ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.4).content or ""
    text, k = _sanitize_citations(raw, whitelist)
    if k:
        try:
            import streamlit as st
            st.warning(f"检测到 {k} 个不在真实文献列表内的角标，已自动替换为 [?]")
        except Exception:
            pass
    return text


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
公式与引用标记，保持段落结构；只输出中文译文，不要解释。
内容：
{text}"""
    return ctx.llm.chat([{"role": "user", "content": prompt}], temperature=0.2).content

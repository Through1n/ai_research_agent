# -*- coding: utf-8 -*-
"""团队信息占位符校验器（交付前门禁用，可重复运行）。

用法：
    python scripts/_check_placeholders.py

功能：
    扫描 README.md 与 资料/团队信息.md，定位三类"待填"占位符并逐条输出
    「文件:行号 | 占位符原文 | 所属章节 › 行内标签」：
      1. 【占位符】        —— README 团队信息一节使用的中文占位 token；
      2. TODO             —— 行内 TODO 标记（README:342 的提交前提醒注释）；
      3. 方括号占位       —— 资料/团队信息.md 的 [待填写] / [姓名] / [学校] / [组别] /
                             [成员 N] / [____-__-__] 等待填方括号组。

退出码（供 CI / 交付前门禁使用）：
    0 —— 两个文件中均未发现待填占位符（可交付）；
    1 —— 仍存在待填占位符（未通过，按输出逐行填写）；
    2 —— 目标文件缺失或读取失败（环境错误，非填写问题）。

白名单原则（重要）：
    只豁免"有意保留的格式示范文字"，绝不放任真实待填项。命中白名单的括号组
    不计入占位符，但会在汇总里报告豁免数量，保证透明可审计。具体豁免规则
    见下方 WHITELIST 系列常量注释：[ ] 空括号（复选框/元引用）、Markdown
    链接与图片文字、[示例] 示例标记、XX大学类校名模板、example.com 类保留域、
    纯数值列表。[待填写] / [姓名] / [学校] / [组别] / [成员 N] / [____-__-__]
    等真实待填项一律拦截。

配套文档：资料/占位符填空清单.md（怎么填）、资料/复盘/round15_team_info.md（实测报告）。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "README.md",
    ROOT / "资料" / "团队信息.md",
]

# ---- 三类待填占位符模式 ----------------------------------------------------
CN_PLACEHOLDER_RE = re.compile(r"【占位符】_*")   # 中文占位 token（输出含尾随下划线，便于对照原文）
TODO_RE = re.compile(r"\bTODO\b")              # 行内 TODO 标记
BRACKET_RE = re.compile(r"\[[^\[\]]*\]")       # 方括号组（不允许嵌套，逐个匹配）

# ---- 白名单 -----------------------------------------------------------------
# 白名单 A：方括号"内容文本"命中即豁免（附豁免理由，供汇总审计）。
WHITELIST_TEXT_PATTERNS = [
    # ① 填空清单统一示例标记：[示例] / [示例：...] 明确声明"格式示范，非真实信息"。
    #    用户按 资料/占位符填空清单.md 填写时若保留该前缀，属有意保留。
    (re.compile(r"^示例"), "示例标记（[示例] 前缀 = 格式示范声明）"),
    # ② 校名/院系/单位格式示范：XX大学、XX学院 是填空清单"填写模板"一节的模板写法，
    #    不是忘填的占位符。
    (re.compile(r"^X{2,}(大学|学院|学校|中学|专业|公司|单位|实验室)"), "校名/单位格式示范（XX大学 模板写法）"),
    # ③ IANA 保留示范域名：example.com/.org/.net/.edu/.cn 等保留域永不指向真实主体，
    #    专门用于邮箱/网址格式说明；your-domain.com 为同类示范串。
    (re.compile(r"(^|[^A-Za-z0-9])example\.(com|org|net|edu|cn)\b"), "IANA 保留示范域名（example.com 等）"),
    (re.compile(r"your-domain\.com"), "示范域名占位串（your-domain.com）"),
]

# 白名单 B：方括号"内容"整体是纯数值/标点（技术参数列表，如 [1.5, 3.0, 4.5]）。
# 注意字符集不含下划线，因此 [____-__-__] 日期占位不会被误豁免。
NUMERIC_CONTENT_RE = re.compile(r"^[\d.,\s;:()%-]+$")

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def clean_text(s):
    """去掉 Markdown 强调/代码标记，便于提取人类可读标签。"""
    return s.replace("**", "").replace("`", "").strip()


def bracket_exempt_reason(bracket_text, line, end_idx):
    """判断一个方括号组是否属于白名单；命中返回理由字符串，否则返回 None。"""
    inner = bracket_text[1:-1]
    # A1. 空括号：Markdown 任务列表复选框（- [ ]）或文中对占位符写法的元引用
    #     （如团队信息.md:4 / :81 的 `[ ]`），均非待填占位。
    if not inner.strip():
        return "空括号（Markdown 复选框 / 对占位写法的元引用）"
    # A2. Markdown 链接/图片文字：] 后紧跟 ( ，如 [文字](url) / ![图注](img)，
    #     方括号内是链接文字或图注，不是占位符（README:5 / :366）。
    if line[end_idx:].lstrip().startswith("("):
        return "Markdown 链接/图片文字（] 后紧跟 ( ）"
    # A3. 内容文本命中白名单模式（示例标记 / 校名模板 / 保留示范域名）。
    for pat, reason in WHITELIST_TEXT_PATTERNS:
        if pat.search(inner):
            return reason
    # A4. 纯数值/标点列表（技术参数，如 README:190 的退避时间 [1.5, 3.0, 4.5]）。
    if NUMERIC_CONTENT_RE.fullmatch(inner):
        return "纯数值/标点列表（技术参数）"
    return None


def row_label(cells):
    """表格行内取第一个"有信息量"的单元格作为行标签。

    跳过：空单元格、纯数字单元格（行号列）、自身含占位符的单元格。
    """
    for c in cells:
        t = clean_text(c)
        if not t or t.isdigit():
            continue
        if CN_PLACEHOLDER_RE.search(c) or TODO_RE.search(c) or BRACKET_RE.search(c):
            continue
        return t
    return ""


def line_label(line, match_start):
    """生成行内上下文标签：表格行取行首有效单元格，其余行取占位符前的文字。"""
    s = line.strip()
    if s.startswith("|"):
        cells = s.strip("|").split("|")
        return row_label(cells)
    head = clean_text(s[:match_start]).lstrip(" \t->*<!").strip("：:，,。.、·-— ")
    if not head:
        # 占位符前没有可读文字（如 HTML 注释行）：退回整行摘要
        head = clean_text(s)[:60]
    return head


def scan_file(path):
    """扫描单个文件，返回 (findings, exempt_count, section_counts)。

    findings: [(lineno, kind, text, context)]，按行内出现顺序。
    """
    rel = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8")
    findings = []
    exempt_count = 0
    section = ""
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = HEADING_RE.match(line)
        if m:
            section = clean_text(m.group(2))
        # TODO 与【占位符】直接逐字匹配；方括号组需过白名单
        for kind, regex in (("【占位符】", CN_PLACEHOLDER_RE), ("TODO", TODO_RE)):
            for mo in regex.finditer(line):
                label = line_label(line, mo.start())
                ctx = f"{section} › {label}" if label else section
                findings.append((lineno, kind, mo.group(0), ctx))
        for mo in BRACKET_RE.finditer(line):
            if bracket_exempt_reason(mo.group(0), line, mo.end()) is None:
                label = line_label(line, mo.start())
                ctx = f"{section} › {label}" if label else section
                findings.append((lineno, "方括号占位", mo.group(0), ctx))
            else:
                exempt_count += 1
    return rel, findings, exempt_count


def main():
    # Windows 控制台/管道默认 locale 编码可能无法输出「👥 › ❌」等字符，统一转 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 68)
    print("占位符校验 · python scripts/_check_placeholders.py")
    print("目标: README.md, 资料/团队信息.md")
    print("=" * 68)

    total = 0
    failed = False
    for path in TARGETS:
        if not path.is_file():
            print(f"[错误] 文件不存在: {path}", file=sys.stderr)
            return 2
        rel, findings, exempt = scan_file(path)
        by_kind = {}
        for _, kind, _, _ in findings:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        total += len(findings)
        status = "❌ 有占位符" if findings else "✅ 已填完"
        print(f"\n[{rel}]  {status}（待填 {len(findings)} 处；白名单豁免 {exempt} 处）")
        print(f"  【占位符】  {by_kind.get('【占位符】', 0):>3} 处")
        print(f"  TODO       {by_kind.get('TODO', 0):>3} 处")
        print(f"  方括号占位 {by_kind.get('方括号占位', 0):>3} 处")
        for lineno, kind, ptext, ctx in findings:
            print(f"  {rel}:{lineno} | {ptext} | {ctx}")
        if findings:
            failed = True

    print("\n" + "-" * 68)
    if failed:
        print(f"合计: {total} 处待填占位符")
        print("结果: ❌ 未通过 —— 请按 资料/占位符填空清单.md 逐项填写")
        print("提示: 填完后重跑本脚本，退出码为 0 方可进入交付前门禁")
        return 1
    print(f"合计: {total} 处待填占位符")
    print("结果: ✅ 通过 —— 未发现待填占位符")
    return 0


if __name__ == "__main__":
    sys.exit(main())

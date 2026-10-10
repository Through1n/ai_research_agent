"""Guardrails 安全防护：注入检测、密钥脱敏、引用校验、学术范围与输出约束。"""
import re

# 常见 prompt 注入特征
_INJECTION_PATTERNS = [
    r"ignore (all |the )?previous instructions",
    r"disregard (all |your )?(prior|previous)",
    r"you are now (a|an) ",
    r"forget everything",
    r"system prompt",
    r"忽略(以上|之前|前面).{0,4}指令",
    r"忽略(所有)?(之前|前面)的?提示",
]

# 密钥 / token 特征
_SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9\-_]{16,}", "sk-***"),
    (r"\b[0-9a-fA-F]{32}\.[0-9a-zA-Z]{8,}\b", "***ZHIPU_KEY***"),
    (r"(?i)(api[_-]?key\s*[=:]\s*[\"']?)[A-Za-z0-9\-_\.]{16,}", r"\1***"),
    (r"(?i)(bearer\s+)[A-Za-z0-9\-\._]{16,}", r"\1***"),
]

CITATION_RE = re.compile(r"【(\d+)】|\[(\d+)\]")


def check_injection(text):
    """返回 (是否疑似注入, 命中的规则描述)。"""
    low = text.lower()
    for p in _INJECTION_PATTERNS:
        if re.search(p, low):
            return True, p
    return False, ""


def redact_secrets(text):
    """在展示/入库前对密钥脱敏。"""
    if not text:
        return text
    out = text
    for pat, rep in _SECRET_PATTERNS:
        out = re.sub(pat, rep, out)
    return out


def validate_citations(answer, n_sources):
    """校验答案中的引用角标是否都指向有效来源，返回问题列表（空列表表示通过）。"""
    issues = []
    nums = set()
    for m in CITATION_RE.finditer(answer):
        nums.add(int(m.group(1) or m.group(2)))
    invalid = sorted(n for n in nums if n < 1 or n > n_sources)
    if invalid:
        issues.append(f"引用角标 {invalid} 超出有效来源范围(1~{n_sources})")
    if n_sources > 0 and not nums:
        issues.append("答案未包含任何引用角标，存在无依据生成风险")
    return issues


# 统一的学术系统提示片段（强调确定性、可溯源、零幻觉）
ACADEMIC_SYSTEM = (
    "你是一名严谨的科研助手 Agent。请遵守：\n"
    "1) 只能依据提供的文献片段作答，禁止编造数据、参数、引用；\n"
    "2) 每个事实结论后用【n】标注其来源编号，n 对应当前给出的资料编号；\n"
    "3) 若资料不足以回答，必须明确说“根据已提供文献无法确定”，不得臆测；\n"
    "4) 对比类问题用中文 Markdown 表格，保留专业术语原文；\n"
    "5) 输出客观、简洁、符合学术规范。"
)

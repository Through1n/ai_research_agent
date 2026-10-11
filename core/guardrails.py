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
# 跨文献对比模式的分论文角标，形如 【P1-2】/ [P2-3]
GROUP_CITATION_RE = re.compile(r"【P(\d+)-(\d+)】|\[P(\d+)-(\d+)\]")

# 内容一致性阈值：答案句与来源文本的锚点 n-gram Jaccard 低于此值判为"可疑引用"
SUSPICION_THRESHOLD = 0.15

# 拒答闸门统一文案（services/rag.py 与 ui/page_qa.py 共用，改一处即可）
REFUSAL_TEXT = "未在文献库中找到可靠依据，为避免幻觉我不作答。"

# 锚点 token：只取英文词 / 数字（中英混排文档里真正可跨语言比对的"硬词"）。
# 不取汉字：答案是中文、来源是英文，汉字参与只会稀释覆盖率；纯中文句因锚点数 <2 会被跳过。
_ANCHOR_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-\.]*")


def _anchor_tokens(text, n=2):
    """抽取可跨语言比对的锚点 n-gram 集合（极简词干：去复数 s）。"""
    toks = []
    for t in _ANCHOR_TOKEN.findall((text or "").lower()):
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        toks.append(t)
        if any(ch.isdigit() for ch in t):
            # 数字单独再放一份：兼容 "110M"/"110 million"、"beta_1"/"beta 1" 这类写法差异
            toks.extend(re.findall(r"\d+(?:\.\d+)?", t))
    return {tuple(toks[i:i + n]) for i in range(max(len(toks) - n + 1, 0))}


def _overlap(sentence, source_text):
    """支持度：答案句锚点 n-gram 被来源文本"覆盖"的比例（uni/bi 各占一半，0~1）。

    刻意不用 Jaccard：来源 chunk 通常 1200 字，来源侧 n-gram 数量是答案句的几十倍，
    Jaccard 会被稀释到接近 0，导致**正常引用也被判为可疑**。覆盖率才是"这句话
    有多少硬词能在原文找到"的正确度量。
    """
    su, ou = _anchor_tokens(sentence, 1), _anchor_tokens(source_text, 1)
    if not su:
        return 0.0
    sb, ob = _anchor_tokens(sentence, 2), _anchor_tokens(source_text, 2)
    uni = len(su & ou) / len(su)
    bi = (len(sb & ob) / len(sb)) if sb else uni
    return 0.5 * uni + 0.5 * bi


def citation_sentences(answer, pattern=None):
    """把答案按句切开，返回 [(角标元组, 去掉角标后的句子)]。"""
    pat = pattern or CITATION_RE
    out = []
    for sent in re.split(r"[。；;\n]", answer or ""):
        marks = []
        for m in pat.finditer(sent):
            if pattern is None:
                marks.append(int(m.group(1) or m.group(2)))
            else:
                marks.append(f"P{m.group(1) or m.group(3)}-{m.group(2) or m.group(4)}")
        if marks:
            out.append((marks, pat.sub(" ", sent)))
    return out


def check_citation_support(answer, source_texts, threshold=SUSPICION_THRESHOLD):
    """内容一致性校验：每个被引用的【n】，检查其所在句与第 n 个来源文本的词汇重叠。

    只比对"锚点词"（英文术语/数字/汉字 n-gram），避免中英跨语言导致的全量误杀；
    答案句锚点词少于 2 个时无法判定，直接跳过（宁可漏报，不误报）。
    """
    susp = []
    ctxs = {}
    for marks, body in citation_sentences(answer):
        for n in marks:
            ctxs.setdefault(n, "")
            ctxs[n] += " " + body
    for n in sorted(ctxs, key=lambda x: str(x)):
        if not isinstance(n, int) or n < 1 or n > len(source_texts):
            continue
        sent, src = ctxs[n].strip(), source_texts[n - 1] or ""
        if len(_anchor_tokens(sent)) < 2:
            continue
        ov = _overlap(sent, src)
        if ov < threshold:
            susp.append(f"可疑引用【{n}】：答案句与来源文本词汇重叠仅 {ov:.0%}"
                        f"（阈值 {threshold:.0%}），可能脱离原文")
    return susp


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


def validate_citations(answer, n_sources, source_texts=None, threshold=SUSPICION_THRESHOLD):
    """校验答案引用，返回问题列表（空列表表示通过）。

    两层校验：
    1) 角标范围校验（原行为，向后兼容 n_sources 两参调用）；
    2) 内容一致性校验（新增）：传入 source_texts 时，对每个被引用的【n】检查其
       所在句与第 n 个来源文本的锚点 n-gram Jaccard，低于 threshold 判为"可疑引用"。
    """
    issues = []
    nums = set()
    for m in CITATION_RE.finditer(answer or ""):
        nums.add(int(m.group(1) or m.group(2)))
    invalid = sorted(n for n in nums if n < 1 or n > n_sources)
    if invalid:
        issues.append(f"引用角标 {invalid} 超出有效来源范围(1~{n_sources})")
    if n_sources > 0 and not nums:
        issues.append("答案未包含任何引用角标，存在无依据生成风险")
    if source_texts:
        try:
            issues.extend(check_citation_support(answer, list(source_texts), threshold))
        except Exception as e:  # 校验本身绝不阻断主流程
            issues.append(f"内容一致性校验异常已跳过：{type(e).__name__}")
    return issues


def validate_grouped_citations(answer, index, threshold=SUSPICION_THRESHOLD):
    """跨文献对比模式的引用校验：index 为 {角标: {"source","page","para_idx","text"}}。

    校验【P1-2】型角标是否存在于给定来源，并做与 validate_citations 相同的内容一致性校验。
    """
    issues = []
    seen = set()
    for m in GROUP_CITATION_RE.finditer(answer or ""):
        tag = f"P{m.group(1) or m.group(3)}-{m.group(2) or m.group(4)}"
        seen.add(tag)
        if tag not in index:
            issues.append(f"引用角标【{tag}】不存在于给定来源")
    if index and not seen:
        issues.append("答案未包含任何【Pn-m】引用角标，存在无依据生成风险")
    susp = set()
    for marks, body in citation_sentences(answer, GROUP_CITATION_RE):
        if len(_anchor_tokens(body)) < 2:
            continue
        for tag in marks:
            src = (index.get(tag) or {}).get("text", "")
            ov = _overlap(body, src)
            if ov < threshold:
                susp.add(f"可疑引用【{tag}】：答案句与来源文本词汇重叠仅 {ov:.0%}"
                         f"（阈值 {threshold:.0%}），可能脱离原文")
    issues.extend(sorted(susp))
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

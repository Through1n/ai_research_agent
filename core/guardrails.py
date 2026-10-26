"""Guardrails 安全防护：注入检测、密钥脱敏、引用校验、学术范围与输出约束。"""
import math
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

# 引用角标：【n】/ [n]，兼容模型实际输出过的 "n6" 前缀写法（实测 glm-4-flash 会混用）
CITATION_RE = re.compile(r"【[nN]?(\d+)】|\[[nN]?(\d+)\]")
# 跨文献对比模式的分论文角标，形如 【P1-2】/ [P2-3]（兼容小写 p）
GROUP_CITATION_RE = re.compile(r"【[pP](\d+)-(\d+)】|\[[pP](\d+)-(\d+)\]")

# 内容一致性阈值（覆盖率口径，非 Jaccard 相似度；口径见 _overlap 的说明）
SUSPICION_THRESHOLD = 0.15

# 越界角标重定位阈值：答案句与某来源的锚点覆盖率达到该值，才把越界角标
# 重新指向该来源（低于此值宁可标注"未在来源中验证"，不硬指）
RECOVER_THRESHOLD = 0.30

# 引用净化后插入答案的未验证标记
UNVERIFIED_MARK = "（未在来源中验证）"

# 数字/实体核验时不计入的上下文（页码、章节号等版面信息，不是事实主张）
_FACT_STRIP = re.compile(
    r"第\s*\d+\s*[页章节段讲]|图\s*\d+|表\s*\d+|式\s*\d+|算法\s*\d+|Appendix|appendix")

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
       所在句与第 n 个来源文本的锚点 n-gram 覆盖率，低于 threshold 判为"可疑引用"。
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


# ---------------- 数字 / 实体核验（抗幻觉：答案中的数值必须能在来源中找到） ----------------

# 英文数词 → 数值（"eight GPUs" 与答案 "8 个 GPU" 要能对上）
_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}
# 量级词：既当后缀（65M / 6500 万），也当独立数词（sixty five million）
_SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
                "万": 1e4, "亿": 1e8}
# 数字后缀量级（65M → 6.5e7，与来源 "65 million" 归一后相等）
_SUFFIX_SCALE = {"k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9,
                 "million": 1e6, "billion": 1e9, "万": 1e4, "亿": 1e8}
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "两": 2}

# 数字串（先去千分位逗号）：65 / 28.4 / 100000 / 10^-9 取 10 与 9
_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(k|m|bn|b|million|billion|万|亿)?",
                     re.IGNORECASE)
# 英文数词（可含量级词）
_NUM_WORD_RE = re.compile(r"(?<![a-z])([a-z]+)(?:[ -](thousand|million|billion|trillion))?")
# 中文数字串
_CN_NUM_RE = re.compile(r"[零一二三四五六七八九十百千万亿]+")
# 含数字的实体编码：P100 / V100 / GPT-4 / A100 …（须含大写字母，避免把 beta_1 这类
# 普通术语误当实体编码；小写拼写多在来源中以同款大小写出现，大小写不敏感比对即可）
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,8}[-_ ]?\d{1,5}[A-Za-z0-9]*)(?![A-Za-z0-9])")


def _is_code_token(code):
    return any(ch.isupper() for ch in code)


def _cn_number(s):
    """中文数字串 → 数值（六千五百万 → 6.5e7；十二 → 12；八 → 8）。"""
    total, section, number = 0.0, 0.0, 0.0
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch == "十":
            section += (number or 1) * 10
            number = 0
        elif ch == "百":
            section += (number or 1) * 100
            number = 0
        elif ch == "千":
            section += (number or 1) * 1000
            number = 0
        elif ch in ("万", "亿"):
            total += (section + number) * _SCALE_WORDS[ch]
            section = number = 0
    return total + section + number


def _extract_numbers(text):
    """抽取文本中的全部数值（含量级归一）：来源 "65 million" → {65, 6.5e7}；
    答案 "6500 万" → {6500, 6.5e7}，两者因此可以互相验证。"""
    t = re.sub(r"(?<=\d),(?=\d)", "", text or "")          # 去千分位逗号
    t = _FACT_STRIP.sub(" ", t)                            # 去页码/章节等版面信息
    t = CITATION_RE.sub(" ", t)                            # 去引用角标（角标不是事实）
    t = GROUP_CITATION_RE.sub(" ", t)
    out = set()

    def _add(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        if v == v and abs(v) != float("inf"):
            out.add(v)

    for m in _NUM_RE.finditer(t):
        raw, suf = m.group(1), (m.group(2) or "").lower()
        _add(raw)
        if suf:
            _add(float(raw) * _SUFFIX_SCALE[suf])
    low = t.lower()
    for m in _NUM_WORD_RE.finditer(low):
        w, scale = m.group(1), m.group(2)
        if w in _NUM_WORDS:
            v = float(_NUM_WORDS[w])
            _add(v)
            if scale:
                _add(v * _SCALE_WORDS[scale])
    for m in _CN_NUM_RE.finditer(t):
        v = _cn_number(m.group(0))
        if v:
            _add(v)
    return out


def find_unverified_numbers(answer, source_texts):
    """答案中在全部来源里找不到对应数值的数字（按量级归一后比对）。

    返回可读列表，如 ["6500 万", "2031"]；为空表示答案数值均有来源依据。
    单位换算（65 million ↔ 6500 万 ↔ 65M ↔ 0.65 亿）与中英数词
    （eight ↔ 8 ↔ 八）均已归一，避免误报。
    """
    src_vals = set()
    for t in source_texts or []:
        src_vals |= _extract_numbers(t)
    if not src_vals:
        return []
    bad = []
    t = re.sub(r"(?<=\d),(?=\d)", "", answer or "")
    t = _FACT_STRIP.sub(" ", t)
    t = CITATION_RE.sub(" ", t)
    t = GROUP_CITATION_RE.sub(" ", t)
    seen = set()
    for m in _NUM_RE.finditer(t):
        raw, suf = m.group(1), (m.group(2) or "").lower()
        vals = [float(raw)] + ([float(raw) * _SUFFIX_SCALE[suf]] if suf else [])
        if any(any(math.isclose(v, s, rel_tol=1e-9) for s in src_vals) for v in vals):
            continue
        shown = raw + (suf if suf else "")
        if shown not in seen:
            seen.add(shown)
            bad.append(shown)
    low = t.lower()
    for m in _NUM_WORD_RE.finditer(low):
        w, scale = m.group(1), m.group(2)
        if w not in _NUM_WORDS:
            continue
        v = float(_NUM_WORDS[w])
        vals = [v] + ([v * _SCALE_WORDS[scale]] if scale else [])
        if any(any(math.isclose(x, s, rel_tol=1e-9) for s in src_vals) for x in vals):
            continue
        shown = m.group(0)
        if shown not in seen:
            seen.add(shown)
            bad.append(shown)
    for m in _CN_NUM_RE.finditer(t):
        s = m.group(0)
        if not any(ch in _CN_DIGITS for ch in s):
            continue        # 纯量词（百万/亿/万）是单位不是数值主张，跳过避免误报
        v = _cn_number(s)
        if not v or any(math.isclose(v, x, rel_tol=1e-9) for x in src_vals):
            continue
        shown = s
        if shown not in seen:
            seen.add(shown)
            bad.append(shown)
    return bad


def find_unverified_codes(answer, source_texts):
    """答案中含数字的实体编码（P100/V100/GPT-4…）在来源中不存在的列表。"""
    blob = " ".join(t or "" for t in source_texts or []).lower()
    if not blob.strip():
        return []
    bad = []
    for m in _CODE_RE.finditer(answer or ""):
        code = m.group(1)
        if not _is_code_token(code):
            continue
        if code.lower() in blob:
            continue
        if code not in bad:
            bad.append(code)
    return bad


# ---------------- 引用净化（越界重定位 + 连续重编号） ----------------

def out_of_range_citations(answer, n_sources):
    """答案中超出 1~n_sources 范围的角标列表（升序去重）。"""
    return sorted({int(m.group(1) or m.group(2))
                   for m in CITATION_RE.finditer(answer or "")
                   if not (1 <= int(m.group(1) or m.group(2)) <= n_sources)})


def _recover_citation(sentence, source_texts, threshold=RECOVER_THRESHOLD):
    """越界角标重定位：在该句与各来源的锚点覆盖率里找最佳来源。

    只有最佳覆盖率 >= threshold 才返回其 1-based 编号；否则返回 None
    （宁可标注"未在来源中验证"，也不把角标硬指到一个不包含该事实的来源）。
    """
    if not source_texts or len(_anchor_tokens(sentence)) < 2:
        return None
    best, best_i = 0.0, None
    for i, t in enumerate(source_texts, 1):
        ov = _overlap(sentence, t or "")
        if ov > best:
            best, best_i = ov, i
    return best_i if best >= threshold else None


def sanitize_citations(answer, n_sources, source_texts=None):
    """把答案角标整理为 1..k 连续且与来源一一对应。返回 (clean, cited_idx, notes)。

    - cited_idx：0-based 来源下标列表，clean 中的【i】一一对应 cited_idx[i-1]；
    - 越界角标：先按内容重定位到真实来源（_recover_citation），找不到依据的
      替换为「（未在来源中验证）」并记入 notes；
    - 幸存角标按文中首次出现顺序重编号，杜绝"引用编号 > sources 数"的展示事故。
    """
    text = answer or ""
    texts = [t or "" for t in (source_texts or [])]
    notes = []
    # 0) 清掉模型偶尔输出的裸【n】占位（无数字，不是有效角标，留着只会困扰用户）
    text = re.sub(r"【[nN]】|\[[nN]\]", "", text)
    # 1) 逐句处理越界角标（按句切片，保留原始分隔符）
    segs = re.split(r"([。；;\n])", text)
    fixed = []
    for i in range(0, len(segs), 2):
        sent = segs[i]
        sep = segs[i + 1] if i + 1 < len(segs) else ""
        marks = list(CITATION_RE.finditer(sent))
        if not marks:
            fixed.append(sent + sep)
            continue
        body = CITATION_RE.sub(" ", sent)
        pieces, last = [], 0
        for m in marks:
            n = int(m.group(1) or m.group(2))
            pieces.append(sent[last:m.start()])
            if 1 <= n <= n_sources:
                pieces.append(m.group(0))
            else:
                style = "【】" if m.group(1) else "[]"
                rec = _recover_citation(body, texts)
                if rec is not None:
                    pieces.append(f"【{rec}】" if style == "【】" else f"[{rec}]")
                    notes.append(f"引用整理：角标【{n}】超出来源范围(1~{n_sources})，"
                                 f"已按内容重定位到来源【{rec}】")
                else:
                    pieces.append(UNVERIFIED_MARK)
                    notes.append(f"引用整理：角标【{n}】超出来源范围(1~{n_sources})，"
                                 f"且该句无法在来源中验证，已标注「{UNVERIFIED_MARK}」")
            last = m.end()
        pieces.append(sent[last:])
        fixed.append("".join(pieces) + sep)
    staged = "".join(fixed)
    # 2) 幸存角标按首次出现顺序重编号为 1..k
    order, mapping = [], {}

    def _renum(m):
        n = int(m.group(1) or m.group(2))
        if n not in mapping:
            mapping[n] = len(order) + 1
            order.append(n)
        return f"【{mapping[n]}】" if m.group(1) else f"[{mapping[n]}]"

    clean = CITATION_RE.sub(_renum, staged)
    cited = [n - 1 for n in order if 1 <= n <= n_sources]
    return clean, cited, notes


def sanitize_grouped_citations(answer, index):
    """对比模式净化：不存在的【Pn-m】角标与裸数字角标都替换为未验证标记。

    返回 (clean, notes)。分组角标无法重定位（P 编号对应具体论文），越界即标注。
    """
    notes = []

    def _sub_group(m):
        tag = f"P{m.group(1) or m.group(3)}-{m.group(2) or m.group(4)}"
        if tag in index:
            return m.group(0)
        notes.append(f"引用整理：角标【{tag}】不存在于给定来源，已标注「{UNVERIFIED_MARK}」")
        return UNVERIFIED_MARK

    def _sub_plain(m):
        n = m.group(1) or m.group(2)
        notes.append(f"引用整理：对比模式出现裸数字角标【{n}】，无法对应到具体论文片段，"
                     f"已标注「{UNVERIFIED_MARK}」")
        return UNVERIFIED_MARK

    clean = re.sub(r"【[nN]】|\[[nN]\]", "", answer or "")   # 裸【n】占位清理
    clean = GROUP_CITATION_RE.sub(_sub_group, clean)
    clean = CITATION_RE.sub(_sub_plain, clean)
    return clean, notes


# 统一的学术系统提示片段（强调确定性、可溯源、零幻觉）
ACADEMIC_SYSTEM = (
    "你是一名严谨的科研助手 Agent。请遵守：\n"
    "1) 只能依据提供的文献片段作答，禁止编造数据、参数、引用；\n"
    "2) 每个事实结论后用【n】标注其来源编号，n 对应当前给出的资料编号；\n"
    "3) 若资料不足以回答，必须明确说“根据已提供文献无法确定”，不得臆测；\n"
    "4) 对比类问题用中文 Markdown 表格，保留专业术语原文；\n"
    "5) 输出客观、简洁、符合学术规范；\n"
    "6) 参考资料中若出现同一文献的多个版本（来源文件名带 v1/v7 等版本号，或 arXiv 编号相同），以版本号最新者的内容为准——旧版本中与最新版冲突的数值已过时，禁止采用。\n"
    "7) **禁止自相矛盾**：一旦已依据资料给出确定答案（如“β2 是 0.98”），不得再补充"
    "“资料未提供/无法确定/未提及”等相反表述——自相矛盾比答错更严重；同一结论在多个来源"
    "（含同一文献的不同版本）中一致时只陈述一次，不逐来源重复、不追加否定补充句。\n"
)


# =====================================================================
# Round 13：抗幻觉增强（确定性、无 LLM 依赖；append-only，不改动上方被
# 资料/technical_doc.md 引用行号的代码，避免文档行号漂移）。
# =====================================================================

def _char_ngrams(text, n=2):
    """仅中文的字符 n-gram 集合（跨语言场景：中文答案句 vs 中文来源也能判定支持度）。

    英文来源返回空集——此时 _overlap 退化回 token 锚点口径，与原行为一致，不引入回归。
    """
    s = "".join(ch for ch in (text or "") if "\u4e00" <= ch <= "\u9fff")
    s = re.sub(r"\s+", "", s)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def citation_coverage(answer, source_texts, threshold=SUSPICION_THRESHOLD):
    """逐处【n】引用的"锚点覆盖率"（0~1）+ 总体指标。

    用于 answer() 生成后判定"模型引用的来源是否真的包含该句主张"——覆盖率=0 即
    "引用来源与主张零词面重合"，是强幻觉信号（AGREE 的 claims-not-supported）。
    返回 {"coverage": {n: score}, "mean": float, "any_zero": bool, "unsupported": [n...]}。
    """
    cov, ctxs = {}, {}
    for marks, body in citation_sentences(answer):
        for n in marks:
            if isinstance(n, int):
                ctxs.setdefault(n, "")
                ctxs[n] += " " + body
    for n in sorted(ctxs):
        if not (1 <= n <= len(source_texts)):
            cov[n] = 0.0
            continue
        sent, src = ctxs[n].strip(), source_texts[n - 1] or ""
        if len(_anchor_tokens(sent)) < 2 and not _char_ngrams(sent):
            cov[n] = 1.0            # 无锚点可判，按支持处理（宁可漏报，不误报）
            continue
        cov[n] = round(_overlap(sent, src), 4)
    mean = (sum(cov.values()) / len(cov)) if cov else 1.0
    return {"coverage": cov, "mean": round(mean, 4),
            "any_zero": any(v <= 0.0 for v in cov.values()),
            "unsupported": sorted(n for n, v in cov.items() if 0 < v < threshold)}


def grade_retrieval(query, source_texts, source_ids=None):
    """CRAG 式"知识条"相关性评分（确定性，无 LLM）：对每个候选片段算 query↔chunk 的
    词面覆盖，返回 (scores_by_id, mean, max)。

    answer() 的拒答闸门可借此区分"仅单一来源高亮、整体无关"与"整体相关"两种情形。
    复用 query 与 chunk 的中英锚点覆盖率，避免引入外部模型依赖。source_ids 为 None
    时按 0-based 下标作为 key。
    """
    q = (query or "").lower()
    q_tokens = set(re.findall(r"[a-z0-9][a-z0-9\-\.]*", q))
    scores = {}
    for i, t in enumerate(source_texts or []):
        cid = (source_ids[i] if source_ids else i)
        toks = set(re.findall(r"[a-z0-9][a-z0-9\-\.]*", (t or "").lower()))
        if not q_tokens:
            scores[cid] = 0.0
            continue
        hit = len(q_tokens & toks) / len(q_tokens)
        scores[cid] = round(min(max(hit, 0.0), 1.0), 4)
    vals = list(scores.values()) or [0.0]
    return scores, round(sum(vals) / len(vals), 4), round(max(vals), 4)

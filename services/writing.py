"""学术写作 Copilot：无幻觉论文框架、拓扑图真实渲染、Figure Caption、中英学术翻译。

本轮整改对应 ``docs/audit_report_20261010.md`` 第五节 E 路（5.1-5.6）：
- **5.1 无幻觉 References**：``extract_references_from_pdf`` 用 PyMuPDF 从 PDF 末尾真实抽取
  References 段（复用 ``services.parsing.split_reference_entries``），生成后用
  ``validate_reference_whitelist`` 做角标 + 标题双重正则校验，幻觉在 UI 上显式暴露；
- **5.2 TikZ**：``try_compile_tikz`` 检测 tectonic/pdflatex/xelatex，编译 PDF→PNG 真实渲染，
  无编译器时优雅降级（源码 + 在线编辑器链接），编译失败展示日志前 500 字；
- **5.3 Graphviz**：``render_dot`` 三级降级（dot -Tsvg → networkx+matplotlib → 零依赖
  DOT→SVG），最终兜底展示源码；
- **5.4 CSV 自动出图**：``infer_chart_plan`` 按列类型自动推荐 1-3 个图表；
- **5.5 Figure Caption**：``chart_facts`` 从 df 实测极值/趋势/Top-N/相关系数注入 prompt，
  生成后校验是否含真实数字，不含则强化重生成一次；
- **5.6 中译英**：保持原有能力，LLM 调用统一入口。

统一 LLM 入口：本模块所有调用都走 :func:`core.llm.call_llm_with_retry`
（core/llm.py:129，只读不改），并透传 ctx.llm 的 api_key/base_url/model，
保证侧边栏选择的 Provider 依然生效（见 ``_llm_text``）。

R6 修复（References 占位废稿）：
- 生成流程改为「LLM 只写正文 + 系统**确定性拼接** References」，References 章节
  逐条照抄 :func:`reference_whitelist` 抽出的真实文献，不经过大模型，根除
  ``[1..40] (List the references…)`` 这类占位输出，同时省掉一次大的 LLM 输出；
- :func:`sanitize_placeholders` / :func:`scan_placeholders` 做统一的占位串检测与清洗，
  ``[Placeholder]`` / ``TODO`` / ``[1..N]`` 等命中后要么修复要么上报 UI；
- :func:`validate_reference_whitelist` 补上「References 章节缺失/条目数不足」判定——旧版
  在这种情形下会解析出 0 条并直接判 PASS（占位废稿被当成无幻觉放行）。

R9 修复（流式 + 失败降级）：
- :func:`outline_body_stream` 流式吐正文（UI 用 ``st.write_stream`` 消费），首 chunk
  实测 <1s，用户不必再对着 40s 的空白转圈等整篇；
- :func:`finalize_outline` 把「剔模型 References → 清洗占位 → 角标打码 → 确定性拼接
  References → 白名单校验」抽成共用后处理，流式/非流式两条路径共用同一套保证；
- LLM 中途失败（正文混入 ``【LLM 暂时不可用…】`` 伪响应）时：剔除伪响应、**保留已生成
  部分**并置 ``llm_error/partial`` 标记，由 UI 明确告知，绝不白屏也不把伪响应当正文。
"""
import os
import re
import io
import time
import shutil
import difflib
import hashlib
import tempfile
import subprocess
from collections import OrderedDict

from services import graph
from core.llm import call_llm_with_retry

try:                                  # pandas 是项目核心依赖，缺失时相关功能降级而非崩溃
    import pandas as pd
except Exception:                     # pragma: no cover
    pd = None


# ---------------------------------------------------------------- 可调常量（本模块自持）
LLM_TEMPERATURE = 0.3                 # 默认温度
TITLE_SIM_THRESHOLD = 0.8             # References 标题实质匹配阈值（归一化后相似度）
MIN_REF_ENTRIES = 3                   # PDF 抽取条目少于该值则视为失败，回退库元数据
MIN_QUALITY_ENTRIES = 5               # 质量自检：页数 >10 的 PDF 抽出的条目少于该值判为 low_confidence
MIN_ENTRY_CHARS = 30                  # 条目最短长度，短于此多为页码/题注碎片
PDF_TAIL_CHARS = 24000                # 参考资料区拼接的字符上限（跨多页时使用）
REF_SCAN_PAGES = 150                  # 从末尾向前最多扫描多少页找 References 标题
REF_MAX_PAGES = 40                    # References 区最多向后拼接多少页
MAX_REF_ENTRIES = 400                 # 白名单最大条目数
MAX_AUTO_CHARTS = 3                   # CSV 自动出图推荐数量上限
CAT_CARDINALITY_MAX = 15              # 类别列基数上限（超过视为高基数文本列）
TIKZ_TIMEOUT_SEC = 90                 # LaTeX 编译超时
TIKZ_LOG_SNIPPET = 4000               # 编译日志保留长度（UI 展示前 500 字）
DOT_TIMEOUT_SEC = 30                  # dot -Tsvg 超时
LATEX_ENGINES = ("tectonic", "pdflatex", "xelatex", "lualatex")
EMPTY_LIBRARY_HINT = ("⚠️ 文献库为空，无法生成「无幻觉 References」。"
                      "请先到「📁 文献库管理」上传 PDF（或在左侧点击「🚀 一键加载演示数据」），"
                      "系统会从上传论文正文末尾的 References 段真实抽取条目作为引用白名单；"
                      "在文献库为空时本模块不会生成任何占位/编造引用。")
NO_MODEL_OUTPUT_HINT = ("⚠️ 大模型未返回内容（可能是 API Key 未配置或网络异常），"
                        "已中止生成，不输出任何引用。")
# 大纲正文的篇幅上限（词数）：越长越慢，现场演示需要与质量平衡
OUTLINE_TARGET_WORDS = 900
# References 章节标题（确定性拼接用）
REFERENCES_HEADING = "# References"

# LLM 输出中可能出现的 [n] 引用角标
_CITE_MARK_RE = re.compile(r"\[(\d+)\]")
# PDF 正文中的 References 段标题锚点
_REF_HEADING_RE = re.compile(
    r"(?im)^\s*(?:\d+\s*[.)]?\s*)?(?:references|bibliography|works\s+cited|reference\s+list|"
    r"参考文献|参考书目)\s*:?\s*$")
# References 段之后出现这些标题时立即截断（附录/致谢/补充材料不是文献）
_REF_STOP_HEAD_RE = re.compile(
    r"(?im)^\s*(?:[A-Z]\d{0,2}\.?\s*)?(?:appendix|acknowledg\w*|author\s+contributions?|"
    r"supplementary\s+material|about\s+the\s+authors?|further\s+details|broader\s+impact|"
    r"index|索引|附录|致谢|作者介绍|补充材料)\b")
# 生成文本（Markdown 大纲）里的 References 段标题
_GEN_REF_HEADING_RE = re.compile(
    r"(?im)^\s*#{0,6}\s*\*{0,2}(?:references|bibliography|works\s+cited|参考文献)\*{0,2}\s*:?\s*$")
_YEAR_RE = re.compile(r"\((19|20)\d{2}[a-z]?\)")
_YEAR_LOOSE_RE = re.compile(r"\b(19|20)\d{2}[a-z]?\b")
_NUM_RE = re.compile(r"\d")
# caption 开头的 "Figure 1." / "Fig. 2a:" / "图 3"——其中数字不算"真实数据数字"
_FIG_PREFIX_RE = re.compile(
    r"^\s*(?:\*{0,2}(?:figure|fig\.?|图)\*{0,2}\s*[A-Za-z]?\s*\d+(?:[.\-]\d+)?[A-Za-z]?\s*[.::：]?\s*)+",
    re.IGNORECASE)


# ---------------------------------------------------------------- 占位串检测与修复（R6-T2）
# LLM 常见的「偷懒占位」写法：把该生成的内容写成一句指令或占位符。
# 命中这些模式说明**这一段根本没生成**，必须修复或显式告知用户，绝不能静默输出。
_PLACEHOLDER_RULES = (
    ("占位符标签", re.compile(r"\[\s*placeholder\s*\]", re.IGNORECASE)),
    ("占位符标签", re.compile(r"\{\s*placeholder\s*\}", re.IGNORECASE)),
    ("占位符标签", re.compile(r"<\s*placeholder\s*>", re.IGNORECASE)),
    ("待办标记", re.compile(r"\b(?:TODO|TBD|FIXME)\b")),            # 大小写敏感：避免误伤普通词
    ("待办标记", re.compile(r"\bXXX{2,}\b")),
    ("未生成指令", re.compile(
        r"\(\s*list (?:all )?the references[^)\n]{0,160}\)", re.IGNORECASE)),
    ("未生成指令", re.compile(
        r"\(\s*(?:insert|add|fill in|complete|provide)[^)\n]{0,80}"
        r"(?:references?|citations?|here|later)[^)\n]{0,80}\)", re.IGNORECASE)),
    ("未生成指令", re.compile(r"\bto be (?:added|filled|completed|written)\b", re.IGNORECASE)),
    ("中文占位符", re.compile(r"(?:待补(?:充|写)?|占位符|此处省略|以下略|略去)")),
)
# 编号区间占位符 ``[1..40]`` / ``[1-40]``：区间跨度 >= 5 才判定（避免误伤合法的 [1, 3]）
_NUM_RANGE_RE = re.compile(r"\[\s*(\d{1,4})\s*(\.\.|…|–|—|-)\s*(\d{1,4})\s*\]")
_NUM_RANGE_MIN_SPAN = 5


def scan_placeholders(text):
    """扫描生成文本里的占位串，返回 ``[{"rule","fragment","line","line_no"}]``。

    用于 T2 的「生成后统一检测」：命中即说明该片段没有被真正生成。
    """
    issues = []
    try:
        lines = str(text or "").splitlines()
        for ln, line in enumerate(lines, 1):
            hits = []
            for name, rule in _PLACEHOLDER_RULES:
                for m in rule.finditer(line):
                    hits.append((name, m.group(0)))
            for m in _NUM_RANGE_RE.finditer(line):
                a, b = int(m.group(1)), int(m.group(3))
                if b - a >= _NUM_RANGE_MIN_SPAN:
                    hits.append(("编号区间占位符", m.group(0)))
            for name, frag in hits:
                issues.append({"rule": name, "fragment": frag.strip()[:120],
                               "line": line.strip()[:200], "line_no": ln})
    except Exception:
        return issues
    return issues


def sanitize_placeholders(text):
    """移除占位串，返回 ``(清洗后文本, 命中的 issue 列表)``。

    策略（保守优先）：
    1. 逐行删除命中的片段，保留该行其余真实内容；
    2. 若一行删完之后什么都不剩 → 整行丢弃（这行本来就是占位行）；
    3. 原有空行保留，不破坏 Markdown 结构。
    """
    issues = []
    src_lines = str(text or "").splitlines()
    out_lines = []
    try:
        for ln, line in enumerate(src_lines, 1):
            cleaned, hits = line, []
            for name, rule in _PLACEHOLDER_RULES:
                for m in list(rule.finditer(cleaned)):
                    hits.append((name, m.group(0)))
                    cleaned = cleaned[:m.start()] + " " + cleaned[m.end():]
            for m in list(_NUM_RANGE_RE.finditer(cleaned)):
                a, b = int(m.group(1)), int(m.group(3))
                if b - a >= _NUM_RANGE_MIN_SPAN:
                    hits.append(("编号区间占位符", m.group(0)))
                    cleaned = cleaned[:m.start()] + " " + cleaned[m.end():]
            if hits:
                cleaned = re.sub(r"\s{2,}", " ", cleaned)
                cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)   # 删占位符后残留的 " ."
                cleaned = cleaned.strip(" \t-–—:：;；*")
                for name, frag in hits:
                    issues.append({"rule": name, "fragment": frag.strip()[:120],
                                   "line": line.strip()[:200], "line_no": ln})
                if not cleaned:                 # 占位行整行丢弃
                    continue
            elif not line.strip():
                out_lines.append(line)          # 原有空行：保持 Markdown 段落结构
                continue
            out_lines.append(cleaned)
    except Exception:
        return str(text or ""), scan_placeholders(text)
    return "\n".join(out_lines), issues


def build_references_section(refs_list, heading=REFERENCES_HEADING):
    """**确定性**拼接 References 章节（不经过 LLM），逐条照抄白名单里的真实文献。

    真实文献的作者/标题/年份/venue 都在白名单里，让 LLM 再"写"一遍只会有编造风险
    （实测 glm-4-flash 会把整节写成 ``[1..40] (List the references...)`` 占位指令），
    因此这里用字符串拼接：编号即白名单下标，条目文本与注入 prompt 的完全一致，
    既 100% 无幻觉，又省掉一次 LLM 输出。
    """
    lines = [heading, ""]
    try:
        for i, r in enumerate(refs_list or [], 1):
            lines.append(_ref_line(i, r))
    except Exception:
        return heading
    return "\n".join(lines)


def _strip_generated_references(text):
    """删掉 LLM 自己写的 References 章节（含标题行之后的全部内容）。

    References 由 :func:`build_references_section` 确定性拼接，必须先清掉模型版本，
    否则会出现两份 References 或把占位行留在末尾。
    """
    try:
        hits = list(_GEN_REF_HEADING_RE.finditer(text or ""))
        if not hits:
            return str(text or ""), False
        head = str(text or "")[:hits[-1].start()].rstrip()
        return head, True
    except Exception:
        return str(text or ""), False


# ---------------------------------------------------------------- 统一 LLM 入口（T6）
def _llm_kwargs(ctx):
    """从 ctx.llm 取出 provider 配置，供 call_llm_with_retry 构造临时客户端。"""
    try:
        llm = getattr(ctx, "llm", None)
        return {
            "api_key": getattr(llm, "api_key", "") or None,
            "base_url": getattr(llm, "base_url", None),
            "model": getattr(llm, "model", None),
        }
    except Exception:
        return {"api_key": None, "base_url": None, "model": None}


def llm_available(ctx):
    """LLM 是否可用（供 UI 判断，避免用裸属性访问触发异常）。"""
    try:
        return bool(getattr(getattr(ctx, "llm", None), "available", False))
    except Exception:
        return False


def _llm_text(ctx, prompt_or_messages, temperature=LLM_TEMPERATURE):
    """统一 LLM 文本入口：所有调用都经 core.llm.call_llm_with_retry（带重试 + 失败兜底）。

    ``prompt_or_messages`` 可传字符串或 OpenAI 风格 messages。返回纯文本；
    调用失败时返回空字符串（由调用方给出中文友好提示，绝不抛异常）。
    """
    messages = prompt_or_messages if isinstance(prompt_or_messages, list) \
        else [{"role": "user", "content": str(prompt_or_messages)}]
    try:
        kw = _llm_kwargs(ctx)
        resp = call_llm_with_retry(messages, temperature=temperature, **kw)
        content = ""
        try:
            content = resp.choices[0].message.content or ""
        except Exception:
            content = ""
        content = (content or "").strip()
        # call_llm_with_retry 在全部重试失败时返回伪响应（core/llm.py:78），此处识别为空，
        # 由调用方给出中文降级提示，绝不把"【LLM 暂时不可用…】"当成正文展示给用户
        if content.startswith("【LLM 暂时不可用"):
            return ""
        return content
    except Exception:
        return ""


# ---------------------------------------------------------------- 文本归一 / 相似度
def _norm_title(text):
    """标题归一化：小写、去标点、合并空白，用于实质匹配。"""
    try:
        t = str(text or "").lower()
        t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t)
        return re.sub(r"\s+", " ", t).strip()
    except Exception:
        return ""


def _title_similarity(a, b):
    """两个标题的实质相似度：完全相等/包含/前缀截断 > 序列匹配比，取较大值（0~1）。"""
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 8 and shorter in longer:
        return 1.0                      # 一方完整出现在另一方里（引文常见缩略）
    if len(shorter) >= 4 and longer.startswith(shorter + " "):
        return 0.9                      # 截断式引用（如 "BERT" ⊂ "BERT pre training of..."）
    try:
        return difflib.SequenceMatcher(None, na, nb).ratio()
    except Exception:
        return 0.0


def _ref_line(i, r):
    """把一条结构化文献渲染为给 LLM 看的 ``[i] authors. title. venue, year.``。"""
    try:
        # 各字段自带句点时不再补第二个句点（避免出现 "et al.. Title."）
        parts = [str(r.get("authors") or "（作者见原文）").strip().rstrip("."),
                 str(r.get("title") or "").strip().rstrip("."),
                 str(r.get("venue") or "").strip().rstrip(".")]
        line = ". ".join(p for p in parts if p.strip())
        if r.get("year"):
            line += f", {r['year']}."
        elif line and not line.endswith("."):
            line += "."
        return f"[{i}] {line}".strip()
    except Exception:
        return f"[{i}] {r}"


# ---------------------------------------------------------------- T1：PDF References 真实抽取
def _split_segments(text):
    """按句切分，但**不在人名首字母缩写后切**（避免 "Tom B. Brown" 被切成两段）。"""
    segs, start = [], 0
    try:
        for m in re.finditer(r"(?<=[.?!])\s+", text):
            prev = text[:m.start()]
            tok = re.search(r"([A-Za-z\u4e00-\u9fff]+)[.?!]$", prev)
            if tok and len(tok.group(1)) == 1:      # 单字母缩写（人名首字母 / et al.）
                continue
            segs.append(text[start:m.start()])
            start = m.end()
        segs.append(text[start:])
    except Exception:
        return [text]
    return [s.strip(" .") for s in segs if s.strip(" .")]


def _ligatures(s):
    """还原 PDF 里常见的连字，避免 "ﬁne-tuning" 之类的标题无法与真值比对。"""
    for a, b in (("\ufb01", "fi"), ("\ufb02", "fl"), ("\ufb00", "ff"),
                 ("\ufb03", "ffi"), ("\ufb04", "ffl"), ("\u017f", "s")):
        s = s.replace(a, b)
    return s


def _strip_numbering(text):
    """去掉 ``[12]`` / ``12.`` / ``12)`` 形式的条首编号。"""
    s = re.sub(r"^\[\d{1,4}\]\s*", "", str(text or "").strip())
    s = re.sub(r"^\(?\d{1,4}\)[.]?\s+", "", s)
    s = re.sub(r"^\d{1,4}[.)]\s+", "", s)
    return s.strip(" .")


def _flow_text(text):
    """把参考文献区文本压成单行流：还原跨行连字符、合并换行与多余空白。"""
    s = _ligatures(str(text or ""))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[-\u2010\u2011]\s*\n\s*", "", s)      # "correspon-\ndence" → "correspondence"
    s = re.sub(r"[ \t]*\n[ \t]*", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _sentence_ends(text, lo=0, hi=None):
    """返回 [lo, hi) 内**下一句起点**的位置列表。

    与 ``_split_segments`` 一致：**不在单字母缩写（人名首字母、"et al."）后断句**，
    否则 "Tom B. Brown" 会被切成两段，导致 authors/title/venue 错位。
    """
    hi = len(text) if hi is None else hi
    starts = []
    for m in re.finditer(r"(?<=[.?!])\s+", text[lo:hi]):
        pos = lo + m.start()
        prev = re.search(r"([A-Za-z\u4e00-\u9fff]+)[.?!]$", text[:pos])
        if prev and len(prev.group(1)) == 1:
            continue
        if re.search(r"(?i)\b(?:et|al|ed|eds|fig|ref|no|vol|pp|ibid|e\.g|i\.e|approx)\.$",
                     text[:pos]):
            continue
        starts.append(lo + m.end())
    return starts


def _split_sentences(text):
    """按 ``_sentence_ends`` 切句，返回非空句段列表。"""
    if not text:
        return []
    bounds = [0] + [e for e in _sentence_ends(text)] + [len(text)]
    out = []
    for i in range(len(bounds) - 1):
        seg = text[bounds[i]:bounds[i + 1]].strip(" .")
        if seg:
            out.append(seg)
    return out


# venue/噪声判别词典：用于从一段文本里认出「这是期刊/会议名而不是作者名」
_NAME_PARTICLES = {"van", "von", "de", "del", "della", "der", "den", "di", "da", "dos",
                   "bin", "ibn", "la", "le", "ter", "ten", "st", "and", "&", "et", "al"}
# venue 提示词必须**按整词**匹配：子串匹配会把 "Kevin Clark"（含 "in "）误判成 venue
_VENUE_HINT_RE = re.compile(
    r"\b(?:in|proceedings?|proc|journal|transactions?|conference|workshop|symposium|"
    r"international|annual|meeting|advances?|springer|ieee|acm|elsevier|press|university|"
    r"department|institute|laborator\w*|college|school|thesis|technical|report|retrieved|"
    r"chapter|figure|table|appendix|pages?|vol|arxiv|corr|doi|http|lecture|notes|nature|"
    r"science|communications|inc|publishers?|editors?|proceeedings)\b", re.IGNORECASE)


def _looks_like_author_block(s, require_separator=False):
    """判断一段文本是否像「作者名列表」（用于定位无编号条目的起点）。"""
    t = str(s or "").strip().rstrip(".").strip()
    if not t or len(t) > 220:
        return False
    low = t.lower()
    if _VENUE_HINT_RE.search(low):
        return False
    if re.search(r"\d", t):                       # 含数字基本是 venue/日期而非姓名
        return False
    if require_separator and "," not in t and " and " not in low:
        return False
    toks = [x for x in re.split(r"[,\s]+", t) if x]
    if not toks or len(toks) > 26:
        return False
    for tok in toks:
        core = tok.strip(".")
        if core.lower() in _NAME_PARTICLES:
            continue
        # 只要求「词首字母大写」：PDF 里常见组合变音符（如 "Lo¨ıc"）会让严格正则误判
        if not re.match(r"^[A-Z\u00c0-\u00de\u0100-\u024f]", core):
            return False
    return True


def _authorblock_start(text, year_pos, window=460):
    """给定年份位置，向前回找作者块的起点（用于切分无编号的作者-年份式条目）。"""
    lo = max(0, year_pos - window)
    # 年份紧跟句点时会算出一个「句起点 == 年份位置」的空边界，先丢掉这些空片段
    starts = [s for s in _sentence_ends(text, lo, year_pos) if lo < s < year_pos]
    bounds = [lo] + starts + [year_pos]
    if len(bounds) < 2:
        return lo
    # 紧邻年份的最后一句一定是作者块尾部（单作者 "Wilson L Taylor. 1953." 也成立）
    start = bounds[-2]
    for i in range(len(bounds) - 3, -1, -1):
        seg = text[bounds[i]:bounds[i + 1]].strip()
        if _looks_like_author_block(seg, require_separator=True):
            start = bounds[i]
        else:
            break
    return max(lo, start)


# 年份必须独立成 token：排除 arXiv:1808.04444 / 6(Nov):1817–1853 / pages 1914–1925 / CVPR09
_YEAR_TOKEN_RE = re.compile(r"(?<![\w.:/\u2013\u2014-])((?:19|20)\d{2}[a-z]?)(?![\w./-])")
# "作者. 2018. 标题" 流式切分用的年份锚点：年份紧跟在句点之后（Springer/ACM 常见版式）
_YEAR_ANCHOR_RE = re.compile(r"(?<=[.]\s)((?:19|20)\d{2}[a-z]?)\.(?=\s|$)")


def _split_author_year_flow(ref_text):
    """切分「无编号 + 作者-年份」版式的参考文献区（ACL/arXiv 论文常见）。

    典型原文：``Authors. Year. Title. In Proceedings of VENUE, ...``，条目之间既没有编号
    也没有空行，只能用「下一个条目的作者块在哪」来断句。
    """
    text = _flow_text(ref_text)
    if len(text) < 40:
        return []
    anchors = []
    for m in _YEAR_ANCHOR_RE.finditer(text):
        y = int(m.group(1)[:4])
        if 1900 <= y <= 2100:
            anchors.append(m.start(1))
    if len(anchors) < 2:
        return []
    starts = []
    for i, pos in enumerate(anchors):
        starts.append(0 if i == 0 else _authorblock_start(text, pos))
    out = []
    for i, pos in enumerate(anchors):
        end = starts[i + 1] if i + 1 < len(anchors) else len(text)
        piece = text[starts[i]:end].strip(" .;,")
        if piece:
            out.append(piece)
    return out


def _parse_entry_fields(idx, raw):
    """把一条参考文献字符串结构化为 {id, authors, title, year, venue, raw}。

    按 ``Authors. Year. Title. Venue.`` / ``Authors (Year). Title. Venue.`` /
    ``Authors. Title. In Proceedings of VENUE, Year.`` 三类常见版式分别处理，
    避免旧实现「authors/title/venue 三个字段错位」的问题。
    """
    text = _flow_text(_strip_numbering(raw))
    # PDF 双栏排版会把 citation count / 页码混到条目末尾（"..., 2016. 10."），它会以句段身份
    # 参与切分并顶掉真实年份，导致条目显示缺年份 → 先剥掉这层噪声
    text = _TAIL_PAGE_NOISE_RE.sub(".", text)
    year = ""
    authors, title, venue = "", "", ""

    # ① "Authors (Year). Title. Venue."
    m = re.match(r"^(?P<a>.+?)[.,]?\s*\(\s*(?P<y>(?:19|20)\d{2}[a-z]?)\s*\)\s*[.,]?\s*(?P<rest>.+)$",
                 text)
    if m and 4 <= len(m.group("a")) <= 240:
        authors, year, rest = m.group("a"), m.group("y"), m.group("rest")
    else:
        # ② "Authors. Year. Title. Venue."（ACL/arXiv 版式，BERT 论文就是这种）
        m = re.match(r"^(?P<a>.+?)\.\s+(?P<y>(?:19|20)\d{2}[a-z]?)\.\s*(?P<rest>.+)$", text)
        if m and 4 <= len(m.group("a")) <= 240:
            authors, year, rest = m.group("a"), m.group("y"), m.group("rest")
        else:
            # ③ "Authors. Title. In Proceedings of VENUE, Year." 兜底
            segs = _split_sentences(text)
            authors = segs[0] if segs else text
            rest = ". ".join(segs[1:]) if len(segs) > 1 else ""

    authors = _ligatures(authors).strip(" .,")
    authors = re.sub(r"^\d{1,4}\s+", "", authors)     # 双栏书籍里混进来的页码/citation 计数
    authors = re.sub(r"\s*;\s*", ", ", authors)       # "Devlin; Chang" → "Devlin, Chang"

    # 从 rest 里再分 title / venue
    title, venue = _split_title_venue(rest)
    if not year:
        ys = [x for x in _YEAR_TOKEN_RE.findall(rest)]
        if ys:
            year = ys[-1]
            venue = re.sub(r"[.,;:]?\s*(?:19|20)\d{2}[a-z]?\s*[.]?\s*$", "", venue).strip(" .,")

    return {
        "id": int(idx),
        "authors": authors,
        "title": title.strip(" .,"),
        "year": year,
        "venue": venue.strip(" .,"),
        "raw": text,
    }


# PDF 双栏排版会把 citation count / 页码混到条目末尾（"..., 2016. 10."），见 _parse_entry_fields
_TAIL_PAGE_NOISE_RE = re.compile(r"[.,;:]?\s+\d{1,3}\s*[.,;:]?\s*$")
# 一眼能看出是 venue 而不是论文标题的开头
_VENUE_HEAD_RE = re.compile(
    r"^(?:in\s+|proceeding|proc\w*\.|arxiv|corr\b|abs/|journal|transactions?|advance|"
    r"ieee|acm|springer|lncs|lecture\s+notes|nature|science|communications|conference|"
    r"workshop|symposium|international|annual|meeting|http|doi)", re.IGNORECASE)


def _split_title_venue(rest):
    """把 ``Title. In Proceedings of VENUE, 2018.`` 切成 (title, venue)。"""
    rest = str(rest or "").strip()
    if not rest:
        return "", ""
    segs = _split_sentences(rest)
    if not segs:
        return "", ""
    title = segs[0]
    venue = ". ".join(segs[1:])
    # 第一句其实是 venue（个别版式把 "In Journal of X, 2018." 写在标题前）→ 顺延取第二句
    if _VENUE_HEAD_RE.match(title) and len(segs) > 1:
        title, venue = segs[1], ". ".join([segs[0]] + segs[2:])
    elif _VENUE_HEAD_RE.match(title):
        title, venue = "", rest
    venue = re.sub(r"^\s*in\s+", "", venue, flags=re.IGNORECASE)
    return title, venue


def _locate_reference_section(pdf_doc):
    """在 PDF 里定位 References 区，返回 ``(文本, 命中标题?, 页数)``。

    处理三类实际情况：
    - References 在正文里出现多次（目录页也算）→ **取最后一个**；
    - 参考文献跨多页 → 从标题所在位置向后逐页拼接；
    - 参考文献之后跟附录/致谢 → 遇到附录/致谢标题立即截断，
      （旧实现会把附录里的 ``Question:`` / ``Answer:`` 问答当成文献条目）。
    找不到任何 References 标题时返回 ``("", False, 页数)``，**不再拿尾部索引页凑数**。
    """
    page_count = getattr(pdf_doc, "page_count", 0) or 0
    lo = max(0, page_count - REF_SCAN_PAGES)
    hit_pages = []
    for pi in range(page_count - 1, lo - 1, -1):
        try:
            txt = pdf_doc[pi].get_text() or ""
        except Exception:
            continue
        hits = list(_REF_HEADING_RE.finditer(txt))
        if hits:
            hit_pages.append((pi, hits[-1].end()))
    if not hit_pages:
        return "", False, page_count
    # 书籍/长文里 References 标题常作为页眉重复出现 → 取**最后一簇的第一页**，
    # 否则只会拿到参考文献区的最后一页，前面几页全丢（UnderstandingDeepLearning 即如此）
    offsets = dict(hit_pages)
    pages = sorted(offsets, reverse=True)
    start_page = pages[0]
    for p in pages:                       # 向前扩到该簇的起始页为止
        start_page = p
        if p - 1 not in offsets:
            break
    off = offsets.get(start_page, 0)
    parts = []
    try:
        first = (pdf_doc[start_page].get_text() or "")[off:]
    except Exception:
        first = ""
    parts.append(first)
    total = len(first)
    pi = start_page + 1
    added = 1
    while pi < page_count and added < REF_MAX_PAGES and total < PDF_TAIL_CHARS:
        try:
            t = pdf_doc[pi].get_text() or ""
        except Exception:
            t = ""
        pi += 1
        if not t.strip():
            continue
        cut = _REF_STOP_HEAD_RE.search(t)
        if cut:
            head = t[:cut.start()]
            if len(head.strip()) > 40:      # 同一页里：参考文献结束后紧跟附录/致谢
                parts.append(head)
                total += len(head)
            break
        parts.append(t)
        total += len(t)
        added += 1
    return "\n".join(parts), True, page_count


# 伪参考文献过滤器：附录问答 / 图表题注 / 页码 / 过短碎片
_NOISE_HEAD_RE = re.compile(
    r"^(?:(?:question|answer|询问|回答)\s*[:：]|"
    r"(?:table|figure|fig\.?|algorithm|appendix|listing|代码示例|图|表)\b\s*[:：]?\s*\d|"
    r"\d{1,4}\s*$|[\d\s.,;:()\[\]/-]+$)", re.IGNORECASE)


def _is_noise(entry):
    s = str(entry or "").strip()
    if len(s) < MIN_ENTRY_CHARS:
        return True
    if _NOISE_HEAD_RE.match(s):
        return True
    if len(re.findall(r"[A-Za-z]", s)) < 12:          # 几乎没有字母：纯页码/符号行
        return True
    if not re.search(r"(?:19|20)\d{2}", s) and "http" not in s.lower() \
            and not re.search(r"[A-Z][\w'.]+\s+[A-Z]", s):
        return True                                    # 既无年份也无作者形态：多半不是文献
    return False


# 某些编号切分器会在 "arXiv preprint" 处把一条切成两半 → 合并回来
_ORPHAN_TAIL_RE = re.compile(r"(?i)(?:arxiv\s+preprint|corr|technical\s+report|in|available\s+at|,)$")


def _merge_orphans(entries):
    """把被错误切开的相邻条目合并：上一条没有年份且以 "arXiv preprint/In/," 结尾。"""
    out = []
    for e in entries or []:
        prev = out[-1] if out else ""
        if prev and _ORPHAN_TAIL_RE.search(prev.rstrip()) \
                and not re.search(r"(?:19|20)\d{2}", prev):
            out[-1] = (prev.rstrip() + " " + str(e)).strip()
        else:
            out.append(str(e))
    return out


def _entry_score(entries):
    """用「能解析出完整字段的条目数」给候选切分结果打分。"""
    good = 0
    for e in entries or []:
        r = _parse_entry_fields(0, e)
        if r["year"] and len(r["title"]) >= 12 and 4 <= len(r["authors"]) <= 240:
            good += 1
    return good


def _clean_entries(entries, max_entries=MAX_REF_ENTRIES):
    """去噪 + 去重（保持顺序）。"""
    out, seen = [], set()
    for e in entries or []:
        if not isinstance(e, str):
            continue
        s = _strip_numbering(_flow_text(e))
        if _is_noise(s):
            continue
        key = _norm_title(s)[:160]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_entries:
            break
    return out


def _quality_report(structured, page_count, heading_found):
    """抽取质量自检：给上层返回 ``low_confidence`` + 说明。"""
    n = len(structured)
    no_title = sum(1 for r in structured if len(str(r.get("title") or "")) < 12)
    no_year = sum(1 for r in structured if not str(r.get("year") or "").strip())
    reasons = []
    low = False
    if not heading_found:
        low = True
        reasons.append("PDF 中未定位到 References/Bibliography 标题，已返回空列表（不拿正文或索引页凑数）")
    elif n == 0:
        low = True
        reasons.append("定位到 References 标题但未切出任何有效条目（可能是扫描件或图片型 PDF）")
    elif n < MIN_QUALITY_ENTRIES and page_count > 10:
        low = True
        reasons.append(f"PDF 共 {page_count} 页却只抽出 {n} 条参考文献（< {MIN_QUALITY_ENTRIES}），估计漏抽")
    if n:
        if no_title / n > 0.4:
            low = True
            reasons.append(f"{no_title}/{n} 条缺少有效标题，字段解析质量偏低")
        if no_year / n > 0.4:
            low = True
            reasons.append(f"{no_year}/{n} 条缺少年份，字段解析质量偏低")
    return {
        "count": n, "page_count": page_count, "missing_title": no_title,
        "missing_year": no_year, "heading_found": heading_found,
        "low_confidence": low, "reasons": reasons,
    }


def extract_references_with_quality(pdf_path_or_doc, tail_chars=PDF_TAIL_CHARS,
                                    max_entries=MAX_REF_ENTRIES):
    """抽取 References 并给出质量自检信息。

    :return: ``{"entries":[...], "quality":{...}, "low_confidence":bool, "reasons":[...]}``
    """
    empty_q = {"count": 0, "page_count": 0, "missing_title": 0, "missing_year": 0,
               "heading_found": False, "low_confidence": True,
               "reasons": ["无法打开 PDF（PyMuPDF 不可用或文件损坏）"]}
    try:
        import pymupdf as fitz
    except Exception:
        try:
            import fitz  # 老版本 PyMuPDF 的导入名
        except Exception:
            return {"entries": [], "quality": empty_q,
                    "low_confidence": True, "reasons": empty_q["reasons"]}
    doc, opened = None, False
    try:
        if hasattr(pdf_path_or_doc, "page_count"):
            doc = pdf_path_or_doc
        else:
            doc = fitz.open(pdf_path_or_doc)
            opened = True
        section, found, page_count = _locate_reference_section(doc)
        if not found:
            q = {"count": 0, "page_count": page_count, "missing_title": 0,
                 "missing_year": 0, "heading_found": False, "low_confidence": True,
                 "reasons": ["PDF 中未定位到 References/Bibliography 标题，"
                             "已返回空列表（不拿正文或索引页凑数）"]}
            return {"entries": [], "quality": q, "low_confidence": True, "reasons": q["reasons"]}

        # 两种切分器：① 编号式（[1]/1./(2017)）→ services.parsing；② 作者-年份流式
        # 按「能解析出完整字段的条目数」择优，避免编号式在某些版式下把条目切残
        numbered = _clean_entries(_merge_orphans(_numbered_split(section)), max_entries)
        flow = _clean_entries(_merge_orphans(_split_author_year_flow(section)), max_entries)
        entries = numbered if _entry_score(numbered) >= _entry_score(flow) else flow

        structured, seen = [], set()
        for i, e in enumerate(entries, 1):
            rec = _parse_entry_fields(i, e)
            key = _norm_title(rec["title"]) or _norm_title(e)
            if key in seen:
                continue
            seen.add(key)
            structured.append(rec)
        q = _quality_report(structured, page_count, found)
        return {"entries": structured, "quality": q,
                "low_confidence": q["low_confidence"], "reasons": q["reasons"]}
    except Exception as e:
        empty_q["reasons"] = [f"抽取异常：{type(e).__name__}: {str(e)[:120]}"]
        return {"entries": [], "quality": empty_q,
                "low_confidence": True, "reasons": empty_q["reasons"]}
    finally:
        if opened:
            try:
                doc.close()
            except Exception:
                pass


def _numbered_split(section):
    """编号式切分：优先复用 services.parsing（口径一致），失败回退 graph._ref_entries。"""
    entries = []
    try:
        from services.parsing import split_reference_entries
        entries = split_reference_entries(section) or []
    except Exception:
        entries = []
    try:
        fallback = [e for e in graph._ref_entries(section or "") if isinstance(e, str)]
        if len(fallback) > len(entries):
            entries = fallback
    except Exception:
        pass
    return entries


def extract_references_from_pdf(pdf_path_or_doc, tail_chars=PDF_TAIL_CHARS,
                                max_entries=MAX_REF_ENTRIES):
    """用 PyMuPDF 从 PDF 真实抽取 References 段并结构化（返回 ``[{id, authors, title, year, venue, raw}, ...]``）。

    质量自检信息见 :func:`extract_references_with_quality`；无 PyMuPDF / 无 References 段时返回 []。
    """
    return extract_references_with_quality(
        pdf_path_or_doc, tail_chars, max_entries).get("entries", [])


def _resolve_pdf(filename):
    """解析论文 PDF 真实路径：优先复用 ui.common.resolve_pdf，失败时按 config 目录兜底。"""
    if not filename:
        return ""
    try:
        from ui.common import resolve_pdf as _rp
        p = _rp(filename)
        if p:
            return p
    except Exception:
        pass
    try:
        from config import UPLOAD_DIR, PDF_DIR
        for base in (UPLOAD_DIR, PDF_DIR):
            p = os.path.join(str(base), str(filename))
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return ""


def _target_paper(ctx, preferred=None):
    """确定从哪篇论文抽 References：显式指定 > ctx.current_paper > 文献库首篇。"""
    try:
        name = preferred or getattr(ctx, "current_paper", None)
        if name and name in getattr(ctx, "papers", {}):
            return name
        order = list(getattr(ctx, "paper_order", []) or [])
        return order[0] if order else ""
    except Exception:
        return ""


def _pdf_reference_entries(ctx, preferred=None):
    """从当前论文 PDF 的 References 段抽真实条目（字符串列表）；失败返回 []。

    优先真实解析 PDF 文件；PDF 不可解析时退回正文 chunks（保留旧行为）。
    """
    try:
        name = _target_paper(ctx, preferred)
        if not name:
            return []
        path = _resolve_pdf(name)
        if path:
            res = extract_references_with_quality(path)
            # 数量够**且**质量自检通过才采用；low_confidence 时上层应提示而非静默使用
            if not res["low_confidence"] and len(res["entries"]) >= MIN_REF_ENTRIES:
                return [r["raw"] for r in res["entries"]]
        # 退回：从入库 chunks 拼出的正文里切 References 段
        full_text = "\n".join(
            c.get("text", "") for c in getattr(ctx, "chunks", []) if c.get("source") == name)
        if len(full_text) < 500:
            return []
        ref_text = graph.ref_section(full_text)
        entries = graph._ref_entries(ref_text)
        entries = [e.strip() for e in entries if isinstance(e, str) and e.strip()]
        return entries if len(entries) >= MIN_REF_ENTRIES else []
    except Exception:
        return []


def reference_whitelist(ctx, preferred=None):
    """构建引用白名单，返回 ``(refs_text, refs_list, source_label)``。

    优先级：① 上传 PDF 的 References 段真实抽取 → ② 入库 chunks 的 References 段
    → ③ 文献库元数据（authors/title/year）→ ④ 空（调用方必须提示先上传文献）。
    """
    refs_text, refs_list, source = "", [], ""
    source_file, name = "", ""
    try:
        name = _target_paper(ctx, preferred)
        if name:
            path = _resolve_pdf(name)
            if path:
                source_file = os.path.basename(path)
                res = extract_references_with_quality(path)
                refs_list = res["entries"]
                low = res["low_confidence"]
                if len(refs_list) >= MIN_REF_ENTRIES and not low:
                    source = f"PDF References 段真实抽取（{os.path.basename(path)}）"
                elif refs_list:
                    # 抽到了但质量不过关：仍然给出，只是打标让上层提示
                    source = (f"⚠️ PDF References 段抽取质量偏低（{os.path.basename(path)}）："
                              + "；".join(res["reasons"]))
                else:
                    refs_list = []          # 没有 References 段时不拿索引页/正文凑数
        if len(refs_list) < MIN_REF_ENTRIES:
            raw = _pdf_reference_entries(ctx, preferred)
            if raw:
                refs_list = [_parse_entry_fields(i, e) for i, e in enumerate(raw, 1)]
                source = f"入库正文 chunks 的 References 段（{name or '文献库'}）"
        if len(refs_list) < MIN_REF_ENTRIES:
            papers = getattr(ctx, "papers", {}) or {}
            order = list(getattr(ctx, "paper_order", []) or [])
            built = []
            for i, n in enumerate(order, 1):
                p = papers.get(n) or {}
                built.append({
                    "id": i,
                    "authors": p.get("authors") or "（作者见原文）",
                    "title": p.get("title") or n,
                    "year": str(p.get("year", "") or ""),
                    "venue": p.get("venue") or p.get("journal") or "",
                    "raw": f"{p.get('authors') or ''}. {p.get('title', n)}. {p.get('year','')}.",
                })
            if built:
                refs_list = built
                source = "文献库元数据（无 PDF References 可用时的兜底）"
    except Exception:
        refs_list, source = [], ""
    refs_list = (refs_list or [])[:MAX_REF_ENTRIES]
    # 每条挂上来源 PDF 文件名：UI 要逐条展示"这条出自哪篇入库论文的参考文献表"（T4）
    for r in refs_list:
        try:
            if isinstance(r, dict) and not r.get("source_pdf"):
                r["source_pdf"] = source_file or name or ""
        except Exception:
            pass
    if refs_list:
        refs_text = "\n".join(_ref_line(i, r) for i, r in enumerate(refs_list, 1))
    return refs_text, refs_list, source


def _real_references(ctx):
    """向后兼容旧签名：返回 (白名单文本, 允许的 [n] 下标集合)。"""
    try:
        text, refs, _ = reference_whitelist(ctx)
        return text, set(range(1, len(refs) + 1))
    except Exception:
        return "", set()


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


def _generated_reference_entries(text):
    """从生成文本里抽出 References 段条目 ``[{"id": n, "text": "..."}]``。"""
    out = []
    try:
        hits = list(_GEN_REF_HEADING_RE.finditer(text or ""))
        seg = text[hits[-1].end():] if hits else ""
        if not seg.strip():
            return out
        cur = None
        for line in seg.splitlines():
            s = line.strip()
            if not s:
                continue
            m2 = re.match(r"^\[(\d{1,4})\]\s*(.*)$", s) or re.match(r"^(\d{1,4})[.)]\s+(.*)$", s)
            if m2:
                if cur:
                    out.append(cur)
                cur = {"id": int(m2.group(1)), "text": m2.group(2).strip()}
            elif s.startswith(("-", "*", "•")):
                if cur:
                    out.append(cur)
                cur = {"id": len(out) + 1, "text": s.lstrip("-*• ").strip()}
            elif cur is not None and not s.startswith("#"):
                cur["text"] += " " + s
        if cur:
            out.append(cur)
        out = [e for e in out if len(e.get("text", "")) >= 15]
        # 去掉尾部非文献内容（如 Conclusion 后的杂项）：仅保留前 MAX_REF_ENTRIES 条
        return out[:MAX_REF_ENTRIES]
    except Exception:
        return out


def validate_reference_whitelist(generated_text, allowed_refs, threshold=TITLE_SIM_THRESHOLD):
    """白名单正则校验：检查生成文本中的引用是否全部来自真实文献。

    校验两件事：
    1. 所有 ``[n]`` 角标是否落在白名单 ``[1..len(allowed_refs)]`` 内（越界=幻觉角标）；
    2. 生成文本 References 段的每条，其标题是否与白名单某条**标题实质匹配**
       （归一化后相似度 > threshold），匹配不上=编造条目。

    :return: ``{"ok", "hallucinated", "report", "n_citations", "n_entries", "n_allowed"}``
    """
    result = {"ok": True, "hallucinated": [], "report": "", "n_citations": 0,
              "n_entries": 0, "n_allowed": 0}
    try:
        allowed = []
        for r in (allowed_refs or []):
            if isinstance(r, dict):
                allowed.append(r)
            elif isinstance(r, str):
                allowed.append(_parse_entry_fields(len(allowed) + 1, r))
        n_allowed = len(allowed)
        result["n_allowed"] = n_allowed
        text = str(generated_text or "")

        # ---- 1) 角标越界检查 ----
        marks = [int(x) for x in _CITE_MARK_RE.findall(text)]
        result["n_citations"] = len(marks)
        if n_allowed == 0:
            if marks:
                result["hallucinated"].append({
                    "kind": "citation", "id": marks[0],
                    "text": f"[{marks[0]}]",
                    "reason": "引用白名单为空（文献库为空或未抽取到 References），文中却出现引用角标",
                })
        else:
            seen_bad = set()
            for n in marks:
                if n < 1 or n > n_allowed:
                    if n in seen_bad:
                        continue
                    seen_bad.add(n)
                    result["hallucinated"].append({
                        "kind": "citation", "id": n, "text": f"[{n}]",
                        "reason": f"角标 [{n}] 超出真实文献范围 [1..{n_allowed}]",
                    })

        # ---- 2') References 章节是否真实存在且条目数达标 ----
        # 旧逻辑在这里有个致命漏洞：模型把整节写成 "[1..40] (List the references…)"
        # 时 _generated_reference_entries 解析出 0 条，走不到下面的标题匹配，
        # 于是校验直接判 PASS —— 占位废稿被当成"无幻觉"放行。这里补上：
        # References 章节缺失/条目数不达标，一律视为生成失败。
        entries = _generated_reference_entries(text)
        result["n_entries"] = len(entries)
        if n_allowed > 0:
            has_heading = bool(list(_GEN_REF_HEADING_RE.finditer(text)))
            min_entries = min(MIN_REF_ENTRIES, n_allowed)
            if not has_heading:
                result["hallucinated"].append({
                    "kind": "section", "id": 0, "text": "（整篇文本没有 References 章节）",
                    "reason": f"生成文本中未找到 References/参考文献 章节，但白名单有 {n_allowed} 条真实文献，"
                              f"参考文献应当被完整列出",
                })
            elif result["n_entries"] < min_entries:
                result["hallucinated"].append({
                    "kind": "section", "id": 0,
                    "text": f"References 章节仅 {result['n_entries']} 条",
                    "reason": (f"References 章节只有 {result['n_entries']} 条（< {min_entries}），"
                               f"疑似未生成或被占位符替代"),
                })

        # ---- 3) References 段条目标题实质匹配 ----
        if n_allowed == 0:
            if entries:
                result["hallucinated"].append({
                    "kind": "entry", "id": entries[0].get("id", 0),
                    "text": str(entries[0].get("text", ""))[:160],
                    "reason": "白名单为空，References 段任何条目都无法核实",
                })
        else:
            allowed_titles = [r.get("title") or r.get("raw") or "" for r in allowed]
            for e in entries:
                cand = _parse_entry_fields(e.get("id", 0), e.get("text", ""))
                # 候选标题池：解析出的 title + raw + 每个句段——引文标题抽取是启发式的，
                # 逐段匹配可显著降低对 "Devlin et al. BERT. NAACL, 2019." 这类
                # 短标题/缩略写法的误报（真实条目宁可不误杀）
                cands = [cand.get("title", ""), cand.get("raw", "")] + list(cand.get("segs") or [])
                cands = [c for c in dict.fromkeys(cands) if c and len(str(c)) >= 4]
                best, best_idx = 0.0, -1
                for ti, t in enumerate(allowed_titles):
                    for c in cands:
                        sim = _title_similarity(c, t)
                        if sim > best:
                            best, best_idx = sim, ti
                    if best >= 0.999:
                        break
                if best < threshold:
                    result["hallucinated"].append({
                        "kind": "entry", "id": e.get("id", 0),
                        "text": str(e.get("text", ""))[:200],
                        "reason": (f"标题与真实文献最高相似度仅 {best:.2f}（阈值 {threshold}），"
                                   f"最接近的是 [{best_idx + 1}] {str(allowed_titles[best_idx])[:60]}"
                                   if best_idx >= 0 else "未匹配到任何真实文献"),
                    })

        ok = not result["hallucinated"]
        result["ok"] = ok
        if ok:
            result["report"] = (f"✅ 引用白名单校验通过：{result['n_citations']} 处 [n] 角标、"
                                f"{result['n_entries']} 条 References 条目，"
                                f"全部命中 {n_allowed} 条真实文献。")
        else:
            lines = [f"🚨 检测到 {len(result['hallucinated'])} 条疑似编造文献/越界角标："]
            for h in result["hallucinated"][:20]:
                lines.append(f"- [{h['kind']}] {h['text'][:120]} —— {h['reason']}")
            result["report"] = "\n".join(lines)
        return result
    except Exception as e:
        result["ok"] = False
        result["report"] = f"⚠️ 白名单校验自身异常（{type(e).__name__}: {str(e)[:120]}），本次结果未核实。"
        return result


# ---------------------------------------------------------------- 大纲生成（T1）
def _outline_prompt(idea, refs_text, n_allowed, strict=False):
    """大纲正文 prompt。

    **刻意不再要求模型输出 References**：实测 glm-4-flash 会把整节写成占位指令
    （``[1..40] (List the references as per the given real literature sources [1..40].)``）。
    References 改由 :func:`build_references_section` 用白名单确定性拼接，
    prompt 里明确禁止模型写这一节（写了也会被剔除）。
    """
    rule = (f"【引用规则·必须逐条遵守】\n"
            f"1) 只能引用下面列表中的真实文献，引用角标 [n] 的 n 必须落在 [1..{n_allowed}] 内；"
            f"禁止出现列表以外的编号，禁止编造文献。\n"
            f"2) 若某个论点找不到对应文献，就**不写引用**，绝不要补一个编号上去。\n"
            f"3) Introduction 与 Related Work 至少各引用 3 处，全文引用角标总数不少于 8 处；"
            f"引用时写明该文献的作者姓氏与年份（如 [n] (Vaswani et al., 2017)）。\n"
            f"4) 【重要】不要输出 References / Bibliography / 参考文献 章节，也不要写类似 "
            f"“[1..{n_allowed}] (List the references…)” 的占位指令；"
            f"这一节由系统用真实文献自动拼接，你写出来也会被丢弃。\n"
            f"5) 禁止输出任何占位符/待办字眼（[Placeholder]、TODO、TBD、XXX、待补充）；"
            f"写不出来的小节直接不写，不要留占位。\n"
            f"\n可用真实文献（共 {n_allowed} 条）：\n{refs_text}")
    if strict:
        rule += ("\n【强化约束·上一轮不合格，本次务必遵守】\n"
                 "1) 上一轮出现了白名单之外的文献或越界编号，本次每一个 [n] 都必须能在上面的列表里"
                 "按位查到（第 n 条 = 列表里编号为 [n] 的文献）；\n"
                 "2) 不允许出现任何占位符、省略语或“同上/见列表”之类的说法；\n"
                 "3) 再次强调：不要写 References 章节。")
    return f"""围绕以下研究 Idea，输出一篇英文学术论文的写作框架（Markdown）。

必须且只包含以下章节（一级/二级标题均可，顺序固定，不要新增 References 章节）：
Title / Abstract / Introduction（动机与贡献点）/ Related Work /
Method（拟采用的模型或 Agent 架构，含关键公式）/ Experiments（数据集、指标、基线、消融）/
Threats to Validity / Conclusion。

【篇幅】全文控制在 {OUTLINE_TARGET_WORDS} 词以内，以要点式 bullet + 短句为主，每节给出字数建议。
{rule}

Idea：{idea}"""


def outline_body_stream(ctx, prompt, temperature=0.4):
    """流式生成大纲正文：逐 chunk yield 字符串（UI 侧用 ``st.write_stream`` 消费）。

    优先走 ``ctx.llm.stream``（与 LLMClient 其余入口同一套退避重试语义）；
    ctx.llm 不可用时退化为一次性 :func:`_llm_text`（整段 yield）。
    ``ctx.llm.stream`` 在流式中途失败时会 yield 一条 ``【LLM 暂时不可用…】``
    伪响应，由 :func:`finalize_outline` 识别、剔除并保留已生成部分。
    """
    try:
        llm = getattr(ctx, "llm", None)
        if llm is not None and getattr(llm, "available", False):
            for chunk in llm.stream([{"role": "user", "content": str(prompt)}],
                                    temperature=temperature):
                if chunk:
                    yield chunk
            return
    except Exception:
        pass
    text = _llm_text(ctx, prompt, temperature=temperature)
    if text:
        yield text


def finalize_outline(raw, refs_list, source, elapsed_sec=0.0):
    """把 LLM 正文原文加工为最终大纲（流式/非流式两条生成路径共用的后处理）。

    步骤：剔除模型自带 References 章节 → 清洗占位串 → 越界角标打码 →
    **确定性拼接** References（不上 LLM）→ 对最终全文跑白名单校验。

    失败语义（对应「LLM 失败不能白屏/不输出半章垃圾」）：
    - 正文为空 → 返回 :data:`NO_MODEL_OUTPUT_HINT`，明确告知未生成；
    - 正文混入 ``【LLM 暂时不可用…】`` 伪响应（流式中途失败）→ 剔除伪响应、
      **保留已生成部分**，置 ``llm_error=True`` / ``partial=True``，由 UI 明确告警；
    - 白名单为空 → 返回 :data:`EMPTY_LIBRARY_HINT`，不生成任何占位/编造引用。

    返回 dict 与 :func:`generate_outline_verified` 一致（多 ``llm_error``/``partial`` 两字段）。
    """
    out = {"text": "", "refs": [], "validation": None, "source": source or "",
           "sanitized": 0, "empty_library": False, "placeholder_issues": [],
           "elapsed_sec": round(float(elapsed_sec or 0.0), 2), "body": "",
           "references_deterministic": False, "llm_error": False, "partial": False}
    if not refs_list:
        out["text"] = EMPTY_LIBRARY_HINT
        out["empty_library"] = True
        return out
    body = str(raw or "")
    if "【LLM 暂时不可用" in body:
        # 流式中途失败的伪响应：剔掉标记、保留前面已经真实生成的内容
        out["llm_error"] = True
        body = re.sub(r"【LLM 暂时不可用[^】]*】", "", body)
    if not body.strip():
        out["text"] = NO_MODEL_OUTPUT_HINT
        return out

    # 顺序很重要：先删 References 段，占位串里最常见的 "[1..N] (List the references…)"
    # 本来就落在这一节里，会被整节带走。
    body, _ = _strip_generated_references(body)
    body, issues = sanitize_placeholders(body)
    body, k = _sanitize_citations(body, set(range(1, len(refs_list) + 1)))
    # 再扫一遍：正文里若仍有占位串（如正文中间写了 [Placeholder]），必须如实上报
    residual = scan_placeholders(body)

    # 确定性拼接 References（不上 LLM，100% 来自白名单）
    ref_section = build_references_section(refs_list)
    text = body.rstrip() + "\n\n" + ref_section + "\n"

    # 对**最终全文**做白名单校验
    validation = validate_reference_whitelist(text, refs_list)
    out.update({"text": text, "body": body, "refs": refs_list, "validation": validation,
                "sanitized": k, "placeholder_issues": residual,
                "references_deterministic": True, "partial": out["llm_error"]})
    return out


def generate_outline_verified(ctx, idea, preferred=None, strict=False, pdf_path=None):
    """生成大纲正文 + **确定性**拼接真实 References，然后跑白名单校验。返回 dict（不抛异常）。

    返回字段：
      ``text`` 最终全文（正文 + 确定性 References 章节）
      ``refs`` 白名单真实条目（每条带 source_pdf）
      ``validation`` :func:`validate_reference_whitelist` 的结果
      ``source`` / ``sanitized`` / ``empty_library`` / ``placeholder_issues`` / ``elapsed_sec``
      ``llm_error`` / ``partial``（LLM 中途失败但保留了已生成部分）

    关键设计（对应赛题模块六「无幻觉 References」）：
      References 章节**不交给大模型**，只用白名单字符串拼接，因此不存在编造空间，
      也省掉了占用时间最长的那部分 token 输出。
    后处理与流式路径共用 :func:`finalize_outline`，两条路径的校验保证完全一致。
    """
    out = {"text": "", "refs": [], "validation": None, "source": "",
           "sanitized": 0, "empty_library": False, "placeholder_issues": [],
           "elapsed_sec": 0.0, "body": "", "references_deterministic": False,
           "llm_error": False, "partial": False}
    t_start = time.time()
    try:
        # ---- 1) 真实白名单（少了它就谈不上无幻觉，直接提示用户先上传文献）----
        if pdf_path:
            refs_list = extract_references_from_pdf(pdf_path)
            source = f"PDF References 段真实抽取（{os.path.basename(str(pdf_path))}）" if refs_list else ""
            for _r in refs_list:                       # 挂上来路，供 UI 逐条展示（T4）
                try:
                    _r["source_pdf"] = os.path.basename(str(pdf_path))
                except Exception:
                    pass
        else:
            refs_list = []
            source = ""
        if len(refs_list) < MIN_REF_ENTRIES:
            refs_text, refs_list, source = reference_whitelist(ctx, preferred)
        else:
            refs_text = "\n".join(_ref_line(i, r) for i, r in enumerate(refs_list, 1))
        if not refs_list:
            out["text"] = EMPTY_LIBRARY_HINT
            out["empty_library"] = True
            out["elapsed_sec"] = round(time.time() - t_start, 2)
            return out

        # ---- 2) LLM 只写正文（不含 References）----
        prompt = _outline_prompt(idea, refs_text, len(refs_list), strict=strict)
        raw = _llm_text(ctx, prompt, temperature=0.4)

        # ---- 3) 共用后处理：剔 References → 清洗 → 确定性拼接 → 白名单校验 ----
        return finalize_outline(raw, refs_list, source, time.time() - t_start)
    except Exception as e:
        out["text"] = f"⚠️ 大纲生成失败：{type(e).__name__}: {str(e)[:160]}"
        out["elapsed_sec"] = round(time.time() - t_start, 2)
        return out


def generate_outline(ctx, idea, preferred=None):
    """向后兼容签名（tools/definitions.py:279 调用）：返回大纲文本 str。"""
    try:
        return generate_outline_verified(ctx, idea, preferred=preferred).get("text", "")
    except Exception:
        return ""


# ---------------------------------------------------------------- 拓扑图代码生成
def diagram(ctx, kind, topic):
    kinds = {
        "mermaid": ("Mermaid flowchart", "```mermaid"),
        "tikz": ("LaTeX TikZ", "```latex"),
        "graphviz": ("Graphviz DOT", "```dot"),
        "matplotlib": ("Python Matplotlib", "```python"),
    }
    name, fence = kinds.get(kind, ("Mermaid flowchart", "```mermaid"))
    prompt = f"""请为主题“{topic}”生成可直接运行的 {name} 代码，用于绘制系统/架构拓扑图。

绘图硬性要求：
1. **节点与连线标签一律用中文**（专业术语按「中文优先、无法准确翻译才保留英文」原则，
   例如 Transformer / BERT / Softmax / Embedding 等模型与算法名保留原文，其余描述性
   文字必须中文，如 “输出概率”“前馈网络”“加归一化”）；
2. **精简但不丢内容**：同层同类节点合并为一个带说明的节点，节点总数控制在 12 个以内；
   层级不超过 4 层；禁止为凑结构添加空节点、重复节点或与主题无关的分支；
3. 每个节点标签 ≤ 10 个汉字，用换行（flowchart 节点内 <br/>）分行使框体不超宽；
4. 只输出代码，不要任何解释。"""
    raw = _llm_text(ctx, prompt, temperature=0.2)
    raw = raw.replace("```mermaid", "").replace("```latex", "").replace("```dot", "")
    raw = raw.replace("```python", "").replace("```", "").strip()
    return raw


# ---------------------------------------------------------------- T2：TikZ 真实编译
def _detect_latex_engine():
    """检测本机 LaTeX 编译器，返回 (引擎名, 可执行文件路径)；没有则返回 ("", "")。"""
    try:
        for engine in LATEX_ENGINES:
            path = shutil.which(engine)
            if path:
                return engine, path
    except Exception:
        pass
    return "", ""


def _build_latex_document(code):
    """把 TikZ 片段包成可编译的最小 LaTeX 文档；已是完整文档则原样返回。"""
    if re.search(r"\\documentclass", code or ""):
        return code
    libs = [l.strip() for l in re.findall(r"\\usetikzlibrary\{([^}]*)\}", code or "") if l.strip()]
    pkgs = {"tikz"}
    for p in re.findall(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", code or ""):
        for item in p.split(","):
            item = item.strip()
            if item and item != "tikz":
                pkgs.add(item)
    body = re.sub(r"\\usetikzlibrary\{[^}]*\}", "", code or "")
    body = re.sub(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\}", "", body)
    preamble = "\\documentclass[border=8pt]{standalone}\n" + \
        "\n".join("\\usepackage{%s}" % p for p in sorted(pkgs))
    if libs:
        preamble += "\n\\usetikzlibrary{" + ",".join(libs) + "}"
    return preamble + "\n\\begin{document}\n" + body.strip() + "\n\\end{document}\n"


def _pdf_to_png_bytes(pdf_path, dpi=170):
    """用 PyMuPDF 把 PDF 首页渲染为 PNG 字节（不需要 ImageMagick）。"""
    try:
        import pymupdf as fitz
    except Exception:
        try:
            import fitz
        except Exception:
            return None
    doc = None
    try:
        doc = fitz.open(pdf_path)
        if doc.page_count <= 0:
            return None
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        return pix.tobytes("png")
    except Exception:
        return None
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def try_compile_tikz(code, timeout=TIKZ_TIMEOUT_SEC, dpi=170):
    """尝试把 TikZ 代码编译成 PNG。

    :return: ``{"ok", "engine", "png"(bytes|None), "log", "reason"}``
      - ok=True：真实编译渲染成功（engine → PDF → PNG）；
      - ok=False 且 reason 含"未检测到"：本机无 LaTeX 编译器，调用方应展示降级提示；
      - ok=False 且有 log：编译失败，调用方应折叠展示日志前 500 字。
    临时目录在 finally 中清理。
    """
    res = {"ok": False, "engine": "", "png": None, "log": "", "reason": ""}
    code = (code or "").strip()
    if not code:
        res["reason"] = "TikZ 代码为空，跳过编译。"
        return res
    if "\\begin{tikzpicture}" not in code and "\\documentclass" not in code:
        res["reason"] = "代码中未检测到 tikzpicture 环境，跳过编译（按纯文本展示）。"
        return res
    engine, exe = _detect_latex_engine()
    if not engine:
        res["reason"] = ("本机未检测到 LaTeX 编译器（已依次查找 "
                         f"{'/'.join(LATEX_ENGINES)}），无法本地渲染。"
                         "已降级为源码展示 + 在线编辑器入口。")
        return res
    res["engine"] = engine
    tmp = tempfile.mkdtemp(prefix="wcopilot_tikz_")
    try:
        tex_path = os.path.join(tmp, "main.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(_build_latex_document(code))
        if engine == "tectonic":
            cmd = [exe, "--outdir", tmp, "--outfmt", "pdf", "main.tex"]
        else:
            cmd = [exe, "-interaction=nonstopmode", "-file-line-error",
                   "-output-directory", tmp, "main.tex"]
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True,
                              timeout=timeout, shell=False)
        log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        res["log"] = log[:TIKZ_LOG_SNIPPET]
        pdf_path = os.path.join(tmp, "main.pdf")
        if not os.path.exists(pdf_path):
            res["reason"] = f"{engine} 编译未产出 PDF（返回码 {proc.returncode}），详见错误日志。"
            return res
        png = _pdf_to_png_bytes(pdf_path, dpi=dpi)
        if png:
            res["ok"] = True
            res["png"] = png
        else:
            res["reason"] = "PDF 已生成，但转 PNG 失败（PyMuPDF 不可用或页面为空）。"
        return res
    except subprocess.TimeoutExpired:
        res["reason"] = f"LaTeX 编译超时（>{timeout}s），已放弃本次渲染。"
        return res
    except Exception as e:
        res["reason"] = f"LaTeX 编译异常：{type(e).__name__}: {str(e)[:160]}"
        return res
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------- T3：Graphviz 真实渲染
def _dot_via_matplotlib(code):
    """降级方案 A：networkx + matplotlib 画等价拓扑图，返回 PNG bytes；不可用返回 None。"""
    try:
        from services import wcopilot_dotrender as dr
        g = dr.parse_dot(code)
        if not g["nodes"]:
            return None
        import networkx as nx
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        G = nx.DiGraph() if g.get("directed", True) else nx.Graph()
        labels = {}
        for n in g["nodes"]:
            G.add_node(n["id"])
            labels[n["id"]] = n["label"]
        for e in g["edges"]:
            G.add_edge(e["src"], e["dst"])
        fig, ax = plt.subplots(figsize=(min(12, 2 + len(G) * 1.1), min(8, 1.5 + len(G) * 0.8)))
        pos = nx.spring_layout(G, seed=42)
        nx.draw_networkx_nodes(G, pos, ax=ax, node_color="#eef4fb",
                               edgecolors="#3b6fb6", node_size=1800)
        nx.draw_networkx_edges(G, pos, ax=ax, arrows=g.get("directed", True),
                               edge_color="#5b6b7a", arrowstyle="->", arrowsize=18)
        nx.draw_networkx_labels(G, pos, labels=labels, ax=ax, font_size=9, font_color="#1f3a5f")
        ax.set_axis_off()
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140)
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


def render_dot(code, timeout=DOT_TIMEOUT_SEC):
    """渲染 Graphviz DOT，三级降级，返回 dict（不抛异常）。

    ``{"ok", "mode", "svg", "png", "note", "log"}``
      - mode="graphviz"：真实 dot 可执行文件渲染（SVG）；
      - mode="matplotlib"：networkx + matplotlib 降级渲染（PNG，等价拓扑图）；
      - mode="pure"：零依赖纯 Python DOT→SVG 降级渲染（等价拓扑图）；
      - mode="none"：都不可用，调用方展示源码 + 说明。
    """
    res = {"ok": False, "mode": "none", "svg": None, "png": None, "note": "", "log": ""}
    code = (code or "").strip()
    if not code:
        res["note"] = "DOT 源码为空，无法渲染。"
        return res
    dot_exe = ""
    try:
        dot_exe = shutil.which("dot") or ""
    except Exception:
        dot_exe = ""
    has_pkg = False
    try:
        import importlib.util as _ilu
        has_pkg = bool(_ilu.find_spec("graphviz"))
    except Exception:
        has_pkg = False

    # 1) Python graphviz 包 + dot 可执行文件
    if has_pkg and dot_exe:
        try:
            import graphviz
            svg = graphviz.Source(code, format="svg").pipe(encoding="utf-8")
            if "<svg" in (svg or ""):
                return {"ok": True, "mode": "graphviz", "svg": svg, "png": None,
                        "note": "真实 Graphviz 渲染（Python graphviz 包 + dot 可执行文件）", "log": ""}
        except Exception as e:
            res["log"] = f"Python graphviz 包渲染失败：{type(e).__name__}: {str(e)[:200]}"
    # 2) 直接用 dot -Tsvg（无 Python 包也能跑）
    if dot_exe:
        try:
            proc = subprocess.run([dot_exe, "-Tsvg"], input=code, capture_output=True,
                                  text=True, timeout=timeout, shell=False)
            svg = proc.stdout or ""
            if "<svg" in svg:
                return {"ok": True, "mode": "graphviz", "svg": svg, "png": None,
                        "note": f"真实 Graphviz 渲染（{dot_exe} -Tsvg）", "log": ""}
            res["log"] = (res["log"] + "\n" + (proc.stderr or ""))[:2000]
        except Exception as e:
            res["log"] = (res["log"] + f"\ndot 调用异常：{type(e).__name__}: {str(e)[:200]}")[:2000]
    else:
        res["log"] = (res["log"] + "\n未找到 dot 可执行文件（PATH 中无 graphviz）。").strip()

    # 3) networkx + matplotlib 降级
    png = _dot_via_matplotlib(code)
    if png:
        return {"ok": True, "mode": "matplotlib", "svg": None, "png": png,
                "note": "降级渲染（等价拓扑图，networkx + matplotlib 力导向布局）", "log": res["log"]}

    # 4) 零依赖纯 Python DOT→SVG 降级
    try:
        from services import wcopilot_dotrender as dr
        svg = dr.dot_to_svg(code)
        if svg:
            return {"ok": True, "mode": "pure", "svg": svg, "png": None,
                    "note": "降级渲染（等价拓扑图，零依赖 DOT→SVG 分层布局，非 Graphviz 官方渲染）",
                    "log": res["log"]}
    except Exception as e:
        res["log"] = (res["log"] + f"\n纯 Python SVG 渲染异常：{type(e).__name__}: {str(e)[:200]}")[:2000]

    res["note"] = ("无法渲染：本机未安装 dot 可执行文件、Python graphviz 包、"
                   "networkx/matplotlib，纯 Python 降级也未解析出节点。已展示 DOT 源码。")
    return res


# ---------------------------------------------------------------- T4：CSV 自动出图
def _is_datetime_like(series, name):
    try:
        if pd is None:
            return False
        sample = series.dropna().astype(str).head(200)
        if sample.empty:
            return False
        if not any(k in str(name).lower() for k in
                   ("date", "time", "year", "month", "day", "日期", "时间", "年份")):
            return False
        conv = pd.to_datetime(sample, errors="coerce")
        return float(conv.notna().mean()) > 0.8
    except Exception:
        return False


def classify_columns(df):
    """按列推断类型：numeric / datetime / categorical / boolean / text_highcard。"""
    out = OrderedDict()
    if pd is None or df is None:
        return out
    try:
        for col in df.columns:
            s = df[col]
            try:
                if pd.api.types.is_bool_dtype(s):
                    out[col] = "boolean"
                    continue
                if pd.api.types.is_numeric_dtype(s):
                    out[col] = "numeric"
                    continue
                if pd.api.types.is_datetime64_any_dtype(s):
                    out[col] = "datetime"
                    continue
                if _is_datetime_like(s, col):
                    out[col] = "datetime"
                    continue
                nunique = int(s.nunique(dropna=True))
                out[col] = "categorical" if nunique <= CAT_CARDINALITY_MAX else "text_highcard"
            except Exception:
                out[col] = "text_highcard"
    except Exception:
        return out
    return out


def infer_chart_plan(df, max_charts=MAX_AUTO_CHARTS):
    """按列类型自动推荐 1-3 个最合适图表，返回 plan 列表。

    plan: ``{"kind", "x", "y", "dims", "title", "reason"}``
    规则：时间+数值→折线；类别(基数<15)+数值→柱状；数值≥3→相关性热力图 + 平行坐标；
    两数值→散点；单数值→直方图。
    """
    plans = []
    if pd is None or df is None or getattr(df, "empty", True):
        return plans
    try:
        types = classify_columns(df)
        nums = [c for c, t in types.items() if t == "numeric"]
        dts = [c for c, t in types.items() if t == "datetime"]
        cats = [c for c, t in types.items() if t in ("categorical", "boolean")]
        n_rows = int(len(df))

        def add(kind, x, y, dims, title, reason):
            plans.append({"kind": kind, "x": x, "y": y, "dims": dims,
                          "title": title, "reason": reason})

        # 1) 时间列 + 数值列 → 折线
        if dts and nums:
            add("line", dts[0], nums[0], [], f"{nums[0]} 随 {dts[0]} 的变化趋势",
                f"因为检测到 {dts[0]} 是时间列、{nums[0]} 是数值列，推荐折线图（看随时间的变化趋势）。")
        # 2) 类别列（基数<15）+ 数值列 → 柱状
        if cats and nums:
            x = cats[0]
            # 折线已占用 nums[0] 时，柱状换一列，避免两个图看同一对数据
            y = nums[1] if (dts and len(nums) > 1) else nums[0]
            try:
                card = int(df[x].nunique(dropna=True))
            except Exception:
                card = 0
            add("bar", x, y, [], f"{y} 按 {x} 分组对比",
                f"因为检测到 {x} 是类别列（基数 {card}<{CAT_CARDINALITY_MAX}）、{y} 是数值列，"
                f"推荐柱状图（做分组对比）。")
        # 3) 数值列 >= 3 → 相关性热力图 + 平行坐标
        if len(nums) >= 3:
            add("heatmap", None, None, nums[:10], "数值列相关性热力图",
                f"因为检测到 {len(nums)} 个数值列（>=3），推荐相关性热力图（一次性看清两两相关强度）。")
            if n_rows <= 500:
                add("parallel", None, None, nums[:10], "多维平行坐标图",
                    f"因为检测到 {len(nums)} 个数值列（>=3）且样本量 {n_rows} 适中，"
                    f"推荐平行坐标图（观察多维样本分布与分簇）。")
        # 4) 两个数值列 → 散点
        if len(nums) >= 2:
            add("scatter", nums[0], nums[1], [], f"{nums[1]} vs {nums[0]} 散点图",
                f"因为检测到 {nums[0]} 与 {nums[1]} 均为数值列，推荐散点图（判断相关性与离群点）。")
        # 5) 单个数值列 → 直方图
        if len(nums) == 1:
            add("histogram", None, nums[0], [], f"{nums[0]} 分布直方图",
                f"因为仅检测到 1 个数值列 {nums[0]}，推荐直方图（看分布形态与偏态）。")
        # 6) 兜底：纯类别列 → 频数柱状图
        if not plans and cats:
            add("bar", cats[0], None, [], f"{cats[0]} 频数分布",
                f"因为未检测到数值列，但 {cats[0]} 是类别列，推荐频数柱状图。")

        # 去重（同 kind + 同 x/y）+ 截断
        seen, uniq = set(), []
        for p in plans:
            key = (p["kind"], p["x"], p["y"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(p)
        return uniq[:max_charts]
    except Exception:
        return plans[:max_charts]


# ---------------------------------------------------------------- T5：Figure Caption
def _fmt_num(v):
    try:
        f = float(v)
    except Exception:
        return str(v)
    if abs(f - round(f)) < 1e-9 and abs(f) < 1e7:
        return str(int(round(f)))
    return f"{f:.4g}"


def _row_label(df, idx_label):
    """取一行的可读标签（第一个非数值列的值）；取不到就退回索引值本身。"""
    try:
        row = df.loc[idx_label]
    except Exception:
        return str(idx_label)[:32]
    try:
        for c in df.columns:
            if pd is None or not pd.api.types.is_numeric_dtype(df[c]):
                return str(row[c])[:32]
    except Exception:
        pass
    return str(idx_label)[:32]


def caption_has_numbers(text):
    """caption 是否包含**真实数据**数字（T5 校验用）。

    先剥掉开头的 "Figure 1." / "Fig. 2:" / "图 3" 这类编号——否则几乎所有 caption
    都会因为编号里的数字而"通过"校验，校验形同虚设。
    """
    body = _FIG_PREFIX_RE.sub("", str(text or ""))
    return bool(_NUM_RE.search(body))


def chart_facts(df, x=None, y=None, top_n=3):
    """从 df 实测计算图表关键数值事实（极值、趋势、Top-N、相关系数），供 caption prompt 注入。"""
    lines = []
    if pd is None or df is None or getattr(df, "empty", True):
        return ""
    try:
        types = classify_columns(df)
        nums = [c for c, t in types.items() if t == "numeric"]
        lines.append(f"Table shape: {len(df)} rows x {len(df.columns)} columns.")
        # 指定 x/y 的事实
        if y and y in df.columns and pd.api.types.is_numeric_dtype(df[y]):
            ys = df[y].dropna()
            if len(ys):
                lines.append(f"Column '{y}': min={_fmt_num(ys.min())} (@{_row_label(df, int(ys.idxmin()))}), "
                             f"max={_fmt_num(ys.max())} (@{_row_label(df, int(ys.idxmax()))}), "
                             f"mean={_fmt_num(ys.mean())}, median={_fmt_num(ys.median())}.")
            if x and x in df.columns:
                xt = types.get(x, "")
                if xt in ("datetime", "numeric"):
                    try:
                        sub = df[[x, y]].dropna().sort_values(by=x)
                        if len(sub) >= 2:
                            first, last = float(sub[y].iloc[0]), float(sub[y].iloc[-1])
                            delta = last - first
                            pct = (delta / abs(first) * 100.0) if abs(first) > 1e-12 else float("nan")
                            direction = "rises" if delta > 0 else ("falls" if delta < 0 else "stays flat")
                            lines.append(f"Trend over '{x}': {y} {direction} from {_fmt_num(first)} "
                                         f"to {_fmt_num(last)} (delta {_fmt_num(delta)}"
                                         + (f", {_fmt_num(pct)}%" if pct == pct else "") + ").")
                            diffs = sub[y].diff().dropna()
                            if len(diffs) and (diffs > 0).all():
                                lines.append(f"Monotonic increasing across all {len(sub)} points.")
                            elif len(diffs) and (diffs < 0).all():
                                lines.append(f"Monotonic decreasing across all {len(sub)} points.")
                    except Exception:
                        pass
                elif xt in ("categorical", "boolean", "text_highcard"):
                    try:
                        agg = df.groupby(x)[y].mean().sort_values(ascending=False)
                        top = list(agg.items())[:top_n]
                        if top:
                            lines.append("Top-%d %s by mean %s: %s." % (
                                len(top), x, y,
                                ", ".join(f"{k} ({_fmt_num(v)})" for k, v in top)))
                        if len(agg) > top_n:
                            lines.append(f"Lowest: {agg.index[-1]} ({_fmt_num(agg.iloc[-1])}).")
                    except Exception:
                        pass
            if x and x in df.columns and types.get(x) == "numeric":
                try:
                    sub = df[[x, y]].dropna()
                    if len(sub) >= 3:
                        corr = float(sub[x].corr(sub[y]))
                        strength = ("strong positive" if corr > 0.7 else
                                    "moderate positive" if corr > 0.3 else
                                    "weak positive" if corr > 0.05 else
                                    "weak negative" if corr > -0.3 else
                                    "moderate negative" if corr > -0.7 else "strong negative")
                        lines.append(f"Pearson correlation between '{x}' and '{y}': "
                                     f"r={corr:.3f} ({strength}).")
                except Exception:
                    pass
        else:
            for c in nums[:4]:
                s = df[c].dropna()
                if len(s):
                    lines.append(f"Column '{c}': min={_fmt_num(s.min())}, max={_fmt_num(s.max())}, "
                                 f"mean={_fmt_num(s.mean())}, std={_fmt_num(s.std())}.")
        # 数值列两两相关 Top-3
        if len(nums) >= 2:
            try:
                corr = df[nums[:8]].corr().abs()
                pairs = []
                cols = list(corr.columns)
                for i in range(len(cols)):
                    for j in range(i + 1, len(cols)):
                        pairs.append((float(corr.iloc[i, j]), cols[i], cols[j]))
                pairs.sort(reverse=True)
                for v, a, b in pairs[:3]:
                    raw = float(df[a].corr(df[b]))
                    lines.append(f"corr({a}, {b}) = {raw:.3f} (|r|={v:.3f}).")
            except Exception:
                pass
        return "\n".join(lines)
    except Exception:
        return "\n".join(lines)


def figure_caption(ctx, df, chart_desc="", x=None, y=None):
    """生成 Figure Caption：把实测数值事实注入 prompt，并校验是否含真实数字。

    向后兼容旧签名 ``figure_caption(ctx, df, chart_desc)``；新增可选 x/y 用于精确定位事实。
    若首轮输出不含任何数字，会用强化 prompt 重新生成一次（仍不含则原样返回，由 UI 提示）。
    """
    try:
        summary = df.describe(include="all").to_string()
    except Exception:
        summary = ""
    facts = chart_facts(df, x=x, y=y)
    base = f"""根据以下数据统计与图表信息，用英文撰写一条符合学术规范的 Figure Caption
（句式：Figure X. It shows/illustrates ...；需点明核心趋势、对比与结论，客观简洁，2-3 句）。
图表类型：{chart_desc}
关键数值事实（必须真实引用其中的数字，禁止编造未出现的数字）：
{facts or "（无可用数值事实，请只做定性描述）"}
数据统计：
{summary[:2500]}"""
    text = _llm_text(ctx, base, temperature=0.3)
    if text and not caption_has_numbers(text):
        retry = base + """

【重写要求】你上一次的输出没有包含任何具体数字。请重写，并在句中明确引用上面的
"关键数值事实"里出现过的真实数字（至少 2 个，例如 72.1%、89.4%、r=0.93），
不要发明任何未在上面出现过的数字。"""
        text2 = _llm_text(ctx, retry, temperature=0.2)
        if text2 and caption_has_numbers(text2):
            text = text2
    return text


# ---------------------------------------------------------------- 翻译
def translate_to_english(ctx, text):
    prompt = f"""请将以下中文学术内容翻译为符合国际期刊/会议规范的英文：术语准确、句式客观、逻辑严密，
保留数学公式与引用标记；只输出英文译文，不要解释。
内容：
{text}"""
    return _llm_text(ctx, prompt, temperature=0.2)


def translate_to_chinese(ctx, text):
    prompt = f"""请将以下英文学术内容精准翻译为中文：保留专业术语（首次出现时可在括号内标注原文）、
公式与引用标记，保持段落结构；只输出中文译文，不要解释。
内容：
{text}"""
    return _llm_text(ctx, prompt, temperature=0.2)


def code_fingerprint(code):
    """代码指纹，供 UI 缓存编译/渲染结果用（避免每次 rerun 都重新编译）。"""
    try:
        return hashlib.md5(str(code or "").encode("utf-8", "ignore")).hexdigest()
    except Exception:
        return ""


# ================================================================ R17：写作质量审计三件套 + 论证单元骨架
# 本节全部**追加在文件末尾**：既有函数/常量行号零变动，
# docs/technical_doc.md 的 `path:N sym` 引用不漂移，_check_doc_refs 自动保持绿。
#
# 借鉴的业界做法（均取「可离线确定性实现」的部分，CI 无 API Key 也能跑）：
# 1. **论断-证据对齐**（claim-evidence coverage）——可靠写作系统架构
#    （rasha.me《Reliable AI Writing Systems》Stage 3 "Deterministic Coverage
#    Check"：每条论断先检查有没有引用，没有的一律标记，零模型调用、零成本）；
#    Paperpal Reference Checker 的 Citation Relevance / Missing Reference
#    Match 两项检查同属这一类。
# 2. **引用-参考文献双向核对**——Paperpal Manuscript Checker 的
#    "Citation-Reference Correspondence"（文中角标与文献清单一一对应，
#    漏引 / 越界 / 格式混用全部显式列出，不等审稿人替我们发现）。
# 3. **文档内自一致性**——同上架构 Stage 6 "Intra-Document Consistency
#    Check"（术语漂移、跨节矛盾）+ 中文长文写作方法论的「跨章节检查」。
# 4. **大纲→段落展开骨架**——SciSpace / Jenni AI 的 section-aware drafting
#    与「论证单元」模板（论点句 / 证据 / 过渡），离线模板化，LLM 或用户只填内容。
#
# 设计纪律（与本模块既有校验完全一致）：
# - 全部纯正则 / 字符串启发式：零网络、零 LLM 调用、零新增第三方依赖；
# - 任何异常都走 try/except 返回结构化降级结果，绝不抛给调用方；
# - 「没问题」与「没检查成」必须可区分：自身异常时 ok=False 且 report 明说。

# ---------------------------------------------------------------- 本节公共正则（确定性、零依赖）
# Markdown 标题行（`## Method`）或加粗独行标题（`**Method**`）
_HEADING_LINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s+(?P<md>[^#]+?)\s*#*|\*\*(?P<bold>[^*]{2,80})\*\*:?)\s*$")
# 列表项前缀（`- ` / `* ` / `• ` / `1. ` / `1) `）
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*+•]\s+|\d{1,2}[.)]\s+)")
# 表格行（审计跳过，表格单元格不是完整论断）
_TABLE_ROW_RE = re.compile(r"^\s*\|")
# 论断性表述线索（中英双语，高精度优先：命中即视为「这是一个需要证据的论断」）
_CLAIM_CUE_RE = re.compile(
    r"(?i)"
    # 经验性/对比性论断：需要实验或文献证据
    r"\b(?:results?|experiments?|evaluations?|findings?|ablations?)\s+"
    r"(?:show|shows|indicate|indicates|suggest|suggests|demonstrate|demonstrates|"
    r"confirm|confirms|reveal|reveals)\b"
    r"|\b(?:outperform\w*|surpass\w*|exceeds?|state[- ]of[- ]the[- ]art|sota)\b"
    r"|\b(?:significantly|substantially|remarkably|consistently)\s+"
    r"(?:outperform\w*|improv\w*|reduc\w*|increas\w*|decreas\w*|boost\w*|better|worse|higher|lower)\b"
    r"|\b(?:achieves?|improves?|reduces?|boosts?|increases?|decreases?)\s+[^.\n]{0,50}?\sby\s"
    # 文献立场句：不给出处就是无据引用
    r"|\b(?:prior|previous|earlier|existing|recent)\s+"
    r"(?:work|works|studies|study|research|methods?|systems?|approaches?)\b"
    r"|\b(?:studies|researchers|authors|work)\s+(?:have|has)\s+"
    r"(?:shown|demonstrated|found|reported|observed)\b"
    r"|\bit\s+has\s+been\s+(?:shown|demonstrated|reported|observed)\b"
    r"|\b(?:widely|commonly|extensively)\s+(?:used|adopted|studied|applied|believed|assumed)\b"
    # 中文对应
    r"|实验(?:表明|结果|显示|证明|验证)"
    r"|显著(?:提升|降低|提高|优于|减少|增加)"
    r"|(?:性能|精度|召回率|准确率|F1)[^。\n]{0,12}(?:达到|提升至|提高到)"
    r"|(?:现有|先前|已有|以往)(?:方法|工作|研究|系统|模型)[^。\n]{0,20}"
    r"(?:存在|难以|无法|受限于|面临)"
    r"|研究(?:表明|显示|发现)"
    r"|(?:被)?广泛(?:应用于|用于|采用)")
# 证据指针（Table/Figure/Equation/Section 等显式证据锚点，无引用角标也可作为支撑）
_EVIDENCE_POINTER_RE = re.compile(
    r"(?i)"
    r"\b(?:table|figure|fig\.?|equation|eq\.?|algorithm|section|appendix)\s*\.?\s*\d+"
    r"|\b(?:as\s+shown\s+in|as\s+reported\s+in|see|per)\s+"
    r"(?:table|figure|fig\.?|section|algorithm)\b"
    r"|[见表]\s*\d")
# 引用格式甄别（方括号族）
_CITE_GROUP_RE = re.compile(r"\[\s*\d{1,4}\s*,\s*\d{1,4}\s*\]")     # [1, 2]
_CITE_ADJACENT_RE = re.compile(r"\[\s*\d{1,4}\s*\]\s*\[\s*\d{1,4}\s*\]")  # [1][2]
_CITE_REMOVED_RE = re.compile(r"\[\s*\?\s*\]")                       # _sanitize_citations 打码后的 [?]
# 自一致性：缩略语定义 "RAG (Retrieval-Augmented Generation)"
_ACRONYM_DEF_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})\s*[（(]\s*([^()（）]{2,90})\s*[)）]")
_ACRONYM_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}\b")
_ACRONYM_STOP = {"AI", "API", "PDF", "CSV", "URL", "UI", "UX", "GPU", "CPU", "DOI",
                 "HTML", "SVG", "PNG", "JPG", "ID", "OK", "CI", "CD", "PR"}
# 自一致性：连字符/空格写法变体组（ML 论文高频，混用即不专业）
_TERM_VARIANT_GROUPS = (
    ("fine-tuning", ("fine-tuning", "fine tuning", "finetuning")),
    ("pre-training", ("pre-training", "pre training", "pretraining")),
    ("state-of-the-art", ("state-of-the-art", "state of the art")),
    ("real-time", ("real-time", "realtime")),
    ("end-to-end", ("end-to-end", "end to end")),
    ("out-of-vocabulary", ("out-of-vocabulary", "out of vocabulary")),
    ("open-source", ("open-source", "open source")),
    ("out-of-domain", ("out-of-domain", "out of domain")),
)
# 自一致性：本模块大纲生成的固定章节结构（与 _outline_prompt 的章节要求一致）
DEFAULT_REQUIRED_SECTIONS = ("Title", "Abstract", "Introduction", "Related Work",
                             "Method", "Experiments", "Threats to Validity", "Conclusion")
# 论证单元骨架的过渡句模板（按单元位置选取；中文/英文两套）
_TRANSITION_ZH = ("首先，<引出本节核心论点>。", "其次，<补充一层论证>。",
                  "进一步地，<深化论证或给出细节>。", "与此相对，<对比、反例或边界>。",
                  "综上，<小结并引出下一节>。")
_TRANSITION_EN = ("To begin with, <state the core claim of this section>.",
                  "Furthermore, <add one layer of support>.",
                  "In addition, <deepen the argument with detail>.",
                  "By contrast, <contrast, counter-example or boundary>.",
                  "In summary, <wrap up and lead to the next section>.")


def _body_without_references(text):
    """切掉 References 章节（含标题行），返回正文前缀。

    三个审计都只针对**正文**：References 段每行以 ``[n]`` 开头，若不切掉，
    条目编号会被误当成「文中引用角标」、条目文本会被误当成「论断」，全部误报。
    复用 :func:`_strip_generated_references`（同一套标题正则，行为一致）。
    """
    try:
        return _strip_generated_references(text)[0]
    except Exception:
        return str(text or "")


def _norm_heading(title):
    """标题归一化：小写 → 去首级编号（``2.1`` / ``2)`` / ``2 ``）→ 去标点 → 合并空白。"""
    t = str(title or "").strip().lower()
    t = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _add_issue(bucket, severity, kind, detail):
    """向审计结果里追加一条问题（severity: error / warning / info）。"""
    try:
        bucket["issues"].append({"severity": severity, "kind": kind,
                                 "detail": str(detail)[:300]})
        bucket["n_" + severity] += 1
        if severity == "error":
            bucket["ok"] = False
    except Exception:
        pass


# ---------------------------------------------------------------- 1) 论断-证据对齐（claim-evidence coverage）
def claim_evidence_audit(text, refs_list=None, max_items=40, max_lines=4000):
    """论断-证据对齐审计：每个论断性表述都必须有引用角标或证据指针，否则显式列出。

    借鉴 rasha.me《Reliable AI Writing Systems》Stage 3 "Deterministic Coverage
    Check"（零模型调用、零成本的确定性覆盖检查）与 Paperpal Reference Checker 的
    Citation Relevance / Missing Reference Match。既有白名单校验回答的是
    「引用是不是真的」，本审计回答的是「**论断有没有证据**」——两者互补。

    判定规则（确定性，保守优先，宁漏勿错杀）：
    - ``supported``：单元内含白名单内引用角标 ``[n]``（无白名单时任一 ``[n]`` 即可），
      或含 Table/Figure/Equation/Section 等**证据指针**；
    - ``bad_citation``（error）：有角标但全部越界（白名单场景下的幻觉角标）；
    - ``needs_evidence``（warning）：命中论断线索（"results show"/"existing
      systems"/"实验表明"/"显著提升"…）但既无引用也无证据指针 → **待补依据**；
    - ``background``：动机/方法设计/过渡等无需外部证据的陈述（如 "We propose X"
      是贡献声明不是经验论断，刻意不判为缺证据，避免审计噪音）。

    References 章节整体剔除（条目编号/文本不算文中引用与论断）。

    :param text: 待审计文本（大纲正文或最终全文均可）
    :param refs_list: 白名单文献列表；``None``/空列表 = 不做范围校验
    :return: ``{"ok","n_units","n_supported","n_needs_evidence","n_background",
      "n_bad_citation","n_out_of_range","sections","needs_evidence","report"}``；
      ``ok`` 仅对 error 级（坏角标）为 False，warning 级（待补依据）不影响 ok。
    """
    result = {"ok": True, "n_units": 0, "n_supported": 0, "n_needs_evidence": 0,
              "n_background": 0, "n_bad_citation": 0, "n_out_of_range": 0,
              "sections": [], "needs_evidence": [], "report": ""}
    try:
        n_refs = len(refs_list or [])
        body = _body_without_references(text)
        lines = str(body or "").splitlines()[:max_lines]
        section = "（文首）"
        sec_stat = OrderedDict()
        flagged = []
        for ln, raw in enumerate(lines, 1):
            line = raw.rstrip()
            if not line.strip() or _TABLE_ROW_RE.match(line):
                continue
            m_h = _HEADING_LINE_RE.match(line)
            if m_h:
                title = (m_h.group("md") or m_h.group("bold") or "").strip(" *:")
                if title:
                    section = title[:60]
                continue
            unit = _BULLET_PREFIX_RE.sub("", line).strip()
            if len(unit) < 4:
                continue
            cites = [int(x) for x in _CITE_MARK_RE.findall(unit)]
            in_range = [n for n in cites if n_refs == 0 or 1 <= n <= n_refs]
            out_range = [n for n in cites if n not in in_range]
            result["n_out_of_range"] += len(out_range)
            has_pointer = bool(_EVIDENCE_POINTER_RE.search(unit))
            has_claim = bool(_CLAIM_CUE_RE.search(unit))
            if in_range or has_pointer:
                status = "supported"
            elif cites:
                status = "bad_citation"
            elif has_claim:
                status = "needs_evidence"
            else:
                status = "background"
            st = sec_stat.setdefault(section, {"section": section, "n_units": 0,
                                               "n_supported": 0, "n_needs_evidence": 0})
            st["n_units"] += 1
            if status == "supported":
                st["n_supported"] += 1
                result["n_supported"] += 1
            elif status == "needs_evidence":
                st["n_needs_evidence"] += 1
                result["n_needs_evidence"] += 1
                flagged.append({"severity": "warning", "section": section,
                                "line_no": ln, "text": unit[:180],
                                "reason": "含论断性表述，但既无白名单内引用角标 [n]，"
                                          "也无 Table/Figure/式 等证据指针（待补依据）"})
            elif status == "bad_citation":
                result["n_bad_citation"] += 1
                flagged.append({"severity": "error", "section": section,
                                "line_no": ln, "text": unit[:180],
                                "reason": f"引用角标 {out_range} 超出真实文献范围 "
                                          f"[1..{n_refs}]（幻觉角标）"})
            else:
                result["n_background"] += 1
        result["n_units"] = sum(s["n_units"] for s in sec_stat.values())
        result["sections"] = list(sec_stat.values())
        result["needs_evidence"] = [f for f in flagged if f["severity"] == "warning"][:max_items]
        result["ok"] = result["n_bad_citation"] == 0
        report = ["### 论断-证据对齐审计（确定性 · 零 LLM · 零网络）", "",
                  f"共扫描 {result['n_units']} 个论断单元：有证据支撑 {result['n_supported']} · "
                  f"待补依据 {result['n_needs_evidence']} · 背景陈述 {result['n_background']} · "
                  f"坏角标 {result['n_bad_citation']}", ""]
        if flagged:
            for it in flagged[:15]:
                tag = "🚨" if it["severity"] == "error" else "⚠️"
                report.append(f"- {tag} [{it['section']}] 第 {it['line_no']} 行："
                              f"「{it['text']}」——{it['reason']}")
            if len(flagged) > 15:
                report.append(f"- …另有 {len(flagged) - 15} 条未展示")
        else:
            report.append("✅ 未发现「有论断无证据」或「越界角标」的单元。")
        result["report"] = "\n".join(report)
        return result
    except Exception as e:
        result["ok"] = False
        result["report"] = (f"⚠️ 论断-证据对齐审计自身异常"
                            f"（{type(e).__name__}: {str(e)[:120]}），本次结果未核实。")
        return result


# ---------------------------------------------------------------- 2) 引用-参考文献双向核对
def citation_correspondence_audit(text, refs_list):
    """引用-参考文献双向核对：文中角标 ↔ 文献清单必须一一对应（Paperpal 式）。

    借鉴 Paperpal Manuscript Checker 的 "Citation-Reference Correspondence"
    （消除 in-text citation 与 reference list 的漏引/多引/格式混用）。检查四项：
    1. **越界角标**（error）：正文出现白名单范围之外的 ``[n]``；
    2. **漏引文献**（warning）：白名单里存在但正文从未引用的条目——读者会问
       「列了为什么不引」，要么补引用要么从清单移除；
    3. **引用格式混用**（warning）：``[n]`` / ``[n, m]`` / ``[n][m]`` 多种风格
       混用（期刊格式一致性问题，Paperpal Formatting inconsistencies 同款）；
    4. **被清洗的幻觉角标**（info）：正文残留的 ``[?]`` 数量——
       :func:`_sanitize_citations` 曾把越界角标打码的地方，必须人工复核。

    :return: ``{"ok","n_cited","n_refs","cited_ids","out_of_range","uncited_refs",
      "styles","mixed_style","removed_marks","report"}``
    """
    result = {"ok": True, "n_cited": 0, "n_refs": 0, "cited_ids": [], "out_of_range": [],
              "uncited_refs": [], "styles": [], "mixed_style": False, "removed_marks": 0,
              "report": ""}
    try:
        refs = list(refs_list or [])
        n_refs = len(refs)
        result["n_refs"] = n_refs
        body = _body_without_references(text)
        marks = [int(x) for x in _CITE_MARK_RE.findall(body or "")]
        cited = sorted(set(marks))
        result["cited_ids"] = cited
        result["n_cited"] = len(cited)
        if n_refs:
            result["out_of_range"] = [n for n in cited if n < 1 or n > n_refs]
            cited_set = set(cited)
            uncited = []
            for i, r in enumerate(refs, 1):
                if i not in cited_set:
                    title = str(r.get("title") or r.get("raw") or "") if isinstance(r, dict) else str(r)
                    uncited.append({"id": i, "title": title[:120]})
            result["uncited_refs"] = uncited
        styles = ["方括号 [n]"] if marks else []
        if _CITE_GROUP_RE.search(body or ""):
            styles.append("成组 [n, m]")
        if _CITE_ADJACENT_RE.search(body or ""):
            styles.append("并排 [n][m]")
        result["styles"] = styles
        result["mixed_style"] = len(styles) > 1
        result["removed_marks"] = len(_CITE_REMOVED_RE.findall(body or ""))
        result["ok"] = not result["out_of_range"]
        report = ["### 引用-参考文献双向核对（确定性 · 零 LLM · 零网络）", "",
                  f"白名单文献 {n_refs} 条；正文实际引用 {len(cited)} 条"
                  + (f"（编号：{', '.join('[%d]' % n for n in cited[:20])}"
                     f"{' …' if len(cited) > 20 else ''}）" if cited else "（正文没有任何引用角标）"),
                  ""]
        if result["out_of_range"]:
            report.append(f"- 🚨 越界角标：{result['out_of_range']}"
                          f"（真实文献范围 [1..{n_refs}]）")
        else:
            report.append("- ✅ 无越界角标。")
        if result["uncited_refs"]:
            names = "；".join(f"[{u['id']}] {u['title']}" for u in result["uncited_refs"][:8])
            more = f" …等共 {len(result['uncited_refs'])} 条" if len(result["uncited_refs"]) > 8 else ""
            report.append(f"- ⚠️ 漏引文献 {len(result['uncited_refs'])} 条：{names}{more}"
                          "（列在 References 却从未被正文引用）")
        if result["mixed_style"]:
            report.append(f"- ⚠️ 引用格式混用：{' + '.join(styles)}（建议统一为一种）")
        if result["removed_marks"]:
            report.append(f"- ℹ️ 正文残留 {result['removed_marks']} 处 [?] 打码角标"
                          "（曾被判定越界并替换，需人工复核）")
        result["report"] = "\n".join(report)
        return result
    except Exception as e:
        result["ok"] = False
        result["report"] = (f"⚠️ 引用-参考文献双向核对自身异常"
                            f"（{type(e).__name__}: {str(e)[:120]}），本次结果未核实。")
        return result


# ---------------------------------------------------------------- 3) 文档内自一致性（章节漂移 + 术语一致）
def consistency_audit(text, required_sections=None):
    """文档内自一致性审计：章节漂移、顺序、术语变体、缩略语冲突、编号、引号风格。

    借鉴 rasha.me Stage 6 "Intra-Document Consistency Check"（术语漂移与跨节
    矛盾的整文档记忆外置）与中文长文写作方法论的「跨章节检查」：同一概念前后
    定义不同、同一数据两个版本、章节顺序漂移，都是大模型逐段生成的典型病症。
    全部确定性检查，零 LLM。

    检查项与严重度：
    - ``section_missing``（error）：缺少必需章节（默认 :data:`DEFAULT_REQUIRED_SECTIONS`，
      与本模块大纲 prompt 的固定章节一致；传 ``required_sections=[]`` 可关闭）；
    - ``section_order``（warning）：必需章节出现顺序与预期不符；
    - ``extra_section``（info）：出现固定结构之外的章节（References 段不算）；
    - ``term_variant``（warning）：连字符/空格写法混用（fine-tuning/finetuning）；
    - ``acronym_conflict``（warning）：同一缩略语出现两个不同释义；
    - ``acronym_undefined``（info）：缩略语使用了却从未给出释义（截断展示）；
    - ``heading_numbering``（warning）：标题编号倒流或跳号；
    - ``quote_style``（info）：直引号与弯引号混用。

    :return: ``{"ok","issues","n_error","n_warning","n_info","sections_found",
      "missing_sections","report"}``；``ok`` 仅对 error 级为 False。
    """
    result = {"ok": True, "issues": [], "n_error": 0, "n_warning": 0, "n_info": 0,
              "sections_found": [], "missing_sections": [], "report": ""}
    try:
        body = _body_without_references(text)
        src = str(body or "")
        if required_sections is None:
            required = list(DEFAULT_REQUIRED_SECTIONS)
        else:
            required = [str(s) for s in required_sections]
        req_norm = [_norm_heading(s) for s in required]

        # ---- 1) 收集标题 ----
        headings = []
        for ln, raw in enumerate(src.splitlines(), 1):
            m = _HEADING_LINE_RE.match(raw.rstrip())
            if not m:
                continue
            title = (m.group("md") or m.group("bold") or "").strip(" *:")
            if not title:
                continue
            mnum = re.match(r"^\s*(\d+)(?:\.\d+)*[.)]?\s", title)
            headings.append({"line_no": ln, "title": title[:80],
                             "norm": _norm_heading(title),
                             "num": int(mnum.group(1)) if mnum else None})

        # ---- 2) 必需章节：缺失（error）/ 乱序（warning）/ 额外章节（info）----
        found = {}
        for hi, h in enumerate(headings):
            if not h["norm"]:
                continue
            for ri, rn in enumerate(req_norm):
                if ri in found or not rn:
                    continue
                shorter, longer = ((h["norm"], rn) if len(h["norm"]) <= len(rn)
                                   else (rn, h["norm"]))
                if len(shorter) >= 4 and shorter in longer:
                    found[ri] = hi
                    break
        result["sections_found"] = [required[ri] for ri in sorted(found, key=lambda r: found[r])]
        missing = [required[ri] for ri in range(len(required)) if ri not in found]
        result["missing_sections"] = missing
        for name in missing:
            _add_issue(result, "error", "section_missing", f"缺少必需章节「{name}」")
        appear = sorted(found, key=lambda ri: found[ri])
        if appear != sorted(appear):
            for a, b in zip(appear, appear[1:]):
                if a > b:
                    _add_issue(result, "warning", "section_order",
                               f"章节顺序与固定结构不符：「{required[b]}」出现在"
                               f"「{required[a]}」之前")
                    break
        ref_norms = {"references", "bibliography", "works cited", "参考文献"}
        extras = [h["title"] for h in headings
                  if h["norm"] not in ref_norms
                  and not any(len(s) >= 4 and (s in h["norm"] or h["norm"] in s)
                              for s in req_norm if s)]
        if extras:
            _add_issue(result, "info", "extra_section",
                       "出现固定结构之外的章节：" + "、".join(extras[:8])
                       + (" …" if len(extras) > 8 else ""))

        # ---- 3) 术语写法变体混用 ----
        for canon, variants in _TERM_VARIANT_GROUPS:
            counts = []
            for v in variants:
                c = len(re.findall(r"(?<!\w)%s(?!\w)" % re.escape(v), src, re.IGNORECASE))
                if c:
                    counts.append((v, c))
            if len(counts) >= 2:
                _add_issue(result, "warning", "term_variant",
                           "术语写法混用：" + "、".join(f"「{v}」×{c}" for v, c in counts)
                           + f"（建议统一为「{canon}」）")

        # ---- 4) 缩略语：定义冲突（warning）+ 使用了却没给释义（info）----
        defs = {}
        for m in _ACRONYM_DEF_RE.finditer(src):
            acro, exp = m.group(1), m.group(2)
            if not re.search(r"[A-Za-z]{3}", exp):
                continue                      # "(2023)" 这类不是释义
            norm_exp = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", exp.lower()).strip()
            if norm_exp:
                defs.setdefault(acro.upper(), [])
                if norm_exp not in defs[acro.upper()]:
                    defs[acro.upper()].append(norm_exp)
        for acro, exps in defs.items():
            if len(exps) >= 2:
                _add_issue(result, "warning", "acronym_conflict",
                           f"缩略语 {acro} 有多个不同释义："
                           + " / ".join(f"「{e}」" for e in exps[:3]))
        used = {}
        for m in _ACRONYM_TOKEN_RE.finditer(src):
            t = m.group(0)
            if t in _ACRONYM_STOP or t in defs:
                continue
            used[t] = used.get(t, 0) + 1
        if used:
            top = sorted(used.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
            _add_issue(result, "info", "acronym_undefined",
                       "以下缩略语未在文中给出释义："
                       + "、".join(f"{t}×{c}" for t, c in top))

        # ---- 5) 标题编号连续性（只看首级编号，兼容 1 / 1.1 / 2 的层级）----
        nums = [h["num"] for h in headings if h["num"] is not None]
        if nums:
            back = next(((a, b) for a, b in zip(nums, nums[1:]) if b < a), None)
            if back:
                _add_issue(result, "warning", "heading_numbering",
                           f"标题编号倒流：{back[0]} → {back[1]}")
            else:
                distinct = sorted(set(nums))
                gap = next(((a, b) for a, b in zip(distinct, distinct[1:]) if b > a + 1), None)
                if gap:
                    _add_issue(result, "warning", "heading_numbering",
                               f"标题编号跳号：{gap[0]} → {gap[1]}")

        # ---- 6) 引号风格混用 ----
        straight = src.count('"')
        curly = src.count("“") + src.count("”")
        if straight and curly:
            _add_issue(result, "info", "quote_style",
                       f"直引号 \" ×{straight} 与弯引号 ×{curly} 混用")

        report = ["### 文档内自一致性审计（确定性 · 零 LLM · 零网络）", "",
                  f"标题 {len(headings)} 个；必需章节命中 {len(found)}/{len(required)}；"
                  f"问题：error {result['n_error']} · warning {result['n_warning']} · "
                  f"info {result['n_info']}", ""]
        if result["issues"]:
            icon = {"error": "🚨", "warning": "⚠️", "info": "ℹ️"}
            for it in result["issues"][:15]:
                report.append(f"- {icon[it['severity']]} [{it['kind']}] {it['detail']}")
            if len(result["issues"]) > 15:
                report.append(f"- …另有 {len(result['issues']) - 15} 条未展示")
        else:
            report.append("✅ 未发现一致性问题。")
        result["report"] = "\n".join(report)
        return result
    except Exception as e:
        result["ok"] = False
        result["report"] = (f"⚠️ 自一致性审计自身异常"
                            f"（{type(e).__name__}: {str(e)[:120]}），本次结果未核实。")
        return result


# ---------------------------------------------------------------- 4) 大纲→段落展开骨架（论证单元）
def paragraph_skeleton(section_title, bullets, refs_list=None, max_units=10):
    """把大纲小节展开为「论点句 → 证据 → 过渡」论证单元骨架（确定性模板，零 LLM）。

    借鉴 SciSpace / Jenni AI 的 section-aware drafting 与「论证单元」写作模板
    （每个单元 = 直接结论 + 解释 + 证据 + 边界/过渡）：先把结构钉死，再让 LLM
    或作者只往槽位里填内容——比「把大纲整段丢给模型自由发挥」更可控，也让
    claim-evidence 对齐在**写作前**就生效（没有证据的单元在骨架上就带着
    「待补依据」标记）。

    槽位规则：
    - **论点句**：要点原文（已去列表符号）；
    - **证据**：要点里的 ``[n]`` 角标映射为白名单文献条目（:func:`_ref_line`
      同源渲染）；越界角标显式标注「按幻觉处理」；无角标 → 「待补依据」；
    - **过渡**：按单元位置（首/中/末）从确定性模板选取，中段循环不重复；
    - 语言自适应：要点含中文 → 中文脚手架，否则英文脚手架。

    :param bullets: 要点列表，或一段 Markdown 文本（按行拆分、自动去列表符号）
    :return: ``{"section","n_units","n_with_evidence","n_pending","units","markdown"}``
    """
    out = {"section": str(section_title or "").strip()[:80], "n_units": 0,
           "n_with_evidence": 0, "n_pending": 0, "units": [], "markdown": ""}
    try:
        if isinstance(bullets, str):
            raw_lines = bullets.splitlines()
        else:
            raw_lines = [str(b) for b in (bullets or [])]
        items = []
        for raw in raw_lines:
            line = str(raw or "").strip()
            if not line or _HEADING_LINE_RE.match(line) or _TABLE_ROW_RE.match(line):
                continue
            item = _BULLET_PREFIX_RE.sub("", line).strip()
            if len(item) >= 4:
                items.append(item)
        items = items[:max_units]
        if not items:
            out["markdown"] = f"### {out['section']}\n\n（无可展开的要点：输入为空或全是标题行）"
            return out
        zh = any(re.search(r"[\u4e00-\u9fff]", it) for it in items)
        lab = ({"unit": "单元", "claim": "论点句", "evidence": "证据", "transition": "过渡"}
               if zh else
               {"unit": "Unit", "claim": "Topic sentence", "evidence": "Evidence",
                "transition": "Transition"})
        trans = _TRANSITION_ZH if zh else _TRANSITION_EN
        refs = list(refs_list or [])
        n_refs = len(refs)
        lines = [f"### {out['section']}｜{lab['unit']}骨架（{len(items)} 个{lab['unit']}）", "",
                 ("> 用法：每个单元按「论点句 → 证据 → 过渡」展开为一个段落；"
                  "标注「待补依据」的单元必须补一条白名单内文献，"
                  "或明确改写为作者判断/方法设计说明（无需引用）后才能定稿。"
                  if zh else
                  "> Usage: expand each unit into one paragraph (claim -> evidence -> "
                  "transition); units marked 'pending evidence' must gain a whitelist "
                  "reference or be rewritten as author judgment before finalizing."),
                 ""]
        n_with_evidence = 0
        for i, item in enumerate(items):
            cites = [int(x) for x in _CITE_MARK_RE.findall(item)]
            ev_lines, has_ev, pending = [], False, False
            for n in cites:
                if 1 <= n <= n_refs:
                    ev_lines.append(_ref_line(n, refs[n - 1]))
                    has_ev = True
                else:
                    ev_lines.append(f"（角标 [{n}] 超出白名单范围 [1..{n_refs}]，"
                                    f"按幻觉处理，需删除或更正）")
                    pending = True
            if not cites:
                pending = True
            if pending and not ev_lines:
                ev_lines.append("待补依据：尚未标注引用——补一条白名单内文献，"
                                "或改写为作者判断（无需引用）" if zh else
                                "Pending evidence: no citation yet — add a whitelist "
                                "reference, or rewrite as author judgment (no citation needed)")
            if i == 0:
                t = trans[0]
            elif i == len(items) - 1:
                t = trans[-1]
            else:
                t = trans[1 + (i - 1) % max(1, len(trans) - 2)]
            lines.append(f"**{lab['unit']} {i + 1}｜{lab['claim']}**：{item}")
            lines.append(f"**{lab['evidence']}**：")
            for e in ev_lines:
                lines.append(f"- {e}")
            lines.append(f"**{lab['transition']}**：{t}")
            lines.append("")
            out["units"].append({"claim": item, "evidence": ev_lines,
                                 "transition": t, "pending": pending})
            if has_ev:
                n_with_evidence += 1
        out["n_units"] = len(items)
        out["n_with_evidence"] = n_with_evidence
        out["n_pending"] = len(items) - n_with_evidence
        out["markdown"] = "\n".join(lines).rstrip() + "\n"
        return out
    except Exception as e:
        out["markdown"] = f"⚠️ 骨架生成异常（{type(e).__name__}: {str(e)[:120]}）"
        return out


# ---------------------------------------------------------------- 5) 组合入口（UI/工具层单点调用）
def writing_quality_audit(text, refs_list=None, required_sections=None):
    """组合入口：一次跑齐「论断-证据对齐 + 引用双向核对 + 自一致性」三个确定性审计。

    建议调用点（本函数不调 LLM，可放在任何生成后处理之后）：
    ``ui/page_writing.py`` 在 ``writing.finalize_outline(...)`` 之后、展示
    ``res['text']`` 的同一区块内追加本报告——三个审计共享同一份最终全文，
    与既有白名单校验互为补充：白名单管「引用是不是真的」，本组合管
    「论断有没有证据 / 引用与清单是否一一对应 / 章节术语是否自洽」。

    :return: ``{"ok","n_error","n_warning","claim_evidence","citation_correspondence",
      "consistency","report"}``；三个子审计任一自身异常都会反映在 report 里。
    """
    out = {"ok": False, "n_error": 0, "n_warning": 0, "claim_evidence": None,
           "citation_correspondence": None, "consistency": None, "report": ""}
    try:
        ce = claim_evidence_audit(text, refs_list)
        cc = citation_correspondence_audit(text, refs_list) if (refs_list or []) else None
        co = consistency_audit(text, required_sections)
        n_error = ce["n_bad_citation"] + co["n_error"]
        n_warning = ce["n_needs_evidence"] + co["n_warning"]
        if cc is not None:
            n_error += len(cc["out_of_range"])
            n_warning += len(cc["uncited_refs"]) + (1 if cc["mixed_style"] else 0)
        ok = n_error == 0
        parts = ["## 写作质量审计（确定性 · 零 LLM · 零网络）", "", ce["report"]]
        if cc is not None:
            parts += ["", cc["report"]]
        parts += ["", co["report"], "",
                  f"**汇总**：错误 {n_error} 项 · 警告 {n_warning} 项"
                  + ("——全部通过，可进入人工终审。" if ok
                     else "——存在错误级问题，需处理后才能定稿。")]
        out.update({"ok": ok, "n_error": n_error, "n_warning": n_warning,
                    "claim_evidence": ce, "citation_correspondence": cc,
                    "consistency": co, "report": "\n".join(parts)})
        return out
    except Exception as e:
        out["report"] = (f"⚠️ 写作质量审计自身异常"
                         f"（{type(e).__name__}: {str(e)[:160]}），本次结果未核实。")
        return out

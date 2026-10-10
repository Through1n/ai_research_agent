"""参考文献含金量打分（纯规则版，未联网）。

打分维度：arXiv 标识、DOI、发表年份新旧、顶会/顶刊命中。
当前实现为本地正则规则，不调用任何外部 API、不依赖 streamlit；
后续可无缝替换为 semantic_scholar / openalex API 版本，
保持 score_reference / score_references 的函数签名与返回结构不变。
"""
import re

# arXiv：兼容 "arXiv:2301.00001" 与独立编号 "2301.00001"
_ARXIV_RE = re.compile(r"arXiv\s*:\s*\d{4}\.\d{4,5}|\b\d{4}\.\d{4,5}\b")
# DOI：10.xxxx/xxxxx
_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# 顶会/顶刊子串（大小写不敏感匹配）
_TOP_VENUES = (
    "nature", "science", "cell",
    "neurips", "nips", "icml", "iclr",
    "acl", "emnlp", "naacl",
    "cvpr", "iccv", "eccv",
    "kdd", "www", "aaai", "ijcai", "sigir",
    "tpami", "jmlr",
)


def _coerce(ref):
    """归一输入为 (可检索文本块, 显式年份, 发表 venue 字符串)。"""
    if isinstance(ref, dict):
        title = str(ref.get("title", "") or "")
        authors = str(ref.get("authors", "") or "")
        venue = str(ref.get("venue", "") or "")
        return f"{title} {authors} {venue}", ref.get("year"), venue
    return str(ref), None, str(ref)


def _year_score(blob, year, current_year):
    """返回 (分值, recency_years, 原因)。年份缺失时从原文兜底抠 4 位年份。"""
    y = None
    try:
        if year not in (None, ""):
            m = re.search(r"\d{4}", str(year))
            if m:
                y = int(m.group(0))
    except Exception:
        y = None
    if y is None:
        m = _YEAR_RE.search(blob)
        if m:
            y = int(m.group(0))
    if y is None:
        return 0, None, "年份缺失 (+0)"
    age = max(0, current_year - y)
    if age <= 3:
        return 25, age, f"近 3 年发表 ({y}, +25)"
    if age <= 5:
        return 15, age, f"近 5 年发表 ({y}, +15)"
    if age <= 10:
        return 5, age, f"5-10 年前发表 ({y}, +5)"
    return 0, age, f"发表超过 10 年 ({y}, +0)"


def score_reference(ref, *, current_year=2026):
    """对单条参考文献做含金量打分。

    ref 为 dict 时读取 title/authors/year/venue；为 str 时整体作为原文处理。
    返回 {"score","grade","reasons","has_arxiv","has_doi","is_top_venue","recency_years"}。
    """
    blob, year, venue = _coerce(ref)
    reasons = []
    score = 0

    has_arxiv = bool(_ARXIV_RE.search(blob))
    if has_arxiv:
        score += 20
        reasons.append("含 arXiv 标识 (+20)")

    has_doi = bool(_DOI_RE.search(blob))
    if has_doi:
        score += 20
        reasons.append("含 DOI (+20)")

    y_score, recency_years, y_reason = _year_score(blob, year, current_year)
    score += y_score
    reasons.append(y_reason)

    venue_blob = (venue or "").lower() or blob.lower()
    is_top_venue = any(v in venue_blob for v in _TOP_VENUES)
    if is_top_venue:
        score += 30
        reasons.append("命中顶会/顶刊 (+30)")

    score = min(100, score)
    grade = "A" if score >= 80 else ("B" if score >= 50 else "C")
    return {
        "score": score,
        "grade": grade,
        "reasons": reasons,
        "has_arxiv": has_arxiv,
        "has_doi": has_doi,
        "is_top_venue": is_top_venue,
        "recency_years": recency_years,
    }


def score_references(refs, *, current_year=2026):
    """批量打分入口：refs 为 dict/str 列表，返回与输入等长的结果列表。"""
    return [score_reference(r, current_year=current_year) for r in (refs or [])]

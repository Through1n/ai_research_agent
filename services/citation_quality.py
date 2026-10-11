"""参考文献含金量评估：真实被引数据（OpenAlex → Semantic Scholar → Crossref 三级降级）
+ 离线 CCF / 水刊 venue 字典 + 结构化打分。

工程要点（对应赛题 C-#9「引用含金量」）：
- fetch_citation_count 三级降级全部走真实公开学术 API：
    OpenAlex（cited_by_count） → Semantic Scholar（citationCount） → Crossref（is-referenced-by-count）；
  每级 8s 超时 + try/except，**全部失败返回 None 并给出原因，绝不返回编造数字**；
- venue_tier 使用内置离线等级字典（CCF-A/B/C + 预印本 + 水刊黑名单），未命中返回"未知"并说明；
- assess_reference 综合 (a) 真实被引数 (b) venue 等级 (c) 是否仅在 Related Work 凑数
  (d) 与引用方正文的主题相关性，输出 {level, real_citations, venue_tier, reasons}；
- 内存缓存 + 失败 TTL，避免同一条参考文献反复打网络。

向后兼容：score_reference / score_references（纯离线规则版）签名与返回结构保持不变，
tools/definitions.py 的 t_citation_quality 仍可调用。
"""
import re
import json
import time
import difflib
import urllib.request
import urllib.parse
import urllib.error

# ---------------- 常量（如需调整只改这里） ----------------
HTTP_TIMEOUT = 8              # 每个 API 单次请求超时（秒）
FAILURE_TTL = 300             # 失败缓存的有效期（秒），过期后允许重试
TITLE_MATCH_MIN = 0.60        # 返回条目与查询标题的最低相似度，低于此值视为未命中
TITLE_STRONG_MIN = 0.85       # 「高置信标题匹配」阈值：达到此值时进入「被引数优先」选优
# 低置信判据：当选中的被引数 < 候选池最大被引数的 2%、自身 < 10 且池里存在 >=1000 的记录，
# 说明很可能抓到了同名的噪声/影印记录（如 AlexNet 的 760 vs 108512）。
LOW_CONF_RATIO = 0.02
LOW_CONF_ABS_MIN = 10
LOW_CONF_POOL_MIN = 1000
# 同年/相似标题时的次要权重：只用于**被引数相同**时的排序，不能反超被引数（见 _pick_best）
YEAR_BONUS = 0.10
AUTHOR_BONUS_STEP = 0.075
AUTHOR_BONUS_MAX = 0.15
USER_AGENT = "AI4S-Research-Agent/1.0 (academic citation quality check)"
OPENALEX_MAILTO = ""          # 可选：填入邮箱可进 OpenAlex polite pool（提高限流额度）

# ---------------- 缓存 ----------------
_CACHE = {}      # norm_title -> {"ts":float, "ok":bool, "data":dict|None, "err":str}
_TITLE_CACHE = {}  # 归一化标题缓存


def _norm(s):
    key = s or ""
    if key in _TITLE_CACHE:
        return _TITLE_CACHE[key]
    v = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", key.lower())).strip()
    _TITLE_CACHE[key] = v
    return v


def _cache_get(key):
    hit = _CACHE.get(key)
    if not hit:
        return None
    if hit["ok"]:
        return hit
    if time.time() - hit["ts"] < FAILURE_TTL:
        return hit          # 近期失败，直接复用降级结论，避免雪崩
    return None


def _cache_put(key, ok, data=None, err=""):
    _CACHE[key] = {"ts": time.time(), "ok": ok, "data": data, "err": err}


def fetch_error(title):
    """查询某标题最近一次抓取失败的原因（供 UI/日志展示，不做任何猜测性补全）。"""
    hit = _CACHE.get(_norm(title))
    if hit and not hit["ok"]:
        return hit["err"] or "未知原因"
    return ""


def clear_cache():
    _CACHE.clear()


# ---------------- HTTP ----------------
# 同一进程内相邻两次 HTTP 请求的最小间隔（秒）。
# 实测：不做节流时连续查多条参考文献会被 OpenAlex 直接 429 限流，导致整批降级到下一级数据源。
MIN_REQUEST_INTERVAL = 0.5
_LAST_REQUEST = [0.0]


def _http_json(url, timeout=HTTP_TIMEOUT, headers=None):
    """GET 一个 JSON 接口，统一 UA/超时/**节流**/异常；失败抛异常由上层捕获。"""
    wait = MIN_REQUEST_INTERVAL - (time.time() - _LAST_REQUEST[0])
    if wait > 0:
        time.sleep(wait)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json",
                                                   **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="ignore")
        return json.loads(raw)
    finally:
        _LAST_REQUEST[0] = time.time()


# ---------------- 标题清洗与候选生成 ----------------
def _search_title(raw):
    """把参考文献原文收敛成干净的检索标题：去掉年份/venue/括号/arXiv/DOI/逗号。

    OpenAlex 的 filter 值不允许出现逗号（即使 percent-encode 也会被 edge 拒绝），
    且检索串里夹带作者/年份会显著拉低标题相似度，故统一清洗。
    """
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    s = re.sub(r"\bet al\.?", " ", s)
    s = re.sub(r"arXiv\s*:\s*\d{4}\.\d{4,5}|\b\d{4}\.\d{4,5}\b|10\.\d{4,9}/\S+", " ", s)
    s = re.split(r"\b(?:19|20)\d{2}\b", s)[0]        # 截断到年份之前
    s = re.sub(r"\([^)]*\)", " ", s)                 # 去括号（多为会议缩写/编号）
    s = re.sub(r"[^A-Za-z0-9 \-:]", " ", s)          # 去标点（含逗号）
    return re.sub(r"\s+", " ", s).strip()[:160]


def _candidate_titles(raw):
    """生成检索用标题变体（最多 4 个）：整体清洗串 → 年份**之后**的句段 → 年份之前的句段。

    必须同时覆盖两种版式：
    - ``Authors. Title. Venue, Year.``：标题在年份**之前**（旧逻辑只支持这一种）；
    - ``Authors. Year. Title. Venue.``（ACL/arXiv 常见，BERT 论文即如此）：标题在年份**之后**，
      若只在年份处截断并把前面当标题，检索串会退化成"纯作者名"，相似度必然不足。
    """
    cands = []
    full = _search_title(raw)
    if full:
        cands.append(full)
    # 与 _search_title 相同的清洗，但不做「截断到年份之前」，以便分别取年份两侧
    base = re.sub(r"\bet al\.?", " ", re.sub(r"\s+", " ", str(raw or ""))).strip()
    base = re.sub(r"arXiv\s*:\s*\d{4}\.\d{4,5}|\b\d{4}\.\d{4,5}\b|10\.\d{4,9}/\S+", " ", base)
    base = re.sub(r"\([^)]*\)", " ", base)
    parts = re.split(r"\b(?:19|20)\d{2}\b", base, maxsplit=1)
    for part in (parts[1] if len(parts) > 1 else "", parts[0]):
        for p in re.split(r"(?<=[.?!])\s+|\s{2,}", part):
            p = re.sub(r"[.,;:]*\s+(?:In|In:)\s+[^.]{3,40}$", "", p.strip(" .;:,"))  # 去掉尾部 venue
            if len(p.split()) < 4:      # 太短的片段多半是作者名碎片，白白消耗请求额度
                continue
            c = _search_title(p.strip(" .;:,"))
            if c and c not in cands:
                cands.append(c)
            if len(cands) >= 4:
                break
        if len(cands) >= 4:
            break
    return cands or [str(raw)[:160]]


def _sim(a, b):
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _score_components(title, cand, year=None, authors=None):
    """返回 (sim, combined)：sim=标题归一化相似度，combined=sim+年份/作者次要加成。"""
    sim = _sim(title, cand.get("title", ""))
    combined = sim
    if year and cand.get("year"):
        try:
            if abs(int(cand["year"]) - int(year)) <= 1:
                combined += YEAR_BONUS
        except (TypeError, ValueError):
            pass
    if not authors or not cand.get("authors"):
        return sim, combined
    q = _norm(authors).split()
    c = _norm(" ".join(str(x) for x in cand["authors"])).split()
    if q and c:
        hit = sum(1 for w in q if len(w) > 3 and w in c)
        combined += min(AUTHOR_BONUS_MAX, AUTHOR_BONUS_STEP * hit)
    return sim, combined


def _extract_ids(raw):
    """从参考文献原文里抽出 DOI / arXiv ID，供精确匹配使用（命中失败则回退标题模糊匹配）。"""
    out = {"doi": "", "arxiv": ""}
    s = str(raw or "")
    m = re.search(r"(10\.\d{4,9}/[^\s,\]\)]+)", s)
    if m:
        out["doi"] = m.group(1).rstrip(".,;)")
    m = re.search(r"arXiv\s*:\s*(\d{4}\.\d{4,5})(v\d+)?", s, re.I)
    if m:
        out["arxiv"] = m.group(1)
    return out


def _count_of(cand):
    v = cand.get("count")
    return int(v) if isinstance(v, int) else (int(v) if isinstance(v, float) else -1)


def _low_confidence_reasons(hit, sim, pool_max):
    """判定是否需要给这条匹配打 ``low_confidence``，返回说明列表（空列表表示可信）。"""
    reasons = []
    cnt = _count_of(hit)
    if sim < TITLE_STRONG_MIN:
        reasons.append(
            f"标题相似度仅 {sim:.2f}（< {TITLE_STRONG_MIN}），未达高置信匹配，"
            f"匹配到的《{str(hit.get('title', ''))[:50]}》可能不是同一篇")
    if cnt >= 0 and pool_max >= LOW_CONF_POOL_MIN and cnt < LOW_CONF_ABS_MIN \
            and cnt < pool_max * LOW_CONF_RATIO:
        reasons.append(
            f"选中记录被引 {cnt} 次，而同一批候选里存在被引 {pool_max} 次的同名记录"
            f"（相差 {round(pool_max / max(cnt, 1), 1)} 倍），高度疑似命中噪声/影印版本")
    return reasons


def _pick_best(cands, title, year=None, authors=None):
    """从候选列表挑最匹配的一条（**被引数为首要权重**）。

    选优优先级：
      ① 过滤掉标题相似度 < TITLE_MATCH_MIN 的候选；
      ② 若存在「高置信匹配」（相似度 >= TITLE_STRONG_MIN），**只在这些里面比**；
      ③ 在高置信集合里**取 cited_by_count 最大的那条**——经典论文的同名/重印/影印记录往往
         只差 venue 与年份，真正那篇被引数通常高 1~3 个数量级；
      ④ 只有在被引数完全相同时，才用相似度 + 年份/作者的小加成做 Tie-break。

    这样 0.10 的年份加成永远无法让噪声记录反超被引数更高的正主（修复 AlexNet 760 vs 108512）。

    返回 dict（额外携带 match_score / low_confidence / confidence_reasons），
    无合格候选返回 None。
    """
    pool = []
    for c in cands:
        if not isinstance(c.get("count"), int):
            continue
        sim, combined = _score_components(title, c, year, authors)
        if sim < TITLE_MATCH_MIN:
            continue
        pool.append((sim, combined, c))
    if not pool:
        return None

    strong = [x for x in pool if x[0] >= TITLE_STRONG_MIN]
    group = strong or pool
    pool_max = max(_count_of(x[2]) for x in group)
    if strong:
        # ③ 被引数为主键降序；④ 被引数相同时用相似度 + 次要加成做同分裁决
        sim, combined, c = max(group, key=lambda x: (_count_of(x[2]), x[0], x[1]))
    else:
        # 无高置信匹配：退化为「相似度优先、被引数次之」，避免把热门但不相关的论文选进来
        sim, combined, c = max(group, key=lambda x: (x[1], _count_of(x[2])))

    out = dict(c)
    out["match_score"] = round(sim, 3)
    reasons = _low_confidence_reasons(c, sim, pool_max)
    out["low_confidence"] = bool(reasons)
    out["confidence_reasons"] = reasons
    out["strong_match"] = bool(strong)
    out["pool_max_count"] = pool_max
    out["pool_size"] = len(pool)
    return out


# ---------------- 三级数据源 ----------------
def _work_to_cand(w):
    """OpenAlex work → 统一候选结构。"""
    src = ((w.get("primary_location") or {}).get("source") or {})
    return {
        "title": w.get("display_name") or w.get("title") or "",
        "count": w.get("cited_by_count"),
        "year": w.get("publication_year"),
        "venue": src.get("display_name") or "",
        "authors": [a.get("author", {}).get("display_name", "")
                    for a in (w.get("authorships") or [])[:5]],
    }


def _openalex_exact_lookup(ids, title):
    """DOI 精确优先：``/works/doi:<DOI>``。

    OpenAlex 偶有 DOI 错指到无关文献的情况（实测 10.48550/arXiv.1810.04805 会返回一篇
    完全不相干的 2018 年论文），因此**取到后必须用标题相似度复核**：相似度不足即丢弃，
    回退标题模糊匹配，绝不返回编造/错指的数字。
    """
    doi = (ids or {}).get("doi", "")
    if not doi:
        return None
    for raw in (doi, doi.lower()):
        url = ("https://api.openalex.org/works/doi:" + urllib.parse.quote(raw, safe="") +
               "?select=id,display_name,cited_by_count,publication_year,authorships,primary_location")
        try:
            w = _http_json(url)
        except Exception:
            continue
        cand = _work_to_cand(w)
        if not isinstance(cand["count"], int):
            return None
        if _sim(title or "", cand["title"]) < TITLE_MATCH_MIN:
            return None            # DOI 命中但标题对不上 → 判为错指，弃用
        return {"count": int(cand["count"]), "source": "openalex(doi-exact)",
                "matched_title": cand["title"], "match_score": 1.0, "exact_match": True,
                "year": cand["year"], "venue": cand["venue"], "authors": cand["authors"],
                "low_confidence": False, "confidence_reasons": [],
                "pool_max_count": int(cand["count"]), "pool_size": 1}
    return None


def _semanticscholar_exact_lookup(ids, title):
    """arXiv ID 精确优先：Semantic Scholar ``/graph/v1/paper/arXiv:<ID>``。

    同样要做标题复核（防止编号写错却照样返回一个数）；失败/无此 ID 时返回 None 交回给标题模糊匹配。
    """
    aid = (ids or {}).get("arxiv", "")
    if not aid:
        return None
    url = ("https://api.semanticscholar.org/graph/v1/paper/arXiv:" + urllib.parse.quote(aid) +
           "?fields=title,citationCount,year,venue,authors")
    try:
        d = _http_json(url)
    except Exception:
        return None
    cnt = d.get("citationCount")
    if not isinstance(cnt, int):
        return None
    cand_title = d.get("title") or ""
    if _sim(title or "", cand_title) < TITLE_MATCH_MIN:
        return None                # arXiv ID 命中但标题对不上 → 判为不可信，弃用
    return {"count": int(cnt), "source": "semanticscholar(arxiv-exact)",
            "matched_title": cand_title, "match_score": 1.0, "exact_match": True,
            "year": d.get("year"), "venue": d.get("venue") or "",
            "authors": [a.get("name", "") for a in (d.get("authors") or [])[:5]],
            "low_confidence": False, "confidence_reasons": [],
            "pool_max_count": int(cnt), "pool_size": 1}


def _openalex_lookup(title, year=None, authors=None):
    """OpenAlex：/works?filter=title.search:... ，取 cited_by_count。"""
    q = urllib.parse.quote(title[:180])
    url = ("https://api.openalex.org/works?filter=title.search:" + q +
           "&per-page=5&select=id,title,display_name,cited_by_count,publication_year,"
           "authorships,primary_location")
    if OPENALEX_MAILTO:
        url += "&mailto=" + urllib.parse.quote(OPENALEX_MAILTO)
    data = _http_json(url)
    cands = [_work_to_cand(w) for w in (data.get("results") or [])]
    hit = _pick_best([c for c in cands if isinstance(c.get("count"), int)], title, year, authors)
    if not hit:
        return None
    return {"count": int(hit["count"]), "source": "openalex", "matched_title": hit["title"],
            "match_score": hit["match_score"], "year": hit["year"], "venue": hit["venue"],
            "authors": hit["authors"], "low_confidence": bool(hit.get("low_confidence")),
            "confidence_reasons": list(hit.get("confidence_reasons") or []),
            "pool_max_count": hit.get("pool_max_count"), "strong_match": hit.get("strong_match")}


def _semanticscholar_lookup(title, year=None, authors=None):
    """Semantic Scholar Graph API：/paper/search?query=...&fields=title,citationCount,year,venue。"""
    url = ("https://api.semanticscholar.org/graph/v1/paper/search?" +
           urllib.parse.urlencode({"query": title[:180], "limit": 5,
                                   "fields": "title,citationCount,year,venue,authors"}))
    data = _http_json(url)
    cands = []
    for p in (data.get("data") or []):
        cands.append({
            "title": p.get("title") or "",
            "count": p.get("citationCount"),
            "year": p.get("year"),
            "venue": (p.get("venue") or ""),
            "authors": [a.get("name", "") for a in (p.get("authors") or [])[:5]],
        })
    hit = _pick_best([c for c in cands if isinstance(c.get("count"), int)], title, year, authors)
    if not hit:
        return None
    return {"count": int(hit["count"]), "source": "semanticscholar", "matched_title": hit["title"],
            "match_score": hit["match_score"], "year": hit["year"], "venue": hit["venue"],
            "authors": hit["authors"], "low_confidence": bool(hit.get("low_confidence")),
            "confidence_reasons": list(hit.get("confidence_reasons") or []),
            "pool_max_count": hit.get("pool_max_count"), "strong_match": hit.get("strong_match")}


def _crossref_lookup(title, year=None, authors=None):
    """Crossref：/works?query.bibliographic=... ，取 is-referenced-by-count（第三级降级）。"""
    url = ("https://api.crossref.org/works?" +
           urllib.parse.urlencode({"query.bibliographic": title[:180], "rows": 5,
                                   "select": "title,is-referenced-by-count,container-title,issued,author"}))
    data = _http_json(url)
    cands = []
    for it in ((data.get("message") or {}).get("items") or []):
        issued = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        cands.append({
            "title": (it.get("title") or [""])[0],
            "count": it.get("is-referenced-by-count"),
            "year": issued[0] if issued else None,
            "venue": (it.get("container-title") or [""])[0] if it.get("container-title") else "",
            "authors": [f"{a.get('family','')} {a.get('given','')}".strip()
                        for a in (it.get("author") or [])[:5]],
        })
    hit = _pick_best([c for c in cands if isinstance(c.get("count"), int)], title, year, authors)
    if not hit:
        return None
    return {"count": int(hit["count"]), "source": "crossref", "matched_title": hit["title"],
            "match_score": hit["match_score"], "year": hit["year"], "venue": hit["venue"],
            "authors": hit["authors"], "low_confidence": bool(hit.get("low_confidence")),
            "confidence_reasons": list(hit.get("confidence_reasons") or []),
            "pool_max_count": hit.get("pool_max_count"), "strong_match": hit.get("strong_match")}


_LOOKUPS = (
    ("openalex", _openalex_lookup),
    ("semanticscholar", _semanticscholar_lookup),
    ("crossref", _crossref_lookup),
)


def fetch_citation_count(title, authors=None, year=None, use_cache=True):
    """取一条文献的真实被引次数。

    三级降级：OpenAlex → Semantic Scholar → Crossref，每级 8s 超时；每级按顺序试各标题变体。
    返回 {"count":int,"source":str,"matched_title":str,"match_score":float,"year","venue","authors",
    "low_confidence":bool,"confidence_reasons":[...]}；
    **三级全部失败（或标题匹配度不足）返回 None**，原因可用 fetch_error(title) 读取——不编造任何数字。
    若主数据源 OpenAlex 因网络错误（如 HTTP 429）不可用而降级到下一级，结果会打上
    ``source_degraded`` + ``low_confidence`` 并在 confidence_reasons 里写明口径差异。
    """
    if not title or not str(title).strip():
        return None
    key = _norm(title)
    if use_cache:
        hit = _cache_get(key)
        if hit:
            return hit["data"] if hit["ok"] else None
    errs = []
    # ① 精确匹配优先：原文里带 DOI 时先走 OpenAlex /works/doi:<DOI>（内部会用标题复核，防止错指）
    ids = _extract_ids(title)
    if ids["doi"]:
        for q in _candidate_titles(title):
            try:
                res = _openalex_exact_lookup(ids, q)
                if res is not None:
                    res["query_used"] = q
                    _cache_put(key, True, data=res)
                    return res
                errs.append("openalex-doi: 「%s」精确命中但标题未通过复核，已弃用" % ids["doi"][:36])
            except Exception as e:
                errs.append(f"openalex-doi: {type(e).__name__}: {str(e)[:70]}")
    # ① arXiv ID 精确优先：Semantic Scholar /paper/arXiv:<ID>（同样要做标题复核）
    if ids.get("arxiv"):
        for q in _candidate_titles(title):
            try:
                res = _semanticscholar_exact_lookup(ids, q)
                if res is not None:
                    res["query_used"] = q
                    _cache_put(key, True, data=res)
                    return res
                errs.append("semanticscholar-arxiv: arXiv:%s 精确命中但标题未通过复核或该源无此文，已弃用"
                            % ids["arxiv"])
            except Exception as e:
                errs.append(f"semanticscholar-arxiv: {type(e).__name__}: {str(e)[:70]}")
    # ② 标题模糊匹配三级降级（内部已是「被引数优先」选优）
    degraded = False            # 主数据源 OpenAlex 是否因网络错误而不可用
    for name, fn in _LOOKUPS:
        for q in _candidate_titles(title):
            try:
                res = fn(q, year, authors)
                if res is not None:
                    res["query_used"] = q
                    if degraded:
                        # 主数据源挂了才轮到这一级：口径不同且常缺 arXiv 预印本，必须显式提示
                        res["source_degraded"] = True
                        res["low_confidence"] = True
                        res["confidence_reasons"] = list(res.get("confidence_reasons") or []) + [
                            f"主数据源 OpenAlex 本次不可用，已降级到 {res['source']}；"
                            f"其统计口径与 OpenAlex 不同（常显著偏低）且可能未收录 arXiv 预印本，数值仅供参考"]
                    _cache_put(key, True, data=res)
                    return res
                errs.append(f"{name}: 「{q[:40]}」无相似度≥{TITLE_MATCH_MIN} 的匹配")
            except urllib.error.HTTPError as e:
                errs.append(f"{name}: HTTP {e.code}")
                if name == _LOOKUPS[0][0]:
                    degraded = True
            except Exception as e:
                errs.append(f"{name}: {type(e).__name__}: {str(e)[:80]}")
                if name == _LOOKUPS[0][0]:
                    degraded = True
    _cache_put(key, False, err="；".join(errs) or "全部数据源不可用")
    return None


# ---------------- venue 等级字典（离线） ----------------
# (正则, 等级, 规范名)
_VENUE_RULES = [
    # ---- A 类：CCF-A / 公认顶会顶刊 ----
    (r"\bneurips\b|\bnips\b|advances in neural information processing systems", "A类", "NeurIPS"),
    (r"\bicml\b|international conference on machine learning", "A类", "ICML"),
    (r"\biclr\b|international conference on learning representations", "A类", "ICLR"),
    (r"\bcvpr\b|computer vision and pattern recognition", "A类", "CVPR"),
    (r"\biccv\b|international conference on computer vision", "A类", "ICCV"),
    (r"\beccv\b|european conference on computer vision", "A类", "ECCV"),
    (r"\bacl\b|association for computational linguistics|\btacl\b", "A类", "ACL/TACL"),
    (r"\bemnlp\b|empirical methods in natural language processing", "A类", "EMNLP"),
    (r"\bnaacl\b", "A类", "NAACL"),
    (r"\baaai\b|\bijcai\b", "A类", "AAAI/IJCAI"),
    (r"\bkdd\b|knowledge discovery and data mining", "A类", "KDD"),
    (r"\bsigir\b", "A类", "SIGIR"),
    (r"\bwww\b|the web conference|international world wide web conference", "A类", "WWW"),
    (r"\btpami\b|transactions on pattern analysis and machine intelligence", "A类", "TPAMI"),
    (r"\bijcv\b|international journal of computer vision", "A类", "IJCV"),
    (r"\bjmlr\b|journal of machine learning research", "A类", "JMLR"),
    (r"\bsigmod\b|\bvldb\b|\bicde\b|\btods\b|\btois\b", "A类", "数据库顶会/顶刊"),
    (r"\bsigcomm\b|\bosdi\b|\bsosp\b|eurosys|usenix security|\bccs\b|\bndss\b|symposium on security and privacy",
     "A类", "系统/安全顶会"),
    (r"\bicse\b|\bfse\b|\bissta\b|\btosem\b", "A类", "软件工程顶会/顶刊"),
    (r"\bchi\b|\buist\b|siggraph|\btog\b|acm transactions on graphics", "A类", "人机交互/图形学顶会"),
    (r"\bnature\b|\bscience\b|\bcell\b|\bpnas\b|national academy of sciences", "A类", "综合性顶刊"),
    (r"acm multimedia|acm international conference on multimedia|\bacm mm\b", "A类", "ACM MM"),

    # ---- B 类 ----
    (r"\bicassp\b|international conference on acoustics", "B类", "ICASSP"),
    (r"\bcoling\b|\beacl\b|\baacl\b|\bconll\b|\blrec\b", "B类", "COLING/EACL/CoNLL"),
    (r"\baistats\b|\buai\b|\bcolt\b|\becml\b|\bpkdd\b", "B类", "AISTATS/UAI/COLT/ECML"),
    (r"\bicip\b|\bicpr\b|\bbmvc\b|\bwacv\b|\bicmr\b", "B类", "图像/多媒体 B 类会议"),
    (r"\becai\b|\bijcnn\b|\bicdm\b|\bwww companion\b", "B类", "ECAI/IJCNN/ICDM"),
    (r"\bmiccai\b|medical image computing", "B类", "MICCAI"),
    (r"\btnnls\b|transactions on neural networks|\btkde\b|\btmm\b|\btmlr\b", "B类", "TNNLS/TKDE/TMLR"),
    (r"pattern recognition\b(?! letters)", "B类", "Pattern Recognition"),
    (r"information processing & management|information processing and management", "B类", "IP&M"),
    (r"\bijcai survey\b|acm computing surveys|\bcsur\b", "B类", "ACM Computing Surveys"),

    # ---- C 类 ----
    (r"\bieee access\b", "C类", "IEEE Access"),
    (r"neurocomputing|knowledge[- ]based systems|applied soft computing|information sciences\b",
     "C类", "发文量大/门槛较低的期刊"),
    (r"\bsensors\b|\belectronics\b|applied sciences|\bentropy\b|mathematics\b|\bplos one\b",
     "C类", "MDPI/PLOS 类综合刊"),
    (r"procedia|lecture notes in computer science|\blncs\b|workshop|companion proceedings",
     "C类", "会议论文集/Workshop"),

    # ---- 预印本（不评级，返回未知并说明） ----
    (r"\barxiv\b|biorxiv|chemrxiv|medrxiv|\bssrn\b|preprint|under (?:review|submission)|submitted to",
     "未知", "预印本/未正式发表，不参与等级评定"),

    # ---- 水刊/掠夺性出版黑名单 ----
    (r"scirp|scientific research publishing", "C类", "命中水刊黑名单：SCIRP"),
    (r"\biosr\b|international journal of engineering research|\bijert\b|\birjet\b|\bijser\b|"
     r"\bijarcs\b|\bijaerd\b|\bijraset\b|\bijitee\b|\bijca\b|international journal of computer applications|"
     r"international journal of advanced research", "C类", "命中水刊黑名单：常见掠夺性/低质量期刊"),
]


def venue_detail(venue_name):
    """返回 {"tier": "A类/B类/C类/未知", "reason": str}。未在字典中命中时 tier="未知"。"""
    v = (venue_name or "").strip()
    if not v:
        return {"tier": "未知", "reason": "未提供发表venue，无法评级"}
    low = v.lower()
    for pat, tier, name in _VENUE_RULES:
        if re.search(pat, low):
            return {"tier": tier, "reason": f"命中离线等级字典：{name}（{v[:60]}）"}
    return {"tier": "未知", "reason": f"离线字典未收录该 venue（{v[:60]}），未做等级推断"}


def venue_tier(venue_name):
    """返回 "A类"/"B类"/"C类"/"未知"；未命中一律返回"未知"（原因见 venue_detail）。"""
    return venue_detail(venue_name)["tier"]


# ---------------- 引用位置 / 主题相关性 ----------------
_STOP = {"the", "a", "an", "of", "and", "or", "for", "with", "on", "in", "to", "via",
         "using", "based", "toward", "towards", "study", "method", "model", "network",
         "learning", "deep", "neural", "paper", "new", "novel", "approach", "framework"}

_RW_RE = re.compile(r"(related work|literature review|background|prior work|previous work|"
                    r"相关工作|文献综述|研究现状)", re.IGNORECASE)
_NEXT_SEC_RE = re.compile(r"\n\s*(?:\d+\.?\s*)?(?:method|approach|experiment|results?|conclusion|"
                          r"discussion|our|proposed|方法|实验|结论)", re.IGNORECASE)


def _title_tokens(title):
    return [w for w in _norm(title).split() if len(w) > 4 and w not in _STOP]


def _locate(citing_text, title):
    """在引用方正文中定位该参考文献标题出现的位置。

    返回 (found: bool, positions: [int])。用标题前 5/4/3 个实词做短语匹配（容忍缩写与标点差异）。
    """
    if not citing_text or not title:
        return False, []
    toks = _title_tokens(title)
    if len(toks) < 3:
        return False, []
    hay = _norm(citing_text)
    for n in (5, 4, 3):
        if len(toks) < n:
            continue
        phrase = r"\s+".join(re.escape(w) for w in toks[:n])
        pos = [m.start() for m in re.finditer(phrase, hay)]
        if pos:
            return True, pos
    return False, []


def _related_work_window(citing_text):
    """返回 (start, end)：Related Work 章节在正文中的字符区间；无该章节返回 (None, None)。"""
    m = _RW_RE.search(citing_text or "")
    if not m:
        return None, None
    start = m.start()
    nxt = _NEXT_SEC_RE.search(citing_text, m.end())
    end = nxt.start() if nxt else min(len(citing_text), start + 6000)
    return start, end


def _position_flag(citing_text, title):
    """判断引用位置：(flag, 说明)。flag ∈ {"related_work_only","multi","正文未定位","无正文"}。"""
    if not citing_text:
        return "无正文", "未提供引用方正文，跳过引用位置判定"
    found, pos = _locate(citing_text, title)
    if not found:
        return "正文未定位", "正文中未定位到该文献标题（多为编号式引用），无法判定引用位置"
    s, e = _related_work_window(citing_text)
    if s is None:
        return "multi", f"正文命中 {len(pos)} 处，未识别到 Related Work 章节，默认按实质性引用"
    in_rw = [p for p in pos if s <= p < e]
    if len(pos) == len(in_rw):      # 全部命中都落在 Related Work 里
        return "related_work_only", f"仅在 Related Work/综述段落出现 {len(in_rw)} 次，存在凑数嫌疑"
    return "multi", f"正文中命中 {len(pos)} 处，其中 {len(in_rw)} 处在 Related Work、{len(pos)-len(in_rw)} 处在正文其他部分"


def _relevance(ref_title, citing_text):
    """主题相关性：参考文献标题实词在引用方正文中的覆盖率。"""
    toks = _title_tokens(ref_title)
    if not toks or not citing_text:
        return None
    hay = set(_norm(citing_text).split())
    hit = sum(1 for w in toks if w in hay)
    return round(hit / len(toks), 3)


# ---------------- 综合评估 ----------------
def _coerce_entry(entry):
    """归一输入：dict → (title, authors, year, venue)；str → 原文（标题取首个句号/逗号前的片段）。"""
    if isinstance(entry, dict):
        return (str(entry.get("title", "") or ""), str(entry.get("authors", "") or ""),
                entry.get("year"), str(entry.get("venue", "") or ""))
    s = str(entry or "")
    m = re.search(r"\b(19|20)\d{2}\b", s)
    # 字符串条目：用清洗规则猜出标题（去作者/年份/venue/括号），供标题匹配与位置定位使用
    cands = _candidate_titles(s)
    return (cands[0], "", m.group(0) if m else None, s)


def assess_reference(entry, citing_paper_text=None, use_network=True):
    """综合评估一条参考文献的含金量。

    entry: dict{title, authors, year, venue} 或参考文献原文字符串。
    citing_paper_text: 引用方（本文）正文，用于判定 (c) 是否 Related Work 凑数、(d) 主题相关性。
    返回 {"level": "高含金量/中含金量/低含金量", "real_citations": int|None, "venue_tier": str,
          "score": float, "reasons": [...], "evidence": {...}}
    """
    title, authors, year, venue = _coerce_entry(entry)
    reasons, evidence = [], {}
    score = 0.0
    # 检索用的查询串：字符串条目用**原文**交给 fetch（保留句点/年份等切分线索，便于生成多个标题变体），
    # 清洗后的 title 只用于展示与正文定位/相关性计算。
    query = str(entry) if not isinstance(entry, dict) else title

    # (a) 真实被引数
    real = fetch_citation_count(query, authors=authors, year=year) if (use_network and query) else None
    cnt = real["count"] if real else None
    if cnt is None:
        reasons.append(f"真实被引数：未能取到（{fetch_error(query) or '未联网或标题匹配不足'}），"
                       f"该维度不计分、不猜测")
    else:
        evidence["citation_source"] = real["source"]
        evidence["matched_title"] = real["matched_title"]
        evidence["match_score"] = real["match_score"]
        if cnt >= 500:
            d = 45; desc = "极高被引"
        elif cnt >= 100:
            d = 40; desc = "高被引"
        elif cnt >= 30:
            d = 30; desc = "中高被引"
        elif cnt >= 10:
            d = 20; desc = "中等被引"
        elif cnt >= 1:
            d = 10; desc = "低被引"
        else:
            d = 0; desc = "零被引"
        score += d
        reasons.append(f"真实被引 {cnt} 次（{real['source']}，匹配《{real['matched_title'][:60]}》"
                       f"相似度 {real['match_score']}）→ {desc} (+{d})")
        if real.get("venue") and not venue.strip(" .,"):
            venue = real["venue"]          # entry 未给 venue 时用 API 返回的来源名
        if real.get("low_confidence"):
            evidence["low_confidence"] = True
            evidence["confidence_reasons"] = real.get("confidence_reasons", [])
            for r in real.get("confidence_reasons", []):
                reasons.append(f"⚠️ 低置信：{r}（请在 UI 层提示「数据可能不准确」）")

    # (b) venue 等级
    vd = venue_detail(venue)
    tier = vd["tier"]
    bonus = {"A类": 30, "B类": 20, "C类": 5, "未知": 0}[tier]
    score += bonus
    reasons.append(f"发表venue「{(venue or '缺失')[:60]}」评级 {tier}：{vd['reason']} (+{bonus})")

    # (c) 引用位置：是否只在 Related Work 里凑数
    flag, pos_desc = _position_flag(citing_paper_text, title)
    evidence["position"] = flag
    if flag == "related_work_only":
        score -= 20
        reasons.append(f"引用位置：{pos_desc} → 疑似凑数引用 (-20)")
    elif flag in ("multi",):
        score += 10
        reasons.append(f"引用位置：{pos_desc} → 正文多处实质引用 (+10)")
    else:
        reasons.append(f"引用位置：{pos_desc}（不扣分）")

    # (d) 主题相关性
    rel = _relevance(title, citing_paper_text)
    evidence["relevance"] = rel
    if rel is None:
        reasons.append("主题相关性：无正文或未抽到有效标题词，跳过该维度")
    elif rel >= 0.5:
        score += 20
        reasons.append(f"主题相关性高（标题实词覆盖率 {rel:.0%}）→ 与本文强相关 (+20)")
    elif rel >= 0.25:
        score += 10
        reasons.append(f"主题相关性中等（覆盖率 {rel:.0%}）(+10)")
    else:
        score -= 10
        reasons.append(f"主题相关性低（覆盖率 {rel:.0%}）→ 与本文主题偏离，疑似凑数 (-10)")

    score = max(0.0, min(100.0, score))
    level = "高含金量" if score >= 60 else ("中含金量" if score >= 35 else "低含金量")
    return {"level": level, "real_citations": cnt, "venue_tier": tier, "score": round(score, 1),
            "reasons": reasons, "title": title[:160], "evidence": evidence,
            "low_confidence": bool(evidence.get("low_confidence"))}


def assess_references(entries, citing_paper_text=None, max_network=5):
    """批量评估：前 max_network 条走真实网络链路，其余仅做本地规则评估（避免请求风暴）。"""
    out = []
    for i, e in enumerate(entries or []):
        out.append(assess_reference(e, citing_paper_text=citing_paper_text,
                                    use_network=(i < max_network)))
    return out


# ---------------- 以下为原有离线规则版（保持向后兼容） ----------------
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
    """对单条参考文献做含金量打分（纯离线规则版，不联网）。

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
    """批量打分入口（离线规则版）：refs 为 dict/str 列表，返回与输入等长的结果列表。"""
    return [score_reference(r, current_year=current_year) for r in (refs or [])]

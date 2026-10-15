"""外部数据源集成：arXiv、PubMed、DuckDuckGo 网页、PDF 下载、GitHub 线索识别与仓库复现性评估。"""
import os
import re
import json
import base64
import difflib
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

from config import _env_str

# GitHub REST API：未认证限流 60 次/小时；配置 GITHUB_TOKEN（或 .env 中同名变量）可提额
# 走 _env_str：云平台上 .env 不存在，需回退到 Streamlit Secrets 才读得到
GITHUB_TOKEN = _env_str("GITHUB_TOKEN", "") or _env_str("GITHUB_API_TOKEN", "")
GITHUB_TIMEOUT = 8            # 仓库元数据请求超时（秒）
GITHUB_README_TIMEOUT = 8     # README 抓取超时（秒）
GITHUB_README_CHARS = 3000    # README 取前 3000 字用于复现性判断


def detect_github(*texts):
    joined = " ".join(texts)
    m = re.findall(r"https?://github\.com/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-\.]+", joined)
    return list(dict.fromkeys(m))


def search_arxiv(query, max_results=5, sort="relevance"):
    import arxiv
    sort_by = arxiv.SortCriterion.Relevance if sort == "relevance" else arxiv.SortCriterion.SubmittedDate
    s = arxiv.Search(query=query, max_results=max_results, sort_by=sort_by)
    c = arxiv.Client(page_size=100, delay_seconds=2, num_retries=3)
    out = []
    for p in c.results(s):
        out.append({
            "id": p.entry_id, "arxiv_id": p.get_short_id(), "title": p.title,
            "summary": p.summary, "authors": [a.name for a in p.authors],
            "pdf_url": p.pdf_url, "published": str(p.published.date()) if p.published else "",
            "github": detect_github(p.summary, p.title),
        })
    return out


def latest_arxiv(category="cs.AI", max_results=5):
    return search_arxiv(f"cat:{category}", max_results, sort="submitted")


def match_arxiv_title(title):
    """按标题在 arXiv 检索并返回最匹配项（含相似度）。"""
    try:
        res = search_arxiv(title, 1)
        if not res:
            return None, "arXiv 未找到"
        p = res[0]
        sim = difflib.SequenceMatcher(None, title.lower(), p["title"].lower()).ratio()
        if sim < 0.5:
            return None, f"相似度低({sim:.2f})"
        p["similarity"] = sim
        return p, None
    except Exception as e:
        return None, str(e)[:80]


def search_pubmed(query, max_results=5):
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    p1 = urllib.parse.urlencode({"db": "pubmed", "term": query, "retmode": "json", "retmax": max_results})
    with urllib.request.urlopen(base + "esearch.fcgi?" + p1, timeout=20) as r:
        d = json.loads(r.read().decode())
    ids = d.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    p2 = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "json"})
    with urllib.request.urlopen(base + "esummary.fcgi?" + p2, timeout=20) as r:
        d = json.loads(r.read().decode())
    out = []
    for pid in ids:
        info = d.get("result", {}).get(pid, {})
        out.append({
            "pmid": pid, "title": info.get("title", ""),
            "authors": ", ".join(a.get("name", "") for a in info.get("authors", [])[:5]),
            "journal": info.get("fulljournalname", ""), "pubdate": info.get("pubdate", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
        })
    return out


def web_search(query, max_results=5):
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": query}).encode()
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        pat = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
        out = []
        for link, title in pat.findall(html)[:max_results]:
            title = re.sub(r"<[^>]+>", "", title).strip()
            if "uddg=" in link:
                link = urllib.parse.unquote(link.split("uddg=")[1].split("&")[0])
            out.append({"title": title, "url": link})
        return out
    except Exception as e:
        return [{"error": str(e)}]


def download(url, dest, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


# ---------------- GitHub 仓库复现性评估（真实调用 REST API，不编造） ----------------
def _github_headers():
    h = {"User-Agent": "AI4S-Research-Agent/1.0",
         "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def _github_json(url, timeout=GITHUB_TIMEOUT):
    # 瞬时故障（超时/连接重置/DNS 瞬时失败）指数退避+jitter 重试；HTTP 4xx/429 不重试
    # （撞限流墙只会更糟），交上层降级+负缓存处理（外壳在文件末尾；本函数行数不变）。
    return _github_json_retry(url, timeout)


def parse_github_repo(url):
    """从任意 GitHub 链接（含 .git / ssh 形式）解析 (owner, repo)；解析失败返回 (None, None)。"""
    m = re.search(r"github\.com[:/]+([A-Za-z0-9_\-\.]+)/([A-Za-z0-9_\-\.]+)", str(url or ""))
    if not m:
        return None, None
    return m.group(1), m.group(2).rstrip("/").removesuffix(".git")


def _fetch_readme(owner, repo):
    """取 README：优先 raw.githubusercontent（HEAD），失败降级 GitHub contents API（base64）。

    返回 (文本, 来源, 错误)；任一路径失败都不编造内容，返回空文本 + 明确错误说明。
    """
    try:
        req = urllib.request.Request(
            f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md",
            headers={"User-Agent": "AI4S-Research-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=GITHUB_README_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="ignore"), "raw.githubusercontent", ""
    except Exception as e:
        raw_err = f"raw.githubusercontent 不可用：{type(e).__name__}: {str(e)[:60]}"
    try:
        data = _github_json(f"https://api.github.com/repos/{owner}/{repo}/readme")
        content = (data.get("content") or "").replace("\n", "")
        text = base64.b64decode(content).decode("utf-8", errors="ignore") if content else ""
        return text, "api.github.com/readme", ""
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            return "", "", f"{raw_err}；GitHub API 限流(HTTP {e.code})，README 未取到"
        return "", "", f"{raw_err}；README 接口 HTTP {e.code}"
    except Exception as e:
        return "", "", f"{raw_err}；README 抓取失败：{type(e).__name__}: {str(e)[:60]}"


def github_repo_info(url):
    """查询 GitHub 仓库真实复现性指标。

    真实调用 https://api.github.com/repos/{owner}/{repo}（免 key，限流 60 次/小时）取
    stars/forks/open_issues/license/pushed_at/archived，再尝试取 README（前 GITHUB_README_CHARS 字）。
    返回结构化结果；限流/网络失败时返回明确降级说明（degraded=True），**不编造任何指标**。
    """
    owner, repo = parse_github_repo(url)
    if not owner or not repo:
        return {"error": f"无法从 URL 解析 owner/repo：{url}", "degraded": True,
                "reproducibility": "未知", "reasons": [f"URL 解析失败：{url}"]}
    out = {"repo": f"{owner}/{repo}", "url": f"https://github.com/{owner}/{repo}",
           "stars": None, "forks": None, "open_issues": None, "license": "unknown",
           "pushed_at": "", "last_push_days": None, "archived": None,
           "has_readme": False, "readme_chars": 0, "readme_source": "",
           "reproducibility": "未知", "reasons": [], "degraded": False, "error": ""}
    try:
        d = _github_json(f"https://api.github.com/repos/{owner}/{repo}")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            out.update({"degraded": True, "reproducibility": "未知",
                        "error": f"GitHub API 限流或禁止访问（HTTP {e.code}）：未认证额度 60 次/小时，"
                                 f"可配置 GITHUB_TOKEN 提额。本次不做任何推测性评级"})
            out["reasons"].append("仓库元数据未取到（限流），无法评估复现性")
            return out
        out.update({"degraded": True, "error": f"GitHub API HTTP {e.code}",
                    "reasons": [f"仓库元数据请求失败：HTTP {e.code}"]})
        return out
    except Exception as e:
        out.update({"degraded": True, "error": f"{type(e).__name__}: {str(e)[:120]}",
                    "reasons": [f"仓库元数据请求失败：{type(e).__name__}"]})
        return out

    out["stars"] = d.get("stargazers_count")
    out["forks"] = d.get("forks_count")
    out["open_issues"] = d.get("open_issues_count")
    lic = (d.get("license") or {}).get("spdx_id") or "unknown"
    out["license"] = lic
    out["archived"] = bool(d.get("archived"))
    out["pushed_at"] = d.get("pushed_at") or ""
    try:
        pushed = datetime.fromisoformat(out["pushed_at"].replace("Z", "+00:00"))
        out["last_push_days"] = int((datetime.now(timezone.utc) - pushed).total_seconds() // 86400)
    except Exception:
        out["last_push_days"] = None

    readme, src, rerr = _fetch_readme(owner, repo)
    out["has_readme"] = bool(readme.strip())
    out["readme_chars"] = len(readme)
    out["readme_source"] = src
    if rerr:
        out["reasons"].append(rerr)

    # ---- 复现性评分（可解释加权，全部基于上面真实取到的字段） ----
    s, reasons = 0.0, out["reasons"]
    st = out["stars"] or 0
    if st >= 500:
        s += 2.0; reasons.append(f"stars {st} ≥ 500，社区关注度高 (+2.0)")
    elif st >= 100:
        s += 1.5; reasons.append(f"stars {st} 在 100~500，有一定社区基础 (+1.5)")
    elif st >= 20:
        s += 1.0; reasons.append(f"stars {st} 在 20~100 (+1.0)")
    else:
        reasons.append(f"stars {st} < 20，社区验证不足 (+0)")

    if out["has_readme"]:
        s += 1.5; reasons.append(f"存在 README（{out['readme_chars']} 字，来源 {src or '未取到'}）(+1.5)")
        if out["readme_chars"] >= 1500:
            s += 0.5; reasons.append("README 内容较完整（≥1500 字）(+0.5)")
    else:
        reasons.append("未取到 README (+0)")

    if lic and lic not in ("unknown", "NOASSERTION"):
        s += 1.0; reasons.append(f"有明确开源许可证 {lic} (+1.0)")
    else:
        reasons.append(f"许可证缺失或不明确（{lic}）(+0)")

    d_days = out["last_push_days"]
    if d_days is None:
        reasons.append("最近推送时间未知 (+0)")
    elif d_days <= 180:
        s += 1.5; reasons.append(f"最近推送在 {d_days} 天内，维护活跃 (+1.5)")
    elif d_days <= 365:
        s += 1.0; reasons.append(f"最近推送 {d_days} 天前，近一年内有维护 (+1.0)")
    elif d_days <= 730:
        s += 0.5; reasons.append(f"最近推送 {d_days} 天前，维护缓慢 (+0.5)")
    else:
        reasons.append(f"最近推送 {d_days} 天前，长期未维护 (+0)")

    if out["archived"]:
        s -= 3.0; reasons.append("仓库已归档（archived），作者停止维护 (-3.0)")

    if out["archived"]:
        out["reproducibility"] = "低"
    elif s >= 4.0:
        out["reproducibility"] = "高"
    elif s >= 2.5:
        out["reproducibility"] = "中"
    else:
        out["reproducibility"] = "低"
    out["score"] = round(s, 2)
    out["reasons"].append(f"综合得分 {s:.2f} → 复现性评级「{out['reproducibility']}」")
    return out


# ================= 外部请求缓存层（TTL + 负缓存 + 单飞 + 瞬时重试） =================
# 全部追加于文件末尾，两个原因：
#   ① 上方各函数（含 docs/technical_doc.md 引用的 github_repo_info:162 等）行号不变，
#      文档引用零漂移；
#   ② import 完成后才把模块属性包上缓存外壳，调用方 `extern.xxx(...)` 无感，
#      冒烟脚本的故障注入（整体替换模块属性）也天然绕过缓存。
#
# 缓存策略（环境变量可调，默认值面向演示/ CI 都安全）：
#   AI4S_HTTP_CACHE_TTL      统一覆盖各缓存的正缓存秒数（缺省 github/pubmed 600、
#                            arxiv/web 300）
#   AI4S_HTTP_CACHE_NEG_TTL  负缓存秒数（默认 30；置 0 = 关闭负缓存，每次真实重试）
#   AI4S_HTTP_CACHE_MAXSIZE  统一覆盖各缓存条目上限（缺省 128/128/64/64）
#   AI4S_GITHUB_RETRIES      GitHub 瞬时故障重试次数（默认 1）
# 离线确定性：命中路径零网络；未命中时行为与加缓存前完全一致（就是原函数本身）。
import functools  # noqa: E402
from core.perf import TTLCache, call_with_retry, env_float, env_int  # noqa: E402


def _ttl(default):
    """AI4S_HTTP_CACHE_TTL 可统一覆盖各缓存的正缓存 TTL（缺省用各自的默认值）。"""
    return env_float("AI4S_HTTP_CACHE_TTL", default)


def _neg_ttl():
    """负缓存 TTL：AI4S_HTTP_CACHE_NEG_TTL，默认 30s；置 0 = 关闭负缓存。"""
    return env_float("AI4S_HTTP_CACHE_NEG_TTL", 30.0)


def _maxsize(default):
    """AI4S_HTTP_CACHE_MAXSIZE 可统一覆盖条目上限（缺省用各自的默认值）。"""
    return env_int("AI4S_HTTP_CACHE_MAXSIZE", default)


_NEG_TTL = _neg_ttl()
_GITHUB_RETRIES = env_int("AI4S_GITHUB_RETRIES", 1)

# GitHub 未认证限流 60 次/小时：同一仓库被 Agent / 页面重复查询时正缓存直接挡掉，
# 限流期（403/429）的降级响应用负缓存短存，避免每次重试都撞墙。
_github_cache = TTLCache("github", ttl=_ttl(600.0), neg_ttl=_NEG_TTL,
                         maxsize=_maxsize(128))
# arXiv 检索结果在分钟级窗口内基本不变；空结果按负缓存短存（防刷屏式重查）。
_arxiv_cache = TTLCache("arxiv", ttl=_ttl(300.0), neg_ttl=_NEG_TTL,
                        maxsize=_maxsize(128))
_pubmed_cache = TTLCache("pubmed", ttl=_ttl(600.0), neg_ttl=_NEG_TTL,
                         maxsize=_maxsize(64))
# 网页搜索失败时 web_search 返回 [{"error": ...}]（不抛异常）：按负缓存短存同一错误，
# payload 原样保留 error 字段——不会把故障洗成"搜索成功但无结果"。
_web_cache = TTLCache("web", ttl=_ttl(300.0), neg_ttl=_NEG_TTL, maxsize=_maxsize(64))


def _github_json_retry(url, timeout=GITHUB_TIMEOUT):
    """_github_json 的重试外壳：默认 1 次瞬时重试；HTTPError（含 403/429）直接放行。"""
    def _once():
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))

    return call_with_retry(_once, attempts=_GITHUB_RETRIES,
                           give_up_on=(urllib.error.HTTPError,))


def _cached(fn, cache, should_cache):
    """把 fn 包上 TTL + 负缓存 + 单飞外壳（签名透明：*args/**kwargs 原样透传）。"""
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        key = (args, tuple(sorted(kwargs.items())))
        return cache.get_or_load(key, lambda: fn(*args, **kwargs),
                                 should_cache=should_cache)
    return _wrapper


def _github_should_cache(info):
    """只有「真拿到了指标」才进正缓存；降级/错误响应当负缓存短存（字段原样保留）。"""
    return bool(info) and not info.get("degraded") and not info.get("error")


def _nonempty(items):
    return bool(items)


def _web_should_cache(items):
    return bool(items) and not any(isinstance(r, dict) and "error" in r for r in items)


_github_repo_info_origin = github_repo_info
github_repo_info = _cached(_github_repo_info_origin, _github_cache, _github_should_cache)
search_arxiv = _cached(search_arxiv, _arxiv_cache, _nonempty)
search_pubmed = _cached(search_pubmed, _pubmed_cache, _nonempty)
web_search = _cached(web_search, _web_cache, _web_should_cache)


def cache_stats():
    """各外部缓存的计数快照（命中/未命中/负缓存/真实加载/错误/单飞合并），供实测对比。"""
    return {"github": _github_cache.stats(), "arxiv": _arxiv_cache.stats(),
            "pubmed": _pubmed_cache.stats(), "web": _web_cache.stats()}


def clear_caches():
    """清空所有外部请求缓存（演示复位 / 测试隔离用）。"""
    for c in (_github_cache, _arxiv_cache, _pubmed_cache, _web_cache):
        c.clear()

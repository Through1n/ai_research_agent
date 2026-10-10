"""外部数据源集成：arXiv、PubMed、DuckDuckGo 网页、PDF 下载、GitHub 线索识别。"""
import re
import json
import difflib
import urllib.request
import urllib.parse


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

# -*- coding: utf-8 -*-
"""P8 性能优化前后对比基准（可重复运行，零外网依赖）。

用法：
    python scripts/_bench_perf.py              # 标准场景
    AI4S_BENCH_LATENCY=0.01 python scripts/_bench_perf.py   # 缩短模拟延迟

在 origin/main（优化前）与 opt/perf（优化后）各跑一次，对比同一组指标：
  A. GitHub 仓库查询 x5          → 真实 HTTP 次数 / 总耗时
  B. GitHub 限流(403) x3         → 真实 HTTP 次数 / 总耗时（负缓存）
  C. arXiv 检索 x5               → 真实检索次数 / 总耗时
  D. 8 线程并发同仓库查询        → 真实 HTTP 次数 / 总耗时（单飞）
  E. BM25 分词（600 chunk 建两次）→ tokenize 实算次数 / 总耗时（LRU）
  F. 重复 embed（同 3 条文本 x5）→ 推理次数 / 总耗时（记忆缓存）

所有外部依赖（urllib / arxiv / embedding）都用计数假件替换并叠加模拟延迟，
优化前后差异全部来自缓存/单飞本身。结果行以 RESULT 开头，便于脚本化比对。
"""
import os
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import services.extern as extern          # noqa: E402
from services import state                # noqa: E402

LAT = float(os.environ.get("AI4S_BENCH_LATENCY", "0.04") or 0.04)
RESULTS = []


def _record(tag, **kw):
    RESULTS.append((tag, kw))
    print("  " + "  ".join("%s=%s" % (k, v) for k, v in kw.items()))


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------- 假 HTTP 层
def _install_github_fake(counter, mode="ok"):
    """mode: ok=正常仓库数据 / 403=限流降级。counter 按 URL 模式计数。"""
    import urllib.error

    def fake_open(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if "api.github.com/repos/" in url:
            counter["api"] += 1
            time.sleep(LAT)
            if mode == "403":
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            return _FakeResp(b'{"stargazers_count": 42, "forks_count": 3, '
                             b'"open_issues_count": 1, "license": {"spdx_id": "MIT"}, '
                             b'"pushed_at": "2026-09-01T00:00:00Z", "archived": false}')
        if "raw.githubusercontent.com" in url:
            counter["readme"] += 1
            time.sleep(LAT)
            return _FakeResp(b"# Demo\n" + b"repro " * 400)
        raise AssertionError("unexpected url: " + url)

    extern.urllib.request.urlopen = fake_open


def _install_arxiv_fake(counter):
    class _Page:
        entry_id = "http://arxiv.org/abs/2501.00001v1"
        title = "Fake"
        summary = "s"
        authors = []
        pdf_url = "http://arxiv.org/pdf/2501.00001v1"
        published = None

        def get_short_id(self):
            return "2501.00001v1"

    class _Client:
        def __init__(self, **kw):
            pass

        def results(self, s):
            counter["search"] += 1
            time.sleep(LAT)
            return iter([_Page()])

    fake = types.ModuleType("arxiv")
    fake.SortCriterion = types.SimpleNamespace(Relevance="rel", SubmittedDate="sub")
    fake.Search = lambda **kw: kw  # noqa: E731
    fake.Client = _Client
    sys.modules["arxiv"] = fake


def _reset_caches():
    if hasattr(extern, "clear_caches"):
        extern.clear_caches()
    if hasattr(state, "_tokenize_cached"):
        state._tokenize_cached.cache_clear()
    if hasattr(state, "_EMBED_MEMO"):
        state._EMBED_MEMO.clear()


def _cache_stats():
    if hasattr(extern, "cache_stats"):
        st = extern.cache_stats()
        return "github(hits=%s,neg=%s,loads=%s)" % (
            st["github"]["hits"], st["github"]["neg_hits"], st["github"]["loads"])
    return "(无缓存统计：优化前基线)"


# ---------------------------------------------------------------- 场景
def scenario_github_repeat():
    print("\n[A] GitHub 仓库查询 x5（模拟 %.0fms/请求）" % (LAT * 1000))
    counter = {"api": 0, "readme": 0}
    _install_github_fake(counter)
    _reset_caches()
    t0 = time.time()
    infos = [extern.github_repo_info("https://github.com/torvalds/linux") for _ in range(5)]
    dt = time.time() - t0
    ok = all(not i["degraded"] for i in infos) and infos[-1]["stars"] == 42
    _record("A_github_x5", http=counter["api"] + counter["readme"], sec=round(dt, 3),
            stars_ok=ok, cache=_cache_stats())


def scenario_github_ratelimit():
    print("\n[B] GitHub 限流(403) x3（负缓存）")
    counter = {"api": 0, "readme": 0}
    _install_github_fake(counter, mode="403")
    _reset_caches()
    t0 = time.time()
    outs = [extern.github_repo_info("https://github.com/a/b") for _ in range(3)]
    dt = time.time() - t0
    degraded = all(o["degraded"] and o["reproducibility"] == "未知" for o in outs)
    _record("B_github_403_x3", http=counter["api"] + counter["readme"], sec=round(dt, 3),
            degraded_ok=degraded, cache=_cache_stats())


def scenario_arxiv_repeat():
    print("\n[C] arXiv 检索 x5（模拟 %.0fms/次）" % (LAT * 1000))
    counter = {"search": 0}
    _install_arxiv_fake(counter)
    _reset_caches()
    t0 = time.time()
    for _ in range(5):
        res = extern.search_arxiv("cat:cs.AI", 3)
    dt = time.time() - t0
    _record("C_arxiv_x5", searches=counter["search"], sec=round(dt, 3),
            n=len(res), cache=_cache_stats())


def scenario_github_concurrent():
    print("\n[D] 8 线程并发同仓库查询（单飞，模拟 %.0fms/请求）" % (LAT * 1000))
    counter = {"api": 0, "readme": 0}
    _install_github_fake(counter)
    _reset_caches()
    t0 = time.time()
    out = []

    def worker():
        out.append(extern.github_repo_info("https://github.com/x/y")["stars"])

    ths = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    dt = time.time() - t0
    _record("D_github_8threads", http=counter["api"] + counter["readme"], sec=round(dt, 3),
            stars=set(out), cache=_cache_stats())


def scenario_bm25_tokenize():
    print("\n[E] BM25 分词：600 chunk 构建两次（LRU）")
    chunks = [{"text": "paper %d transformer attention is all you need " % i + "text " * 30}
              for i in range(600)]
    calls = {"n": 0}
    real = state.tokenize

    def counting(text):
        calls["n"] += 1
        return real(text)

    state.tokenize = counting
    try:
        t0 = time.time()
        state.Container._build_bm25(types.SimpleNamespace(chunks=chunks))
        t1 = time.time()
        state.Container._build_bm25(types.SimpleNamespace(chunks=chunks))
        t2 = time.time()
    finally:
        state.tokenize = real
    _record("E_bm25_600x2", tokenize_calls=calls["n"],
            sec_first=round(t1 - t0, 3), sec_second=round(t2 - t1, 3))


def scenario_embed_repeat():
    print("\n[F] 重复 embed：同 3 条文本 x5（记忆缓存，模拟 %.0fms/次）" % (LAT * 1000))
    calls = {"n": 0}

    def fake_fn(texts):
        calls["n"] += 1
        time.sleep(LAT)
        return [[0.1, 0.2, 0.3] for _ in texts]

    c = state.Container.__new__(state.Container)
    _reset_caches()
    orig = state.get_embed_fn
    state.get_embed_fn = lambda: fake_fn
    try:
        t0 = time.time()
        vecs = [c.embed(["hello", "world", "again"]) for _ in range(5)]
        dt = time.time() - t0
    finally:
        state.get_embed_fn = orig
    same = all(v == vecs[0] for v in vecs)
    _record("F_embed_x5", infer_calls=calls["n"], sec=round(dt, 3), same_result=same)


def main():
    print("=" * 72)
    print("P8 性能基准：模拟延迟 %.0fms/请求" % (LAT * 1000))
    print("=" * 72)
    scenario_github_repeat()
    scenario_github_ratelimit()
    scenario_arxiv_repeat()
    scenario_github_concurrent()
    scenario_bm25_tokenize()
    scenario_embed_repeat()
    print("\n" + "-" * 72)
    for tag, kw in RESULTS:
        print("RESULT %-18s %s" % (tag, " ".join("%s=%s" % kv for kv in kw.items())))
    print("-" * 72)


if __name__ == "__main__":
    main()

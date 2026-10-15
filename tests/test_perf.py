# -*- coding: utf-8 -*-
"""性能优化测试（P8）：core.perf 缓存语义 + extern 缓存接线 + state 热点缓存 + tasks 调度。

全部离线确定性：不发起任何真实网络请求（HTTP 层 / arxiv 模块用计数假件替换）。
覆盖重点对应本项目性能纪律：
- 缓存进出深拷贝（调用方就地改写不污染缓存）；
- 负缓存短 TTL、降级/错误字段原样保留（不洗成正式结果）；
- 单飞等待超时抛 TimeoutError（绝不返回空结果冒充成功）；
- loader 异常原样传播（绝不吞成 []/None）；
- 新函数全部有调用方（接线验证，防死代码）。
"""
import sys
import threading
import time
import types
import unittest
import urllib.error
from unittest import mock

from core.perf import TTLCache, backoff_delays, call_with_retry
import services.extern as extern
from services import state, tasks


class _FakeResp:
    """最小 urllib 响应替身（支持 with 语法与 read）。"""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------- core.perf
class TTLCacheTest(unittest.TestCase):
    def test_get_set_and_deepcopy_isolation(self):
        c = TTLCache("t", ttl=5)
        c.set("k", {"a": [1, 2]})
        got = c.get("k")
        self.assertEqual(got, {"a": [1, 2]})
        got["a"].append(3)                                # 调用方就地改写
        self.assertEqual(c.get("k"), {"a": [1, 2]})       # 缓存未被污染
        c.set("k", {"a": [9]})
        self.assertEqual(c.get("k"), {"a": [9]})          # 覆盖写同样存快照

    def test_ttl_expiry(self):
        c = TTLCache("t", ttl=0.15)
        c.set("k", 1)
        self.assertEqual(c.get("k"), 1)
        time.sleep(0.2)
        self.assertIsNone(c.get("k"))

    def test_negative_entry_short_ttl_and_fields_preserved(self):
        c = TTLCache("t", ttl=5, neg_ttl=0.2)
        loads = []

        def loader():
            loads.append(1)
            return {"degraded": True, "error": "限流", "stars": None}

        rule = lambda v: not v.get("degraded")  # noqa: E731
        r2 = None
        c.get_or_load("k", loader, should_cache=rule)
        r2 = c.get_or_load("k", loader, should_cache=rule)
        self.assertEqual(len(loads), 1)                   # 第二次负缓存命中，没有真调
        self.assertTrue(r2["degraded"])                   # 降级字段原样保留
        self.assertEqual(r2["error"], "限流")
        self.assertEqual(c.stats()["neg_hits"], 1)
        time.sleep(0.25)
        c.get_or_load("k", loader, should_cache=rule)
        self.assertEqual(len(loads), 2)                   # 负缓存过期后恢复真实调用

    def test_negative_cache_disabled_by_env_semantics(self):
        c = TTLCache("t", ttl=5, neg_ttl=0)
        loads = []
        rule = lambda v: bool(v)  # noqa: E731
        c.get_or_load("k", lambda: loads.append(1) or [], should_cache=rule)
        c.get_or_load("k", lambda: loads.append(1) or [], should_cache=rule)
        self.assertEqual(len(loads), 2)                   # neg_ttl=0：每次都真实调用

    def test_singleflight_coalesces_concurrent_loads(self):
        c = TTLCache("t", ttl=5)
        loads = []

        def loader():
            loads.append(1)
            time.sleep(0.15)
            return {"v": 42}

        results = [None] * 8

        def run(i):
            results[i] = c.get_or_load("same", loader)

        ths = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(len(loads), 1)                   # 8 并发只放 1 个真实请求
        self.assertEqual({r["v"] for r in results}, {42})
        st = c.stats()
        self.assertEqual((st["loads"], st["waits"], st["coalesced"]), (1, 7, 7))

    def test_singleflight_timeout_raises_not_empty(self):
        c = TTLCache("t", ttl=5, wait_sec=0.15)
        done = threading.Event()

        def leader():
            c.get_or_load("k", lambda: (time.sleep(0.8), "late")[1])
            done.set()

        th = threading.Thread(target=leader, daemon=True)
        th.start()
        time.sleep(0.05)                                  # 确保 leader 已在飞
        with self.assertRaises(TimeoutError):             # 等待方超时抛错，绝不是空结果
            c.get_or_load("k", lambda: "other")
        self.assertTrue(done.wait(timeout=3))
        self.assertEqual(c.get("k"), "late")              # leader 结果正常落缓存
        th.join(timeout=3)

    def test_loader_exception_propagates_and_negative_cached(self):
        c = TTLCache("t", ttl=5, neg_ttl=5)
        loads = []

        def boom():
            loads.append(1)
            raise ValueError("network down")

        with self.assertRaises(ValueError):
            c.get_or_load("k", boom)
        with self.assertRaises(ValueError):               # 负缓存重放同一异常，不重复撞墙
            c.get_or_load("k", boom)
        self.assertEqual(len(loads), 1)
        st = c.stats()
        self.assertEqual((st["neg_hits"], st["errors"]), (1, 1))

    def test_bounded_maxsize(self):
        c = TTLCache("t", ttl=5, maxsize=3)
        for i in range(5):
            c.set(i, i)
        self.assertLessEqual(c.stats()["size"], 3)
        self.assertIsNone(c.get(0))                       # 最旧的被淘汰


class RetryTest(unittest.TestCase):
    def test_backoff_delays_jittered_and_bounded(self):
        ds = backoff_delays(3, base=0.01, cap=0.02)
        self.assertEqual(len(ds), 3)
        for d in ds:
            self.assertGreaterEqual(d, 0.005)
            self.assertLessEqual(d, 0.03)

    def test_call_with_retry_gives_up_on_http_error(self):
        n = {"c": 0}

        def f():
            n["c"] += 1
            raise urllib.error.HTTPError("u", 429, "x", {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            call_with_retry(f, attempts=3, give_up_on=(urllib.error.HTTPError,))
        self.assertEqual(n["c"], 1)                       # 429 不重试（撞墙只会更糟）

    def test_call_with_retry_retries_transient_then_raises(self):
        n = {"c": 0}

        def f():
            n["c"] += 1
            raise OSError("boom")

        with self.assertRaises(OSError):
            call_with_retry(f, attempts=2, base=0.001, cap=0.002)
        self.assertEqual(n["c"], 3)                       # 首次 + 2 重试，异常原样抛出


# ---------------------------------------------------------------- extern 接线
class ExternCacheWiringTest(unittest.TestCase):
    def setUp(self):
        extern.clear_caches()

    def test_github_repo_info_cached_with_fake_http(self):
        api, readme = {"n": 0}, {"n": 0}

        def fake_open(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "api.github.com/repos/" in url:
                api["n"] += 1
                time.sleep(0.02)
                return _FakeResp(b'{"stargazers_count": 42, "forks_count": 3, '
                                 b'"open_issues_count": 1, "license": {"spdx_id": "MIT"}, '
                                 b'"pushed_at": "2026-09-01T00:00:00Z", "archived": false}')
            if "raw.githubusercontent.com" in url:
                readme["n"] += 1
                time.sleep(0.02)
                return _FakeResp(b"# Demo\n" + b"repro " * 400)
            raise AssertionError("unexpected url: " + url)

        with mock.patch.object(extern.urllib.request, "urlopen", fake_open):
            first = extern.github_repo_info("https://github.com/torvalds/linux")
            first["stars"] = 999                          # 调用方改写返回值
            second = extern.github_repo_info("https://github.com/torvalds/linux")
        self.assertEqual(first["stars"], 999)
        self.assertEqual(second["stars"], 42)             # 缓存未被污染
        self.assertEqual((api["n"], readme["n"]), (1, 1))  # 两次查询只各打 1 次
        self.assertFalse(second["degraded"])

    def test_github_rate_limit_negative_cached(self):
        def fake_403(req, timeout=None):
            raise urllib.error.HTTPError("https://api.github.com/x", 403, "Forbidden", {}, None)

        extern.clear_caches()
        with mock.patch.object(extern.urllib.request, "urlopen", fake_403):
            outs = [extern.github_repo_info("https://github.com/a/b") for _ in range(3)]
        self.assertTrue(all(o["degraded"] for o in outs))
        self.assertIn("403", outs[0]["error"])            # 真实原因透出
        self.assertEqual(outs[0]["reproducibility"], "未知")  # 不做推测性评级
        st = extern.cache_stats()["github"]
        self.assertEqual((st["loads"], st["neg_hits"]), (1, 2))  # 后两次负缓存命中

    def test_github_http_error_not_retried(self):
        seen = {"n": 0}

        def fake_429(req, timeout=None):
            seen["n"] += 1
            raise urllib.error.HTTPError("https://api.github.com/x", 429, "Too Many", {}, None)

        extern.clear_caches()
        with mock.patch.object(extern.urllib.request, "urlopen", fake_429):
            with self.assertRaises(urllib.error.HTTPError):
                extern._github_json("https://api.github.com/x")
        self.assertEqual(seen["n"], 1)

    def test_search_arxiv_and_latest_arxiv_cached(self):
        calls = {"n": 0}

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
                calls["n"] += 1
                return iter([_Page()])

        fake = types.ModuleType("arxiv")
        fake.SortCriterion = types.SimpleNamespace(Relevance="rel", SubmittedDate="sub")
        fake.Search = lambda **kw: kw  # noqa: E731
        fake.Client = _Client
        with mock.patch.dict(sys.modules, {"arxiv": fake}):
            extern.clear_caches()
            r1 = extern.search_arxiv("cat:cs.AI", 3)
            r2 = extern.search_arxiv("cat:cs.AI", 3)
            r3 = extern.latest_arxiv("cs.AI", 3)
            r4 = extern.latest_arxiv("cs.AI", 3)
        self.assertEqual(calls["n"], 2)                   # search 一次 + latest 一次，各自命中
        self.assertEqual(r1[0]["arxiv_id"], "2501.00001v1")
        self.assertEqual(r2, r1)
        self.assertEqual(r4, r3)

    def test_search_pubmed_cached(self):
        seen = {"n": 0}

        def fake_open(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            seen["n"] += 1
            if "esearch" in url:
                return _FakeResp(b'{"esearchresult": {"idlist": ["111", "222"]}}')
            return _FakeResp(b'{"result": {"111": {"title": "T1", "authors": [], '
                             b'"fulljournalname": "J", "pubdate": "2026"}, '
                             b'"222": {"title": "T2", "authors": [], '
                             b'"fulljournalname": "J", "pubdate": "2026"}}}')

        extern.clear_caches()
        with mock.patch.object(extern.urllib.request, "urlopen", fake_open):
            r1 = extern.search_pubmed("attention", 5)
            r2 = extern.search_pubmed("attention", 5)
        self.assertEqual(len(r1), 2)
        self.assertEqual(r2, r1)
        self.assertEqual(seen["n"], 2)                    # esearch+esummary 只各打 1 次

    def test_web_search_failure_negative_cached_not_whitened(self):
        seen = {"n": 0}

        def fake_down(req, timeout=None):
            seen["n"] += 1
            raise OSError("network unreachable")

        extern.clear_caches()
        with mock.patch.object(extern.urllib.request, "urlopen", fake_down):
            r1 = extern.web_search("q")
            r2 = extern.web_search("q")
        self.assertIn("error", r1[0])
        self.assertIn("error", r2[0])                     # 负缓存重放同一错误，没洗成成功
        self.assertEqual(seen["n"], 1)                    # 第二次没有真发请求


# ---------------------------------------------------------------- state 热点缓存
class StateHotspotCacheTest(unittest.TestCase):
    def test_tokenize_cache_returns_copy(self):
        a = state.tokenize("training trained transformer")
        b = state.tokenize("training trained transformer")
        self.assertEqual(a, b)
        self.assertIsNot(a, b)                            # 每次返回新 list
        a.append("POISON")
        self.assertNotIn("POISON", state.tokenize("training trained transformer"))
        self.assertGreater(state._tokenize_cached.cache_info().hits, 0)

    def test_build_bm25_second_build_all_cache_hits(self):
        chunks = [{"text": "paper %d transformer attention is all you need" % i}
                  for i in range(20)]
        with mock.patch.object(state, "tokenize", wraps=state.tokenize) as spy:
            state.Container._build_bm25(mock.Mock(chunks=chunks))
            n_first = spy.call_count
            state.Container._build_bm25(mock.Mock(chunks=chunks))
            n_second = spy.call_count
        self.assertEqual(n_first, 20)                     # 首次全量实算
        self.assertEqual(n_second, 20)                    # 第二次 0 次实算，全部 LRU 命中

    def test_embed_memory_cache(self):
        calls = {"n": 0}

        def fake_fn(texts):
            calls["n"] += 1
            return [[0.1, 0.2, 0.3] for _ in texts]

        c = state.Container.__new__(state.Container)      # 避开重构造，只测 embed
        with mock.patch.object(state, "get_embed_fn", lambda: fake_fn):
            v1 = c.embed(["hello", "world"])
            v2 = c.embed(["hello", "world"])
            v1[0][0] = 999                                # 改写返回值不污染缓存
            v3 = c.embed(["hello", "world"])
        self.assertEqual(calls["n"], 1)                   # 第二次命中记忆缓存，零推理
        self.assertEqual(v2, [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]])
        self.assertEqual(v3[0][0], 0.1)


# ---------------------------------------------------------------- tasks 调度
class TaskCenterTest(unittest.TestCase):
    def setUp(self):
        self._backup = dict(tasks._TASKS)
        tasks._TASKS.clear()

    def tearDown(self):
        tasks._TASKS.clear()
        tasks._TASKS.update(self._backup)

    def test_prune_keeps_recent_terminal_only(self):
        for i in range(80):
            tasks._TASKS["t%03d" % i] = {"id": "t%03d" % i, "state": "done",
                                         "created": float(i)}
        tasks._TASKS["run1"] = {"id": "run1", "state": "running", "created": 999.0}
        tasks._prune_locked()
        self.assertLessEqual(len(tasks._TASKS), tasks._TASK_KEEP + 1)
        self.assertIn("run1", tasks._TASKS)               # 运行中永不淘汰
        self.assertIn("t079", tasks._TASKS)               # 保留最近的
        self.assertNotIn("t000", tasks._TASKS)            # 最旧的被淘汰

    def test_queued_task_wakes_promptly_and_completes(self):
        gate = threading.Event()

        def blocker(progress):
            gate.wait(timeout=5)
            return "ok"

        def quick(progress):
            return "quick"

        t1 = tasks.submit("b1", blocker)
        t2 = tasks.submit("b2", blocker)
        self._wait_state(t1, "running")
        self._wait_state(t2, "running")
        queued = [tasks.submit("q%d" % i, quick) for i in range(3)]
        self.assertEqual(tasks.active_count(), 5)         # 2 运行 + 3 排队
        for tid in queued:                                # 排队期间保持 pending
            self.assertEqual(tasks.get(tid)["state"], "pending")
        released = time.time()
        gate.set()                                        # 空位出现 → notify 即时唤醒
        for tid in queued:
            st = self._wait_state(tid, "done")
            self.assertEqual(st["result"], "quick")
            self.assertLess(st["ended"] - released, 0.45)  # 事件唤醒，远小于 0.5s 轮询间隔

    def test_runner_exception_recorded_not_swallowed(self):
        def boom(progress):
            raise ValueError("kaboom")

        tid = tasks.submit("bad", boom)
        st = self._wait_state(tid, "error")
        self.assertIn("ValueError", st["error"])          # 异常落条目，不向外炸主线程

    @staticmethod
    def _wait_state(tid, want, timeout=8.0):
        deadline = time.time() + timeout
        st = tasks.get(tid) or {}
        while time.time() < deadline and st.get("state") != want:
            time.sleep(0.02)
            st = tasks.get(tid) or {}
        return st


if __name__ == "__main__":
    unittest.main()

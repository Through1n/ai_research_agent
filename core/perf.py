"""性能基础设施：TTL 缓存（负缓存 + 单飞 + 统计）与瞬时故障退避重试。

本模块是「工程质量-性能维度」优化的公共底座，目前被
`services/extern.py`（外部学术 API 缓存）与 `services/state.py`（分词 / 向量
记忆缓存）复用。设计上逐条对应本项目踩过的性能坑：

1. **缓存进出都深拷贝**：入站存快照、出站再复制——调用方拿到后就地改写
   （append / pop / update）永远不会污染缓存（lru_cache 返回共享可变对象坑）；
2. **负缓存短 TTL 且不洗白**：失败 / 降级 / 空结果只缓存 `neg_ttl` 秒（默认
   远小于正缓存），且**原样保留 degraded / error 等字段**——不把限流期的伪
   响应冻结成正式结果；`neg_ttl <= 0` 即彻底关闭负缓存；
3. **单飞（singleflight）**：同键并发只放一个真实请求，其余线程有界等待其
   结果；等待超时抛 `TimeoutError`——绝不返回空结果冒充成功（线程内吞异常坑）；
4. **可观测**：hits / misses / neg_hits / loads / errors / waits / coalesced
   全量计数，优化前后对比以计数器与真实调用次数为证；
5. **离线确定性**：纯内存结构、零网络路径；CI 无网环境下未命中时的行为与
   优化前完全一致（就是直接调 loader）。

参考的成熟做法：Go `x/sync/singleflight` 的请求合并、cachetools 的
TTLCache + 负缓存策略、AWS 架构博客的指数退避 + jitter 重试公式。
"""
import copy
import os
import random
import threading
import time

__all__ = ["TTLCache", "call_with_retry", "backoff_delays", "env_float", "env_int"]


def env_float(name, default):
    """读环境变量转 float；缺失或非法时回落默认值（离线/CI 环境无配置也能跑）。"""
    raw = os.getenv(name, "")
    try:
        return float(raw) if raw.strip() else float(default)
    except (TypeError, ValueError):
        return float(default)


def env_int(name, default):
    """读环境变量转 int；缺失或非法时回落默认值。"""
    raw = os.getenv(name, "")
    try:
        return int(raw) if raw.strip() else int(default)
    except (TypeError, ValueError):
        return int(default)


def backoff_delays(attempts, base=0.3, cap=2.0):
    """生成 `attempts` 次指数退避睡眠时间（秒），每次叠加 ±50% 均匀 jitter。

    jitter 的作用是避免并发线程同步重试形成「打满下游」的惊群；base/cap
    保持保守（默认 0.3s 起步、2s 封顶），失败路径最多多等几秒，且调用方
    还有负缓存兜底，不会反复惊群。
    """
    out = []
    for i in range(max(0, int(attempts))):
        d = min(float(cap), float(base) * (2 ** i))
        out.append(d * (0.5 + random.random()))
    return out


def call_with_retry(fn, attempts=1, base=0.3, cap=2.0, give_up_on=()):
    """瞬时故障重试外壳。

    - `attempts`：重试次数（不含首次），默认 1；
    - `give_up_on`：这些异常类型不重试直接抛（如 HTTP 4xx/429——撞限流墙
      重试只会更糟，应交由上层降级与负缓存处理）；
    - 其余异常（超时 / 连接重置 / DNS 瞬时失败）按指数退避 + jitter 重试，
      全部失败后抛出最后一次异常——绝不吞成空值。
    """
    last = None
    for delay in backoff_delays(attempts, base, cap) + [None]:
        try:
            return fn()
        except give_up_on:
            raise
        except Exception as e:  # noqa: BLE001  最后一次迭代原样抛出，不吞异常
            last = e
            if delay is None:
                raise
            time.sleep(delay)
    raise last  # pragma: no cover（循环结构保证不会到达）


class _Entry:
    """缓存条目：值 + 过期时刻 + 负缓存标记。"""

    __slots__ = ("value", "expires_at", "negative")

    def __init__(self, value, expires_at, negative):
        self.value = value
        self.expires_at = expires_at
        self.negative = negative


class TTLCache:
    """线程安全的 TTL 缓存：正缓存 + 负缓存 + 单飞 + 计数统计。

    参数
    ----
    name : str        缓存名（统计输出用）
    ttl : float       正缓存存活秒数
    neg_ttl : float   负缓存（失败 / 降级 / 空结果）存活秒数；<=0 关闭负缓存
    maxsize : int     条目上限，超出后按插入序淘汰最旧者（有界，防内存无界增长）
    wait_sec : float  单飞等待方最长等待秒数；超时抛 TimeoutError（不返回空）
    """

    def __init__(self, name, ttl, neg_ttl=None, maxsize=256, wait_sec=60.0):
        self.name = str(name)
        self.ttl = float(ttl)
        self.neg_ttl = float(neg_ttl if neg_ttl is not None else min(30.0, self.ttl))
        self.maxsize = max(1, int(maxsize))
        self.wait_sec = float(wait_sec)
        self._lock = threading.Lock()
        self._store = {}        # key -> _Entry（dict 保序，充当 FIFO 淘汰）
        self._err_store = {}    # key -> (exception, expires_at)  负缓存：异常短存
        self._inflight = {}     # key -> threading.Event  单飞：进行中的加载
        self._stats = {"hits": 0, "misses": 0, "neg_hits": 0, "loads": 0,
                       "errors": 0, "waits": 0, "coalesced": 0, "evictions": 0}

    # ---------- 内部方法：调用方必须持有 self._lock ----------
    def _get_locked(self, key, now):
        ent = self._store.get(key)
        if ent is None:
            return False, None, False
        if now >= ent.expires_at:
            self._store.pop(key, None)
            return False, None, False
        return True, ent.value, ent.negative

    def _put_locked(self, key, value, negative):
        if len(self._store) >= self.maxsize and key not in self._store:
            oldest = next(iter(self._store))
            self._store.pop(oldest, None)
            self._stats["evictions"] += 1
        ttl = self.neg_ttl if negative else self.ttl
        if ttl <= 0:
            return  # 负缓存关闭：不落条目，下次仍真实调用
        try:
            snapshot = copy.deepcopy(value)
        except Exception:  # noqa: BLE001  不可深拷贝的罕见对象退化为原引用（只读用法）
            snapshot = value
        self._store[key] = _Entry(snapshot, time.monotonic() + ttl, negative)

    def _put_err_locked(self, key, exc):
        if self.neg_ttl <= 0:
            return
        self._err_store[key] = (exc, time.monotonic() + self.neg_ttl)

    # ---------- 公开读取 ----------
    def get(self, key):
        """纯读取：命中返回深拷贝；未命中 / 过期返回 None。"""
        with self._lock:
            hit, value, negative = self._get_locked(key, time.monotonic())
            if not hit:
                self._stats["misses"] += 1
                return None
            self._stats["neg_hits" if negative else "hits"] += 1
            return copy.deepcopy(value)

    def set(self, key, value, negative=False):
        """显式写入（深拷贝快照）。"""
        with self._lock:
            self._put_locked(key, value, negative)

    def get_or_load(self, key, loader, should_cache=None):
        """取缓存；未命中时加载，同键并发单飞（只放一个真实请求）。

        `should_cache(value)` 返回 False 时（空结果 / 降级响应）按负缓存短
        TTL 存放。loader 抛出的异常：原样抛给调用方，并短暂负缓存同一异常，
        避免限流期并发重复撞墙；等待方重放的也是同一个异常，绝不是空值。
        """
        while True:
            with self._lock:
                now = time.monotonic()
                hit, value, negative = self._get_locked(key, now)
                if hit:
                    self._stats["neg_hits" if negative else "hits"] += 1
                    return copy.deepcopy(value)
                self._stats["misses"] += 1
                err = self._err_store.get(key)
                if err is not None:
                    if now < err[1]:
                        self._stats["neg_hits"] += 1
                        raise err[0]  # 同一异常重放：不重复撞墙，也不吞成空
                    self._err_store.pop(key, None)
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    break  # 本线程成为 leader，出去持锁执行 loader
                self._stats["waits"] += 1
            # 有界等待 leader 的结果；超时抛错，绝不返回空结果冒充成功
            if not event.wait(timeout=self.wait_sec):
                raise TimeoutError(
                    "%s 缓存单飞等待超时（%.0fs）：同键请求仍在加载，key=%r"
                    % (self.name, self.wait_sec, key))
            self._stats["coalesced"] += 1
            # leader 已完成：回到循环顶部读缓存（正缓存命中 / 负缓存 / 异常重放）
        # ---------- leader：真正调用 loader（不持锁，避免阻塞其他键） ----------
        try:
            value = loader()
        except BaseException as e:  # noqa: BLE001  负缓存后原样重抛，绝不吞成空
            with self._lock:
                self._stats["errors"] += 1
                self._inflight.pop(key, None)
                self._put_err_locked(key, e)
            event.set()
            raise
        with self._lock:
            self._stats["loads"] += 1
            negative = bool(should_cache is not None and not should_cache(value))
            self._put_locked(key, value, negative)
            self._inflight.pop(key, None)
        event.set()
        return copy.deepcopy(value)

    # ---------- 维护 ----------
    def stats(self):
        """计数器快照 + 当前条目数（供前后对比实测与报告）。"""
        with self._lock:
            out = dict(self._stats)
            out["size"] = len(self._store) + len(self._err_store)
            return out

    def clear(self):
        """清空缓存条目与负缓存（进行中的单飞不受影响）。"""
        with self._lock:
            self._store.clear()
            self._err_store.clear()

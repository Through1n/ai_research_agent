"""长文档处理的性能探针：MemoryProbe（峰值内存 / 耗时采样）。

设计目标
  1. 主后端 psutil（RSS，最贴近真实进程占用）；
  2. psutil 缺失时自动降级：Windows 无 resource 模块 → 用 tracemalloc（Python 层分配峰值）；
  3. 再不行 → 只统计耗时，peak_mb() 返回 0.0，**绝不抛异常**（页面不能因为探针崩）。

用法
    from scripts._memory_probe import MemoryProbe
    p = MemoryProbe("批量扫描 541 页"); p.start()
    for ...: p.sample()
    st.info(f"峰值内存 {p.peak_mb():.0f} MB / 耗时 {p.elapsed():.1f} 秒")
    report = p.report()      # dict
    print(p.text())          # 中文一行摘要
"""
from __future__ import annotations

import os
import time

# ---------------- 后端探测 ----------------
_HAS_PSUTIL = False
_HAS_TRACEMALLOC = False
try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except Exception:
    psutil = None  # type: ignore
try:
    import tracemalloc  # stdlib，Windows 同样可用
    _HAS_TRACEMALLOC = True
except Exception:
    tracemalloc = None  # type: ignore


def backend_name() -> str:
    if _HAS_PSUTIL:
        return "psutil"
    if _HAS_TRACEMALLOC:
        return "tracemalloc"
    return "none"


def _rss_mb() -> float:
    """当前进程 RSS（MB）；不可用时返回 0.0。"""
    if not _HAS_PSUTIL:
        return 0.0
    try:
        return psutil.Process(os.getpid()).memory_info().rss / 1048576.0
    except Exception:
        return 0.0


class MemoryProbe:
    """轻量内存/耗时探针：start() → sample() × N → peak_mb() / report() / text()。"""

    def __init__(self, label: str = ""):
        self.label = str(label or "")
        self.backend = backend_name()
        self.samples = []            # [(t_rel, mb)]
        self._t0 = None
        self._t_end = None
        self._peak = 0.0
        self._start_mb = 0.0
        self._tm_started = False

    # ---------- 生命周期 ----------
    def start(self) -> "MemoryProbe":
        try:
            if self.backend == "tracemalloc" and not tracemalloc.is_tracing():
                tracemalloc.start()
                self._tm_started = True
        except Exception:
            self._tm_started = False
        self._t0 = time.time()
        self._start_mb = self._now_mb()
        self._peak = max(self._peak, self._start_mb)
        self.samples.append((0.0, self._start_mb))
        return self

    def reset(self) -> "MemoryProbe":
        self.samples = []
        self._t0 = None
        self._t_end = None
        self._peak = 0.0
        self._start_mb = 0.0
        return self.start()

    def stop(self) -> dict:
        if self._t_end is None and self._t0 is not None:
            self.sample()
            self._t_end = time.time()
        if self._tm_started:
            try:
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
            except Exception:
                pass
            self._tm_started = False
        return self.report()

    # ---------- 采样 ----------
    def _now_mb(self) -> float:
        if self.backend == "psutil":
            return _rss_mb()
        if self.backend == "tracemalloc":
            try:
                cur, _peak = tracemalloc.get_traced_memory()
                return cur / 1048576.0
            except Exception:
                return 0.0
        return 0.0

    def sample(self, note: str = "") -> float:
        """记录一次采样，返回当前内存（MB）。未 start 时自动 start。"""
        if self._t0 is None:
            self.start()
        mb = self._now_mb()
        if mb > self._peak:
            self._peak = mb
        self.samples.append((self.elapsed(), mb))
        if len(self.samples) > 4096:      # 防止超长任务把采样表撑爆
            self.samples = self.samples[:1024] + self.samples[-2048:]
        return mb

    # ---------- 读取 ----------
    def peak_mb(self) -> float:
        try:
            self.sample()
        except Exception:
            pass
        return float(self._peak or 0.0)

    def start_mb(self) -> float:
        return float(self._start_mb or 0.0)

    def delta_mb(self) -> float:
        return float((self._peak or 0.0) - (self._start_mb or 0.0))

    def elapsed(self) -> float:
        if self._t0 is None:
            return 0.0
        end = self._t_end if self._t_end is not None else time.time()
        return max(0.0, end - self._t0)

    def report(self) -> dict:
        return {
            "label": self.label,
            "backend": self.backend,
            "start_mb": round(self.start_mb(), 1),
            "peak_mb": round(self.peak_mb(), 1),
            "delta_mb": round(self.delta_mb(), 1),
            "elapsed_s": round(self.elapsed(), 2),
            "n_samples": len(self.samples),
        }

    def text(self) -> str:
        d = self.report()
        tag = f"「{d['label']}」" if d["label"] else "任务"
        tail = "" if d["backend"] == "psutil" else f"（后端：{d['backend']}，非进程 RSS）"
        return (f"{tag} 峰值内存 {d['peak_mb']:.0f} MB / 耗时 {d['elapsed_s']:.1f} 秒"
                f"（起始 {d['start_mb']:.0f} MB，增量 {d['delta_mb']:+.0f} MB，采样 {d['n_samples']} 次）{tail}")

    def __repr__(self):  # pragma: no cover
        return f"<MemoryProbe {self.text()}>"


def friendly_note() -> str:
    """psutil 缺失时给用户的中文友好说明（不阻断主流程）。"""
    if _HAS_PSUTIL:
        return ""
    if _HAS_TRACEMALLOC:
        return ("（未安装 psutil，内存统计已降级为 tracemalloc 的 Python 层分配峰值，"
                "数值通常小于进程真实 RSS；如需精确值请安装 psutil）")
    return "（未安装 psutil 且 tracemalloc 不可用，仅统计耗时，内存显示为 0 MB）"


if __name__ == "__main__":  # 手工自验：python scripts/_memory_probe.py
    p = MemoryProbe("自验")
    p.start()
    junk = []
    for i in range(5):
        junk.append(bytearray(8 * 1024 * 1024))   # 申请 40MB
        p.sample()
    print(p.text())
    p.stop()
    print(p.report())
    print("backend =", backend_name(), friendly_note())

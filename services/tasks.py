"""轻量后台任务中心：让长任务在后台线程执行，UI 可继续操作其他页面。

背景（用户问题五/六）：此前所有长任务（Agent 执行、翻译、PDF 生成、图谱构建）
都在 Streamlit 主 rerun 线程里同步跑，跑一个任务时整个 UI 冻结、点击全部排队，
表现为「运行一个项目时不能运行另一个」；且除了零散的 spinner 没有统一的
「进行到哪个节点」可视化。

设计要点：
1. 状态存**模块级 dict + threading.Lock**，不碰 st.session_state ——
   后台线程访问 session_state 会报 missing ScriptRunContext，这是硬约束；
2. 后台线程只允许：执行 fn、写本模块 dict。所有 st.* 调用留在主线程；
3. 并发上限 MAX_PARALLEL=2（LLM 服务端对并发敏感，>4 已实测触发 429），
   超出的任务排队为 pending，UI 可见「排队中」；
4. progress(stage, pct) 回调由任务函数在关键节点调用，UI 轮询渲染；
5. 结果与异常都落盘到任务条目，UI 完成后自取——后台线程绝不抛异常出去。
"""
import threading
import time
import traceback
import uuid

_MAX_PARALLEL = 2
_LOCK = threading.Lock()
_TASKS = {}          # task_id -> dict（见 submit 注释）
_WAKER = threading.Event()   # 排队任务被唤醒用（当前实现用轮询兜底，事件仅加速）


def submit(name, fn, kind="generic", goal=""):
    """提交后台任务。

    fn: 形如 fn(progress) 的可调用，progress(stage:str, pct:float|None)。
        fn 的返回值存入任务条目 result；抛出的异常存入 error（不向外传播）。
    kind: 任务类型标记（如 "agent"），供 UI 完成回调分发。
    goal: 原始任务描述（Agent 任务转入历史记录时用）。
    返回 task_id。
    """
    tid = uuid.uuid4().hex[:12]
    item = {
        "id": tid, "name": name, "kind": kind, "goal": goal,
        "state": "pending",
        "stage": "等待调度", "pct": None,
        "result": None, "error": None,
        "created": time.time(), "started": None, "ended": None,
    }
    with _LOCK:
        running = sum(1 for t in _TASKS.values() if t["state"] == "running")
        _TASKS[tid] = item
    if running < _MAX_PARALLEL:
        _spawn(tid, fn)
    else:
        threading.Thread(target=_queued_runner, args=(tid, fn), daemon=True).start()
    return tid


def _spawn(tid, fn):
    threading.Thread(target=_worker, args=(tid, fn), daemon=True).start()


def _queued_runner(tid, fn):
    """排队等并发空位；每 0.5s 检查一次（任务量级为个位数，轮询足够）。"""
    while True:
        with _LOCK:
            running = sum(1 for t in _TASKS.values() if t["state"] == "running")
        if running < _MAX_PARALLEL:
            _spawn(tid, fn)
            return
        time.sleep(0.5)


def _worker(tid, fn):
    def progress(stage, pct=None):
        with _LOCK:
            t = _TASKS.get(tid)
            if t is not None and t["state"] == "running":
                t["stage"] = str(stage)[:120]
                if pct is None:
                    t["pct"] = None
                else:
                    try:
                        t["pct"] = max(0.0, min(1.0, float(pct)))
                    except (TypeError, ValueError):
                        t["pct"] = None

    with _LOCK:
        t = _TASKS.get(tid)
        if t is None:
            return
        t["state"] = "running"
        t["started"] = time.time()
        t["stage"] = "启动中"
    try:
        result = fn(progress)
        with _LOCK:
            t = _TASKS.get(tid)
            if t is not None:
                t["result"] = result
                t["state"] = "done"
                t["stage"] = "已完成"
                t["pct"] = 1.0
                t["ended"] = time.time()
    except Exception as e:
        with _LOCK:
            t = _TASKS.get(tid)
            if t is not None:
                t["error"] = "%s: %s" % (type(e).__name__, e)
                t["traceback"] = "".join(traceback.format_exception_only(type(e), e))[:500]
                t["state"] = "error"
                t["stage"] = "执行失败"
                t["ended"] = time.time()
    finally:
        _WAKER.set()


def get(tid):
    with _LOCK:
        t = _TASKS.get(tid)
        return dict(t) if t else None


def mark_consumed(tid):
    """UI 已把完成任务的结果转入页面内容，面板不再重复展开/回调。"""
    with _LOCK:
        t = _TASKS.get(tid)
        if t is not None:
            t["consumed"] = True


def snapshot(limit=20):
    """按创建时间倒序返回最近任务（供 UI 面板渲染）。"""
    with _LOCK:
        items = sorted(_TASKS.values(), key=lambda t: t["created"], reverse=True)
        return [dict(t) for t in items[:limit]]


def running_count():
    with _LOCK:
        return sum(1 for t in _TASKS.values() if t["state"] in ("running", "pending"))


def elapsed_str(sec):
    if sec is None:
        return ""
    sec = int(sec)
    if sec < 60:
        return "%d 秒" % sec
    return "%d 分 %02d 秒" % (sec // 60, sec % 60)

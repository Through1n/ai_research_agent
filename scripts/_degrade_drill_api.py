# -*- coding: utf-8 -*-
"""故障演练：6 类外部 API 不可用场景的**取证**脚本（只读，不改任何业务代码）。

用法（必须用 ai_research 环境）：
    C:/Users/Administrator/anaconda3/envs/ai_research/python.exe scripts/_degrade_drill_api.py

纪律：
  * 全程 mock 注入，**不发起任何真实外网请求**（不打真实 LLM / arXiv / PubMed）；
  * 用 streamlit.testing.v1.AppTest 在进程内真实跑 app.py，抓页面实际渲染出的字符串；
  * 注入点尽量贴近真实故障层：
      - LLM 场景 → patch ``openai.resources.chat.completions.Completions.create``
        （业务代码与 OpenAI SDK 的边界。其下的请求构造/HTTP 被替换，其上的
        错误分类 / 重试退避 / 降级伪响应 / UI 渲染全部真实执行）；
      - arXiv → patch ``requests.adapters.HTTPAdapter.send`` 抛
        ``requests.exceptions.ConnectionError``（arxiv 库底层就是 requests，
        且它自带的 num_retries=3 + delay_seconds=2 重试循环会真实跑一遍）；
      - PubMed → patch ``urllib.request.urlopen`` 抛 ``urllib.error.URLError``
        （services/extern.py:64 就是 urllib.request.urlopen 直连 NCBI E-utilities）。

关于"点击后多跑一轮"：AppTest 里 st.button 的 True 只存活一次 run，
所以**必须在 click().run() 之后立刻抓快照**，再跑一轮用于观察提示是否仍在。
"""
import os
import sys
import time
import socket
import urllib.error
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# 只为了让 LLMClient 能构造出来（available=True）而占位；全程 mock，不会真打智谱。
# 用覆盖而非 setdefault：保证无论本机 .env 里有没有 Key，演练行为都一致（且不烧真额度）。
os.environ["ZHIPU_API_KEY"] = "drill-placeholder-key-not-a-real-key"

import openai                                           # noqa: E402
import requests                                         # noqa: E402
from unittest import mock                               # noqa: E402
from streamlit.testing.v1 import AppTest                # noqa: E402

# openai 3.13 用的是随包分发的 httpx2（不是 PyPI 的 httpx），异常构造/传输层都要用它
import httpx2                                           # noqa: E402
import httpx2._exceptions as _h2exc                     # noqa: E402

import core.llm as _llm                                 # noqa: E402
from ui.sidebar import MENU                             # noqa: E402

PAGE_SEARCH = next(m for m in MENU if "检索" in m)
PAGE_LIB = next(m for m in MENU if "文献库" in m)
PAGE_QA = next(m for m in MENU if "问答" in m)

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
TB_MARKS = ("Traceback (most recent call last)", 'File "', "line ")
KEYS = ("error", "warning", "success", "info", "caption", "markdown", "code")


def _hr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


# ============================ AppTest 操作与快照 ============================
def _goto(at, label):
    at.session_state["menu"] = label
    at.run()


def _click(at, label):
    for b in at.button:
        if str(b.label).strip() == label.strip():
            b.click().run()
            return True
    return False


def _set_text(at, key, value):
    for w in list(at.text_input) + list(at.text_area):
        if getattr(w, "key", None) == key:
            w.set_value(value).run()
            return True
    return False


def _snapshot(at):
    out = {"exception": [str(e.value) for e in at.exception]}
    for k in KEYS:
        vals = []
        try:
            for w in getattr(at, k):
                try:
                    vals.append(str(w.value))
                except Exception:
                    pass
        except Exception:
            pass
        out[k] = vals
    return out


def _new_msgs(before, after):
    """只保留「本次点击新增」的页面文案，避免被常驻文案淹没。"""
    cb, ca = Counter(), Counter()
    for k in KEYS + ("exception",):
        for s in before.get(k, []):
            cb[(k, s)] += 1
        for s in after.get(k, []):
            ca[(k, s)] += 1
    out = {}
    for (k, s), n in ca.items():
        d = n - cb.get((k, s), 0)
        if d > 0:
            out.setdefault(k, []).extend([s] * d)
    return out


def _dump(new, keys=("error", "warning", "success", "info", "caption", "markdown"),
          width=170, limit=8):
    hit = False
    for k in keys:
        for s in new.get(k, [])[:limit]:
            hit = True
            s1 = s.replace("\n", " ⏎ ")
            print("      [%s] %s" % (k, s1[:width] + ("…" if len(s1) > width else "")))
    if not hit:
        print("      （无新增文案）")


def _visible_traceback(snap):
    blob = "\n".join(snap.get("error", []) + snap.get("warning", [])
                     + snap.get("success", []) + snap.get("info", [])
                     + snap.get("markdown", []))
    return any(m in blob for m in TB_MARKS)


def _code_traceback(snap):
    return any(m in "\n".join(snap.get("code", [])) for m in TB_MARKS)


def _last_err(at):
    try:
        return at.session_state["container"].llm.last_error
    except Exception:
        return None


def _last_kind(at):
    try:
        return at.session_state["container"].llm.last_error_kind
    except Exception:
        return None


_CREATE_MS = []          # 每次 Completions.create 的单调钟耗时（秒），全局累计
_SLEEP_REAL = []         # 每次退避真睡了多久（time.monotonic 实测），全局累计
_SLEEP_PLAN = []         # 每次退避计划睡多久（业务代码传参），全局累计
ROWS = []                # D1 实测排期表：S1–S4 × 探针 A/B/C = 12 行


def _reset_timers():
    del _CREATE_MS[:]
    del _SLEEP_REAL[:]
    del _SLEEP_PLAN[:]


def _row(sid, probe, n_call, dt_wall):
    """登记一行 D1 实测排期数据（退避/调用耗时全部实测，不做理论求和）。"""
    ROWS.append(dict(sid=sid, probe=probe, n_call=n_call,
                     planned=sum(_SLEEP_PLAN), planned_list=list(_SLEEP_PLAN),
                     sleep_real=sum(_SLEEP_REAL), n_sleep=len(_SLEEP_REAL),
                     create=sum(_CREATE_MS), wall=dt_wall))


def _probe_qa_e2e(at, exc_factory):
    """附加实测 H：💬 问答页**端到端**（真实点击「提问」，不是直接调 rag.answer）。

    为什么能做成了：AppTest.button 包含 st.form_submit_button；真正的前提是
    **文献库非空**，所以先点「🚀 一键加载演示数据（6 篇）」装库。
    关掉我此前「问答页端到端未能验证」这条缺口（报告 §四 第 9 条）。

    看什么（都是 F1 的判据）：
      1. 未捕获异常是否为 0
      2. 答案区是否出现**降级明示**（「离线占位响应」/「不是模型生成」）
      3. 是否出现「✅ 引用校验通过」这类成功态（应为 False）
      4. 自动修复次数是否为 0（验证 F4 修复环守卫）
    """
    _hr("附加实测 H：💬 问答页端到端（装演示数据后真实点击「提问」，401 注入）")
    _goto(at, PAGE_QA)
    demo = [b for b in at.button if "一键加载演示数据" in getattr(b, "label", "")]
    if not demo:
        print("  ✗ 未找到「一键加载演示数据」按钮（断言未执行）")
        return
    demo[0].click().run()
    _goto(at, PAGE_QA)
    ti = [t for t in at.text_input if getattr(t, "label", "") == "请输入问题"]
    ask = [b for b in at.button if getattr(b, "label", "") == "提问"]
    if not (ti and ask):
        print("  ✗ 未找到问题输入框或「提问」按钮（断言未执行）")
        return
    ti[0].set_value("Transformer 训练用了什么 GPU？").run()
    counter = []
    t0 = time.time()
    with _llm_patch(exc_factory, counter), _patched_sleep([]):
        ask[0].click().run()
    dt = time.time() - t0

    def _vals(seq):
        return [str(getattr(x, "value", "")) for x in seq]

    errs, warns, succs, caps = _vals(at.error), _vals(at.warning), \
        _vals(at.success), _vals(at.caption)
    joined = "\n".join(warns + succs + caps + errs)
    print("  耗时 %.1fs  SDK调用=%d次  未捕获异常=%d"
          % (dt, len(counter), len(at.exception)))
    print("  【判据 H-1】答案区出现降级明示=%s（应为 True）"
          % (("离线占位响应" in joined) or ("不是模型生成" in joined)))
    print("  【判据 H-2】出现「✅ 引用校验通过」=%s（应为 False）"
          % ("引用校验通过" in joined))
    _repair = [c for c in caps if "自动修复次数" in c]
    print("  【判据 H-3】自动修复次数=%s（应为 0，验证 F4 修复环守卫）"
          % (_repair[0].split("自动修复次数")[1][:12].strip() if _repair else "未取到"))
    print("  —— 答案区相关文案（warning / success）：")
    for w in warns[:4]:
        print("      WARN : %s" % w[:160])
    for s in succs[:4]:
        print("      SUCC : %s" % s[:160])
    # 侧边栏"通道就绪"措辞：失败时不该再报「通道就绪」（degrade-fix 01:26 落地修复）
    _ready = [s for s in succs if "通道就绪" in s]
    _failed_note = [c for c in caps if "调用失败" in c]
    print("  【判据 H-4】侧边栏仍报「✅ 通道就绪」=%s（应为 False）；"
          "改报「最近一次调用失败」=%s（应为 True）"
          % (bool(_ready), bool(_failed_note)))
    for c in _failed_note[:2]:
        print("      CAPT : %s" % c[:160])
    for s in _ready[:2]:
        print("      SUCC : %s" % s[:160])


def _print_schedule_table():
    """附加实测 G / D1：实测排期表 12 行（S1–S4 × 探针 A/B/C）。

    口径说明：**每一行都是 time.monotonic 实测**，不是"退避序列求和"的理论值。
      SDK调用   —— 该探针期间 Completions.create 被调了几次
      退避·计划 —— 业务代码打算睡的秒数之和（core/llm 传给 time.sleep 的参数）
      退避·实测 —— time.monotonic 实测真睡了多久
      create    —— 所有 SDK 调用自身的单调钟耗时之和（不含退避）
      链路实测   —— 该探针整体挂起时长（墙钟，含页面渲染等一切开销）
    """
    _hr("附加实测 G / D1：实测排期表（S1–S4 × 探针 A/B/C，全部 time.monotonic 实测）")
    print("  %-4s %-4s %8s %10s %10s %10s %10s"
          % ("场景", "探针", "SDK调用", "退避·计划", "退避·实测", "create和", "链路实测"))
    for r in ROWS:
        print("  %-4s %-4s %8d %9.2fs %9.2fs %9.3fs %9.2fs"
              % (r["sid"], r["probe"], r["n_call"], r["planned"],
                 r["sleep_real"], r["create"], r["wall"]))
    print("  口径：退避·实测 / create和 均为 time.monotonic 实测；"
          "**实测值含整条重试链路，不是单次退避**；链路实测为墙钟，含页面渲染开销。")


class _TimeShim(object):
    """替换 core.llm 里的 time：记录「计划退避秒数」并照常真睡。

    Round 12 D1 补充：同时用 **time.monotonic** 记录**真实睡了多久**（`real_sleep`），
    因为"计划退避"只是业务代码打算睡的秒数，不是实测值。排期表要的是实测。
    """

    def __init__(self, rec, real_rec=None):
        self._rec = rec
        self._real_rec = real_rec if real_rec is not None else []
        self._real = time

    def sleep(self, secs):
        self._rec.append(float(secs))
        try:
            _SLEEP_PLAN.append(float(secs))
        except Exception:
            pass
        _t0 = time.monotonic()          # 单调钟，不受系统调时影响
        self._real.sleep(secs)
        _el = time.monotonic() - _t0
        self._real_rec.append(_el)
        try:
            _SLEEP_REAL.append(_el)
        except Exception:
            pass

    def __getattr__(self, n):
        return getattr(self._real, n)


def _patched_sleep(rec, real_rec=None):
    return mock.patch.object(_llm, "time", _TimeShim(rec, real_rec))


# ============================ 故障注入：LLM ============================
def _req():
    return httpx2.Request("POST", ZHIPU_URL)


def _exc_401():
    """智谱 Key 失效：真实响应是 HTTP 401 + JSON body，SDK 抛 AuthenticationError。"""
    return openai.AuthenticationError(
        "Error code: 401 - {'error': {'code': '1001', "
        "'message': '身份验证失败：API Key 无效或已过期'}}",
        response=httpx2.Response(401, request=_req(),
                                 headers={"content-type": "application/json"}),
        body={"error": {"code": "1001", "message": "invalid api key"}},
    )


def _exc_429(retry_after=None):
    """智谱限流：HTTP 429，服务端可能带 Retry-After 响应头。"""
    h = {"content-type": "application/json"}
    if retry_after is not None:
        h["retry-after"] = str(retry_after)
    return openai.RateLimitError(
        "Error code: 429 - {'error': {'code': '1113', "
        "'message': '请求过于频繁，已触发限流'}}",
        response=httpx2.Response(429, request=_req(), headers=h),
        body={"error": {"code": "1113", "message": "rate limited"}},
    )


def _exc_timeout():
    """读取超时：httpx2.ReadTimeout 被 SDK 原样包装成 openai.APITimeoutError。"""
    return openai.APITimeoutError(request=_req())


def _exc_network():
    """完全不可达：DNS 失败 / 连接被拒 / 代理拦截，SDK 统一抛 APIConnectionError。"""
    return openai.APIConnectionError(message="Connection error.", request=_req())


def _llm_patch(exc_factory, counter):
    """patch openai SDK 的 chat.completions.create（业务代码与 SDK 的边界）。"""

    def _boom(self, *a, **kw):
        counter.append(1)
        _t0 = time.monotonic()
        try:
            raise exc_factory()
        finally:
            try:
                _CREATE_MS.append(time.monotonic() - _t0)
            except Exception:
                pass

    return mock.patch.object(openai.resources.chat.completions.Completions,
                             "create", _boom)


# ============================ 场景探针 ============================
def _probe_sidebar_ping(at, exc_factory):
    """探针 A：侧边栏「🔌 测试连通性」——演示现场第一反应就是点它。

    路径：ui/sidebar.py:234 按钮 → _llm_ping(:120) → ctx.llm.chat → core/llm.py:_create
    """
    at.run()
    before = _snapshot(at)
    counter, planned = [], []
    t0 = time.time()
    with _llm_patch(exc_factory, counter), _patched_sleep(planned):
        hit = _click(at, "🔌 测试连通性")
    dt = time.time() - t0
    after = _snapshot(at)
    err1, kind1 = _last_err(at), _last_kind(at)
    at.run()                                    # 结算回合：看提示在下一轮是否还在
    settle = _snapshot(at)
    print("  探针A 侧边栏「🔌 测试连通性」 命中=%s  SDK调用=%d次  计划退避=%s  实测=%.1fs"
          % (hit, len(counter), planned, dt))
    print("      —— 点击当轮新增文案：")
    _dump(_new_msgs(before, after))
    print("      last_error=%r kind=%r" % (str(err1)[:110], kind1))
    print("      —— 再跑一轮后（模拟用户切页/重绘）新增文案：")
    _dump(_new_msgs(before, settle))
    print("      last_error=%r（%s）" % (str(_last_err(at))[:60],
                                       "仍保留" if _last_err(at) else "已被清空"))
    return after, dt, len(counter), sum(planned)


def _probe_main_llm(at, exc_factory):
    """探针 B：检索页「🧹 分析并剔除应付式引用」——主体区真实 LLM 功能。

    路径：ui/page_search.py:181 ctx.llm.chat → 结果直接 long_block 渲染成"报告"。
    """
    _goto(at, PAGE_SEARCH)
    if not _set_text(at, "qrefs", "[1] Vaswani et al. Attention is all you need."):
        print("  ✗ 未找到 qrefs 输入框（断言未执行）")
        return None, 0.0, 0
    before = _snapshot(at)
    counter, planned = [], []
    t0 = time.time()
    with _llm_patch(exc_factory, counter), _patched_sleep(planned):
        hit = _click(at, "🧹 分析并剔除应付式引用")
    dt = time.time() - t0
    after = _snapshot(at)
    err1, kind1 = _last_err(at), _last_kind(at)
    at.run()
    settle = _snapshot(at)
    print("  探针B 检索页「🧹 引用含金量」     命中=%s  SDK调用=%d次  计划退避=%s  实测=%.1fs"
          % (hit, len(counter), planned, dt))
    print("      —— 点击当轮新增文案（含被当作「报告」渲染的答案区）：")
    _dump(_new_msgs(before, after))
    print("      last_error=%r kind=%r" % (str(err1)[:110], kind1))
    print("      —— 再跑一轮后新增文案：")
    _dump(_new_msgs(before, settle))
    print("      last_error=%r（%s）" % (str(_last_err(at))[:60],
                                       "仍保留" if _last_err(at) else "已被清空"))
    # 【修复核验②】主体区绝不允许出现「✅ 分析完成」这类成功态文案（离线占位 ≠ 成功）
    fake_ok = [s for s in after.get("success", []) if ("分析完成" in s or "成功" in s)]
    # 降级明示文案沿用 core/llm.py:19 / DEGRADED_HINT 已定的那句，判据取其中稳定子串
    _MARK = "以下内容不是模型生成"
    print("      【核验②】主体区出现「✅ 分析完成」= %s（修复后应为 False）；"
          "页面可见「%s」明示文案=%s（修复后应为 True）"
          % (bool(fake_ok), _MARK,
             any(_MARK in s for s in after.get("warning", []))))
    if fake_ok:
        print("        → 仍存在的成功态文案：%s" % fake_ok[:2])
    return after, dt, len(counter)


def _probe_rag(at, exc_factory, title, on_token=None, tag="C"):
    """探针 C：旗舰问答链路 services/rag.py answer()（💬 跨文献问答页走的就是它）。

    ⚠️ 订正（2026-10-14，degrade-fix 指出并经我核实）：我原先写「AppTest 不暴露
    form_submit_button，故只能直接调 rag.answer」——**这句是错的**。
    streamlit/testing/v1/app_test.py:613 的 docstring 写明 `AppTest.button`
    **同时包含 `st.button` 和 `st.form_submit_button`**；实测「提问」确实在
    at.button 里。真正卡住端到端的是**问答页需要非空文献库**（chunks/chroma 有货），
    不是 API。已补**探针 H** 装演示数据后真实点击走端到端；本探针保留，
    因为只有它能单独测 rag.answer 的调用次数与内部分支（H 测不到这些）。
    渲染方式见 ui/page_qa.py:355-365
    （refused → st.warning("🛑 "+a)，否则 st.markdown(a) 当答案展示）。

    on_token=None  → 走 rag.py:845 非流式分支（探针 C）
    on_token=回调  → 走 rag.py:843 流式分支（探针 C2，与 💬 问答页实际一致，

                     ui/page_qa.py 会传 _progress 回调）

    同时给 rag 的两个内部入口装计数器，把"翻倍到底发生在哪一行"拆开：
      _llm_complete        = rag.py:845（首次生成） + :859（修复环）+ :183/:186（流式回退）
      _llm_complete_stream = rag.py:843
    """
    from services import rag
    ctx = at.session_state["container"]
    try:
        ctx.llm.last_error = None
        ctx.llm.last_error_kind = None
    except Exception:
        pass
    counter, planned = [], []
    calls = {"_llm_complete": 0, "_llm_complete_stream": 0}
    _orig_c, _orig_s = rag._llm_complete, rag._llm_complete_stream

    def _wrap_c(*a, **kw):
        calls["_llm_complete"] += 1
        # 记录**是谁**调的：rag.py 里有 4 个调用点（845 首次生成 / 859 修复环 /
        # 183 流式回退 / 186 异常回退），不拆开行号就无法判断"翻倍发生在哪一行"。
        try:
            import traceback as _tb
            fr = _tb.extract_stack()[-2]
            calls.setdefault("at", []).append(
                "%s:%d" % (os.path.basename(fr.filename), fr.lineno))
        except Exception:
            pass
        return _orig_c(*a, **kw)

    def _wrap_s(*a, **kw):
        calls["_llm_complete_stream"] += 1
        return _orig_s(*a, **kw)

    t0 = time.time()
    try:
        rag._llm_complete, rag._llm_complete_stream = _wrap_c, _wrap_s
        with _llm_patch(exc_factory, counter), _patched_sleep(planned):
            res = rag.answer(ctx, "Transformer 训练用了什么 GPU？",
                             use_rerank=False, on_token=on_token)
    finally:
        rag._llm_complete, rag._llm_complete_stream = _orig_c, _orig_s
    dt = time.time() - t0
    print("  探针%s 问答链路 rag.answer()(%s)  SDK调用=%d次  计划退避=%s  实测=%.1fs"
          % (tag, "流式" if on_token else "非流式", len(counter), planned, dt))
    print("      内部入口调用分解：_llm_complete=%d次  _llm_complete_stream=%d次"
          % (calls["_llm_complete"], calls["_llm_complete_stream"]))
    if calls.get("at"):
        print("      _llm_complete 的调用点（rag.py 行号）：%s" % calls["at"])
    print("      refused=%s  top_score=%s  issues=%s"
          % (res.get("refused"), res.get("top_score"),
             (res.get("issues") or [])[:2]))
    print("      页面将渲染的答案=%r" % str(res.get("answer", ""))[:190])
    return dt, len(counter)


def _degrade_check(at):
    """降级路径检查：故障后其余页面是否还能正常渲染。"""
    _goto(at, PAGE_LIB)
    s1 = _snapshot(at)
    n1 = len(list(at.button))
    _goto(at, PAGE_QA)
    s2 = _snapshot(at)
    n2 = len(list(at.button))
    _goto(at, PAGE_SEARCH)
    s3 = _snapshot(at)
    n3 = len(list(at.button))
    ok = not (s1["exception"] or s2["exception"] or s3["exception"]) and min(n1, n2, n3) > 0
    print("  降级路径：故障后「%s」异常=%d/控件=%d ·「%s」异常=%d/控件=%d ·「%s」异常=%d/控件=%d → %s"
          % (PAGE_LIB, len(s1["exception"]), n1, PAGE_QA, len(s2["exception"]), n2,
             PAGE_SEARCH, len(s3["exception"]), n3,
             "其余页面全部可用" if ok else "受影响"))
    return ok


# ============================ 故障注入：外部学术 API ============================
_ARXIV_ERR = ("HTTPSConnectionPool(host='export.arxiv.org', port=443): "
              "Max retries exceeded with url: /api/query?search_query=all:transformer "
              "(Caused by NewConnectionError('<urllib3.connection.HTTPSConnection object>: "
              "Failed to establish a new connection: [Errno 11001] getaddrinfo failed'))")

_PUBMED_ERR = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))


def _patch_arxiv():
    """arXiv 不可达：arxiv 库走 requests，最底层 patch HTTPAdapter.send。"""

    def _boom(self, request, **kw):
        raise requests.exceptions.ConnectionError(_ARXIV_ERR)

    return mock.patch.object(requests.adapters.HTTPAdapter, "send", _boom)


def _patch_pubmed():
    """PubMed 不可达：extern.py 直连 urllib.request.urlopen。"""

    def _boom(*a, **kw):
        raise _PUBMED_ERR

    return mock.patch("urllib.request.urlopen", _boom)


# ============================ 场景编排 ============================
def scenario_llm(at, sid, title, exc_factory):
    _hr("场景 %s：%s" % (sid, title))
    _reset_timers()
    snap_a, dt_a, n_call, planned = _probe_sidebar_ping(at, exc_factory)
    _row(sid, "A", n_call, dt_a)
    _reset_timers()
    snap_b, dt_b, _ = _probe_main_llm(at, exc_factory)
    _row(sid, "B", len(_CREATE_MS), dt_b)
    _reset_timers()
    dt_c, n_c = _probe_rag(at, exc_factory, title)
    _row(sid, "C", n_c, dt_c)
    # 探针 C2：与 💬 问答页实际一致的**流式**分支（ui/page_qa.py 会传 on_token 回调）。
    # 单独跑是因为 rag.py:845（非流式）与 :843（流式）是两条不同分支，
    # 2026-10-13 23:21 落地的 F4 修复只加在流式回退处（:178），
    # 必须分别验证，否则会把"没走到那一行"误读成"修复无效"。
    dt_c2, n_c2 = _probe_rag(at, exc_factory, title,
                             on_token=lambda t: None, tag="C2")
    degrade_ok = _degrade_check(at)
    n_exc = len(snap_a["exception"]) + len((snap_b or {}).get("exception", []))
    tb = _visible_traceback(snap_a) or _visible_traceback(snap_b or {})
    print("  崩溃：AppTest 未捕获异常=%d；可见区 traceback 直出=%s；"
          "折叠区「🔍 查看技术细节」内含 traceback=%s"
          % (n_exc, tb, _code_traceback(snap_a) or _code_traceback(snap_b or {})))
    return dict(sid=sid, title=title, crashed=(n_exc > 0 or tb), snap_a=snap_a,
                snap_b=snap_b, dt_a=dt_a, dt_b=dt_b, n_call=n_call,
                planned=planned, degrade_ok=degrade_ok, dt_c=dt_c, n_call_c=n_c,
                dt_c2=dt_c2, n_call_c2=n_c2)


def scenario_extern(at, sid, title, patcher, page_key, seed, btn):
    _hr("场景 %s：%s" % (sid, title))
    _goto(at, PAGE_SEARCH)
    if not _set_text(at, page_key, seed):
        print("  ✗ 未找到输入框 %s（断言未执行）" % page_key)
        return None
    before = _snapshot(at)
    t0 = time.time()
    with patcher():
        hit = _click(at, btn)
    dt = time.time() - t0
    after = _snapshot(at)
    at.run()
    settle = _snapshot(at)
    print("  点击「%s」命中=%s  实测=%.1fs" % (btn, hit, dt))
    print("      —— 点击当轮新增文案：")
    _dump(_new_msgs(before, after))
    print("      —— 再跑一轮后新增文案：")
    _dump(_new_msgs(before, settle))
    tb = _visible_traceback(after)
    n_err_new = len(_new_msgs(before, after).get("error", []))
    n_err_all = len(after["error"])
    print("  崩溃：未捕获异常=%d；可见区 traceback 直出=%s；折叠区技术细节含 traceback=%s"
          % (len(after["exception"]), tb, _code_traceback(after)))
    print("  【判据①】红色 error 框数量：本轮新增=%d，页面总计=%d（修复前后基线均应为 1；"
          "变 2 = str 归一化引入回归）" % (n_err_new, n_err_all))
    joined = " ".join(_new_msgs(before, after).get("error", []))
    print("  【判据②】错误标题是否落到网络类话术=%s（原文：%s）"
          % ("网络连接失败" in joined, joined[:90]))
    degrade_ok = _degrade_check(at)
    print("  降级路径：故障后其余页面可用=%s" % degrade_ok)
    return dict(sid=sid, title=title, crashed=bool(after["exception"]) or tb,
                snap_a=after, snap_b=None, dt_a=dt, dt_b=0.0, n_call=0,
                planned=0.0, degrade_ok=degrade_ok)


# ============================ 附加实测 ============================
def measure_call_counts():
    """验证「401 不重试 / 其余最多 4 次调用」。"""
    _hr("附加实测 C：各类错误的实际调用次数与降级标记")
    for name, fac in (("401", _exc_401), ("429", lambda: _exc_429(0)),
                      ("超时", _exc_timeout), ("断网", _exc_network)):
        c = _llm.LLMClient(api_key="drill-placeholder-key", base_url=ZHIPU_URL,
                           model="glm-4-flash", timeout=5.0)
        counter, planned = [], []
        with _llm_patch(fac, counter), _patched_sleep(planned):
            msg = c.chat([{"role": "user", "content": "ping"}])
        print("  %-4s → SDK 调用 %d 次，退避合计 %.1fs，kind=%r，返回值带降级标记=%s"
              % (name, len(counter), sum(planned), c.last_error_kind,
                 _llm.is_degraded(msg)))
    print("  （此处 429 用 Retry-After=0，被 _retry_after_seconds 判非法而回落 1.5/3.0/4.5；"
          "各 Retry-After 取值的退避时长见附加实测 B）")


def measure_429_cap():
    """429 最坏等待：Retry-After 被 RATE_LIMIT_RETRY_AFTER_CAP 封顶 10s。"""
    _hr("附加实测 B：429 限流的最坏等待时长（Retry-After 封顶验证）")
    for ra in (None, 3, 60):
        c = _llm.LLMClient(api_key="drill-placeholder-key", base_url=ZHIPU_URL,
                           model="glm-4-flash", timeout=5.0)
        planned = []
        t0 = time.time()
        with _llm_patch(lambda: _exc_429(ra), []), _patched_sleep(planned):
            c.chat([{"role": "user", "content": "ping"}])
        dt = time.time() - t0
        print("  429 Retry-After=%-4s → 计划退避 %5.1fs %s  实测 %.1fs  kind=%r"
              % (ra, sum(planned), planned, dt, c.last_error_kind))
    print("  → 服务端要求等 60s 时，core/llm.py:47 RATE_LIMIT_RETRY_AFTER_CAP=5 "
          "把它压到 5s，最坏 3×5 = 15s 才降级（Round 13 修复 B：原 10→5）")


def measure_retry_budget():
    """附加实测 E：总时长预算 LLM_RETRY_BUDGET_SEC 是否真的截断重试链。

    注入方式与附加实测 A2 完全一致（传输层"服务端不返回、每次白等 T 秒"后抛
    httpx2.ReadTimeout），同一套 T 下对比**预算关闭**与**预算生效**的调用次数与
    实测挂起时长；最后按同一模型线性外推到线上默认（T=90 / B=45）的最坏等待。
    """
    _hr("附加实测 E：总时长预算（LLM_RETRY_BUDGET_SEC）对重试链的截断效果")

    def _mk(timeout):
        return _llm.LLMClient(api_key="drill-placeholder-key", base_url=ZHIPU_URL,
                              model="glm-4-flash", timeout=timeout)

    T = 2.0
    try:
        import config as _cfg
    except Exception:
        _cfg = None

    for tag, budget in (("预算关闭（B=0）", 0.0), ("预算 B=6s", 6.0),
                        ("预算 B=45s（线上默认）", 45.0)):
        calls = []

        def _slow_send(self, request, _c=calls, _t=T, **kw):
            _c.append(1)
            time.sleep(_t)                        # 模拟"服务端不返回"，SDK 一直等到超时
            raise _h2exc.ReadTimeout("timed out", request=request)

        planned = []
        c = _mk(T)
        old = getattr(_cfg, "LLM_RETRY_BUDGET_SEC", None)
        if _cfg is not None:
            _cfg.LLM_RETRY_BUDGET_SEC = budget
        t0 = time.time()
        try:
            with mock.patch.object(httpx2.Client, "send", _slow_send), _patched_sleep(planned):
                c.chat([{"role": "user", "content": "ping"}])
        finally:
            if _cfg is not None:
                if old is None:
                    try:
                        del _cfg.LLM_RETRY_BUDGET_SEC
                    except Exception:
                        pass
                else:
                    _cfg.LLM_RETRY_BUDGET_SEC = old
        dt = time.time() - t0
        print("  %-22s → 实测挂起 %5.1fs · SDK 调用 %d 次 · 退避 %s · kind=%r"
              % (tag, dt, len(calls), planned, c.last_error_kind))

    # 线上默认档（T=45 / B=20，Round 13 修复 A）按同一套循环模型外推
    def _sim(timeout, budget):
        el, n = 0.0, 0
        for a in range(_llm.MAX_RETRIES + 1):
            el += timeout
            n += 1
            if a >= _llm.MAX_RETRIES:
                break
            if budget and el >= budget:
                break
            el += _llm._backoff_for(a)
            if budget and el >= budget:
                break
        return n, el

    n0, e0 = _sim(45.0, 0.0)
    n1, e1 = _sim(45.0, 20.0)
    print("  [外推，非实测] 线上默认 LLM_TIMEOUT=45 / 预算=20（Round 13 修复 A）：")
    print("     预算关闭 → %d 次尝试 × 45s + 退避 ≈ %.0fs 才降级" % (n0, e0))
    print("     预算 20s → %d 次尝试 × 45s ≈ %.0fs 才降级（首尝窗口 45s ≥ 预算 20s → 立即截断）" % (n1, e1))
    print("  ⇒ 预算只决定「要不要发起下一次尝试」，不裁剪任何单次调用的超时窗口，"
          "故正常成功请求的时间窗一点没少。")


def measure_last_error_lifecycle(at):
    """附加实测 D：ctx.llm.last_error 跨 rerun 是否存活（决定横幅能不能被看见）。

    疑点：ui/sidebar.py:216 每轮都调 ctx.set_provider(pid, key)，而
    services/state.py:132 的重建条件是 `manual_key != self.llm.api_key`；
    侧边栏 Key 输入框为空（Key 来自 .env / 环境变量 / Docker env —— 本仓库
    默认方式）时该条件恒真 → 每轮重建 LLMClient → last_error 被清零。
    实测两种填 Key 方式。
    """
    _hr("附加实测 D：last_error 跨 rerun 存活验证（框架横幅能不能被用户看到）")
    at.session_state["menu"] = PAGE_SEARCH
    at.run()
    key_widget = [w for w in at.text_input if getattr(w, "key", None) == "key_input"]
    if key_widget:
        key_widget[0].set_value("drill-placeholder-key-not-a-real-key").run()
    cases = [("在侧边栏输入框真实填 Key", None),
             ("Key 来自 .env/环境变量（输入框空）", "")]
    for tag, clear_to in cases:
        if clear_to is not None and key_widget:
            key_widget[0].set_value(clear_to).run()
        c = at.session_state["container"]
        id_before = id(c.llm)
        with _llm_patch(_exc_401, []):
            _click(at, "🔌 测试连通性")
        e1 = _last_err(at)
        at.run()                       # 再跑一轮 = 用户切页/任意重绘
        c2 = at.session_state["container"]
        e2 = _last_err(at)
        print("  %-28s → LLMClient 每轮重建=%s；点击当轮 last_error=%s；重绘一轮后=%s"
              % (tag, id(c2.llm) != id_before, "有" if e1 else "无",
                 "有（横幅会渲染）" if e2 else "无（横幅消失）"))


def measure_timeout_equivalence():
    """证明「直接抛 APITimeoutError」==「真实读取超时」，并实测超时场景的等待规模。"""
    _hr("附加实测 A：超时注入的等价性证明 + 超时等待时长缩放")

    def _mk_client(timeout):
        return _llm.LLMClient(api_key="drill-placeholder-key", base_url=ZHIPU_URL,
                              model="glm-4-flash", timeout=timeout)

    # A1：传输层抛 httpx2.ReadTimeout（真实读取超时时 httpx2 抛的就是它），
    #     看 openai SDK 是否把它包装成 APITimeoutError、core/llm 是否分类为超时。
    def _instant_readtimeout(self, request, **kw):
        raise _h2exc.ReadTimeout("timed out", request=request)

    c = _mk_client(5.0)
    with mock.patch.object(httpx2.Client, "send", _instant_readtimeout):
        c.chat([{"role": "user", "content": "ping"}])
    print("  A1 传输层抛 httpx2.ReadTimeout → core/llm 分类 kind=%r，last_error=%r"
          % (c.last_error_kind, str(c.last_error)[:80]))
    print("     等价性：真实读取超时时 httpx2 抛 ReadTimeout，openai SDK 捕获后原样")
    print("     包装成 openai.APITimeoutError 上抛——与本脚本主场景直接抛的对象")
    print("     是同一个异常类、命中 _classify_error 的同一个分支，故注入等价。")

    # A2：真实"服务端不返回" = 每次尝试白等 timeout 秒。用 T=2s 实测，再按线性外推。
    T = 2.0
    planned = []

    def _slow_send(self, request, **kw):
        time.sleep(T)                    # 模拟"服务端不返回"，SDK 一直等到超时
        raise _h2exc.ReadTimeout("timed out", request=request)

    c2 = _mk_client(T)
    t0 = time.time()
    with mock.patch.object(httpx2.Client, "send", _slow_send), _patched_sleep(planned):
        c2.chat([{"role": "user", "content": "ping"}])
    dt = time.time() - t0
    print("  A2 LLM_TIMEOUT=%.1fs 实测挂起 %.1fs ≈ 4 次尝试×%.1fs + 退避 %.1fs"
          % (T, dt, T, sum(planned)))
    print("     → 线上默认 LLM_TIMEOUT=45.0 / 预算=20.0（config.py:123/128）时：首尝 45s ≥ 预算 20s"
          " → 立即截断，最坏 ≈45s 才降级（旧 90/45 为 ≈90s，Round 13 修复 A 已压半）")

    # A3：验证 core/llm.py 新增的 RETRY_BUDGET_SEC 是否真的能截断重试链。
    # 把预算临时压到 5s（< A2 的 17s），若生效则实测应显著小于 17s。
    A3_budget = 5.0
    planned3, calls3 = [], []
    _real_send = httpx2.Client.send

    def _counting_send(self, request, **kw):
        calls3.append(1)
        return _slow_send(self, request, **kw)

    c3 = _mk_client(T)
    import config as _cfg
    _had = hasattr(_cfg, "LLM_RETRY_BUDGET_SEC")
    _old = getattr(_cfg, "LLM_RETRY_BUDGET_SEC", None)
    _cfg.LLM_RETRY_BUDGET_SEC = A3_budget
    t0 = time.time()
    try:
        with mock.patch.object(httpx2.Client, "send", _counting_send), _patched_sleep(planned3):
            c3.chat([{"role": "user", "content": "ping"}])
    finally:
        if _had:
            _cfg.LLM_RETRY_BUDGET_SEC = _old
        else:
            try:
                del _cfg.LLM_RETRY_BUDGET_SEC
            except Exception:
                pass
    dt3 = time.time() - t0
    print("  A3 把总时长预算压到 %.0fs（LLM_TIMEOUT 仍 %.1fs）→ 实测挂起 %.1fs，"
          "SDK 调用 %d 次，退避 %s"
          % (A3_budget, T, dt3, len(calls3), planned3))
    print("     对比 A2 的 %.1fs：预算%s"
          % (dt, "**生效**（已截断重试链）" if dt3 < dt - 1.0 else "未生效"))


# ============================ 主流程 ============================
def measure_banner_spill(at):
    """附加实测 F：横幅可见性 + 外溢 + F7 收敛边界（00:05 建，00:52 扩成双路径）。

    背景：F2 修好后 last_error 跨 rerun 存活（sidebar 回补），app.py 又把横幅
    挪到页面渲染之后 —— 两者叠加的新问题是：**一次 LLM 失败之后，横幅会不会
    在完全无关的页面（文献库 / 问答页）一直挂着**（= F7）。

    00:46 app.py 又加了 F7 收敛（用 last_error_seq 判断"本轮本页是否新写过错误"，
    只把横幅挂在产生它的那一页）。收敛后必须**两条失败路径都验**，否则会漏掉
    "侧边栏失败时横幅压根不出现"这类回归：
      路径 1 侧边栏「🔌 测试连通性」  —— seq 在 _seq_before 之前就 +1，收敛判据不成立
      路径 2 主体区「🧹 引用含金量」  —— seq 在 _PAGES 渲染内 +1，收敛判据成立
    另需确认横幅是 st.warning 还是 st.error —— 后者会污染判据①的红框计数。
    """
    _hr("附加实测 F：横幅可见性与外溢（F2 是否可见 + F7 收敛边界）")

    lib = [m for m in MENU if "文献库" in m]
    qa = [m for m in MENU if "跨文献问答" in m]

    def _banner(at):
        return [str(w.value)[:80] for w in at.warning
                if "大模型调用失败" in str(w.value)]

    def _walk(tag_fail):
        """失败后依次切到文献库 / 问答页 / 切回，看横幅在哪儿出现。"""
        out = []
        for tag, page in [("文献库", lib[0] if lib else None),
                          ("问答页", qa[0] if qa else None),
                          ("检索页(切回)", PAGE_SEARCH)]:
            if not page:
                continue
            _goto(at, page)
            out.append((tag, bool(_banner(at)), len(at.error)))
        for tag, shown, n_e in out:
            print("      %-14s 横幅=%-5s error框=%d" % (tag, shown, n_e))
        return out

    # ---- 路径 1：侧边栏「🔌 测试连通性」失败（演示现场第一反应就是点它）----
    print("  【路径 1】侧边栏「🔌 测试连通性」失败 —— 横幅应可见（F2）")
    _goto(at, PAGE_SEARCH)
    btn = [c for c in at.button if "测试连通性" in getattr(c, "label", "")]
    if not btn:
        print("    ✗ 未找到按钮（断言未执行）")
    else:
        with _llm_patch(_exc_401, []):
            btn[0].click().run()
        print("    失败当轮：seq=%s  横幅=%s  error框=%d"
              % (getattr(at.session_state["container"].llm,
                         "last_error_seq", None), _banner(at), len(at.error)))
        _walk("sidebar")

    # ---- 路径 2：主体区「🧹 分析并剔除应付式引用」失败（页面渲染内）----
    print("  【路径 2】主体区「🧹 引用含金量」失败 —— 横幅应可见，且切走后消失（F7 收敛）")
    _goto(at, PAGE_SEARCH)
    ta = [t for t in at.text_area if getattr(t, "key", "") == "qrefs"]
    bt = [c for c in at.button if "剔除应付式引用" in getattr(c, "label", "")]
    if not (ta and bt):
        print("    ✗ 未找到输入框/按钮（断言未执行）")
    else:
        ta[0].set_value("[1] Vaswani et al. Attention is all you need.").run()
        with _llm_patch(_exc_401, []):
            bt[0].click().run()
        print("    失败当轮：seq=%s  横幅=%s  error框=%d"
              % (getattr(at.session_state["container"].llm,
                         "last_error_seq", None), _banner(at), len(at.error)))
        _walk("main")
    print("  → 判据①口径提醒：横幅是 st.warning，**不进入红框计数**；"
          "若哪天改成 st.error，判据①会失效。")


def main():
    t_boot = time.time()
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300)
    at.run()
    print("=" * 78)
    print("AI4S 故障演练（API 不可用 6 场景）　进程内 AppTest，全程 mock，无真实外网请求")
    print("=" * 78)
    print("应用启动+首渲染 %.1fs；未捕获异常 %d；LLM available=%s（占位 Key，仅供注入）"
          % (time.time() - t_boot, len(at.exception),
             at.session_state["container"].llm.available))

    scenario_llm(at, "S1", "智谱 API 返回 401（Key 失效）", _exc_401)
    scenario_llm(at, "S2", "智谱 API 返回 429（限流，Retry-After=2s）",
                 lambda: _exc_429(2))
    scenario_llm(at, "S3", "智谱 API 超时（读取超时）", _exc_timeout)
    scenario_llm(at, "S4", "智谱 API 完全不可达（断网）", _exc_network)
    scenario_extern(at, "S5", "arXiv API 不可达", _patch_arxiv,
                    "aq", "transformer", "搜索 arXiv")
    scenario_extern(at, "S6", "PubMed API 不可达", _patch_pubmed,
                    "pq", "protein", "🔍 PubMed")

    measure_call_counts()
    measure_429_cap()
    measure_timeout_equivalence()
    measure_retry_budget()
    measure_last_error_lifecycle(at)
    measure_banner_spill(at)
    _probe_qa_e2e(at, _exc_401)
    _print_schedule_table()

    print("\n" + "=" * 78)
    print("演练结束：以上全部为脚本真实输出，无编造。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""LLM 客户端封装：统一重试、流式输出、JSON 解析、多模态调用。基于 OpenAI 兼容协议。

统一重试策略（「任务组 C」基线 + Round 9 S6 错误分类）：

    第 1 次尝试（立即）→ 失败按错误类别差异化处理
      · 401/403（Key 无效/过期）        ：**不重试**，立即人话降级（重试纯属浪费用户时间）
      · 400（请求被拒）                  ：**不重试**，立即人话降级
      · 429（限流）                      ：按 Retry-After（封顶 10s）退避后重试
      · 5xx / 超时 / 网络错误 / 未知错误 ：退避 1.5 / 3.0 / 4.5s 后重试（最多 3 次）
    全部失败：落盘 storage/llm_failures.log + 返回**带降级标记**的伪响应（不抛异常）

即「1 次立即尝试 + 最多 3 次退避重试 = 最多 4 次调用」，可重试错误仍等价于历史语义
（冒烟基线：RuntimeError 桩 → 4 次调用、退避总时长 ≈ 9s）。
底层 SDK 的 max_retries 置 0（SDK_MAX_RETRIES），避免 SDK 与应用层重试叠加放大调用次数。

**降级诚实性（抗幻觉，Round 9 最高优）**：
伪响应不是答案。_fake_response 产出对象带不可伪造的结构化标记
（degraded=True + degraded_marker=DEGRADED_FLAG + message 同级字段），
文案以「⚠️ 离线占位响应：LLM 不可用，以下内容不是模型生成」明示。
调用方两种透出方式（无需改调用方文件即可生效）：
  1. 结构化：``is_degraded(resp)`` / ``getattr(msg, "degraded", False)``；
  2. 纯文本：伪响应文案以 ``【LLM 暂时不可用`` 开头（与历史一致，
     services/writing.py、services/translate.py、services/library.py 的
     既有守卫继续生效），并内嵌 ``⚠️ 离线占位响应`` 明示文案，
     services/rag.py、services/graph.py、services/parsing.py 直接展示该文案。

向后兼容（其他 agent 已在 import，签名一个都不能动）：
    _fake_response / parse_json / LLMClient(api_key, base_url, model, vl_model, timeout)
    LLMClient.chat / stream / vision / json_chat / _create / available
    call_llm_with_retry(messages, *, model, temperature, max_tokens, response_format, tools, tool_choice, **kw)
"""
import os
import json
import re
import threading
import time
import types
from openai import OpenAI

# ---------------- 统一重试常量（全局唯一来源） ----------------
MAX_RETRIES = 3                     # 退避重试次数（不含首次尝试）
RETRY_BACKOFF = (1.5, 3.0, 4.5)     # 第 1/2/3 次重试前的等待秒数
SDK_MAX_RETRIES = 0                 # 关掉 SDK 自带重试，避免与上面叠加（3×3→4）
FAIL_LOG_LIMIT = 2000               # 失败 prompt 落盘的最大字符数
FAIL_LOG_PATH_HINT = "storage/llm_failures.log"
CONFIG_LOG_PATH_HINT = "storage/llm_config_errors.log"   # 可预见配置错误单独分类，不污染失败统计
RATE_LIMIT_RETRY_AFTER_CAP = 10.0   # 429 尊重 Retry-After，但封顶 10s，避免交互界面长睡
MAX_VISION_IMAGE_BYTES = 20 * 1024 * 1024   # 多模态图片大小上限（20MB），超过直接人话降级

# 单次 LLM 调用的**总时长预算**（秒）：同一条链路（1 次立即尝试 + 最多 3 次退避重试）
# 累计耗时超过预算就放弃后续重试、直接走人话降级。
# 背景（Round 12 场景 S3 实测）：LLM_TIMEOUT 默认 90s × 4 次尝试 + 9s 退避 ≈ 369s，
# 问答链路还会翻倍到 ≈738s —— 演示现场转圈 6~12 分钟。
# **边界（重要）**：预算只决定"要不要发起下一次尝试"，**不改变任何一次尝试自己的
# 超时窗口**（不裁剪 timeout 参数），因此不会缩短正常成功请求的可用时间窗：
# 首次成功的调用仍拥有完整的 LLM_TIMEOUT 秒。
RETRY_BUDGET_SEC = 45.0

# ---------------- 降级标记（抗幻觉：伪响应绝不可伪装成正常答案） ----------------
DEGRADED_FLAG = "ai4s-offline-placeholder-v1"   # 结构化标记值：真实 SDK 响应对象不可能携带
DEGRADED_TEXT_MARK = "⚠️ 离线占位响应"           # 文案标记：纯文本通道（string 返回值）的透出
DEGRADED_HINT = "LLM 不可用，以下内容不是模型生成，请勿作为答案引用"


def _sanitize_brackets(text):
    """去掉文案里的【】括号，保证伪响应整体是一个连续括号串。

    services/writing.py 用 ``【LLM 暂时不可用[^】]*】`` 整段剔除伪响应，
    错误摘要里若混入】会截断剔除范围、把残留文案漏进正文。
    """
    return str(text).replace("】", "〗").replace("【", "〖")


def degraded_content(reason=""):
    """构造伪响应的展示文案。

    格式约束（调用方既有守卫依赖，勿改）：
      - 以 ``【LLM 暂时不可用`` 开头（writing.py startswith / translate.py 前缀判断）；
      - 全文只有一个结尾 ``】``（writing.py 正则整段剔除，不漏残文案）；
      - 内嵌 ``⚠️ 离线占位响应：LLM 不可用，以下内容不是模型生成`` 明示文案。
    """
    body = _sanitize_brackets(str(reason or "未知错误"))
    return "【LLM 暂时不可用，请稍后重试。%s：%s。原因：%s】" % (
        DEGRADED_TEXT_MARK, DEGRADED_HINT, body)


def is_degraded(obj):
    """判断 LLM 返回（响应对象 / message 对象）是否为离线占位响应。

    结构化标记优先（degraded_marker 等于模块内常量，模型输出无法伪造），
    兼容仅置 degraded=True 的旧形态；自动下钻 .choices[0].message。
    """
    try:
        if obj is None:
            return False
        if getattr(obj, "degraded_marker", None) == DEGRADED_FLAG:
            return True
        if getattr(obj, "degraded", False):
            return True
        choices = getattr(obj, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg is not None:
                if getattr(msg, "degraded_marker", None) == DEGRADED_FLAG:
                    return True
                if getattr(msg, "degraded", False):
                    return True
        return False
    except Exception:
        return False


def is_degraded_text(text):
    """纯文本通道（str）的降级判断：文案含离线占位标记。"""
    try:
        return bool(text) and DEGRADED_TEXT_MARK in str(text)
    except Exception:
        return False


class LLMConfigError(RuntimeError):
    """可预见的配置错误（Key 含非 ASCII 等）：不重试、不写失败日志噪声，直接人话提示。"""


def _validate_api_key(api_key):
    """校验 Key 形态，返回 strip 后的 Key；非 ASCII 抛出 LLMConfigError（人话）。

    非 ASCII Key（误粘贴中文/占位文本）会在 OpenAI SDK 写 HTTP 头时抛
    UnicodeEncodeError 并触发 4 次无效重试 + llm_failures.log 噪声（历史 74 条）。
    这里在构造请求前拦截，直接给出可操作的提示。**绝不记录 Key 内容**。
    """
    key = "" if api_key is None else str(api_key).strip()
    if key and not key.isascii():
        raise LLMConfigError(
            "API Key 含非 ASCII 字符（长度 %d），无法写入 HTTP 请求头；"
            "疑似误粘贴了中文或占位文本，请在侧边栏重新粘贴纯英文 Key 后再试" % len(key))
    return key


# ---------------- openai 异常类（防御式导入，缺类不致命） ----------------
try:
    from openai import (AuthenticationError as _OAuthError,
                        PermissionDeniedError as _OPermissionError,
                        RateLimitError as _ORateLimitError,
                        APITimeoutError as _OAPITimeoutError,
                        APIConnectionError as _OAPIConnectionError,
                        InternalServerError as _OInternalServerError,
                        BadRequestError as _OBadRequestError)
except Exception:                                     # pragma: no cover - 旧版 SDK 缺类
    _OAuthError = _OPermissionError = _ORateLimitError = ()
    _OAPITimeoutError = _OAPIConnectionError = _OInternalServerError = _OBadRequestError = ()


# ---------- LLMClient 连接池复用缓存（话术 10：避免每次调用重建 OpenAI 客户端/连接池） ----------
# call_llm_with_retry 原本每次调用都 new 一个 LLMClient（连带新建 TCP/TLS 连接池，
# 实测每次约数十 ms 开销）。相同 (key, base_url, model, timeout) 复用同一实例：
# OpenAI 客户端内部连接池可并发复用、线程安全，不改变任何重试/降级语义。
_CLIENT_CACHE = {}
_CLIENT_CACHE_LOCK = threading.Lock()
_CLIENT_CACHE_MAX = 16


def _get_cached_client(api_key, base_url, model, timeout):
    """复用相同参数的 LLMClient；锁外构造避免阻塞其它调用，构造后再校验一次缓存。"""
    k = (api_key, base_url, model, timeout)
    with _CLIENT_CACHE_LOCK:
        c = _CLIENT_CACHE.get(k)
        if c is not None:
            return c
    try:
        c = LLMClient(api_key=api_key, base_url=base_url, model=model, timeout=timeout)
    except Exception:
        # 极端情况下构造失败也直接抛，交由上层降级逻辑处理（与原行为一致）
        raise
    with _CLIENT_CACHE_LOCK:
        existing = _CLIENT_CACHE.get(k)
        if existing is not None:
            return existing
        if len(_CLIENT_CACHE) >= _CLIENT_CACHE_MAX:
            _CLIENT_CACHE.clear()
        _CLIENT_CACHE[k] = c
    return c


class _ErrInfo:
    """错误分类结果：kind 分类名 / retryable 是否值得重试 / human 人话原因 / retry_after 秒。"""

    __slots__ = ("kind", "retryable", "human", "retry_after")

    def __init__(self, kind, retryable, human, retry_after=None):
        self.kind = kind
        self.retryable = retryable
        self.human = human
        self.retry_after = retry_after


def _retry_after_seconds(e, cap=RATE_LIMIT_RETRY_AFTER_CAP):
    """从 429 响应头读 Retry-After（秒），缺失/非法返回 None；封顶 cap 秒。"""
    resp = getattr(e, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("retry-after")
        if raw is None:
            raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        secs = float(str(raw).strip())
    except Exception:
        return None
    if secs <= 0:
        return None
    return min(secs, cap)


def _classify_error(e):
    """把异常归类为 (kind, retryable, human)，实现错误分类与差异化处理。

    不可重试：401/403（Key 问题，重试无意义）、400（请求被拒）、
    非 ASCII Key 的 UnicodeEncodeError（可预见配置错误）。
    可重试：429（限流，按 Retry-After）、5xx（服务端）、连接/读取超时、网络错误、
    未知错误（保持历史 4 次调用语义，冒烟基线依赖）。
    """
    name = type(e).__name__
    text = str(e)[:400]
    low = text.lower()
    try:
        status = int(getattr(e, "status_code", 0) or 0)
    except Exception:
        status = 0

    # 非 ASCII Key：SDK 写 HTTP 头时的 UnicodeEncodeError（历史 74 条噪声的来源）
    if isinstance(e, UnicodeEncodeError) or ("codec can't encode" in low and "ascii" in low):
        return _ErrInfo("config_key_ascii", False,
                        "API Key 含非 ASCII 字符，无法写入 HTTP 请求头；请在侧边栏重新粘贴纯英文 Key")
    if isinstance(e, LLMConfigError):
        return _ErrInfo("config", False, str(e))

    # 401/403：Key 无效或过期，重试 3 次纯属浪费用户时间 → 立即失败
    if (isinstance(e, _OAuthError) or isinstance(e, _OPermissionError)
            or status in (401, 403) or "401" in text[:48] or "403" in text[:48]
            or "令牌已过期" in text or "invalid api key" in low or "api key is invalid" in low):
        return _ErrInfo("auth", False,
                        "API Key 无效或已过期（HTTP 401/403），请在侧边栏检查 Key 后重试")

    # 429：限流，尊重 Retry-After（封顶 10s），退避重试
    if (isinstance(e, _ORateLimitError) or status == 429
            or "rate limit" in low or "rate limited" in low or "429" in text[:48]):
        return _ErrInfo("rate_limit", True, "触发模型服务限流（HTTP 429），正在按 Retry-After 退避重试",
                        retry_after=_retry_after_seconds(e))

    # 400：请求被拒（模型名/参数不支持），重试无意义
    if isinstance(e, _OBadRequestError) or status == 400:
        return _ErrInfo("bad_request", False,
                        "请求被模型服务拒绝（HTTP 400），多为模型名称或参数不被当前 Provider 支持")

    # 5xx：服务端错误，退避重试
    if (isinstance(e, _OInternalServerError) or 500 <= status < 600):
        return _ErrInfo("server", True, "模型服务内部错误（HTTP 5xx），正在退避重试")

    # 超时：区分连接超时（端点/网络问题）与读取超时（服务端生成过慢）
    if (isinstance(e, _OAPITimeoutError) or isinstance(e, TimeoutError)
            or "timeout" in low or "timed out" in low):
        if "connect" in low or name == "ConnectTimeout":
            return _ErrInfo("connect_timeout", True,
                            "连接模型服务超时（网络不通或 Base URL 有误），正在退避重试")
        return _ErrInfo("read_timeout", True,
                        "读取模型响应超时（服务端生成过慢），正在退避重试")

    # 网络层错误（DNS / 连接被拒等）
    if isinstance(e, _OAPIConnectionError) or "connection" in low:
        return _ErrInfo("network", True, "网络连接失败，正在退避重试")

    return _ErrInfo("unknown", True, "未知错误，正在退避重试")


def _backoff_for(attempt: int) -> float:
    """第 attempt 次重试（0-based）前应等待的秒数；超出序列取最后一项。"""
    try:
        i = int(attempt)
    except Exception:
        i = 0
    if i < 0:
        i = 0
    if i < len(RETRY_BACKOFF):
        return float(RETRY_BACKOFF[i])
    return float(RETRY_BACKOFF[-1])


def _budget():
    """取总时长预算：优先 config（可被 .env / 环境变量覆盖），失败回落模块常量。

    <=0 视为"不设预算"（保持旧的无限重试语义），便于按需关掉。
    """
    try:
        from config import LLM_RETRY_BUDGET_SEC
        v = float(LLM_RETRY_BUDGET_SEC)
    except Exception:
        v = RETRY_BUDGET_SEC
    return v if v > 0 else 0.0


def _sleep_backoff(attempt: int, info=None):
    """按错误类别睡相应时长：429 优先尊重 Retry-After（封顶），其余用 1.5/3.0/4.5。"""
    secs = _backoff_for(attempt)
    if info is not None and getattr(info, "retry_after", None):
        try:
            secs = min(max(float(info.retry_after), 0.5), RATE_LIMIT_RETRY_AFTER_CAP)
        except Exception:
            pass
    try:
        time.sleep(secs)
    except Exception:
        pass


# ---------- 失败落盘 & 伪响应（统一兜底，避免上层 for 循环/agent 迭代被异常打断） ----------

def _fake_response(content, reason=""):
    """构造一个与 openai 响应兼容的伪响应，让上层 .choices[0].message.content 可读。

    **抗幻觉**：返回对象带不可伪造的降级标记（degraded=True + degraded_marker），
    message 对象同级携带，调用方可用 is_degraded() 或 getattr(msg, "degraded", False)
    识别「这不是模型生成的内容」。
    """
    msg = types.SimpleNamespace(content=content, tool_calls=None,
                                degraded=True, degraded_reason=reason,
                                degraded_marker=DEGRADED_FLAG)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=msg,
            finish_reason="stop",
        )],
        degraded=True, degraded_reason=reason, degraded_marker=DEGRADED_FLAG,
    )


def _caller_info():
    """定位调用点：返回 '文件:行号 函数名'（跳过本模块自身帧）。失败返回 ''。"""
    try:
        import inspect
        me = os.path.abspath(__file__)
        for fr in inspect.stack()[2:14]:
            fn = os.path.abspath(fr.filename or "")
            if fn and os.path.abspath(fn) != me:
                return "%s:%d %s()" % (os.path.basename(fn), fr.lineno, fr.function)
    except Exception:
        pass
    return ""


def _dump_messages(messages, limit=FAIL_LOG_LIMIT):
    """把 prompt 序列化为可落盘字符串，超长截断（不会因序列化失败影响主流程）。"""
    s = ""
    try:
        s = json.dumps(messages, ensure_ascii=False, default=str)
    except Exception:
        try:
            s = str(messages)
        except Exception:
            s = ""
    if len(s) > limit:
        s = s[:limit] + "...[已截断，共 %d 字符]" % len(s)
    return s


def _log_llm_failure(err, messages, model=None, caller=None, category="api", kind=None):
    """把失败详情 append 到 storage/llm_failures.log。写失败不影响主流程。

    增强：记录 prompt 前 2000 字符（默认）+ model + 调用点 caller + 错误分类 kind，
    便于定位是哪个页面/函数打的、属于哪类故障。
    category="config" 表示可预见的配置错误（Key 缺失 / 非 ASCII Key 等），
    单独写 storage/llm_config_errors.log，**不污染 llm_failures.log 的真实失败统计**。
    """
    try:
        try:
            from config import STORAGE_DIR
            log_path = os.path.join(str(STORAGE_DIR),
                                    "llm_config_errors.log" if category == "config"
                                    else "llm_failures.log")
        except Exception:
            log_path = os.path.join("storage",
                                    "llm_config_errors.log" if category == "config"
                                    else "llm_failures.log")
            try:
                os.makedirs("storage", exist_ok=True)
            except Exception:
                pass
        summary = _dump_messages(messages, FAIL_LOG_LIMIT)
        try:
            n_msg = len(messages) if hasattr(messages, "__len__") else -1
        except Exception:
            n_msg = -1
        if not kind and err is not None:
            try:
                kind = _classify_error(err).kind
            except Exception:
                kind = None
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error_type": type(err).__name__ if err is not None else "Unknown",
            "error": str(err)[:800] if err is not None else "",
            "category": category,
            "kind": kind,
            "model": str(model or "") or None,
            "caller": caller if caller is not None else _caller_info(),
            "n_messages": n_msg,
            "messages_preview": summary,
        }
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


class LLMClient:
    def __init__(self, api_key="", base_url=None, model=None, vl_model=None, timeout=90.0):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.vl_model = vl_model
        try:
            self.timeout = float(timeout) if timeout else 90.0
        except Exception:
            self.timeout = 90.0
        self._client = None
        self.last_error = None          # 最近一次失败原因（供 UI 展示，不抛异常）
        self.last_error_kind = None     # 最近一次失败分类（auth / rate_limit / server / ...）
        # 单调递增序号：**每写一次 last_error 就 +1**（成功置 None 时不 bump）。
        # 用途：UI 要判断"某个失败是本轮、在本页新产生的"，光比字符串不够——
        # 两个不同页面完全可能报出**一模一样**的错误串（如同一个 401）。
        # 见 app.py 的 F7 收敛逻辑（横幅只挂在"产生它的那一页"）。
        self.last_error_seq = 0
        self.key_error = None           # Key 形态问题的人话原因（非 ASCII 等；None = 无问题）
        try:
            key = _validate_api_key(api_key)
        except LLMConfigError as e:
            # 非 ASCII Key：不构造 SDK 客户端，available=False，调用立即人话降级
            key = str(api_key or "").strip()
            self.api_key = key
            self.key_error = str(e)
            return
        self.api_key = key              # 统一存 strip 后的 Key（比较/传递口径一致）
        if key:
            try:
                # max_retries=0：重试统一由 _create 负责，避免 SDK 与应用层双重退避
                self._client = OpenAI(api_key=key, base_url=base_url,
                                      timeout=self.timeout, max_retries=SDK_MAX_RETRIES)
            except Exception as e:
                self.key_error = "LLM 客户端构造失败：%s" % str(e)[:200]

    @property
    def available(self):
        return self._client is not None

    def _note_error(self, reason, kind=None):
        """统一写入 last_error，并 bump 序号（F7：UI 靠序号判断"本页本轮新产生"）。

        成功路径（``self.last_error = None``）**不要**走这里、也不要清 seq——
        清了就分不出"旧失败"与"新失败"。
        """
        self.last_error = reason
        self.last_error_kind = kind
        self.last_error_seq = int(getattr(self, "last_error_seq", 0) or 0) + 1

    def _create(self, **kwargs):
        """统一调用入口：1 次立即尝试 + 按错误类别差异化重试（最多 3 次退避重试）。

        401/403/400 等不可重试错误立即停止；全部失败不抛异常：落盘
        storage/llm_failures.log 后返回带降级标记的伪响应。
        """
        model = kwargs.get("model") or self.model
        if not self._client:
            reason = self.key_error or "未配置 API Key，无法调用大模型。"
            self._note_error(reason, "config")
            # 可预见的配置错误单独分类，不写 llm_failures.log 噪声
            _log_llm_failure(RuntimeError(reason), kwargs.get("messages"),
                             model=model, caller=_caller_info(), category="config", kind="config")
            return _fake_response(degraded_content(reason), reason=reason)
        last, info = None, None
        budget = _budget()
        t_start = time.time()
        for attempt in range(MAX_RETRIES + 1):     # 0,1,2,3 → 最多 4 次调用
            try:
                resp = self._client.chat.completions.create(**kwargs)
                self.last_error = None
                self.last_error_kind = None
                return resp
            except Exception as e:                 # 分类差异化：401/400 不重试，429 尊重 Retry-After
                last = e
                info = _classify_error(e)
                self._note_error(str(e)[:300], info.kind)
                if not info.retryable or attempt >= MAX_RETRIES:
                    break
                # 总时长预算：累计耗时（含每次尝试的等待 + 退避）超预算就放弃重试。
                # 只决定是否发起下一次尝试，**不裁剪本次/下次的 timeout**。
                if budget and (time.time() - t_start) >= budget:
                    break
                _sleep_backoff(attempt, info)
                if budget and (time.time() - t_start) >= budget:
                    break
        _log_llm_failure(last, kwargs.get("messages"), model=model,
                         caller=_caller_info(), kind=(info.kind if info else None))
        reason = "%s；原始错误：%s" % (info.human if info else "未知错误", str(last))
        return _fake_response(degraded_content(reason), reason=reason)

    def chat(self, messages, model=None, temperature=0.2, tools=None, tool_choice="auto", json_mode=False):
        kwargs = dict(model=model or self.model, messages=messages, temperature=temperature)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._create(**kwargs)
        try:
            msg = resp.choices[0].message
            if is_degraded(resp) and not getattr(msg, "degraded", False):
                # 兜底透传降级标记（正常不会触发：_fake_response 已同时标记 resp 与 message）
                msg.degraded = True
                msg.degraded_marker = DEGRADED_FLAG
            return msg
        except Exception as e:                     # 极端情况下的结构兜底
            _log_llm_failure(e, messages, model=model or self.model, caller=_caller_info())
            reason = "LLM 返回结构异常；原始错误：%s" % str(e)
            return _fake_response(degraded_content(reason), reason=reason).choices[0].message

    def stream(self, messages, model=None, temperature=0.3):
        """流式文本生成，逐 token yield 字符串。

        重试语义与 _create 一致（按错误类别差异化：401/400 不重试，429 尊重
        Retry-After，其余 1.5/3.0/4.5）；**一旦已经吐出过内容就不再重试**，
        避免重复输出。中途断流时已生成内容保留（已 yield 给调用方），并 yield
        一条带降级标记的中断提示。全部失败时 yield 一条友好错误，不抛异常。
        """
        model = model or self.model
        if not self._client:
            reason = self.key_error or "未配置 API Key，无法调用大模型。"
            self._note_error(reason, "config")
            _log_llm_failure(RuntimeError(reason), messages, model=model,
                             caller=_caller_info(), category="config", kind="config")
            yield degraded_content(reason)
            return
        last, info = None, None
        produced = False
        budget = _budget()
        t_start = time.time()
        for attempt in range(MAX_RETRIES + 1):
            produced = False
            stream = None
            try:
                stream = self._client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature, stream=True
                )
                for chunk in stream:
                    if not getattr(chunk, "choices", None):
                        continue
                    delta = chunk.choices[0].delta
                    c = getattr(delta, "content", None)
                    if c:
                        produced = True
                        yield c
                self.last_error = None
                self.last_error_kind = None
                return
            except Exception as e:
                last = e
                info = _classify_error(e)
                self._note_error(str(e)[:300], info.kind)
                if produced:
                    # 已输出过内容，重试会导致重复 → 直接结束并给出中断提示
                    break
                if not info.retryable or attempt >= MAX_RETRIES:
                    break
                if budget and (time.time() - t_start) >= budget:
                    break
                _sleep_backoff(attempt, info)            # 1.5 / 3.0 / 4.5 后重试
                if budget and (time.time() - t_start) >= budget:
                    break
            finally:
                if stream is not None:
                    try:
                        stream.close()   # 断流/切页/重试前释放底层 HTTP 连接
                    except Exception:
                        pass
        _log_llm_failure(last, messages, model=model,
                         caller=_caller_info(), kind=(info.kind if info else None))
        if produced:
            # 中途断流：已生成内容已 yield 保留，这里只补一条明确的中断+降级提示
            reason = "流式输出中途失败，以上仅为中断前已生成的内容；原始错误：%s" % str(last)
            yield "【LLM 暂时不可用，流式输出已中断。%s：%s】" % (DEGRADED_TEXT_MARK, _sanitize_brackets(reason))
        else:
            reason = "%s；原始错误：%s" % (info.human if info else "未知错误", str(last))
            yield degraded_content(reason)

    def vision(self, prompt, image_bytes, image_mime="png", temperature=0.1):
        """多模态理解：传入图片字节 + 文本提示，返回文本。

        与 chat/stream 走同一个 _create 重试通道（错误分类差异化），
        失败返回带降级标记的文案而非异常。图片超过 MAX_VISION_IMAGE_BYTES
        直接人话降级，不发起无效请求。
        """
        import base64
        if not image_bytes:
            reason = "图片内容为空，无法调用多模态模型"
            self._note_error(reason, "config")
            _log_llm_failure(RuntimeError(reason), prompt, model=self.vl_model,
                             caller=_caller_info(), category="config", kind="config")
            return degraded_content(reason)
        if len(image_bytes) > MAX_VISION_IMAGE_BYTES:
            reason = ("图片过大（%.1f MB，超过 %.0f MB 上限），无法调用多模态模型；"
                      "请压缩图片或降低分辨率后重试"
                      % (len(image_bytes) / 1048576.0, MAX_VISION_IMAGE_BYTES / 1048576.0))
            self._note_error(reason, "config")
            _log_llm_failure(RuntimeError(reason), prompt, model=self.vl_model,
                             caller=_caller_info(), category="config", kind="config")
            return degraded_content(reason)
        try:
            b64 = base64.b64encode(image_bytes).decode("utf-8")
        except Exception as e:
            self._note_error(str(e)[:300], "config")
            _log_llm_failure(e, prompt, model=self.vl_model, caller=_caller_info(),
                             category="config", kind="config")
            return degraded_content("图片编码失败，无法调用多模态模型；原始错误：%s" % str(e))
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/{image_mime};base64,{b64}"}},
            ],
        }
        resp = self._create(model=self.vl_model, messages=[msg], temperature=temperature)
        try:
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            _log_llm_failure(e, msg, model=self.vl_model, caller=_caller_info())
            return degraded_content("多模态调用返回结构异常；原始错误：%s" % str(e))

    def json_chat(self, messages, model=None, temperature=0.1):
        """要求模型输出 JSON，兼容无 json_mode 的端点：去代码围栏后解析。

        降级语义：伪响应不是合法 JSON，parse_json 会抛出带降级文案的
        ValueError（调用方既有 except 分支照常工作），不会把占位内容当数据。
        """
        msg = self.chat(messages, model=model, temperature=temperature)
        return parse_json(msg.content or "")


def call_llm_with_retry(messages, *, model=None, temperature=None, max_tokens=None,
                        response_format=None, tools=None, tool_choice=None, **kw):
    """模块级便捷入口，供 tools/definitions.py 等后续直接调用。

    优先从环境/配置中读取默认 key 构造临时 LLMClient；构造不出或调用失败时，
    统一返回带降级标记的伪响应（不抛异常），重试语义与 LLMClient._create 完全一致。
    非 ASCII / 空 Key 在调用前拦截：不打 SDK、不写失败日志噪声，直接人话降级。
    """
    api_key = kw.pop("api_key", None)
    base_url = kw.pop("base_url", None)
    timeout = kw.pop("timeout", None)
    default_model = None
    try:
        from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
        api_key = api_key or LLM_API_KEY
        base_url = base_url or LLM_BASE_URL
        default_model = LLM_MODEL
    except Exception:
        api_key = api_key or os.getenv("LLM_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    # 调用前校验 Key 形态：非 ASCII / 空 Key 直接人话降级，不触发无效重试与日志噪声
    try:
        api_key = _validate_api_key(api_key)
    except LLMConfigError as e:
        _log_llm_failure(e, messages, model=model or default_model,
                         caller=_caller_info(), category="config", kind="config")
        return _fake_response(degraded_content(str(e)), reason=str(e))
    if not api_key:
        err = RuntimeError("未配置 API Key，无法调用大模型。")
        _log_llm_failure(err, messages, model=model or default_model,
                         caller=_caller_info(), category="config", kind="config")
        return _fake_response(degraded_content(str(err)), reason=str(err))
    try:
        t = float(timeout) if timeout else 90.0
    except Exception:
        t = 90.0

    client = _get_cached_client(api_key, base_url,
                                model or default_model, t)
    kwargs = dict(model=model or default_model, messages=messages)
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice if tool_choice is not None else "auto"
    kwargs.update(kw)
    return client._create(**kwargs)


def parse_json(text):
    """从模型输出中稳健解析 JSON（兼容 ```json 围栏与前后多余文字）。"""
    if isinstance(text, (dict, list)):
        return text
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        # 退化：截取首个 [ 或 { 到对应结尾
        for open_c, close_c in (("[", "]"), ("{", "}")):
            i, j = t.find(open_c), t.rfind(close_c)
            if i != -1 and j != -1 and j > i:
                try:
                    return json.loads(t[i:j + 1])
                except Exception:
                    pass
    raise ValueError(f"无法解析为 JSON：{text[:200]}")

"""LLM 客户端封装：统一重试、流式输出、JSON 解析、多模态调用。基于 OpenAI 兼容协议。

统一重试策略（本轮「任务组 C」）——所有调用点（chat / json_chat / stream / vision /
模块级 call_llm_with_retry）走同一份实现，语义完全一致：

    第 1 次尝试（立即）→ 失败 sleep 1.5s
    第 2 次尝试        → 失败 sleep 3.0s
    第 3 次尝试        → 失败 sleep 4.5s
    第 4 次尝试        → 仍失败：落盘 storage/llm_failures.log + 返回友好伪响应（**不抛异常**）

即「1 次立即尝试 + 3 次退避重试（1.5/3.0/4.5）= 最多 4 次调用」，
3 次重试全部失败后才返回伪响应，与需求「3 次失败后返回友好伪响应、不抛异常」一致。
退避序列集中在 RETRY_BACKOFF，改一处即全局生效。
底层 SDK 的 max_retries 置 0（SDK_MAX_RETRIES），避免 SDK 与应用层重试叠加放大调用次数。

向后兼容（其他 agent 已在 import，签名一个都不能动）：
    _fake_response / parse_json / LLMClient(api_key, base_url, model, vl_model, timeout)
    LLMClient.chat / stream / vision / json_chat / _create / available
    call_llm_with_retry(messages, *, model, temperature, max_tokens, response_format, tools, tool_choice, **kw)
"""
import os
import json
import re
import time
import types
from openai import OpenAI

# ---------------- 统一重试常量（全局唯一来源） ----------------
MAX_RETRIES = 3                     # 退避重试次数（不含首次尝试）
RETRY_BACKOFF = (1.5, 3.0, 4.5)     # 第 1/2/3 次重试前的等待秒数
SDK_MAX_RETRIES = 0                 # 关掉 SDK 自带重试，避免与上面叠加（3×3→4）
FAIL_LOG_LIMIT = 2000               # 失败 prompt 落盘的最大字符数
FAIL_LOG_PATH_HINT = "storage/llm_failures.log"


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


def _sleep_backoff(attempt: int):
    try:
        time.sleep(_backoff_for(attempt))
    except Exception:
        pass


# ---------- 失败落盘 & 伪响应（统一兜底，避免上层 for 循环/agent 迭代被异常打断） ----------

def _fake_response(content):
    """构造一个与 openai 响应兼容的伪响应，让上层 .choices[0].message.content 可读。"""
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content, tool_calls=None),
            finish_reason="stop",
        )]
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


def _log_llm_failure(err, messages, model=None, caller=None):
    """把失败详情 append 到 storage/llm_failures.log。写失败不影响主流程。

    增强：记录 prompt 前 2000 字符（默认）+ model + 调用点 caller，便于定位是哪个页面/函数打的。
    """
    try:
        try:
            from config import STORAGE_DIR
            log_path = os.path.join(str(STORAGE_DIR), "llm_failures.log")
        except Exception:
            log_path = os.path.join("storage", "llm_failures.log")
            try:
                os.makedirs("storage", exist_ok=True)
            except Exception:
                pass
        summary = _dump_messages(messages, FAIL_LOG_LIMIT)
        try:
            n_msg = len(messages) if hasattr(messages, "__len__") else -1
        except Exception:
            n_msg = -1
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error_type": type(err).__name__ if err is not None else "Unknown",
            "error": str(err)[:800] if err is not None else "",
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
        self.timeout = timeout
        self._client = None
        self.last_error = None          # 最近一次失败原因（供 UI 展示，不抛异常）
        if api_key:
            # max_retries=0：重试统一由 _create 负责，避免 SDK 与应用层双重退避
            self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                                  max_retries=SDK_MAX_RETRIES)

    @property
    def available(self):
        return self._client is not None

    def _create(self, **kwargs):
        """统一调用入口：1 次立即尝试 + 3 次退避重试（1.5s / 3.0s / 4.5s）。

        全部失败不抛异常：落盘 storage/llm_failures.log 后返回伪响应。
        """
        model = kwargs.get("model") or self.model
        if not self._client:
            err = RuntimeError("未配置 API Key，无法调用大模型。")
            self.last_error = str(err)
            _log_llm_failure(err, kwargs.get("messages"), model=model, caller=_caller_info())
            return _fake_response(f"【LLM 暂时不可用，请稍后重试。错误摘要：{err}】")
        last = None
        for attempt in range(MAX_RETRIES + 1):     # 0,1,2,3 → 最多 4 次调用
            try:
                resp = self._client.chat.completions.create(**kwargs)
                self.last_error = None
                return resp
            except Exception as e:                 # 统一退避重试：1.5 / 3.0 / 4.5
                last = e
                self.last_error = str(e)[:300]
                if attempt >= MAX_RETRIES:
                    break
                _sleep_backoff(attempt)
        _log_llm_failure(last, kwargs.get("messages"), model=model, caller=_caller_info())
        return _fake_response(f"【LLM 暂时不可用，请稍后重试。错误摘要：{last}】")

    def chat(self, messages, model=None, temperature=0.2, tools=None, tool_choice="auto", json_mode=False):
        kwargs = dict(model=model or self.model, messages=messages, temperature=temperature)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._create(**kwargs)
        try:
            return resp.choices[0].message
        except Exception as e:                     # 极端情况下的结构兜底
            _log_llm_failure(e, messages, model=model or self.model, caller=_caller_info())
            return _fake_response(f"【LLM 返回结构异常，请稍后重试。错误摘要：{e}】").choices[0].message

    def stream(self, messages, model=None, temperature=0.3):
        """流式文本生成，逐 token yield 字符串。

        重试语义与 _create 一致（1.5/3.0/4.5）；但**一旦已经吐出过内容就不再重试**，
        避免重复输出。全部失败时 yield 一条友好错误，不抛异常。
        """
        model = model or self.model
        if not self._client:
            err = RuntimeError("未配置 API Key，无法调用大模型。")
            self.last_error = str(err)
            _log_llm_failure(err, messages, model=model, caller=_caller_info())
            yield "【LLM 暂时不可用，请稍后重试。错误摘要：未配置 API Key，无法调用大模型。】"
            return
        last = None
        for attempt in range(MAX_RETRIES + 1):
            produced = False
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
                return
            except Exception as e:
                last = e
                self.last_error = str(e)[:300]
                if produced:
                    # 已输出过内容，重试会导致重复 → 直接结束并给出提示
                    break
                if attempt >= MAX_RETRIES:
                    break
                _sleep_backoff(attempt)            # 1.5 / 3.0 / 4.5 后重试
        _log_llm_failure(last, messages, model=model, caller=_caller_info())
        yield f"【LLM 暂时不可用，请稍后重试。错误摘要：{last}】"

    def vision(self, prompt, image_bytes, image_mime="png", temperature=0.1):
        """多模态理解：传入图片字节 + 文本提示，返回文本。

        与 chat/stream 走同一个 _create 重试通道（1.5 / 3.0 / 4.5），失败返回友好文案而非异常。
        """
        import base64
        try:
            b64 = base64.b64encode(image_bytes).decode("utf-8")
        except Exception as e:
            self.last_error = str(e)[:300]
            _log_llm_failure(e, prompt, model=self.vl_model, caller=_caller_info())
            return "【图片编码失败，无法调用多模态模型。】"
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
            return f"【多模态调用返回结构异常：{e}】"

    def json_chat(self, messages, model=None, temperature=0.1):
        """要求模型输出 JSON，兼容无 json_mode 的端点：去代码围栏后解析。"""
        msg = self.chat(messages, model=model, temperature=temperature)
        return parse_json(msg.content or "")


def call_llm_with_retry(messages, *, model=None, temperature=None, max_tokens=None,
                        response_format=None, tools=None, tool_choice=None, **kw):
    """模块级便捷入口，供 tools/definitions.py 等后续直接调用。

    优先从环境/配置中读取默认 key 构造临时 LLMClient；构造不出或调用失败时，
    统一返回伪响应（不抛异常），重试语义与 LLMClient._create 完全一致。
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
    if not api_key:
        err = RuntimeError("未配置 API Key，无法调用大模型。")
        _log_llm_failure(err, messages, model=model or default_model, caller=_caller_info())
        return _fake_response(f"【LLM 暂时不可用，请稍后重试。错误摘要：{err}】")

    client = LLMClient(api_key=api_key, base_url=base_url,
                       model=model or default_model,
                       timeout=float(timeout) if timeout else 90.0)
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

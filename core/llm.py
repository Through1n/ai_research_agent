"""LLM 客户端封装：统一重试、流式输出、JSON 解析、多模态调用。基于 OpenAI 兼容协议。"""
import os
import json
import re
import time
import types
from openai import OpenAI


# ---------- 失败落盘 & 伪响应（统一兜底，避免上层 for 循环/agent 迭代被异常打断） ----------

def _fake_response(content):
    """构造一个与 openai 响应兼容的伪响应，让上层 .choices[0].message.content 可读。"""
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content, tool_calls=None),
            finish_reason="stop",
        )]
    )


def _log_llm_failure(err, messages):
    """把失败摘要 append 到 storage/llm_failures.log。写失败不影响主流程。"""
    try:
        try:
            from config import STORAGE_DIR
            log_path = STORAGE_DIR / "llm_failures.log"
        except Exception:
            log_path = os.path.join("storage", "llm_failures.log")
            os.makedirs("storage", exist_ok=True)
        try:
            summary = json.dumps(messages, ensure_ascii=False)[:500]
        except Exception:
            summary = str(messages)[:500]
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error_type": type(err).__name__,
            "error": str(err)[:500],
            "messages_preview": summary,
        }
        with open(log_path, "a", encoding="utf-8") as f:
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
        if api_key:
            self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)

    @property
    def available(self):
        return self._client is not None

    def _create(self, **kwargs):
        """统一调用入口：3 次指数退避（1.5s/3s/4.5s），全部失败不抛异常，落盘并返回伪响应。"""
        if not self._client:
            err = RuntimeError("未配置 API Key，无法调用大模型。")
            _log_llm_failure(err, kwargs.get("messages"))
            return _fake_response(f"【LLM 暂时不可用，请稍后重试。错误摘要：{err}】")
        last = None
        for attempt in range(3):
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as e:  # 指数退避重试
                last = e
                if attempt == 2:
                    break
                time.sleep(1.5 * (attempt + 1))
        _log_llm_failure(last, kwargs.get("messages"))
        return _fake_response(f"【LLM 暂时不可用，请稍后重试。错误摘要：{last}】")

    def chat(self, messages, model=None, temperature=0.2, tools=None, tool_choice="auto", json_mode=False):
        kwargs = dict(model=model or self.model, messages=messages, temperature=temperature)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._create(**kwargs)
        return resp.choices[0].message

    def stream(self, messages, model=None, temperature=0.3):
        """流式文本生成，逐 token yield 字符串。失败时 yield 一条友好错误，不抛异常。"""
        if not self._client:
            yield "【LLM 暂时不可用，请稍后重试。错误摘要：未配置 API Key，无法调用大模型。】"
            return
        try:
            stream = self._client.chat.completions.create(
                model=model or self.model, messages=messages, temperature=temperature, stream=True
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    yield delta.content
        except Exception as e:
            _log_llm_failure(e, messages)
            yield f"【LLM 暂时不可用，请稍后重试。错误摘要：{e}】"

    def vision(self, prompt, image_bytes, image_mime="png", temperature=0.1):
        """多模态理解：传入图片字节 + 文本提示，返回文本。"""
        import base64
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/{image_mime};base64,{b64}"}},
            ],
        }
        m = self.chat([msg], model=self.vl_model, temperature=temperature)
        return m.content or ""

    def json_chat(self, messages, model=None, temperature=0.1):
        """要求模型输出 JSON，兼容无 json_mode 的端点：去代码围栏后解析。"""
        msg = self.chat(messages, model=model, temperature=temperature)
        return parse_json(msg.content or "")


def call_llm_with_retry(messages, *, model=None, temperature=None, max_tokens=None,
                       response_format=None, tools=None, tool_choice=None, **kw):
    """模块级便捷入口，供 tools/definitions.py 等后续直接调用。

    优先从环境/配置中读取默认 key 构造临时 LLMClient；构造不出或调用失败时，
    统一返回伪响应（不抛异常），行为与 LLMClient._create 一致。
    """
    api_key = kw.pop("api_key", None)
    base_url = kw.pop("base_url", None)
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
        _log_llm_failure(err, messages)
        return _fake_response(f"【LLM 暂时不可用，请稍后重试。错误摘要：{err}】")

    client = LLMClient(api_key=api_key, base_url=base_url, model=model or default_model)
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

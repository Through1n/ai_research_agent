"""LLM 客户端封装：统一重试、流式输出、JSON 解析、多模态调用。基于 OpenAI 兼容协议。"""
import time
import json
import re
from openai import OpenAI


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
        if not self._client:
            raise RuntimeError("未配置 API Key，无法调用大模型。")
        last = None
        for attempt in range(3):
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as e:  # 指数退避重试
                last = e
                if attempt == 2:
                    raise
                time.sleep(1.4 * (attempt + 1))
        raise last

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
        """流式文本生成，逐 token yield 字符串。"""
        if not self._client:
            raise RuntimeError("未配置 API Key，无法调用大模型。")
        stream = self._client.chat.completions.create(
            model=model or self.model, messages=messages, temperature=temperature, stream=True
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                yield delta.content

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

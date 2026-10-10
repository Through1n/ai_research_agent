"""模型 Provider 注册表：一套 OpenAI 兼容协议，灵活切换多家大模型。

设计说明（对应赛题「大模型选型理由 / 集成方式」考察点）：
- 所有 Provider 均走 OpenAI 兼容 chat/completions 协议，LLMClient 零改动即可复用；
- 每个 Provider 声明对话模型、视觉模型与多模态能力标志，
  无视觉能力的 Provider（DeepSeek / Ollama 默认）自动降级，界面给出明确提示；
- 支持 LLM_PROVIDERS_EXTRA 环境变量追加自定义 Provider（内网网关等）；
- 本地 Ollama 无需真实 Key，填任意非空字符串即可激活。
"""
import json
import os
from dataclasses import dataclass, field


@dataclass
class Provider:
    id: str
    label: str
    base_url: str
    model: str
    vl_model: str = ""            # 空 = 无多模态能力
    multimodal: bool = False
    needs_key: bool = True        # Ollama 本地服务无需密钥
    key_env: str = "LLM_API_KEY"  # 默认从该环境变量读 Key
    hint: str = ""
    models_hint: str = field(default="")

    @property
    def has_vision(self):
        return self.multimodal and bool(self.vl_model)


PROVIDERS = [
    Provider(
        id="zhipu", label="智谱 GLM（默认）",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        model="glm-4-flash", vl_model="glm-4v", multimodal=True,
        key_env="ZHIPU_API_KEY",
        hint="免费 Key 在 open.bigmodel.cn 申请；glm-4-flash 文本免费，glm-4v 支持 OCR",
        models_hint="glm-4-flash / glm-4v",
    ),
    Provider(
        id="deepseek", label="DeepSeek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat", vl_model="", multimodal=False,
        key_env="DEEPSEEK_API_KEY",
        hint="api.deepseek.com 申请；无视觉模型，扫描件 OCR 将自动降级为离线文本解析",
        models_hint="deepseek-chat",
    ),
    Provider(
        id="qwen", label="通义千问（DashScope 兼容模式）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-turbo", vl_model="qwen-vl-plus", multimodal=True,
        key_env="DASHSCOPE_API_KEY",
        hint="bailian.console.aliyun.com 申请；支持函数调用与视觉",
        models_hint="qwen-turbo / qwen-vl-plus",
    ),
    Provider(
        id="openai", label="OpenAI",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini", vl_model="gpt-4o-mini", multimodal=True,
        key_env="OPENAI_API_KEY",
        hint="platform.openai.com 申请；需要国际网络环境",
        models_hint="gpt-4o-mini",
    ),
    Provider(
        id="ollama", label="本地 Ollama（完全离线）",
        base_url="http://localhost:11434/v1",
        model="qwen2.5:7b", vl_model="", multimodal=False,
        needs_key=False, key_env="OLLAMA_API_KEY",
        hint="本机执行 ollama serve + ollama pull qwen2.5:7b；Key 填任意非空值即可",
        models_hint="qwen2.5:7b 等任意本地模型",
    ),
]


def _extra_providers():
    """LLM_PROVIDERS_EXTRA=[{"id":"mycloud","label":"内网网关","base_url":"...","model":"...","api_key_env":"..."}]"""
    raw = os.getenv("LLM_PROVIDERS_EXTRA", "")
    out = []
    if raw:
        try:
            for e in json.loads(raw):
                out.append(Provider(
                    id=e["id"], label=e.get("label", e["id"]), base_url=e["base_url"],
                    model=e.get("model", "default"), vl_model=e.get("vl_model", ""),
                    multimodal=bool(e.get("multimodal")),
                    needs_key=bool(e.get("needs_key", True)),
                    key_env=e.get("api_key_env", "LLM_API_KEY"),
                ))
        except Exception:
            pass
    return out


def list_providers():
    return PROVIDERS + _extra_providers()


CUSTOM_ID = "custom"


def custom_provider(spec):
    """从界面填写的端点描述构建 Provider（任意 OpenAI 兼容模型均可接入）。"""
    vl = (spec.get("vl_model") or "").strip()
    return Provider(
        id=CUSTOM_ID, label="自定义端点",
        base_url=(spec.get("base_url") or "").strip(), model=(spec.get("model") or "").strip(),
        vl_model=vl, multimodal=bool(vl),
        needs_key=bool(spec.get("needs_key", True)), key_env="LLM_API_KEY",
        hint="OpenAI 兼容协议：vLLM / LM Studio / 硅基流动等任意网关均可",
        models_hint=(spec.get("model") or "") + (f" / {vl}" if vl else ""),
    )


def resolve_key(prov, manual=""):
    """Key 优先级：用户在界面手动填写 > 对应 Provider 的环境变量。"""
    if manual and manual.strip():
        return manual.strip()
    if not prov.needs_key:
        return "local"  # 本地/免密端点用占位值激活客户端
    return os.getenv(prov.key_env, "") or os.getenv("LLM_API_KEY", "")


def get_provider(pid):
    for p in list_providers():
        if p.id == pid:
            return p
    return PROVIDERS[0]  # 默认智谱

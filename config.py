"""全局配置：路径、模型、常量。所有参数均可通过 .env / 环境变量覆盖。"""
import os
import sys
import shutil
from pathlib import Path
from dotenv import load_dotenv

# ---------- 桌面 / 开发双模式判定（见 desktop/INTERFACE_CONTRACT.md §3） ----------
FROZEN = getattr(sys, "frozen", False)
DESKTOP_MODE = FROZEN or os.environ.get("AI4S_DESKTOP") == "1"

# 项目根目录（本文件所在目录）
ROOT = Path(__file__).resolve().parent

if FROZEN:
    # 冻结打包态：业务代码已由 desktop/paths.ensure_app_copy 从 app_bundle 复制到
    # %APPDATA%/AI4SResearchAgent/app/，config.py 即位于该目录；以自身位置作为资源根，
    # 使 demo_data/、.streamlit/、lib/ 等业务静态资源随热更新目录一起解析。
    # 可写数据始终放在 %APPDATA%/AI4SResearchAgent 下（与桌面壳共享同一 data_root）。
    RESOURCE_ROOT = Path(__file__).resolve().parent
    # APPDATA 缺失时回落用户主目录，避免 import 期 KeyError 直接崩
    DATA_ROOT = Path(os.environ.get("APPDATA") or Path.home()) / "AI4SResearchAgent"
    ROOT = RESOURCE_ROOT
else:
    # 开发态：资源与数据都在项目根，行为与历史完全一致
    RESOURCE_ROOT = ROOT
    DATA_ROOT = ROOT

# ---------- 路径 ----------
STORAGE_DIR = DATA_ROOT / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
CHROMA_DIR = STORAGE_DIR / "chroma"
# PDF 与存储同生命周期：桌面态归入 storage/pdf；纯开发态保持项目根 pdf/
PDF_DIR = (DATA_ROOT / "storage" / "pdf") if DESKTOP_MODE else ROOT / "pdf"
DEMO_DIR = RESOURCE_ROOT / "demo_data"
LOGS_DIR = DATA_ROOT / "logs" if DESKTOP_MODE else ROOT / "storage"

_mkdirs = [STORAGE_DIR, UPLOAD_DIR, CHROMA_DIR, PDF_DIR]
if DESKTOP_MODE:
    _mkdirs += [LOGS_DIR, DATA_ROOT / "config"]
for _d in _mkdirs:
    _d.mkdir(parents=True, exist_ok=True)

# ---------- .env 加载 ----------
if DESKTOP_MODE:
    # 桌面态：优先用户数据目录 .env；不存在时尝试从资源侧 .env.example 复制一份
    _env_user = DATA_ROOT / ".env"
    if not _env_user.exists():
        _env_example = RESOURCE_ROOT / ".env.example"
        if _env_example.exists():
            try:
                shutil.copyfile(_env_example, _env_user)
            except OSError:
                pass
    load_dotenv(_env_user)                          # load_dotenv 不覆盖已有环境变量
    load_dotenv(RESOURCE_ROOT / ".env")             # fallback：资源侧 .env（不覆盖用户侧）
else:
    load_dotenv(ROOT / ".env")

# ---------- 环境变量安全读取（Round 9 S6：默认值兜底 + 类型转换安全） ----------
def _st_secret(name):
    """从 Streamlit Secrets 读值：Streamlit Community Cloud / HuggingFace Spaces
    的官方密钥入口。

    为什么需要这一层：本仓库的 .env 被 .gitignore 排除，推到云平台后**根本不存在**，
    而代码原本只读 os.getenv + .env —— 在云上会一个 Key 都拿不到，整站静默降级成
    "离线模式"，评委看到的就是所有 AI 功能不可用。Streamlit 官方承诺 Secrets 可用
    st.secrets 读取（是否同时注入 os.environ 未承诺），所以补这一层兜底。

    非 Streamlit 运行环境（桌面版 / 单元测试 / 脚本）访问 st.secrets 会抛异常，
    这里直接吞掉，行为与改动前完全一致。
    """
    try:
        import streamlit as st
        v = st.secrets.get(name, "")
    except Exception:
        return ""
    return str(v or "").strip()


def _env_str(name, default=""):
    """读环境变量：缺失或空白值都回落默认值。

    os.getenv(name, default) 在变量存在但为空串时返回 ""（不是 default），
    会把后面的默认值/回退链顶掉；这里统一把空白视为未设置。
    读取顺序：os.environ（含 .env 注入）→ Streamlit Secrets → default。
    """
    v = os.getenv(name)
    if v is None or not v.strip():
        v = _st_secret(name)
    if not v:
        return default
    v = v.strip()
    return v if v else default


def _env_int(name, default):
    """整数配置：空串 / 非数字（如 int('') 直接崩）都安全回落默认值。"""
    try:
        return int(_env_str(name, str(default)))
    except Exception:
        return default


def _env_float(name, default):
    """浮点配置：空串 / 非数字（如 float('') 直接崩）都安全回落默认值。"""
    try:
        return float(_env_str(name, str(default)))
    except Exception:
        return default


LIBRARY_JSON = STORAGE_DIR / "library.json"
MEMORY_JSON = STORAGE_DIR / "memory.json"
PROFILE_JSON = STORAGE_DIR / "user_profile.json"

# ---------- 大模型（默认智谱 GLM，OpenAI 兼容协议） ----------
LLM_BASE_URL = _env_str("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
# 注意：ZHIPU_API_KEY 设为空串时不再"屏蔽" LLM_API_KEY（os.getenv 双参默认值的坑）
LLM_API_KEY = _env_str("ZHIPU_API_KEY", "") or _env_str("LLM_API_KEY", "")
LLM_MODEL = _env_str("LLM_MODEL", "glm-4-flash")        # 文本/规划/函数调用
VL_MODEL = _env_str("VL_MODEL", "glm-4v")               # 多模态/OCR
LLM_TIMEOUT = _env_float("LLM_TIMEOUT", 45.0)
# 单次 LLM 调用的**总时长预算**（秒）：1 次立即尝试 + 最多 3 次退避重试，累计超预算即降级。
# **不裁剪单次 LLM_TIMEOUT 窗口**，不影响正常成功请求。演示保险（Round 13 网络降级演练）：
# 默认 LLM_TIMEOUT=45 / 预算=20，失败最坏等待压到约 45s（原 90s），避免台上长时间转圈；
# 现场想更严可设 LLM_TIMEOUT=25，慢生成仍可享 45s 窗口。
LLM_RETRY_BUDGET_SEC = _env_float("LLM_RETRY_BUDGET_SEC", 20.0)

# HuggingFace 国内镜像（供 chroma 默认 embedding 首次下载）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# ---------- 检索 / 解析参数 ----------
CHUNK_SIZE = _env_int("CHUNK_SIZE", 1200)                  # 切块目标字符数
CHUNK_OVERLAP = _env_int("CHUNK_OVERLAP", 150)
DENSE_K = _env_int("DENSE_K", 12)                          # 向量召回数
SPARSE_K = _env_int("SPARSE_K", 12)                        # BM25 召回数
FINAL_K = _env_int("FINAL_K", 6)                           # 融合后进入上下文数
DEDUP_THRESHOLD = _env_float("DEDUP_THRESHOLD", 0.90)      # 语义去重相似度阈值
AGENT_MAX_ITERS = _env_int("AGENT_MAX_ITERS", 8)
# 单个上传 PDF 的体积上限（应用层前置闸门，解析之前拦截）。
# 与 Streamlit 的 server.maxUploadSize（默认 200MB）不冲突：那个是 HTTP 层硬上限，
# 这里是解析前的应用层友好拒绝，超限会给出"建议 ≤100MB / 加载演示数据"的提示。
MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 100)

# ---------- 内置演示语料（无 Key 也可演示） ----------
DEMO_PAPERS = {
    "1706.03762v7.pdf": {
        "arxiv_id": "1706.03762", "version": "v7", "title": "Attention Is All You Need",
        "year": "2017", "authors": "Ashish Vaswani; Noam Shazeer; Niki Parmar; Jakob Uszkoreit; Llion Jones; Aidan N. Gomez; Lukasz Kaiser; Illia Polosukhin",
        "text": """Attention Is All You Need (v7 Latest)
Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Lukasz Kaiser, Illia Polosukhin
Google Brain, Google Research, University of Toronto

Abstract: We propose the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely. Our model achieves 28.4 BLEU on WMT 2014 English-to-German translation, improving over the best previously reported results including ensembles by more than 2 BLEU. On WMT 2014 English-to-French, we achieve 41.8 BLEU after training for 3.5 days on eight GPUs, a small fraction of the training costs of the best models from the literature.

1 Introduction
Recurrent neural networks, long short-term memory and gated recurrent neural networks in particular, have been firmly established as state of the art approaches in sequence modeling and transduction problems such as language modeling and machine translation.

3 Model Architecture
Most competitive neural sequence transduction models have an encoder-decoder structure. For each model, the number of parameters is 65 million for the base model and 213 million for the big model.

3.1 Encoder and Decoder Stacks
Encoder: the encoder is composed of a stack of N = 6 identical layers. Each layer has two sub-layers. The decoder is also composed of a stack of N = 6 identical layers.

3.2 Attention
An attention function can be described as mapping a query and a set of key-value pairs to an output. Scaled Dot-Product Attention: Attention(Q,K,V) = softmax(QK^T / sqrt(d_k)) V. Multi-head attention allows the model to jointly attend to information from different representation subspaces at different positions.

5 Training
We trained on the standard WMT 2014 English-German dataset consisting of about 4.5 million sentence pairs. We trained the base model for 100,000 steps using the Adam optimizer with beta_1 = 0.9, beta_2 = 0.98 and epsilon = 10^-9. We used eight NVIDIA P100 GPUs. The base model was trained for 12 hours and the big model for 3.5 days.

References
[1] Yoshua Bengio, et al. A neural probabilistic language model. JMLR, 2003.
[11] Sepp Hochreiter and Jurgen Schmidhuber. Long short-term memory. Neural Computation, 1997.
[38] Ilya Sutskever, et al. Sequence to sequence learning with neural networks. NIPS, 2014.""",
    },
    "1706.03762v1.pdf": {
        "arxiv_id": "1706.03762", "version": "v1", "title": "Attention Is All You Need",
        "year": "2017", "authors": "Ashish Vaswani; Noam Shazeer; Niki Parmar; Jakob Uszkoreit",
        "text": """Attention Is All You Need (v1 First Version)
Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit

Abstract: We propose the Transformer, based solely on attention mechanisms. We achieve 28.4 BLEU on WMT 2014 English-to-German translation.

1 Introduction
Recurrent neural networks have been established as state of the art in sequence modeling.

3 Model Architecture
Encoder-decoder structure.

References
[1] Yoshua Bengio, et al. A neural probabilistic language model. JMLR, 2003.
[13] Sepp Hochreiter and Jurgen Schmidhuber. Long short-term memory. 1997.""",
    },
    "1810.04805v2.pdf": {
        "arxiv_id": "1810.04805", "version": "v2", "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "year": "2018", "authors": "Jacob Devlin; Ming-Wei Chang; Kenton Lee; Kristina Toutanova",
        "text": """BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding
Jacob Devlin, Ming-Wei Chang, Kenton Lee, Kristina Toutanova
Google AI Language

Abstract: We introduce a new language representation model called BERT, which stands for Bidirectional Encoder Representations from Transformers. BERT obtains new state-of-the-art results on eleven natural language processing tasks.

1 Introduction
Language model pre-training has been shown to be effective for improving many natural language processing tasks.

3 BERT
There are two steps in our framework: pre-training and fine-tuning. BERT is designed to pre-train deep bidirectional representations from unlabeled text. We present two model sizes: BERT_BASE (L=12, H=768, 110 million parameters) and BERT_LARGE (L=24, H=1024, 340 million parameters). BERT_BASE uses the same model size as the Transformer encoder in Attention Is All You Need.

References
[13] Ashish Vaswani, et al. Attention is all you need. In NIPS, 2017.
[11] Sepp Hochreiter and Jurgen Schmidhuber. Long short-term memory. 1997.""",
    },
}


def get_cfg():
    """以字典形式返回关键配置，便于文档/页面展示。"""
    return {
        "LLM_BASE_URL": LLM_BASE_URL, "LLM_MODEL": LLM_MODEL, "VL_MODEL": VL_MODEL,
        "CHUNK_SIZE": CHUNK_SIZE, "DENSE_K": DENSE_K, "SPARSE_K": SPARSE_K,
        "FINAL_K": FINAL_K, "DEDUP_THRESHOLD": DEDUP_THRESHOLD, "AGENT_MAX_ITERS": AGENT_MAX_ITERS,
    }

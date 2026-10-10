"""全局配置：路径、模型、常量。所有参数均可通过 .env / 环境变量覆盖。"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（本文件所在目录）
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# ---------- 路径 ----------
STORAGE_DIR = ROOT / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
CHROMA_DIR = STORAGE_DIR / "chroma"
PDF_DIR = ROOT / "pdf"
DEMO_DIR = ROOT / "demo_data"
for _d in (STORAGE_DIR, UPLOAD_DIR, CHROMA_DIR, PDF_DIR):
    _d.mkdir(parents=True, exist_ok=True)

LIBRARY_JSON = STORAGE_DIR / "library.json"
MEMORY_JSON = STORAGE_DIR / "memory.json"
PROFILE_JSON = STORAGE_DIR / "user_profile.json"

# ---------- 大模型（默认智谱 GLM，OpenAI 兼容协议） ----------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
LLM_API_KEY = os.getenv("ZHIPU_API_KEY", os.getenv("LLM_API_KEY", ""))
LLM_MODEL = os.getenv("LLM_MODEL", "glm-4-flash")        # 文本/规划/函数调用
VL_MODEL = os.getenv("VL_MODEL", "glm-4v")               # 多模态/OCR
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))

# HuggingFace 国内镜像（供 chroma 默认 embedding 首次下载）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# ---------- 检索 / 解析参数 ----------
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))          # 切块目标字符数
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
DENSE_K = int(os.getenv("DENSE_K", "12"))                  # 向量召回数
SPARSE_K = int(os.getenv("SPARSE_K", "12"))                # BM25 召回数
FINAL_K = int(os.getenv("FINAL_K", "6"))                   # 融合后进入上下文数
DEDUP_THRESHOLD = float(os.getenv("DEDUP_THRESHOLD", "0.90"))   # 语义去重相似度阈值
AGENT_MAX_ITERS = int(os.getenv("AGENT_MAX_ITERS", "8"))

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

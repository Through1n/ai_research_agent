"""全局配置：路径、模型、常量。所有参数均可通过 .env / 环境变量覆盖。"""
import os
import sys
import shutil
from pathlib import Path
from dotenv import load_dotenv

# ---------- 品牌（用户可见名称的唯一来源）----------
# 改名只动这两行 + desktop/about.py 的 APP_NAME（桌面壳不导入业务包，需自带一份）。
# 注意：AI4SResearchAgent 这个**内部标识**（exe 名 / %APPDATA% 目录 / 环境变量前缀）
# 保持不变——改它会搬走用户已有的文献库与配置，属于数据迁移，不随品牌改名一起做。
BRAND = "MoreHand"
BRAND_TAGLINE = "研究流水线 · 文献 → 问答 → 图谱 → 写作"

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

# HuggingFace 镜像：仅桌面态（国内）启镜像；云平台留空走 huggingface.co（境外连镜像更慢/易超时）
if DESKTOP_MODE: os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

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
    # ---- 以下 3 篇为「真公式」参考语料（2026-10-25 新增）----
    # 旧 3 篇（v1/v7/BERT）的 text 是人工摘写的散文摘要，几乎不含数学表达式，
    # 导致「🧮 提取 LaTeX 公式」只能靠模型从词里猜（实测抽出 sum / prod 这类词）。
    # 这 3 篇的公式行逐条摘录自 PDF 文本层里**真实出现**的表达式，可回原文核对。
    #
    # 为什么公式写成 LaTeX 记号而不是 PDF 文本层原样（m̂ / v̂ 这类组合字符）：
    # 实测三种写法喂同一个提示词，只有 LaTeX 记号能被逐字复现——
    #   `m̂t`        → `m^{\hat{}}_t`（帽子挂空，渲染出悬空小帽）
    #   `m̂_t`       → `m_t^{\hat}`（帽子变上标，位置错）
    #   `\hat{m}_t` → `\hat{m}_t` ✅
    # PDF 原样那种退化由 ui/page_library.py 的 _fix_latex_controls 兜底（用户上传的
    # 真 PDF 走的就是那条路），演示语料这边则直接给无歧义写法，两不耽误。
    "1412.6980v9.pdf": {
        "arxiv_id": "1412.6980", "version": "v9", "title": "Adam: A Method for Stochastic Optimization",
        "year": "2015", "authors": "Diederik P. Kingma; Jimmy Lei Ba",
        "text": r"""Adam: A Method for Stochastic Optimization (v9 Latest)
Diederik P. Kingma, Jimmy Lei Ba
University of Amsterdam / OpenAI; University of Toronto

Abstract: We introduce Adam, an algorithm for first-order gradient-based optimization of stochastic objective functions, based on adaptive estimates of lower-order moments. The method is straightforward to implement, is computationally efficient, has little memory requirements, is invariant to diagonal rescaling of the gradients, and is well suited for problems that are large in terms of data and/or parameters. The method is also appropriate for non-stationary objectives and problems with very noisy and/or sparse gradients.

2 Algorithm
Algorithm 1: Adam, our proposed algorithm for stochastic optimization. Good default settings for the tested machine learning problems are \alpha = 0.001, \beta_1 = 0.9, \beta_2 = 0.999 and \epsilon = 10^{-8}. All operations on vectors are element-wise.
Require: \alpha: Stepsize
Require: \beta_1, \beta_2 \in [0, 1): Exponential decay rates for the moment estimates
Require: f(\theta): Stochastic objective function with parameters \theta
Require: \theta_0: Initial parameter vector
m_0 \leftarrow 0 (Initialize 1st moment vector)
v_0 \leftarrow 0 (Initialize 2nd moment vector)
t \leftarrow 0 (Initialize timestep)
while \theta_t not converged do
  t \leftarrow t + 1
  g_t \leftarrow \nabla_\theta f_t(\theta_{t-1}) (Get gradients w.r.t. stochastic objective at timestep t)
  m_t \leftarrow \beta_1 \cdot m_{t-1} + (1 - \beta_1) \cdot g_t (Update biased first moment estimate)
  v_t \leftarrow \beta_2 \cdot v_{t-1} + (1 - \beta_2) \cdot g_t^2 (Update biased second raw moment estimate)
  \hat{m}_t \leftarrow m_t / (1 - \beta_1^t) (Compute bias-corrected first moment estimate)
  \hat{v}_t \leftarrow v_t / (1 - \beta_2^t) (Compute bias-corrected second raw moment estimate)
  \theta_t \leftarrow \theta_{t-1} - \alpha \cdot \hat{m}_t / (\sqrt{\hat{v}_t} + \epsilon) (Update parameters)
end while
return \theta_t (Resulting parameters)

2.1 Adam's update rule
An important property of Adam's update rule is its careful choice of stepsizes. Assuming \epsilon = 0, the effective step taken in parameter space at timestep t is \Delta_t = \alpha \cdot \hat{m}_t / \sqrt{\hat{v}_t}. The effective stepsize has two upper bounds: |\Delta_t| \leq \alpha \cdot (1 - \beta_1) / \sqrt{1 - \beta_2} in the case (1 - \beta_1) > \sqrt{1 - \beta_2}, and |\Delta_t| \leq \alpha otherwise. In more common scenarios, we will have that \hat{m}_t / \sqrt{\hat{v}_t} \approx \pm 1 since |E[g] / \sqrt{E[g^2]}| \leq 1. The effective magnitude of the steps taken in parameter space at each timestep are approximately bounded by the stepsize setting \alpha, i.e., |\Delta_t| \lesssim \alpha. This can be understood as establishing a trust region around the current parameter value, beyond which the current gradient estimate does not provide sufficient information.

3 Initialization bias correction
We can also derive an alternative form of the update, where the bias correction is folded into the stepsize: \alpha_t = \alpha \cdot \sqrt{1 - \beta_2^t} / (1 - \beta_1^t) and \theta_t \leftarrow \theta_{t-1} - \alpha_t \cdot m_t / (\sqrt{v_t} + \hat{\epsilon}). For the exponential moving average v_t = \beta_2 \cdot v_{t-1} + (1 - \beta_2) \cdot g_t^2 with v_0 = 0 the expectation is E[v_t] = E[g^2] \cdot (1 - \beta_2^t) + \zeta, which explains why the estimate is biased towards zero during the initial timesteps, and especially when the decay rates are small.

References
[1] Yoshua Bengio, et al. A neural probabilistic language model. JMLR, 2003.
[5] Diederik P. Kingma and Jimmy Lei Ba. Adam: A method for stochastic optimization. ICLR, 2015.
[8] Yurii Nesterov. Introductory lectures on convex optimization: a basic course. 2004.""",
    },
    "1312.6114v10.pdf": {
        "arxiv_id": "1312.6114", "version": "v10", "title": "Auto-Encoding Variational Bayes",
        "year": "2014", "authors": "Diederik P. Kingma; Max Welling",
        "text": r"""Auto-Encoding Variational Bayes (v10 Latest)
Diederik P. Kingma, Max Welling
Machine Learning Group, Universiteit van Amsterdam

Abstract: How can we perform efficient inference and learning in directed probabilistic models, in the presence of continuous latent variables with intractable posterior distributions, and large datasets? We introduce a stochastic variational inference and learning algorithm that scales to large datasets and, under some mild differentiability conditions, even works in the intractable case. First, we show that a reparameterization of the variational lower bound yields a lower bound estimator that can be straightforwardly optimized using standard stochastic gradient methods. Second, we show that for i.i.d. datasets with continuous latent variables per datapoint, posterior inference can be made especially efficient by fitting an approximate inference model to the intractable posterior.

2 Method
Let us consider some dataset X = \{x^{(i)}\}_{i=1}^{N} consisting of N i.i.d. samples of some continuous or discrete variable x. We assume that the data are generated by some random process, involving an unobserved continuous random variable z. The true parameters \theta^{*} as well as the values of the latent variables z^{(i)} are unknown to us. The marginal likelihood is p_\theta(x) = \int p_\theta(z) p_\theta(x|z) dz, and the true posterior density is p_\theta(z|x) = p_\theta(x|z) p_\theta(z) / p_\theta(x).

2.2 The variational bound
The marginal likelihood is composed of a sum over the marginal likelihoods of individual datapoints: \log p_\theta(x^{(1)}, \ldots, x^{(N)}) = \sum_{i=1}^{N} \log p_\theta(x^{(i)}), which can each be rewritten as
\log p_\theta(x^{(i)}) = D_{KL}(q_\phi(z|x^{(i)}) \Vert p_\theta(z|x^{(i)})) + \mathcal{L}(\theta, \phi; x^{(i)})
The first RHS term is the KL divergence of the approximate posterior from the true posterior. Since the KL-divergence is non-negative, the second RHS term \mathcal{L}(\theta, \phi; x^{(i)}) is called the (variational) lower bound on the marginal likelihood of datapoint i, and can be written as
\mathcal{L}(\theta, \phi; x^{(i)}) = \mathbb{E}_{q_\phi(z|x)}[-\log q_\phi(z|x) + \log p_\theta(x, z)]
which can also be written as
\mathcal{L}(\theta, \phi; x^{(i)}) = -D_{KL}(q_\phi(z|x^{(i)}) \Vert p_\theta(z)) + \mathbb{E}_{q_\phi(z|x^{(i)})}[\log p_\theta(x^{(i)}|z)]

2.3 The SGVB estimator and AEVB algorithm
We can reparameterize the random variable z \sim q_\phi(z|x) using a differentiable transformation z = g_\phi(\epsilon, x) of an auxiliary noise variable \epsilon \sim p(\epsilon). We can now form Monte Carlo estimates of expectations of some function f(z) w.r.t. q_\phi(z|x^{(i)}):
\mathbb{E}_{q_\phi(z|x^{(i)})}[f(z)] = \mathbb{E}_{p(\epsilon)}[f(g_\phi(\epsilon, x^{(i)}))] \simeq \frac{1}{L} \sum_{l=1}^{L} f(g_\phi(\epsilon^{(l)}, x^{(i)})) where \epsilon^{(l)} \sim p(\epsilon)
This yields the generic Stochastic Gradient Variational Bayes (SGVB) estimator
\tilde{\mathcal{L}}(\theta, \phi; x^{(i)}) = \frac{1}{L} \sum_{l=1}^{L} \log p_\theta(x^{(i)}, z^{(i,l)}) - \log q_\phi(z^{(i,l)}|x^{(i)})
where z^{(i,l)} = g_\phi(\epsilon^{(i,l)}, x^{(i)}) and \epsilon^{(l)} \sim p(\epsilon). We use settings M = 100 and L = 1 in experiments. The minibatch estimator of the lower bound is
\mathcal{L}(\theta, \phi; X) \simeq \tilde{\mathcal{L}}(\theta, \phi; X^{M}) = \frac{N}{M} \sum_{i=1}^{M} \tilde{\mathcal{L}}(\theta, \phi; x^{(i)})
where the minibatch X^{M} = \{x^{(i)}\}_{i=1}^{M} is a randomly drawn sample of M datapoints from the full dataset X.

References
[1] Yoshua Bengio, et al. A neural probabilistic language model. JMLR, 2003.
[4] Diederik P. Kingma and Max Welling. Auto-encoding variational bayes. ICLR, 2014.""",
    },
    "1502.03167v3.pdf": {
        "arxiv_id": "1502.03167", "version": "v3", "title": "Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift",
        "year": "2015", "authors": "Sergey Ioffe; Christian Szegedy",
        "text": r"""Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift (v3 Latest)
Sergey Ioffe, Christian Szegedy
Google Inc.

Abstract: Training Deep Neural Networks is complicated by the fact that the distribution of each layer's inputs changes during training, as the parameters of the previous layers change. This slows down the training by requiring lower learning rates and careful parameter initialization. We refer to this phenomenon as internal covariate shift, and address the problem by normalizing layer inputs. Batch Normalization allows us to use much higher learning rates, achieves the same accuracy with 14 times fewer training steps, and in some cases eliminates the need for Dropout.

3 Normalization via Mini-Batch Statistics
Since the full whitening of each layer's inputs is costly and not everywhere differentiable, we normalize each scalar feature independently, by making it have zero mean and unit variance. For a layer with d-dimensional input x = (x^{(1)}, \ldots, x^{(d)}), we normalize each dimension
\hat{x}^{(k)} = \frac{x^{(k)} - E[x^{(k)}]}{\sqrt{Var[x^{(k)}]}}
where the expectation and variance are computed over the training data set. Note that simply normalizing each input of a layer may change what the layer can represent. To address this, we introduce, for each activation x^{(k)}, a pair of parameters \gamma^{(k)}, \beta^{(k)}, which scale and shift the normalized value:
y^{(k)} = \gamma^{(k)} \hat{x}^{(k)} + \beta^{(k)}
These parameters are learned along with the original model parameters, and restore the representation power of the network. Indeed, by setting \gamma^{(k)} = \sqrt{Var[x^{(k)}]} and \beta^{(k)} = E[x^{(k)}], we could recover the original activations, if that were the optimal thing to do.

3.1 Training and Inference with Batch-Normalized Networks
Since the statistics of activations depend on the mini-batch, we use mini-batches. The mean and variance are computed over each mini-batch of size m:
\mu_B = \frac{1}{m} \sum_{i=1}^{m} x_i
\sigma_B^2 = \frac{1}{m} \sum_{i=1}^{m} (x_i - \mu_B)^2
\hat{x}_i = \frac{x_i - \mu_B}{\sqrt{\sigma_B^2 + \epsilon}}
y_i = \gamma \hat{x}_i + \beta \equiv BN_{\gamma, \beta}(x_i)
where \epsilon is a constant added to the mini-batch variance for numerical stability. The BN transform can be added to a network to manipulate any activation. In the batch setting, the normalization is applied to each activation independently.

3.3 Batch Normalization enables higher learning rates
In traditional deep networks, too-high learning rate may result in the gradients that explode or vanish, as well as getting stuck in poor local minima. By normalizing activations throughout the network, Batch Normalization prevents small changes in layer parameters from amplifying as the data propagates through a deeper network. For example, this enables training with the saturation of the sigmoid nonlinearity, which is known to be problematic.

4 Experiments
Applied to a state-of-the-art image classification model, Batch Normalization achieves 4.9% top-5 validation error and 4.8% test error on ImageNet, exceeding the accuracy of human raters.

References
[1] Jimmy Lei Ba, et al. Layer normalization. 2016.
[2] Yoshua Bengio, et al. A neural probabilistic language model. JMLR, 2003.
[5] Sergey Ioffe and Christian Szegedy. Batch normalization. ICML, 2015.""",
    },
}


def get_cfg():
    """以字典形式返回关键配置，便于文档/页面展示。"""
    return {
        "LLM_BASE_URL": LLM_BASE_URL, "LLM_MODEL": LLM_MODEL, "VL_MODEL": VL_MODEL,
        "CHUNK_SIZE": CHUNK_SIZE, "DENSE_K": DENSE_K, "SPARSE_K": SPARSE_K,
        "FINAL_K": FINAL_K, "DEDUP_THRESHOLD": DEDUP_THRESHOLD, "AGENT_MAX_ITERS": AGENT_MAX_ITERS,
    }

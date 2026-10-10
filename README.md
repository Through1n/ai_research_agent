# 🔬 AI4S 科研助手 Agent

> 智领科研 · 意图驱动 —— 基于大模型与 Agent 架构的全流程科研助手

全国高校 AI 大模型挑战赛参赛作品。系统深度融合大语言模型（LLM）的通用推理能力与 Agent 架构的自主交互能力，
从 0 到 1 构建覆盖**文献智能解析、抗幻觉问答、沉浸式对比翻译、自动检索推荐、引用图谱分析与学术写作辅助**的
全流程「科研助手 Agent」。

---

## ✨ 核心特性（对应赛题六大模块）

| 模块 | 能力 | 关键技术 |
|---|---|---|
| ① PDF 管理与细粒度解析 | 单/批量上传、语义去重、版本管理、扫描件 OCR、标题树/公式/表格/图表/参考文献抽取 | PyMuPDF、多模态 LLM、论文级 Embedding |
| ② 抗幻觉知识问答 | 单篇细粒度问答（超参数/硬件/BLEU）、多篇跨文献对比推理、原文出处锚点 | BM25+向量混合检索(RRF)、LLM 重排、内联【n】溯源 |
| ③ 版式阅读与双向翻译 | 双栏原生渲染、标题树导航、引用↔参考文献双向跳转、中英同步滚动、划词翻译 | 坐标级版面还原、JS 比例+段落对齐联动 |
| ④ 检索追踪与推荐 | arXiv/PubMed/网页检索、自动下载入库、批量抓取、引用含金量筛选、个性化推荐、动态追踪 | arXiv API、Function Calling、GitHub 复现加权 |
| ⑤ 引用图谱与综述 | 真实引用关系建图、PageRank/中心性挖掘基石/边缘、自动综述、Future Work 前沿过滤 | NetworkX、vis-network、时间线比对 |
| ⑥ 写作与可视化 Copilot | 无幻觉论文框架、Mermaid/TikZ/Graphviz/Matplotlib 拓扑图、CSV→图表+Figure Caption、中英学术翻译 | 真实文献约束生成、Plotly |

**Agent 能力**：任务规划（Task Planning）、多专家路由（Multi-Agent Router）、Function Calling 闭环、
短期/长期记忆与用户画像、Guardrails 安全防护、全程思考过程（Trace）可视化与流式输出。

---

## 🏗️ 系统架构

```
用户自然语言意图
        │
 Streamlit 交互层（PDF阅读 / 图谱 / 对话 / 可视化）
        │
 Agent 内核：Guardrails → Task Planning → Multi-Agent Router → Function Calling 闭环
        │            └── Memory（短期对话 / 长期笔记 / 用户画像）
        │
 工具/服务层：PDF解析·OCR │ 混合RAG │ arXiv·PubMed·Web │ 引用图谱·图论 │ 写作·绘图·代码执行
        │
 存储层：Chroma 向量库 │ BM25 索引 │ 文献库元数据 │ 原始 PDF/图片
```

---

## 🧰 技术栈

- **前端/可视化**：Streamlit、Plotly、vis-network（本地离线）、KaTeX
- **Agent / RAG**：自研 Agent 内核（规划/路由/记忆/防护）、ChromaDB、rank-bm25、RRF 融合
- **文档处理**：PyMuPDF、多模态 LLM（OCR / 图表 / 公式）
- **图谱**：NetworkX（PageRank、入度、中介、特征向量中心性）
- **外部集成**：arXiv、PubMed、DuckDuckGo、GitHub 链接识别
- **大模型**：默认智谱 GLM（glm-4-flash / glm-4v，OpenAI 兼容协议），可通过环境变量切换任意兼容端点
- **工程**：Conda、uv/pip、Docker、一键启动脚本

---

## 🚀 快速开始

### 方式一：一键启动（Windows）

双击项目根目录的 **`启动科研助手.bat`**，浏览器自动打开 `http://localhost:8501`。

### 方式二：手动启动（Conda）

```bash
conda create -n ai_research python=3.11 -y
conda activate ai_research
cd C:\Users\Administrator\ai_research_agent
pip install -r requirements.txt
streamlit run app.py
```

### 方式三：Docker

```bash
cp .env.example .env       # 填入 API Key
docker compose up -d       # 访问 http://localhost:8501
```

---

## 🔑 配置

复制 `.env.example` 为 `.env` 并填入智谱 API Key（也可直接在应用侧边栏填入）：

```env
ZHIPU_API_KEY=你的Key
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
LLM_MODEL=glm-4-flash
VL_MODEL=glm-4v
```

> ⚠️ API Key 仅通过环境变量 / 侧边栏注入，**不会**写入公开仓库或代码。
> 无 Key 时，PDF 原生解析、混合检索、引用图谱等离线功能仍可运行；OCR、问答、翻译、综述等需联网调用模型。

---

## 🧪 测试数据集与验证用例

| 用例 | 文件 | 预期 |
|---|---|---|
| 内容去重 / 版本演进 | `demo_data/1706.03762v7.pdf`、`pdf/1706.03762v1.pdf` | 基于语义（非文件名）检测 v1/v7，提示保留最新版本 |
| 标准矢量 PDF | `pdf/1706.03762v1.pdf` | 双栏文本、标题树、公式、表格精细解析 |
| 图像扫描版 | `pdf/1706.03762v1_img.pdf` | 检测为扫描件，多模态 OCR 还原 |
| 超长篇文档 | 官方《Understanding Deep Learning》或 `pdf/LongBook_540p_fixture.pdf` | 切块/索引/分级加载，内存可控（540 页实测约 251MB） |
| 多篇推理 / 图谱 | `demo_data/1810.04805v2.pdf`（BERT）等 | BERT→Transformer 引用边，Transformer 为基石论文 |

一键离线自检（含 Mock LLM 驱动 Agent）：

```bash
python scripts/selftest.py
```

---

## 📁 目录结构

```
ai_research_agent/
├── app.py                 # Streamlit 入口（精简分发）
├── config.py              # 路径 / 模型 / 参数配置
├── 启动科研助手.bat
├── requirements.txt / Dockerfile / docker-compose.yml / .env.example
├── core/                  # Agent 内核：llm / agent / planner / router / memory / guardrails / trace
├── services/              # 业务服务：parsing / library / rag / graph / writing / translate / extern / state
├── tools/                 # 工具注册中心 + 16 个工具定义（Function Calling）
├── ui/                    # 各页面与公共组件
├── scripts/               # 自检 / 压测 / 数据准备脚本
├── pdf/                   # 测试 PDF（v1、扫描件、长文档夹具）
├── demo_data/             # 演示数据（v7、BERT、CSV）
├── lib/                   # 本地离线 vis-network 等前端库
└── storage/               # 运行时：Chroma、library.json、uploads（git 忽略）
```

---

## 🎬 演示与文档

- 技术文档：`docs/技术文档.pdf`（≤30 页）
- 演示视频脚本：`docs/演示视频脚本.md`
- 在线演示：部署后填入评审地址（Docker 一键部署，见技术文档「测试与部署」）

---

## 👥 团队信息

- 参赛队伍：__________
- 组别：__________
- 成员：__________
- 指导教师：__________

---

## 📌 说明

- 本项目允许并使用 AI 辅助编程，核心架构与关键逻辑由参赛成员设计、理解与掌控。
- `pdf/LongBook_540p_fixture.pdf` 为本地生成的长文档鲁棒性测试夹具，非官方教材。

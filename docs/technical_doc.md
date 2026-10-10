# AI4S 科研助手 Agent · 技术文档

> 版本：v2.0（2026-10-10）
> 定位：全国高校 AI 大模型挑战赛参赛作品 · 全流程科研助手 Agent
> 本文档基于当前代码库（`main` 分支）真实实现编写，所有文件/函数引用均可在仓库内定位复现。

---

## 1. 项目概述

### 1.1 背景

大语言模型（LLM）的通用推理能力已相当成熟，但在学术科研这一高门槛、强证据、重引用的场景中，
直接把文献 PDF 丢给聊天式 Copilot 会面临三类硬伤：一是**检索幻觉**——模型凭记忆编造参考文献与页码；
二是**版面丢失**——双栏 PDF 被按阅读顺序左右串读，RAG 上下文语义断裂；三是**流程割裂**——
解析、问答、阅读、检索、图谱、写作散落在六七个工具里，研究员要反复手动搬运。

本项目从 0 到 1 构建一个**意图驱动**的科研助手 Agent：以自研 Agent 内核统一编排六大科研命题模块，
把「文献解析 → 抗幻觉问答 → 版式阅读 → 检索追踪 → 引用图谱 → 学术写作」串成一条闭环，
并以赛题官方 PDF 数据集实测背书关键指标。

### 1.2 目标用户

- **研究生 / 科研人员**：需要快速读懂一批英文论文、跨文献对比、追踪新论文、写开题/论文框架；
- **教学与评审场景**：需要在无真实 API Key 的演示环境下，仅凭离线链路即可完整跑通全部模块；
- **参赛与复现场景**：所有关键数字（去重率、建图耗时、长文档入库耗时）均有一键复跑脚本。

### 1.3 赛题定位

系统对应赛题五大能力大类：①多模态与细粒度文档感知、②抗幻觉 RAG、③自主工具调用（Function Calling）、
④引用图谱挖掘、⑤学术 Copilot 输出。并在此之上沉淀自研 Agent 内核（规划 / 多专家路由 / 记忆 / 三层 Guardrails / Trace 可视化）。
合规对照详见 `docs/audit_report_20261010.md` 与 README 中的「赛题要求对照表」。

---

## 2. 需求分析

### 2.1 功能需求（按赛题五大类）

| 大类 | 核心功能需求 | 实现落点 |
|---|---|---|
| ① 文档感知 | 单/批量上传、内容级语义去重（不依赖文件名）、版本差异总结、扫描件 OCR、标题树/公式/表格/参考文献抽取、双栏文本正确切分 | `services/parsing.py`、`services/library.py` |
| ② 抗幻觉 RAG | 单篇/多篇问答、跨语言混合检索、内联【n】溯源、出处锚点可点击跳转、引用校验 + 自动修复 | `services/rag.py`、`core/guardrails.py`、`ui/page_qa.py` |
| ③ 自主工具调用 | arXiv/PubMed/Web 检索、自动下钻入库、Python 沙箱执行、定时追踪、GitHub 复现性、引用含金量打分、用户画像推荐 | `tools/definitions.py`、`services/extern.py`、`services/track.py` |
| ④ 图谱挖掘 | 真实引用建图、PageRank/多种中心性挖掘基石/边缘、图谱驱动综述、Future Work 过滤 | `services/graph.py` |
| ⑤ 学术 Copilot | 无幻觉论文框架、真实 References 白名单校验、Mermaid/TikZ/Graphviz 拓扑图、CSV→图表+Figure Caption、中英学术互译 | `services/writing.py`、`ui/page_writing.py` |

### 2.2 非功能需求

- **可复现**：Streamlit 锁定 `1.63.x`，关键指标全部可由 `scripts/verify_official.py`、`scripts/bench_graph.py` 一键复跑；
- **安全**：API Key 只经环境变量或侧边栏会话注入，绝不落代码/前端；所有模型调用走后端代理；
- **健壮**：LLM 调用 3 次指数退避重试，失败落盘 `storage/llm_failures.log` 并返回伪响应，不打断 Agent 循环；
- **离线可演示**：无 Key 时 PDF 原生解析、混合检索、引用图谱、去重、追踪增量比对等离线链路仍可运行；
- **多 Provider 可切换**：智谱 / DeepSeek / 通义 / OpenAI / 本地 Ollama 一套 OpenAI 兼容协议接入；
- **长文档可入库**：541 页《Understanding Deep Learning》实测 1523 分块、26.3s、约 292MB 内存。

---

## 3. 系统架构

系统采用清晰的五层分层架构，自上而下依赖，底层不反向依赖上层：

```
┌─────────────────────────────────────────────────────────┐
│  UI 交互层（Streamlit，9 个页面）                          │
│  ui/page_agent / page_library / page_qa / page_reading    │
│  page_search / page_graph / page_writing / page_misc      │
│  ui/common.py（错误边界、source_card 可点击出处锚点）        │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  Agent 内核层（core/）                                     │
│  llm（统一重试/流式/JSON/多模态）  providers（Provider 注册表）│
│  agent（ReAct 闭环）  router（多专家路由）  planner（规划）    │
│  memory（短期/长期/画像）  guardrails（注入检测/角标校验）     │
│  sandbox（AST 白名单）  trace（思考可视化）                 │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  服务层（services/）                                       │
│  parsing / library / rag / query_expansion / graph        │
│  track / writing / translate / extern / citation_quality   │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  工具注册层（tools/）：16 个 Function Schema + 注册表        │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  数据/存储层（storage/，git 忽略）                          │
│  Chroma 向量库 │ BM25 索引 │ library.json │ memory.json     │
│  user_profile.json │ 追踪检查点 │ 点赞账本 │ 原始 PDF         │
└─────────────────────────────────────────────────────────┘
```

**调用主线**：用户在 `app.py` 选择侧边栏菜单 → 对应 `ui/page_*.py` 渲染 → 经 `services/state.get_container()`
拿到统一上下文 `ctx`（含向量库、BM25、chunks、LLM 客户端）→ 调用 `services/*` 业务服务；
Agent 页面则进一步交给 `core/agent.py` 的 ReAct 闭环，由 `core/router.py` 多专家路由决定暴露哪些 `tools/definitions.py` 工具。

**目录速览**：`core/`（内核）、`services/`（业务）、`tools/`（工具 Schema）、`ui/`（9 页 + 公共组件）、
`tests/`（18 项单元测试）、`scripts/`（复跑/守护脚本）、`pdf/` 与 `demo_data/`（赛题测试 PDF）、`docs/`（本文档等）、
`storage/`（运行时数据，git 忽略）。

---

## 4. AI 方案

### 4.1 LLM Provider 抽象与统一重试

所有 Provider 均走 **OpenAI 兼容 chat/completions 协议**，因此 `core/llm.py` 的 `LLMClient` 零改动即可复用。
`core/providers.py` 用 `Provider` dataclass 声明每家的对话模型、视觉模型与多模态能力标志：

- 智谱 GLM（`glm-4-flash` + `glm-4v`，多模态，默认）；
- DeepSeek（`deepseek-chat`，无视觉，OCR 自动降级为离线文本解析）；
- 通义千问（`qwen-turbo` + `qwen-vl-plus`）；
- OpenAI（`gpt-4o-mini`）；
- 本地 Ollama（`qwen2.5:7b`，无需真实 Key，填任意非空值激活，完全离线）。

无视觉能力的 Provider 自动降级，界面给出明确提示；`LLM_PROVIDERS_EXTRA` 环境变量可追加内网自定义网关
（vLLM / LM Studio / 硅基流动等），侧边栏「➕ 自定义端点」也可直接填 Base URL + 模型名接入。

**统一重试（`core/llm.py` `_create`）**：3 次指数退避（1.5s / 3s / 4.5s），全部失败时**不抛异常**，
而是把失败摘要 append 到 `storage/llm_failures.log` 并返回一个与 OpenAI 响应兼容的伪响应
（`_fake_response`），让上层 for 循环与 Agent 迭代不被单次网络抖动打断。另有 `chat / stream / vision / json_chat`
四种调用封装，`parse_json` 兼容 ```json 代码围栏与前后多余文字。

### 4.2 抗幻觉 RAG 流程（`services/rag.py`）

检索管线为「**混合召回 → RRF 融合 → 可选 LLM 重排 → 带内联引用的生成 + 自动修复**」：

1. **查询扩展**：`services/query_expansion.py` 先做跨语言扩展（中文提问 → 英文检索词），带内存缓存与离线词表兜底；
2. **Dense 向量召回**：`dense_search` 走 Chroma，默认 `DENSE_K=12`；
3. **Sparse 稀疏召回**：`sparse_search` 走 BM25Plus（rank-bm25），对每个查询变体取逐块最大分，默认 `SPARSE_K=12`；
4. **RRF 融合**：`rrf_fuse`（c=60）把两路排名融合为 `FINAL_K=6` 条进入上下文；
5. **可选 LLM 重排**：`llm_rerank` 让模型按相关性对候选排序（cross-encoder 式），失败时原样回退；
6. **生成 + 引用修复环**：`build_context` 把每条来源编号为 `[n] 来源：《标题》 第N页`，
   `qa_messages` 注入学术系统提示；`answer()` 生成后调 `validate_citations` 校验角标范围，
   若越界/缺失则**自动带着错误答案再请求一次修正**（`temperature=0.0`），并对修正结果二次校验。
   QA 页面（`ui/page_qa.py`）直接复用 `rag.answer()`，不再内联重写，保证主 UI 路径上修复环不被绕过。

### 4.3 引用图谱算法（`services/graph.py`）

- **建图**：从 PDF References 段定位（`_REF_HEAD` 锚定 References/Bibliography/Works cited/参考文献），
  用「标题锚点 + IDF 稀有度加权倒排索引」生成候选被引论文，再用向量语义兜底捕获缩写标题，
  避免标题字面不一致丢边；
- **图指标**：`analyze()` 基于 NetworkX 计算 `pagerank(alpha=0.85)`、入度、`betweenness_centrality`、
  `eigenvector_centrality_numpy`，多处 `except` 回退（PR 失败退 1/N、eigenvector 失败退用 PR）；
- **分类**：`classify_papers` 按 (入度, PageRank) 倒序前 30% 判为基石论文（cornerstone），入度为 0 者为边缘衍生；
- **图谱驱动综述**：`generate_review` 不再只读摘要，而是按「基石论文 / 高中心性论文 / 边缘新兴」三层分层组织 prompt；
- **Future Work 过滤**：`explore_future` 用正则截取 Future Work 段落窗口，并结合引用图可达性与时间线做规则预判，
  自动排除已被后续工作解决的旧 idea。

### 4.4 抗幻觉策略（三层 Guardrails）

`core/guardrails.py` 提供三层防护：①**输入层**——`check_injection` 检测提示注入；
②**输出层**——`ACADEMIC_SYSTEM` 学术系统提示约束「只依据资料、每条事实带【n】」，
`validate_citations` 正则校验角标 `1~n_sources` 范围，并由 RAG 修复环自动修正；
③**执行层**——`core/sandbox.py` 的 `validate_code` 用 AST 白名单（numpy/pandas/sklearn/matplotlib/plotly 等）
拦截 `eval/exec/open/subprocess/socket` 等危险调用，再以独立 tempfile 子进程执行（超时 25s）。

写作模块另有**无幻觉 References 白名单**：`services/writing.py` 的 `_pdf_reference_entries` 从 PDF References 段
（而非库元数据）真实抽取条目，`_sanitize_citations` 在生成后用白名单正则核对 LLM 输出的 [n]，
越界引用直接剔除，杜绝凭空造条目。

---

## 5. 核心功能（按赛题五大类展开）

### 5.1 多模态与细粒度文档感知（services/parsing.py, services/library.py）

- **双栏分栏**：`_sorted_blocks_by_column(page, raw_blocks)`（parsing.py:59）取页面竖直中线 `x_mid = pw/2`，
  按块 x 坐标归入左/右栏，再按 y 坐标排序拼接——**彻底解决双栏左右串读污染 RAG chunks 的 P0 问题**；
- **语义去重**：`check_duplicate` 三路判定（内容相似度≥0.90、标题≥0.9+内容≥0.7、arXiv 号相同+内容≥0.7），
  基于「前 4000 字向量 + k=5 段指纹」，**不依赖文件名**；官方实测 v7→v1 命中 98.0%；
- **扫描件 OCR**：`detect_scanned` 判定文本层缺失后，整页转 PNG 走多模态 LLM vision，
  prompt 要求按阅读顺序、公式转 LaTeX、表格转 Markdown；
- **长文档**：`build_chunks_from_texts`（CHUNK_SIZE=1200, OVERLAP=150），541 页实测 1523 块 / 26.3s。

### 5.2 抗幻觉知识问答（services/rag.py, ui/page_qa.py）

单篇/多篇闭环、跨语言混合检索、内联【n】溯源、出处锚点可点击——`ui/common.py` 的 `source_card`
渲染蓝色「第 N 页」徽章并通过 `ui/page_reading.py` 的 query_params 跳转到对应 PDF 版式页，
实现「看答案 → 点页码 → 定位原文」的完整溯源链路。

### 5.3 自主工具调用（tools/definitions.py, core/router.py）

16 个 Function Schema 工具经注册表挂载，`core/agent.py` 的 ReAct 闭环
（LLM chat with tools → 解析 tool_calls → 回灌结果 → 最多 `AGENT_MAX_ITERS=8` 轮）自主下钻。
`core/router.py` 多专家路由：search 专家放行 arXiv/PubMed/Web，qa 专家挂多文对比，
graph 专家挂建图/综述/Future Work。P0/P1 修复后新增 `t_citation_quality`、`t_track_arxiv`、
`t_github_search` 三个工具，Agent 可自主触发引用打分、增量追踪、GitHub 复现性评估。

### 5.4 引用图谱与综述（services/graph.py, ui/page_graph.py）

vis-network 本地离线渲染真实引用图；PageRank/多种中心性自动识别基石；三层分层综述与
Future Work 时间线过滤。规模压测：n=20 建图 7.9s，n=40 建图 25.0s（渲染 0.2s）。

### 5.5 写作与可视化 Copilot（services/writing.py, ui/page_writing.py）

- **无幻觉框架**：`generate_outline` 注入真实文献白名单，生成后 `_sanitize_citations` 二次核对；
- **图表**：Mermaid 前端真实渲染（内联 mermaid@10），TikZ/Graphviz 输出代码块供 Overleaf 编译；
- **CSV→图表**：上传 CSV → Plotly 六类图（line/bar/scatter/Scatterpolar/imshow/parallel_coordinates）
  + `figure_caption` 自动生成英文规范图注；
- **学术翻译**：纯文本中英互译 + 中英对照双栏 JS 同步滚动（按比例 + 段落对齐双机制）+ 划词翻译（后端代理）。

---

## 6. 测试与部署

### 6.1 测试体系（三层）

| 层 | 命令 | 覆盖 |
|---|---|---|
| 单元测试 | `python -m unittest discover -s tests -v` | 18 项单元测试 |
| 离线端到端自检 | `python scripts/selftest.py` | 无需 Key 的全链路冒烟 |
| 官方实测复跑 | `python scripts/verify_official.py` | 去重/扫描件/长文档/图谱 |
| 图谱压测 | `python scripts/bench_graph.py` | 20/40 篇建图耗时 |
| 页面渲染检查 | `python scripts/apptest_check.py` | Streamlit 9 页面全绿 |
| 守护进程（可选） | `python scripts/watch_daemon.py 30` | arXiv 每 30 分钟增量追踪 |

### 6.2 本地启动

```bash
conda create -n ai_research python=3.11 -y
conda activate ai_research
cd ai_research_agent
pip install -r requirements.txt
streamlit run app.py        # 默认 http://localhost:8501
```

Windows 下也可双击根目录 `启动科研助手.vbs`（后台无黑窗口、自动开浏览器）。

### 6.3 Docker 部署

```bash
cp .env.example .env        # 填入 API Key
docker compose up -d        # Dockerfile 基于 python:3.11-slim，暴露 8501，挂载 ./storage
```

`Dockerfile` 关键设定：`HF_ENDPOINT=https://hf-mirror.com`（Chroma embedding 首次走国内镜像）、
`CMD ["streamlit","run","app.py","--server.port=8501","--server.address=0.0.0.0"]`；
`docker-compose.yml` 挂载 `./storage:/app/storage` 持久化向量库与账本，`restart: unless-stopped`。

### 6.4 关键配置项（config.py / .env）

| 配置 | 默认 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `zhipu` | zhipu/deepseek/qwen/openai/ollama |
| `LLM_MODEL` / `VL_MODEL` | glm-4-flash / glm-4v | 文本模型 / 多模态模型 |
| `LLM_TIMEOUT` | 90s | 单次调用超时 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 1200 / 150 | 切块字符数与重叠 |
| `DENSE_K` / `SPARSE_K` / `FINAL_K` | 12 / 12 / 6 | 召回与融合条数 |
| `DEDUP_THRESHOLD` | 0.90 | 语义去重阈值 |
| `AGENT_MAX_ITERS` | 8 | Agent 最大工具调用轮数 |
| `LLM_PROVIDERS_EXTRA` | 空 | JSON 追加自定义 Provider |

---

## 7. 创新点

1. **双栏按 x 坐标分栏**（`_sorted_blocks_by_column`）：以页面竖直中线判定左右栏，
   从文本提取根上解决双栏串读，而非仅在视觉层做坐标还原；视觉渲染（`native_page_html`）与
   文本提取（`build_chunks`）共用同一套分栏判定，保证「看到的版面 = 检索的上下文」一致。
2. **图谱驱动综述（三层分层）**：`generate_review` 真正消费图结构（cornerstone / 高中心性 / 边缘新兴三层），
   而非把摘要堆给 LLM；Future Work 结合引用可达性做规则预判。
3. **引用含金量纯规则打分**（`services/citation_quality.py`）：arXiv(+20) + DOI(+20) +
   年份新旧（近 3 年 +25 / 近 5 年 +15 / 5–10 年 +5）+ 顶会顶刊命中(+30)，输出 A/B/C 等级与可解释理由，
   函数签名预留无缝替换为 semantic_scholar/openalex API 的接口。
4. **统一 LLM 重试与伪响应兜底**（`core/llm.py`）：3 次指数退避 + 失败落盘 + 伪响应，
   保证 Agent 循环与 Streamlit 渲染不被单次网络抖动打断——这是演示稳定性的关键工程取舍。
5. **无幻觉 References 白名单**：生成后 `_sanitize_citations` 正则核对，越界角标直接剔除。
6. **安全默认**：Key 只经环境变量/会话注入、模型调用全走后端代理、Python 代码 AST 白名单沙箱。

---

## 8. 局限性与后续规划

诚实声明当前边界（均不影响主流程，但需在评审中说明）：

- **引用含金量为规则打分、未联网**：`citation_quality.py` 不抓真实 `citation_count` / h-index /
  impact factor，CCF 名单为内置子串表而非完整版；后续可在保持函数签名不变的前提下接 semantic_scholar/openalex；
- **规则打分非语义**：顶会命中靠字符串子串匹配，可能误判 venue 同名；年份与 DOI 均靠正则，格式异常时静默为 0；
- **Python 为「软沙箱」**：`core/sandbox.py` 是同一解释器子进程 + AST 白名单，非容器/命名空间隔离，
  超时 25s、输出截断，存在理论绕过面；生产化应换 Docker gVisor 或 Firecracker；
- **OCR 依赖多模态 API**：离线时扫描件以原生文本层功能为准，无本地 OCR 引擎（如 PaddleOCR）；
- **个性化推荐为领域计数加权**：`user_profile` 用 4 个硬编码领域 if-else 累计，非协同过滤；
- **长文档非流式入库**：`prepare_pdf` 一次性把全文与 chunks 驻内存，无断点续传/分批 embedding；
- **图谱回退静默**：PageRank/eigenvector 异常时退化为均匀分布，目前不向用户显式告警。

---

<!-- PDF 版本未生成：环境无 weasyprint，本地用 pandoc 或 Typora 导出即可 -->

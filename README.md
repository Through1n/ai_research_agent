# 🔬 AI4S 科研助手 Agent

> 智领科研 · 意图驱动 —— 基于大模型与 Agent 架构的全流程科研助手

![screenshot](docs/screenshots/main.png)

[在线演示（待部署）](https://example.com)

**一键运行**：

```bash
git clone <repo> && cd ai_research_agent
pip install -r requirements.txt
streamlit run app.py
```

全国高校 AI 大模型挑战赛参赛作品。系统深度融合大语言模型（LLM）的通用推理能力与 Agent 架构的自主交互能力，
从 0 到 1 构建覆盖**文献智能解析、抗幻觉问答、沉浸式对比翻译、自动检索追踪推荐、引用图谱分析与学术写作辅助**的
全流程「科研助手 Agent」。六大命题模块全部闭环，关键指标均以**官方赛题 PDF 数据集实测**背书。

---

## ✨ 核心特性（对应赛题六大模块）

| 模块 | 能力 | 关键技术 |
|---|---|---|
| ① PDF 管理与细粒度解析 | 单/批量上传、**内容级语义去重（不依赖文件名）**、版本差异总结、扫描件 OCR、标题树/公式/表格/参考文献抽取 | PyMuPDF、多模态 LLM、分段指纹 Embedding |
| ② 抗幻觉知识问答 | 单篇细粒度问答（超参数/硬件/BLEU）、多篇跨文献对比推理、原文出处锚点 | **跨语言混合检索（查询扩展+BM25Plus+向量+RRF）**、LLM 重排、内联【n】溯源 |
| ③ 版式阅读与双向翻译 | 双栏原生渲染、标题树导航、引用↔参考文献双向跳转、中英同步滚动、划词翻译（**后端代理，零密钥暴露**） | 坐标级版面还原、标题锚点定位、JS 比例+段落对齐联动 |
| ④ 检索追踪与推荐 | arXiv/PubMed/网页检索、自动下钻入库、引用含金量筛选、**订阅式增量动态追踪**、**多用户点赞/踩+画像个性化推荐** | arXiv API、Function Calling、last-seen 检查点、GitHub 复现加权 |
| ⑤ 引用图谱与综述 | 真实引用关系建图、PageRank/中心性挖掘基石/边缘、自动综述、Future Work 前沿过滤 | NetworkX、vis-network、**IDF 稀有度倒排候选+语义兜底**、时间线比对 |
| ⑥ 写作与可视化 Copilot | 无幻觉论文框架、Mermaid/TikZ/Graphviz/Matplotlib 拓扑图、CSV→六类图表+Figure Caption、中英学术翻译 | 真实文献约束生成、**AST 沙箱执行护栏**、Plotly |

**Agent 内核**：任务规划（Task Planning）、多专家路由（Multi-Agent Router）、Function Calling 闭环、
短期/长期记忆与用户画像、三层 Guardrails（输入注入检测 / 输出脱敏与角标校验 / 执行沙箱）、
全程思考过程（Trace）可视化与流式输出。

---

## ⚖️ 功能矩阵（vs 传统 RAG / 通用 Copilot）

| 能力 | 传统 RAG | 通用 Copilot | 本项目 |
|---|---|---|---|
| 双栏 PDF 解析 | ❌ | ❌ | ✅ 按 x 坐标分栏 |
| 抗幻觉 RAG | ⚠️ | ❌ | ✅ 角标白名单+自动修复环 |
| 引用含金量评估 | ❌ | ❌ | ✅ CCF 名单+arXiv/DOI/年份规则打分 |
| 引用图谱挖掘 | ❌ | ❌ | ✅ PageRank/介面中心性/时间线综述 |
| 多 Provider 切换 | ❌ | ⚠️ | ✅ 智谱/DeepSeek/通义/OpenAI/Ollama |
| 定时追踪新论文 | ❌ | ❌ | ✅ arxiv 增量检查工具 |
| GitHub 复现性评估 | ❌ | ❌ | ✅ star/license/pushed_at |
| 中英对照同步阅读 | ❌ | ⚠️ | ✅ 双栏 JS 同步滚动 |

---

## 📊 官方数据集实测数字（全部可一键复跑）

| 用例 | 结果 |
|---|---|
| v7→v1 语义去重（官方 PDF） | 分段指纹内容匹配 **98.0%**，附版本演进证据与建议 |
| v1 改名为 `attention_v1.pdf` 再入库 | **100%** 命中（证明不依赖文件名） |
| BERT 论文入库（非同篇） | 相似度 0.652 < 0.90，正确不误报 |
| 扫描件 1706.03762v1_img.pdf | 15 页零文本层自动判定 scanned，走 OCR 通道 |
| 正版《Understanding Deep Learning》541 页 | **1523 分块 / 26.3s 入库 / 约 292MB** |
| BERT→Attention 真实引用边 | 参考文献条目 100% 匹配证据，基石论文识别正确 |
| 图谱规模压测 | n=20 建图 **7.9s**，n=40 建图 **25.0s**（渲染 0.2s） |
| 中文查询英文文献 | 查询扩展生效：BM25 召回 7 条 / Dense 召回 11 条融合重排 |
| 质量保障 | 18 项单元测试 + 离线自测 + AppTest 9 页面全部通过 |

复跑命令：

```bash
python scripts/verify_official.py          # 官方 PDF 去重/扫描件/长文档/图谱实测
python scripts/bench_graph.py              # 20/40 篇图谱规模压测
python scripts/selftest.py                 # 离线端到端自检（无需 Key）
python -m unittest discover -s tests -v    # 单元测试
python scripts/apptest_check.py            # Streamlit 全页面渲染检查
python scripts/watch_daemon.py 30          # （可选）arXiv 定期追踪守护进程
```

---

## 🏗️ 系统架构

```
用户自然语言意图
        │
 Streamlit 交互层（PDF阅读 / 图谱 / 对话 / 可视化 / Agent 统一入口）
        │
 Agent 内核：Guardrails → Task Planning → Multi-Agent Router → Function Calling 闭环
        │            └── Memory（短期对话 / 长期笔记 / 用户画像）｜ Trace（思考可视化）
        │
 工具/服务层：PDF解析·OCR │ 混合RAG │ arXiv·PubMed·Web │ 引用图谱·图论 │ 写作·绘图·沙箱执行
        │
 存储层：Chroma 向量库 │ BM25 索引 │ library.json 元数据 │ 追踪检查点 │ 点赞账本 │ 原始 PDF
```

---

## 🧰 技术栈

- **前端/可视化**：Streamlit（锁定 1.63.x 保证可复现）、Plotly、vis-network（本地离线）、KaTeX
- **Agent / RAG**：自研 Agent 内核（规划/路由/记忆/防护/沙箱）、ChromaDB、rank-bm25（BM25Plus）、RRF 融合、查询扩展
- **文档处理**：PyMuPDF、多模态 LLM（OCR / 图表 / 公式）
- **图谱**：NetworkX（PageRank、入度、中介、特征向量中心性）、IDF 加权倒排候选
- **外部集成**：arXiv、PubMed、DuckDuckGo、GitHub 链接识别、订阅式增量追踪
- **大模型 Provider 抽象层**：一套 OpenAI 兼容协议接入多家模型，侧边栏随时切换（智谱 GLM / DeepSeek / 通义千问 / OpenAI / 本地 Ollama），无视觉能力的 Provider 自动降级；`LLM_PROVIDERS_EXTRA` 可追加内网自定义网关
- **工程**：Conda、pip、Docker、一键启动脚本、三层测试体系

---

## 🚀 快速开始

### 方式一：双击静默启动（Windows，推荐）

双击项目根目录的 **`启动科研助手.vbs`** —— **全程无 CMD 黑窗口**：服务在后台隐藏拉起，
就绪后自动打开浏览器 `http://localhost:8501`，点击侧边栏 **🚀 一键加载演示数据** 即可开始全流程体验。
（`启动科研助手.bat` 为终端备用方式，会显示运行日志窗口，便于排查问题。）

### 方式二：手动启动（Conda）

```bash
conda create -n ai_research python=3.11 -y
conda activate ai_research
cd ai_research_agent
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

复制 `.env.example` 为 `.env`，按需填一个 Provider 的 Key（也可直接在应用侧边栏下拉切换 + 填入）：

```env
LLM_PROVIDER=zhipu            # zhipu | deepseek | qwen | openai | ollama
ZHIPU_API_KEY=你的Key         # 智谱免费 Key：open.bigmodel.cn
# DEEPSEEK_API_KEY=xxx        # deepseek-chat（无多模态，OCR 自动降级）
# DASHSCOPE_API_KEY=xxx       # 通义千问 qwen-turbo + qwen-vl-plus ✅
# OPENAI_API_KEY=xxx          # gpt-4o-mini ✅
# LLM_PROVIDER=ollama         # 本地 qwen2.5:7b，完全离线、无需真实 Key
# —— 任意其他 OpenAI 兼容端点：侧边栏「➕ 自定义端点」直接填写 Base URL + 模型名即可接入
#   （vLLM / LM Studio / 硅基流动 / 校内网关……；也可用 JSON 注册：）
# LLM_PROVIDERS_EXTRA=[{"id":"mycloud","label":"公司内网","base_url":"http://10.0.0.8/v1","model":"qwen2.5-14b","multimodal":false}]
```

> ⚠️ 安全设计：API Key 仅经环境变量 / 侧边栏会话注入，**绝不**写入代码、前端页面或公开仓库；
> 划词翻译等所有模型调用均走后端代理，浏览器控制台可见内容无任何凭据。
> 无 Key 时，PDF 原生解析、混合检索、引用图谱、去重、追踪增量比对等离线链路仍可运行与自测。

---

## 🧪 测试数据集

| 用例 | 文件 | 预期 |
|---|---|---|
| 内容去重 / 版本演进 | `demo_data/1706.03762v7.pdf`、`pdf/1706.03762v1.pdf` | 语义（非文件名）检测 v1/v7，建议保留最新版 |
| 标准矢量 PDF | `pdf/1706.03762v1.pdf` | 双栏文本、标题树、公式、表格精细解析 |
| 图像扫描版 | `pdf/1706.03762v1_img.pdf` | 检测为扫描件，多模态 OCR 还原 |
| 超长篇文档 | `pdf/UnderstandingDeepLearning_02_09_26_C.pdf`（官方正版 541 页） | 1523 分块 26.3s 入库，内存约 292MB |
| 多篇推理 / 图谱 | `demo_data/1810.04805v2.pdf`（BERT） | BERT→Transformer 引用边，Transformer 为基石 |

> 注：`pdf/LongBook_540p_fixture.pdf` 为本地生成的补充压测夹具，非官方教材。

---

## 📁 目录结构

```
ai_research_agent/
├── app.py                     # Streamlit 入口（统一错误边界）
├── config.py                  # 路径 / 模型 / 检索参数配置
├── 启动科研助手.bat
├── requirements.txt / Dockerfile / docker-compose.yml / .env.example / LICENSE
├── core/                      # Agent 内核：llm / agent / planner / router / memory / guardrails / sandbox / trace
├── services/                  # 业务服务：parsing / library / rag / query_expansion / graph / track / writing / translate / extern / state
├── tools/                     # 工具注册中心 + 16 个 Function Schema 工具
├── ui/                        # 9 个页面 + 公共组件（错误边界 / 空库守卫 / HTML 兼容层）
├── tests/                     # 单元测试（18 项）
├── scripts/                   # selftest / apptest_check / verify_official / bench_graph / watch_daemon / setup_real_library
├── pdf/ 与 demo_data/         # 官方赛题测试 PDF 与演示数据
├── docs/                      # 技术文档（PDF）、演示视频脚本、界面截图
├── lib/                       # 本地离线 vis-network 等前端库
├── version_history/           # v1 单体原型历史存档
└── storage/                   # 运行时：Chroma、library.json、uploads、点赞与追踪账本（git 忽略）
```

---

## 🎬 演示与文档

- 技术文档：`docs/技术文档.pdf`（21 页，全部实测数据可复跑）
- 演示视频脚本：`docs/演示视频脚本.md`（5–8 分钟逐段台词 + 录制检查清单）
- 界面截图：`docs/shots/`
- 在线演示：部署后填入评审地址（Docker 一键部署，见技术文档第 6 章）

---

## 🎯 赛题要求对照表（P0 五项 + P1 关键项）

> 对照 `docs/audit_report_20261010.md` 审计清单，逐条标注实现位置与修复状态。

| 赛题要求 | 实现位置 | 状态 |
|---|---|---|
| 双栏 PDF 解析（P0-3） | services/parsing.py `_sorted_blocks_by_column` | ✅ 已修复 |
| 出处锚点可点击（P0-1） | ui/common.py `source_card` + ui/page_reading.py query_params | ✅ 已修复 |
| 幻觉防护自动修复（P0-2） | ui/page_qa.py 调 services/rag.py `answer()` | ✅ 已修复 |
| 无幻觉 References（P0-4） | services/writing.py `_sanitize_citations` + `_pdf_reference_entries` | ✅ 已修复 |
| 引用含金量（P0-5） | services/citation_quality.py + tools/definitions.py `t_citation_quality` | ✅ 已修复 |
| 图谱驱动综述（P1-7） | services/graph.py `generate_review` 三层分层 | ✅ 已修复 |
| Future Work 自动排除（P1-15） | services/graph.py `explore_future` 规则预判 | ✅ 已修复 |
| 定时追踪工具化（P1-9） | tools/definitions.py `t_track_arxiv` | ✅ 已修复 |
| GitHub 复现性（P1-14） | tools/definitions.py `t_github_search` | ✅ 已修复 |

---

## 🖥️ 桌面版（Windows 一键运行）

无需安装 Python，双击 `AI4SResearchAgent.exe` 即在原生窗口（WebView2 / Edge Chromium）中运行。

- **数据目录**：`%APPDATA%\AI4SResearchAgent\`（含 `app/` 业务代码、`storage/` 文献库、`logs/` 日志）
- **热更新**：业务代码（`app.py`、`ui/`、`services/` 等）通过 GitHub Release 静默下发，下次启动生效；壳层 exe 不参与热更新
- **打包**：在项目根目录执行 `build_desktop.bat`（产物 `dist\AI4SResearchAgent\`）
- **发版**：`python scripts/release.py --version 1.0.1 --notes "修复说明"`，自动打 tag 并（如装了 gh CLI）创建 Release
- **详细文档**：[`docs/desktop_guide.md`](docs/desktop_guide.md)（系统要求、首次启动、回滚、常见问题）

---

## 👥 团队信息

- 参赛队伍：__________
- 组别：__________
- 成员：__________
- 指导教师：__________

---

## 🤝 AI 协同声明与局限

- 本项目允许并使用 AI 辅助编程（符合赛规）：核心架构（Agent 内核、检索管线、图谱算法、Guardrails）
  与关键取舍由参赛成员设计与掌控，AI 协同贡献与 Prompt 策略详见技术文档第 4 章。
- 诚实声明的当前局限（详见技术文档 8.1）：异形排版的参考文献定位仍需启发式兜底；个性化推荐基于领域计数
  而非协同过滤；OCR 依赖多模态 API，离线以原生文本层功能为准。

## 📄 License

MIT（见 `LICENSE`）

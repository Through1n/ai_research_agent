# MoreHand

> 意图驱动 · 抗幻觉 · 全流程 —— 基于大模型 Agent 架构的学术科研助手

![主界面 · 文献库管理（语义查重 / 版本管理 / 细粒度解析）](资料/截图/p0.png)

**在线演示**：**https://ai4s-research-agent.app.workbuddy.host/**
- 系统为**开放式访问，无需注册登录**（代码中无任何登录鉴权逻辑）；该地址已部署，WebSocket 跨源与冷启动两项线上访问问题已定位并修复，**重部署后需复验打开效果**。
- **无需自备 API Key**：打开后点侧边栏 `一键加载演示数据（6 篇）`，即可体验文献解析 / 语义去重 / 版本管理 / 引用图谱等全部离线链路。
- 若想体验问答、翻译、写作等 LLM 功能，可在侧边栏「接入方式 → 🏢 预设平台」自选平台并填入自己的 Key（仅存于会话内存，不落盘、不回传）。
- ⚠️ 该地址由应用发布服务托管，**未注入 API Key，默认即为离线演示态**；线上部署已将 embedding 模型随包预置（chroma 的跳过判据是 6 个文件齐全），正常情况下不发生下载。**本地自行运行**时若未预置模型，首次点「加载演示数据」会尝试从模型源站下载（该源站境内网络可能传输挂死；代码层已改为后台预热 + 有界等待 + 人话降级，不会堵死首帧）。如需 streamlit.io 官方域名，可按 `资料/部署操作手册.md` 五步另行部署。

---

## 🚀 一键运行（3 行命令）

> 三条安装运行路径任选其一：**conda 本地运行**（推荐开发/演示）、**Docker**（推荐交付/复现）、**在线访问**（评审打开即可）。

### 路径一：conda 本地运行（Windows / Linux / macOS）

```bash
git clone <repo-url> ai_research_agent && cd ai_research_agent

# ① 建环境（环境名固定为 ai_research，与启动脚本一致）
conda create -n ai_research python=3.11 -y
conda activate ai_research

# ② 装依赖（13 个核心包，约 3–5 分钟）
pip install -r requirements.txt

# ③ 起服务
streamlit run app.py                   # 浏览器打开 http://localhost:8501
```

> Windows 也可双击根目录 **`启动科研助手.vbs`**（无黑窗口、自动开浏览器，内部执行
> `conda activate ai_research` + `streamlit run app.py`）。
> 打开后点击侧边栏 **一键加载演示数据**，无需 API Key 即可体验全部离线链路。
>
> **可选依赖**（不装也能跑，会自动走降级链路）：
> ```bash
> pip install matplotlib graphviz      # 拓扑图更好的渲染效果（未安装时降级为零依赖 DOT→SVG）
> pip install playwright && playwright install chromium   # 仅 scripts/resnap.py 截图脚本需要
> ```

### 路径二：Docker（最省事，环境 100% 可复现）

```bash
cp .env.example .env                   # 按需填入任一 Provider 的 API Key（也可留空跑离线链路）
docker compose up -d                   # 首次构建约 3–6 分钟
docker compose logs -f ai4s-agent      # 等到出现 "You can now view your Streamlit app"
# 访问 http://localhost:8501
```

### 路径三：在线访问（评审专用，无需安装）

- **主用地址**：**https://ai4s-research-agent.app.workbuddy.host/**（已部署，开放式访问；WebSocket 跨源与冷启动两项线上访问问题已修复，重部署后需复验）
- 备用地址（streamlit.io 官方域名）：按 `资料/部署操作手册.md` 五步部署后填入
- **无需注册登录，无需自备 API Key**：复验通过后正常访问；点侧边栏 `一键加载演示数据` 即可体验离线全链路。

---

## ⚖️ 功能矩阵（与主流方案对比）

| 能力 | ChatGPT 等通用对话 | 通用 RAG 问答 | Zotero + 插件 | **本系统** |
|---|---|---|---|---|
| 双栏 PDF 按栏正确切分入库 | ❌ OCR 串读 | ⚠️ 按文本流，顺序易乱 | ⚠️ 只存储不解析 | ✅ 4 条必要条件判栏（含横跨否决） |
| 答案强制原文页码溯源 + 可点击跳转 | ⚠️ 常编造页码 | ❌ 一般只有文档名 | ❌ | ✅ 【n】角标 + 页码徽章 + 跨页跳阅读页 |
| 拒答闸门 + 引用一致性校验 + 自动修复环 | ❌ | ❌ | ❌ | ✅ 相关度 <0.35 拒答，重叠率逐句校验，修复环上限 1 次 |
| 真实被引数 / 引用含金量评估 | ❌ | ❌ | ⚠️ Zotero 可查条目不评分 | ✅ OpenAlex→S2→Crossref 三级降级，宁缺毋假 |
| 引用图谱挖掘 + 图谱驱动综述 | ❌ | ❌ | ⚠️ 有引用条目无图挖掘 | ✅ PageRank/社区发现/frontier 五维打分 |
| arXiv/PubMed 检索 + 定时增量追踪 | ⚠️ 需手动粘贴 | ❌ | ⚠️ 手动抓取 | ✅ 22 个 Function Calling 工具 + last-seen 检查点 |
| 无幻觉 References 写作框架 | ❌ 高编造率 | ❌ | ⚠️ 仅文献管理 | ✅ PDF References 真实抽取白名单 + 生成后校验 |
| 中英对照同步阅读 + 划词翻译 | ⚠️ 需上传全文 | ❌ | ⚠️ 插件体验割裂 | ✅ 比例+段落对齐同步滚动、后端代理零密钥暴露 |
| 541 页长文档工程化入库 | ⚠️ 有长度限制 | ❌ 常超时 | ❌ | ✅ 25.32s / 1305 分块 / 峰值 RSS 893.1MB / 0 失败页（实测） |
| 无 Key 离线演示 / 私有化部署 | ❌ | ⚠️ | ⚠️ | ✅ 解析/检索/图谱/去重/追踪离线可用，Docker 一键部署 |

---

## 🤖 Agent 内核

任务规划（Task Planning）→ 多专家路由（6 专家 × 工具白名单）→ Function Calling 闭环（22 个注册工具）
→ 短期/长期记忆与用户画像 → 三层 Guardrails（输入注入检测 / 输出角标校验 / Python AST 沙箱）
→ 全程思考过程（Trace）可视化与流式输出。
大模型侧统一走 OpenAI 兼容协议：智谱 GLM / DeepSeek / 通义千问 / OpenAI / 本地 Ollama 随时切换，
LLM 调用 3 次退避重试（1.5/3.0/4.5s），失败落盘并返回友好伪响应，不中断演示。

---

## 🏗️ 系统架构

```
用户自然语言意图
        │
 Streamlit 交互层（9 个页面：Agent/文献库/问答/阅读/检索/图谱/写作/架构/创新）
        │
 Agent 内核（core/）：Guardrails → Task Planning → Multi-Agent Router → Function Calling 闭环
        │             └── Memory（短期对话/长期笔记/用户画像） ｜ Trace（思考可视化）
        │
 服务层（services/）：PDF解析·OCR ｜ 混合RAG ｜ arXiv·PubMed·Web ｜ 图谱·图论 ｜ 写作·绘图·沙箱
        │
 工具注册层（tools/）：22 个 Function Schema + Registry
        │
 存储层（storage/）：Chroma 向量库 ｜ BM25 索引 ｜ library.json ｜ 追踪检查点 ｜ 点赞账本 ｜ 原始 PDF
```

---

## 🎯 赛题要求对照表（5 大类，行号可在仓库中直接定位）

> ① 多模态与细粒度文档感知

| 赛题要求 | 实现位置 |
|---|---|
| 双栏 PDF 按栏正确切分（防串读） | `services/parsing.py:123 _detect_two_columns`（横跨否决/空白带采样/栏宽平衡，阈值 `:14-22`）；`services/parsing.py:220 extract_page_blocks_column_aware` |
| 扫描件 OCR（多模态） | `services/parsing.py:287 ocr_page_text`；整本 `:316 ocr_document` |
| 插图↔图注关联 | `services/parsing.py:616 extract_figures` + `:594 _nearest_caption`（水平重叠≥15% 取垂直最近） |
| 图表内容解读 | `services/parsing.py:674 describe_figures`（多模态 LLM） |
| 标题树 | `services/parsing.py:519 extract_toc`（无书签回退 `:457 infer_headings_from_text` 字号/编号推断） |
| 表格抽取 | `services/parsing.py:537 extract_tables` |
| 541 页长文档分批解析 / 单页超时 / 进度回调 | `services/library.py:253 prepare_pdf`、`:160 _extract_page_with_timeout`（30s/页）、常量 `:16-18`（PDF_BATCH_SIZE=20 等） |
| 损坏/截断/加密 PDF 拦截（防幽灵文献） | `services/library.py:206 _validate_prepared`、`:97 _empty_prepared`（带 error 键）、`:430 commit` 双保险 |
| 内容级语义去重（不依赖文件名） | `services/library.py:57 check_duplicate`（向量+分段指纹） |
| 版本管理 / 版本差异 | `services/library.py:527 group_versions`、`:543 version_diff` |
| 双栏原生渲染（阅读页） | `services/translate.py:60 detect_columns` + `:374 native_page_html`（分栏容器+限宽防串栏） |

> ② 抗幻觉 RAG

| 赛题要求 | 实现位置 |
|---|---|
| 混合检索（Dense+Sparse+RRF+LLM 重排） | `services/rag.py:225 retrieve`、`:97 rrf_fuse`、`:106 llm_rerank`；查询扩展 `services/query_expansion.py:134 expand_queries` |
| **拒答闸门**（相关度不足不作答） | `services/rag.py:411`（阈值 `RELEVANCE_FLOOR=0.35` 见 `:16`），拒答文案 `core/guardrails.py:31` |
| **引用一致性校验**（逐句比对来源） | `core/guardrails.py:83 check_citation_support`、`:51 _overlap`（覆盖率口径）、`:127 validate_citations` |
| **修复环**（校验失败自动重生成） | `services/rag.py:418-429`（`MAX_REPAIR=1` 见 `:17`，防死循环） |
| **References 白名单校验**（写作侧） | `services/writing.py:837 validate_reference_whitelist`（角标越界 + 条目实质匹配），生成主链路 `:956 generate_outline_verified` |
| 出处锚点可点击跳转 | `ui/page_qa.py:51 anchor_card`（页内锚点+跨页跳转）、`:80 _set_jump`；消费端 `ui/page_reading.py:454 _consume_qa_jump` |
| 段落级坐标 | `services/rag.py:132 annotate_paragraphs` |
| 多篇跨文献对比 | `services/rag.py:272 retrieve_per_paper` + `:328 build_grouped_context`；对比角标校验 `core/guardrails.py:152 validate_grouped_citations` |
| 提示注入检测 / 密钥脱敏 | `core/guardrails.py:108 check_injection`、`:117 redact_secrets` |

> ③ 自主工具调用

| 赛题要求 | 实现位置 |
|---|---|
| Function Calling 闭环（ReAct） | `core/agent.py:56 run`（工具白名单过滤 `:87-88`，`AGENT_MAX_ITERS=8` 见 `config.py:80`） |
| 多专家路由 | `core/router.py:4-23 EXPERTS`（6 专家 × 白名单）、`:44 route`、`:36 keyword_route` 离线兜底 |
| 22 个注册工具 | `tools/definitions.py:379 build_registry`（`r.register` 共 22 处，`:381-457`） |
| 定时追踪（增量检查点） | `services/track.py:57 track_topic` + `tools/definitions.py:382`（已接入 qa/search 专家白名单 `core/router.py:8` / `:12`） |
| arXiv/PubMed/Web/GitHub 检索 | `services/extern.py:25 search_arxiv`、`:61 search_pubmed`、`:84 web_search`、`:159 github_repo_info` |
| 引用含金量（真实被引，不编造） | `services/citation_quality.py:409 fetch_citation_count`（OpenAlex→S2→Crossref 三级降级 `:402`）、`:211 _pick_best`（被引数优先选优）、`:647 assess_reference`（四维打分） |
| Python 沙箱执行 | `core/sandbox.py:27 validate_code`（AST 白名单）+ `tools/definitions.py` `t_run_python`（输出安全截尾 `_tail_text`） |
| 长期记忆 / 用户画像 | `core/memory.py`；画像驱动推荐 `ui/page_search.py` |

> ④ 图谱挖掘

| 赛题要求 | 实现位置 |
|---|---|
| 真实引用关系建图 | `services/graph.py:161 build_graph`（References 定位 `:82 ref_section` + IDF 倒排候选 + 语义兜底 `:202 _semantic_cite`） |
| 图论指标 | `services/graph.py:220 analyze`（PageRank/入度/介数/特征向量，异常逐项回退） |
| 基石 / 边缘 / 前沿识别 | `services/graph.py:240 classify_papers`（基石）+ `:299 detect_frontier`（前沿五维打分，孤立噪声排除） |
| 图谱驱动自动综述 | `services/graph.py:510 review_structure`（社区发现+时间线+骨干边）→ `:644 generate_review`（LLM 不可用时确定性兜底 `:595`） |
| Future Work 可达性过滤 | `services/graph.py:774 _reachable_citers`（1/2 跳引用可达）、`:793 _future_status`（已解决/存疑/开放三态）、`:833 explore_future_structured` |
| 可视化 | `services/graph.py:430 graph_html`（vis-network 本地离线渲染） |
| 演示兜底（无真实引用边时） | `ui/page_graph.py:57 _build_demo`（页面明示"演示模式示意图谱"） |

> ⑤ 学术 Copilot

| 赛题要求 | 实现位置 |
|---|---|
| 无幻觉论文框架 | `services/writing.py:956 generate_outline_verified`；白名单来源三级回退 `:726 reference_whitelist`（PDF References 真实抽取 `:576 extract_references_with_quality`，质量自检 `:546 _quality_report`） |
| Mermaid/TikZ/Graphviz/Matplotlib 拓扑图 | `services/writing.py:1004 diagram`；TikZ 真实编译 `:1080 try_compile_tikz`（无编译器优雅降级）；Graphviz 三级降级渲染 `:1178 render_dot`（零依赖 DOT→SVG 兜底 `services/wcopilot_dotrender.py`） |
| CSV 自动出图（六类） | `services/writing.py:1298 infer_chart_plan`（列类型推断+推荐依据） |
| Figure Caption 注入真实数值事实 | `services/writing.py:1405 chart_facts`（实测 min/max/趋势/相关系数注入 prompt）+ `:1493 figure_caption`（生成后 `:1395 caption_has_numbers` 校验） |
| 中英学术互译 / 对照同步阅读 | `services/writing.py:1525 translate_to_english`；`services/translate.py:465 bilingual_html`（比例+段落对齐双机制同步滚动） |
| 划词翻译（后端代理零密钥暴露） | `services/translate.py:249 SELECTION_JS`（捕获即清空+剪贴板+hash 去重）、`:734 selectable_text_html` |
| 中文原位 PDF 输出（不乱码） | `services/translate.py:634 in_place_pdf`（`china-s` 字体 + 字号递降/截断省略号/逐行三级回退） |

---

## 📊 关键实测数字（真实环境复跑）

| 用例 | 实测结果 |
|---|---|
| 541 页《Understanding Deep Learning》入库 | **25.32s（46.8ms/页）/ 1305 分块 / 峰值 RSS 893.1MB / 0 失败页 / 进度回调 11 次** |
| 单页超时跳过（注入 2 页挂死） | 总耗时 ≈ 基线 + 2×30s，`failed_pages` 精确记录 2 页，其余 539 页正常 |
| 损坏/截断/加密 PDF | 24/24 用例全部拦截入库（`_validate_prepared`），0 幽灵文献 |
| 双栏判定（单栏语料 1111 页） | 误判率修复前 19.1% → **修复后 0%**；真双栏 BERT 正文页 10/10 正确识别 |
| 抗幻觉闭环 | 无关问题拒答（0 次 LLM 调用）；编造引用被逐句标记"重叠仅 x%"；修复环恰好 2 次 LLM 调用 |
| 真实被引数 | AlexNet 108512 / Attention 26917 / BERT 33959 / ResNet 227948 / Adam 82571（与 OpenAlex 一致） |
| 参考文献抽取 | BERT 修复前 9 条（字段错位+附录噪声）→ 修复后 **56 条全对** |
| LLM 重试 | 恒定 ≤4 次 HTTP 调用，退避实测 [1.5, 3.0, 4.5]s，失败落盘 `storage/llm_failures.log` |
| 演示路径 | AppTest 复跑 **7/7 条 0 未捕获异常**，全部有中文友好提示 |

复跑命令：

```bash
python scripts/verify_official.py          # 官方 PDF 去重/扫描件/长文档/图谱实测
python scripts/bench_graph.py              # 20/40 篇图谱规模压测
python scripts/selftest.py                 # 离线端到端自检（无需 Key）
python -m unittest discover -s tests -v    # 238 项单元测试
python scripts/apptest_check.py            # Streamlit 全页面渲染检查
```

---

## 🧰 技术栈

- **交互**：Streamlit 1.63.x（锁定版本保证可复现）、Plotly、vis-network（本地离线）
- **RAG**：ChromaDB + rank-bm25（BM25Plus）+ RRF 融合 + 查询扩展 + 可选 LLM 重排
- **文档**：PyMuPDF（双栏几何判定/图注关联/标题树/表格/参考文献）、多模态 LLM OCR
- **图谱**：NetworkX（PageRank、介数/特征向量中心性、社区发现）
- **外部**：arXiv、PubMed、DuckDuckGo、OpenAlex/Semantic Scholar/Crossref、GitHub API
- **Provider**：智谱 GLM / DeepSeek / 通义千问 / OpenAI / 本地 Ollama（OpenAI 兼容协议一键切换）
- **工程**：Conda、Docker、三层测试体系、统一 LLM 重试与失败落盘

---

## 🔑 配置与环境变量

复制根目录 `.env.example` 为 `.env`，填一个 Provider 的 Key 即可；也可在侧边栏实时切换并手填。

> 本模板已按代码中实际读取位置逐个核对，**共覆盖 23 个环境变量**（`config.py` 14 个、
> `core/providers.py` 2 个、`core/llm.py` 1 个、`services/state.py` 1 个、
> `services/extern.py` 2 个、`HF_ENDPOINT` 1 个、`AI4S_DESKTOP` 1 个；另 `APPDATA` 为系统自带无需配置）。
> 每个变量均带注释说明用途与读取位置。

最简配置：

```env
LLM_PROVIDER=zhipu            # zhipu | deepseek | qwen | openai | ollama
ZHIPU_API_KEY=你的Key         # 智谱免费 Key：open.bigmodel.cn
```

Provider 名与环境变量名的对应（`core/providers.py` 的 `key_env` 字段，**填错变量名是最常见的"页面说没 Key"的原因**）：

| 侧边栏平台 | `LLM_PROVIDER` | 读哪个变量 |
|---|---|---|
| 智谱 GLM（默认，唯一带视觉模型 `glm-4v`） | `zhipu` | `ZHIPU_API_KEY` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| 通义千问（DashScope 兼容模式） | `qwen` | `DASHSCOPE_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| 本地 Ollama（免密钥，先 `ollama serve` + `ollama pull qwen2.5:7b`） | `ollama` | 无需真实 Key |

常用可调项（`config.py:74-80`）：`CHUNK_SIZE`(1200)、`CHUNK_OVERLAP`(150)、`DENSE_K`(12)、
`SPARSE_K`(12)、`FINAL_K`(6)、`DEDUP_THRESHOLD`(0.90)、`AGENT_MAX_ITERS`(8)。

> ⚠️ 安全设计（赛题要求「API 密钥不得出现在公开仓库」）：API Key 只经环境变量 / 侧边栏会话注入，
> 不写入代码与前端；`.env` 已在 `.gitignore` 与 `.dockerignore` 中排除，不会进入仓库与镜像；
> 所有模型调用走后端代理。无 Key 时离线链路（解析/检索/图谱/去重/追踪）全部可用。
> 公网部署的安全加固与评审交付清单见 `资料/在线演示部署指南.md`。

## 🧰 依赖清单

`requirements.txt`（13 个核心包，均为硬依赖）：

| 包 | 用途 | 主要引用位置 |
|---|---|---|
| `streamlit>=1.63,<1.64` | 交互层（锁定版本保证可复现） | `app.py`、`ui/` |
| `PyMuPDF>=1.24.3` | PDF 几何解析（双栏判栏 / 图注 / 表格 / 标题树） | `services/parsing.py` |
| `chromadb>=0.5` | 向量库 | `core/`、`storage/chroma` |
| `openai>=1.40` | OpenAI 兼容协议的统一客户端 | `core/llm.py` |
| `pandas>=2.0` / `numpy>=1.26` | 表格与数值计算 | `services/writing.py`、`ui/` |
| `plotly>=5.20` | 六类学术图表渲染 | `services/writing.py`、`ui/page_writing.py` |
| `networkx>=3.2` | 图论指标（PageRank / 中心性 / 社区发现） | `services/graph.py` |
| `arxiv>=2.1` | arXiv 检索 | `services/extern.py`、`tools/definitions.py` |
| `pillow>=10` | 图像抽取与处理 | `services/parsing.py` |
| `python-dotenv>=1.0` | 加载 `.env` | `config.py` |
| `rank-bm25>=0.2.2` | BM25Plus 稀疏检索 | `services/rag.py` |
| `psutil>=5.9` | 内存监控与压测 | `ui/common.py`、`scripts/` |

**可选依赖（未安装时自动降级，不影响主流程）**：

| 包 | 影响的功能 | 未安装时的降级行为 |
|---|---|---|
| `matplotlib` | Graphviz 拓扑图渲染第 2 级 | 降级为零依赖 DOT→SVG（`services/wcopilot_dotrender.py`） |
| `graphviz` + `dot` 可执行文件 | Graphviz 渲染第 1 级 | 同上，两级降级 |
| `playwright` | 仅 `scripts/resnap.py` 批量截图脚本 | 该脚本不可用，其余功能不受影响 |
| `pywebview`（`webview`） | 仅桌面壳 `desktop/window.py` | Web 模式完全不受影响 |

> ⚠️ **已知待办（本次未修改 `requirements.txt`）**：上方 2 个可选包目前不在 `requirements.txt` 中。
> 如需最佳拓扑图渲染效果，请在执行 `pip install -r requirements.txt` 后追加
> `pip install matplotlib graphviz`。

**桌面版（可选，打包成独立 exe）**：构建期依赖与业务依赖分开放，见 `requirements-desktop.txt`
（`pywebview` / `PyInstaller` / `pythonnet`）——只装 `requirements.txt` 是打不了包的。

```bash
pip install -r requirements.txt -r requirements-desktop.txt
build_desktop.bat          # 双击即可；产物在 dist\AI4SResearchAgent\AI4SResearchAgent.exe
```

产物约 40 MB（exe 本体）/ 504 MB（整包 3766 个文件），目标机器**无需安装 Python**，双击即用。

---

## 🧪 测试数据

| 用例 | 文件 | 预期 |
|---|---|---|
| 版本去重 / 版本演进 | `演示语料/1706.03762v7.pdf` + `论文样本/1706.03762v1.pdf` | 语义（非文件名）命中，建议保留最新版 |
| 真双栏 PDF | `演示语料/1810.04805v2.pdf`（BERT） | 正文页正确判双栏；BERT→Transformer 引用边 |
| 图像扫描版 | `论文样本/1706.03762v1_img.pdf` | 判定扫描件，走多模态 OCR |
| 超长文档 | `论文样本/UnderstandingDeepLearning_02_09_26_C.pdf`（541 页，本地测试文件·版权原因不随仓库分发） | 25.32s / 1305 分块 / 峰值 RSS 893.1MB |
| 无 References 书籍 | `论文样本/LongBook_540p_fixture.pdf` | 质量自检返回空列表，不拿索引页凑数 |

---

## 📁 目录结构

```
ai_research_agent/
├── app.py                     # Streamlit 入口（依赖导入兜底 + 14 个全局 state 初始化 + flash 提示）
├── config.py                  # 路径 / 模型 / 检索参数（环境变量可覆盖）
├── core/                      # Agent 内核：llm / agent / planner / router / memory / guardrails / sandbox / trace / providers
├── services/                  # 业务服务：parsing / library / rag / query_expansion / graph / track / writing / translate / extern / citation_quality / state / wcopilot_dotrender
├── tools/                     # 注册中心 + 22 个 Function Schema 工具
├── ui/                        # 9 个页面 + 公共组件（错误边界 / 空库守卫 / flash / 锚点卡片）
├── tests/                     # 单元测试（238 项）
├── scripts/                   # verify_official / bench_graph / selftest / apptest_check / resnap(截图) / watch_daemon
├── 论文样本/ 与 演示语料/     # 赛题测试 PDF 与演示数据
├── 资料/                      # 技术文档（md/html/pdf）、评审报告（复盘/）、界面截图（截图/）
├── 品牌资源/                  # 品牌徽标生成器与图标（logo / icon / make_*.py）
├── lib/                       # 本地离线 vis-network 等前端库
├── version_history/           # v1 单体原型历史存档
└── storage/                   # 运行时数据（git 忽略）
```

> ⚠️ **版权说明（赛题测试 PDF）**：`论文样本/` 下的测试 PDF **不随本公开仓库分发**（已在 `.gitignore` 中以 `论文样本/*.pdf` 排除）。其中 `UnderstandingDeepLearning_02_09_26_C.pdf`（541 页长文档压测用例）是《Understanding Deep Learning》官方免费教材（作者 Simon J.D. Prince 授权官网免费阅读，**但不允许第三方仓库再分发**）。评委如需复现该用例，请自行从官网 **https://udlbook.github.io/** 下载后放入 `论文样本/`。其余 8 件测试 PDF（6 件 arXiv 公开预印本 + 2 件本地生成夹具）可通过 `sh scripts/fetch_pdfs.sh` 一键现拉现用；`演示语料/` 下随仓库提交的 2 件（`1706.03762v7.pdf`、`1810.04805v2.pdf`）均为 arXiv 公开预印本（允许再分发），无版权风险。

---

## 🎬 演示与文档

| 材料 | 路径 | 说明 |
|---|---|---|
| 技术文档 | `资料/技术文档.pdf`（源：`资料/technical_doc.md` / `资料/技术文档.html`） | 30 页，赛题要求 PDF ≤30 页 |
| **演示视频脚本** | `资料/演示视频脚本.md` | 8 分 00 秒逐镜脚本，含分镜表、录制参数、12 条翻车预案 |
| **现场演示彩排脚本** | `资料/演示彩排脚本.md` | 7 分 30 秒真人现场操作口径（一镜到底、每个按钮当场点得到） |
| **预指导环节介绍** | `资料/预指导环节介绍.md` | 预指导环节的说明底稿：目的 / 适用范围 / 核心要点 / 操作步骤（含证据与边界） |
| **预指导视频脚本** | `资料/预指导视频脚本.md` | 3 分 30 秒预指导录屏脚本：7 个分镜（场景 / 画面提示 / 旁白 / 时长），含红线与翻车预案；顶部有**一页速查卡**，可单独打印后照着录 |
| **录制工程化脚本** | `资料/录制工程化脚本.md` | 秒级时间轴 + 鼠标坐标 + 重录判据（必重录 / 可容忍） |
| **在线部署指南** | `资料/在线演示部署指南.md` | Docker / 云服务器 / Streamlit Cloud 三条路径 + 安全配置 + 排查表 |
| **部署操作手册** | `资料/部署操作手册.md` | Streamlit Cloud 五步上手 + R1–R6 报错速查 |
| **一键复现包** | `start.bat` / `start.sh` / `Dockerfile.quick` | 评委本机跑起来：自动建 conda 环境、装依赖、起服务、开浏览器 |
| **5 分钟体验指南** | `资料/5分钟快速体验指南.md` | 评委专用：从打开材料到看到第一个回答 ≤5 分钟 |
| **评委 FAQ** | `资料/评委FAQ.md` | 22 条评委提问，每条答案附 `文件:行号` 依据 |
| **答辩问答手册** | `资料/答辩问答手册.md` + `资料/决赛答辩问答手册.md` | 10 题 + 5 刁难 / 20 问标准答案，含代码行号佐证 |
| **团队信息** | `资料/团队信息.md` | 队伍 / 成员分工矩阵 / 指导教师 / 项目时间线 |
| 界面截图 | `资料/截图/` | `p0`=文献库、`p1`=Agent、`p2`=问答溯源、`p3`=阅读翻译、`p4`=图谱、`p5`=写作、`p6`=检索追踪；**文件名与页面顺序不一一对应** |
| 评审过程报告 | `资料/复盘/` | 各轮自检、评审意见与修复记录 |

> ⚠️ 截图目录是 **`资料/截图/`**（不是 `资料/screenshots/`）；该目录已在 `.gitignore` 中**显式保留**，
> 以保证推送到 GitHub 后文档图片不裂图。

---

## 👥 团队信息

<!-- 团队信息以省级赛事报名系统填报内容为准；本页与 资料/团队信息.md 仅作结构展示，不含个人隐私信息。-->

| 项目 | 内容 |
|---|---|
| **参赛队伍名称** | 队伍信息见省级赛事报名系统（以报名填报为准） |
| **组别** | 以报名系统填报组别为准（本科组 / 研究生组 / 高职组，须与报名表单一致） |
| **所属高校 / 院系** | 以报名系统填报为准 |
| **代码仓库** | `https://github.com/Through1n/ai_research_agent`（Public，已推送） |
| **在线演示地址** | **https://ai4s-research-agent.app.workbuddy.host/**（已部署，两项线上访问问题已修复，已重部署并线上复验通过；见上方「路径三：在线访问」） |

**成员与分工**（按七大代码模块：Agent 内核 / 文档感知 / 抗幻觉 RAG / 图谱挖掘 / 学术 Copilot / UI / 测试交付）：

| # | 姓名 | 学校 / 专业 | 组别 | 负责模块 | 主要交付物 |
|---|---|---|---|---|---|
| 1 | 队长（个人信息见报名系统） | 见报名系统 | 见报名系统 | Agent 内核 + 总体架构 | `core/`（agent / planner / router / memory / guardrails / sandbox / trace） |
| 2 | 见报名系统 | 见报名系统 | 见报名系统 | 多模态文档感知与长文档工程化 | `services/parsing.py`、`services/library.py` |
| 3 | 见报名系统 | 见报名系统 | 见报名系统 | 抗幻觉 RAG | `services/rag.py`、`query_expansion.py` |
| 4 | 见报名系统 | 见报名系统 | 见报名系统 | 引用图谱与综述 | `services/graph.py`、`citation_quality.py` |
| 5 | 见报名系统 | 见报名系统 | 见报名系统 | 学术 Copilot（写作 / 翻译 / 可视化） | `services/writing.py`、`translate.py` |
| 6 | 见报名系统 | 见报名系统 | 见报名系统 | 前端交互与桌面壳 | `ui/`（9 页）、`app.py`、`desktop/` |
| 7 | 见报名系统 | 见报名系统 | 见报名系统 | 测试、压测与交付材料 | `tests/`、`scripts/`、`资料/`、`Dockerfile` |

- **指导教师**：见报名系统（若实际无指导教师，则明确写"无指导教师"）
- **联系方式（队长）**：见报名系统（不在此公开个人隐私信息）
- 分工矩阵完整版、项目时间线、人员与代码目录对应关系见 **[`资料/团队信息.md`](资料/团队信息.md)**

> 注明：若实际人数与上述 7 个模块不一致，请自行合并或拆分对应行，保持"每个模块有且仅有一名主要负责人"。

---

## 🤝 AI 协同说明（赛题 3.4 条）

> 赛题规定：允许使用 AI 辅助编程，但须清晰说明 AI 的协同贡献与 Prompt 调优策略。以下为简要版，
> 完整说明见 `资料/技术文档.pdf` 第 8 章。

### 1. 使用方式声明

本项目在**开发过程中使用了大模型作为辅助编程工具**，用于代码草稿生成、重构建议、
文档润色与测试用例扩展。**所有生成代码均经人工逐行审阅、修改并在真实环境中验证后入库。**

### 2. AI 实际承担的工作

| 类别 | 具体占比 | 说明 |
|---|---|---|
| 样板 / 胶水代码、表单与错误提示文案 | 高（约 60–70%） | 9 个页面的 `st.empty/spinner/expander` 骨架、统一的中文友好错误提示（`ui/common.py:friendly_error`） |
| 单元测试与边界用例扩展 | 高（约 60%） | `tests/` 初始 18 项用例的草稿（现扩至 238 项），人工补充断言与真实数据 |
| 文档初稿（README / 技术文档 / 脚本） | 中（约 40–50%） | 结构与措辞建议，实测数字一律由人工实跑填入 |
| 算法与核心链路代码 | **低（< 20%）** | 仅用于局部实现建议与代码风格统一，核心逻辑由人工设计 |

### 3. 人工主导的架构决策与技术判断（非 AI 产出）

以下关键取舍由参赛成员独立设计、在真实环境反复验证后确定，**是本作品的核心贡献**：

1. **抗幻觉四道防线**：拒答闸门阈值 `RELEVANCE_FLOOR=0.35`（`services/rag.py:16`）、
   引用一致性重叠率口径、`MAX_REPAIR=1` 的修复环上限（`:17`，防死循环同时控成本）。
   阈值是跑完全部测试用例后调出来的，不是默认值。
2. **双栏判栏的四条件与"横跨否决"规则**（`services/parsing.py:123`）：修复前单栏语料误判率 19.1%，
   加入横跨否决后降到 **0%**（本 README「关键实测数字」一节可复跑）。这是从真实 PDF 几何统计中归纳出的判据。
3. **引用图谱的真实引用还原**：References 标题锚点 + IDF 加权倒排候选 + 语义兜底三级策略
   （`services/graph.py:82/161/202`），明确拒绝用"标题相似度"冒充真实引用关系；
   无真实边时宁可在界面标注"🎭 演示模式示意图谱"，也不糊弄。
4. **长文档工程化**：分批解析（`PDF_BATCH_SIZE=20`）、单页 30s 超时跳过并精确记录失败页、
   损坏/截断/加密 PDF 三重拦截（`services/library.py:206/253/430`）——目标是"坏页不拖垮整本书"。
5. **降级优先的设计哲学**：任何外部依赖（LLM / OpenAlex / GitHub / Graphviz / TikZ 编译器）失败时，
   必须有明确的中文降级提示与兜底路径，**绝不静默失败、绝不用编造数据掩盖失败**。
6. **全部实测数字的复跑验证**：本 README「关键实测数字」表中每一项都在指定环境下实跑得到，
   并提供 `scripts/verify_official.py` 一键复现。

### 4. Prompt 调优策略

| 策略 | 做法示例 |
|---|---|
| **约束前置** | 要求模型"不得编造任何文献、页码与数字；拿不到就说没有"，并把该约束写进系统提示而非后处理 |
| **给足上下文** | 提问时附上真实代码片段、报错堆栈、相关常量定义，而非只丢一句"帮我改 bug" |
| **角色与格式约定** | 指定"你是 Streamlit 工程专家，输出须含错误边界 / 中文友好提示 / 不新增依赖" |
| **小步迭代 + 立即验证** | 一次只让模型改一个函数，改完立刻跑对应单元测试与 AppTest，失败就把真实报错喂回去 |
| **反幻觉硬约束** | 涉及输出 References 的 Prompt 一律白名单化：只允许引用给定的真实条目清单 |
| **温度控制** | 生成 JSON / 结构化结果时 `temperature=0.0`，生成自然语言时 `0.1`（见 `core/llm.py`） |

### 5. 诚实声明的当前局限

- 引用含金量依赖 OpenAlex / S2 / Crossref 外部 API，被限流时会降级并明确标注"数据可能不准确"，**不编造数字**；
- Python 沙箱为「子进程 + AST 白名单」的软隔离，非容器/命名空间级强隔离；
- 中文纯句的引用一致性校验存在盲区（跨语言重叠率计算受限），无法判定时**如实提示而非谎报通过**；
- OCR 依赖多模态 API，离线环境以原生文本层为准，扫描件识别率受 Provider 视觉能力影响。

## 📄 License

MIT（见 `LICENSE`）

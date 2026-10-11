# 🔬 AI4S 科研助手 Agent

> 意图驱动 · 抗幻觉 · 全流程 —— 基于大模型 Agent 架构的学术科研助手

![主界面 · 文献库管理（语义查重 / 版本管理 / 细粒度解析）](docs/shots/p0.png)

<!-- TODO: 替换为真实演示链接 -->
**在线演示**：`【占位符】待部署后填入评审地址（Docker 一键部署命令见下方"快速开始 · Docker"，或技术文档第 6 章）`
提交前请把本行替换为 `https://你的演示域名` 形式的真实链接。

---

## 🚀 一键运行（3 行命令）

```bash
git clone <repo-url> ai_research_agent && cd ai_research_agent
pip install -r requirements.txt        # 已有 conda 可改为: conda activate ai_research
streamlit run app.py                   # 浏览器打开 http://localhost:8501
```

> Windows 也可双击根目录 **`启动科研助手.vbs`**（无黑窗口、自动开浏览器，内部执行
> `conda activate ai_research` + `streamlit run app.py`）。
> 打开后点击侧边栏 **🚀 一键加载演示数据**，无需 API Key 即可体验全部离线链路。

**Docker 方式**：

```bash
cp .env.example .env                   # 填入任一 Provider 的 API Key
docker compose up -d                   # 访问 http://localhost:8501
```

---

## ⚖️ 功能矩阵（与主流方案对比）

| 能力 | ChatGPT 等通用对话 | 通用 RAG 问答 | Zotero + 插件 | **本系统** |
|---|---|---|---|---|
| 双栏 PDF 按栏正确切分入库 | ❌ OCR 串读 | ⚠️ 按文本流，顺序易乱 | ⚠️ 只存储不解析 | ✅ 4 条必要条件判栏（含横跨否决） |
| 答案强制原文页码溯源 + 可点击跳转 | ⚠️ 常编造页码 | ❌ 一般只有文档名 | ❌ | ✅ 【n】角标 + 页码徽章 + 跨页跳阅读页 |
| 拒答闸门 + 引用一致性校验 + 自动修复环 | ❌ | ❌ | ❌ | ✅ 相关度 <0.35 拒答，重叠率逐句校验，修复环上限 1 次 |
| 真实被引数 / 引用含金量评估 | ❌ | ❌ | ⚠️ Zotero 可查条目不评分 | ✅ OpenAlex→S2→Crossref 三级降级，宁缺毋假 |
| 引用图谱挖掘 + 图谱驱动综述 | ❌ | ❌ | ⚠️ 有引用条目无图挖掘 | ✅ PageRank/社区发现/frontier 五维打分 |
| arXiv/PubMed 检索 + 定时增量追踪 | ⚠️ 需手动粘贴 | ❌ | ⚠️ 手动抓取 | ✅ 21 个 Function Calling 工具 + last-seen 检查点 |
| 无幻觉 References 写作框架 | ❌ 高编造率 | ❌ | ⚠️ 仅文献管理 | ✅ PDF References 真实抽取白名单 + 生成后校验 |
| 中英对照同步阅读 + 划词翻译 | ⚠️ 需上传全文 | ❌ | ⚠️ 插件体验割裂 | ✅ 比例+段落对齐同步滚动、后端代理零密钥暴露 |
| 541 页长文档工程化入库 | ⚠️ 有长度限制 | ❌ 常超时 | ❌ | ✅ 24.6s / 1301 分块 / 峰值 283MB / 0 失败页（实测） |
| 无 Key 离线演示 / 私有化部署 | ❌ | ⚠️ | ⚠️ | ✅ 解析/检索/图谱/去重/追踪离线可用，Docker 一键部署 |

---

## 🤖 Agent 内核

任务规划（Task Planning）→ 多专家路由（6 专家 × 工具白名单）→ Function Calling 闭环（21 个注册工具）
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
 工具注册层（tools/）：21 个 Function Schema + Registry
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
| 21 个注册工具 | `tools/definitions.py:355 build_registry`（`r.register` 共 21 处，`:357-430`） |
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
| 541 页《Understanding Deep Learning》入库 | **24.56s（45.4ms/页）/ 1301 分块 / 峰值 283MB / 0 失败页 / 进度回调 11 次** |
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
python -m unittest discover -s tests -v    # 18 项单元测试
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

## 🔑 配置

复制 `.env.example` 为 `.env`，填一个 Provider 的 Key 即可（也可在应用侧边栏直接切换 + 填入）：

```env
LLM_PROVIDER=zhipu            # zhipu | deepseek | qwen | openai | ollama
ZHIPU_API_KEY=你的Key         # 智谱免费 Key：open.bigmodel.cn
```

> ⚠️ 安全设计：API Key 只经环境变量 / 侧边栏会话注入，不写入代码与前端；
> `.env` 已在 `.gitignore` 与 `.dockerignore` 中排除，不会进入仓库与镜像；
> 所有模型调用走后端代理。无 Key 时离线链路（解析/检索/图谱/去重/追踪）全部可用。

---

## 🧪 测试数据

| 用例 | 文件 | 预期 |
|---|---|---|
| 版本去重 / 版本演进 | `demo_data/1706.03762v7.pdf` + `pdf/1706.03762v1.pdf` | 语义（非文件名）命中，建议保留最新版 |
| 真双栏 PDF | `demo_data/1810.04805v2.pdf`（BERT） | 正文页正确判双栏；BERT→Transformer 引用边 |
| 图像扫描版 | `pdf/1706.03762v1_img.pdf` | 判定扫描件，走多模态 OCR |
| 超长文档 | `pdf/UnderstandingDeepLearning_02_09_26_C.pdf`（541 页） | 24.6s / 1301 分块 / 峰值 283MB |
| 无 References 书籍 | `pdf/LongBook_540p_fixture.pdf` | 质量自检返回空列表，不拿索引页凑数 |

---

## 📁 目录结构

```
ai_research_agent/
├── app.py                     # Streamlit 入口（依赖导入兜底 + 14 个全局 state 初始化 + flash 提示）
├── config.py                  # 路径 / 模型 / 检索参数（环境变量可覆盖）
├── core/                      # Agent 内核：llm / agent / planner / router / memory / guardrails / sandbox / trace / providers
├── services/                  # 业务服务：parsing / library / rag / query_expansion / graph / track / writing / translate / extern / citation_quality / state / wcopilot_dotrender
├── tools/                     # 注册中心 + 21 个 Function Schema 工具
├── ui/                        # 9 个页面 + 公共组件（错误边界 / 空库守卫 / flash / 锚点卡片）
├── tests/                     # 单元测试（18 项）
├── scripts/                   # verify_official / bench_graph / selftest / apptest_check / resnap(截图) / watch_daemon
├── pdf/ 与 demo_data/         # 赛题测试 PDF 与演示数据
├── docs/                      # 技术文档（md/html/pdf）、评审报告、界面截图 docs/shots/
├── lib/                       # 本地离线 vis-network 等前端库
├── version_history/           # v1 单体原型历史存档
└── storage/                   # 运行时数据（git 忽略）
```

---

## 🎬 演示与文档

- 技术文档：`docs/技术文档.pdf`（同内容源文件 `docs/technical_doc.md` / `docs/技术文档.html`）
- 界面截图：`docs/shots/`（`p0`=文献库、`p1`=Agent、`p2`=问答溯源、`p3`=阅读翻译、`p4`=图谱、`p5`=写作、`p6`=检索追踪、`p7`=架构、`p8`=创新亮点；文件名与页面顺序**不**一一对应）
- 演示视频脚本：`docs/演示视频脚本.md`

---

## 👥 团队信息

<!-- TODO: 提交前填写 -->

- 参赛队伍：__________
- 组别：__________
- 队员与分工：
  - ______（文档感知与长文档模块）：__________
  - ______（抗幻觉 RAG 模块）：__________
  - ______（图谱挖掘模块）：__________
  - ______（学术 Copilot 模块）：__________
  - ______（UI 与演示路径）：__________
  - ______（性能 / LLM 统一 / 交付）：__________
- 指导教师：__________

---

## 🤝 AI 协同声明与局限

- 本项目使用 AI 辅助编程（符合赛规）：核心架构（Agent 内核、检索管线、图谱算法、Guardrails）
  与关键取舍由参赛成员设计与掌控。
- 诚实声明的当前局限（详见技术文档第 8 章）：引用含金量依赖 OpenAlex 等外部 API，被限流时降级并明确标注
  "数据可能不准确"，不编造数字；Python 沙箱为子进程 + AST 白名单的软隔离；中文纯句的引用一致性校验
  存在盲区（无法判定时如实提示而非谎报通过）；OCR 依赖多模态 API，离线以原生文本层为准。

## 📄 License

MIT（见 `LICENSE`）

# MoreHand · 赛题合规审计报告（2026-10-10）

> 审计方式：5 路并行子代理独立只读取证（A 文档感知 / B 抗幻觉 RAG / C 工具调用 / D 图谱挖掘 / E 学术 Copilot），Organizer 合并交叉比对。**未改任何业务代码、未启动 streamlit、未跑测试**。所有行号均来自 Grep/Read 真实定位。

---

## 一、总览仪表盘

| 指标 | 数值 |
|---|---|
| 审计能力点总数 | **33**（A9 + B5 + C9 + D4 + E6） |
| ✅ 已实现 | **13** |
| 🟡 部分实现 | **20** |
| ❌ 未实现 | **0** |
| 交叉矛盾（真矛盾） | **0** |
| 交叉重叠需澄清 | **5**（已在第四节列出） |

### 状态分布（按赛题 5 大类）

| 赛题大类 | 已实现 | 部分实现 | 未实现 | 完成度 |
|---|---|---|---|---|
| 1. 多模态与细粒度文档感知（A 路 9 项） | 4 | 5 | 0 | 44% |
| 2. 抗幻觉 RAG（B 路 5 项） | 0 | 5 | 0 | 0% |
| 3. 自主工具调用（C 路 9 项） | 6 | 3 | 0 | 67% |
| 4. 图谱挖掘（D 路 4 项） | 1 | 3 | 0 | 25% |
| 5. 学术 Copilot 输出（E 路 6 项） | 2 | 4 | 0 | 33% |

### 🔴 P0 项清单（5 项，直接戳赛题核心卖点）

| # | 能力点 | 致命伤 |
|---|---|---|
| P0-1 | B-#2 出处锚点 | 赛题明文"可点击跳转"，当前 source_card 是纯样式 div，无 `<a>`/`st.page_link`，QA 页不链接到 PDF 版式视图 |
| P0-2 | B-#4 幻觉防护 | validate_citations 只查角标数字范围、不校验内容；page_qa 内联重写问答流，**绕过** rag.answer 的修复环（issues 只显示不修复） |
| P0-3 | A-#2 双栏 PDF 文本提取 | _blocks 按 PyMuPDF 默认顺序拼接，双栏左右栏串读，直接污染 RAG chunks |
| P0-4 | E-#1 无幻觉 References | 仅 prompt 软约束，无生成后白名单正则校验；来源是库元数据而非从 PDF References 段抽取 |
| P0-5 | C-#9 引用含金量 | 全项目无 citation_count / semantic_scholar / openalex / CCF 字典，仅 LLM 主观判断"应付式引用" |

---

## 二、合并总表（按赛题 1-5 大类）

### 1. 多模态与细粒度文档感知（A 路）

| 赛题要求 | 状态 | 对应代码文件:行号 | 差距说明 |
|---|---|---|---|
| 1.1 扫描件 OCR | 已实现 | `services/parsing.py:16-18` detect_scanned；`:25-56` ocr_document；`services/library.py:91-100` 入库自动分支 | 走"整页 PNG → 多模态 LLM vision"路线，prompt 要求按阅读顺序、公式转 LaTeX、表格转 Markdown（parsing.py:43-44）。无本地离线 OCR 引擎、无版面分析。 |
| 1.2 双栏 PDF 解析 | 部分实现 | 检测 `ui/page_library.py:78-80`；坐标渲染 `services/translate.py:5-31` native_page_html | **只做视觉坐标还原，未做按 x 分栏的文本提取**。`parsing.py:59-70` _blocks 直接按 PyMuPDF blocks 顺序拼接，双栏会左右串读。已 grep `double column/two column/分栏/栏` 无切栏逻辑。 |
| 1.3 公式识别 | 部分实现 | `ui/page_library.py:128-131` LLM json_chat 抽 {name, latex}；`:141-144` st.latex；parsing.py:44 OCR prompt 要求公式转 LaTeX | 纯靠 LLM 从纯文本 chunks 猜公式，无 mathpix/pix2tex/LaTeX-OCR，无 PyMuPDF font/span 数学区域定位。 |
| 1.4 图表提取 | 部分实现 | `services/parsing.py:136-164` extract_figures；`:167-180` describe_figures（多模态 LLM 一句话描述） | 能抽图存 PNG，但**未做 Figure↔caption 关联**。fig["desc"] 是 LLM 重新生成的，不是原文 "Figure X: ..." 图注；grep `caption/图注/associate` 无 bbox 邻近关联。 |
| 1.5 层级树/目录 | 已实现 | `services/parsing.py:114-115` doc.get_toc()；`ui/page_library.py:99,112-116`；`ui/page_reading.py:121-126` | 只取 PDF 内置书签。无书签时直接显示"未检测到内置书签目录"，无按字号/字体/粗体推断正文标题的回退。 |
| 1.6 参考文献抽取 | 部分实现 | 区域锚定 `services/graph.py:26-35` _REF_HEAD；raw 截取 `services/parsing.py:187-188`（末尾 22000 字）；结构化 `ui/page_library.py:132-140` LLM 转 {id,authors,title,year,venue} | 区域锚定稳（References/Bibliography/Works cited/参考文献）。条目切分靠正则 `\[\d+\]\|\d{1,3}.`（graph.py:41），对 author-year、折行、arXiv 编号格式不强健；结构化完全依赖 LLM，无规则兜底。 |
| 1.7 语义去重 v1/v7 | 已实现 | `services/library.py:47-84` check_duplicate；`:17-19` _paper_embedding（前 4000 字向量）；`:22-30` _paper_fingerprints（k=5 段指纹）；`:37-44` _seg_sim | 三路判定：内容相似度≥0.90（config.py:39）、标题≥0.9+内容≥0.7、arXiv 号相同+内容≥0.7。不依赖文件名。局限：embedding 只取前 4000 字，尾部/新增章节权重低。 |
| 1.8 版本管理建议 | 已实现 | `services/library.py:206-212` group_versions；`:215-219` latest_of；`:222-235` version_diff（LLM 中文 Markdown 表）；UI `ui/page_library.py:53-61,160-171` | 入库查重命中给"覆盖旧版/保留两版/取消"三按钮；多版本分组提示"建议保留最新版"。局限：版本差异只取每版前 3500 字 chunks 喂 LLM（library.py:227），长版本对比会截断。 |
| 1.9 541 页长文档 | 部分实现 | 切块 `services/parsing.py:73-111` build_chunks_from_texts（CHUNK_SIZE=1200, OVERLAP=150）；OCR 进度 `parsing.py:54-55`；压测夹具 `scripts/make_longbook.py`（540 页）；实测 `scripts/verify_official.py:55-73`；内存 `ui/common.py:81-85` | 有分页 OCR 进度、切块、内存监控。**但无长文档工程化**：①prepare_pdf（library.py:87-114）一次性把全部 page_texts 装进 full_text 和 chunks，无流式/分页入库；②切块只按字符滑窗，不按章节/页边界语义切；③无断点续传、无分批 embedding；④541 页实测数字只在脚本里，UI 无长文档专项加载态。 |

### 2. 抗幻觉 RAG（B 路）

| 赛题要求 | 状态 | 对应代码文件:行号 | 差距说明 |
|---|---|---|---|
| 2.1 单篇精准问答 | 部分实现 | `services/rag.py:52` retrieve；`:70` build_context；`:77` qa_messages；`:85` answer；`ui/page_qa.py:33-50` | Q&A 闭环完整，但**无"锁定单篇"机制**：retrieve 直接对全库 ctx.chunks 检索（rag.py:55 无 source 过滤），page_qa 无论文选择器。"单篇"仅在库里恰好一篇时隐式成立。**page_qa 未调用 rag.answer()，在 :36-45 内联重写了一遍**。 |
| 2.2 原文出处锚点 | 部分实现 | chunk 字段 `services/library.py:150-151`（仅 id/source/page/page_end/text）；页码注入 `rag.py:73`；UI `ui/common.py:24-32` source_card；`ui/page_qa.py:57-59` | 页码**已真实回填 UI**（蓝色"第 N 页"徽章 + 编号【n】卡片）。但：①**不可点击跳转**——source_card 是纯样式 div（common.py:27-32），无 `<a href>`/st.page_link/switch_page；全项目唯一锚点跳转在 `services/translate.py:93-104` citation_jump_html（中英对照阅读页），与 QA 页无联动；②**无段落/节坐标**：parsing.py:82,97 分块只存 page/page_end。 |
| 2.3 多篇跨文献对比 | 部分实现 | 检索跨全库 `rag.py:55-64`；对比 prompt `rag.py:81`、`core/guardrails.py:65`；EX 按钮 `page_qa.py:9-10`；真多文工具 `tools/definitions.py:30-39` t_multi_paper，注册 `:172`，路由 `core/router.py:6,18` | QA 页上的"多篇对比"是**隐式拼答案**：①无多篇勾选 UI，靠 RRF 召回恰好落入不同论文的 chunk；②build_context（rag.py:70-74）把不同论文 chunk 按 [1][2][3] 混排编号，不按论文分组、不约束每篇至少几条；③**multi_paper 工具只挂在 Agent 路由（page_agent），QA 页根本没接**。 |
| 2.4 幻觉防护 | 部分实现 | 系统 prompt `core/guardrails.py:60-67` ACADEMIC_SYSTEM；引用校验 `guardrails.py:45-56` validate_citations（正则 `:23`）；自动修复 `rag.py:96-110`；注入检测 `guardrails.py:26` check_injection（仅 `core/agent.py:64` 调用） | 三道硬伤：①**事实校验缺失**——validate_citations 只做角标数字范围检查（1~n_sources），**完全不验证答案内容是否与 chunk 文本相符**；②拒答是 prompt 层，代码层无拒答闸门；③**自动修复环在主 UI 路径被绕过**——page_qa.py:36-48 内联流程只在 :46 调 validate_citations 后以 st.caption 展示 issues（:56），**不触发修复**。注入/密钥脱敏也不在 QA 路径。 |
| 2.5 查询扩展 | 部分实现 | `services/query_expansion.py:14-29` _GLOSSARY；`:32-40` _offline_expand；`:43-67` expand_queries（在线 LLM+内存缓存）；调用点 `rag.py:58-60`、sparse_search `:8-18` | ①**方向单一**：仅"中文提问→英文检索词"跨语言扩展，无同语言同义词、无 HyDE、无多视角（grep `hyde/multi_query/perspective` 零命中）；②prompt 要"4-8 个词"但被 replace("\n"," ") 拼成一整个字符串作单个 variant append（query_expansion.py:56-66），BM25 只是多一次打分取 max，不是多查询 RRF；③**扩展词只进稀疏路**：dense_search（rag.py:59）用原始中文查询，向量路完全不消费 variants。 |

### 3. 自主工具调用（C 路）

| 赛题要求 | 状态 | 对应代码文件:行号 | 差距说明 |
|---|---|---|---|
| 3.1 arXiv 检索 | 已实现 | `services/extern.py:15-28` search_arxiv（arxiv PyPI 库，retry=3, delay=2s）；`tools/definitions.py:43-46` t_arxiv，注册 `:175-178`；下载链路 `definitions.py:57-71` _ingest_arxiv_paper | 真联网，非 mock。PDF 下载+语义查重入库。被 search 专家路由放行（router.py:9）。 |
| 3.2 PubMed 检索 | 已实现 | `services/extern.py:51-71` search_pubmed（urllib 直连 NCBI E-utilities esearch+esummary）；`tools/definitions.py:49-50` t_pubmed，注册 `:179-182` | 真联网，无第三方包装、无降级假数据。**仅挂在 search 专家下（router.py:9），qa 专家工具列表（router.py:6-7）无 pubmed_search**，多轮对话 LLM 可能因 schema 不可见而不主动调用。 |
| 3.3 搜索引擎 | 已实现 | `services/extern.py:74-90` web_search（POST html.duckduckgo.com，正则解析 result__a，解 uddg= 跳转）；`tools/definitions.py:53-54` t_web，注册 `:183-185` | 真联网，无 API key 方案、无 mock。异常路径返回 [{"error":...}]，不回退假结果。 |
| 3.4 Python 沙箱执行 | 已实现 | `tools/definitions.py:135-150` t_run_python；`core/sandbox.py:27-51` validate_code（AST 白名单 numpy/pandas/sklearn 等，禁 eval/exec/open/subprocess/socket）；执行 `subprocess.run([sys.executable, fp], cwd=work, timeout=25)` | 真执行，独立 tempfile 目录。限制：超时 25s、输出截断 stdout 3000/stderr 1500；是同一解释器子进程，非容器/命名空间，仅白名单级"软沙箱"。 |
| 3.5 自动下钻 | 已实现 | `core/agent.py:56-139` run()，循环 `:96-130`；`config.py:40` AGENT_MAX_ITERS=8；ReAct 文本兜底 `:32-53` parse_action；路由 `core/router.py:4-21` | 真闭环：LLM chat(messages, tools=schemas) → 解析 tool_calls → reg.execute() → 结果回灌 messages → 继续下一轮直到无 tool_call 或 8 轮上限。planner（core/planner.py:15-30）只生成步骤字符串用于展示，不驱动真实下钻。 |
| 3.6 定时追踪 | 部分实现 | `services/track.py:24-45` diff_new/fetch_track（arxiv_id 检查点存 memory.json）；`scripts/watch_daemon.py:47-60` 常驻循环；UI 入口 `ui/page_search.py:141-163` | 增量检测真实。但：①**未在 tools/definitions.py 注册任何 track 工具**，agent 循环无法自主触发，只能 UI 手动点按钮或另起 watch_daemon.py 进程；②Streamlit 内无 st_autorefresh/定时回调，所谓"定时"完全依赖外部 daemon（默认 30 分钟轮询），与 Web 会话解耦；③无邮件/IM 推送，新论文只写日志+memory。 |
| 3.7 GitHub 复现性 | 部分实现 | `services/extern.py:9-12` detect_github（正则 https?://github.com/owner/repo）；调用点 extern.py:26、definitions.py:45,70、page_search.py:25-26,109,161 | 仅从 arXiv 摘要/标题抽链接：①不调 GitHub API，不读 README/star/最近提交/license；②无"按论文标题/主题主动找代码仓库"工具（registry 无 github_search）；③推荐页仅以 +0.15 加分（page_search.py:109），不做仓库健康度评估。 |
| 3.8 用户偏好推荐 | 已实现 | `core/memory.py:53-67` update_profile；调用点 `services/library.py:159`；推荐加权 `ui/page_search.py:92-138` _recommend；点赞反馈 `:80-89` | 真学习：入库时硬编码关键词分类（NLP/CV/生物医学/GNN）累加到 profile；推荐按 (a) 本地库 embedding 质心余弦 rel、(b) 画像关键词命中 interest、(c) GitHub 链接 gh=0.15、(d) 跨用户点赞净值 lk=0.20*likes 线性加权。局限：画像关键词是 4 个领域硬编码 if-else，无 LLM 自动抽兴趣；偏好未回灌到 agent 工具调用参数。 |
| 3.9 引用含金量评估 | 部分实现 | `ui/page_search.py:71-77` _quality（让 LLM 主观判断实质 vs 应付引用）；本地图指标 `services/graph.py:168-185` analyze（pagerank/indegree/betweenness） | ①**不抓真实 citation 数**——_quality 只是把参考文献列表喂 LLM 主观判断；②grep `citation_count/citations/impact/venue/h-index/google_scholar/semantic_scholar/openalex` 全项目无命中（仅 validate_citations 是角标越界校验）；③无期刊/会议等级字典（CCF/影响因子）；④graph.py 的 pagerank 基于本地 PDF 解析的引用网络，与外部被引次数无关。 |

### 4. 图谱挖掘（D 路）

| 赛题要求 | 状态 | 对应代码文件:行号 | 差距说明 |
|---|---|---|---|
| 4.1 PageRank 基石论文 | 已实现 | `services/graph.py:11` import networkx as nx；`:168-185` analyze() → `:173` nx.pagerank(alpha=0.85)、`:176` in_degree、`:178` betweenness_centrality、`:182` eigenvector_centrality_numpy；`:188-201` classify_papers 按 (indeg,pagerank) 倒序前 30% 为 cornerstone；UI `ui/page_graph.py:35-42`；工具 `tools/definitions.py:99-106` t_build_graph | **真 networkx 计算**，非 LLM 编造。局限：①"基石"= 被引+PR 混合，未融合发表年份/最早出现等"奠基性"维度；②graph.py:174-184 多处 except Exception 回退（pagerank 失败退 1/N、eigenvector 失败退用 PR），回退时不报错，可能静默降级。 |
| 4.2 边缘论文 boundary/frontier | 部分实现 | `services/graph.py:200` edge = [x for x in ranked if indeg[x]==0]；`:189` docstring"边缘衍生=入度低"；UI `ui/page_graph.py:46-48`；工具字段 `tools/definitions.py:106` edge_derivative | **仅判定"本地库中无人引用它"= 边缘**。grep `boundary/frontier/cross-disciplinary/interdisciplinary/新兴/潜力` 全项目仅命中 UI 文案与 prompt 字样，**无跨学科向量聚类、无发表时间衰减、无"低被引但高出度/近期高增长"frontier 评分模型**。本质是孤立节点（orphan），不是赛题要求的"跨学科/新方向/潜力论文"。 |
| 4.3 自动综述生成 | 部分实现 | `services/graph.py:247-256` generate_review(ctx, G, metrics, cornerstone)；`:248-250` 拼接所有 abstract 前 500 字；`:251` 仅 cornerstone 标题拼进 prompt；`:252-255` prompt 让 LLM 写 800 字中文综述；UI `ui/page_graph.py:56-59` | **函数签名带 G、metrics 但函数体内未使用**（仅用 cornerstone 标题）。综述质量=LLM 读摘要能力，**没有基于图谱结构的主题聚类、引用脉络、时间线分段、中心性加权综合**。属于"文献摘要 LLM 综述"，不是"图谱挖掘驱动的综述"。 |
| 4.4 Future Work 过滤 | 部分实现 | `services/graph.py:259-281` explore_future(ctx, G)；`:264` 正则 `(future work\|limitations\|conclusion\|展望).{0,1200}` 截 1200 字窗口；`:270-280` prompt 让 LLM 按"后续论文摘要"判断旧 idea 状态；UI `ui/page_graph.py:60-62`；工具 `tools/definitions.py:114-116` t_future | 抽取是真做的（正则定位 Future Work 段落）。但"自动排除已解决"完全交给 LLM 读摘要主观判断，**未利用 G 做引用可达性/直接回应关系分析**（如"某 future work 被后续 B 直接引用且 B 标题含该方向"才算解决）。函数签名带 G 但函数体未使用。抽取窗口兜底 text[-900:]（:265）在正则失配时会把正文结尾误当 Future Work。 |

### 5. 学术 Copilot 输出（E 路）

| 赛题要求 | 状态 | 对应代码文件:行号 | 差距说明 |
|---|---|---|---|
| 5.1 无幻觉 References | 部分实现 | `services/writing.py:5-11` _real_references；`:14-25` generate_outline；`services/parsing.py:187-188` extract_references_raw（仅在 library 展示用，**未被写作流程调用**）；`services/graph.py:26-43` ref_section（仅用于引用图谱，**未被写作流程复用**） | References 来源是**本地文献库元数据**（ctx.papers[n] 的 authors/title/year，见 services/state.py:51-52），而非从上传 PDF 的 References 段真实抽取。仅靠 prompt 注入"只能引用下列真实文献，禁止编造"做软约束（writing.py:16-18），**无任何生成后校验/正则核对 LLM 输出的 [n] 是否在白名单内**。文献库为空时占位 [待补充真实文献]，但 LLM 仍可能凭空造条目。 |
| 5.2 TikZ 图输出 | 部分实现 | `services/writing.py:31,36-41` diagram() 以 ```latex prompt 让 LLM 生成 TikZ 代码并剥 code fence；`ui/page_writing.py:81,87-88` st.code(code, language="latex") 仅展示代码块 | 只输出**纯文本 .tex TikZ 代码块**，前端无 LaTeX 编译器、无 PDF 预览、无 tectonic/xelatex 沙箱编译、无 KaTeX/MathJax 渲染。用户必须自行复制到 Overleaf 才能看到图。grep `tikz/latex/pdflatex/xelatex/tectonic` 在 ui/、services/、core/sandbox.py（白名单仅 numpy/pandas/matplotlib/plotly，无 latex）中确认无编译/渲染环节。 |
| 5.3 Graphviz / Mermaid | 部分实现 | Mermaid `ui/page_writing.py:89-93`（embed_html 内联 mermaid@10 CDN，真实渲染）；Graphviz `ui/page_writing.py:87-88`（st.code(code, language="dot") 仅代码块） | **Mermaid 真实渲染**（含 mermaid.initialize({startOnLoad:true})）。**Graphviz 只输出 DOT 源码**：前端无 viz.js、无 graphviz server、无 SVG 渲染、无沙箱 `dot -Tsvg`。grep `graphviz/digraph/viz-js/vizjs/dot -T` 在 lib/（仅 vis-network/tom-select）和 ui/common.py 中确认无 Graphviz 渲染器。 |
| 5.4 Matplotlib / CSV 可视化 | 部分实现 | `ui/page_writing.py:11-66` _csv_section（st.file_uploader → pd.read_csv → 6 种 plotly 图：line/bar/scatter/Scatterpolar/imshow/parallel_coordinates）；`services/writing.py:33,39-41` matplotlib 分支仅生成 Python 代码文本；`core/sandbox.py:13` matplotlib 在沙箱白名单但 diagram 流程未调用 run_python | ①实际出图用 **plotly**，不是 matplotlib；②"matplotlib"拓扑图分支（writing.py:33）只把 LLM 生成的 Python 代码塞 st.code，**不执行、不出图**——虽有 t_run_python 工具（tools/definitions.py:135）但写作页未将其与 diagram 输出串联；③"自动出图"不成立：用户必须手动从下拉框选 X/Y/图表类型（page_writing.py:30-32），无自动列类型推断、无自动推荐图表。 |
| 5.5 Figure Caption 自动生成 | 已实现 | `services/writing.py:44-51` figure_caption(ctx, df, chart_desc)（df.describe(include="all") 前 2500 字 + 图表类型 prompt LLM，2-3 句英文 "Figure X. It shows/illustrates..."）；`ui/page_writing.py:63-66` 按钮"🤖 自动提炼 Figure Caption" | 功能闭环：上传 CSV → 出图 → 点按钮 → 英文 caption。小差距：按钮触发而非出图后自动追加到图下；caption 仅基于 df.describe() 统计量，未传入图中实际数值点，但赛题"自动生成"已满足。 |
| 5.6 中译英 | 已实现 | `services/writing.py:54-67` translate_to_english/translate_to_chinese；`ui/page_writing.py:96-106` 中→英/英→中 radio；`services/translate.py:34-62` bilingual_html（双栏 JS 同步滚动+段落联动高亮）；`ui/page_reading.py:58-73` 整页逐段中英对齐；`ui/page_reading.py:102-117` 划词翻译；`ui/page_reading.py:76-85` 原位 PDF 底部叠加中文 | 三种形态均在：(1) 纯文本中英互译（写作页）；(2) 中英对照同步滚动（阅读页 tab2，JS scroll 联动+mouseenter 高亮）；(3) 划词翻译（阅读页 tab5）。另有原位 PDF 译文叠印。 |

---

## 三、交叉矛盾清单

**经 Organizer 逐文件核对 5 路证据，未发现真矛盾。** 以下 5 项是"同一文件被多路引用、看似冲突实则互补"的点，已逐项澄清：

| # | 重叠文件 | 两路结论 | 澄清 |
|---|---|---|---|
| X-1 | `services/writing.py` | D 路："writing.py 只做 outline/diagram/caption/翻译，**未参与**综述/future work"；E 路："writing.py:5-11 _real_references、:14-25 generate_outline、:31-41 diagram、:44-51 figure_caption、:54-67 translate" | **一致**。D 路说的"综述/future work 在 graph.py 内"与 E 路列的 writing.py 函数集完全不冲突——writing.py 不碰图谱挖掘。 |
| X-2 | `services/graph.py` | A 路："graph.py:26-35 ref_section 参考文献区域定位"；D 路："graph.py:168-201 analyze/classify_papers、:247-256 generate_review、:259-281 explore_future"；C 路："graph.py:168-185 analyze 作为本地图指标" | **一致**。ref_section 在文件前段（:26-35），analyze 在中段（:168+），generate_review/explore_future 在后段（:247+），三段不重叠。C 路引用的 analyze 与 D 路引用的 analyze 是同一函数，行号对齐。 |
| X-3 | `services/parsing.py` 的 chunk 字段 | A 路："build_chunks_from_texts:73-111，CHUNK_SIZE=1200"；B 路："parsing.py:82,97 分块只存 page/page_end，无 section/paragraph 坐标" | **互补**。A 路讲切块逻辑（字符滑窗），B 路讲 chunk 数据字段（page/page_end）。两者拼起来正好是"切块按字符滑窗、字段只到页级"的完整证据，共同支撑"出处锚点无段落坐标"的结论。 |
| X-4 | "Figure Caption" 一词 | A 路："extract_figures:136-164 抽 PDF 内嵌图，未做 Figure↔caption 关联"；E 路："figure_caption:44-51 给 CSV 图自动写 caption，已实现" | **不是同一件事，不能互相抵消**。A 路的 caption 是"PDF 里的图 ↔ 图注文本"关联（缺失）；E 路的 caption 是"CSV 上传后自动生成图表说明文字"（已实现）。审计时这是两个独立能力点，分别落在 1.4 和 5.5。 |
| X-5 | `services/translate.py` | A 路："translate.py:5-31 native_page_html 双栏坐标渲染"；B 路："translate.py:93-104 citation_jump_html 中英对照锚点跳转"；E 路："translate.py:34-62 bilingual_html 双栏 JS 同步滚动" | **三段不冲突**。native_page_html（:5-31）是 PDF 原生版式渲染；bilingual_html（:34-62）是中英对照同步滚动；citation_jump_html（:93-104）是中英对照页内的段落锚点跳转。B 路据此推断"QA 页无锚点跳转"是成立的——citation_jump_html 仅在中英对照阅读页内部使用，未与 page_qa 联动。 |

---

## 四、统一优先级（按影响 × 修复紧迫度）

### 🔴 P0（5 项）— 直接戳赛题核心卖点，演示时一眼看穿

| 排名 | 能力点 | 现状 | 为什么是 P0 |
|---|---|---|---|
| P0-1 | B-#2 出处锚点可点击跳转 | source_card 是纯样式 div，无超链接；chunk 无段落坐标 | 赛题明文要求"原文出处锚点"，当前只做到"显示页码徽章"，评委点开发现跳不走直接扣分 |
| P0-2 | B-#4 幻觉防护 | validate_citations 只查角标数字；page_qa 内联重写绕过修复环 | "抗幻觉 RAG"是赛题第 2 大类的灵魂；issues 只显示不修复=主 UI 路径上防护是死代码 |
| P0-3 | A-#2 双栏 PDF 文本提取 | _blocks 按 PyMuPDF 默认顺序拼接，双栏左右串读 | RAG 检索质量根基——chunks 串栏=问答上下文语义断裂，影响所有下游 |
| P0-4 | E-#1 无幻觉 References | 仅 prompt 软约束，无生成后白名单正则；来源是库元数据而非 PDF References 段 | 赛题第 5 大类明文"无幻觉 References"，LLM 仍可凭空造条目 |
| P0-5 | C-#9 引用含金量评估 | 全项目无 citation_count/semantic_scholar/openalex/CCF 字典 | 赛题第 3 大类明文"引用含金量"，当前仅 LLM 主观判断 |

### 🟡 P1（10 项）— 功能有但深度不足，影响评分档次

| 排名 | 能力点 | 现状要点 |
|---|---|---|
| P1-6 | D-#2 边缘论文 | 仅"本地库入度=0 的孤儿"，无跨学科/前沿评分模型 |
| P1-7 | D-#3 自动综述 | generate_review 签名带 G/metrics 但函数体未使用，综述=LLM 读摘要 |
| P1-8 | A-#4 Figure↔caption 关联 | PDF 图只存 PNG，caption 文本未做 bbox 邻近关联 |
| P1-9 | C-#6 定时追踪 | track 增量检测真实，但未注册成 agent 工具，agent 无法自主触发 |
| P1-10 | B-#3 跨文献对比 | multi_paper 工具锁在 Agent 路由，QA 页靠 RRF 拼召回 |
| P1-11 | A-#9 长文档工程化 | prepare_pdf 一次性全量驻内存，无流式/分页入库 |
| P1-12 | E-#2 TikZ | 只出 .tex 代码块，无编译/渲染 |
| P1-13 | E-#3 Graphviz | 只出 DOT 代码块，与 Mermaid 真实渲染不一致 |
| P1-14 | C-#7 GitHub 复现性 | 仅正则抽链接，不调 GitHub API、不读 README |
| P1-15 | D-#4 Future Work 过滤 | 抽取是真的，但"已解决"判定靠 LLM 读摘要主观，未用引用图可达性 |

### 🟢 P2（8 项）— 有瑕疵但不影响主流程

| 排名 | 能力点 | 现状要点 |
|---|---|---|
| P2-16 | B-#1 单篇范围锁定 | 问答闭环可用，但无论文选择器，"单篇"靠库状态巧合 |
| P2-17 | B-#5 查询扩展 | 仅跨语言单路，且扩展词不进向量路 |
| P2-18 | A-#3 公式识别 | 靠 LLM 从纯文本 chunks 猜，无专用引擎 |
| P2-19 | A-#5 层级树回退 | 无书签时空白，无字号/字体推断 |
| P2-20 | A-#6 参考文献切分 | author-year/折行格式不强健 |
| P2-21 | C-#4 Python 沙箱隔离 | 同解释器子进程，非容器 |
| P2-22 | C-#2 PubMed 路由 | 仅 search 专家放行，qa 专家 schema 不可见 |
| P2-23 | E-#4 Matplotlib/CSV 自动出图 | 实际用 plotly 手动选列选图型 |
| P2-24 | D-#1 PageRank 回退 | pagerank/eigenvector 异常时静默降级不报错 |

---

## 五、各路证据置信度摘要

| 路 | 高置信（实锤） | 中置信（推断） | 已确认无（grep 反向证） |
|---|---|---|---|
| A 路 | 1、3、6、7、8 | 4、5（通读 parsing.py 全文确认无 caption 关联、无 heading 推断） | 2、9（基于 _blocks/prepare_pdf 静态推断，未跑双栏 PDF 实测） |
| B 路 | chunk 字段、source_card 无超链接、page_qa 未调 rag.answer、validate_citations 仅正则、dense 路不用 variants、multi_paper 仅挂 Agent 路由 | QA 页与阅读页无锚点联动（grep 结果推断，未逐行读完 app.py） | 同义/HyDE/多视角（grep `hyde/multi_query/perspective/rewrite/synonym` 零业务命中） |
| C 路 | 1-5、8（外部调用均 urllib/arxiv 库，无 演示语料 假数据回退） | 6、7（registry/router 逐行确认无 track/github 工具） | 9（grep `citation_count/impact/venue/semantic_scholar/openalex` 全项目无命中） |
| D 路 | 4 个能力点核心实现位置均 Read 直接读到 | — | 跨学科 frontier 评分、主题聚类、图谱驱动综述结构、基于引用图的"已解决"判定 |
| E 路 | 5、6、3 的 Mermaid 分支、4 的 CSV→plotly 链路 | 1（generate_outline 返回值直接 st.markdown，中间无白名单正则过滤） | 2、3 的 Graphviz/TikZ 编译渲染（sandbox 白名单无 latex、lib/ 无 viz-js） |

---

## 六、审计边界声明

- 本报告**只审计、只定级**，未给出任何"建议接下来优化 XX"的越界建议。
- 未改任何业务代码；未启动 streamlit；未跑测试；未 pip install。
- 所有行号来自 Grep -n / Read 真实定位；拿不准处均写"未定位到"或"基于静态推断"，未臆造。
- 已排除 `__pycache__`、`.venv`、`node_modules`、`lib/`（tom-select 等前端库干扰 grep）。
- 版本历史文件 `version_history/app_streamlit_v1.py:865` 也有一份旧版 nx.pagerank，但当前生效路径是 `services/graph.py`，不影响结论。

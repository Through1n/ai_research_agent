# 答辩 PPT 大纲（决赛版）

> 共 **14 页**。每页：一个标题 + 3 个要点。
> 硬性规则：**只用实测数字说话，不写"性能优异 / 体验流畅 / 大幅领先"这类形容词。**
> 每页末尾标注建议配图（`docs/shots/` 下的 p0–p6.png）。
> 数据口径与技术文档 6.1 测试表一致，引用行号以当前代码为准。

---

## P1　封面：MoreHand

1. 版面感知 RAG + 多 Agent + 引用图谱 + 学术写作，Streamlit 单进程交付
2. 9 个页面（`_PAGES@app.py:121`）、22 个 Function Schema 工具、`build_registry@tools/definitions.py:371` 注册
3. 一句话定位：**别人比"答得好不好"，我们比"答错了能不能被抓出来"**

> 配图：`shots/p1.png`（Agent 总助手，作封面主视觉）

---

## P2　项目定位与痛点

1. 读者手里的 PDF 有版本：同一篇论文存着 v1 和 v7，通用 RAG 两版混着喂模型
2. 通用 RAG 按文本流切块，双栏论文的左右栏会被搅在一起
3. 答案里的引用角标没人逐句验：越界角标、脱离原文的句子直接进正文

> 配图：`shots/p2.png`（问答与溯源页，拒答实例 + 幻觉防护黄条）

---

## P3　需求与赛题对照

1. 赛题六项能力点逐条对应到模块：文档感知① / 抗幻觉 RAG② / 自主工具调用③ / 图谱挖掘④ / 学术 Copilot⑤ / Agent 内核⑥
2. 六项全部落在代码层，技术文档第 6 章逐项给出"命令 + 本轮实测结果"
3. 技术文档 **231 条代码引用，0 条疑似错误、0 条无法判定**（`scripts/_check_doc_refs.py` 可一键复跑）

> 配图：`shots/p0.png`（文献库管理页）

---

## P4　系统架构：五层与依赖方向

1. 分层 `ui/` → `core/`（router / agent / planner / llm / guardrails / sandbox）→ `services/` + `tools/` → `storage/`
2. 依赖方向单向：`services/rag.py:40` 引 `core.guardrails`，`core/agent.py:28` 引 tools，反向一条都没有
3. 换掉整个 UI 层，Agent 内核与业务服务一行不用动

> 配图：`shots/p1.png`，或架构页 `🏗️ 系统架构` 实时截图

---

## P5　AI 技术方案与选型理由

1. 默认智谱 glm-4-flash：文本免费、国内直连、OpenAI 兼容协议；内置 5 个 Provider（`PROVIDERS@core/providers.py:33`），换模型不改业务代码
2. embedding 用 chroma 自带 ONNXMiniLM_L6_V2（384 维，本地推理不出网），持有单例 + 预热（`_build_embed_fn@services/state.py:43`），实测省掉每次 128ms 的会话重建
3. `requirements.txt` 共 13 个依赖，无 LangChain：`streamlit run app.py` 一条命令起，断网也能解析与检索

> 配图：`shots/p0.png`（上传与解析页签）

---

## P6　RAG 与抗幻觉四道防线

1. **生成前**：top 相关度 < 0.35 直接拒答，一次 LLM 都不调（`RELEVANCE_FLOOR@services/rag.py:46`、判据落点 `:834`）
2. **生成中**：系统提示词强制逐条带角标（`ACADEMIC_SYSTEM@core/guardrails.py:470`）；**生成后**：逐句算锚点覆盖率 < 15% 判可疑（`check_citation_support@core/guardrails.py:96`）
3. **净化**：越界角标按内容重定位或标"（未在来源中验证）"，幸存角标重编号与来源一一对应（`sanitize_citations@core/guardrails.py:383`）；四道里三道是代码判定

> 配图：`shots/p2.png`（拒答闸门实例 + 溯源卡片）

---

## P7　PDF 解析与版面感知

1. 541 页官方教材实测：**537 页提取 / 1305 chunks / 25.32s（46.8ms/页）/ 峰值 893.1MB / 0 失败页**
2. 双栏判定要求 4 条必要条件同时成立（`_detect_two_columns@services/parsing.py:171`），1111 页单栏语料误判率 **19.1% → 0%**，真双栏 BERT 正文页 **10/10** 仍识别
3. 扫描件 OCR 单页 **2833 字符 / 9.3s**，词级准确率 **93.9%**、召回 **98.5%**；没配视觉模型时直接拒收，不入库

> 配图：`shots/p3.png`（双栏分栏渲染：左 13 块 / 右 15 块，中缝 293-306pt）

---

## P8　核心功能（一）：文献库 / 问答溯源 / 版式阅读

1. 文献库：上传即解析入库，内容级语义去重 + 版本识别；标题抽取全量 **11/11** 对照符合预期
2. 问答与溯源：【n】角标映射到"第几页第几段"，溯源卡片可跨页跳到阅读页（`anchor_card@ui/page_qa.py:101`、`_set_jump@ui/page_qa.py:130`）
3. 版式阅读：原生渲染 + 分栏轨道渲染 + 划词翻译；阅读页专项冒烟 **62/62**

> 配图：`shots/p0.png` / `shots/p2.png` / `shots/p3.png`（三图并排）

---

## P9　核心功能（二）：检索追踪 / 引用图谱 / 写作辅助

1. 检索与追踪：arXiv 官方接口实时拉取（`search_arxiv@services/extern.py:28`），打分用本地库向量质心 + GitHub 命中 + 研究画像，无预置假行为日志
2. 引用图谱：边从你 PDF 的 References 章节解析（`ref_section@services/graph.py:122`、`build_graph@services/graph.py:201`），PageRank alpha=0.85（`services/graph.py:276`）
3. 写作辅助：References 白名单 **40/40** 逐条可溯源；代码执行走 AST 白名单沙箱（`validate_code@core/sandbox.py:27`）

> 配图：`shots/p6.png` / `shots/p4.png` / `shots/p5.png`（三图并排）

---

## P10　测试与验证：全部是实测数字

1. 单测 **230/230**（约 10s）；AI 链路端到端（真实 Key）**21/21 PASS**，用例由 19 条收紧至 21 条后仍全绿
2. UI 冒烟 **9 页面 + 9 项故障注入 exception=0**；演示路径回归 **7/7 条 0 未捕获异常**（含断网注入分支）
3. 工具接入 **22/22 全部进专家白名单、孤岛 0**；阅读页专项 **62/62**

> 配图：`shots/p2.png`（或测试终端输出截图）

---

## P11　创新点：四条都能当场演示

1. **检索层版本仲裁**：v1 全文写 BLEU 41.0、v7 只在摘要和表 2 改 41.8，提示词规则实测 4 次进程全无效，改检索层（`drop_stale_versions@services/rag.py:91`）后 4/4 答 28.4/41.8
2. **来源均衡截断**：541 页大书占 88% chunk 会挤出正确答案，融合池放大到 final_k×4 后按来源轮转截断（`POOL_FACTOR@services/rag.py:53`、`cap_by_source@services/rag.py:267`）
3. **覆盖率判可疑引用**：刻意不用 Jaccard，用覆盖率（`_overlap@core/guardrails.py:64`）——来源 1200 字会把 Jaccard 稀释到 0

> 配图：`shots/p4.png`（图谱与综述）

---

## P12　局限性与诚实降级

1. 未实现三条（技术文档 8.2/8.3 已标注）：**20 篇批量文献无真实实测、协同过滤是长期项、真并发入库没做**（`save_library@services/state.py:95` 整体覆盖无文件锁）
2. 图谱当前库里 5 个文件、进图仅 2 篇，PageRank 结论不构成能力证据；embedding 是英文模型，中文提问余弦偏低（v1/v7 整篇 0.891292）
3. 降级不装作没事：伪响应带不可伪造标记 `DEGRADED_FLAG@core/llm.py:60`，UI 侧给"人话 + 原因 + 怎么办 + 日志位置"（`llm_failure_banner@ui/common.py:390`）

> 配图：`shots/p2.png`（降级黄条实例）

---

## P13　部署与一键复现

1. `pip install -r requirements.txt && streamlit run app.py`；13 个依赖，每个包都带下一个大版本上界，锁定本地实测组合（chromadb 1.5.9 / pandas 3.0.5 / numpy 2.4.6）
2. Docker + Compose 双路径；`storage/` 目录即全部状态，删掉即回到出厂
3. 断网可演示链路：解析、检索、图谱检索式演示不依赖 LLM；无 Key 时 `llm_guard@ui/common.py:345` 给中文引导而非静默失败

> 配图：`shots/p0.png`

---

## P14　总结与展望

1. 已做完：六项赛题能力点全部落到代码层，四道抗幻觉防线三道是代码判定
2. 一个月内先改三处：并发写库加文件锁、补 20 篇真实语料做实图谱、换中文 embedding 重标阈值
3. 不做的事：不加新页面。9 个页面足够，缺的是深度不是广度

> 配图：`shots/p1.png`

---

## 附：配图速查

| 截图 | 对应页面 | 建议出现的 PPT 页 |
|---|---|---|
| `shots/p0.png` | 文献库管理 | P3 / P5 / P8 / P13 |
| `shots/p1.png` | Agent 总助手 | P1 / P4 / P14 |
| `shots/p2.png` | 问答与溯源 | P2 / P6 / P8 / P10 / P12 |
| `shots/p3.png` | 版式阅读与翻译 | P7 / P8 |
| `shots/p4.png` | 引用图谱与综述 | P9 / P11 |
| `shots/p5.png` | 写作辅助与可视化 | P9 |
| `shots/p6.png` | 检索与追踪 | P9 |

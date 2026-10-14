# Round 16 · 「有 API Key vs 无 API Key」功能对比审计报告

- 调研方式：**只读代码审计**（未运行应用、未修改任何文件、未 git commit）。所有结论均给出 `文件:行号` 证据；凡判定「不依赖 LLM」的，必附本地代码路径证据。
- 审计对象：`C:/Users/Administrator/ai_research_agent`（Streamlit 科研助手，git HEAD = `410e4aa`）。
- 背景口径：Key 读取链为 `config.py:120`（`ZHIPU_API_KEY` → `LLM_API_KEY` → Streamlit Secrets 兜底，见 `config.py:61-94`）；侧边栏另有时运行时手填入（`ui/sidebar.py:211-214`，`type="password"`，存 `st.session_state.saved_api_key` → `ctx.set_provider`，`services/state.py:239-250`）。当前线上未注入 Key。
- 降级机制基线：无 Key 或调用失败时 `core/llm.py` 返回带 `degraded=True` + `degraded_marker` 的伪响应，文案以「⚠️ 离线占位响应：LLM 不可用」开头（`core/llm.py:59-84`、`core/llm.py:323-339`）。

---

## 一、功能对比总表（9 个导航页，以 `app.py:121-131` 的 `_PAGES` 与 `ui/sidebar.py:13-26` 的 `MENU` 为准）

> 「无 Key」列同时覆盖「未注入环境变量 Key 且侧边栏未填」的纯离线态。

| 功能（页面） | 无 Key 表现 | 有 Key 表现 | 证据（文件:行号） |
|---|---|---|---|
| 🤖 智能总助手（Agent） | **完全不可用（0 次请求）**：点执行仅弹 `⚠️ …需要大模型，但未检测到可用 API Key`；即使绕过 UI 守卫，`agent.run` 内部也直接返回「当前未配置 API Key，无法驱动 Agent」。无本地兜底 | 完整链路：LLM 意图路由 → 任务规划 → 每步 ReAct 工具调用 → 流式综合。单次任务最坏约 30 次调用（见第三节） | 守卫：`ui/page_agent.py:290`、`core/agent.py:468-471`；链路：`core/router.py:132-156`、`core/planner.py:246-272`、`core/agent.py:206-215`、`core/agent.py:390-410`、`core/agent.py:629-643` |
| 📁 文献库管理（上传/查重/库管理） | **完全本地可用**：PDF 文本抽取、分块、入库、语义查重（本地 embedding）、版本分组、删除全部不碰 LLM。仅「扫描件 OCR」不可用（给出分档人话提示） | 同上；扫描件走多模态 OCR 逐页识别 | 本地路径：`services/library.py:430-452`（`extract_page_text_column_aware`）、`services/state.py:44-67`（本地 ONNX embedding）；OCR 降级提示：`ui/page_library.py:25-27`、`services/parsing.py:353-356` |
| 📁 元数据 / 公式 / 参考文献结构化 | **不可用（0 次请求）**：`llm_guard` 拦截并给 warning + 引导；元数据场景另有 info 兜底文案 | 各 1 次 `json_chat`，结果 JSON 渲染 | `ui/page_library.py:337-338`、`ui/page_library.py:356`、`ui/page_library.py:481`、`ui/page_library.py:488`、`ui/page_library.py:494`、`ui/page_library.py:509`；守卫：`ui/common.py:345-355` |
| 📁 细粒度结构解析（标题树/表格/插图） | **完全本地可用**（PyMuPDF 抽取）；勾选「多模态逐图理解」时提示「未检测到可用多模态模型，已跳过」，其余结构不受影响 | 结构解析本地完成；勾选后每张插图 1 次视觉调用 | `ui/page_library.py:377-408`（`399-408` 视觉分支与降级 warning）、`services/parsing.py:743-793` |
| 📁 版本差异对比 | **部分可用**：自动降级为「离线客观统计表」（页数/切块/字数/参考文献条目/章节逐项对比），页面明确提示已降级 | 离线统计表 + LLM 语义对比表（1 次调用） | `services/library.py:1215-1254`（`1239-1240` 无 Key 直接转离线、`1253-1254` 离线表）；UI 降级提示：`ui/page_library.py:601-605` |
| 💬 跨文献问答与溯源 | **完全不可用（0 次请求，连检索都不执行）**：点「🔎 提问」仅一行 `⚠️ 请先在左侧填入 API Key。`；`rag.answer` 内卫兵同样直接拒答 | 混合检索（本地 dense+sparse）→ 可选 LLM 扩展/重排 → 流式生成 → 引用校验 + 最多 1 次修复。一次提问最坏 16 次尝试（见第三节） | 守卫：`ui/page_qa.py:279-280`、`services/rag.py:807-808`；链路：`services/rag.py:785-875`；常量：`services/rag.py:46-53` |
| 📖 版式阅读（原生渲染/标题树/引用跳转） | **完全本地可用**：双栏坐标级渲染、书签/推断目录、引用锚点跳转均不碰 LLM | 同左（不因 Key 变化） | `ui/page_reading.py`（渲染/目录/锚点区，无 `ctx.llm` 调用）；服务层 `services/translate.py` 的 HTML 生成 |
| 📖 中英翻译（逐段/划词/原位 PDF） | **完全不可用（0 次请求）**：三个翻译入口均只给 `⚠️ 未检测到可用 API Key，无法调用翻译模型。` | 逐段翻译每段 1 次调用（线程池并发、带缓存）；划词/原位 PDF 各 1 次（流式） | 守卫：`ui/page_reading.py:1341-1343`、`1392-1394`、`1669-1670`、`services/translate.py:1467-1468`、`ui/page_reading.py:392-393`；逐段并发：`ui/page_reading.py:407-475` |
| 🔍 论文检索与追踪（arXiv/PubMed/网页/订阅/推荐） | **完全本地+外网可用，不依赖 LLM Key**：检索、批量下载、动态追踪、个性化推荐打分（本地 embedding + numpy）全部不碰 LLM。唯一 LLM 按钮「引用含金量分析」被 `llm_guard` 拦截。注意：本页依赖**外网**，断网时走 `net_error` 话术 | 同左 + 引用含金量分析 1 次调用 | 无 LLM 证据：`ui/page_search.py:24-168`（extern 检索）、`ui/page_search.py:259-308`（本地打分，`275` 走 `ctx.embed`）；LLM 点：`ui/page_search.py:176-184`；断网话术：`ui/page_search.py:43`、`ui/common.py:337-342` |
| 🕸️ 引用图谱（构建/演示图谱/指标/时间线） | **完全本地可用**：真实引用解析、PageRank/中心性、社区发现、时间线全部是 networkx 确定性计算；演示图谱在 embedding 不可用时再降级为关键词重合度（有可见说明） | 同左（不因 Key 变化） | `ui/page_graph.py:66-76`、`ui/page_graph.py:79-159`（`117-133` 关键词降级）、`ui/page_graph.py:217-233`；`services/graph.py` 建图段无 LLM 调用 |
| 🕸️ 领域综述 / Future Work | **不可用（0 次请求）**：`llm_guard` 拦截，warning「需要大模型，但未检测到可用 API Key」。⚠️ 注意：代码里本的「LLM 失败→networkx 确定性综述」兜底（`services/graph.py:840-842`、`1049-1051`）**只在有 Key 但调用失败时可达**，无 Key 时被 UI 守卫挡在该路径之前 | 各 1 次 LLM 调用生成；调用失败时降级为确定性综述/判定结果，页面挂「非大模型输出」横幅 | 守卫：`ui/page_graph.py:332-333`、`374-375`；降级横幅：`ui/page_graph.py:363-366`、`ui/common.py:477-482`；兜底实现：`services/graph.py:839-845`、`1048-1052` |
| ✍️ CSV 自动出图 + 自定义图表 | **完全本地可用**：列类型推断、plotly 渲染不碰 LLM | 同左；Figure Caption 各 1 次调用 | `ui/page_writing.py:60-209`、`services/writing.py`（`infer_chart_plan` 本地） |
| ✍️ Figure Caption / 论文框架 / 拓扑图代码 / 学术翻译 | **不可用（0 次请求）**：四个按钮均明确 `⚠️ 未配置 API Key，无法调用大模型。`其中 References 白名单抽取是本地确定性拼接（无 Key 也能抽），但正文生成必须 LLM | 各 1 次调用（框架正文为流式）；References 仍走本地白名单 + 生成后白名单校验 | 守卫：`ui/page_writing.py:151-152`、`211-212`、`327-328`、`471-472`、`523-524`；本地白名单：`ui/page_writing.py:287-298`、`services/writing.py:1160-1180` |
| 🏗️ A. 系统架构设计 | **完全本地可用**：静态 HTML 架构图 + 文字说明，零 LLM、零外网 | 同左 | `ui/page_misc.py:61-73`（仅 `embed_html`/`st.markdown`，全文无 `ctx.llm`） |
| 💡 B. 技术创新亮点 | **完全本地可用**：12 张静态卡片 + 折叠表格 | 同左 | `ui/page_misc.py:117-133` |

**汇总**：7 个主菜单页中，`📁 文献库管理`、`🔍 检索与追踪`、`📖 版式阅读`、`🕸️ 图谱可视化`、`✍️ CSV 出图` 有完整的无 Key 可用面；`🤖 Agent`、`💬 问答`、`📖/✍️ 的翻译与生成类` 在无 Key 时完全关闭（但都有明确中文提示，不是静默失败）。

---

## 二、embedding 是否依赖 LLM Key —— **不依赖（已证实）**

| 环节 | 结论 | 证据 |
|---|---|---|
| 向量 embedding | 本地 chromadb ONNX 模型（`ONNXMiniLM_L6_V2`，失败回落 `DefaultEmbeddingFunction`），构造与调用链无任何 `api_key` 参数 | `services/state.py:44-67`（`_build_embed_fn`）、`services/state.py:214-216`（`Container.embed`） |
| 模型加载方式 | 后台线程预热 + 有界等待（默认 90s，可用 `AI4S_EMBED_WAIT_SEC` 覆盖），超时给人话错误并建议先用 BM25 | `services/state.py:114-125`（`warm_embed_async`）、`services/state.py:136-163`（`get_embed_fn`） |
| BM25 稀疏检索 | 本地 `rank_bm25.BM25Plus` + 自研分词 | `services/state.py:221-227`、`services/state.py:32-41` |
| 向量库 | chromadb `PersistentClient` 持久化到本地 `storage/chroma` | `services/state.py:169-170`、`config.py:32` |

唯一与网络相关的行为是 chroma 首次从 S3 下载约 83MB 的 ONNX 模型文件（`services/state.py:70-88` 注释），这与 LLM API Key 无关；部署层已把模型预置进镜像 `~/.cache/chroma/onnx_models` 使下载不发生（`services/state.py:86-88`）。

---

## 三、配额 / 费用风险分级

**计费模型**：每次 LLM 调用 = 1 次立即尝试 + 最多 3 次退避重试 = **最多 4 次尝试**（`core/llm.py:41`、`core/llm.py:487`）；401/403/400/非 ASCII Key 不重试（仅 1 次，`core/llm.py:220-278`）；总时长预算 `LLM_RETRY_BUDGET_SEC=20s` 可提前终止重试（`config.py:128`、`core/llm.py:501-505`）。

**关键前提：无 Key 时全站 0 次真实请求。** 已核实的短路守卫：`ui/page_qa.py:279`、`services/rag.py:807`、`core/agent.py:468`、`core/planner.py:246`、`core/router.py:132`、`services/query_expansion.py:166-168`、`services/translate.py:1467`、`ui/page_reading.py:1341/1392/1669`、`ui/page_writing.py:151/211/327/471/523`、`ui/page_search.py:176`、`ui/page_graph.py:333/375`、`ui/page_library.py:337/481/494`，以及底层 `core/llm.py:477-483`（`_client=None` 直接伪响应）与 `core/llm.py:678-682`（`call_llm_with_retry` 空 Key 直接伪响应）。**配额风险只存在于注入 Key 之后。**

### 高频入口（点一下 ≈ 1 次调用，最坏 4 次尝试）

| 入口 | 调用次数 | 证据 |
|---|---|---|
| 💬 问答「🔎 提问」（普通模式） | 查询扩展 1（弱命中才调；强命中跳过）+ 重排 1（可选）+ 生成 1（流式，失败回退非流式再 1）+ 修复 1（`MAX_REPAIR=1`）→ **正常 2~4 次，最坏 4 调用 = 16 次尝试** | `services/rag.py:603`（强命中跳过扩展）、`services/rag.py:635-636`（rerank）、`services/rag.py:842-875`（生成+修复环）、`services/rag.py:144-186`（流式失败回退） |
| ✍️ 写作页翻译 / Figure Caption / 拓扑图 | 各 1 次 | `ui/page_writing.py:520-531`、`150-157`、`468-476` |
| 🕸️ 综述 / Future Work | 各 1 次 | `services/graph.py:839`、`services/graph.py:1048` |
| 📁 元数据 / 公式 / 参考文献 / 版本差异 | 各 1 次 | `ui/page_library.py:356/488/509`、`services/library.py:1241` |
| 🔍 引用含金量分析 | 1 次 | `ui/page_search.py:181` |
| 侧边栏「🔌 测试连通性」 | 1 次（最小请求） | `ui/sidebar.py:127` |

### 中频入口（一次操作 = 多次调用）

| 入口 | 调用次数 | 证据 |
|---|---|---|
| 🤖 Agent 一次任务 | 路由 1 + 规划 1 + ReAct 迭代 N + 重规划 ≤2 + 流式综合 1（失败回退 1）。N 上限 = `min(max(16, 2×步数), 24)`（`GLOBAL_MAX_ITERS=max(6,AGENT_MAX_ITERS×2)=16`，硬顶 `MAX_TASKS×STEP_MAX_ITERS=12×4=48` 被 `global_iter_cap` 收敛到 24）→ **最坏 ≈ 1+1+24+2+1+1 = 30 次调用 = 120 次尝试**；3~5 步的常规演示约 6~12 次 | `core/agent.py:34-58`、`core/agent.py:206-215`、`core/agent.py:478`、`core/agent.py:555-557`、`core/agent.py:629-643`、`core/planner.py:38-39` |
| 💬 问答对比模式（勾选 2~5 篇） | 每篇各 1 次 rerank（扩展命中缓存实际 1 次）+ 生成 1 + 修复 1 → **≈ 8 次调用** | `services/rag.py:666-704`、`ui/page_qa.py:259-264` |
| 📖 阅读页「加载并逐段翻译」 | **每段 1 次调用**（`MAX_TRANSLATE_WORKERS` 并发 + 本地缓存，缓存命中不调）→ 一页 10~30 段 = 10~30 次调用 | `ui/page_reading.py:407-475`（`441-443` 每段一次 `_safe_translate`） |

### 最高风险放大器（一次点击 = N 次调用，N 随内容规模）

| 入口 | 调用次数 | 证据 |
|---|---|---|
| 📁 上传扫描件 PDF（入库 OCR） | **每页 1 次视觉调用**（`OCR_PARALLEL=3` 并发）。官方测试件 541 页教材 → **541 次调用 = 最坏 2164 次尝试**，为全站单次点击请求数上界之最 | `services/library.py:449`、`services/library.py:673-696`、`services/parsing.py:350-376` |
| 📁 「全方位解析」勾选多模态读图 | 每张插图 1 次视觉调用；无视觉模型的 Provider 时每张图再 1 次图注翻译 | `services/parsing.py:761-793`（`779`）、`services/parsing.py:748-756` + `796-808` |
| ✍️ 论文框架「重新生成（强化约束）」 | 每次 1 次流式（可重复点） | `ui/page_writing.py:390-394`、`services/writing.py:1160-1180` |

**控费设计（已有）**：查询扩展/重排/规划/路由均带缓存（`services/query_expansion.py:269-276`、`services/rag.py:353-368`、`core/planner.py:241-266`、`core/router.py:127-151`）；流式失败后不重复非流式重试（`services/rag.py:169-183`、`core/llm.py:573-575`）；401/400 不重试；20s 总预算。**建议**：现场演示若用真实 Key，优先避免反复上传扫描件大 PDF 与反复点 Agent 示例任务。

---

## 四、Key 泄露面逐项结论

### (a) 前端 HTML / JS —— **拿不到（结论：安全）**

Key 的全生命周期都在服务端 Python 进程内：侧边栏 `text_input(type="password")`（`ui/sidebar.py:211-214`）→ `st.session_state.saved_api_key` → `ctx.set_provider`（`services/state.py:239-250`）→ `LLMClient.api_key` → `OpenAI(api_key=key, ...)`（`core/llm.py:451-452`）。Streamlit 的执行模型是服务端渲染，前端只收到渲染产物。

全仓库 grep `api_key|API_KEY|saved_api_key` 的生产代码命中点全部是**服务端内部传递**（作为 `call_llm_with_retry` 的参数）：`services/rag.py:136`、`services/library.py:1245`、`services/parsing.py:372/783/804`、`services/query_expansion.py:174`、`services/writing.py:234`。没有任何一处把 Key 写进 `st.write/st.markdown/st.code/st.json/st.query_params/URL`。`type="password"` 保证输入框本身不明文回显。

> 历史备注：`version_history/app_streamlit_v1.py` 存在过 `const API_KEY="{api_key}"` 注入前端 JS 的旧写法，但 `version_history/` 不在运行导入链（已 grep 确认 `app.py`/`ui`/`services`/`core` 无引用），当前代码无此问题。

### (b) 落盘日志 —— **不记录 Key 内容（结论：安全，附实测抽样）**

| 落盘点 | 记录内容 | 是否含 Key | 证据 |
|---|---|---|---|
| `storage/llm_failures.log` | 时间/异常类型/`str(err)[:800]`/prompt 前 2000 字/调用点/model | 否。OpenAI SDK 异常的 `str(e)` 只含状态码与响应体，不含 Authorization 头；实测抽样（2026-10-15 记录）为测试桩，无 Key | `core/llm.py:371-417`（`403-415` 记录字段） |
| `storage/llm_config_errors.log` | 可预见配置错误（Key 缺失/非 ASCII） | 否——非 ASCII 错误**只报长度不报内容**（实测记录「长度 5」「长度 6」） | `core/llm.py:134-136`、`core/llm.py:376-384` |
| `logs/errors.log` | 异常 traceback（UI 层 `show_error`） | 否（同上，异常文案不含 Key；实测抽样无 Key） | `app.py:19-29`、`ui/common.py:218-230` |
| `storage/memory.json`、`storage/user_profile.json` | 查询扩展缓存、用户画像 | 否（缓存内容是查询变体，非 Key） | `services/query_expansion.py:269-307` |
| `.env` / `.streamlit/secrets.toml` | 本地/云平台密钥入口 | **均被 `.gitignore` 排除**（`.gitignore` 第 2 行 `.env`、第 8 行 `.streamlit/secrets.toml` 且注释明说「一旦提交等于泄露真实 Key」）；`git log --all -- .env .streamlit/secrets.toml` 为空（从未提交）、`git ls-files` 不含 `.env`；`.streamlit/` 现只有 `config.toml` 与 `secrets.toml.example` | `.gitignore`、git 只读查询 |

### (c) 报错信息回显 —— **不包含 Key 内容（结论：安全，附一条理论边界）**

- `_validate_api_key` 的人话错误只含**长度**（`core/llm.py:134-136`：「长度 %d」）。
- 框架横幅 `llm_failure_banner` 展示 `last_error[:300]`（`ui/common.py:410-419`，折叠区标注「已自动脱敏」）；`last_error` 来源是 `str(e)[:300]`（`core/llm.py:496`）或自造文案（`core/llm.py:478/508`），均不含 Key。
- 侧边栏连通性测试回显 `last_error[:160]`（`ui/sidebar.py:132-133`、`277-278`）。
- `show_error` 的「查看技术细节」展开完整异常（`ui/common.py:319-323`）——同样只可能是 SDK 异常文案。
- **理论边界**：若某 Provider 在错误响应体中回显 Key（非 OpenAI SDK / 智谱现行行为），该文案会经 `last_error[:300]` 进入横幅折叠区并写入 `llm_failures.log` 的 `error[:800]`。这是全仓库唯一可能的泄露路径，按当前对接的两家 SDK 行为不成立。

### (d) 部署注入方式 —— **运行期注入，不固化进镜像（结论：安全）**

`Dockerfile:58` 明确「业务密钥一律运行期注入」；`docker-compose.yml:46-51` 用 `${ZHIPU_API_KEY:-}` 等从宿主环境变量透传（未设时为空串，即当前离线态）；云平台走 Streamlit Secrets（`config.py:61-78` `_st_secret`，`core/providers.py:128-138`）。本地 `start.sh:141-156` / `start.bat:170-186` 只是检测并提示，不写 Key。

### (e) git 历史 —— **干净（结论：安全）**

`.env` 与 `.streamlit/secrets.toml` 从未进入任何提交（`git log --all` 双路径查询为空；`git ls-files` 无 `.env`）。近期提交（Round 13~15）均为演练/文档/部署修复，未见 Key 模式串。

---

## 五、离线态「看起来像坏了」的评委误判点（最需警惕的 3 条）

1. **问答页无 Key 时只有一行小 warning。** 评委点「🔎 提问」→ 主体区只出现 `⚠️ 请先在左侧填入 API Key。`（`ui/page_qa.py:280`），没有检索过程、没有答案，视觉上等同于「核心功能失败」。缓解事实：侧边栏顶部有「📌 尚未配置 API Key」警告框且接入区自动展开（`ui/sidebar.py:301-309`）——但评委注意力在主体区，大概率看不到。**演示前应确保侧边栏 Key 就位，或在开场白中明示「无 Key 态下问答/Agent/翻译为 intentional 降级」**。

2. **embedding 模型冷启动期间向量检索长时间无响应。** 云端首次加载约 83MB ONNX 模型，`get_embed_fn` 有界等待默认 90s（`services/state.py:143-147`）；期间语义查重/向量召回不可用，只走 BM25 或人话超时提示（`services/state.py:152-157`）。这是 Round 15 线上全白事故的同一根因家族（`services/state.py:71-88` 注释：同步加载曾致 183s 全白）。虽然已改后台预热 + 镜像预置模型，但若镜像缓存失效，现场表现就是「检索转圈/查重不动」。

3. **图谱综述存在两条表现不一致的降级路径，容易被读成「时好时坏」。** 无 Key 时 `llm_guard` 直接拦掉综述按钮（`ui/page_graph.py:333`），连「确定性综述」都看不到；有 Key 但调用失败时才走 networkx 确定性生成并挂「⚠️ 非大模型输出」横幅（`services/graph.py:840-842`、`ui/common.py:477-482`）。前者像功能缺失，后者像功能报错——实际两者都是设计内的诚实降级。同理，阅读页三个翻译入口（`ui/page_reading.py:1341/1392/1669`）无 Key 时各只给一行 warning，集中出现会放大「翻译模块整体报废」的观感。

---

## 六、附录：本报告的判定依据索引

- 降级文案与标记：`core/llm.py:59-84`（`degraded_content`/`DEGRADED_FLAG`）、`core/llm.py:323-339`（`_fake_response`）
- 重试与分类：`core/llm.py:41-57`（常量）、`core/llm.py:220-278`（`_classify_error`）、`core/llm.py:470-509`（`_create`）
- Key 校验：`core/llm.py:125-137`（`_validate_api_key`）、`services/query_expansion.py:111-125`（`key_issue`）
- Key 解析优先级：`core/providers.py:116-138`（`resolve_key`：界面手填 > 环境变量 > Secrets）
- 页面路由：`app.py:121-131`；菜单定义：`ui/sidebar.py:13-26`
- 无 Key 守卫汇总：见第三节「关键前提」段逐条列出的 **21 处**守卫（按 `文件:行号` 计）+ 底层 2 处伪响应短路。另按提示文案全文 grep（`未检测到可用 API Key|请先在左侧填入 API Key|未检测到可用多模态|未配置 API Key`）实测命中 **27 个代码位置**（同一守卫在多分支重复出现文案），两个数字口径不同、均已复测。

*报告完。所有结论基于上述只读调研；未修改任何仓库文件。*

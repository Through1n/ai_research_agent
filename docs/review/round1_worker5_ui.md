# Round1 · 考生5号 · UI 一致性重做 + 演示路径稳定性加固

> 报告人：考生5号　|　涉及文件（全部为我独占，未触碰他人文件）：
> `ui/common.py`、`ui/sidebar.py`、`ui/page_library.py`、`ui/page_graph.py`、
> `ui/page_search.py`、`ui/page_misc.py`、`ui/page_agent.py`、`app.py`
> 临时新增：`scripts/_smoke_ui.py`（进程内渲染冒烟脚本，可删）
>
> 说明：全文行号均指**本次改动后**的文件行号。

---

## 0. 一句话结论

7 条 UI 规范已在我负责的 6 个页面 + 侧边栏 + 公共层全部落地；演示路径上的 API 调用、文件操作、session_state、下标、空数据五类风险点全部加了防护；**9 个页面 × (空库/已加载演示数据) 两轮渲染 + 4 个本地重操作 + 9 条故障注入，共 0 未捕获异常**。有 4 处逻辑问题属于他人文件/服务层，我只记录未改（见第 6、7 节）。

---

## 1. 公共层重构：ui/common.py（137 → 434 行）

保持**所有旧函数签名不变**后才增强，确保 `app.py` 与 page_qa/page_reading/page_writing（他人文件）的既有调用不被破坏。
已验证跨文件引用：`grep` 结果显示他人页面只 import `page_header / require_library / embed_html`（`ui/page_qa.py:13`、`ui/page_reading.py:6`、`ui/page_writing.py:19`），另有 `services/writing.py:311` 复用 `resolve_pdf` —— 这些函数签名与返回语义均未变。

| 新增/增强 | 行号 | 说明 |
|---|---|---|
| 常量 `LONG_TEXT_LIMIT=300` / `LONG_LINE_LIMIT=15` / `QA_JUMP_KEY` / `READING_MENU_IDX=3` | 26–33 | 长文本折叠阈值、跨页跳转约定常量 |
| `_esc()` 统一转义 | 35–38 | 防 PDF 文本里的 HTML 破坏页面/注入 |
| `step_text(action, cur, total)` | 40–45 | 生成「正在检索 3/8 …」式 spinner 文案（规范 6） |
| `at(items, idx, default)` | 47–59 | **安全下标读取**，越界/非法索引返回默认值，杜绝 IndexError |
| `page_header(title, subtitle, icon="")` | 62–77 | 规范 1：浅色渐变卡片（浅蓝→浅紫、左侧蓝色竖条、深色文字），positional 旧调用兼容 |
| `pretty_table(df_or_rows, columns, title, max_rows)` | 114–139（CSS 79–88） | 规范 4：浅蓝/浅紫渐变表头 + **深色文字** + 细边框 + 斑马纹 + hover；支持 DataFrame/list[dict]/list[list]；**默认 escape=True**；空表自动友好提示；渲染失败降级原生 dataframe |
| `empty_state(text, hints, icon)` | 141–147 | 空状态统一话术 + 引导操作列表 |
| `friendly_error(what, exc, reasons, actions)` | 149–161 | 错误 + 「🩺 可能原因」+「🛠️ 怎么办」统一降级话术 |
| `net_error(what, exc, fallback_hint)` | 163–169 | 网络类失败专用话术（含"用离线演示数据继续"引导） |
| `llm_guard(ctx, action)` | 171–183 | 大模型未就绪守卫，替代原来"按钮点了没反应" |
| `long_block(text, title, limit)` | 185–199 | 规范 5：>300 字或 ≥15 行自动折叠（短内容展开显示） |
| `ensure_state(defaults)` | 201–215 | 集中初始化 session_state；callable 取默认值，list/dict 自动 copy |
| `safe_call(what, fn, ...)` | 217–224 | 函数级异常边界，返回 `(ok, value)` |
| **`safe_render(fn, ctx, what)`** | 226–240 | **增强版**：保持 `app.py` 旧调用签名；增加 Logo「暂时不可用」+ 可能原因 + 怎么办 + 折叠Traceback |
| `require_library(ctx)` | 242–257 | 增强：异常安全 + 走 `empty_state` 统一话术 |
| `jump_banner()` | 265–301 | 任务组 C：消费 `qa_jump_page`，侧边栏提示条 + 跳转/已看按钮 |
| `render_styled_table` / `embed_html` / `html_table` / `source_card` / `render_trace` / `memory_mb` / `resolve_pdf` | 304 / 309 / 323 / 337 / 360 / 411 / 420 | 全部**保留原签名**，内部改为异常安全（如 `embed_html` 的 iframe 兜底失败也不再抛、`html_table` 失败降级 pretty_table、`render_trace` 单条目异常只跳过该条） |

---

## 2. 任务组 A：7 条 UI 规范落实情况

### 规范 1 · 页面顶部统一（渐变浅色卡片 + 一句话说明）

| 文件 | 行号 | 说明 |
|---|---|---|
| 实现 | `ui/common.py:62` | 浅色渐变 HTML 卡片 |
| 📁 文献库管理 | `ui/page_library.py:35–37` | render 第一行调用 |
| 🔍 检索与追踪 | `ui/page_search.py:359–361` | render 第一行调用 |
| 🕸️ 引用图谱 | `ui/page_graph.py:150–152` | render 第一行调用 |
| 🤖 Agent 总助手 | `ui/page_agent.py:23–25` | render 第一行调用 |
| 🏗️ 系统架构 | `ui/page_misc.py:62–64` | |
| 💡 创新亮点 | `ui/page_misc.py:97–99` | |
| 侧边栏 | `ui/sidebar.py:44–48` | 保持原生 `st.title`（本就是统一标题） |

> 副产品：`page_header` 是公共层，page_qa / page_reading / page_writing（他人文件）**无需改动即自动获得新标题样式**，保证全项目视觉一致。

### 规范 2 · 按钮文案统一带 emoji

全量核对过我负责的每个 `st.button` / `form_submit_button`：

- `app.py` 无按钮；`ui/common.py:284,295`（🗺️ 前往该页 / ✅ 已查看）
- `ui/sidebar.py:175`（🚀 一键加载演示数据）、`197`（🗑️ 清空文献库）
- `ui/page_library.py`：🔍 解析并查重(42)、♻️ 覆盖旧版(100)、🗂️ 保留两个版本(108)、❌ 取消本次上传(116)、🤖 抽取元数据(128)、🧩 全方位解析(171)、🧮 提取 LaTeX 公式(274)、📚 结构化参考文献(287)、🗑️ 删除(336)、🔍 对比差异(365)
- `ui/page_graph.py`：🕸️ 构建引用图谱(157)、🎭 演示模式示意图谱(210)、📝 一键生成领域综述(227)、🚀 Future Work 前沿扫描(240)
- `ui/page_search.py`：🔍 搜索 arXiv(27)、⬇️ 下载并入库(54)、🚀 批量下载入库(75)、🔍 PubMed(108)、🔍 DuckDuckGo(138)、🧹 分析并剔除(173)、✨ 刷新并推荐(214)、🔄 运行一次追踪(313)、♻️ 重置检查点(326)、👍/👎(245,248)
- `ui/page_agent.py:67`（🚀 执行）+ 5 个示例问题按钮统一加 emoji（8–14）
- 同一语义文案统一：删除=🗑️、对比差异=🔍、清空=🗑️、重置=♻️

### 规范 3 · 输出分级（不再一律 st.write/markdown）

统一用法：`st.success`=成功结果、`st.info`=中性提示/空状态、`st.warning`=降级-but-可用、`st.error`=失败。
代表位置（`friendly_error/empty_state/net_error` 内部即按此分级）：
`ui/common.py:141,149,163,171,226`；`ui/page_library.py:44,52,112,133,171,240,252,255,368,371`；
`ui/page_graph.py:163,177,185,213,231,243`；`ui/page_search.py:36,38,43,53,66,92,97,121,123,147,156,158,185,218,221,225,229,307,323,325,331`；
`ui/page_agent.py:41,54,56,70,90`；`ui/page_misc.py:71–74,102`。

### 规范 4 · 表格统一走 pretty_table（带渐变表头的 HTML）

| 位置 | 行号 | 原来 |
|---|---|---|
| 图谱：图论指标表 | `ui/page_graph.py:125` | `st.dataframe(df, width="stretch")` ← **修复见 §7.1** |
| 文献库：提取的表格 | `ui/page_library.py:249` | `html_table()`（旧深色表头） |
| 文献库：结构化参考文献 | `ui/page_library.py:320` | `html_table()` |
| 检索页：PubMed 结果 | `ui/page_search.py:126` | 逐条 `st.caption` |
| 创新亮点：12 项创新一览 | `ui/page_misc.py:100–101` | markdown 列表 |
| 旧台账 `render_styled_table` 内部 | `ui/common.py:304` | dataframe（无人调用，保留兼容） |

### 规范 5 · 长文本折叠

| 位置 | 行号 | 触发条件 |
|---|---|---|
| `long_block` 实现 | `ui/common.py:185` | >300 字或 ≥15 行 |
| arXiv/PubMed/网页/推荐摘要 | `ui/page_search.py:50, 241, 354` | 摘要按需限长折叠 |
| 引用含金量分析报告 | `ui/page_search.py:184` | 全文折叠 |
| 版本差异报告 | `ui/page_library.py:373` | markdown 报告折叠 |
| 领域综述 / 前沿方向全文 | `ui/page_graph.py:256, 259` | 全文折叠 |
| Agent 最终答案 / 执行轨迹 | `ui/page_agent.py:55, 58` | 答案>200 字折叠；轨迹始终折叠（原来已折叠，保留） |
| 标题树 | `ui/page_library.py:233` | 默认收起 |
| 公式列表 | `ui/page_library.py:308` | 默认收起 |
| 系统架构分层说明 | `ui/page_misc.py:8–17, 76` | 抽成 `_ARCH_DETAIL`，默认收起 |
| 创新点详情 | `ui/page_misc.py:103–105` | 改为「表格总览 + 逐条折叠详情」 |
| 引用关系列表 | `ui/page_graph.py:134` | 默认收起 + 超过 12 条只展示前 12 |

### 规范 6 · 加载态带进度描述的 spinner

`step_text()`（`ui/common.py:40`）生成「动作 X/Y …」文案，共 15 处：
`ui/sidebar.py:178`（📥 正在加载演示数据 X/Y）、`202`（🗑️ 正在清空文献库）；
`ui/page_library.py:137, 281, 294, 368`；`ui/page_graph.py:211, 229, 242`；
`ui/page_search.py:32, 56, 81, 113, 143, 181, 216, 320`；`ui/page_agent.py:82`。
其中能算总量的都显示 X/Y（演示数据按篇、批量按标题行数、订阅按话题数、推荐/采样按条数）。
PDF 解析与细粒度解析另用 `st.progress` + 阶段文案：`ui/page_library.py:178–196`（「🧩 解析标题树 1/3」三段式）。

### 规范 7 · 多列布局比例统一

已消灭全部非常规比例：

| 位置 | 行号 | 原 | 现 |
|---|---|---|---|
| 文献库：版本决策按钮 | `ui/page_library.py:99` | `[3 列]`→ | `[1,1,1]` |
| 文献库：公式/参考文献按钮 | `ui/page_library.py:273` | `[2]` | `[1,1]` |
| 文献库：文献列表行 | `ui/page_library.py:334` | `[6,1]` | `[2,1]` |
| 图谱：三个操作按钮 | `ui/page_graph.py:209` | `[2]` | `[1,1,1]` |
| 检索页：PubMed / 网页 | `ui/page_search.py:104` | `[2]` | `[1,1]` |
| 检索页：推荐卡片 | `ui/page_search.py:237` | `[5,1]` | `[2,1]`（内部赞踩用 `[1,1]`，244） |
| 检索页：追踪按钮 | `ui/page_search.py:312` | `[3,1]` | `[2,1]` |
| Agent：示例问题 | `ui/page_agent.py:17,28` | `columns(5)` | `columns(3)` 换行排布 |
| 侧边栏：跳转提示条按钮 | `ui/common.py:283` | — | `[1,1]` |
| 架构页：结论卡片 | `ui/page_misc.py:71` | — | `[1,1]` |

---

## 3. 任务组 B：演示路径零报错加固清单

### 3.1 app.py（集中初始化 + 全链路兜底）

| 行号 | 防护类型 |
|---|---|
| 16–30 | **依赖导入 try/except**：任何 import 失败 → 中文 error + 原因 + 办法 + 折叠 Traceback + `st.stop()`（实测：在 anaconda3 base 环境无 chromadb 时，页面显示「❌ 依赖模块加载失败：No module named 'chromadb'」而不是红字 Traceback） |
| 32–36 | `desktop_chrome.apply()` 再包一层 try（该文件非我所有，不改） |
| **38–64** | **`ensure_state()` 集中初始化 14 个全局 key**：`saved_api_key / pending / struct / meta_view / formula_view / ref_view / arxiv_res / pm / web / rec_rows / trk_report / graph_pack / review / future / agent_runs / qa_jump_page`（list/dict 用 callable 惰性构造，避免可变对象复用） |
| 66–73 | `get_container()` try/except + `st.stop()`（向量库损坏时给"删 storage/chroma 重启"的指引） |
| 76–80 | **侧边栏渲染 try/except**：侧栏异常不再导致整站白屏，回退到默认首页 |
| 82–92 | `_PAGES` 字典**结构未变**（要求项，键/值/顺序完全保持） |
| 95–101 | 路由循环保持原逻辑，仅补"未匹配路由"的友好提示 |

### 3.2 ui/sidebar.py

| 行号 | 防护类型 |
|---|---|
| 52 | `ensure_state({"saved_api_key":"", "menu":MENU[0]})` —— **先初始化再读**（原代码多处直接 `.get`/读 `saved_api_key`） |
| 66–71 | `list_providers()` try/except，失败给中文提示并降级空列表 |
| 87–98 | 自定义端点 `ctx.set_provider` + `custom_provider` try/except |
| 105–112 | **列表下标边界保护**：`ids.index(provider_id)` 前先做 `in ids` 判断，sel→pid 再做二次 `ids[0]` 兜底（原 85–89 一行表达式可能 ValueError） |
| 114–118 / 126–136 | `get_provider` / `ctx.set_provider` try/except |
| 140–148 | 用户画像读取 try/except（memory.json 损坏不崩） |
| 156–164 | query_params `p` 做 int 转换 + `0 <= idx < len(MENU)` **边界检查** + `IndexError` 捕获（原 116–122 无边界检查） |
| 175–195 | 演示数据加载：逐篇 try/except、失败明细、进度 spinner X/Y、成功后才 rerun |
| 197–215 | 清空文献库：空库直接提示不必清空；逐篇 try/except；**清空后同步清理派生 state**（graph_pack/review/future/struct/...），避免"文献删了图谱还在" |
| 46 | 桌面版本号读取已有 try（保留） |

### 3.3 ui/page_library.py（📁 页面1：上传→查重预警→版本差异）

| 行号 | 防护类型 |
|---|---|
| 16–23 | `_init()`：`pending/struct/meta_view/formula_view/ref_view` 五个 key 全部 `ensure_state` |
| 38–45 | 文件名为空校验 + `st.file_uploader` 读数前的空文件保护 |
| 57–96 | `library.prepare_pdf` try/except；**返回值类型检查**（非 dict / 带 `error` 字段分别处理，原代码只假设 dict 成功） |
| 81–83 | 进度回调 clamp `0~1`，回调自身再包 try（服务层回调异常不影响主流程） |
| 99–118 | 覆盖/保留/取消三个动作各自 try/except，失败不丢 `pending` |
| 124 / 166 / 328 / 355 | **空数据保护**：四个区块（元数据 / 细粒度 / 文献列表 / 版本管理）空库时给 `empty_state` + "点左侧一键加载演示数据"引导 |
| 128–157 | 元数据抽取：`llm_guard` + PDF **路径存在性检查** `os.path.exists` + try/except + `finally: doc.close()` 防句柄泄漏（原代码异常时不关闭 doc） |
| 172–231 | 细粒度解析：路径检查 + 三段 progress + **异常时也关闭 doc** + try/finally |
| 255–268 | 插图渲染前 `os.path.exists` 检查 + 单张图渲染失败只提示不中断 + 超过 8 张只展示前 8 张 |
| 233 / 308 / 314 | `stobj` 结构成员用 `.get`，避免缺失键 KeyError；标题树单条解包异常跳过 |
| 274–322 | 公式 / 参考文献：**原代码要求 LLM 可用否则按钮静默无反应**，改为 `llm_guard` 给中文引导；并加 try/except 与 JSON 渲染兜底 |
| 342–352 | 删除文献 try/except |
| 344–378 | 版本管理：`group_versions` try/except；**版本差异按钮**加 `llm_guard` + try/except + 空报告提示 + long_block 折叠 |
| 380–389 | 抽出 `_commit()` 统一入库成功/失败话术 |

### 3.4 ui/page_graph.py（🕸️ 页面6：演示模式建图→一键综述）

| 行号 | 防护类型 |
|---|---|
| 16 | `ensure_state({"graph_pack","review","future"})` |
| 27–41 | `_metrics_rows`：`metrics` 每个字段用 `.get(key,{})` 兜底，缺失按 0 计（原直接下标取 `m["pagerank"]`，analyze 失败返回空 dict 会 KeyError） |
| 19–25 | `_title()` 用 `.get` 兜底缺失论文元数据 |
| 105–108 / 110–146 | `_make_pack` / `_render_pack`：pack 用 `.get` 读取全部字段 + `isinstance(dict)` 校验；单独 try/except 包住整个渲染，异常不向上传播 |
| 118–122 | HTML 缺失时只提示"可视化不可用，表格仍可用"，不崩 |
| 131–142 | 空节点 / 空边 / 空 metrics 均有空状态提示 |
| 156–200 | 建图按钮：progress + try/except + `graph_html` 单独 try（缺 lib/vis 文件时不崩）+ 无真实引用边时给出原因与两条补救办法 |
| **56–103 / 209–226** | **新增「🎭 演示模式示意图谱」兜底**（见 §3.7） |
| 228–255 | 综述 / 前沿扫描：`llm_guard` + try/except + 结果 long_block 折叠；两处原有 try/except 已被 try-with-spinner 完整覆盖 |
| 256–259 | 结果为空时不渲染空白标题 |

### 3.5 ui/page_search.py（🔍 页面5：arXiv 检索→降级提示）

| 行号 | 防护类型 |
|---|---|
| 358 | `ensure_state`：五个结果 key（`arxiv_res/pm/web/rec_rows/trk_report`）集中初始化，**原代码多处 `st.session_state.arxiv_res[...]` 直接读可能 KeyError** |
| 27–70 | arXiv：空输入校验 + **API try/except → net_error 降级话术**（含"可能原因/怎么办/离线演示"）+ 逐条 `isinstance(dict)` 校验 + 入库 try/except 分 ok/duplicate/error 三级提示 |
| 72–101 | 批量下载：空输入校验 + try/except + 结果条目类型校验 |
| 103–168 | PubMed / 网页：同样的空输入校验 + try/except + net_error；网页返回体里的 `error` 结构专门处理（`extern.web_search` 会把异常塞进结果列表） |
| 170–189 | 引用含金量：空输入校验 + `llm_guard` + `ctx.llm.chat` try/except（**原 97 行裸调用，无任何保护**） |
| 191–208 | 点赞/踩：`ctx.likes` 读写 try/except（`setdefault` + `save_library` 失败不再崩） |
| 210–289 | 推荐：候选抓取 try/except + net_error；**打分逻辑抽成 `_score_rows`，内部每个向量/相似度计算都 try**（原 `.embed` 与 `np.dot` 裸调用）；`p["summary"]` 全改 `.get`；rows 条目类型校验 |
| 301–355 | 追踪：`import services.track` 失败有提示并 return；空话题校验；`set_subscriptions`/`fetch_track` try/except + net_error；`reset_checkpoints` try/except；`r["new"]` 改 `.get`（缺字段不再 KeyError）；每条新论文限制 6 条 |
| 357–370 | render 内**逐区块 try/except**：任一区块（追踪/推荐/arXiv/批量/其他/含金量）异常只跳过该区块，其余继续可用，页首加"断网请先加载演示数据"的说明 |
| 29（原） | **修复 typo**：`**（{published}，{arxiv_id}）` 缺右括号（现已修正为 49 行） |

### 3.6 ui/page_agent.py / ui/page_misc.py

| 行号 | 防护类型 |
|---|---|
| `ui/page_agent.py:26` | `ensure_state({"agent_runs": list})`（原只有单 key 判断，且历史上轮 loop 直接 `st.session_state.agent_runs` 读） |
| 30–36 | 示例按钮点击包 try；历史条目 `isinstance(dict)` 校验；`tr.to_dicts()` try/except（53） |
| 44 / 58–62 | 最终答案缺失给提示；轨迹渲染独立 try/except（原来裸调用） |
| 70 | `llm_guard(ctx, "Agent 执行")` 替代原来的 `st.warning` + `return` |
| 76–93 | **`agent.run` 全程 try/except**（原 54 行裸调用，工具异常会崩整页））+ live 回调 try + `ph.empty()` try |
| `ui/page_misc.py:66–70` | 架构图 `embed_html` 二次 try/except（公共层已包一层，页面层再兜一次） |
| `ui/page_misc.py:100–105` | 创新点渲染前统一转 DataFrame，`pretty_table` 渲染失败会降级 |

### 3.7 演示路径专属兜底：🕸️ 页面的「演示模式示意图谱」

`ui/page_graph.py:56–103`（`_build_demo`）+ 按钮 `209–226`。
动机：真实引用关系依赖 PDF 正文的 References 章节，若现场换机器/换数据导致解析为空，页面只会显示"未检测到引用关系"，演示就断在这。兜底逻辑：

- 只在"真实边为空"且用户主动点按钮时触发，**不覆盖** `🕸️ 构建引用图谱` 的原有行为；
- 用 `ctx.embed` 对"标题+正文前 1200 字"做余弦相似度，按年份从新到旧连边（近似"新工作参考旧工作"）；
- 走 `services/graph.py` 的公开函数 `analyze / classify_papers / graph_html`（**未改服务层任何代码**）；
- 展示时顶部固定警告条（`ui/page_graph.py:112–114`）："当前为**演示模式示意图谱**……不代表真实引用关系"，避免误导评委；
- 全流程 try/except，论文数 <2 或 embedding 失败给中文提示。

---

## 4. 任务组 C：与考生2（ui/page_qa.py）的跨文件约定对接

- **未改动 `ui/page_qa.py`**（grep 确认其 `JUMP_KEY = "qa_jump_page"` 写入结构为 `{"source","page","para_idx","from"}`）。
- 在公共层实现只读消费：`ui/common.py:265–301` `jump_banner()`
  - 读到 `qa_jump_page` 且是 dict 且含 source 才渲染；
  - `page` 做 `int()` 转换，非法值回落 1；
  - 显示「🎯 问答溯源待跳转：《X》第 N 页（第 M 段）」+ `🗺️ 前往该页` / `✅ 已查看` 两个按钮；
  - 跳转用 `at(MENU, READING_MENU_IDX)` **索引边界查找**（`ui/common.py:47` 的 `at()`），越界不崩；
  - 同时写 `session_state["menu"]` + `query_params`（p/jump_page/paper），兼容阅读页原有的 URL 参数消费方式；
  - 挂载点：`ui/sidebar.py:150–154`，并包了 try/except（该组件任何异常不影响侧栏其余部分）。
- `qa_jump_page` 的默认值 `None` 已在 `app.py:63` 集中初始化（与 page_qa 自己的初始化一致，无冲突）。
- **注意**：? 本项目里还有一个待确认点 → page_qa 的 `READING_PAGE_IDX = 3` 与我的 `READING_MENU_IDX = 3` 一致，`ui/common.py:43` 里的 `source_card` 硬编码也同步为同一常量。

---

## 5. 自验结果

### 5.1 语法编译

```
C:/Users/Administrator/anaconda3/python.exe -m py_compile \
  ui/common.py ui/sidebar.py ui/page_library.py ui/page_graph.py \
  ui/page_search.py ui/page_misc.py ui/page_agent.py app.py
→ BASE py_compile OK
```
（另在项目真实环境 `anaconda3/envs/ai_research`（Python 3.11.16 / streamlit **1.63.0** / chromadb 1.5.9，即 `启动科研助手.bat` 里 `conda activate ai_research` 所用环境）也通过一次 py_compile，均 0 错误。）

### 5.2 真实渲染冒烟（进程内 AppTest，未启动 streamlit server）

脚本：`scripts/_smoke_ui.py`（可复跑：`python scripts/_smoke_ui.py --heavy`）

| 阶段 | 内容 | 结果 |
|---|---|---|
| [1] 逐页渲染（空库） | 9 个页面逐个切换 | **exception=0**（9/9） |
| [2] 演示数据后逐页渲染 | 点「🚀 一键加载演示数据」→ 9 页再全跑 | **exception=0**（9/9），文献库页正确识别出"同一论文（1706.03762）在库中有 2 版" |
| [3] 本地重操作 | 🕸️ 构建引用图谱（0.6s，成功产出 **2 节点 / 1 条真实引用关系**：BERT→Attention Is All You Need）；🎭 演示模式示意图谱（0.2s）；📝 一键生成领域综述（5.9s）；📁 细粒度全方位解析（1.6s） | **exception=0**（4/4，且全部产出 success 消息，非"点了没反应"） |
| [4] 故障注入 | 把 `extern.search_arxiv/latest_arxiv/search_pubmed/web_search`、`track.fetch_track`、`definitions._ingest_arxiv_paper/t_batch_download`、`library.version_diff`、`graph.build_graph`、`agent.run` 全部替换成必崩函数，再逐个点击 9 个按钮（并先给输入框填值，避免被空值守卫绕开） | **exception=0**（9/9），全部给出中文降级提示，实测文案如：`arXiv 检索失败：模拟断网：arXiv 不可达`（+可能原因+怎么办）、`构建引用图谱失败：模拟 embedding 服务异常`、`版本差异对比失败：模拟大模型不可用：额度耗尽` |

最终脚本退出码 0，输出「✅ 冒烟通过：全部页面 0 未捕获异常」。

> 补充事实（用于判断"没跑的项"）：
> - anaconda3 **base** 环境没有 chromadb/streamlit≥1.63，只能在那里跑 py_compile；AppTest 在那里跑的结果是**我的导入兜底生效**（页面显示「❌ 依赖模块加载失败：No module named 'chromadb'」+中文指引，无 Traceback），这是额外的一次降级验证。
> - **未实测项**：真实 PDF 上传解析路径（`library.prepare_pdf`）——AppTest 无法给 `file_uploader` 灌入真实 PDF 字节且涉及 OCR/CN 模型，本次未覆盖，属于他人服务层；未打磨split screen 真机视觉效果（按要求不做视觉校验）。

---

## 6. 未完成项与原因

1. **PDF 上传解析路径未实测**：见上，工具限制（`st.file_uploader` 在 AppTest 里无法注入文件）。代码侧已做完防护（类型/返回值/异常），但未经端到端验证。
2. **真实断网未实测**：用故障注入代替（ogenicity 更高，因为能精确命中 except 分支）。
3. **未修改任何业务逻辑**：按硬性约束 3，所有"看起来可疑"的逻辑仅加防护+记录，未按自己判断改。
4. **`page_library.py` 的 `struct/meta_view` 不上锁（跟随选择的文件失效）**：这是既有逻辑（切换论文后仍显示上一次解析结果），我只加了一行提示文案（`ui/page_library.py:230–232`），**没有擅自改成"换文件自动清空"**，因为那会改变演示时"看完 A 再看 B"的行为，属业务逻辑。
5. **OTHER 页面（page_qa/page_reading/page_writing）我只改了"它们用到的公共层"**：它们的 `page_header` 样式会变（更统一），但按钮/表格/折叠是否符合 7 条规范由各自负责同学处理，我不越界。
6. **跨页跳转的端到端联动未实测**：需要 page_qa 真的写入 `qa_jump_page` 才能触发 `jump_banner`，冒烟脚本没有点击 QA 页的溯源按钮（该页是他人文件且可能正在改，避免干扰）。组件本身有 `None`/非 dict/缺字段三层保护。

---

## 7. 风险点（诚实版）

1. **修复了一处真实的版本兼容性崩溃（重要）**：
   原 `ui/page_graph.py:44` 为 `st.dataframe(df, width="stretch")`、原 `ui/page_library.py:130` 为 `st.image(..., width="stretch")`。`width="stretch"` 是新版 Streamlit（≥1.5x）才有的写法；在旧版（如本机 anaconda3 base 的 **streamlit 1.30**）会直接报参数错误/失效。已全部改为 `pretty_table` 与不带 width 参数的 `st.image`，**跨版本安全**。若评委机器上的 streamlit 不是 `ai_research` 环境那份，原代码是有崩溃风险的。
2. **`onsetsidebar "✅ 模型已就绪"` 可能名不副实**（既有现象，不改服务层）：冒烟中发现，未填 API Key 时 `ctx.set_provider(pid, "")` 仍返回 True，侧栏显示"模型已就绪"，但真正调用 LLM 时才失败。我的防护（`llm_guard` + try/except）能保证失败时不崩且有中文提示，但"看着像已就绪"的观感风险来自服务层，建议由服务层同学确认。
3. **故意保留的行为变化**：`render_styled_table`（`ui/common.py:304`）内部改为渲染 `pretty_table` HTML。目前 grep 确认**无任何页面调用它**，但若有同学在并行分支里新增调用，看到的会是新的浅色表格而非 DataFrame（功能等价，不影响数据）。
4. **`page_header` 改为 HTML 后会对他人页面生效**：这是"一致性"的收益，但如果考生2/4/6 期望 `st.header` 的原生语义（如目录锚点），可能有轻微样式预期差。
5. **`app.py` 在 bare 模式（`python app.py`，非 streamlit run）下**：导入兜底里 `st.stop()` 在 bare 模式不一定中断执行，会继续跑到 `ensure_state` 报 `NameError`（已实测复现）。**正常 `streamlit run` 及 AppTest 下不成立**（st.stop 正常工作），实际演示不受影响。
6. **演示数据存在 session 级 + JSON 双写**：清空文献库时会清理派生 state（`ui/sidebar.py:210–212`），但如果评委在其它页面先把结果写进 session_state、又在别处删文献，仍可能有"结果指向已删论文"的中间态；我已在关键渲染处（图谱 `_title`、推荐 `_score_rows`）做 `.get` 兜底，但不保证每处都完美同步。
7. **LLM 相关演示依赖外部额度**：代表团一键综述/前沿扫描/版式差异等 LLM 分支，我已全部 try/except 兜底，但**实际能不能出内容取决于现场 Key 与额度**。建议现场优先演示"离线可复现"的路径：一键加载演示数据 → 📁 版本去重 → 💬 问答溯源 → 🕸️ 建图（离线实测 0.6s）→ 🎭 演示模式图谱。
8. **与其他 agent 的并行冲突面**：我只动了本人 8 个文件（已用 `git status` 核对：`app.py`、`ui/common.py`、`ui/page_agent.py`、`ui/page_graph.py`、`ui/page_library.py`、`ui/page_misc.py`、`ui/page_search.py`、`ui/sidebar.py` + 新增 `scripts/_smoke_ui.py`）。若他人后续在 `ui/common.py` 里加函数，请注意我已重排文件结构（保留了所有旧函数与其语义）。

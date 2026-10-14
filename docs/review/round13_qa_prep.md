# Round 13 · 话术2「决赛答辩问答手册」过程报告

负责人：qa-prep（只读角色，仅新建本文档与 `docs/决赛答辩问答手册.md`）
环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`

---

## 1. 交付结果

| 项 | 结果 |
|---|---|
| 产物 | `docs/决赛答辩问答手册.md`（20 条表格 + 3 个刁钻问题专节 + 可复跑命令 + 口径纪律） |
| 表格条目 | **20 条**，四分类各 5 条（技术选型 5 / 抗幻觉 5 / 创新性 5 / 局限性 5） |
| 答案字数 | 中文正文 **20/20 条落在 100~150 字**（范围 102~150） |
| 引用总数 | 文档内 `符号@文件:行号` **48 条（去重），逐条校验通过 0 问题** |
| 刁钻问题 | 3 条单列成节（智谱倒闭 / 扫描件准确率 / 推荐是否模拟数据） |

---

## 2. 引用校验：我改过行号的、我排除掉的

### 2.1 运行中改过行号的（4 处）

| 原写法 | 问题 | 改成 |
|---|---|---|
| `tools/definitions.py:11` 引 services | 该行实际是 `from tools.registry import Registry` | `tools/definitions.py:12` |
| `_consume_qa_jump@ui/page_reading.py:514` | 514 是分隔注释行，函数定义在下一行 | `ui/page_reading.py:515` |
| `analyze@services/graph.py:276` | 276 是 `nx.pagerank(...)` 调用行，不是函数定义行 | 拆成 `nx.pagerank@services/graph.py:276` + `analyze@services/graph.py:271` |
| `_validate_api_key@core/llm.py:130` | 函数定义在 125；130 是 docstring 里"绝不记录 Key 内容"那句注释 | 拆成 函数 `@125` + 注释 `@130` |

另外 `DENSE_K`/`SPARSE_K@config.py:136-137` 这种合并写法会让符号与行号对不上，已拆成 `DENSE_K@config.py:136`、`SPARSE_K@config.py:137`。

### 2.2 排除掉的（1 处，是代码注释本身写错了）

`_set_jump@ui/page_qa.py:130` 的 docstring 里写着「`ui/page_reading.py:43-60` 已消费 jump_page / paper」。
**这个行号范围是错的**——43~60 是 `ui/page_reading.py` 的 import 区间，真正消费跳转的地方在 `_consume_qa_jump@ui/page_reading.py:515`（清 query_params 在 `:565`）。
我没有沿用这条注释，改用 515。**建议后续轮次修掉这条 docstring**（它是别人的独占文件，本轮我不动）。

### 2.3 批量校验方式

写完后用脚本把文档里所有 `符号@文件:行号` 抽出来，逐条读目标文件、比对 `符号 in 该行`，共跑三轮（88 条候选 → 46 条候选 → 48 条成稿），最后一轮 **48/48 全过**。

---

## 3. 我实跑过、确认可复跑的（5 项）

```bash
# ① 标题抽取对照 —— 通过
python scripts/verify_title_extract.py
#   末尾输出：核对通过 8/8
#   说明：8 = pdf/ 下 6 个 + demo_data/ 下 2 个，**是 8 条路径不是 8 个不同文件**
#   （EXPECT 只有 6 个唯一文件名；demo_data 的两份与 pdf/ 重名）
#   其中 pdf/1706.03762v1_img.pdf 是扫描件，两份都如实判空

# ② 单元测试 —— 通过，1.483s
python -m unittest discover -s tests -v
#   Ran 18 tests ... OK
#   注意：本环境**没有装 pytest**，只能走 unittest，别在场上敲 pytest

# ③ 工具接入 21/21 无孤岛（运行时统计，不是文档抄的）
python -c "import sys;sys.path.insert(0,'.');from tools.definitions import build_registry;from core.router import EXPERTS;t=set(build_registry().names());c=set().union(*[set(v['tools']) for v in EXPERTS.values()]);print('工具',len(t),'已接入',len(t&c),'孤岛',sorted(t-c))"
#   输出：工具 21 已接入 21 孤岛 []

# ④ 541 页大书解析耗时（本机实测，可复跑）
#   pdf/UnderstandingDeepLearning_02_09_26_C.pdf = 541 页 / 22.3MB
#   parsing.native_page_texts → 4.36s，132.3 万字
#   （技术文档写的是 4.02s，本机 4.36s，同量级；**现场建议报本机实测值**）

# ⑤ 扫描件检测
#   detect_scanned(pdf/1706.03762v1_img.pdf) → True（15 页，首页原生文本 0 字符）
#   detect_scanned(pdf/UnderstandingDeepLearning_02_09_26_C.pdf) → False（541 页教材不误判，R9 修复生效）
#   detect_scanned(pdf/1706.03762v7.pdf) → False
```

**只读补充核查**（读 `storage/library.json`，没写入）：库内 5 个文件 → 按 arxiv_id 分组 3 个独立文档
（1706.03762 占 3 份含 v1/v1_img/v7、1810.04805、UnderstandingDeepLearning）。

---

## 4. 我没能验证的（诚实清单，共 6 项）

| # | 未验证内容 | 原因 | 数字当前出处 |
|---|---|---|---|
| 1 | **AI 链路 21/21 PASS** | 来源是 `scripts/_verify_live_api.py`，**要真实 API Key 会烧配额**，我没跑也没被授权跑 | `docs/review/round7_judgeG.md:42`、`round8_worker14_doc.md:117`（组内为 OCR 2 + QA 7 + guard 2 + compare 1 + write 5 + graph 3 + extern 1 = 21） |
| 2 | **OCR 量化数字**（2790 字符 / 8.3s / SequenceMatcher 0.960 / 词级准确率 93.9% / 召回 98.5% / 392 词对 383 词 / 漏 6 错 18 / 真错字 1 处） | 要配视觉模型 Key 才能复跑，没跑 | `docs/review/round7_judgeG.md:295-318`（Round 7 实测）；Round 8 复跑为 2833 字符 / 9.3s |
| 3 | **三个相似度数字**：v1/v7 整篇 0.891292、分段指纹 0.963228、改名后 1.000000 | 复跑脚本 `scripts/selftest.py` 会**先清空现有文献库**（`reset@scripts/selftest.py:14-19`），我是只读角色不能跑 | `docs/technical_doc.md:264-265` |
| 4 | **图谱压测 n=20 7.9s / n=40 25.0s** | `scripts/bench_graph.py` 同样**先清空文献库**（`bench_graph.py:17-18` 与 `:55-56`），不能跑；且文档自注"近几轮未重复实测" | `docs/technical_doc.md:841` |
| 5 | **"入图 2 个节点 / 1 条边 / 1 个社区簇"** | `build_graph()` 需要容器实例，我只验证了 5 文件 / 3 逻辑文档这一层，没验到入图节点数 | `docs/technical_doc.md:841`、`:72` |
| 6 | **PDF 解析 4.02s**（官方用例 B 原值） | 我测出的是 4.36s（不同机器） | 已在手册里改用本机实测 4.36s |

**第 2 项风险最高**：OCR 那组数字是刁钻问题 2 的核心答案，但只能引报告、不能当场复跑。现场如果评委要求复跑，必须提前确认有视觉模型 Key，否则只能说明"数据来自 Round 7 实测记录"。

---

## 5. 两个容易翻车的数字陷阱（提醒全队）

### 陷阱 A：有两个「21/21」，含义完全不同

- **工具接入 21/21**：`build_registry@tools/definitions.py:355` 注册 21 个工具，被 6 个专家白名单全覆盖 —— **这个我运行时验过，可当场复跑**。
- **AI 链路 21/21 PASS**：`scripts/_verify_live_api.py` 的 21 条真实 API 断言 —— **这个没验证，要 Key**。

两个 21 是**数字巧合**。场上如果混着说，被追问"你这 21 是哪来的"就会卡壳。手册里我只在可复跑命令那一节放了工具版 21/21，AI 链路版 21/21 我没写进手册正文。

### 陷阱 B：两个「8/8」和「5 个文件」

- 标题抽取 **8/8 = 8 条 PDF 路径**（pdf/ 6 + demo_data/ 2），不是 8 个不同文件。
- 库内 **5 个文件 → 3 个逻辑文档 →（据文档）2 个入图节点**。三层数字不一样，说混了会被抓住。

---

## 6. 对任务描述的一处纠正

任务里写的分层是「ui→core→services→tools」。
**实际不是这个顺序**——`app.py:4` 写的是 `交互层(ui) → Agent 内核(core) → 工具/服务层(tools/services) → 存储层(storage)`，**tools 和 services 是并列关系，不是 services→tools 的上下游**。

我 grep 验过依赖方向，是干净 DAG（无环）：

| 边 | 位置 |
|---|---|
| ui → core / services | `ui/page_agent.py:18`、`ui/page_qa.py:20` 等 |
| core/agent → tools | `core/agent.py:28` |
| tools → services | `tools/definitions.py:12` |
| services → core 的**叶子模块** | `services/rag.py:40` 引 `core.guardrails`；`services/state.py:12-14` 引 `core.llm/memory/providers`；`services/library.py:12`、`services/graph.py:22`、`services/parsing.py:11`、`services/writing.py:49` 引 `core.llm` |

**没有环的关键原因**：`core/agent.py` 是唯一引 tools 的 core 模块，而它只被 `ui/page_agent.py` 引（grep `from core.agent` in services/ + tools/ → 空）。services 引的全是 core 里不反向依赖任何东西的叶子模块。

手册里那条回答我已经按正确顺序讲，并主动纠正了提问里的顺序。

---

## 7. 绝对量词核实记录（写之前 grep 数过的）

| 说法 | 核实方式 | 结果 |
|---|---|---|
| 共 21 个注册工具 | 运行时 `build_registry().names()` | **21** ✅ |
| 6 个专家 | 运行时 `len(EXPERTS)` | **6** ✅（qa/search/graph/writing/reading/parsing） |
| 9 个页面 | `_PAGES@app.py:121` 数字典 | **9** ✅ |
| 内置 5 个 Provider | `PROVIDERS@core/providers.py:33` 数列表 | **5** ✅（zhipu/deepseek/qwen/openai/ollama） |
| 单元测试 18 条 | 实跑 unittest | **18** ✅ |
| 白名单悬空工具 | 运行时差集 | **0** ✅ |
| 代码里所有 Lock | grep `Lock()` in core/ services/ | 全是**内存缓存锁**，**0 个保护磁盘写入** ✅（这是并发局限的证据） |

---

## 8. 我没敢说成"局限"的两条（前轮教训）

按纪律，写「局限/待修」前先 grep 验真，有两条**已经完成**，如果说成局限等于自曝：

1. **Key 的 ASCII 校验** —— `_validate_api_key@core/llm.py:125` 已落地（`isascii()` 拦截 + 人话提示）。技术文档 8.2 第 9 条也标注"已解除（Round 9）"。**不能说成待修。**
2. **扫描件 OCR 端到端实测** —— Round 7/8 已补测（2790 字符 / 93.9%）。技术文档 8.2 第 11 条标注"已解除"。**不能说成未实测。** 但它的 3 项残留是真的：无本地 OCR 引擎兜底、VLM 逐次非确定性、单页受超时与成本约束 —— 手册里我按残留讲。

手册末尾「口径纪律」一节把这两条和"别把没做的说成做了"（20 篇批量 / 协同过滤 / 真并发入库）都写进去了。

---

## 9. 附带验证：drill-script 报的「版本差异降级警告失效」（已确认）

drill-script 报线索，我独立验证。**结论：缺陷属实，但触发条件比原报更窄。**

**失效点**：`ui/page_library.py:590` 的判定 `"暂不可用" in txt or txt.startswith("【")`，对 `version_diff` 的真实降级返回永远为 False。

**三条路径实测**（用源码里的真实字面量跑，非推测）：

| 路径 | 返回串 | 命中 590？ |
|---|---|---|
| A 真降级（`services/library.py:1222`） | `#### 离线客观统计（LLM 不可用，已降级为离线对比）…` | **False** ❌ |
| B 占位串若漏出（`core/llm.py:83`） | `【LLM 暂时不可用，请稍后重试。…】` | True（靠 `startswith("【")`） |
| C 成功（`services/library.py:1219`） | `#### 离线客观统计（可直接复核）…` | False ✅正确 |

**B 路径实际不可达**：`library.py:1217` 已用 `"【LLM 暂时不可用" in content` 拦下并 raise，走不到页面。
**C 不命中是正确行为**。
**所以只有 A 是缺陷**——它恰恰是真正会出现的那条。

**两个 clause 都是死的**：
1. `"暂不可用"` —— `library.py` 的降级标题写的是「LLM 不可用」，没有"暂"字；而 `core/llm.py` 的占位串写的是「**暂时**不可用」，`"暂不可用" in "暂时不可用"` 实测为 **False**（子串不匹配）。作者手滑少打了一个"时"。
2. `startswith("【")` —— 本可兜住占位串，但占位串在 1217 已被前置拦截，永远到不了页面。

**grep 佐证**：全项目 `core/ services/ tools/ ui/ app.py` 里含"暂不可用"的只有 `ui/page_library.py:590`（判定）和 `:591`（警告文案本身）。`services/library.py` 任何返回路径都不含这三个字；`_offline_version_report@services/library.py:1158` 只产 Markdown 表格，也不含。

**触发条件（比原报更窄，这点要讲清楚）**：

| 场景 | 行为 |
|---|---|
| 完全没 Key / Key 非 ASCII | `available@core/llm.py:457` 为 False（**静态**判 `self._client is not None`，不探测网络）→ `llm_guard@ui/common.py:345` 拦截 → version_diff 不执行 → 提示"未检测到可用 API Key" ✅正常 |
| **有 Key 但调用时失败**（429 / 超时 / 断网 / 额度耗尽 / 返回占位串） | 守卫放行 → `except` @1221 → 1222 返回 → 590 不命中 → **无降级警告** ❌ |

即 drill-script 说的"模型不可用 → 走 1222"**在"完全没 Key"时不成立**（被 llm_guard 提前拦了），只有"填了 Key 但现场调用失败"才触发。现场最可能撞上的就是 429 限流。

**影响范围（已 grep 界定）**：`version_diff` 的调用方只有 `ui/page_library.py:583`（UI）和 `scripts/_verify_official.py:481`（验证脚本）；`tools/definitions.py` 里没有对应工具，**Agent 链路不受影响**。

**我的处置**：只读不改代码。已写进 `docs/决赛答辩问答手册.md` 新增的第五节（含现场口头圆场话术），并上报 team-lead。另注：`docs/演示视频脚本.md` 的 P1 预案写的是"页面出现『⚠️ 版本差异报告已降级：大模型暂不可用』"，与现场实际画面对不上，drill-script 已在其彩排脚本里按实际文字改。

**drill-script 的反馈（已闭环）**：他独立复核后确认结论一致，并采纳了"复现步骤要改成'填格式合法但会失败的 Key'"的修正。他这轮因此发现**自己脚本里一个会当场露馅的错**——节点 2 操作红线原本指向 E1，而 E1 描述的是橙色「⚠️ 离线占位响应」横幅 + 占位串，版本差异这步根本不会出现这些，照 E1 念就是"话术与画面打架"。已新增 E1b 专用预案（含触发条件对照表）并把红线改指 E1b。**E1b 的圆场话术与我手册第五节口径一致，两份文档不打架。**

**派修时机约束（drill-script 提出，我已转达 team-lead）**：这个 bug **不要在演示前 30 分钟内改**。他脚本 T-30 第 15 条有一条验证动作（点一次版本差异确认空态文案），改了判定那条的预期文案就变了，**改完必须重跑**。已进入 T-30 窗口就维持现状 + 口头圆场。

### B 的再一轮：drill-script 补的两条（属实，但第 1 条要加一个条件）

他把我说的"撞文件名"从运气升级成了确定性。我逐条验了：

**① `commit` 落盘 —— 属实，但**是有条件的，不是无条件**。**
`commit@services/library.py:768` 的 `:776-777`：
```python
if prepared.get("raw"):
    (UPLOAD_DIR / filename).write_bytes(prepared["raw"])
```
落盘**取决于 `prepared` 里有没有 `raw`**。我 grep 了 `"raw"` 的三处来源（`services/library.py:341 / 703 / 759`）——**全是 `prepare_pdf`**（上传路径）；而 `add_text_corpus@:854` 构造的 prepared **没有 `raw` 这个键**，落盘那行直接跳过。

所以准确说法是：**上传路径必然落盘（确定性）；演示语料路径不落盘（只能撞文件名）**。他原话"commit 一定落盘"略去了这个条件，结论方向对，但按无条件讲会在追问"那演示数据呢"时卡住。

**② `remove_paper` 不删 PDF 文件 —— 属实，且比他说的更强。**
`remove_paper@services/library.py:1070` 只做 `_delete_paper_records`（chunks + collection）+ pop papers/paper_order + rebuild_bm25 + save_library，**不碰文件**。我进一步 grep 了整个 `services/library.py`：`os.remove` / `unlink` / `rmtree` **一处都没有**。全项目仅有的两处 `unlink` 在 `scripts/_verify_official.py:218`（脚本自建临时目录）和 `scripts/make_scanned` 同类的 `make_tech_pdf.py:193`（造 fixture），**都不碰 `storage/uploads/`**。

**影响（我据此下调了此前警告的严重度）**：我此前提醒"清库脚本一跑库就没了、要用必须提前备份"。更准确的说法是——**只丢记录，`storage/uploads/` 里的 PDF 全部存活**，重新上传即可恢复，不必从备份还原。手册第五节已按此改写。

**手册第五节据此重写**，把两条入库路径分开讲：上传 → 确定性落盘必然可渲染；演示数据 → 不落盘只能撞文件名。并加了"删文献不删文件"这条。

### 10.1 ① 已被 team-lead 修复，我独立复验通过

team-lead 亲修 `ui/page_library.py:590`（Round 13）。**没有采纳我"让 version_diff 返回显式标记字段"的建议**，理由是 `services/library.py` 在技术文档里有 11 条行号锚点、改签名会让 `version_diff@:1183` 之后的锚点整体位移，而当前卡在 29 页硬门禁上——**为干净架构去动热点文件，收益/风险比不合算**。这个判断我认可。

改用**精确子串判据** `"已降级为离线对比" in txt`。我的独立复验：

| 项 | 结果 |
|---|---|
| `git diff --stat` | `ui/page_library.py \| 2 +-`，**1 insertion / 1 deletion** —— 改 1 行不增行 |
| `:590` 行号是否位移 | **未位移**，判定仍在该行，我已引用的 `:590` 全部继续有效 |
| 四象限实测 | 降级串（`library.py:1222`）→ **True**（修复前 False）；成功串（`library.py:1219`）→ **False**（前后均不误报）；占位串 → True（原有 `startswith("【")` 分支） |
| 判据子串唯一性 | `已降级为离线对比` 在降级串=True、在成功串=False ✅；`可直接复核` 反之 ✅ |

**两条返回串都以 `#### 离线客观统计` 开头**，所以确实不能拿这个当前缀判据（会误伤成功路径），team-lead 这个观察是对的。

**手册同步**：第五节重构为 5.1 / 5.2 / 5.3 三小节，5.1 按 team-lead 要求写进了**成功串与降级串两条原文对照**（`services/library.py:1219` / `:1222`），并更新为"**已修、现场会正常出现黄字警告**"，保留圆场话术作为黄字之后的补充（与 drill-script E1b 口径一致）。`口径纪律` 第 1 条也补上了这条，并加了反向提醒：别把"修好了"讲成"还得靠嘴圆"。

### 10.2 ② 是误读：我从未把 `ui/page_library.py:25` 列为待修

team-lead 让我把 ② 从待派修清单删掉。**确认：我从来没有把它列为待修项。** 我给 team-lead 的待派修三条一直是 ① `ui/page_library.py:590`、② `docs/演示视频脚本.md` P1 文案、③ `ui/page_qa.py:108` docstring 行号。

`ui/page_library.py:25` 在我手册里是作为**支持"扫描件是拒收而非降级"这一结论的证据**被引用的（原文「是扫描件，但当前未启用 / 不可用的视觉模型 Key，全部页面都提取不到文本」，与 `services/library.py:508`「均未提取到文本」一致）。team-lead 的核查结论与我一致——**这句没有说反，无需修改**。手册里该引用保留，无需删除。

---

## 10. 交叉验证 drill-script 回我的两条补充（A 属实 / B 需修正）

### A. 五家平台视觉能力矩阵 —— **属实，已采纳**

运行时 `list_providers()` 实测：

| provider | has_vision | vl_model |
|---|---|---|
| zhipu | **True** | `glm-4v` |
| deepseek | **False** | `''` |
| qwen | **True** | `qwen-vl-plus` |
| openai | **True** | `gpt-4o-mini` |
| ollama | **False** | `''` |

即 **deepseek 与 ollama 两家无视觉**，其余三家有。drill-script 提醒"别写成只有智谱支持"——查我手册原文写的是「DeepSeek 和 Ollama 没有视觉模型」，与矩阵一致，**无需修正**；但我顺手把完整矩阵补进了刁 1 的答案，场上被追问"那通义呢"能答。

### A 的延伸：我自己手册里有一处同类错误，已自纠

drill-script 提到他原写"扫描件会自动降级为离线解析"是错的——**我手册刁 1 里原本也是这句**（"换了之后扫描件会自动降级成离线文本解析"）。核查后确认**真实行为是明确拒收、未入库**：

- `ocr_page_text@services/parsing.py:350` 在无 vl_model 时确实先返回占位串 `[扫描件 OCR 降级提示]…`
- 但 `is_ocr_error_text@services/parsing.py:86` 认得这个前缀，`_validate_prepared@services/library.py:484` 最终在 `services/library.py:510` 写 `reason = "scanned_no_ocr"`，error 文案是「全部页面均未提取到文本，**未入库**」（`:508`）
- 页面话术档 `ui/page_library.py:25` 同样是"全部页面都提取不到文本"

**已把刁 1 那句改成"入库时直接拒收、未入库"**，并补上三家支持 / 两家不支持的矩阵。

### B. 阅读页与演示数据 —— **机制对，结论需修正**

drill-script 说"演示数据纯文本入库、无原始 PDF，阅读页会直接报警告"。

**他对的部分（机制）**：`add_text_corpus@services/library.py:854` 确实只把文本写进 chunks，**不落任何 PDF 文件**。

**需要修正的部分（结论）**：那条警告**不是必然会出**。阅读页在 `ui/page_reading.py:1093-1101` 是按**文件名**去 `UPLOAD_DIR` 和 `PDF_DIR` 里找同名 PDF，**找到了就照常渲染**，只有找不到才报 `ui/page_reading.py:1103` 的警告。也就是说这是一次**文件名巧合**，不是设计上的绑定。

**本机实测（只读查 `storage/uploads/`）**：库里 5 篇**全部命中真实 PDF**：

| 库内文件名 | 命中位置 | 实测 |
|---|---|---|
| 1706.03762v7.pdf | uploads | 2163KB / 15 页 / 首页 2857 字 |
| 1706.03762v1.pdf | uploads | 2079KB / 15 页 / 首页 2718 字 |
| 1810.04805v2.pdf | uploads | 757KB / 16 页 / 首页 3954 字 |
| 1706.03762v1_img.pdf | uploads | 3683KB / 15 页 / 首页 **0 字**（扫描件） |
| UnderstandingDeepLearning_02_09_26_C.pdf | uploads | 21821KB / **541 页** / 首页 683 字 |

→ **当前这台机器上阅读页、判双栏、划词翻译都能正常演示，不会报那条警告。**

**另修正他给的行号**：那条警告在 `ui/page_reading.py:1103`，不是他写的 1094（1094 是 `for base in (UPLOAD_DIR, PDF_DIR):`）。

**我的处置**：手册第五节新增一条「版式阅读页靠文件名巧合定位 PDF，演示前必须确认」，写明本机当前 5 篇全命中、可正常演示，但换机器或 `storage/uploads/` 被清就会触发警告，**T-30 必须逐篇确认**。已把这条回给 drill-script——他若在彩排脚本里写成"阅读页不能用"，会让主讲跳过一个实际能演的功能。

---

## 11. 纪律自查

- 全程只读：未修改 `core/ services/ tools/ ui/ app.py/ config.py/ requirements.txt/ Dockerfile/ docker-compose.yml/ docs/technical_doc.md`，也未碰 `docs/review/` 下其他人的文件。
- 未执行 `git add / commit / push`。
- 实跑的 5 项里，只有 ③ 和 ⑤ 会构造对象/读文件，均无写入；`verify_title_extract.py` 与 unittest 均为项目自身的只读验证入口。
- 明确**没有跑** `scripts/bench_graph.py` / `scripts/selftest.py`（会清空 `storage/library.json`），也没跑 `_verify_live_api.py`（烧配额）。

# Round 19 集成轮（父代）：后台任务化收口 + PDF 适配审计 + 冒烟假绿修复

> 日期：2026-10-22。触发：用户两个诉求——①「有些论文不适配，扩大适配范围」（附上传 PDF 报错截图：「PDF 完整性校验异常：cannot access local variable 'minhash'」）；②「0 到 6 的板块中的任务任何一项都是可以同时运行的，而不只是 0 板块」。
> 结论先行：两个诉求的**主体实现都已在工作区里**（团队并行产出，但全部未提交）；父代本轮做的是四件事——补断链、修假绿、实证审计、提交收口。

## 一、进场盘点：磁盘 ≠ 提交

`git status` 实测：13 个文件改动（+2119/−548）**全部未提交**（上一提交还是父代的侧边栏修复 `adce577`）。按本项目纪律（agent 的「已完成」声明一律不可信，只认磁盘+git），逐项核实：

| 组成部分 | 文件 | 对应诉求 |
|---|---|---|
| minhash 未赋值修复 + 回归测试 | `services/library.py:811-819`、`tests/test_parsing_library.py`（+154 行） | ①（截图里的崩溃） |
| PDF 适配加固 | `services/parsing.py`：`open_doc` 空密码认证、栏感知空文本回退纯文本 | ① |
| 后台任务公共层 | `ui/common.py:997+`：`bg_submit / bg_running / bg_consume`（docstring 原文引用用户诉求） | ② |
| 五页面后台化 | `ui/page_{library,search,reading,graph,writing}.py` | ② |
| 批量下载进度回调 | `tools/definitions.py`：`t_batch_download(progress_cb=...)` | ② |
| 交互用例扩充 | `scripts/_apptest_ui_interact.py`（+262）、`scripts/_smoke_read.py`（+65） | 验证 |

## 二、补断链：writing 页半迁移态（本次引入的功能性缺陷）

验证评委独立核查发现：`ui/page_writing.py` 的「🤖 生成该图 Figure Caption」被改成 `bg_submit`，但**全文没有 `bg_consume` 调用**——`_consume_caption_auto` 是死代码，用户点击后任务照跑、结果永久丢失（「点了没反应」）。且该路径零测试覆盖（冒烟只渲染不点、单测不触 UI 交付链）。

父代处置——**完成迁移而非回退**（回退会浪费已写好的 5 个 worker + 5 个交付处理器，且违背用户诉求）：

1. `render()` 顶部接入 5 路 `bg_consume`（置于各 section 之前，交付写状态后即时重绘）；
2. 剩余 5 个内联 LLM 按钮全部后台化（论文框架 ×2 / Figure Caption 自动+自定义 / 拓扑图代码 / 学术翻译），全部用**默认参数快照**防闭包延迟读（`lambda progress, _df=df, ...` 模式，与 auto caption 一致）；
3. 框架生成的**缓存命中保留在主线程**即时返回（无 LLM 调用，不占并发队列）；strict 重生成不走缓存（与旧内联一致）；
4. 删除内联 `_gen`（含 `write_stream` 流式路径——后台无人观看增量输出，让位于任务中心进度）；
5. docstring 改为事实陈述（4 类任务 6 按钮 + 缓存路径说明）。

踩坑记录：`_w_caption(..., idx=None, progress)` 位置参数跟在关键字参数后 → `SyntaxError`，compileall 当场抓住（`progress=progress` 修复）。**教训：lambda 多行调用里混用位置/关键字参数，编译检查是唯一便宜防线。**

## 三、修假绿：smoke_ui 故障注入阶段对后台化按钮全部静默失效

Round 19 后台化后，`_smoke_ui.py` 第 [4] 阶段 9 个故障注入案例里 **8 个打印「✗ 未降级」却不记入 problems**（记账条件写成 `if not ok and friend`，而「无异常也无友好提示」恰恰是降级失效最典型的形态）→ 总结仍输出「✅ 冒烟通过」。这是记忆里「假绿」模式的第三次变体。

修法（`scripts/_smoke_ui.py`）：

1. 新增 `_click_bg_and_deliver()`：点击 → 检测新提交任务 → 轮询终态 → 驱动 `at.run()` 交付 → 收集提示；
2. 记账条件改为 `if not ok:` 即记；
3. `_drain_tasks()`：case 间排空进程级线程池（MAX_PARALLEL=2，不排空会让闸门断言假失败）。

**两个时序陷阱（实测踩到，动代码前必读）**：
- **内联路径不能补 `at.run()`**：点击轮已渲染结果，再跑一轮冲掉瞬态 `st.error`（case 7「版本差异」因此从「有」翻成「无」）；
- **后台交付要逐轮取并集**：flash 是「渲染即 pop」，第一轮驱动 run 交付时出现，第二轮就被 `render_flash` 清空（一次性探针实证：第 1 轮 `at.error` 有完整降级文案，第 2 轮为空）。

修后实测：**9/9 全部「友好提示=有 OK」**（真绿，非永真）。

## 四、补回归：writing 页 AppTest 交互用例（case 8）

针对「断链 bug 因零测试覆盖而漏网」，在 `scripts/_apptest_ui_interact.py` 新增 `case_writing_background`：载入示例 CSV → 事件闸门打桩 `figure_caption`/`llm_available` → 点击 → 断言三态（提交登记 / 闸门禁用+运行中提示 / 交付落点 `auto_captions[0]` 持久写回）+ 闸门恢复。

**反向验证（新断言必须证明它会失败）**：临时注掉 `bg_consume` 复刻断链 → case 8 精确红在「结果落点：auto_captions 未写回：{}」→ 还原后复跑全绿。该测试确实钉死了这个 bug。

## 五、PDF 适配实证审计（需求①的「扩大适配范围」）

自建 10 例边缘电池 + 真实语料回归（探针一次性，已删；结论如下）：

| 用例 | 结果 | 判定 |
|---|---|---|
| 仅所有者密码（空用户密码） | ✓ 完整解析 | R19 加固生效（open_doc 补 `authenticate("")`） |
| 头部垃圾前缀 | ✓ | MuPDF 自愈 |
| 尾部垃圾 | ✓ `is_repaired=True` | MuPDF 修复 |
| 中间夹纯图页（6 页中 1 页 0 字符） | ✓ 入库 + `truncated=True` warning | 正确（5/6=83% ≥70%） |
| 连字密集文本 | ✓ 2339 字符正常 | 无乱码 |
| 90° 旋转页 | ✓ | 无碍 |
| 中部截断（2/3 处切） | ✓ 修复开 10 页、9 页有文 → warning 放行 | 合理（不静默丢，标记 truncated） |
| 真用户密码 | ✗ 「PDF 已加密，需要密码」reason=encrypted | 正确拒收 |
| 全文 26 字符的稀疏文档 | ✗ 「有效文本过少」reason=truncated | 正确拒收 |
| 真实演示 PDF（1706.03762v7 / 1810.04805v2） | ✓ 15 页 40K 字 / 16 页 64K 字 | 回归干净 |

**结论**：截图中的 minhash 崩溃（会让**所有** PDF 解析 100% 失败且报错指错方向，用户观感即「有些论文不适配」）已修复；R19 加固覆盖了真实缺口（空密码 PDF、栏感知空文本回退）；**余下拒绝均为设计正确行为**。两项政策边界如实告知用户，不做单方面调整：① 70% 截断阈值是「不静默入库残篇」的刻意权衡；② 扫描件需要视觉模型 Key（无本地 OCR 引擎，属能力边界而非缺陷）。

## 六、门禁与提交

| 门禁 | 结果 |
|---|---|
| compileall（core services tools ui app.py config.py） | OK |
| unittest discover | **190/190 OK**（+9：minhash 回归等；本轮 writing 改造未新增单测——交付链由 AppTest case 8 覆盖） |
| `_smoke_ui.py` | 9 页 0 异常；**故障注入 9/9 真绿**（修复前 8/9 假绿） |
| `_smoke_read.py` | 61/61 |
| `_apptest_ui_interact.py` | 8/8（新增 case 8，含反向验证） |
| `_check_doc_refs.py` | SUSPECT 0 |
| `make_tech_pdf.py`（官方命令） | 30 页 |

提交分组（按纪律显式列文件、不 `git add -A`、不碰 campaign 遗留的 `docker-compose.yml`）：
- 提交 A（PDF 适配）：`services/parsing.py`、`services/library.py`、`tests/test_parsing_library.py`
- 提交 B（后台任务化收口）：`ui/common.py`、`ui/page_*.py`（5）、`tools/definitions.py`、`scripts/_apptest_ui_interact.py`、`scripts/_smoke_read.py`、`scripts/_smoke_ui.py`、`docs/review/round19_bg_graph.md`、本文件
- 文档计数同步（181→190，字符等长不动页数）：`docs/technical_doc.md`、`README.md`

## 七、明确声明：没有验证什么

1. **真实多任务并发未压测**：`services/tasks.py` 的 `_MAX_PARALLEL = 2`（LLM 服务端 >4 并发实测触发 429，故设 2），第 3 个任务排队、任务中心可见「排队中」。「任何板块的任务可同时运行」的结构性前提（全会话共享池 + 跨页提交/交付）已就位并经 AppTest 三态验证，但**同时跑 3+ 个真实长任务的互不阻塞未在真实环境压测**；
2. **真实 LLM 路径**：writing 页 4 类任务的 ok 档只做了打桩验证（case 8）；降级档由 consume 处理器话术 + 既有横幅承载，未端到端构造伪响应；
3. **真机桌面版**：两个桌面侧修复（GPU 白屏、侧边栏展开）仍待用户侧确认（Job 内无法起 pywebview 壳，见 round13 两份报告）；
4. **用户原始失败 PDF（截图中的 121.pdf）未取得**：适配审计用合成电池 + 仓库语料替代；若用户仍遇具体论文失败，需要那份 PDF 做 targeted 复现。

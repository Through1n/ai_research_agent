# Round 19：page_graph.py（板块 5 引用图谱与综述）长任务后台化复盘

- 执行人：bg-graph（round13 团队）
- 日期：2026-10-22
- 文件独占：`ui/page_graph.py`（唯一改动文件；未碰 ui/common.py、ui/sidebar.py、services/tasks.py、其他 page_*.py、app.py、tests/、scripts/、docs/technical_doc.md、README.md）
- 父代任务背景：用户要求「0 到 6 的板块中的任务任何一项都是可以同时运行的，而不只是 0 板块」。父代已落地公共层 `bg_submit / bg_consume / bg_running`（ui/common.py）并以 `ui/page_library.py` 给出参考实现，本文件为板块 5 的接入。

---

## 一、改造清单

| # | 任务（按钮） | 改造方式 | kind | 结果落点（与改造前一致） |
|---|---|---|---|---|
| 1 | 🕸️ 构建引用图谱 | 后台化：worker `_build_worker` + 交付 `_consume_build` | `graph_build` | `st.session_state.graph_pack` |
| 2 | 📝 一键生成领域综述 | 后台化：worker `_review_worker` + 交付 `_consume_review` | `graph_review` | `st.session_state["review"]` |
| 3 | 🚀 Future Work 前沿扫描（排除已解决） | 后台化：worker `_future_worker` + 交付 `_consume_future` | `graph_future` | `st.session_state["future"]` |
| 4 | 🎭 演示模式示意图谱 | **保持内联**（理由见第四节） | — | `st.session_state.graph_pack` |

新增/变更的函数（均在 `ui/page_graph.py`）：

| 函数 | 类型 | 说明 |
|---|---|---|
| `_build_worker(ctx, progress)` | 新增（替换原 `_build_real(ctx, bar, note)`） | 建图 + 指标 + 分类 + `g.graph_html` + pack 组装，全部在 worker 线程完成并返回 pack |
| `_degrade_tier(text)` | 新增 | 纯文本降级分档（degraded/offline/ok），无 st.*，可在 worker 线程调用 |
| `_review_worker(ctx, G, progress)` | 新增 | 综述生成 + 降级判定，返回 `{"review", "tier"}` |
| `_future_worker(ctx, G, progress)` | 新增 | 前沿扫描 + 降级判定，返回 `{"future", "tier"}` |
| `_consume_build(ctx, kind, t)` | 新增 | 构建交付：写回 graph_pack，成功/空边/失败三档 flash |
| `_consume_review(ctx, kind, t)` | 新增 | 综述交付：写回 review，按档位 flash |
| `_consume_future(ctx, kind, t)` | 新增 | 前沿交付：写回 future，按档位 flash |
| `_render_pack(ctx, pack)` | 修改（仅 HTML 失败分支） | pack 内 `html_error` 标记随包持久化展示 |
| `render(ctx)` | 修改 | 顶部 `bg_consume({...})`；三个提交按钮加 `disabled=bg_running(kinds={...})` + 运行中 caption + 提交 toast |
| `_build_real` | **删除** | 唯一调用点（构建按钮）已改后台化，避免死代码 |

`_build_demo`（演示模式建图）、`_make_pack`、`_metrics_rows`、`_title`、`_init` 未改。

## 二、保留的状态语义（写入时机）

- `graph_pack`：改造前在按钮点击当轮写入；改造后由 `_consume_build` 在任务完成（done/error 均一次性交付）当轮写入。键名、内容结构（html / html_error / metrics / corner / edge / edges / nodes / demo / G）与改造前等价（新增 `html_error` 一个标记键，仅 HTML 生成失败时非空）。
- `review`：改造前在点击当轮写入；改造后由 `_consume_review` 写入。综述展示区（`long_block` + 降级横幅复查）逻辑未动，每次渲染仍会复查档位。
- `future`：同上，由 `_consume_future` 写入。
- 图包 G 的复用语义：改造前点击综述/前沿按钮时读 `session_state["graph_pack"]`，`G is None or demo` 时线程内重建；改造后在**主线程提交时**同样读一次并闭包传给 worker（worker 线程禁止访问 st.session_state），`demo` 或缺失时传 None、由 worker 自建。分支等价。
- 前置检查：`llm_guard(ctx, ...)` 保留在主线程提交前（按钮点击回调内），未配置 Key 时仍当场给中文引导、不提交任务。

## 三、降级档位在后台化后如何呈现

原内联逻辑的两档降级（占位响应 / 离线确定性生成）+ error 态全部保留，且**绝不以成功态呈现**：

1. **worker 线程内判定**：`_degrade_tier` 用 `is_llm_degraded` / `is_llm_offline_generated`（均为纯函数，无 st.*）判定档位，随结果返回。
2. **交付处理器按档位 set_flash**：
   - `degraded`（占位响应）→ `set_flash("error", "⚠️ …未生成：大模型暂时不可用，本次没有生成任何真实结果（离线占位响应，请勿作为答案引用）…")`；
   - `offline`（确定性生成）→ `set_flash("warning", "⚠️ 大模型不可用：…由本地确定性算法生成，不是大模型输出（内容取自本地真实数据，未编造）…")`，保留原 `llm_offline_generated_banner` 的话术要点；
   - `ok` → `set_flash("success", "✅ 领域综述已生成" / "✅ 前沿方向扫描完成")`。
3. **不依赖 flash 单槽位的持续呈现**：综述/前沿内容区每次渲染都会复查 `is_llm_degraded / is_llm_offline_generated` 并渲染 `llm_degraded_banner() / llm_offline_generated_banner()`（原逻辑未动）——即使同一轮交付多个任务导致 flash 只保留最后一条，降级横幅仍随内容持续可见。
4. **error 态**：`t["state"] == "error"` 时 `set_flash("error", …)`，保留原 `friendly_error` 的原因/动作话术要点（可能原因 + 怎么办），并附任务中心捕获的原始错误前 150 字符；不写任何状态键。
5. **图谱 HTML 生成失败**（原 `friendly_error("图谱 HTML 生成", …)`）：worker 内不可调用（含 st.*），改为 html 置空 + pack 内带 `html_error` 标记；由 `_render_pack` 随包持久化展示（warning + 可能原因 lib/vis-9.1.2 可视化库文件缺失 + 怎么办检查 lib/ 目录完整性，表格版指标不受影响）。比原内联更持久（跨 rerun 仍可见）。

## 四、演示模式示意图谱为何保持内联

`🎭 演示模式示意图谱`（`_build_demo`）**未后台化**，理由：

1. **快**：仅对 ≤ 数篇论文各取 1200 字做本地 embedding/关键词重合度建边 + networkx 指标，无 LLM、无网络等待，实测量级为亚秒~数秒；不属于「跑一个任务冻结整个 UI」的痛点场景。
2. **本地、无外部依赖**：不经过任务中心的并发配额（MAX_PARALLEL=2），不会挤占构建/综述/前沿的后台队列。
3. **原有 flash + rerun 收尾已完备**：演示构建完成后 `set_flash("success", …)` + `st.rerun()`，规避「尚未构建图谱」空状态与新图谱并存的矛盾；失败走 `friendly_error`，话术完整。
4. 父代任务书明确允许「可以保持内联，但要在复盘文档说明理由」——取低风险选项，把改造面收缩到三个真正的长任务上。

## 五、风险项

1. **G 对象的跨线程读**：综述/前沿 worker 与主线程 `_render_pack`（`g.review_structure`）会并发读同一个 networkx G。已核实 `services/graph.py` 中 `G.add_*` 仅出现在 `build_graph` 内，analyze/classify/review_structure/generate_review/explore_future 均为只读（grep 实证），并发读安全。
2. **flash 单槽位**：同一轮交付多个任务时只有最后一条 flash 可见。综述/前沿/图谱的重要结论均已同时写入页面状态（review/future 文本 + 内容区降级横幅、graph_pack + 持久化 html_error），不依赖 flash 传递关键信息。
3. **提交时刻与完成时刻的图包可能不同**：用户在构建任务运行中又点综述，worker 拿到的是提交时刻的旧 G（或 None 自建）——与改造前「点击时读 session_state」的行为完全一致，非新风险。
4. **worker 内异常不外抛**：所有异常由任务中心捕获为 error 态并经 flash 人话呈现；交付处理器自身异常由 `bg_consume` 统一兜底（set_flash error），不会白屏。
5. **演示模式仍会短暂冻结 UI**：接受项（第四节理由），已向父代声明。

## 六、实际跑过的验证命令与输出

1. `C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m compileall -q ui/page_graph.py` → `COMPILE_OK`（exit 0）。
2. `C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m unittest discover -s tests` → `Ran 190 tests in 4.342s / OK`（与改造前基线 190/190 一致，无降低）。
3. worker 无 st.* 自查（AST 行域扫描三个 worker 函数体）：唯一命中均在 docstring 注释文字（说明约束），**无实际 st.\* 调用**。
4. 内存态冒烟（tests/test_graph.py 的 FakeCtx 语料，mock `_llm_text` 规避网络，session_state 打桩，**不落盘、不跑被禁的三个脚本**）全部通过：
   - `_build_worker` 真实跑通：4 节点 / 5 条真实引用边 / HTML 690,129 字节 / `html_error` 为空；progress 阶段序列 `🕸️ 解析引用关系(×5, 含 build_graph 回调比率) → 📊 图论指标计算 → 🧩 生成可视化与组装`；
   - `_review_worker`：`_llm_text` 失败桩 → tier=offline（确定性综述）；成功桩 → tier=ok；
   - `_future_worker`：失败桩 → tier=offline；
   - `_consume_build`：done → graph_pack 写回 + success flash「✅ 图谱构建完成：4 个节点 · 5 条真实引用关系」；error → 不写状态 + error flash 含原因/动作；
   - `_consume_review`：offline 档 → review 写回 + warning flash（非成功态）；`_consume_future`：error → error flash、不动 future；kind 不匹配直接返回；
   - 全链路 `bg_submit → bg_running 闸门恢复 → bg_consume 交付`：tid 登记/清除、graph_pack 写回均正确（bare 模式下 st.rerun 仅告警不抛，符合预期）。

## 七、明确声明：哪些没有验证

1. **真实浏览器交互未验证**：按钮 disabled 闸门、运行中 caption、st.toast、侧边栏任务中心进度条、完成后自动重绘的观感，需父代集成时统一跑 AppTest/交互脚本验收（按父代禁令，本人未运行 scripts/_smoke_ui.py、scripts/_smoke_read.py、scripts/_apptest_ui_interact.py）。
2. **真实 LLM 路径未验证**：综述/前沿的 ok 档（真实模型输出）只做了 mock 桩验证；degraded 档（占位响应）只做了 `_degrade_tier` 纯函数级验证，未构造端到端伪响应场景。
3. **graph_html 失败路径未端到端验证**：worker 内置空 + html_error 标记的逻辑已代码走查，但 `g.graph_html` 抛异常的分支未在冒烟中真实触发（该环境 graph_html 正常产出 690KB HTML）。
4. **性能未实测**：改造后综述任务的实际后台耗时、与板块 0 Agent 任务的真实并发互不阻塞，未在真实环境压测（父代集成时验证）。
5. 工作区中其他文件的改动（ui/page_library.py、ui/page_search.py、ui/common.py 等）为团队并行工作，与本人无关；本人只改 `ui/page_graph.py`，未 git commit（父代统一提交）。

# Round6 · 考生11 修复报告（评委E 两个 🟡 缺陷）

- 独占改动：`core/agent.py`（+185/-3）、`ui/page_reading.py`（+352/-2），合计 **532 增 / 5 删**
- `git diff --stat` 只出现这两个文件，未触碰 `core/planner.py / core/router.py / ui/page_agent.py / ui/common.py / services/* / core/llm.py / config.py`
- 环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（streamlit 1.63.0），自验脚本全部在 `C:/tmp/w11/`，未写项目 `storage/`

---

## 缺陷 1（🟡）：12 步计划只执行 8 步、2 步永久 pending，却照常输出"最终答案"

### 1.1 根因（三处叠加，实测确认）

用 mock LLM 构造 12 步计划（`C:/tmp/w11/repro_12steps.py`，修复前输出）：

```
计划步数：12｜LLM chat 调用次数：17｜全局迭代上限 GLOBAL_MAX_ITERS=16
  T1..T8  done    （各 2 次迭代 = 16 次，额度正好耗尽）
  T9      failed  note=本步未产出任何结论      ← 迭代额度已空，一步都没跑却被记 failed
  RX1     failed / RX1 failed / RX2 skipped   ← 失败触发动态规划，计划被重写
  RX3..RX6 pending note=（空）                 ← 计划膨胀到 16 步，超出 MAX_TASKS=12 的尾部永远轮不到
完成度统计： {'done': 8, 'failed': 3, 'skipped': 1, 'pending': 4}
最终答案：本步结论：训练使用八块 P100 GPU【1】。      ← 无任何告警
```

根因链条（与评委 E 给出的行号完全对应，并多找到一环）：

| # | 位置 | 问题 |
|---|---|---|
| ① | `core/agent.py:33`（原）`GLOBAL_MAX_ITERS = max(6, AGENT_MAX_ITERS*2)` = **16** | 上限与计划步数**无关**。12 步 × 每步 2 次迭代 = 24 > 16，第 9 步起额度为空 |
| ② | `core/agent.py:177-179`（原）`if counters["iters"] >= GLOBAL_MAX_ITERS: trace.thought(...); break` | 截断后**既不调模型也不调工具**，却仍被 `_execute_task` 尾部逻辑判为 `failed`（"本步未产出任何结论"）——把"没执行"伪装成"执行失败"，UI 与最终答案都看不出是预算问题 |
| ③ | `core/agent.py:435-439`（原）`tasks = tasks[:idx+1] + new_tail` | 失败触发 `replan()` 后计划膨胀到 16 步，而主循环条件是 `while idx < len(tasks) and idx < MAX_TASKS`（`MAX_TASKS=12`），**超出 12 的尾部永远拿不到执行机会 → 永久 pending**（这正是评委实测里的 `T10R1R3 / T10R1R4`） |
| ④ | `core/agent.py:448-497`（原）综合阶段 | 完全没有"完成度核对"，直接流式生成最终答案，漏做的步骤被静默吞掉 |

### 1.2 修复方案（4 条，全部落在 core/agent.py）

**① 迭代上限随计划步数伸缩 + 可配置**（`core/agent.py:33-58`）

```python
GLOBAL_MAX_ITERS = max(6, AGENT_MAX_ITERS * 2)          # 保留旧常量（向后兼容）
HARD_MAX_ITERS = max(GLOBAL_MAX_ITERS, MAX_TASKS * STEP_MAX_ITERS)   # 硬天花板 48

def global_iter_cap(n_tasks):
    env = str(os.getenv("AGENT_GLOBAL_MAX_ITERS", "") or "").strip()
    if env:  return max(1, int(env))                    # 环境变量可显式覆盖（评测/运维可收紧）
    return min(max(GLOBAL_MAX_ITERS, n * STEP_MIN_ITERS), HARD_MAX_ITERS)
```
12 步计划 → 上限 16 → **24**（`12 × STEP_MIN_ITERS`），默认配置下计划的每一步都能拿到执行机会；`run()` 里 `cap = global_iter_cap(len(tasks))`（`core/agent.py:474`），并把预算打点到思考时间线（`:476`）。

**② 被截断的步骤如实记为 `pending`，不再伪装成 `failed`**（`core/agent.py:205-209`）

```python
if counters["iters"] >= cap:
    trace.thought(f"⏹ 已达全局迭代上限（{cap} 次），子任务 {task.get('id')} 未执行，"
                  f"直接进入下一步/综合阶段（该步结论不计入证据）。")
    return "pending", "", [], f"未执行：达到全局迭代上限（{cap} 次）", last_obs
```
`_execute_task` 新增 `global_cap` 参数（`:178`，默认退回模块常量，签名向后兼容）。

**③ 动态规划尾巴按 `MAX_TASKS` 裁剪，消灭"结构性永久 pending"**（`core/agent.py:555-570`）

```python
room = max(0, MAX_TASKS - (idx + 1))
dropped = max(0, len(new_tail) - room)
if dropped:
    trace.thought(f"⏹ 动态规划产出 {len(new_tail)} 步，超出单轮步数上限"
                  f"（MAX_TASKS={MAX_TASKS}），已省略末尾 {dropped} 步"
                  f"（这些步骤不会进入计划，也不会被算作已完成）。")
new_tail = new_tail[:room]
```

**④ 收尾完成度核对 + 最终答案告警**（`core/agent.py:576-606`、`:647-651`、`:276-368`）

- `:576-594` 循环结束后，凡未拿到终态的步骤统一 `status="pending"` 并写明原因（迭代上限 / 步数上限），**绝不假标 done**
- `:596-606` `audit = plan_completion(tasks)` → `trace.plan_audit`（UI/后续 agent 可读），`trace.guard(warn)` 上屏
- `:610-623` 综合提示词追加约束：告知模型"失败 N 步 / 跳过 N 步 / 未执行 N 步，这些步骤**没有任何证据**，严禁写进答案"
- `:647-651` 最终答案**开头**追加告警块（不是只在 trace 里，评委直接在答案区可见）

新增两个纯函数（可单测、可复用）：
- `plan_completion(tasks)`（`:276`）：统计 `done/failed/skipped/pending` 四类 + id 列表 + (id, title, note) 明细 + `complete` 标志；`running`/未知状态一律并入 `pending`（"没产出结论"≠"完成"）
- `_completion_warning(audit)`（`:317`）：生成告警文案

### 1.3 实测证据（`C:/tmp/w11/verify_agent_w11.py`，10/10 断言通过）

**场景 1 —— 默认上限，12 步全部执行完（证明"上限内优先完成计划步骤"）**
```
【场景1 12步/默认上限】计划 12 步｜迭代上限 24｜实际迭代 24
   T1..T12  done  调用工具：search_literature
   完成度：total=12 done=12 failed=0 skipped=0 pending=0 complete=True
最终答案：本步结论：训练使用八块 NVIDIA P100 GPU【1】。      （无告警，正确）
```

**场景 4 —— 复刻评委的"预算不足"现场（`AGENT_GLOBAL_MAX_ITERS=10`）**
```
【场景4 12步/上限10（纯截断）】计划 12 步｜迭代上限 10｜实际迭代 10
   T1..T5   done
   T6..T12  pending  未执行：达到全局迭代上限（10 次）
   完成度：total=12 done=5 failed=0 skipped=0 pending=7 complete=False
   pending_ids=['T6','T7','T8','T9','T10','T11','T12']
```
最终答案（真实原文，开头即告警）：
```
⚠️ 本次任务共 12 步，已完成 5 步、失败 0 步、跳过 0 步、未执行 7 步（未执行：步骤 T6、T7、T8、T9、T10、T11、T12）。以下答案基于已完成的 5 步生成，可能不完整。
- ⏳ 未执行 · T6「第6步：收集子问题证据」（未执行：达到全局迭代上限（10 次））
- ⏳ 未执行 · T7「第7步：收集子问题证据」（未执行：达到全局迭代上限（10 次））
...（T8~T12 同）...
- ℹ️ 原因：本轮全局迭代上限 10 次已用尽（实际 10 次），剩余步骤未获得执行机会。

---

本步结论：训练使用八块 NVIDIA P100 GPU【1】。
```

**场景 2 —— 失败 + 跳过 + 重规划同现（四类状态齐全）**
```
完成度：total=9 done=1 failed=3 skipped=5 pending=0 complete=False
⚠️ 本次任务共 9 步，已完成 1 步、失败 3 步、跳过 5 步、未执行 0 步。以下答案基于已完成的 1 步生成，可能不完整。
- ❌ 失败 · T1「第1步：收集子问题证据」（工具 no_such_tool_x 越权）
- ❌ 失败 · RX2「重规划步骤2：换工具重试」（工具 no_such_tool_x 越权）
- ⚠️ 跳过 · RX3「重规划步骤3：换工具重试」（依赖任务 RX2 未成功完成，本步跳过）
...
```

**场景 3 —— 第 10 步失败触发重规划（默认上限）**
```
完成度：total=12 done=11 failed=1 skipped=0 pending=0 complete=False
⚠️ 本次任务共 12 步，已完成 11 步、失败 1 步、跳过 0 步、未执行 0 步。...
- ❌ 失败 · T10「第10步：收集子问题证据」（工具 no_such_tool_x 越权）
```
计划不再膨胀出 `MAX_TASKS`（无永久 pending），但失败步骤仍被如实写进告警。

**断言汇总**：`断言通过 10/10`，其中含 `无造假：pending 未被改写成 done`（四类计数之和 == total）。

---

## 缺陷 2（🟡）：引用跳转视图无持久化，任何 rerun 都会销毁整个 iframe

### 2.1 根因

`ui/page_reading.py` 原实现把 `citation_links_html()` 的 iframe **只渲染在 `if st.button("🔗 生成双向跳转视图")` 分支里**（原 `:1067`）。Streamlit 每次 rerun 都重跑整个脚本：按钮的"按下"状态只存活一次，因此

1. 任何 rerun（切 tab、点别的按钮、甚至只是输入框回车）都会让 iframe **彻底消失**，跳转视图要重新点一次生成；
2. 跳转目标 `ref-N` / 反向循环游标 `data-idx` / 滚动位置全部只存在于**那个被销毁的 iframe DOM** 里，Python 侧完全不知情 → 评委所说"'无损'仅单次交互成立"。

### 2.2 修复方案（只加不改，考生7 的双向跳转逻辑一行未动）

**① 5 个 session_state key（`ui/page_reading.py:733-738`）**

| key | 内容 |
|---|---|
| `read_cite_view` | 视图快照 `{id(doc_id), start, end, stats, built}` —— rerun 后据此重建 iframe |
| `read_cite_jump` | 最近一次跳转目标 `{kind, ref, idx, target:"ref-12"/"cit-12-3", ts}` |
| `read_cite_cursor` | 反向循环游标 `{"12": 3}`（第 12 条目前停在第 3 处） |
| `read_cite_scroll` | 阅读位置（Python 侧兜底种子） |
| `read_cite_autoscroll` | **一次性**恢复标记，消费即清空 |
| `read_active_tab` | 最近一次操作的标签序号 |

配套 helper（`:741-901`）：`_ensure_cite_state / _cite_activate_view / _cite_view / _cite_jump_target / _cite_set_jump / _cite_cursor_next / _cite_consume_autoscroll / _cite_ref_total / _cite_clear_jump / _scroll_seed / _inject_restore_js / _restore_tab_js`。

**② rerun 后按快照自动重建 iframe**（`:1445-1467`）

```python
if (not built_now) and refs:
    snap = _cite_view(doc_id)          # doc_id 不一致（换了 PDF）自动失效，不串档
    if snap:
        ... citation_links_html(body_pages, refs) ...
        html2 = _inject_restore_js(html2, auto_target, _scroll_seed())
        embed_html(html2, height=780, scrolling=True)
        st.caption(f"♻️ 跳转视图已按上次状态重建（正文 p{s_i}–p{e_i}，建于 {snap.get('built')}）…")
```

**③ Python 侧跳转控制条（跳转天然走 session_state）**（`:1345-1384`）：`跳转编号` + `➡️ 跳到第 n 条`（正向 `ref-N`）+ `↩ 回到正文（循环）`（反向 `cit-N-k`，游标 1..m 循环）+ `🧹 清除跳转状态`，并常驻显示"📌 当前跳转状态（已持久化）"。

**④ 一次性消费的 autoscroll 恢复**（`:1386-1387`、`:1417`、`:1449`）

```python
auto_target = _cite_consume_autoscroll()   # 读取后立刻清空 → 下次 rerun 不再强制滚动
html = _inject_restore_js(html, auto_target, _scroll_seed())
```
`_inject_restore_js()` 把恢复脚本插到 `</body>` **之前**，不改动 `services/translate.py` 生成的任何锚点。恢复脚本 `_CITE_RESTORE_JS`（`:904-982`）做三件事：
1. 恢复左右两栏 `scrollTop`（阅读位置不丢）；
2. **一次性**定位并高亮跳转目标（`scrollIntoView` + `.hit` 闪烁）：优先用 Python 传下来的 `PY`，其次用 iframe 内点击写入 `localStorage` 的 `pending`，恢复后立刻把 `pending` 置 false；
3. 记录 iframe 内的点击（`.cit` → `ref-N`；`.back` → `cit-N-idx`）与滚动位置到 `localStorage`，**这样即使跳转发生在 iframe 内部（不经过 Python），下一次 rerun 重建 iframe 后也能自动恢复**——修复评委原话里"点跳转到 References 后 rerun 就丢"的场景。

**⑤ 标签恢复（尽力而为）**（`:1469`、`_RESTORE_TAB_JS` `:984-1000`）：只在"刚跳转后的下一次 rerun"注入一次，尝试把父页面标签切回「引用跳转」；Streamlit 无官方 API，用 `window.parent.document` 匹配 tab 文本点击，全程 try/catch，失败静默。

### 2.3 实测证据

**（a）AppTest 会话级验证（真实阅读页，`C:/tmp/w11/verify_cite_persist.py`，PASS 26 / FAIL 0）**
```
=== Part B：真实阅读页 AppTest（1706.03762v7.pdf） ===
  ✅ 点击「生成双向跳转视图」无异常
  ✅ 视图快照写入 session_state | {'id': '1706.03762v7.pdf|15', 'start': 1, 'end': 9,
        'stats': {'pages': 9, 'markers': 51, 'linked': 50, 'unlinked': 1, 'occurrences': 63, 'refs': 40, ...}}
  ✅ 跳转目标写入 read_cite_jump | {'kind': 'ref', 'ref': 1, 'idx': 0, 'target': 'ref-1', 'ts': '21:35:21'}
  ✅ autoscroll 标记已被一次性消费（读后清空） | ''
  ✅ rerun 后跳转目标仍存活 | {'kind':'ref','ref':1,'target':'ref-1',...}
  ✅ rerun 后视图快照仍存活 | {'id': '1706.03762v7.pdf|15', ...}
  ✅ rerun 后视图被自动重建 | ['♻️ 跳转视图已按上次状态重建（正文 p1–p9，建于 21:35:20）：rerun / 切换标签后不再丢失…']
  ✅ 反向循环游标写入 session_state | {'1': 1}
  ✅ 点「🧹 清除跳转状态」后游标清空 | {}
```
Part A（迷你 App 直驱 helper）额外验证了一次性消费与循环语义：
```
  ✅ 跳转当次 rerun：iframe 拿到 autoscroll=ref-12
  ✅ 一次性消费：读完即清空标记（不会反复滚动）
  ✅ rerun 后跳转目标仍存活 / autoscroll 为空（不重复强制滚动）
  ✅ 反向循环游标 1→2→3→1（循环语义保留） | ["{'7': 1}", "{'7': 2}", "{'7': 3}", "{'7': 1}"]
```

**（b）JS 层语义（Node 22 + 手写 DOM 桩，`C:/tmp/w11/sim_restore.js`，9/9）**
```
  ✅ Python 一次性目标生效（scrollIntoView ref-12）
  ✅ 恢复后 pending 被清掉（不会反复滚动）
  ✅ pending 目标在重建后被恢复一次｜滚动位置同步恢复（body=300 / refs=500）
  ✅ 二次 rerun 不再强制跳转（不打扰用户）
  ✅ iframe 内点击 [n] 被记为待恢复目标 ref-12｜下次 rerun 恢复该点击目标｜恢复后再次 rerun 不再重复跳转
```
两段注入 JS 均通过 `node --check` 语法校验。

**（c）未破坏考生7 的双向跳转（`C:/tmp/w11/verify_cite_anchors.py`，1706.03762v1.pdf）**
```
References 条数：36｜引用出现 85 处
注入前： {'href目标': 36, 'ref锚点': 36, '死链': [], '正文锚点 cit-N-k': 85, '反向按钮': 36}
注入后： {'href目标': 36, 'ref锚点': 36, '死链': [], '正文锚点 cit-N-k': 85, '反向按钮': 36}
✅ 锚点配对未被破坏：36/36 成对、死链 0、反向按钮 36/36
```
（`services/translate.py` 未被改动，注入脚本只在 `</body>` 前追加一个 `<script>` 块，锚点集合逐一比对完全一致。）

---

## 自验复跑结果

| 命令 | 结果 |
|---|---|
| `python -m py_compile core/agent.py ui/page_reading.py` | 零报错（OK） |
| `python -m unittest discover -s tests` | **Ran 18 tests … OK**（18/18，无回归） |
| `python scripts/_smoke_read.py` | **PASS 59 / FAIL 0** |
| `python scripts/_smoke_ui.py` | 9 个页面 + 9 项故障注入 **全部 exception=0**，`✅ 冒烟通过：全部页面 0 未捕获异常` |
| `C:/tmp/w11/verify_agent_w11.py` | 断言 **10/10** |
| `C:/tmp/w11/verify_cite_persist.py` | **PASS 26 / FAIL 0** |
| `C:/tmp/w11/verify_cite_anchors.py` | 36/36 锚点，注入前后完全一致 |
| `C:/tmp/w11/sim_restore.js`（node --check + DOM 桩） | 语法 OK + 语义 **9/9** |

---

## 风险点（诚实写）

1. **默认上限从 16 提到 `max(16, 2×步数)`（硬顶 48）**：12 步计划的实际 LLM 调用从 17 次涨到 25 次（实测），耗时/费用上升。已加 `AGENT_GLOBAL_MAX_ITERS` 环境变量可一键收紧，也保留了 `HARD_MAX_ITERS=48` 硬顶；但**默认行为确实变贵了**，若评委按"调用量"扣分，这是我主动做的取舍（宁可多跑，不可静默漏做）。
2. **`MAX_TASKS=12` 不能改**（`core/planner.py` 非我独占）：我选择"把重规划尾巴裁到放得下"，副作用是**被裁掉的候选步骤不会出现在计划里**（只在 trace 里写明"已省略 N 步"）。相比"永远 pending"，我判断这更诚实，但它确实让计划的可见步数变少。
3. **被截断步骤的状态从 `failed` 改成 `pending`**：任何依赖 `status=="failed"` 的下游逻辑（别的 agent 若写了此类判断）行为会变。`_deps_failed()` 仍按 `failed/skipped` 判定，未受影响；`ui/page_agent.py` 的 `pending` 图标（⏳ 待执行）已存在，显示正常。
4. **`localStorage` 桥接依赖 Streamlit iframe 的 `allow-same-origin`**（实测 `IFrameUtil` 的 sandbox 列表确实包含它）。若未来 Streamlit 收紧沙箱，iframe 内点击的跳转就无法回传 Python，此时**只有走 Python 控制条（➡️/↩ 按钮）的跳转能持久化**——这条路径不依赖 localStorage，仍然有效。
5. **标签恢复 `_RESTORE_TAB_JS` 是 DOM 猜测**：依赖 `button[data-baseweb="tab"]` 与文本匹配，Streamlit 改版即失效。已限定"仅在刚跳转后的下一次 rerun 注入一次 + try/catch 静默"，失效时不影响其余功能；**未做真实浏览器验证**（约束不允许起服务）。
6. **未在真实浏览器里跑过端到端**：JS 行为用 `node --check` + 手写 DOM 桩验证逻辑，ScrollIntoView/高亮的实际观感未验证；Python 侧 session_state 语义已用 AppTest 全量验证。
7. `_scroll_seed()` 目前只能提供 `{0,0}` 兜底——Python 侧拿不到 iframe 内的真实滚动值（`components.html` 是单向通道），真实滚动位置由 iframe 内 JS 写 `localStorage` 持久化，属于能力边界内的最优解。

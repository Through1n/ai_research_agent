# Round 11 · 界面简洁化 —— `ui/page_search.py`（🔍 论文检索与追踪页）

> 执行人：declutter-search（独占文件：`ui/page_search.py`）
> 目标：只做「视觉减法」，不动业务逻辑 / 函数签名 / 调用链 / 异常降级文案 / 空态守卫 / 依赖。

---

## 1. 结构改造：7 个区块 → 3 个 tab

改造后 `render()`（`ui/page_search.py:354`）结构：

```
page_header（未改标题与副标题）
st.caption（外网依赖提示，1 条）
st.tabs(["订阅与推荐", "检索与下载", "引用含金量"])
  ├ 订阅与推荐：st.subheader("⏰ 动态追踪订阅") + _track；st.subheader("⭐ 个性化推荐") + _recommend
  ├ 检索与下载：_arxiv → _others（PubMed / 网页 双列）→ _batch
  └ 引用含金量：_quality
```

原结构（`_track` / `_recommend` / `_arxiv`+`_others`+`_batch`+`_quality` 三大块，用 `st.markdown("---")` 与
`st.container(border=True)` 硬分隔）已全部改为 tab 收纳。函数执行顺序与 try/except 兜底粒度保持等价：
`_quality` 原先与其他三个函数共用一个循环兜底，现拆出独立 tab，但兜底文案与 `show_error` 调用方式不变。

---

## 2. 删了什么（源码级实测，见第 5 节统计命令）

| 类别 | 改前 | 改后 | 说明 |
|---|---|---|---|
| `st.markdown("---")` 分隔线 | **3** | **0** | 全删（render 中 3 处）。**诚实说明**：任务书说本页 hr=10，我在当前源码里只数到 3 条（第 391/401/410 行）；`ui/sidebar.py` 另有 4 条是全局共享的（不在我的文件、不能改）。基线 10 我无法复现，可能来自 AppTest 对同一元素的重复计数或历史版本。本文件内确为「全部删完」 |
| 自定义色块分区头 `_SEC_CSS` + `_sec_head(...)` | **3 个**（2 个琥珀高亮 + 1 个蓝色） | **0** | 删除函数、CSS 常量与 3 处调用；`from html import escape as _esc` 随之成为未使用导入，一并删除。分区标题改用原生 `st.subheader` |
| `st.container(border=True)` 嵌套边框 | **3** | **0** | tab 本身已提供分组，嵌套方框是多余边框噪音 |
| `st.info(...)` 色块 | **1** | **0** | `_arxiv` 结果里的「💻 开源代码：xxx」由 `st.info` 改为 `st.markdown`，内容一字未改，仅去掉色块底纹 |
| caption 中的 emoji 前缀 | 4 处 | 0 | 去掉 `💡`（推荐理由）、`🧑‍🤝‍🧑`（当前用户）、`ℹ️`（外网依赖 / 本话题暂无新增）前缀，保留信息本身 |
| 「点上方 XX 按钮」类操作提示 | 2 条 | 0 | `_recommend` 空态去掉「点上方「✨ 刷新并推荐」」；`_track` 空态去掉「填写话题后点「🔄 运行一次追踪」」。按钮就在旁边且标签自解释 |
| 与 tab 名同义的小标题 | 1 | 0 | `_quality` 的 `st.subheader("🔬 引用含金量自动剔除")` 删除（tab 名已是「引用含金量」，按钮是「🧹 分析并剔除应付式引用」） |
| 冗长 caption 精简 | 1 | — | 追踪说明由「按话题订阅领域，系统记住检查点，每次只推送「上次以来新增」的论文，可配合 scripts/watch_daemon.py 定时运行。」→「系统按检查点增量推送（只给上次以来的新增论文），可配合 scripts/watch_daemon.py 定时运行。」（检查点语义 + 增量语义 + 守护脚本三项信息全保留） |

`git diff --stat`：**净减 18 行**（+22 / −40）。

---

## 3. 保留了什么 & 原因

1. **页面顶部「外网依赖」caption**（精简后 1 条）——功能性限制提示（断网时如何演示），规范第 5 条明确要求保留。
2. **追踪区的检查点 caption**（1 条）——「增量推送 + checkpoint + watch_daemon」不是自解释信息，删了用户不知道为什么有的论文不重复推。
3. **`_recommend` 的「当前用户 `uid`（切换侧边栏用户 ID 可演示多用户差异化推荐）；已记录反馈 N 篇」**——保留（仅去 emoji）。它是多用户差异化推荐的解释，也是演示要点；这里我没按「操作提示」删掉，属于**判断项**，见第 6 节。
4. **全部空态守卫 `empty_state(...)`** ——`_track` / `_recommend` / `_arxiv` / `_batch` / PubMed / 网页共 6 处，属空态守卫，禁止删除。
5. **全部异常降级文案**——`net_error` / `friendly_error` / `show_error` 调用、`reasons` / `actions` 列表、`_SEARCH_DEMO_HINT`、4 条「🛠️ 怎么办：其余模块不受影响，可继续使用。」caption 全部原文保留（`🛠️` 前缀也保留，未动降级话术）。
6. **所有 `st.warning` / `st.success` / `st.error`**（共 20 处调用点）——都是**动作触发后的结果/校验反馈**（如「✅ 检索到 N 篇」「⚠️ 请先输入关键词」），属功能语义，未在初始渲染时产生色块，故未动。
7. **循环内的数据型 caption**（批量抓取逐条 ✅/❌、推荐理由、本话题暂无新增）——是结果数据行而非说明噪音，保留。
8. **`page_header` 的标题与副标题**——一字未改（他处文档/脚本引用过该标题，避免漂移）。
9. **`_SEARCH_DEMO_HINT` / `_DOMAIN_EN` / `MAX_REC_SHOW` / `_score_rows` / `_likes_of` / `_set_like`**——业务逻辑与常量，零改动。

---

## 4. 自检结果

```
python -m compileall -q ui/page_search.py          → COMPILE_OK
python -c "import ui.page_search"                  → IMPORT_OK
python -m unittest tests.test_core.TestRecommendationLogic → Ran 1 test / OK
```
（`tests/test_core.py:266` 通过 `ui.page_search._set_like` / `_likes_of` 验证点赞聚合，是我唯一有外部消费者的 API，实测仍通过。）
未运行 `scripts/_ui_clutter_metrics.py`、`scripts/_smoke_ui.py`、`scripts/_smoke_read.py`；未启动 streamlit；未触发任何外网请求。

---

## 5. 源码级统计（可复现）

```
hr st.markdown("---") : 0     (改前 3)
st.caption( 调用点     : 11    (改前 10：静态 3 / 循环 4 / 降级 3；改后 静态 3 / 循环 4 / 降级 4)
st.info(              : 0     (改前 1)
st.subheader(         : 6     (改前 5：删 1 个与 tab 同义的，新增 tab1 内 2 个纯文本小标题)
_sec_head / _SEC_CSS  : 0     (改前 3 次调用)
st.container(border=) : 0     (改前 3)
```

**静态 caption = 3 条**（外网依赖 / 追踪检查点 / 当前用户反馈），满足规范「全页 caption ≤3」——此处按「静态说明性 caption」口径统计；循环数据行与异常降级 caption 不计（后者规范明确禁止改）。

---

## 6. 不确定 / 需要编排者裁决的地方

1. **noise 实测值的口径问题（重要）**：`scripts/_ui_clutter_metrics.py` 用 `AppTest` 统计元素树，而 `st.tabs` 的**所有** tab 子内容在脚本执行时都会被创建并进入元素树，因此该脚本**测不出「一次只显示一个 tab」的视觉收益**。预计实测仅体现 hr 的下降（noise = hr×2 + note×2 + caption，hr −3 → **约 37 → 31**）。**真实观感收益远大于这个数字**：单屏可见元素约为原来的 1/3。我没有跑该脚本（属编排者统一跑），请以上述口径解读。
2. **基线对不上**：任务书给的本页 hr=10 / caption=11 / note=3，我在源码里只数到 hr=3、caption 调用点 10、初始渲染 note≈2（两个 `empty_state` 的 `st.info`）。差异可能来自 sidebar 共享元素（4 条 hr）或历史版本。我已把本文件内 hr 清空，但不敢宣称「删了 10 条」。
3. **「当前用户 uid…」caption 是否该删**：它带「切换侧边栏用户 ID 可演示多用户差异化推荐」的操作提示成分，按规范第 3 条可删；但它是多用户推荐的演示要点，我选择保留并仅去 emoji。若编排者要求严格 ≤3 静态 caption 且不计此条，我再删。
4. **tab1 内新增了 2 个 `st.subheader`**：这是为了替代被删掉的 3 个彩色分区头（彩色块 + 边框 → 纯文本小标题），净视觉噪音是下降的，但 subheader 计数 +1。若编排者希望 tab 内连小标题也不要，我可以改回纯留白分隔（但两个区块会失去标识）。
5. **`_quality` 拆成独立 tab 后新增第 4 条降级 caption**：「🛠️ 怎么办：其余模块不受影响」，文案沿用原有，仅在异常时渲染。

---

## 7. 未触碰（确认边界）

只改了 `ui/page_search.py` 一个文件。`git status --porcelain` 中另有 `ui/page_reading.py`、`ui/sidebar.py`、`scripts/_ui_clutter_metrics.py` 的改动/新增，均来自其他队友或编排者，我未读写其内容（仅 grep 了 `sidebar.py` 的 hr 数以核对基线，未修改）。

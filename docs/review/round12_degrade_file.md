# 故障演练报告（文件 / 状态类 6 场景）

> 演练人：degrade-file　｜　日期：2026-10-13　｜　只读取证，**未修改任何业务代码**
> 驱动方式：`streamlit.testing.v1.AppTest` 真跑 `app.py`（解释器 `C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`，streamlit 1.63.0）
> 一键复现：`python scripts/_degrade_drill_file.py`（一次跑完 6 个场景，总耗时约 78s，已连跑 3 次结果稳定）

---

## 一、结论总表

| 场景 | 崩溃 | 提示文案（页面实际字符串，原样摘录） | 降级路径 | 演示风险 |
|---|---|---|---|---|
| **7 损坏 PDF**（截断 / 结构损坏） | 否（未捕获异常 0） | 页面出现 **2 个红框**，标题完全相同：`⚠️ 操作失败：PDF 解析。发生内部错误，请重试或换一种操作方式`；<br>两组原因/动作：`🩺 可能原因：PDF 损坏 / 加密 / 非标准排版；扫描件但未配置可用的多模态模型 Key` / `🛠️ 怎么办：换一个 PDF 重试；或先加载演示数据继续演示`、以及 `🩺 可能原因：PDF 文件损坏 / 加密 / 超大导致超时；向量库写入失败或磁盘空间不足` / `🛠️ 怎么办：重试一次；换一个体积更小的 PDF；或点击左侧「🚀 一键加载演示数据」继续演示`。<br>真因只在折叠区：`str: 打开 PDF 失败：Failed to open stream`（7a）、`str: 打开 PDF 失败：PDF 没有任何可读页面（0 页）`（7b） | 有：不入库，页面其余部分正常可用，可继续操作 | **高** |
| **7 附带：可修复的损坏**（头部被抹 / 92% 截断） | 否 | **完全没有提示**，直接走到查重：`⚠️ 语义查重命中：库中已有《1706.03762v1.pdf》，内容相似度 100.0%。` | 有（照常入库/查重） | 中 |
| **8 0 字节文件** | 否 | 同场景 7：**2 个红框**，标题同样是 `⚠️ 操作失败：PDF 解析。发生内部错误，请重试或换一种操作方式`；真因藏在折叠区：`str: 打开 PDF 失败：Cannot open empty stream.` | 有：不入库，页面其余可用 | **高** |
| **9 超过 100MB 的 PDF** | 否 | **没有任何体积相关提示或拒绝**，直接成功：`✅ 已入库：AI4S degrade drill line 0（20 页，0 个分块）`（"0 个分块"是本次演练为不污染真实库而隔离落盘导致的副产物，见下文说明） | 无（照常解析入库） | 中 |
| **10 上传后立即切页**（状态竞争） | 否 | 切走后目标页干净、无串页文案；再切回来旧对话框复活：`⚠️ 语义查重命中：库中已有《1810.04805v2.pdf》，内容相似度 100.0%。`；残留进度条 0 个 | 有：切走照常渲染，切回仍可继续决策 | 中 |
| **11 侧边栏 API Key 被清空** | 否（7 个页面逐页渲染，未捕获异常均为 0） | `⚠️ 尚未配置 API Key：请在下方「接入方式」选择平台并填入 Key；无 Key 也可使用底部按钮一键加载离线演示数据。`<br>`⚠️ 元数据智能抽取需要大模型，但未检测到可用 API Key。` + `🛠️ 怎么办：在左侧「接入方式」选择平台并填写 Key；无 Key 时仍可使用本地演示数据与离线功能（解析/检索/图谱检索式演示）。`<br>Agent 页：`⚠️ Agent 执行需要大模型，但未检测到可用 API Key。` | 有：7 页全部可渲染，离线功能可用 | 低 |
| **12 浏览器刷新**（后台任务） | 否 | 刷新后侧边栏：`✅ 演练任务1 — 已完成（用时 6 秒）`，展开后写的是 `结果已转入页面内容（历史记录 / 下载区）。`——但新 session 的 `agent_runs` 为 **0**，历史记录区仍是新手引导，内容并不存在 | 部分：运行中任务刷新后继续跑且能被新 session 领回（实测 `agent_runs=1`）；**已被消费过的任务结果永久丢失** | **高** |

风险含义：高 = 会出现"满屏红字 / 提示指向不存在的内容 / 折叠区露出 traceback"这类当场失分现象；中 = 不崩但有"点了没反应 / 静默放行"的观感问题；低 = 有明确人话提示且可继续演示。

> **重要**：上表是**修复前**的取证结论。degrade-fix 随后落地了修复，我已用同一脚本复验，结果见 **§0 修复后复验**。下表保留为"故障当时的真实表现"，不要拿它当现状。

---

## 〇、修复后复验（degrade-fix 落地修复后，用同一脚本重跑）

复验命令与上文完全一致：`python scripts/_degrade_drill_file.py`（EXIT=0，82.4s）。

| 场景 | 改前 | 改后实测 | 判定 |
|---|---|---|---|
| 7a 截断 / 7b 结构损坏 / 8 零字节 | **2 个红框**，标题"发生内部错误" | **1 个红框**，error 正文即为真因：`打开 PDF 失败：Failed to open stream` / `打开 PDF 失败：PDF 没有任何可读页面（0 页）` / `打开 PDF 失败：Cannot open empty stream.`（三条均**无** `⚠️` 前缀，见下方注） | ✅ 已修，真因直接出现在标题上 |
| 7a/7b/8 折叠区 | 有 `AttributeError: 'str' object has no attribute '__traceback__'` + 指向 `ui/page_library.py:231` 的源码 traceback | **折叠区整体消失**（该路径不再走 `show_error`，不再有 `🔍 查看技术细节` 与 `st.code`），源码路径彻底不会露出 | ✅ 已修 |
| 7a/7b/8 配套 caption | `🩺 可能原因：PDF 损坏 / 加密 / 非标准排版；…` | `🩺 可能原因：PDF 结构损坏 / 非标准排版；扫描件但未配置可用的多模态模型 Key` ＋ `🛠️ 怎么办：换一个 PDF 重试；或先加载演示数据继续演示` | ✅ 已按 `corrupt` 档细化 |
| 9 超大 PDF | 无任何体积提示，直接入库 | `⚠️ 这个文件有 105 MB，超过 100 MB 上限，已停止解析（现场解析会很慢且占用大量内存）。` + `🛠️ 怎么办：换一个更小的 PDF（建议 ≤ 100 MB）；或先点击左侧「🚀 一键加载演示数据」继续演示，其余功能不受影响。`；未进入 commit | ✅ 已修，文案友好 |
| 12b 刷新后僵尸成功态 | `结果已转入页面内容（历史记录 / 下载区）。`，但历史记录是空的 | `⚠️ 该结果属于上一个会话：浏览器刷新后页面内容不可恢复，以下是后台任务留存的原始结果（可复制保存）。`，并**直接渲染出原始结果载荷**（实测 `RESULT-BODY-1` 可见可复制） | ✅ 已修，且比我原建议更好（我建议给"载入"按钮，实际实现把原始结果直接摊开，数据不再丢失）。前缀已由 `ℹ️` 改为 `⚠️`（降级态用 ⚠️，`ℹ️` 读不出是降级） |
| 12c 运行中任务刷新后被新 session 领回 | `agent_runs=1` | `agent_runs=1`（不变） | ✅ **未被改坏**（这是我特地提醒 degrade-fix 保留的正确行为） |
| 10 / 11 | — | 无未捕获异常，与改前一致 | ✅ 无回归 |

**做法说明**：`ui/common.py` 新增 `_MsgError(RuntimeError)` + `_as_exception()`，`show_error` / `log_exception` / `friendly_hint` 三处统一归一化；`friendly_hint` 对 `_MsgError` 直接 `return str(exc)[:200]`，**把服务层的人话原因原样透出**。随后 team-lead 的修复单第 3 条落地：`ui/page_library.py` 的文件级拒收**不再走 `friendly_error`**，改为直接 `st.error()` 并按 `prepared["reason"]`（`empty` / `scanned_no_ocr` / `corrupt` / `truncated` / `encrypted`）分档给话术——这比我在修复 A-2/A-3 里提的方案更彻底，我报告里的 A-2/A-3 因此不再需要单独实施。

> **关于文案里的 emoji 前缀（AppTest 取值机制，复核者必读）**：本报告所有字符串均取自 AppTest 元素树。**`st.error` / `st.warning` 的 body 若以 emoji 开头，Streamlit 会把它当作 alert 的图标拆出去，不再计入 `.value`**；`st.caption` 没有 icon 概念，emoji 原样保留。
> 因此：
> - 场景 7/8 源码是 `st.error("⚠️ " + str(...))`，但抓到的 `[error] 打开 PDF 失败：…` **不含** `⚠️`；
> - 场景 12b 源码是 `st.caption("⚠️ 该结果属于上一个会话…")`，抓到的 **含** `⚠️`。
>
> 两条路径行为完全一致，**页面观感不受影响**（⚠️ 照常显示，只是作为红框图标而非正文字符）。引用本报告的 `error.value` / `warning.value` 时请注意这一差异，不要据此判断"某个提示漏了 ⚠️ 前缀"。该机制由 degrade-fix 用 4 行探针验证（`st.error/st.warning` 的 emoji 被摘、`st.caption` 保留），我这次重跑的 7/8 与 12b 对比正好又交叉印证了一次。

**我也相应修正了脚本**：`scripts/_degrade_drill_file.py` 场景 9 原先硬编码"代码里不存在体积上限常量"，修复后这句已不成立，改为**动态探测**——先扫描常量，检出闸门时自动切换成"验证闸门是否生效、阈值与文案"，未检出时保留原来的取证表述。这样脚本在修复前后都能给出正确结论，不会误导后续复核者。

---

## 二、驱动方式与构造依据（通用部分）

1. **为什么不直接驱动 `st.file_uploader`**：streamlit 1.63 的 AppTest 没有 file_uploader 元素，无法注入文件。故在**运行时**把 `streamlit.file_uploader` 这一个函数句柄换成假上传对象（只实现 `.name` / `.read()`，正是 `ui/page_library.py:206-210` 用到的两个属性），仓库代码零改动。之后走的是**完全真实的** `ui/page_library.py` 解析与提示分支。
2. **文案怎么抓的**：从 AppTest 元素树里原样读 `error / warning / info / success / caption / markdown`，外加折叠区里的 `st.code`（`at.get("code")`）——后者才是"真因"藏身处。
3. **未污染真实库**：`storage/uploads/` 与 `storage/library.json` 演练前后 mtime 未变（仍为 10-12 日），`git status` 无业务文件改动。

---

## 三、逐场景：构造、证据、判定

### 场景 7　损坏的 PDF

**构造方式与依据**（基准件 `pdf/1706.03762v1.pdf`，2.13 MB / 15 页）：

| 子场景 | 构造 | 想等价的真实故障 |
|---|---|---|
| 7a 内容截断 | `good[:int(len*0.55)]` → 1.17 MB | 下载中断 / 网盘同步不全，头部 `%PDF-` 与大部分对象仍在，xref 与文件尾丢失 |
| 7b 结构损坏 | 字节数不变，把 `30%~85%` 区间整段替换为 `0x00` | 磁盘坏块 / 传输错码导致对象流被毁 |
| 7c 头部被抹 | `%PDF-` → `XXXX`，其余完好 | 文件头被编辑器/转码破坏 |
| 7d 轻微截断 | `good[:int(len*0.92)]` | 尾部少量丢失 |

**实测证据（原样摘录）**：

```
  ▶ 7a 内容截断：保留前 55% 字节（%PDF 头与大部分对象仍在，xref/文件尾丢失）
    构造: 1170777 字节 (1.17 MB), 文件名 truncated_55pct.pdf
    解析耗时 0.07s · 进入 pending(查重命中)=False
    未捕获异常: 无
    红色 error 框数量: 2
      [error] 操作失败：PDF 解析。发生内部错误，请重试或换一种操作方式
      [caption] 🩺 可能原因：PDF 损坏 / 加密 / 非标准排版；扫描件但未配置可用的多模态模型 Key
      [caption] 🛠️ 怎么办：换一个 PDF 重试；或先加载演示数据继续演示
      [caption] 🩺 可能原因：PDF 文件损坏 / 加密 / 超大导致超时；向量库写入失败或磁盘空间不足
      [caption] 🛠️ 怎么办：重试一次；换一个体积更小的 PDF；或点击左侧「🚀 一键加载演示数据」继续演示
      [折叠区技术细节#0] str: 打开 PDF 失败：Failed to open stream
      [折叠区技术细节#1] AttributeError: 'str' object has no attribute '__traceback__'
      [折叠区技术细节#2] Traceback (most recent call last):   File "C:\Users\Administrator\ai_research_agent\ui\page_library.py", line 231, in render     friendly_error("PDF 解析", prepared.get("error"),   File "C:\Users\Administrator\ai_research_agent\ui\c
    【根因定位】prepare_pdf 返回的是带 error 的 dict（不是异常），ui/page_library.py:231 把 prepared['error']（一个 str）当异常对象交给 friendly_error；ui/common.py:252 对它取 exc.__traceback__ → str 没有该属性 → AttributeError 冒泡到 ui/page_library.py:242 的 except，于是又渲染了第二个红框。第一个红框的真实原因只存在于折叠区。
```

7b 同构，折叠区真因为 `str: 打开 PDF 失败：PDF 没有任何可读页面（0 页）`。

7c / 7d **没有任何错误提示**：

```
  ▶ 7c 头部被抹：%PDF- 改写成 XXXX，其余字节完好
    解析耗时 1.26s · 进入 pending(查重命中)=True
    红色 error 框数量: 0
      [warning] 语义查重命中：库中已有《1706.03762v1.pdf》，内容相似度 100.0%。
  ▶ 7d 轻微截断：保留前 92% 字节
    解析耗时 1.92s · 进入 pending(查重命中)=True
    红色 error 框数量: 0
      [warning] 语义查重命中：库中已有《1706.03762v1.pdf》，内容相似度 100.0%。
```

**判定**：
- 崩溃：否。
- 文案友好度：**不友好**。① 一次失败弹出**两个一模一样的红框**；② 标题是毫无信息量的"发生内部错误，请重试或换一种操作方式"，把"文件打不开/0 页/截断"这些用户最需要的信息吞掉了；③ 第二个折叠区里躺着真实 traceback（含 `C:\Users\...\ui\page_library.py, line 231` 绝对路径）和 `AttributeError: 'str' object has no attribute '__traceback__'`——**评委点开就是源码级内部错误**，且 `.streamlit/config.toml` 的 `showErrorDetails=false` 管不到它（这是应用自己 `st.code()` 出来的）。
- 降级路径：有。不会污染库，页面其余功能正常。
- 演示风险：**高**（7a/7b）。7c/7d 属于"静默放行"，风险中——演示时如果误传了一个能修复的半残文件，系统一声不吭就入库了。

---

### 场景 8　0 字节文件

**构造方式与依据**：`data=b""`，文件名 `empty_0byte.pdf`。等价真实故障：选中了一个空文件（桌面新建的 0 字节 .pdf）、或从网盘拉下来的占位文件。

**实测证据**：

```
==============================================================================
场景 8｜上传 0 字节文件
==============================================================================
  构造: 0 字节, 文件名 empty_0byte.pdf
    解析耗时 0.10s · 进入 pending(查重命中)=False
    未捕获异常: 无
    红色 error 框数量: 2
      [error] 操作失败：PDF 解析。发生内部错误，请重试或换一种操作方式
      [caption] 🩺 可能原因：PDF 损坏 / 加密 / 非标准排版；扫描件但未配置可用的多模态模型 Key
      [caption] 🛠️ 怎么办：换一个 PDF 重试；或先加载演示数据继续演示
      [caption] 🩺 可能原因：PDF 文件损坏 / 加密 / 超大导致超时；向量库写入失败或磁盘空间不足
      [caption] 🛠️ 怎么办：重试一次；换一个体积更小的 PDF；或点击左侧「🚀 一键加载演示数据」继续演示
      [折叠区技术细节#0] str: 打开 PDF 失败：Cannot open empty stream.
      [折叠区技术细节#1] AttributeError: 'str' object has no attribute '__traceback__'
      [折叠区技术细节#2] Traceback (most recent call last):   File "C:\Users\Administrator\ai_research_agent\ui\page_library.py", line 231, in render ...
```

**判定**：崩溃 否；文案与场景 7 完全同构（**2 红框 + 真因藏折叠区 + 折叠区露 traceback**）；降级路径 有（不入库）；演示风险 **高**。

> 说明：`services/library.py:_empty_prepared()` 已经很负责地把"打不开"变成了带 `error` 键的 dict（这本是为了防止 0 页幽灵文献入库），**问题出在 UI 层把这个 str 当异常对象传给了 `friendly_error`**。服务层是对的，展示层把它渲染坏了。

---

### 场景 9　超过 100MB 的 PDF

**先找上限常量（取证）**——代码里**根本不存在**可 patch 的 `MAX_UPLOAD_MB`：

```
  [取证1] 业务代码里的体积上限常量: 无（app.py / ui/ / services/ / core/ 全部 .py 全量扫描均未命中）
  [取证2] 上传入口 ui/page_library.py:205 = st.file_uploader("选择 PDF", type=["pdf"], key="lib_up")，未传任何体积参数；ui/page_library.py:206-252 的解析流程里也没有 len(data) 判断
  [取证3] .streamlit/config.toml 未配置 server.maxUploadSize → 取 Streamlit 默认 200MB；即 100~200MB 的文件不会被拦在门外，会一路走到解析与入库
```

**等价性说明**：不存在可 patch 的常量，所以改用"**页数与文本量固定 20 页、只放大字节体积**"的**真实合法 PDF**（手工拼 xref，`n_pages` 页正文 + 一个**未被引用的大 stream 对象**撑体积）喂进**同一条** `services.library.prepare_pdf` 链路，把字节体积作为唯一自变量做 1→10→50→105MB 标定。这样"105MB 上传的解析段"是与真实场景等价的；不生成 100MB 落盘文件（全部字节只在内存里，用完即释放；唯一一次落盘发生在系统 temp 目录，写完立即删除）。

**实测证据**：

```
  [标定] services.library.prepare_pdf 同链路，页数固定 20 页，只变体积
    体积         prepare_pdf耗时  进程内存增量         解析结果
    1 MB       0.97s          0.2 MB         解析成功 20 页 / 32218 字符
    10 MB      1.04s          -0.9 MB        解析成功 20 页 / 32218 字符
    50 MB      1.13s          -10.5 MB       解析成功 20 页 / 32218 字符
    105 MB     1.00s          -52.4 MB       解析成功 20 页 / 32218 字符

  [标定2] 页数维度（用真实语料 pdf/UnderstandingDeepLearning_02_09_26_C.pdf，22.45 MB / 541 页）
    open_doc 耗时 0.06s · 总页数 541 · 前 30 页提取 0.39s → 单页 0.013s
    ⇒ 按此单价外推全本 541 页纯文本提取约 7s（不含扫描件 OCR，OCR 每页一次视觉调用会更慢）

  [UI 实测] 105MB PDF 走完整上传链路（点击「🔍 解析并查重」）
    上传体积 110.11 MB
    解析耗时 1.17s · 进入 pending(查重命中)=False
    从构造到页面出结果总耗时 1.21s
    入库落盘动作（已隔离到系统临时目录）: [('huge_105mb.pdf', 110107177, 0.25)]
    ⚠️ 注：为避免污染真实文献库，library.commit 被临时替换成「只实测落盘」的版本，故下方 success 文案里的「0 个分块」是隔离副产物，不是真实行为；真实链路还会 chroma upsert + 写 library.json。
    未捕获异常: 无
    红色 error 框数量: 0
      [success] 已入库：AI4S degrade drill line 0（20 页，0 个分块）
```

**判定**：
- 崩溃：否。
- 文案友好度：**没有任何体积相关的提示/拒绝**——既没有"文件过大"的拦截，也没有"正在处理大文件，请稍候"的告知。唯一的反馈是最后那句成功提示。
- 降级路径：不需要降级（直接成功），但也**没有任何保护**。
- 演示风险：**中**。理由：
  1. 裸字节体积其实不是瓶颈（105MB 只要 1.0s，因为 MuPDF 惰性解析未引用对象），真正的耗时来自**页数/图像内容**——按真实 541 页教材的实测单价外推，纯文本提取约 7s；若是扫描件走 OCR，每页一次视觉调用会到分钟级。演示时传一本大书，页面会长时间"RUNNING"，而**页面只有一条进度条，没有任何"预计还需多久"**。
  2. `services/library.py:777` 会把 `raw` 整份写进 `storage/uploads`（110MB 实测 0.25s，但会长期占盘）；若同一文件命中查重，`ui/page_library.py:239` 的 `st.session_state.pending = prepared` 会把 **110MB 原始字节常驻 session_state 直到用户点决策按钮**。
  3. >200MB 才会被 Streamlit 服务端拦下（见"未能验证"）。

---

### 场景 10　上传 PDF 后立即切换页面（状态竞争）

**构造方式与依据**：
- 10a：真实点「🔍 解析并查重」→ 命中查重（`pending` 写入 session_state，**尚未入库**）→ 立刻用侧边栏导航 radio 切到「💬 2. 跨文献问答与溯源」。
- 10b：再切回「📁 1. 文献库管理」，看旧对话框是否复活。
- 10c：真实时序下，用户在解析期间的点击会**排队**到本轮 script run 结束后才生效；AppTest 无法在 run 中途点击，故把 `library.prepare_pdf` 临时包一层（sleep 1.5s 让"解析中"窗口真实存在，**在解析函数内部**改写 `st.session_state["menu"]`），等价模拟"解析还在跑、用户已点走导航"。

**实测证据**：

```
  ▶ 10a 解析已完成、查重命中（pending 写入 session_state）后立刻点导航切走
    解析耗时 1.50s · 进入 pending(查重命中)=True
    切导航成功=True · 当前 menu=💬 2. 跨文献问答与溯源
    切走后 pending 是否仍残留在 session_state: True
    问答页未捕获异常: 无
    问答页上是否串出文献库内容: 否

  ▶ 10b 再切回文献库页：旧的查重对话框是否复活
    未捕获异常: 无 · pending 仍在=True
    复活文案: ['[warning] 语义查重命中：库中已有《1810.04805v2.pdf》，内容相似度 100.0%。']

  ▶ 10c 解析还在跑时用户已点走导航
    解析途中 menu 被改写=True
    本轮结束未捕获异常: 无
    下一轮渲染的 menu=🕸️ 5. 引用图谱与综述（用户已跳走）
    下一轮未捕获异常: 无
    pending 仍残留在 session_state: True
    图谱页上是否串出文献库的东西: 否
    页面残留进度条 st.progress 数量: 0
```

**判定**：
- 崩溃：否；**没有串页**（切走后目标页干净，不会把文献库的东西带到别的页）；**没有僵尸进度条**（切走后残留 `st.progress` 数 = 0，因为每次 rerun 都重画）。
- 文案：切回来时旧对话框原样复活，但**没有任何"这是上次未完成的决策"的说明**，用户会困惑"我刚才不是切走了吗，怎么还有这个"。
- 降级路径：有（切走照常渲染目标页，切回仍可继续决策，不会卡死）。
- 演示风险：**中**。两个真实的观感问题：
  1. `library.prepare_pdf` 是**同步阻塞**的，解析期间整页 `RUNNING`、点击排队，用户点了导航要等解析跑完才跳——表现为"点了没反应"（小文件 1.5s 无感，大文件按标定 2 可达 7s+ / 扫描件分钟级就很明显）。
  2. `pending` 常驻 session_state 不随切页清理；超大文件命中查重时会把整份 raw 字节一起常驻内存（与场景 9 的第 2 点同源）。

---

### 场景 11　演示中途侧边栏 API Key 被清空

**构造方式与依据**：
- 11a：只把侧边栏输入框清空（`saved_api_key` 与 widget key `key_input` 都置空），**保留 .env 的环境变量兜底**——这是"演示者在界面上把 Key 删了"的最直接等价。
- 11b：在 11a 基础上，再从**本进程** `os.environ` 移除 `LLM_API_KEY` 与各 provider 的 `key_env`（`ZHIPU_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY`），等价"现场唯一一把 Key 也没了"。只动 `os.environ`，**.env 文件全程未读未写**，演练结束立即还原。

**实测证据**：

```
  ▶ 11a 只清空侧边栏输入框（.env 里的环境变量兜底仍在）
    ctx.llm.available = True
    侧边栏出现「尚未配置 API Key」: False
    未捕获异常: 无

  ▶ 11b 唯一 Key 也没了
    本进程内移除的环境变量名: ['ZHIPU_API_KEY', 'DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY', 'OPENAI_API_KEY']
    ctx.llm.available = False
    ctx.llm_status = {'available': False, 'reason': '未配置 API Key，大模型不可用', 'kind': None}
    未捕获异常: 无

    -- 无 Key 下逐个页面渲染（只渲染不点，避免触发真实外网检索）--
      📁 1. 文献库管理             异常=无
      💬 2. 跨文献问答与溯源          异常=无
      📖 3. 版式阅读与智能翻译         异常=无
      🔍 4. 论文检索与追踪           异常=无
      🕸️ 5. 引用图谱与综述          异常=无
      ✍️ 6. 写作辅助与可视化         异常=无
      🤖 0. 智能总助手             异常=无

    -- 点两个典型的大模型按钮 --
      [warning] 元数据智能抽取需要大模型，但未检测到可用 API Key。
      [warning] 尚未配置 API Key：请在下方「接入方式」选择平台并填入 Key；无 Key 也可使用底部按钮一键加载离线演示数据。
      [info] 演示语料的元数据已在入库时自动生成，见「文献库」页签。
      [caption] 🛠️ 怎么办：在左侧「接入方式」选择平台并填写 Key；无 Key 时仍可使用本地演示数据与离线功能（解析/检索/图谱检索式演示）。
      Agent 页异常=无
      [warning] Agent 执行需要大模型，但未检测到可用 API Key。
```

**判定**：
- 崩溃：否（7 个页面逐页渲染 + 两个大模型按钮点击，未捕获异常全为 0）。
- 文案友好度：**好**。侧边栏常驻"尚未配置 API Key"黄条；点任何大模型功能都有 `⚠️ XX需要大模型，但未检测到可用 API Key。` + `🛠️ 怎么办：…` 的成对提示，且明确告知"无 Key 也能用离线功能"。
- 降级路径：有（离线演示数据 / 解析 / 检索式演示全部可用）。
- 演示风险：**低**。唯一值得注意的坑（11a）：**只清空侧边栏输入框并不会真的让 Key 失效**——`core/providers.py:resolve_key` 的优先级是"界面手动填写 > 环境变量 > Secrets"，本机 .env 里有 Key 时，界面清空后 `ctx.llm.available` 仍为 `True`，侧边栏也**不会**弹出"尚未配置 API Key"。演示者若想演示"无 Key 降级"，光删界面 Key 是不够的。

---

### 场景 12　浏览器刷新（session 丢失，后台任务怎么办）

**构造方式与依据**：`services/tasks.py` 的任务状态存在**模块级 dict `_TASKS`**（`tasks.py:24`），设计上就"不碰 session_state"。真实浏览器 F5 = **新 session + 同一服务进程**，因此模块级 dict 存活。AppTest 新建一个实例正好是"新 session + 同进程"，与 F5 等价（AppTest 自身的 `at.run()` 只是 rerun，不等于刷新，故本场景全部用"新建 AppTest 实例"来模拟刷新）。

**实测证据**：

```
  ▶ 12a 任务在旧 session 里已跑完并被消费 → 刷新（新建 session）
    [刷新前] 侧边栏任务中心: ['[caption] ⏳ **运行中的任务**（可切换其他页面，任务在后台继续）']
    [刷新前] state=done · agent_runs 条数=1
    [刷新前] 完成态文案: ['[caption] 结果已转入页面内容（历史记录 / 下载区）。']

  ▶ 12b 浏览器刷新 = 新建 session（模块级 _TASKS 在同一进程内仍存活）
    任务条目仍在模块级 dict: state=done consumed=True
    新 session 的 agent_runs 条数 = 0
    新 session 侧边栏任务中心文案: ['[caption] 结果已转入页面内容（历史记录 / 下载区）。']
    新 session 侧边栏 expander 标题: ['⚙️ 模型接入与账号', 'ℹ️ 关于本系统', '✅ 演练任务1 — 已完成（用时 6 秒）']
    未捕获异常: 无
    新 session 首页主区（历史记录区）文案: ['[info] 🧭 **三步上手**：① 点左侧「🚀 一键加载演示数据（v1/v7/BERT）」…', '[caption] 结果已转入页面内容（历史记录 / 下载区）。']

  ▶ 12c 任务**正在跑**时刷新：新 session 能否接着看到并领回结果
    提交后立刻: state=running
    刷新后立刻: state=running（是否仍在跑）
    刷新后侧边栏运行中文案: ['[caption] ⏳ **运行中的任务**（可切换其他页面，任务在后台继续）']
    刷新后侧边栏 progress 条数: 1
    等它跑完再渲染: state=done · 新 session agent_runs=1
    未捕获异常: 无

  ▶ 12d 后台任务抛异常 → 刷新后的失败态显示
    state=error
    侧边栏文案: ['[caption] 失败原因：RuntimeError: 模拟后台任务抛异常']
    侧边栏 expander 标题: [..., '❌ 演练任务3（必失败） — 执行失败（用时 0 秒）', '✅ 演练任务2（刷新时未完成） — 已完成（用时 25 秒）', ...]
    未捕获异常: 无
```

**判定**：
- 崩溃：否。
- **任务还活着**：模块级 `_TASKS` 跨 session 存活，刷新后仍能显示"⏳ 运行中的任务"和进度条（实测 progress 条数 = 1），跑完后**新 session 能把它领回**（`agent_runs=1`）——这部分设计是好的。
- **结果会丢**：已经在旧 session 里被 `mark_consumed` 标记过的任务，刷新后仍显示 `✅ … — 已完成（用时 6 秒）`，展开写着 `结果已转入页面内容（历史记录 / 下载区）。`，但新 session 的 `agent_runs = 0`，首页历史记录区还是新手引导 **——提示指向一个不存在的地方**。这就是"僵尸成功态"。
- 失败态是友好的（`❌ … — 执行失败（用时 0 秒）` + `失败原因：…`）。
- 降级路径：部分可用（可重新提交任务），已完成任务的结果**无法找回**。
- 演示风险：**高**。典型翻车现场：演示 Agent 跑完一个任务 → 不小心刷了页面 → 侧边栏说"已完成，结果已转入历史记录" → 评委让你点开历史记录，空的。

---

## 四、未能验证（不假装跑过）

1. **超过 200MB 的上传**：会被 Streamlit 服务端 `server.maxUploadSize`（默认 200MB）在 HTTP 层拒绝。AppTest 不经过真实 HTTP 上传通道，**无法在本次演练中验证**；只做了代码/配置层取证（`.streamlit/config.toml` 未配置该值）。
2. **真实扫描件大 PDF 的 OCR 耗时**：需要真实视觉模型 Key 与外网调用，被"不要跑到真实外网"的纪律排除。场景 9 的页数维度标定只测了**纯文本提取**单价，OCR 场景未测。
3. **真实浏览器 F5**：未启浏览器。用"新建 AppTest 实例（新 session + 同进程）"等价模拟，等价性见场景 12 开头说明。
4. **多用户并发下的 `server.maxUploadSize` 与内存竞争**：单机单会话之外的情况未测。

---

## 五、演示风险 = 高 的场景修复方案（**只给方案，未实施**）

### 修复 A：场景 7 / 8 —— 损坏 PDF 与 0 字节弹出两个红框、真因被吞、折叠区露源码 traceback

**根因链（已被实测证据锁定）**：
`services/library.py:_empty_prepared()` 返回的是**带 `error` 键的 dict**（`error` 是一个 `str`）
→ `ui/page_library.py:231` 把 `prepared.get("error")`（str）当作异常对象传给 `friendly_error`
→ `ui/common.py:252` 对它 `traceback.format_exception(type(exc), exc, exc.__traceback__)`，`str` 没有 `__traceback__` → **AttributeError**
→ 冒泡到 `ui/page_library.py:242` 的 `except Exception as e` → 又渲染了一遍 `friendly_error`，于是**两个红框**。

**建议改法（二选一，推荐 1+2）**：

1. `ui/common.py:show_error()` / `friendly_error()` 开头做一次归一化（一行即可，全站受益）：
   ```python
   if isinstance(exc, str):
       exc = RuntimeError(exc)
   ```
   同时 `ui/common.py:log_exception()`（第 198 行）同样要防 `str`。改完后第二个红框与源码路径 traceback 立即消失。
2. `ui/page_library.py:230-234` 不要走 `friendly_error`，改成直接把**人话原因**显示在标题上（这类"文件级拒收"本来就不是异常）：
   ```python
   elif prepared.get("error"):
       st.error("⚠️ " + str(prepared.get("error")))      # 例如「PDF 没有任何可读页面（0 页），未入库。」
       st.caption("🩺 可能原因：PDF 损坏 / 加密 / 非标准排版；扫描件但未配置可用的多模态模型 Key")
       st.caption("🛠️ 怎么办：换一个 PDF 重试；或先加载演示数据继续演示")
   ```
3. 文案建议（按 `prepared["reason"]` 分档，直接给用户看）：
   - `empty` / 打不开：`⚠️ 这个 PDF 打不开（文件可能是 0 字节或已损坏），未入库。请换一个完整的 PDF 重试，或点左侧「🚀 一键加载演示数据」继续演示。`
   - `truncated`：`⚠️ 这个 PDF 不完整：声称 15 页，只解析出 3 页（不足 70%），未入库。请重新下载完整版后重试。`
   - `corrupt`：`⚠️ 这个 PDF 结构损坏，无法提取正文，未入库。请换一个 PDF 重试。`
4. 附带建议（场景 7c/7d 的"静默放行"）：`services/library.py:prepare_pdf` 成功分支里，若文件头不是 `%PDF` 或 `doc.is_repaired` 为真，补一个 `st.warning` 级别的"文件疑似损坏但已修复解析"提示，别让评委以为系统没做校验。

**与 degrade-api 场景的边界（已与其对齐，避免重复报/重复修）**：

同一个 `ui/common.py:friendly_hint()` 兜底句「发生内部错误，请重试或换一种操作方式」有**两个输入面**都会掉进去，但**症状与归属不同**，修复要分开做：

| 输入面 | 谁实测到 | 症状 | 归属 |
|---|---|---|---|
| `exc` 是 **str** | 本报告的 7 / 8 | `exc.__traceback__` → AttributeError → **两个红框** + 折叠区露源码 traceback | **本修复 A**（独立缺陷） |
| `exc` 是**异常对象但文案无关键字命中** | degrade-api 的 S5/S6（`URLError(gaierror)` 的 `str()` 是 `<urlopen error [Errno 11001] getaddrinfo failed>`，不含 `connection`/`timeout`/`401`/`429`/`json`） | **只有 1 个红框**，标题同样是那句兜底句 | degrade-api 的 F5 |

因此 `friendly_hint()` 的关键字表扩容（degrade-api 提的 F5：补 `getaddrinfo / gaierror / urlopen error / Errno 11001 / temporary failure in name resolution` 等网络关键字）**与本报告修复 A 是互补的两条，不能互相替代**：
- 只做 F5 关键字扩容 → 我的双红框仍在（因为崩在 `__traceback__`，关键字表根本没机会生效）；
- 只做修复 A 第 1 条（str 归一化）→ degrade-api 的 URLError 仍掉兜底句。

**合并后的建议提法**（两条一起交 `ui/common.py`）：
1. `show_error()` / `log_exception()` 开头 `if isinstance(exc, str): exc = RuntimeError(exc)` —— 消灭双红框（本报告 A-1）。
2. `friendly_hint()` 增加网络/DNS 关键字分支（degrade-api F5）。**→ 已落地**：degrade-api 22:56 复跑确认，S6 标题已从「发生内部错误」变为 `⚠️ 操作失败：PubMed 检索。网络连接失败，请检查网络后重试（外网不可达或被代理拦截）`（实现为 `ui/common.py:_is_network_exc()` + `_NET_EXC_CLASS` / `_NET_MSG_MARKS`）。
3. **顺序有讲究**：先做 1 再做 2。若先做 2 且实现方式改成"用 `str(exc)` 参与关键字匹配"，那么我这边 `打开 PDF 失败：Cannot open empty stream.` 这类文案依然不含任何网络关键字，**仍旧掉兜底句**——所以我的场景最终还是得靠 A-2/A-3（让 `page_library` 不走 `friendly_error`、直接把 `prepared["error"]` 显示成标题），这一步不能省。

> 一处事实订正（与 degrade-api 消息中的描述略有出入，避免照抄写进修复单）：str 输入时 `type(exc).__name__` 是 `'str'`，**不含** `"error"`，所以 `friendly_hint()` 里 `if "error" in name` 这个分支**不命中**，是直接落到最后那句 `return "发生内部错误，请重试或换一种操作方式"`。结论不变，但依据要写准。

### 修复 B：场景 12 —— 刷新后"结果已转入页面内容"指向空历史记录

**建议改法（最小改动，改 3 处）**：

1. `ui/common.py:render_task_panel()` 现在是无条件 `mark_consumed`。改成**只在回调真的把结果写进当前 session 后才标记**：
   ```python
   if on_finish is not None and fresh:
       try:
           ok = on_finish(t)          # 回调返回是否成功写入本 session
       except Exception:
           ok = False
       if ok:
           tasks_mod.mark_consumed(t["id"])
   ```
2. `ui/sidebar.py:_consume_task()` 已经返回 True/False，确认"结果为空 / 写入失败"时返回 `False`（现在返回 False 也会被标记消费，这就是漏洞）。
3. 文案改掉那句"指空"的话：`ui/common.py` 里 `结果已转入页面内容（历史记录 / 下载区）。`
   → 改成 `✅ 已完成（用时 X）—— 点击下方按钮把结果载入本页历史记录`，并在展开区放一个 `📥 载入结果` 按钮，点了才真正写 `agent_runs` 并 `mark_consumed`。
4. 若要彻底：在 `services/tasks.py` 增加结果落盘（`storage/tasks/<tid>.json`），刷新后按 tid 可重复领取；`snapshot()` 里带上 `has_result` 标志。**注意别改坏 12c 那条已验证正确的行为**（运行中任务刷新后继续跑、完成后被新 session 领回）。

### 修复 C（演示风险=中，建议一并处理）

- **场景 9 · 体积闸门**：`ui/page_library.py:205-211`，在 `data = f.read()` 之后立刻加判断
  ```python
  MAX_UPLOAD_MB = 60
  if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
      st.warning(f"⚠️ 这个文件有 {len(data)/1e6:.0f} MB，超过 {MAX_UPLOAD_MB} MB，"
                 f"现场解析会很慢且占用大量内存。建议换一个更小的 PDF，"
                 f"或点左侧「🚀 一键加载演示数据」继续演示。")
      st.stop()
  ```
  并在 `f.read()` 之前给出"正在读取大文件…"的即时反馈。
- **场景 9 · 内存**：`ui/page_library.py:239` `st.session_state.pending = prepared` 之前把 `prepared.pop("raw", None)`（或只保留落盘路径），避免超大 PDF 的原始字节常驻 session。
- **场景 10 · 切页清理**：`ui/sidebar.py:_pick()` 里，若目标页不是文献库页，顺手 `st.session_state["pending"] = None`；或在切回文献库页时给旧对话框加一句 `st.caption("这是上一次未完成入库的查重决策，可继续处理或取消。")`。
- **场景 11 · 提示补全**：`ui/sidebar.py` 清空 Key 时若 `resolve_key` 的环境变量兜底仍然命中，补一句 `ℹ️ 界面 Key 已清空，但检测到环境变量中仍有可用 Key，模型仍可用；如需完全停用请移除环境变量。`，避免演示者误判。

### 相邻发现（由 degrade-api 实测，**本报告未覆盖**，仅登记不认领）

- **LLM 假成功**（degrade-api 的 F1）：Key 存在但调用失败时，页面显示 `✅ 分析完成`，正文却是 `【LLM 暂时不可用…⚠️ 离线占位响应…】`——不是红框报错而是"假成功"，根因是 `core/llm.py` 的 `is_degraded() / is_degraded_text()` 在 `ui/`、`services/` 零调用。
  **与本报告的边界**：我的场景 11 测的是"Key **完全清空**"，此时 `llm_guard()` 是真的拦住了（实测页面给出 `⚠️ XX需要大模型，但未检测到可用 API Key。`），**没有出现假成功**；"假成功"发生在"Key 有但调用失败"这条路径上，属 degrade-api 的范围。两处守卫都要补，不能只补一边。
  **状态（degrade-api 22:56 复跑）**：核心已修——`✅ 分析完成` 不再出现，改为 `⚠️ 引用含金量分析未完成：大模型暂时不可用，本次**没有**生成任何真实结果。` + `⚠️ 以下为**离线降级结果**，不是大模型的真实输出，请勿作为答案引用。`；**残留**：离线占位正文仍被渲染（但已被上方两条 warning 明确标注，degrade-api 判定可接受）。
  ⚠️ **本行可能已过期**：degrade-fix 随后又改了图谱页/问答页的降级文案（新增 `⚠️ 大模型不可用：以下领域综述/前沿方向由本地确定性算法生成…`、`⚠️ 离线降级结果未做引用校验…`，并让降级时不再出现 `✅ 领域综述已生成` / `✅ 前沿方向扫描完成`）。**以上字符串以 degrade-api 的最新复跑为准，我不重复取证**（不属我的场景）。
- **折叠区露源码绝对路径**（degrade-api 记为 F6，低优先级）：`ui/common.py:250-254` 的 `st.expander("🔍 查看技术细节")` 里 `st.code(tb[-3000:])`。默认折叠，演示不展开看不到；但公网部署被点开就露路径，且 `showErrorDetails=false` 管不到。**注意**：我的场景 7/8 里折叠区露的 traceback 是 `AttributeError`（修复 A-1 解决），与 F6 的"正常异常对象的 traceback"是两回事，别混为一谈。**状态（degrade-api 22:56）：未修，可选。**
- **`services/state.py:132 set_provider` 的重建条件（degrade-api 记为 F2）**【状态：degrade-api 22:56 复跑确认**未修**，其报告建议优先做"挪 `llm_failure_banner` 渲染顺序"，改重建条件列为增强】：该函数与我的场景 11 直接交叉（我的 Key 清空路径就是走 `ctx.set_provider(pid, "")`），所以我**实测验证了它的候选改法对本场景是否安全**，结论如下（探针脚本已删，结论保留）——将来真要改时可直接引用，不必重测：

  | 实现 | 11a 只清侧边栏（env 兜底仍在） | 11b 侧边栏+env 都清空 | 3 轮 run 的重建次数 |
  |---|---|---|---|
  | 现状实现 | available=**True**，无异常 | available=**False**，无异常 | **3 次**（每轮一次，即 F2 所指的"每轮重建把 `last_error` 清零"） |
  | F2 候选实现（改用"解析后的 key"比较） | available=**True**，无异常 | available=**False**，无异常 | **0 次** |

  ⇒ **候选改法对我的场景 11 是安全的**：`available` 的翻转结果与基线完全一致，未捕获异常均为 0。两种清空时序都已覆盖——主脚本的场景 11 是"演示中途清空"，上面的探针是"启动前清空"，两者结论一致。
  ⚠️ 给修复人的边界提醒：要改的是"Key 来自 .env 且输入框为空"时误判重建，**不能**把"切换 Provider / 手填新 Key 时必须重建"这条语义一起改掉；另注意候选实现下"启动前就没有 Key"时一次都不会重建（因为 `Container.__init__` 已经建好了不可用的 llm），这是对的，但"演示中途 Key 失效"仍会且必须重建一次。

  **⚠️ 本结论的适用范围（必须一并读，别过度外推）**：上表只覆盖了 **2 种** Key 时序，**第 3 种我没测**——「用户先手填 Key → 再清空输入框、期望回退到 .env」。degrade-fix 从 `services/state.py:134` 的重建条件 `if pid != self.provider_id or manual_key != self.llm.api_key or changed:` **代码判断**（改动后 `manual_key=""` 时条件整体为假 → 不重建 → 客户端继续持有上一轮手填的 Key）：这一种时序下候选改法会导致客户端不重建、继续持有旧手填 Key。**该时序 degrade-fix 未实测（只有代码判断），degrade-file 侧同样未覆盖。**
  ⇒ 所以上表的正确读法是：**"候选改法在 11a/11b 两种时序下安全"，不等于"候选改法可以照搬落地"**。真要动 `set_provider`，第 3 种时序必须先补测。
  ⇒ 同理，**degrade-fix 自己在 `ui/sidebar.py` 的回补逻辑也未覆盖该时序**：他实测的是"Key 被换成新值后 `last_error` 为无"（重建前后 Key 与平台都没变时才回补，Key 真变了不回补）；而"手填→清空→回退 .env"时其回补会不会误触发（`_now == _prev[:2]` 可能成立、把上一次失败原因带过来），**他也没测**。不要替他把这一条兜下来。

---

## 六、纪律自查

- 未修改任何业务代码（`core/ services/ ui/ app.py config.py` 全部只读）；运行时 patch 均在脚本内 `finally` 还原（`library.commit`、`library.prepare_pdf`、`os.environ`）。
- 未 git commit，未读写 `.env`，报告中无真实 Key。
- 未访问外网；未运行 `scripts/_smoke_ui.py` / `scripts/_smoke_read.py`。
- 未在仓库留下大文件：110MB 测试字节只在内存，唯一一次落盘在系统 temp 目录且立即删除；`storage/uploads/`、`storage/library.json` 演练前后未变。
- 产出文件仅两个：`scripts/_degrade_drill_file.py`（可重复运行）、`docs/review/round12_degrade_file.md`（本文件）；过程中建的 3 个临时探针脚本已删除。

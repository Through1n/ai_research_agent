# Round 12 · 故障演练「演示高风险」缺陷 —— 修复报告

> 修复人：degrade-fix　｜　日期：2026-10-13（含 team-lead 修订单后的第二轮改动）
> 解释器：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（Python 3.11.16 / streamlit 1.63.0 / openai 3.13.0）
> 取证方式：`streamlit.testing.v1.AppTest` + `unittest.mock` **静态注入**，全程未发起真实外网请求
> 复跑命令（**串行，不要并行**）：
> ```
> PY=C:/Users/Administrator/anaconda3/envs/ai_research/python.exe
> $PY scripts/_degrade_drill_file.py      # 场景 7-12（degrade-file 独占），约 80s
> $PY scripts/_degrade_drill_api.py       # 场景 S1-S6 + 实测 A-F（degrade-api 独占），约 5 分钟
> $PY scripts/_degrade_drill_fix.py       # V1-V4：②残留成功态 + F2 横幅（我的独占），约 15s
> ```
> **红线标注**：本轮全部结论都是 **AppTest 静态注入取证**。没有真实断网、没有真实 401、没有真实浏览器 F5。
> 下文凡标「外推 / 推断」的数字与结论都不是实测值；凡标「实测」的都是脚本真实打印出来的页面字符串。

---

## 〇、本轮第二轮改动（team-lead 修订单）说明

team-lead 在我第一轮交付后把「兜底句」这一项拆成了**三条并列、且有顺序要求**的独立修复
（degrade-file 与 degrade-api 对账后发现同一个兜底句有两个不同的输入面）。我已按序全部落地：

| 步骤 | 内容 | 状态 |
|---|---|---|
| 第 1 步 | `show_error()` / `log_exception()` / `friendly_hint()` 入口把非异常入参归一化（消灭双红框） | ✅ 第一轮已做，本轮保留 |
| 第 2 步 | `friendly_hint()` 补网络 / DNS 分支（消灭 S5/S6 的「内部错误」误报） | ✅ 第一轮已做，本轮保留 |
| **第 3 步** | **`ui/page_library.py` 的文件级拒收不走 `friendly_error`，真因直接上红框标题 + 按 `prepared["reason"]` 分档** | ✅ **本轮新增（第一轮漏了，已补）** |
| ② 收紧 | 降级状态必须用 `core/llm.py:19` 已定的明示话术，不另造词 | ✅ **本轮改** |

**为什么第 3 步不能省**：就算第 1 步让 `str(exc)` 参与关键字匹配，degrade-file 这边的真因文案是
`打开 PDF 失败：Cannot open empty stream.`、`PDF 没有任何可读页面（0 页）`——
**不含任何网络 / HTTP 关键字**，照样掉兜底句。PDF 解析这条路径不该去蹭通用的网络话术。

### 第三轮改动（team-lead「授权扩文件收尾三项」）

| 项 | 内容 | 状态 |
|---|---|---|
| **F2** | LLM 失败横幅永不出现：`app.py` 横幅挪到 `_PAGES` 渲染**之后** + `ui/sidebar.py` 保住 `last_error` | ✅ 已修（见 §四之四） |
| **图标** | `ui/page_graph.py:142`、`ui/page_writing.py:445/451` 三处 `ℹ️` → `⚠️`（**仅这三行**） | ✅ 已改（见 §四之三） |
| **残留成功态** | `ui/page_graph.py` 两个 `✅ 已生成/完成`、`ui/page_qa.py` 答案区接线降级判定 | ✅ 已修（见 §四之五） |
| **③ `LLM_TIMEOUT`** | **决定不改默认值**，理由与实测证据见 §四之六 | ⛔ 不改（有据） |

### 第四轮改动（team-lead「F7 决策下发 + 批准三处残留」）

| 项 | 内容 | 状态 |
|---|---|---|
| **F7** | 横幅按「产生它的那一页」收敛（`core/llm.py` 序号 + `app.py` 前后快照） | ✅ 已修，V5 五步全对（§四之七） |
| 残留-A | `ui/page_agent.py:366/275` 假成功 | ✅ 已修（§四之八） |
| 残留-B | `ui/page_reading.py` 逐段翻译 / 划词翻译的假译文（含缓存污染） | ✅ 已修（§四之八） |
| 残留-C | `ui/page_writing.py:534` 学术翻译的假译文 | ✅ 已修（§四之八） |

本轮新授权的独占文件：`app.py`（仅横幅相关行）、`services/state.py`（实际未改，见 §四之四）、
`ui/page_graph.py`、`ui/page_qa.py`、`ui/page_writing.py`；
加之上轮已授权的 `services/rag.py`、`ui/common.py`、`ui/page_search.py`。

---

## 一、结论表

| # | 缺陷 | 优先级 | 修复位置 | 前后对比证据 | 结论 |
|---|---|---|---|---|---|
| ①-1 | str 入参导致两个一模一样的红框 | P0 | `ui/common.py` | 红框 **2 → 1**；折叠区 `AttributeError` 与源码路径消失 | ✅ |
| ①-2 | 网络/DNS 异常被说成「内部错误」 | P1 | `ui/common.py` | `发生内部错误…` → `网络连接失败，请检查网络后重试（外网不可达或被代理拦截）` | ✅ |
| ①-3 | PDF 真因被吞在折叠区 | P0 | `ui/page_library.py` | 真因直接上标题：`⚠️ 打开 PDF 失败：Failed to open stream`，并按 reason 分档 | ✅ |
| ② | LLM 失败时主体区显示「✅ 分析完成」+ 离线占位（假成功 + 假答案） | P0 | `ui/common.py` + `ui/page_search.py` | `[success] 分析完成` 消失，换成两条 `[warning]`；S1–S4 全覆盖 | ✅ |
| ③ | 单次调用最坏转圈 6–12 分钟 | P0 | `core/llm.py` + `config.py` | 预算实测 `17.0s/4次 → 8.5s/2次`；线上外推 369s → ≈90s | ✅（部分，见 §九-3） |
| **F4** | 一条问答链路打 8 次 SDK（重复烧配额） | 中高 | `services/rag.py` | 探针 C **8 → 4**（429/超时/断网）；401 **2 → 1**；流式探针 C2 亦为 **4**；S3/S4 实测 19.5s → 10.4s | ✅ |
| **F2** | LLM 失败时框架横幅**永不出现**（每轮重建 `LLMClient` 清零 `last_error`） | P1 | `app.py` + `ui/sidebar.py` | degrade-api 实测 D：`.env Key → 重绘后 last_error` **无 → 有**；横幅同轮即见 | ✅ |
| **②残留** | 图谱页 `✅ 领域综述已生成` / `✅ 前沿方向扫描完成`、问答页答案区照常渲染 | P0 | `ui/page_graph.py` + `ui/page_qa.py` | 两个 `✅` 实测 **True → False**，降级标识 **False → True**；反向核验不误判 | ✅ |
| **②残留-2** | Agent 页 `✅ 已完成` 不判占位；阅读页/写作页翻译把占位串当译文渲染 | P0 | `ui/page_agent.py` + `ui/page_reading.py` + `ui/page_writing.py` | 六处接线（含翻译缓存不再污染），`_smoke_read.py` 那条无 Key warning 仍 PASS | ✅ |
| **F7** | 一次失败后横幅挂到所有页面 | 中 | `core/llm.py` + `app.py` + `ui/sidebar.py` | V5 五步全对：失败页可见 / 切走消失 / 切回重现 / 换页再失败会挪过去 | ✅ |
| 图标 | 降级提示用 `ℹ️` 被当成普通信息 | 低 | `ui/common.py` + `ui/page_graph.py:142` + `ui/page_writing.py:445/451` | `ℹ️ 该结果属于上一个会话…` → `⚠️ …`；另三处「不可用 / 降级渲染」同改 | ✅ |
| ⑤ | 超过 100MB 的 PDF 无任何闸门 | P1 | `config.py` + `ui/page_library.py` | `已入库：…` → `这个文件有 105 MB，超过 100 MB 上限，已停止解析` | ✅ |
| ⑥ | 刷新后「结果已转入页面内容」指向空历史记录 | P1 | `ui/common.py` | 谎称文案消失；改为诚实说明 + **结果载荷真实渲染出来** | ✅（方案 a） |

改动文件：`ui/common.py`、`ui/page_library.py`、`core/llm.py`、`config.py`、
`app.py`（仅横幅块移位）、`ui/sidebar.py`、`ui/page_graph.py`、`ui/page_qa.py`、
`ui/page_writing.py`（仅 3 行图标）、`services/rag.py`、
`ui/page_search.py`（越界，仅 2 行，见 §七）、
`scripts/_degrade_drill_api.py`（+【核验②】与附加实测 E；已与 degrade-api 对齐，见 §九-9）、
`scripts/_degrade_drill_file.py`（+【核验⑤】【核验⑥】；已与 degrade-file 对齐）。

**未触碰**：`Dockerfile`、`docker-compose.yml`、`.dockerignore`、`DEPLOY.md`、`资料/technical_doc.md`、
`资料/演示视频脚本.md`、`资料/复盘/*.md`（除本报告）、`资料/截图/`、`requirements.txt`、
`services/tasks.py`、`services/state.py`（**授权了但没改，理由见 §四之四**）、
`core/llm.py` 的 `is_degraded / is_degraded_text / DEGRADED_TEXT_MARK` 语义、
`config.py:LLM_TIMEOUT` 的默认值 90.0（**team-lead 明令不动，见 §四之六**）。
**未 git commit。**

---

## 二、① · 兜底句「发生内部错误」的三个输入面（按 team-lead 修订单拆三条）

### 输入面的区别（决定修复顺序）

| 面 | 触发 | 症状 | 归谁管 |
|---|---|---|---|
| 面 1：`exc` 是 **str** | `ui/page_library.py` 把 `prepared["error"]`（str）当异常传给 `friendly_error` | 异常在 `ui/common.py` 的 `exc.__traceback__` 处就抛了，**发生在 `friendly_hint()` 被调用之前** → 冒到外层 except → **2 个红框** | 第 1 步（归一化） |
| 面 2：`exc` 是真异常但文案不含已知关键字 | `URLError(socket.gaierror(11001))` → `<urlopen error [Errno 11001] getaddrinfo failed>` | **1 个红框**，标题是兜底句 | 第 2 步（关键字扩容） |
| 面 3：str 内容不含任何网络关键字 | `打开 PDF 失败：Cannot open empty stream.` / `PDF 没有任何可读页面（0 页）` | 即使面 1 修好，标题仍是兜底句 | **第 3 步（直接显示 + 分档）** |

> **依据订正**（team-lead 指出 degrade-api 原描述有误，这里写准）：
> str 入参时 `type(exc).__name__` 是 **`'str'`**，其中**不含** `"error"` 子串，
> 所以 `friendly_hint()` 里的 `if "error" in name` 分支**不命中**，是直接落到函数最后那句
> `return "发生内部错误，请重试或换一种操作方式"`。结论不变，但依据按此表述。

### 第 1 步 · 入口归一化（消灭双红框）

`ui/common.py`：

```python
class _MsgError(RuntimeError):
    """服务层直接给出的「人话原因」的异常包装。"""

def _as_exception(exc):
    if isinstance(exc, BaseException):
        return exc
    if exc is None:
        return _MsgError("未提供具体错误原因")
    return _MsgError(str(exc))          # str(exc) 与原文本完全一致
```

`show_error()`、`log_exception()`、`friendly_hint()` **三处入口都调**（degrade-file 提醒：
如果将来有调用点绕过 `show_error` 直接调 `log_exception`，那处就是唯一防线，**三处都要留着**）。
`friendly_hint()` 对 `_MsgError` 原样透出原文（截断 200 字），不再掉兜底句。

### 第 2 步 · 网络 / DNS 分支

`ui/common.py` 新增 `_is_network_exc()`，插在 `TimeoutError` 判定之后、401/403 之前：

- **精确「模块 + 类名」命中**：`urllib.error.URLError`、`requests.exceptions.ConnectionError /
  ConnectTimeout / ProxyError / SSLError`、`socket.gaierror / herror`、内置 `ConnectionError`；
- **文案兜底**：`getaddrinfo` / `gaierror` / `urlopen error` / `errno 11001 / 11004` /
  `failed to establish a new connection` / `max retries exceeded` / `name or service not known` …

**刻意排除**：`urllib.error.HTTPError` 是 `URLError` 的子类，用**精确类名**匹配把它排除，
否则 5xx/404 会被误判成网络故障。

### 第 3 步 · PDF 路径不走 `friendly_error`（本轮新增）

`ui/page_library.py`：

```python
elif prepared.get("error"):
    # 文件级拒收**不是异常**：真因直接上红框标题，按 prepared["reason"] 分档
    _rs, _as_ = _PDF_ERR_TIPS.get(str(prepared.get("reason") or ""), _PDF_ERR_TIPS["_"])
    st.error("⚠️ " + str(prepared.get("error")))
    st.caption("🩺 可能原因：" + "；".join(_rs))
    st.caption("🛠️ 怎么办：" + "；".join(_as_))
```

分档键取自 `services/library.py`（我 grep 过全部分档，不是猜的）：
`empty`(:503) / `scanned_no_ocr`(:510) / `corrupt`(:513, :529, :584) / `truncated`(:519, :524) / `encrypted`(:584)，
外加 `_` 兜底档。

### 前后对比（脚本真实输出，原样摘录）

**修复前** —— `scripts/_degrade_drill_file.py` 场景 7a：

```
    红色 error 框数量: 2
      [error] 操作失败：PDF 解析。发生内部错误，请重试或换一种操作方式
      [caption] 🩺 可能原因：PDF 损坏 / 加密 / 非标准排版；扫描件但未配置可用的多模态模型 Key
      [caption] 🛠️ 怎么办：换一个 PDF 重试；或先加载演示数据继续演示
      [caption] 🩺 可能原因：PDF 文件损坏 / 加密 / 超大导致超时；向量库写入失败或磁盘空间不足
      [caption] 🛠️ 怎么办：重试一次；换一个体积更小的 PDF；或点击左侧「🚀 一键加载演示数据」继续演示
      [折叠区技术细节#0] str: 打开 PDF 失败：Failed to open stream
      [折叠区技术细节#1] AttributeError: 'str' object has no attribute '__traceback__'
      [折叠区技术细节#2] Traceback (most recent call last):   File "C:\Users\Administrator\ai_research_agent\ui\page_library.py", line 231, in render     friendly_error("PDF 解析", prepared.get("error"),   File "C:\Users\Administrator\ai_research_agent\ui\c
```

**修复后（三条全做完）** —— 同一场景：

```
    红色 error 框数量: 1
      [error] 打开 PDF 失败：Failed to open stream
      [caption] 🩺 可能原因：PDF 结构损坏 / 非标准排版；扫描件但未配置可用的多模态模型 Key
      [caption] 🛠️ 怎么办：换一个 PDF 重试；或先加载演示数据继续演示
```

三个子场景：

| 子场景 | 修复前 | 修复后（本轮定稿） |
|---|---|---|
| 7a 内容截断 | **2** 个 · `操作失败：PDF 解析。发生内部错误，请重试或换一种操作方式` | **1** 个 · `⚠️ 打开 PDF 失败：Failed to open stream` |
| 7b 结构损坏 | **2** 个 · 同上 | **1** 个 · `⚠️ 打开 PDF 失败：PDF 没有任何可读页面（0 页）` |
| 8 0 字节 | **2** 个 · 同上 | **1** 个 · `⚠️ 打开 PDF 失败：Cannot open empty stream.` |

> 注：第一版（只做第 1 步）标题是 `⚠️ 操作失败：PDF 解析。打开 PDF 失败：Failed to open stream`
> （真因已在标题上、红框已 2→1）。本轮做完第 3 步后去掉了 `操作失败：PDF 解析。` 这个通用前缀
> —— 因为这条路径不再经过 `show_error` 的前缀拼接。**已通知 degrade-file 同步更新他报告的引用。**

**附带收益（实测确认）**：第 3 步之后这条路径**完全不产生「🔍 查看技术细节」折叠区**
（脚本输出里场景 7a/7b/8 的 `[折叠区技术细节#N]` 行数为 **0**），
即源码绝对路径与 `AttributeError` 都不再有任何露出通道。

S6（面 2）前后对比：

```
修复前：[error] 操作失败：PubMed 检索。发生内部错误，请重试或换一种操作方式
修复后：[error] 操作失败：PubMed 检索。网络连接失败，请检查网络后重试（外网不可达或被代理拦截）
        [caption] 🩺 可能原因：演示现场外网不可达或被代理拦截；arXiv / PubMed 接口限流或临时不可用
        [caption] 🛠️ 怎么办：重试一次；如需离线演示，请点左侧「🚀 一键加载演示数据」并使用📁/💬/🕸️页面；点击左侧「🚀 一键加载演示数据」使用离线数据继续演示；检查网络/代理后再试
```

**分支未回归**（一次性手工探针，非演练脚本）：

```
URLError(gaierror 11001)   : 网络连接失败，请检查网络后重试（外网不可达或被代理拦截）
requests ConnectionError   : 网络连接失败，请检查网络后重试（外网不可达或被代理拦截）
str 入参（服务层人话原因）    : 打开 PDF 失败：Failed to open stream
TimeoutError               : 网络请求超时，请检查网络后重试
FileNotFoundError          : 所需文件不存在或已被移动
urllib HTTPError 500       : 发生内部错误，请重试或换一种操作方式   ← 未被误判为网络
ValueError                 : 返回内容格式异常，请重试一次
```

---

## 三、② · 离线占位被当成「✅ 分析完成」（场景 S1–S4）

### 根因（team-lead 已亲自 grep 核实，我未重复挖）

`core/llm.py:78 is_degraded()` / `:104 is_degraded_text()` 在**全仓 `ui/` 与 `services/` 下调用数为 0**——
内核老老实实标了"这是降级占位"，但**没有任何一个页面去读它**。这不是缺能力，是**缺接线**。

### 修法（两条通道都堵）

1. `ui/common.py` 新增：
   - `is_llm_degraded(content)` —— **结构化通道**（`core.llm.is_degraded`，即 `degraded_marker` /
     `degraded` 属性）优先，**纯文本通道**（`【LLM 暂时不可用` 前缀 + `⚠️ 离线占位响应` 标记）兜底；
   - `llm_degraded_banner()` —— 橙色 warning 条，话术**沿用 `core/llm.py:19` / `DEGRADED_HINT`
     已定的那句**，不另造词；
   - `show_llm_result(...)` —— 降级时只出 warning、**绝不出 `st.success`**，返回 True/False 供分支。
2. `ui/page_search.py:_quality()` 改用 `show_llm_result(...)`（唯一有取证的 `✅ 分析完成`）。
3. `ui/common.py:render_trace()` 的 Agent「✅ 最终答案」同样加守卫 → 改成
   `##### ⚠️ 最终答案（离线降级）` + 降级标识条 + `st.warning`。

**没有改** `core/llm.py` 的 `is_degraded` / `is_degraded_text` / `DEGRADED_TEXT_MARK` 语义，只加调用方接线。

### 前后对比（脚本真实输出，原样摘录）

**修复前** —— S1 探针 B：

```
      [success] 分析完成
      [markdown] 【LLM 暂时不可用，请稍后重试。⚠️ 离线占位响应：LLM 不可用，以下内容不是模型生成，请勿作为答案引用。原因：API Key 无效或已过期（HTTP 401/403），请在侧边栏检查 Key 后重试；原始错误：Error code: 401 - …
```

**修复后（本轮定稿）** —— 同一场景：

```
      [warning] 引用含金量分析未完成：大模型暂时不可用，本次**没有**生成任何真实结果。
      [warning] 离线占位响应：LLM 不可用，以下内容不是模型生成，请勿作为答案引用。
      [caption] 🛠️ 怎么办：在左侧「接入方式」检查 API Key / 平台额度 / 网络后重试；无 Key 时可点「🚀 一键加载演示数据」用本地离线链路继续演示。
      [markdown] 【LLM 暂时不可用，请稍后重试。⚠️ 离线占位响应：…（占位正文仍渲染，因为它带着失败原因）】
```

### 收紧后的验收口径（team-lead 追加的一条）

我在脚本里加的自动核验行改成查 `core/llm.py` 那句明示文案的稳定子串 `以下内容不是模型生成`：

```
S1：【核验②】主体区出现「✅ 分析完成」= False；页面可见「以下内容不是模型生成」明示文案=True
S2：【核验②】主体区出现「✅ 分析完成」= False；页面可见「以下内容不是模型生成」明示文案=True
S3：【核验②】主体区出现「✅ 分析完成」= False；页面可见「以下内容不是模型生成」明示文案=True
S4：【核验②】主体区出现「✅ 分析完成」= False；页面可见「以下内容不是模型生成」明示文案=True
```

**适用边界**（degrade-api / degrade-file 均强调）：假成功**只在"Key 填了但调用失败"时出现**。
Key 完全清空时 `llm_guard()` 是真拦住的（`⚠️ 元数据智能抽取需要大模型，但未检测到可用 API Key。`），
**没有假成功**。所以复跑验证用 S1/S4，**不要用 degrade-file 的场景 11**，测不出东西。

---

## 四、③ · LLM 调用总时长预算（场景 S3）

`config.py`：`LLM_RETRY_BUDGET_SEC = _env_float("LLM_RETRY_BUDGET_SEC", 45.0)`（`.env.example` 已补一行说明）。
`core/llm.py`：在 `_create()` 与 `stream()` 两个重试循环各加一处预算判定：

```python
if not info.retryable or attempt >= MAX_RETRIES:
    break
if budget and (time.time() - t_start) >= budget:   # 累计耗时超预算 → 不再重试
    break
_sleep_backoff(attempt, info)
if budget and (time.time() - t_start) >= budget:
    break
```

### 「不缩短正常成功请求时间窗」怎么保证（硬要求）

1. **代码层面**：预算只在**上一次尝试已失败、正准备发起下一次**时判定，只决定"要不要再试"。
   **没有任何一处改写 timeout 参数**——没给 `completions.create()` 传 `timeout=`、
   没改 `LLMClient.timeout`、没动 `LLM_TIMEOUT` 默认 90.0。每一次尝试自己的窗口都是完整 90 秒。
2. **回归证据**：`_smoke_read.py` 的 C 组退避断言照旧 PASS（桩瞬时失败，累计 9s < 45s，预算不触发）。
3. **实测证据**：下面 B=45 / T=2 那一档与"预算关闭"**完全一致**（17.0s / 4 次），
   说明预算不足时行为零变化。

### 前后对比（新增「附加实测 E」，注入方式与 degrade-api 的 A2 完全一致）

```
附加实测 E：总时长预算（LLM_RETRY_BUDGET_SEC）对重试链的截断效果
  预算关闭（B=0）              → 实测挂起  17.0s · SDK 调用 4 次 · 退避 [1.5, 3.0, 4.5] · kind='read_timeout'
  预算 B=6s                → 实测挂起   8.5s · SDK 调用 2 次 · 退避 [1.5, 3.0] · kind='read_timeout'
  预算 B=45s（线上默认）         → 实测挂起  17.0s · SDK 调用 4 次 · 退避 [1.5, 3.0, 4.5] · kind='read_timeout'
  [外推，非实测] 线上默认 LLM_TIMEOUT=90：
     预算关闭 → 4 次尝试 × 90s + 退避 ≈ 369s 才降级
     预算 45s → 1 次尝试 × 90s ≈ 90s 才降级（首尝窗口仍是完整的 90s）
```

B=0 档 17.0s 与 degrade-api 附加实测 A2 的 17.0s 能对上，说明注入等价、测量口径一致。
（degrade-api 另跑过 B=5 档：5.5s / 2 次，与我的 B=6 档 8.5s / 2 次同向。）

---

## 四之二 · F4 · 一条问答链路打 8 次 SDK（team-lead 补派，本轮新增）

### 根因：其实是**两个独立的翻倍源**，不是一处

team-lead 给的根因是 `services/rag.py:170-173`（流式失败后无条件回退非流式）。我照着改了，
但复跑发现探针 C **仍然是 8 次**——于是继续追，发现 8 次是**另一条路**造成的。
两条路**都存在**，都要堵：

| 路径 | 触发条件 | 翻倍来源 | 修法 |
|---|---|---|---|
| **流式路径**（真实 UI） | `rag.answer(..., on_token=...)` —— `ui/page_qa.py:317-320` **确实**传了 `on_token` | `_llm_complete_stream` 跑完 4 次重试失败 → `:171` 无条件回退 `_llm_complete` 又跑 4 次 | `rag.py:170` 回退前判 `last_error_kind` |
| **非流式路径**（探针 C） | `rag.answer(ctx, q, use_rerank=False)` 不传 `on_token` → 走 `:845 _llm_complete` | 答案是离线占位 → `check()` 报「未包含任何引用角标」→ `:853` **修复环**再打一遍完整重试链 | `:851` 后加降级守卫，`repairable = []` |

**证据**：S1（401，不可重试）修复前探针 C 是 **2 次** = 主答案 1 次 + 修复环 1 次。
这正好吻合"修复环翻倍"（1+1=2），而不是"流式+非流式"（那应该是 1+1 也等于 2…）。
真正的判据是 S2/S3/S4 的 8 次 = 4（主）+ 4（修复环）；若是流式回退则探针 C 根本不会走流式。
修完两处后 **S1 变 1 次、S2/S3/S4 变 4 次**，两条路都堵住了才成立。

### 修法（两处）

**① 流式回退（`services/rag.py:170`）** —— 按 team-lead 给的改法：

```python
if (not text.strip()) or ("【LLM 暂时不可用" in text):
    if getattr(getattr(ctx, "llm", None), "last_error_kind", None):
        if text.strip():
            return text
        from core.llm import degraded_content
        return degraded_content("流式调用已失败，已跳过重复的非流式重试")
    return _llm_complete(ctx, messages, temperature=temperature)
```

`last_error_kind` 为 **None** 时（模型真的就返回了空内容、链路本身没报错）**仍照常回退**——
那是真需要重试的场景，没改坏。

**② 修复环（`services/rag.py:851`）** —— 我追出来的第二处：

```python
repairable = [i for i in issues if "超出有效来源范围" not in i]
try:
    from core.llm import is_degraded_text as _is_deg
    _degraded = bool(_is_deg(text))
except Exception:
    _degraded = "【LLM 暂时不可用" in str(text)
if _degraded:
    repairable = []
```

理由：离线占位文案必然「未包含任何引用角标」，修复环会判为可修并再烧一遍完整重试链，
而占位内容再修也修不出角标。

### 前后对比（脚本真实输出，原样摘录）

**修复前**（`before_api.txt`）：

```
S1 探针C 问答链路 rag.answer()      SDK调用=2次
S2 探针C 问答链路 rag.answer()      SDK调用=8次  计划退避=[2.0, 2.0, 2.0, 2.0, 2.0, 2.0]  实测=13.2s
S3 探针C 问答链路 rag.answer()      SDK调用=8次  计划退避=[1.5, 3.0, 4.5, 1.5, 3.0, 4.5]  实测=19.2s
S4 探针C 问答链路 rag.answer()      SDK调用=8次  计划退避=[1.5, 3.0, 4.5, 1.5, 3.0, 4.5]  实测=19.4s
```

**修复后**（`after4_api.txt`，探针 C2 是 degrade-api 后加的**流式**探针）：

```
S1 探针C 问答链路 rag.answer()(非流式)  SDK调用=1次  实测=3.7s
  探针C2 问答链路 rag.answer()(流式)    SDK调用=1次  实测=1.6s
S2 探针C 问答链路 rag.answer()(非流式)  SDK调用=4次  计划退避=[2.0, 2.0, 2.0]       实测=7.4s
  探针C2 问答链路 rag.answer()(流式)    SDK调用=4次  计划退避=[2.0, 2.0, 2.0]       实测=7.5s
S3 探针C 问答链路 rag.answer()(非流式)  SDK调用=4次  计划退避=[1.5, 3.0, 4.5]       实测=10.4s
  探针C2 问答链路 rag.answer()(流式)    SDK调用=4次  计划退避=[1.5, 3.0, 4.5]       实测=10.4s
S4 探针C 问答链路 rag.answer()(非流式)  SDK调用=4次  计划退避=[1.5, 3.0, 4.5]       实测=10.3s
  探针C2 问答链路 rag.answer()(流式)    SDK调用=4次  计划退避=[1.5, 3.0, 4.5]       实测=10.0s
```

**8 → 4，且耗时近乎减半（S3/S4：19.2s/19.4s → 10.4s/10.3s）。**
流式探针 C2 同为 4 次，说明真实 UI 路径（`ui/page_qa.py` 传 `on_token`）也拿到了这个收益。

---

## 四之三 · 降级提示的图标前缀统一

team-lead 指出：后台任务面板那条"刷新后结果不可恢复"用了 `ℹ️`，
会被评委当成普通信息，读不出是降级态。已改，并把同一区块的兜底句一并统一：

| 语义 | 前缀 | 改后 |
|---|---|---|
| 该结果属于上一个会话 / 不可恢复 | `⚠️` | `⚠️ 该结果属于上一个会话：浏览器刷新后页面内容不可恢复，以下是后台任务留存的原始结果（可复制保存）。` |
| 无留存结果载荷 | `⚠️` | `⚠️ 该任务没有留存可展示的结果载荷。` |

**自查结论**：我本轮新增/改动的提示中，表达"降级 / 不可用 / 非真实输出 / 不可恢复"的全部已是 `⚠️`
（`⚠️ 离线占位响应：…`、`⚠️ {action}未完成：…`、`⚠️ 最终答案（离线降级）`、
`⚠️ 这个文件有 … MB，超过 … 上限`、`⚠️ 打开 PDF 失败：…`）；
`🩺 可能原因` / `🛠️ 怎么办` 沿用仓库既有的分组图标，属中性指引，未动。

### 第三轮：team-lead 授权改另外三处

上一版我报给 team-lead 的"顺带发现、不在独占范围"那三处，本轮他明确授权，**已改**，
且**严格只动这三行**，没有顺手改同文件里其他 `ℹ️`：

| 位置 | 语义 | 改后 |
|---|---|---|
| `ui/page_graph.py:142` | **不可用** | `⚠️ 图谱可视化 HTML 不可用（下方表格版图论指标不受影响）。` |
| `ui/page_writing.py:445` | **降级渲染** | `⚠️ {note}（降级渲染（等价拓扑图））` |
| `ui/page_writing.py:451` | **降级渲染** | `⚠️ {note} —— 降级渲染（等价拓扑图）` |

同文件中被保留的 `ℹ️`（判定为中性指引，按 team-lead 的分界不动）：
`ui/page_qa.py:266` `ℹ️ 已选择单篇锁定，多篇对比勾选本次不生效（两种模式互斥）。`、
`ui/page_qa.py:269` `ℹ️ 多篇对比至少需要 2 篇，本次按普通问答处理。`

---

## 四之四 · F2 · LLM 失败横幅「永不出现」（team-lead 第三轮派发）

### 现象与根因（两条叠加，缺一条都复现不出来）

`app.py` 的 `llm_failure_banner(ctx)` 消费 `ctx.llm.last_error`。它看不见有两条独立原因：

1. **读取时机**：横幅原本在 `_PAGES` 渲染**之前**执行。而 `last_error` 是**页面内那次 LLM 调用
   失败时**才被写上的——失败当轮横幅读到的永远是"上一轮"的值，要等下一次任意交互触发 rerun
   才出现。演示现场往往是"点了就看着结果不动"，于是**等于永不出现**。
2. **状态被清零**：`ui/sidebar.py:226` 每轮都调 `ctx.set_provider(pid, key)`。当 Key 来自
   `.env` / 环境变量、界面输入框为空时，`set_provider` 的重建条件
   `manual_key != self.llm.api_key`（`""` vs 真实 Key）**恒真** → 每轮重建 `LLMClient`
   → `last_error` 归零 → 就算时机对了也读不到。

### 修法（两条都修，且不动 `set_provider` 的重建语义）

1. **`app.py`**：把横幅块整体挪到 `_PAGES` 渲染之后（try/except 包裹不变）。
   代价：横幅显示在主体区**底部**而非顶部——换来的是**失败当轮即可见**。
2. **`ui/sidebar.py`**：**不改重建逻辑**（演示中途 Key 失效仍照常重建一次），
   只在"重建前后 Key 与平台都没变"这种**无谓重建**之后，把上一轮的 `last_error /
   last_error_kind` 回补回去。Key 真的变了就不回补，避免把上一次的失败原因带新 Key 上。

> **`services/state.py` 我授权了但一行没改**：team-lead 给的第二个点是 `state.py:132`
> （`set_provider` 的重建条件）。我也试过在那里把条件改成
> `manual_key and manual_key != self.llm.api_key`，但那会引入一个**真实回归**：
> 用户先手填 Key、再清空输入框想回退到 `.env` 时，客户端不会被重建，
> 会一直揣着旧的手填 Key。degrade-file 实测过的场景 11 的语义也会被绕过。
> 所以我把修复落在调用方（`ui/sidebar.py`），**保留 `set_provider` 原语义**。
> 这条是**代码判断 + 场景 11 复跑实测**，不是纯推断。

### 实测（`scripts/_degrade_drill_api.py` 附加实测 D + `scripts/_degrade_drill_fix.py` V4）

degrade-api 的附加实测 D（他上一轮记的是 `.env` 那条 `重绘一轮后=无`）：

```
附加实测 D：last_error 跨 rerun 存活验证（框架横幅能不能被用户看到）
  在侧边栏输入框真实填 Key               → LLMClient 每轮重建=False；点击当轮 last_error=有；重绘一轮后=有（横幅会渲染）
  Key 来自 .env/环境变量（输入框空）       → LLMClient 每轮重建=True；点击当轮 last_error=有；重绘一轮后=有（横幅会渲染）
```

我的 V4（新脚本 `scripts/_degrade_drill_fix.py`）：

```
  【判据】失败当轮 last_error = 有；同轮即出现横幅 = True（应为 True/True）
  【判据】重绘一轮后 last_error 仍存活 = 有（修复前为「无」，横幅消失）；该轮仍有横幅 = True
    → [warning] 大模型调用失败：API Key 无效或已过期（点击页面内其他功能仍可继续使用）
  【边界】Key 被换成新值后 last_error = 无（应为「无」，不把上一次的失败原因带新 Key 上）
```

三条边界核对（team-lead 要求必须同时满足）：

| 边界 | 实测 |
|---|---|
| Key 来自 `.env` 时横幅在主体区可见 | ✅ 重绘后仍有 `LLMClient 每轮重建=True` 但 `last_error=有`、横幅渲染 |
| 演示中途 Key 失效仍须重建一次 | ✅ `set_provider` 语义未动；degrade-file 场景 11 复跑 `11a/11b` 全绿、各页 0 未捕获异常、红框 0 |
| 横幅挪到 `_PAGES` 之后 | ✅ 已挪；失败当轮即见（V4 判据 1） |

degrade-api 的附加实测 F 也确认横幅没有外溢成红框、且会跟随到别的页面：
`检索页 error框=0 warning框=2` / 切到文献库 `warning框=1`。

---

## 四之五 · ② 的残留成功态：图谱页两个 `✅` + 问答页答案区

### 先说一个必须分开的事实：这里有**两类完全不同的降级**

我上一轮在 §九-4 把 `ui/page_graph.py:318/332` 列为"本轮最大残留风险"。真接线时才发现，
图谱页的降级**不是**离线占位，而是 `services/graph.py` 的**确定性生成**：

- `generate_review()`（`graph.py:840-842`）：LLM 拿不到正文 → 改用
  `_deterministic_review()` **用同一份图结构算出真实四段式综述**，并附一句
  `> 注：LLM 不可用，以下综述由 networkx 图谱结构确定性生成（未编造内容）。`
- `explore_future()`（`graph.py:1049-1051`）：同理，附
  `LLM 不可用，以下直接使用引用图可达性的确定性判定结果（未做语义改写、未编造证据）。`

所以它是"**内容真实，但撰写者不是大模型**"，而 `ui/common.py` 已有的
`llm_degraded_banner()` 那句 `⚠️ 离线占位响应…以下内容不是模型生成` 用在这里是**错的**——
会让人以为下面是空壳，白白丢掉一个真实能力（`资料/technical_doc.md` 第 9 章"能确定性生成
就别让模型生成"就是这条设计）。**我没有把两者合并成一句话。**

据此在 `ui/common.py` 新增（不碰 `core/llm.py` 的任何既有语义）：

```python
_OFFLINE_GEN_MARKS = (
    "LLM 不可用，以下综述由 networkx 图谱结构确定性生成",       # graph.py:842
    "LLM 不可用，以下直接使用引用图可达性的确定性判定结果",     # graph.py:1050
)
def is_llm_offline_generated(content) -> bool: ...
def llm_offline_generated_banner(what="内容"): ...
```

判定标记**直接取 `services/graph.py` 里已经写死的那两句**，不另造标记、不改服务层。

### 接线（与 `ui/page_library.py` / `ui/page_search.py` 同一套判定思路）

`ui/page_graph.py`：综述的 `st.status` 与前沿扫描分别做
`is_llm_degraded → is_llm_offline_generated → 正常成功` 三分支；
正文渲染块同样三分支（覆盖后续所有 rerun）。
`ui/page_qa.py`：历史问答渲染循环里，非拒答答案先判 `is_llm_degraded(_ans)`，
命中就先弹 `llm_degraded_banner()` 再渲染正文；并且 **`issues` 为空但降级时不再给
`✅ 引用校验通过`**，改显示 `⚠️ 离线降级结果未做引用校验（没有真实模型输出可供校验）。`

### 实测（`scripts/_degrade_drill_fix.py` V1/V2/V3，注入 401）

```
  ▶ 点击「📝 一键生成领域综述」命中=True  SDK调用=1次
     【判据】仍出现「✅ 领域综述已生成」= False（修复后应为 False）
     【判据】出现可见降级标识（非真实模型输出）= True（应为 True）
       → [warning] 大模型不可用：以下领域综述由本地确定性算法生成，**不是**大模型输出（内容取自本地真实数据，未编造）。

  ▶ 点击「🚀 Future Work 前沿扫描」命中=True
     【判据】仍出现「✅ 前沿方向扫描完成」= False
     【判据】出现可见降级标识（非真实模型输出）= True
       → [warning] 大模型不可用：以下前沿方向由本地确定性算法生成，**不是**大模型输出（内容取自本地真实数据，未编造）。

V3 问答页：
  【判据】答案区出现可见降级标识 = True
  【判据】仍给「✅ 引用校验通过」= False
    → [warning] 离线占位响应：LLM 不可用，以下内容不是模型生成，请勿作为答案引用。
    → [caption] ⚠️ 离线降级结果未做引用校验（没有真实模型输出可供校验）。
```

**反向核验 V2b（team-lead 要求"别把真的答出来了也误判成降级"）**——
把 SDK 换成返回正常正文，成功态必须照常：

```
  ▶ 点击「📝 一键生成领域综述」  【判据】正常成功仍报「✅ 领域综述已生成」= True；误弹降级标识 = False
  ▶ 点击「🚀 Future Work 前沿扫描」【判据】正常成功仍报「✅ 前沿方向扫描完成」= True；误弹降级标识 = False
```

（V2b 第一版我误报过一次 False Positive：探针没清掉上一组遗留的 `session_state["future"]`，
页面在为**旧内容**重放降级标识——那是对的行为。清空后复跑才是上面这组数。
这条我自己在报告里改口，见 §九-16。）

---

## 四之六 · ③ · `LLM_TIMEOUT` 默认值**不改**（附证据，不是"team-lead 说不让改"）

team-lead 的决定是 `LLM_TIMEOUT` 保持 **90.0**，`LLM_RETRY_BUDGET_SEC=45` 已落地。
我把支撑这个决定的**数字与出处**记在这里，免得后面有人再问一次为什么：

| 证据 | 出处 | 说明 |
|---|---|---|
| 写作页整章生成**链路端到端 44.6 s** | `资料/复盘/round8_fixR6_references.md:249`（指标行原文：**耗时（含入库，同一支 API）**） / `round8_worker14_doc.md:64` | 14364 字、40/40 逐字一致、**44.6 s**（修复前 139.3 s） |
| 同一支 API 的**抖动样本 158.4 s** | `资料/复盘/round8_fixR6_references.md:172/249/306` | 原文："glm-4-flash 单次调用延迟抖动极大（实测 44.6s ~ 158.4s）" |

**两处口径订正（都是我自己改口，见 §九-19）**：

1. team-lead 转述的"综述生成实测 44.6 s"其实是**写作页整章生成**，不是综述生成。
2. **更要紧的一条**：我上一版把它当成"单次 LLM 调用耗时"来用，**这个用法是错的**。
   degrade-api 提出判别点（是单次调用还是整条链路累计）后我回去核了原文——
   指标行的口径写得很清楚：**耗时（含入库，同一支 API）**，且产物是 **14364 字的整章**
   （写作链路是 outline + 多个 section，显然不止一次调用）。也就是说
   **44.6 s / 53.2 s / 158.4 s 都是链路累计（含入库），不是单次调用耗时**。
   `round8_fixR6_references.md:172` 那句"单次调用延迟抖动极大"是作者自己的措辞，
   与指标行的"含入库"口径并不一致 —— **两处口径冲突，我没有能力判定哪个对**。

**这把"不改默认值"的理由换了个底座，必须如实说**：

- 原来那条理由（"单次调用就有 44.6 s，45 s 会压线"）**现在没有实测支撑**——
  我拿不出本项目任何一次**单次** LLM 调用的耗时实测值。
- 但结论（**不改 `LLM_TIMEOUT=90`**）依然成立，理由换成：
  ① 预算（`LLM_RETRY_BUDGET_SEC=45`）**不裁剪任何单次调用的超时窗口**（degrade-api 附加实测 E
  已证：B=45 与预算关闭在 T=2 档实测完全一致），所以预算本身不会误杀慢但成功的调用；
  ② 在**没有单次调用耗时实测**的情况下把超时从 90 砍到 45，是拿一个未知分布去赌，
  而收益只有"最坏等待 90 s → 45 s"这 45 秒。**保守选项 = 不动**。
- **建议（未做）**：若真要动，先补一次"单次调用耗时"的实测（在写作/综述链路上埋点，
  取 P50/P95），再据此定 `LLM_TIMEOUT`。这个测量我**没有做**，不在本轮范围。

如果要更激进，正确位置不是 `config.py` 默认值，而是部署侧容器 env 注入
（`.env` 被 .gitignore 排除，改本机不会进仓库也不会进镜像），交 team-lead 定夺。

---

## 四之七 · F7 · 横幅只挂在「产生它的那一页」（team-lead 第四轮派发）

### 根因（team-lead 给的，比"文案不够温和"准确）

`llm_failure_banner(ctx)` 只判 `ctx.llm.last_error` 非空就渲染，而 `last_error` 一旦被写，
**直到下一次 LLM 调用成功才清空**（`core/llm.py` 的成功分支）。所以在问答页失败一次 →
切到文献库 / 系统架构，横幅照样挂着，文案还是 `⚠️ 大模型调用失败：…`——评委在**没触发失败的
页面**上看到它，会读成"这一页坏了"。

### 做法：用「本轮是否新写过 last_error」判定，**不用页面清单**

1. **`core/llm.py`**：新增 `self.last_error_seq = 0`，并把 7 处写入点收敛到新的
   `_note_error(self, reason, kind)`（内部 `seq += 1`）。
   写入点：`_create` 无客户端 / `_create` 异常 / `stream` 无客户端 / `stream` 异常 /
   `vision` 空图 / `vision` 超大图 / `vision` 编码失败 —— **7 处全部改完**
   （`grep` 复核：`self.last_error =` 只剩 `__init__` 与 `_note_error` 内部两处）。
   **成功分支（`self.last_error = None`，2 处）不 bump、也不清零 seq**——
   清零就分不出"旧失败"和"新失败"。
2. **`ui/sidebar.py`**：无谓重建回补时**连 seq 一起回补**。这是我在实现中撞到的一条：
   每轮重建 `LLMClient` 会把 `seq` 归零，`seq_after != seq_before` 就恒真，
   横幅反而会在**每一页**都显示（F7 直接反向）。已修。
3. **`app.py`**：基准快照 `_SEQ_BEFORE` 取在**侧边栏渲染之前**（这样侧边栏
   「🔌 测试连通性」失败也算"本页本轮产生"，否则 degrade-api 的实测 D/F 会翻回「无」），
   页面渲染后取 `_seq_after`；`seq` 变了且 `last_error` 非空 →
   `st.session_state["_llm_err_page"] = name`；最终只在
   `session_state["_llm_err_page"] == name` 时渲染横幅。
   横幅**保持在页面渲染之后**（F2 的结论不能被推翻）。

不用页面清单的理由（team-lead 原话精神）：清单会随功能漂移，写错等于把真横幅藏掉。

### 实测（`scripts/_degrade_drill_fix.py` V5，AppTest 真实走一遍）

两次失败注入的都是**同一个 401、错误字符串完全相同**——所以 ④⑤ 成立即证明靠的是 seq 而非字符串比对：

```
  ① 图谱页触发失败（命中=True，seq=1）→ 本页横幅 = True（应为 True）
  ② 切到【文献库】（未触发任何调用）→ 横幅 = False（应为 False）
  ③ 切回【图谱页】→ 横幅 = True（应为 True，失败页回来仍可见）
  ④ 在【检索页】触发失败（命中=True，seq=2）→ 本页横幅 = True（应为 True）
  ⑤ 再切回【图谱页】→ 横幅 = False（应为 False，横幅已挪到检索页）
     last_error（两页完全相同，故只能靠 seq 区分）= "Error code: 401 - {'error': {'code': '1001', ..."
  【总判据】F7 五步全对 = True
```

**独立交叉验证**（degrade-api 已把 F7 固化成他的「附加实测 F」，我复跑 exit=0）：

```
  【路径 1】侧边栏「🔌 测试连通性」失败 —— 横幅应可见（F2）
    失败当轮：seq=42  横幅=['大模型调用失败：API Key 无效或已过期（…）']  error框=0
      文献库 横幅=False error框=0    问答页 横幅=False error框=0    检索页(切回) 横幅=True error框=0
  【路径 2】主体区「🧹 引用含金量」失败 —— 横幅应可见，且切走后消失（F7 收敛）
    失败当轮：seq=43  横幅=[…]  error框=0
      文献库 横幅=False error框=0    问答页 横幅=False error框=0    检索页(切回) 横幅=True error框=0
```

两条路径都证明：**失败当轮可见（F2 保住）+ 切走即消失（F7 收敛）**，且横幅是 `st.warning`
不进红框计数，两边红框判据都安全。

### ⚠️ 实现中撞到并修掉的反向 bug（team-lead 要求写在显著位置）

> **任何"用前后快照差判定变化"的机制，都必须先确认承载快照的对象在本轮是否会被重建归零。**

`ui/sidebar.py` 每轮重建 `LLMClient`（Key 来自 `.env` 时），新对象的 `last_error_seq` 是 **0**。
于是 `seq_after != seq_before` **恒真** → `_llm_err_page` 每轮都被改写成当前页 →
横幅在**每一页**都显示，**F7 完全反向**。

第一版跑出来就是「切到文献库 → 横幅仍在」（应为 False），我据此才发现。
修法：sidebar 的"无谓重建回补"里**连 `last_error_seq` 一起回补**。

### 双路径实测（team-lead 点名要的两条，缺一不可）

**路径 1 = 侧边栏「🔌 测试连通性」失败**（演示现场第一反应会点的入口，
也是 degrade-api 报告里唯一推荐的现场动作）：

```
V5b：F7 路径 1 —— 侧边栏「🔌 测试连通性」失败
  点击命中=True → 失败当轮横幅 = True（应为 True）
    → [warning] 大模型调用失败：API Key 无效或已过期（点击页面内其他功能仍可继续使用）
  切到【文献库】→ 横幅 = False（应为 False）
  切回【检索页】→ 横幅 = True（应为 True，出问题的页面回来仍可见）
  【路径 1 判据】当轮可见 + 切走消失 + 切回重现 = True
```

> **这条曾经被打回过一次**：degrade-api 00:52 实测的 00:46 那版 `_SEQ_BEFORE` 取在
> `_PAGES` 循环之前，而**侧边栏更早渲染完** → 侧边栏失败时 seq 在取值前就 +1 →
> 判据恒 False → 侧边栏路径横幅永不显示（F2 回归）。
> 修法就是把基准快照挪到侧边栏渲染之前（见上文"两处偏离"）。

**路径 2 = 页面内调用失败**：见上面 V5 的 ①–⑤ 与 degrade-api 的实测 F。

## 四之八 · ② 残留成功态收口：Agent 页 / 阅读页翻译 / 写作页翻译

（team-lead 第四轮批准的三处，均为我上一轮只读排查报上去的候选）

| 位置 | 现象 | 修法 |
|---|---|---|
| `ui/page_agent.py:366` | `if finals: st.success("✅ 已完成")` —— **不判内容是不是离线占位** | 命中 `is_llm_degraded(_ans)` 时改弹 `llm_degraded_banner()`，不再报 ✅；正文仍渲染 |
| `ui/page_agent.py:275` | `progress("✅ 已完成（N 步 / M 个 token）")`，M=0 时也打 ✅ | token 数为 0 时改 `⚠️ 已结束，但大模型没有输出任何内容（N 步 / 0 个 token）` |
| `ui/page_reading.py:1337` 逐段翻译 | `translate_to_chinese` 降级返回占位串，被塞进「中英对照」= **假译文** | 任一段命中 `is_llm_degraded` 就先弹 `llm_degraded_banner()` |
| `ui/page_reading.py:452` 翻译缓存 | 占位串会被写进 `read_tr_cache`，下次"命中缓存"把假译文原样重放 | 降级串**不进缓存** |
| `ui/page_reading.py:1671` 划词翻译 | 流式吐出占位串当译文 | 非空但降级时补 `llm_degraded_banner()` |
| `ui/page_writing.py:534` 学术翻译 | `st.markdown(st.session_state["trres"])` 零判降级 | 降级时先弹 `llm_degraded_banner()` |

### V6：Agent 页实测（team-lead 点名要，因为它是默认落地页、曝光度最高）

走**真实路径**：点示例任务「用此任务 ▶」→ 后台线程跑 agent → 注入 401 →
等结果回填 → 断言。**不是只读代码**（后台任务在线程里，patch 必须一直开着，
否则线程会打真实外网请求）。

```
V6：Agent 页「✅ 已完成」在 LLM 降级时不得报成功（真实点按钮 + 后台任务）
  点击「用此任务 ▶」命中=True  SDK调用=7次  agent_runs=1条
  未捕获异常: 无
  【判据】仍出现「✅ 已完成」= False（修复后应为 False）
  【判据】出现可见降级标识「以下内容不是模型生成」= True（应为 True）
    → [warning] 离线占位响应：LLM 不可用，以下内容不是模型生成，请勿作为答案引用。
```

**阅读页 / 写作页翻译按 team-lead 定夺不补探针**，维持「已接线、未端到端实测」（§九-20）；
其中 `page_reading.py:452`「降级串不进翻译缓存」是逻辑正确性修复（防缓存污染），
已随 `_smoke_read.py` 60/60 通过。

**硬约束守住了**：`_smoke_read.py` 那条
`无 API Key 点「逐段翻译」给出中文 warning 且不崩 | 未检测到可用 API Key，无法调用翻译模型。`
**仍然 PASS**（那是"无 Key"分支，走的是 `llm.available` 判断，与降级判定是两条路）。

---

## 四之九 · 侧边栏「模型通道就绪」在已失败时的假就绪（degrade-api 观察，已改）

degrade-api 在问答页端到端实测里观察到：调用**已经失败**的情况下，侧边栏仍显示
`SUCC: ✅ Key 已填入，模型通道就绪`——语义上指"Key 已填"，但在刚失败的语境下会被读成"一切正常"，
与本轮在消灭的假成功同族。成本很低，已改：

```python
if getattr(ctx.llm, "last_error", None):
    st.caption("⚠️ Key 已填入，但最近一次大模型调用失败了"
               "（原因见主体区提示 / 点下方「🔌 测试连通性」复查）")
else:
    st.success("✅ Key 已填入，模型通道就绪")
```

**未动** `ui/sidebar.py:132`（`_llm_ping` 里消费 `last_error` 的那处判据）——那是别处的消费点。
改的只是 `set_provider` 之后的**展示分支**，判定依据仍是同一个 `last_error`。

### 第一版"修了等于没修"（degrade-api 01:19 实测打回，已修）

第一版写成 `if getattr(ctx.llm, "last_error", None):` —— **读的是 `set_provider` 重建之后的
值**，而 `.env` 供 Key 时每轮都会重建，重建后 `last_error` 是空的；更关键的是
**回补块在判断之后才执行**。所以那条分支永远不命中，实测两轮都还是
`✅ Key 已填入，模型通道就绪`。

按 team-lead 给的修法改成读 **`_prev[2]`（重建前的值）**——它正是回补块要还回去的那个值，
用它判断与"回补之后判断"等价，且不依赖执行顺序。

### V7 实测（`scripts/_degrade_drill_fix.py`）

```
【干净态基线】last_error 已清空 → 侧边栏报「✅ 模型通道就绪」= True（应为 True）
    → [success] Key 已填入，模型通道就绪
【失败当轮】last_error=非空
    仍报「✅ 模型通道就绪」= True（**已知滞后一轮**，见下）
    出现「⚠️ 最近一次大模型调用失败」= False（当轮应为 False，下一轮才翻）
【重绘一轮后】仍报「✅ 模型通道就绪」= False；失败措辞 = True
    → [caption] ⚠️ Key 已填入，但最近一次大模型调用失败了（原因见主体区提示 / 点下方「🔌 测试连通性」复查）
【V7 总判据】干净态报 ✅（未失败不该吓唬人）＋ 失败后下一轮翻成 ⚠️ = True
```

**必须如实说的"滞后一轮"**：这一行渲染在 `set_provider` 处，**早于**「🔌 测试连通性」那次调用，
所以失败**当轮**它拿不到失败信息，仍是 ✅；**下一轮**才翻成 ⚠️。
失败当轮并非没有提示——同轮侧边栏下方有「连通性测试未通过」，主体区也有框架横幅。
要消掉这一轮滞后就得把这一行改成占位符延后渲染，收益不抵改动，**我没做**。

### 顺带：降级提示补「离线演示仍可用」（team-lead 要求）

`ui/common.py:llm_failure_banner` 的「🛠️ 怎么办」现在多一句，实测已在横幅里：

```
🛠️ 怎么办：在左侧「接入方式」重新填写正确的 Key，再点「🔌 测试连通性」验证；切换到其他预设平台或本地 Ollama 继续演示；**离线演示仍可用**（本地解析 / 检索 / 图谱演示不受影响，可继续演示）；完整错误已记录到 storage/llm_failures.log
```

理由（F3 场景）：429 且 `Retry-After` 很长时，只给"重试/等待"会让现场以为系统卡死。

### 一条取证口径的坑（我自己踩的）

V7 加完之后 **V5b 一度从 True 变 False**：我新加的侧边栏文案含"大模型调用失败"五个字，
而 `_banner()` 是按这个子串匹配横幅的 → 把"切页后横幅消失"误判成"仍在显示"。
已把判据改成只认框架横幅独有的 `点击页面内其他功能仍可继续使用`。
**教训**：新增文案会污染按子串匹配的取证判据，改文案后必须回头核所有探针。

---

## 五、⑤ · 超大 PDF 体积闸门（场景 9）

- `config.py`：`MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 100)`；
- `ui/page_library.py`：`data = f.read()` 之后、`library.prepare_pdf` **之前**校验，
  超限 `st.warning` + `st.caption("怎么办")` + `st.stop()`。
- 应用层前置校验，与 `Dockerfile` 的 `--server.maxUploadSize=200`（HTTP 层硬上限）不冲突，**未改 Dockerfile**。

**修复前**：

```
    入库落盘动作（已隔离到系统临时目录）: [('huge_105mb.pdf', 110107177, 0.25)]
    红色 error 框数量: 0
      [success] 已入库：AI4S degrade drill line 0（20 页，0 个分块）
```

**修复后**：

```
    入库落盘动作（已隔离到系统临时目录）: 未触发 commit（解析被拦截）
    【核验⑤】体积闸门拦截（未进入 commit）= True；页面体积提示 = ['[warning] 这个文件有 105 MB，超过 100 MB 上限，已停止解析（现场解析会很慢且占用大量内存）。']
    红色 error 框数量: 0
      [warning] 这个文件有 105 MB，超过 100 MB 上限，已停止解析（现场解析会很慢且占用大量内存）。
      [caption] 🛠️ 怎么办：换一个更小的 PDF（建议 ≤ 100 MB）；或先点击左侧「🚀 一键加载演示数据」继续演示，其余功能不受影响。
```

脚本「[取证1]」也从 `无` 变成命中（degrade-file 把它改成了动态探测）：

```
  [取证1] 检出体积上限相关常量/参数: ['config.py:max_upload', 'config.py:maxuploadsize', 'ui\page_library.py:max_upload']
```

> `[标定]` 那一节是**直接调服务层 `library.prepare_pdf`**，按设计绕过 UI 闸门，
> 故四档（1/10/50/105 MB）仍全部"解析成功 20 页"——那是解析段成本标定，不代表闸门没生效。

---

## 六、⑥ · 刷新后「结果已转入页面内容」指向空历史记录（场景 12）

### 选了方案 (a)，理由

后台任务记录（`services/tasks.py` 的模块级 `_TASKS`）**本来就已经存了结果载荷**（`t["result"]`），
而模块级 dict 跨 session 存活——刷新后结果**并没有真的丢**，只是面板不去读它、反而谎称"已转入页面内容"。
既然载荷还在，直接渲染出来比改文案更彻底，且改动只落在 `ui/common.py` 一个文件，
不需要动 `services/tasks.py`、不需要落盘、不碰 `ui/sidebar.py`。**(a) 成本与 (b) 相当，故不退到 (b)。**

### 修法

1. `on_finish(t)` 的**返回值**被真正用起来：返回 False（结果为空 / 写入失败）时**不** `mark_consumed`；
   同时用 session 级 `set` 保证每个 session 只尝试交付一次，不会每帧重复回调。
2. 用 session 级 set 区分「本 session 消费的」与「上个 session 消费的」，后者换成诚实文案 +
   **把 `t["result"]` 直接渲染出来**。

### 前后对比（脚本真实输出，原样摘录）

**修复前** —— 12b：

```
    任务条目仍在模块级 dict: state=done consumed=True
    新 session 的 agent_runs 条数 = 0
    新 session 侧边栏任务中心文案: ['[caption] 结果已转入页面内容（历史记录 / 下载区）。']
    新 session 首页主区（历史记录区）文案: ['[info] 🧭 **三步上手**：① 点左侧「🚀 一键加载演示数据（v1/v7/BERT）」…', '[caption] 结果已转入页面内容（历史记录 / 下载区）。']
```

**修复后** —— 同一场景：

```
    任务条目仍在模块级 dict: state=done consumed=True
    新 session 的 agent_runs 条数 = 0
    新 session 侧边栏任务中心文案: ['[caption] ℹ️ 该结果属于上一个会话：浏览器刷新后页面内容不可恢复，以下是后台任务留存的原始结果（可复制保存）。']
    新 session 首页主区（历史记录区）文案: ['[info] 🧭 **三步上手**：① 点左侧「🚀 一键加载演示数据（v1/v7/BERT）」…']
    【核验⑥】刷新后侧边栏仍谎称「结果已转入页面内容」= False（修复后应为 False）
    【核验⑥】刷新后侧边栏诚实说明「属于上一个会话」= True（修复后应为 True）
    【核验⑥】刷新后结果载荷可被重新渲染出来 = True（修复后应为 True）
        → [caption] ℹ️ 该结果属于上一个会话：浏览器刷新后页面内容不可恢复，以下是后台任务留存的原始结果（可复制保存）。
        → [markdown] RESULT-BODY-1
```

`RESULT-BODY-1` 是后台任务的返回值，刷新后**真的被重新渲染出来了**。

**12c 已验证未回归**（degrade-file 特别提醒别改坏的那条）：

```
    等它跑完再渲染: state=done · 新 session agent_runs=1
    侧边栏完成态文案: ['[caption] 结果已转入页面内容（历史记录 / 下载区）。']
```

（修复前这里会打印两条重复的"结果已转入"，修复后只剩一条，顺带修掉重复渲染。）

---

## 七、越界改动披露

**我改了 `ui/page_search.py`（不在我的独占清单）**，只动 2 行：

1. import 行加 `show_llm_result`；
2. 把
   ```python
   st.success("✅ 分析完成")
   long_block(getattr(out, "content", str(out)), title="引用含金量分析报告", limit=200)
   ```
   换成
   ```python
   show_llm_result(out, success_msg="✅ 分析完成",
                   title="引用含金量分析报告", action="引用含金量分析", limit=200)
   ```

这是修复 ② 的必经点（全仓唯一有取证的 `✅ 分析完成`，也是 S1/S4 探针 B 的点击目标）。
改动前已确认该文件无人修改。

**`services/rag.py`** —— 不在最初的独占清单里，是 team-lead 在补派 F4 时明确派给我的
（"派发 F4 修复"），故本轮改了该文件（两处，见 §四之二）。其余越界文件一个没动。

**第三轮 team-lead 新增授权**：`app.py`（**只动了 `llm_failure_banner` 那一块的移位**，
其余行零改动）、`services/state.py`（授权了但**最终没改**，理由见 §四之四）、
`ui/page_graph.py`、`ui/page_qa.py`、`ui/page_writing.py`（**只动了 3 行 `ℹ️ → ⚠️`**）。
`ui/sidebar.py` 是 degrade-file 上一轮实测过 F2 边界的文件，team-lead 本轮派发时把它
归在我的所有权下，我改的是 `set_provider` 调用之后的回补逻辑（不碰 `set_provider` 本身）。

**`scripts/_degrade_drill_fix.py`（新建，我的独占）**：按与 degrade-api 的约定，
后续新增验证另起文件，不再写进他的 `_degrade_drill_api.py`。
内容 = V1/V2（图谱页两个 `✅`）、V2b（反向核验）、V3（问答页答案区）、V4（F2 横幅 + 边界）。

---

## 七之二 · 一条取证口径的坑（AppTest 会摘走 emoji 图标）

degrade-file 复验时发现他抓到的 `[error]` 正文**没有 `⚠️` 前缀**，而我转述时带上了。
我写了个 4 行探针实测，结论是：**源码里有 `⚠️ `，是 AppTest/Streamlit 把它摘走了。**

```python
st.error("⚠️ 甲：带 emoji 前缀")     # → value = '甲：带 emoji 前缀'
st.error("乙：无前缀")               # → value = '乙：无前缀'
st.warning("⚠️ 丙：warning 带前缀")  # → value = '丙：warning 带前缀'
st.caption("ℹ️ 丁：caption 带前缀")  # → value = 'ℹ️ 丁：caption 带前缀'
```

原因：`st.error` / `st.warning` 有 `icon` 参数，**body 以 emoji 开头时 Streamlit 把它当成
alert 的图标拆出去**，不再算正文；`st.caption` 没有 icon 概念，emoji 原样留在文本里。
真实浏览器观感不受影响（⚠️ 照样显示，只是作为红框图标而非正文字符）。

**对本报告的影响**：凡引用 AppTest 抓到的 `error.value` / `warning.value`，都不含被拆走的
emoji 图标；引用 `caption.value` 则包含。下文摘录一律按脚本原样输出，不手工补前缀。
这也说明全站走 `show_error` 的提示与 PDF 直出路径在本机制上**完全一致**，不存在"一条轻一条重"。

---

## 八、四条验收基线（**全部串行执行**）

```bash
PY=C:/Users/Administrator/anaconda3/envs/ai_research/python.exe
$PY -m compileall -q core services tools ui app.py config.py
$PY -m unittest discover -s tests
$PY scripts/_smoke_ui.py
$PY scripts/_smoke_read.py
```

| 基线 | 结果 | 关键输出 |
|---|---|---|
| ① `compileall` | ✅ 通过 | `compileall exit=0` |
| ② `unittest discover` | ✅ **18/18 OK** | `Ran 18 tests in 1.652s` / `OK` |
| ③ `_smoke_ui.py` | ✅ 通过（`exit=0`） | 9 个页面 0 未捕获异常 + 9 条故障注入全部 `友好提示=有  OK`；结尾 `✅ 冒烟通过：全部页面 0 未捕获异常` |
| ④ `_smoke_read.py` | ✅ **PASS 60 / FAIL 0** | 含 C 组退避断言 —— **预算未破坏历史重试语义**；含「无 API Key 点逐段翻译给出中文 warning 且不崩」—— **第四轮翻译改动没把这条文案改没** |

（以上为**第四轮全部改动落地之后**的最终一轮：F7 收敛（core/llm.py + app.py + sidebar）+
Agent/阅读/写作三处残留接线之后串行执行，四条全绿。）

另外串行复跑了两条别人的取证脚本做回归（均 `exit=0`）：

| 脚本 | 关键结果 |
|---|---|
| `scripts/_degrade_drill_api.py` | 探针 C = **4 次**（S1 = 1 次，内部分解 `_llm_complete=1 / _llm_complete_stream=0`）；探针 C2 = **4 次**（`_llm_complete=0 / _llm_complete_stream=1` = 流式回退被堵住）；S5/S6 红框 `本轮新增=1，页面总计=1`；网络话术 `True`；【核验②】`✅ 分析完成=False` / 明示文案 `=True`；**实测 D 两条都是「重绘一轮后=有」**；**实测 F（F7 收敛）两条路径全对：失败当轮可见 + 文献库/问答页 False + 切回 True** |
| `scripts/_degrade_drill_file.py` | 场景 7 / 8 红框 **1**；场景 9 闸门拦截 `True`；场景 11 `11a/11b` 全绿（各页异常=无、红框 0）；场景 12 【核验⑥】三条全绿 |

**关于 59 → 60 的说明（避免误读）**：我第一次跑 `_smoke_read.py` 是 `PASS 59 / FAIL 0`，
第二次（第二轮改动后）是 `PASS 60 / FAIL 0`。差异**不是我改的**——比对两次的检查项清单，
是脚本本身被人改过：把 `C _create：退避总时长 ≈ 1.5+3.0+4.5 = 9s`（墙钟断言）
换成了 `C _create：实际睡眠序列（经 retry_after 加工后）为 1.5/3.0/4.5` +
`C _create：退避总时长 == 1.5+3.0+4.5 = 9s（确定性断言，不再量墙钟）` 两条。
**两次都是 0 FAIL**，我的改动没有引入回归。

四条均一次通过，**没有修改任何断言去迁就**。
`ui/common.py` 最终 mtime 23:06:46、`ui/page_library.py` 23:02:28，均早于两条冒烟的执行时间，
确认基线跑的是最终代码。

---

## 九、我没有验证 / 不确定的项（诚实清单）

1. **全部是静态注入取证，没有真实断网、没有真实 401、没有真实浏览器 F5。**
   真实环境下的 DNS 失败文案、平台 401 的 body、浏览器刷新后的 session 行为都可能有细节差异。
2. **③ 的线上时长是外推值，不是实测值。** 真跑 90s 超时代价太大，我用 T=2s 档实测 +
   同一套循环模型外推。真实值还会叠加网络抖动与平台侧行为。
3. **③ 只把最坏等待从 369s 压到 ≈90s（≈ LLM_TIMEOUT），没有压到 45s 以内。**
   `LLM_TIMEOUT` 默认值按 team-lead 决定**保持 90.0 不改**，理由与实测证据见 §四之六
   （单次真实调用实测 44.6 s、抖动样本 158.4 s，45 s 会压线）。
   要更激进只能由部署侧容器 env 注入——**`.env` 被 .gitignore 排除，改本机不会进仓库也不会进镜像**。
   请 team-lead 定夺。
4. ~~**② 残留成功态是本轮最大残留风险**~~ → **本轮已修**（§四之五）：
   `ui/page_graph.py` 两个 `✅` 实测 True→False、`ui/page_qa.py` 答案区不再给 `✅ 引用校验通过`，
   并加了反向核验 V2b 确认没有误判。
   **第四轮已收口**（§四之八）：Agent 页 `✅ 已完成` / `progress ✅ 已完成`、阅读页逐段翻译 +
   划词翻译 + 翻译缓存、写作页学术翻译，共 **6 处**接线。
   **仍未覆盖（新的残留风险点）**：`ui/page_misc.py`、`ui/page_reading.py` 里**除翻译以外**
   的其他 LLM 链路（如原位 PDF 相关的 LLM 用法）我**没有逐一排查**；
   `ui/page_writing.py` 的**大纲生成**（`_outline_prompt` → `finalize_outline`）我读过代码，
   它有自己的 `meta["llm_error"]` 显式分支，**我判断不需要改，但没有实测**。
5. **② 的占位正文 markdown 仍在渲染**（就是那句 `【LLM 暂时不可用…】`）。
   degrade-api 复验时判断"有两条 warning 兜着，可接受"，我也认为可接受——
   它带着失败原因（401 / 超时 / 断网），删掉反而丢信息。若要彻底可在 `show_llm_result` 降级态直接 return。
6. **F4 已修**（本轮补派），但**根因与 team-lead 给的不完全一致**，已实测证伪并双向都堵：
   team-lead 指的 `rag.py:170`（流式回退）只覆盖**流式路径**；探针 C 实测的 8 次其实来自
   `rag.py:853` 的**修复环**（判据：S1 的 401 不可重试却是 2 次 = 1 主 + 1 修复）。
   两处我都改了，8→4 才成立。详见 §四之二。**流式探针 C2 的 4 次是实测**；
   但"真实浏览器里点一次问答按钮"的完整端到端调用数**我没有实测**（AppTest 里
   `st.form` 的提交按钮不可驱动），4 次是按探针路径推断。
7. ~~**F2 未修**~~ → **本轮已修**（§四之四）。改动落在 `app.py`（横幅移位）+ `ui/sidebar.py`
   （`last_error` 回补），**`services/state.py` 一行没改**，理由见 §四之四。
   **代价要如实说**：横幅现在显示在主体区**底部**而不是顶部——换来"失败当轮即可见"。
   如果 team-lead 认为底部不够醒目，需要换成"页面顶部占位符 + 页面渲染后回填"的写法，
   那是另一套实现，我没做。
8. **⑥ 只做了「模块级任务记录里留存的结果」这一层恢复，没有做磁盘持久化。**
   浏览器刷新（同进程）能恢复；**进程重启 / Docker 重启后仍然丢失**。
   我认为这符合场景 12 的定义（F5 = 新 session + 同进程），故未额外落盘。
9. **脚本归属**：我给 `scripts/_degrade_drill_api.py` 加了【核验②】与附加实测 E（degrade-api 的独占文件）。
   degrade-api 复跑确认与他的场景/实测 A–D 无冲突、EXIT=0，已采信并保留。
   他提出后续新增验证应另起文件（如 `_degrade_drill_fix.py`）以免并发覆盖 ——
   **我接受这个约定**；本轮第二轮我只改了自己上一版加的那一行判据字符串（把查"离线降级结果"
   改成查 `core/llm.py` 的稳定子串"以下内容不是模型生成"），没有新增内容。
10. **`scripts/_degrade_drill_file.py` 的并发编辑**：degrade-file 已确认他只在 22:40 改了 `s9_oversize`
    的 `[取证1]`（改成动态探测，因为我的闸门落地后原硬编码表述变成假话），后续不再动。
    我加的【核验⑤】【核验⑥】是独立新增行，没碰他报告引用的既有输出格式。
    我已通知他第 3 步导致红框标题变了（见 §二），请他同步更新报告引用。
11. **场景 7c / 7d（可修复的损坏 PDF）的"静默放行"未处理。** 修复前后行为一致：无提示、直接查重命中。
12. **未验证真实浏览器、未验证多模态 OCR 链路、未验证 >200MB 上传**（AppTest 不经真实 HTTP 上传通道）。
13. **`_MsgError` 的子类风险（推断，非实测）**：`friendly_hint()` 里用的是
    `type(exc) is _MsgError` 精确判断，若将来有人给 `_MsgError` 加子类，该分支会漏掉，
    服务层人话原因又会被吞成"发生内部错误"（= 场景 7/8 症状复发）。
    我**现在没有加子类也不打算加**，但已在 `_MsgError` 的 docstring 里写明这条维护注意。
    这是 degrade-file 提出的，我从代码判断成立，**没有实测**（构造子类再跑一遍的对照我没做）。
14. ~~**图标前缀另外三处没动**~~ → **本轮已改**（§四之三，仅三行）。
    **自查仍只覆盖团队点名的位置**：我没有做全仓 `ℹ️` 普查，
    `ui/` 下其他页面是否还有"降级 / 不可用"语义挂 `ℹ️` 的，**我没有逐一检查**（`ui/page_qa.py:266/269`
    那两处我判为中性指引、按 team-lead 的分界保留，这是我的判断，不是实测）。
15. **F4 的"配额不再重复烧"是推断**：我只实测了**调用次数 8→4**，
    没有实测平台侧配额/计费的实际消耗（那要真实 Key 与真实请求，被"不打真实外网"的纪律排除）。
16. **V2b 反向核验第一版我自己误报了一次 False Positive**（已修正并复跑）：
    探针没清掉上一组遗留的 `session_state["future"]`，页面在为**旧内容**重放降级标识——
    那是对的行为，我却记成了"误弹降级标识=True"。清空状态后复跑才是报告里那组数。
    **这条是我自己抓出来改口的**，不是别人发现的。
17. **问答页 `st.form` 的提交按钮**：degrade-api 说 AppTest 不暴露 `form_submit_button`，
    我读了 `streamlit/testing/v1/app_test.py:612` 的实现——**`AppTest.button` 的 docstring 写明
    它同时包含 `st.button` 和 `st.form_submit_button`**，所以**理论上可驱动**。
    但我**没有实际驱动成功过**：要走通真实问答还需要非空的文献库（chunks/chroma），
    我的 V3 因此改成"直接注入 `qa_runs` 走页面真实的历史问答渲染循环"。
    结论：V3 验的是**渲染分支**（正是 ② 残留成功态的落点），**不是**"点提问按钮"的端到端链路。
18. **我把 44.6 s 当成"单次 LLM 调用耗时"用过，这是错的（degrade-api 提出判别点后我改口）**。
    `资料/复盘/round8_fixR6_references.md:249` 指标行写的是**耗时（含入库，同一支 API）**，
    产物是 14364 字整章 → 是**链路累计**，不是单次调用。同文档 `:172` 又写"单次调用延迟抖动极大
    （44.6s ~ 158.4s）"，两处口径冲突，**我没有能力判定哪个对**。
    详见 §四之六：结论没变，但**支撑理由换了**，原来的理由我现在承认没有实测支撑。
19. **F7 已按 team-lead 决策修完（§四之七），但实现中我自己撞到并修掉一条反向 bug**：
    每轮重建 `LLMClient` 会把新的 `last_error_seq` 归零，`seq_after != seq_before` 恒真 →
    横幅在**每一页**都显示（F7 完全反向）。修法是 sidebar 回补时连 seq 一起回补。
    **另外两条未实测**：① 侧边栏「🔌 测试连通性」失败被归因到"当前页"是我特意设计的
    （快照取在侧边栏之前），但"用户此时在某个具体页面"这个组合我只跑了检索页一种；
    ② `last_error_seq` 在**长时间运行后溢出 / 并发多会话互相干扰**我没有分析（int 不溢出，
    但多会话共享同一个 Container 时 seq 是共享的，**跨会话可能串**，我没测）。
20. **第四轮三处残留中，Agent 页那处（`page_agent.py:366`）我没有写探针实测。**
    我给图谱页写了 V1/V2/V2b，给 F7 写了 V5，但 **Agent 链路在 AppTest 里驱动起来更麻烦**
    （后台任务 + 轨迹对象），我只做了 `grep + 读代码` 判断"降级时它会显示 ✅ 已完成"，
    **没有实测**。同理阅读页 / 写作页的翻译改动我也**没有**注入 401 实测过
    （只守住了 `_smoke_read.py` 那条"无 Key"分支的 PASS）。
    这三处的证明强度**低于**图谱页与 F7，请按"已接线、未端到端实测"读。
    我的倾向是**不改**：横幅只在 `last_error` 非空时出现（= 确实失败过，不是凭空挂），
    且文案已自带"（点击页面内其他功能仍可继续使用）"；若改成"只在相关页面显示"，
    用户在别的页面就完全不知道 LLM 挂了，等于退回"静默失败"。
    这是**我的判断，不是实测**（我没有做评委观感的实测）。
21. **F2 改法有一个未验证的 Key 时序**（我主动标注，别替我兜）：
    「手填 Key → 清空输入框 → 回退 `.env`」这一种时序下，我的回补条件
    `_now == _prev[:2]` **可能**成立（平台没变、解析出的 Key 也没变），
    从而把上一次的失败原因带到"回退后"的客户端上。**这个时序我没有构造探针实测**。
    已实测的边界只有一条：`Key 被换成新值后 last_error = 无`（V4）。
    同理，我说"改 `services/state.py:134` 的重建条件会引入回归"——那也是**读代码判断**，
    **不是实测**（degrade-file 一度把它写成"degrade-fix 实测"，已请他改成"代码判断 + 未实测"）。
22. **已知边界（degrade-api 报给 doc-audit-1-4 的结论，我不改）**：
    `services/rag.py:140` 的 `except Exception: return ""` 会把意外退化成**空串**——
    不抛异常，但也拿不到结构化占位。所以 `is_llm_degraded()` 对空串判 False 的边界
    **在 rag 层是真的会遇到**。我不改的理由：空串场景下没有"假成功"文案可拦截，
    强行判 True 反而会误伤正常路径。仅记录。
23. **`is_llm_offline_generated` 的判定依赖 `services/graph.py` 里那两句中文文案**：
    若将来有人改了 `graph.py:842` / `:1050` 的措辞而没同步 `_OFFLINE_GEN_MARKS`，
    降级标识会失效、成功态会复发。我**没有加任何自动化测试守护这个耦合**，
    只在常量上方写了注释标注行号。这是**推断的维护风险**，未实测。

---

## 十、给演示现场的一句话

`MAX_UPLOAD_MB=100`、双红框、假成功（检索页 / 图谱页 / 问答页三处）、PubMed 误报、
刷新丢结果、F4 重复调用、LLM 失败横幅——这些**无需任何现场配置**即可生效。

现场最该记住的三句人话（断网 / Key 失效时观众会看到的，都是 ⚠️ 不是 ✅）：

- `⚠️ 离线占位响应：LLM 不可用，以下内容不是模型生成，请勿作为答案引用。`
- `⚠️ 大模型不可用：以下领域综述由本地确定性算法生成，**不是**大模型输出（内容取自本地真实数据，未编造）。`
- `⚠️ 大模型调用失败：API Key 无效或已过期（点击页面内其他功能仍可继续使用）`

要把"最坏转圈"从 6 分钟压到 45 秒以内，需要**额外**在部署侧注入 `LLM_TIMEOUT=45`
（与 `LLM_RETRY_BUDGET_SEC=45` 配套）；**默认不改**——单次真实调用实测就有 44.6 s 的样本（§四之六）。
不改也能从 6 分钟降到 1.5 分钟。问答链路在 F4 修完后耗时已减半
（S3/S4 实测 19.2s/19.4s → 10.4s/10.3s @2s 档注入）。

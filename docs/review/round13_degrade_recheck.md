# Round 13 · 话术 5「网络降级演练」复核报告

> 角色：**只读复核**。本轮未修改任何业务代码、未执行任何 git 写操作。
> 唯一新建文件：本报告。
> 复核时间：2026-10-14 14:45–14:55（本机 Windows 11 / Python 3.11 / `ai_research` 环境）

---

## 0. 一句话结论

**12 个场景全部跑到了，全部「不崩溃、不直出 traceback、有降级路径」。**
但有 **3 个真问题**（不是崩溃，是"演示观感/时长"层面），按风险排序：

| # | 问题 | 影响场景 | 风险 |
|---|------|---------|------|
| P1 | 默认 `LLM_TIMEOUT=90` 且预算只截断"后续尝试"→ 超时最坏 **≈90s 空转** | S3（也波及 S4） | **高** |
| P2 | 429 最坏要等 **30s**（`Retry-After` 封顶 10s × 3 次）才降级 | S2 | 中高 |
| P3 | LLM 降级占位文本里的 **401/403/1001、429/1113 被"幻觉核验"当成未验证数字**列出来 | S1、S2 | 中 |

Round 12 报告里"已修复"的项（残留成功态、F2 横幅、F7 横幅收敛、场景 12 会话文案），本轮**重新跑脚本验证，全部仍然成立**（见 §4）。

---

## 1. 取证方法与口径（先说清楚哪些是真跑、哪些是模拟）

| 项 | 说明 |
|----|------|
| 驱动方式 | `streamlit.testing.v1.AppTest` **进程内真跑 `app.py`**，页面文案从元素树原样抓取（含折叠区 `st.code`） |
| 是否打真实外网 | **否**。两个脚本全程 `mock` 注入，不消耗任何真实 token/额度 |
| LLM 注入层 | patch `openai.resources.chat.completions.Completions.create`（业务代码与 SDK 的边界；其上的错误分类/重试/降级/UI 全部真实执行） |
| arXiv 注入层 | patch `requests.adapters.HTTPAdapter.send` → `ConnectionError`（arxiv 库自带的 3 次重试会真实跑） |
| PubMed 注入层 | patch `urllib.request.urlopen` → `URLError`（`services/extern.py:67` 就是直连） |
| 非 mock 的额外实测 | 超时预算截断（§3.1）—— 用 **10s/5s 等比缩放**真跑一遍，不是纯外推 |

**跑了的三个脚本**（均 exit 0）：

```
$PY scripts/_degrade_drill_api.py    → 4m34s，输出 38119 字节（S1–S6）
$PY scripts/_degrade_drill_file.py   →   59s，输出 22471 字节（S7–S12）
$PY scripts/_degrade_drill_fix.py    →   14s，复核 Round 12 的修复是否仍成立
```

⚠️ **基线提醒**：本轮复核的是**当前工作区**（`git status` 有 14 个文件的未提交改动，
`tools/definitions.py` / `core/memory.py` / `services/state.py` / `app.py` 等仍在被其他 agent 并行编辑）。
若这些文件后续再改，**S10/S11/S12 的结论需要重跑**（它们吃 session/state 逻辑）。
报告中给出的行号以 2026-10-14 14:55 为准，实测 `services/rag.py` 的行号在 10 分钟内已经漂移过一次（884→883）。

---

## 2. 总表（12 场景）

| 场景 | 崩溃 | 提示文案 | 降级路径 | 演示风险 |
|------|------|---------|---------|---------|
| 1 智谱 401 | ❌ 否（未捕获异常 0，可见区 traceback=False） | 友好。人话分类 + 原因 + 怎么办 + 离线提示 | `core/llm.py:_classify_error:244` → `core/llm.py:_fake_response:324` → `ui/common.py:llm_failure_banner:390` | **中**（P3 核验噪声） |
| 2 智谱 429 | ❌ 否 | 友好，且明确写"离线演示仍可用" | 同上 + `core/llm.py:_sleep_backoff:308`（尊重 Retry-After） | **中高**（P2 30s + P3 噪声） |
| 3 智谱超时 | ❌ 否 | 友好 | 同上，**但 `config.py:123 LLM_TIMEOUT=90` 使最坏 ≈90s** | **高**（P1） |
| 4 智谱断网 | ❌ 否 | 友好（"无法连接大模型服务"） | 同上 | 中（DNS 失败快，实测 9.0s；但若黑洞路由则同 P1 ≈90s） |
| 5 arXiv 不可达 | ❌ 否 | 友好，一行红框 | `ui/common.py:net_error:339` → `show_error:303` | 低 |
| 6 PubMed 不可达 | ❌ 否 | 友好，一行红框 | 同 S5 | 低 |
| 7 损坏 PDF | ❌ 否 | 友好（7a/7b）；**7c/7d 被 MuPDF 自愈 → 走查重命中，不出错** | `services/library.py:584` → `ui/page_library.py:268` | 低（7c/7d 有观感小坑，见 §3.4） |
| 8 0 字节 PDF | ❌ 否 | 友好 | 同 S7 | 低 |
| 9 超 100MB PDF | ❌ 否 | 友好，**解析前拦下**，黄框不是红框 | `ui/page_library.py:235`（`MAX_UPLOAD_MB` @ `config.py:144`） | 低 |
| 10 上传后立刻切页 | ❌ 否 | 无报错；`pending` 跨页残留，切回后查重框复活 | `ui/page_library.py:293`（`st.session_state.pending`） | 低 |
| 11 演示中 Key 被清空 | ❌ 否 | 友好，7 个页面全部无异常 | `ui/common.py:llm_guard:345` | 低（11a 有坑，见 §3.5） |
| 12 浏览器刷新 | ❌ 否 | 友好且**诚实**（不再谎称"结果已转入页面内容"） | `ui/common.py:868` + `services/tasks.py:_TASKS:24` | 低 |

**绝对量词核对**：上表 12 行，每行都有对应的实测输出段落（§3），无一行是只读代码推断。

---

## 3. 逐场景实测证据

### 3.1 S1 智谱 401（Key 失效）

- SDK 调用 **1 次**（401 不重试，符合设计），侧边栏实测 **2.1s**，问答链路 **2.4s**。
- 未捕获异常 0；可见区 traceback 直出 = **False**；折叠区"🔍 查看技术细节"含 traceback = **False**。
- 故障后其余页面：文献库 异常=0/控件=11、问答页 异常=0/控件=6、检索页 异常=0/控件=13 → **全部可用**。

用户实际看到的原文（AppTest 抓取）：

```
[warning] 大模型调用失败：API Key 无效或已过期（点击页面内其他功能仍可继续使用）
[warning] 连通性测试未通过：请检查 Key / 平台额度 / 网络。
[caption] 🩺 可能原因：Key 填写有误 / 已过期 / 被平台重置；平台账号额度已用完
[caption] 🛠️ 怎么办：在左侧「接入方式」重新填写正确的 Key，再点「🔌 测试连通性」验证；
          切换到其他预设平台或本地 Ollama 继续演示；**离线演示仍可用**
          （本地解析 / 检索 / 图谱演示不受影响，可继续演示）；
          完整错误已记录到 storage/llm_failures.log
```

答案区（真实渲染字符串）：

```
【LLM 暂时不可用，请稍后重试。⚠️ 离线占位响应：LLM 不可用，以下内容不是模型生成，
请勿作为答案引用。原因：API Key 无效或已过期（HTTP 401/403），请在侧边栏检查 Key 后重试；
原始错误：Error code: 401 - {'error': {'code': '1001', 'message': '身份验证失败：API Key 无效或已过期'}}】
```

✅ 好：没有假成功态（实测 `✅ 分析完成`=False、`✅ 引用校验通过`=False）、明示"不是模型生成"=True。

❌ **P3 问题（实测原文）**：占位文本下面还挂着一段——

```
[warning] 以下内容未在来源文本中找到对应依据（已自动标注，请谨慎对待）：
- 401
- 403
- 1001
- HTTP 401
```

**站在评委面前的观感**：系统在说"我瞎编了 401/403/1001 这几个数字"。实际它只是把降级文案里的 HTTP 状态码当成了答案里的数字去核验。
根因见 §5 修复方案 C。

---

### 3.2 S2 智谱 429（限流）

- SDK 调用 **4 次**，`Retry-After=2` 时退避 6.0s，**实测 6.3s**。
- 未捕获异常 0，可见区 traceback=False，其余页面全部可用。

用户看到的原文：

```
[warning] 大模型调用失败：触发平台限流（429）（点击页面内其他功能仍可继续使用）
[caption] 🩺 可能原因：请求过于频繁，平台暂时限流；当前 Key 的并发/额度已打满
[caption] 🛠️ 怎么办：等待 30 秒到 1 分钟后重试；减少连续操作，或切换其他平台；
          **离线演示仍可用**（...）；完整错误已记录到 storage/llm_failures.log
```

❌ **P2 实测（429 最坏等待时长标定，全 `time.monotonic` 实测）**：

| Retry-After | 计划退避 | 实测 | 说明 |
|---|---|---|---|
| 无 | 9.0s `[1.5,3.0,4.5]` | 9.0s | 回落默认退避 |
| 3 | 9.0s `[3,3,3]` | 9.0s | 尊重服务端 |
| **60** | **30.0s** `[10,10,10]` | **30.0s** | 被 `core/llm.py:47` 封顶到 10s，但 **3 次累计 30s** |

→ 服务端要求等 60s 时，用户要盯着转圈 **30 秒**才看到降级提示。这就是"演示时会尴尬"的场景。
同 S1，答案区也挂着 `核验说明：429、1113` 的噪声（日志中答案串结尾为 `...}}】\n\n> ⚠️ 核验说明：以下`）。

---

### 3.3 S3 智谱超时 / S4 断网

文案都好：

```
S3: [warning] 大模型调用失败：大模型调用超时（点击页面内其他功能仍可继续使用）
    [caption] 🩺 可能原因：平台响应慢或网络抖动（已自动重试 3 次）；生成内容过长导致超时
S4: [warning] 大模型调用失败：无法连接大模型服务（点击页面内其他功能仍可继续使用）
    [caption] 🩺 可能原因：本机网络不可达或代理拦截了出网请求；自定义端点 Base URL 填写有误
```

注入立即抛异常时实测都是 **9.0s**（4 次调用 + 9s 退避）。**但这是乐观值**——真实超时要真等满超时窗口。

❌ **P1（本轮最重要的发现）**：

代码事实（`core/llm.py:487-505`）：

```python
for attempt in range(MAX_RETRIES + 1):     # 0,1,2,3 → 最多 4 次调用
    try:
        resp = self._client.chat.completions.create(**kwargs)
        ...
        if not info.retryable or attempt >= MAX_RETRIES:
            break
        if budget and (time.time() - t_start) >= budget:   # ← 只在这之后才检查
            break
```

预算检查**只在尝试之间**发生，**不裁剪任何单次调用的 timeout 窗口**（这是有意设计，为了不缩短成功请求的时间窗）。
因此：首次尝试必然消耗完整的 `LLM_TIMEOUT` 秒。

`config.py:123` `LLM_TIMEOUT = 90.0`、`config.py:128` `LLM_RETRY_BUDGET_SEC = 45.0`
→ **首次尝试 90s 结束，90 ≥ 45 → 不再重试 → 用户等 ≈90 秒才看到降级提示**。

本轮额外做的**等比缩放实测**（不是外推）：

```
LLM_TIMEOUT=10, LLM_RETRY_BUDGET_SEC=5（线上 90/45 的 1/9）
  → 实测 elapsed 11.7s，kind 被分类，降级标记=True
  → 只跑了 1 次尝试就被预算截断（若无限重试应为 4×10+9=49s）
```

按比例放大 → 线上即 **≈90s**。
（该缩尺实测里 `kind='unknown'`，是我手工构造 `APITimeoutError` 少了 `request` 参数的注入瑕疵，不是产品缺陷；
分类正确性由 `_degrade_drill_api.py` 附加实测 A1 单独证明：真实 `httpx2.ReadTimeout` → `kind='read_timeout'`。）

补充：脚本自身也标了 `[外推，非实测] 预算关闭 → 4×90+9 ≈ 369s（约 6 分钟）`。这一条我**没有实测**，只作为关掉预算的后果提示。

---

### 3.4 S5 arXiv / S6 PubMed 不可达

两者对称，实测 **0.1s** 出结果，未捕获异常 0：

```
[error] 操作失败：arXiv 检索。网络连接失败，请检查网络后重试（外网不可达或被代理拦截）
[caption] 🩺 可能原因：演示现场外网不可达或被代理拦截；arXiv / PubMed 接口限流或临时不可用
[caption] 🛠️ 怎么办：重试一次；如需离线演示，请点左侧「🚀 一键加载演示数据」并使用📁/💬/🕸️页面；...
```

- 红框数量 = 1（无重复堆叠）。
- ⚠️ **gotcha（可接受但要知道）**：折叠区「🔍 查看技术细节」**内含 traceback = True**。
  默认折叠、页面上看不见；**但如果评委手贱点开，会看到完整 traceback**。
  代码位置 `ui/common.py:319-323`（`st.code(tb[-3000:])`）。
  判据口径是"可见区不直出"，这条满足；是否要把 traceback 从 UI 里彻底拿掉，由 team-lead 定（我倾向保留，排查有用）。

---

### 3.5 S7 损坏 PDF / S8 0 字节 / S9 超 100MB

| 子场景 | 构造 | 未捕获异常 | 页面原文 |
|---|---|---|---|
| 7a 内容截断 55% | 1.17 MB | 无 | `[error] 打开 PDF 失败：Failed to open stream` + 🩺/🛠️ 两行 |
| 7b 结构损坏（30–85% 置 0） | 2.13 MB | 无 | `[error] 打开 PDF 失败：PDF 没有任何可读页面（0 页）` |
| 7c 抹掉 `%PDF-` 头 | 2.13 MB | 无 | **无报错** → `[warning] 语义查重命中：库中已有《1706.03762v1.pdf》，内容相似度 100.0%。` |
| 7d 轻微截断 92% | 1.96 MB | 无 | **无报错** → 同上，相似度 100.0% |
| 8 0 字节 | 0 字节 | 无 | `[error] 打开 PDF 失败：Cannot open empty stream.` |
| 9 105 MB | 110.11 MB | 无 | `[warning] 这个文件有 105 MB，超过 100 MB 上限，已停止解析（现场解析会很慢且占用大量内存）。` |

- 7c/7d ⚠️ **gotcha**：损坏较轻时 MuPDF 能自愈打开，于是**"损坏的 PDF"被静默当成重复文献**——
  页面显示"查重命中 100%"，而不是"这个文件可能损坏"。不崩溃，但**演示时如果正好拿了个坏文件，会很困惑**。
  （另：7d 时 MuPDF 往 stderr 打了 `MuPDF error: format error: object is not a stream`，**只进终端，不进 UI**，无观感影响。）
- S9 闸门**实测生效**：`【核验⑤】体积闸门拦截（未进入 commit）= True`，耗时 0.04s，红框 0（用的是黄框 warning，语气更软，好评）。
- S9 附带标定（`services.library.prepare_pdf` 同链路，页数固定 20 页只变体积）：
  1MB→0.15s / 10MB→0.15s / 50MB→0.20s / 105MB→0.15s。
  → **"100MB 会卡死"其实不成立**，纯文本解析不受体积影响；真正的成本在页数（541 页实测单页 0.004s，全本约 2s）和扫描件 OCR。
  这说明 100MB 闸门是**保守护栏**，不是性能必需——如果演示要传大文件，可以临时把 `MAX_UPLOAD_MB` 调大。

---

### 3.6 S10 上传后立即切页（状态竞争）

```
10a 解析完成+查重命中后立刻切走：切导航成功=True，menu=💬 2. 跨文献问答与溯源
    切走后 pending 仍残留=True；问答页未捕获异常=无；问答页上串出文献库内容=否
10b 再切回文献库：pending 仍在=True
    复活文案: ['[warning] 语义查重命中：库中已有《1810.04805v2.pdf》，内容相似度 100.0%。']
10c 解析途中切走（在 prepare_pdf 内部改写 menu 等价模拟）：
    本轮/下一轮未捕获异常=无；下一轮 menu=🕸️ 5. 引用图谱与综述
    pending 残留=True；图谱页串出文献库东西=否；残留 st.progress 条数=0
```

✅ 结论：**无崩溃、无串页、无残留进度条**。pending 跨页保留是**有意设计**（等用户做版本决策），切回后对话框复活是正确行为。风险低。

---

### 3.7 S11 演示中途 Key 被清空

```
11a 只清空侧边栏输入框（.env 兜底仍在）→ ctx.llm.available=True，未捕获异常=无
11b 清空输入框 + 移除进程内环境变量 → ctx.llm.available=False
    llm_status={'available': False, 'reason': '未配置 API Key，大模型不可用', 'kind': None}
    7 个页面逐个渲染：异常=无（📁💬📖🔍🕸️✍️🤖 全绿）
    文献库「🤖 抽取标题…」→ [warning] 元数据智能抽取需要大模型，但未检测到可用 API Key。
                          + [warning] 尚未配置 API Key：请在下方「接入方式」选择平台并填入 Key；
                            无 Key 也可使用底部按钮一键加载离线演示数据。
    Agent「用此任务 ▶」  → [warning] Agent 执行需要大模型，但未检测到可用 API Key。
```

✅ 全站无崩溃、每个大模型入口都有人话兜底、且都给了"加载离线演示数据"的出口。风险低。

⚠️ **gotcha（11a）**：如果 Key 来自 `.env`，**清空侧边栏输入框并不生效**（`.env` 兜底仍在，`available` 仍为 True，侧边栏也不显示"尚未配置 API Key"）。
→ 现场想演示"Key 没了会怎样"时，**清输入框是没用的**，得改 `.env` 或换错 Key。这条要写进彩排脚本提醒。

---

### 3.8 S12 浏览器刷新（session 丢失）

```
12a 任务在旧 session 已跑完并被消费 → 刷新
12b 新建 session：任务条目仍在模块级 dict（state=done, consumed=True）
    新 session agent_runs 条数 = 0
    侧边栏文案: [caption] ⚠️ 该结果属于上一个会话：浏览器刷新后页面内容不可恢复，
                以下是后台任务留存的原始结果（可复制保存）。
    结果载荷可被重新渲染 = True（RESULT-BODY-1 原样渲染出来了）
12c 任务正在跑时刷新：state 仍=running，侧边栏 progress 条数=1；
    跑完后 state=done、新 session agent_runs=1、结果正常转入页面
12d 后台任务抛异常 → 侧边栏显示 失败原因：RuntimeError: 模拟后台任务抛异常
```

✅ Round 12 的修复**仍然成立**：
- `【核验⑥】刷新后侧边栏仍谎称「结果已转入页面内容」= False`（不再撒谎）
- `【核验⑥】刷新后侧边栏诚实说明「属于上一个会话」= True`
- `【核验⑥】刷新后结果载荷可被重新渲染出来 = True`

全部子场景未捕获异常=无。风险低。

---

## 4. Round 12 "已修复"项的独立反测（`_degrade_drill_fix.py`，exit 0）

不采信上一轮结论，全部重跑：

| 判据 | 期望 | 实测 |
|---|---|---|
| 图谱页「一键生成领域综述」失败后仍出现「✅ 领域综述已生成」 | False | **False** ✅ |
| 出现可见降级标识（非真实模型输出） | True | **True** ✅ |
| Frontier 扫描失败后仍出现「✅ 前沿方向扫描完成」 | False | **False** ✅ |
| 正常成功时**误弹**降级标识 | False | **False** ✅（无误伤） |
| F7 横幅五步（失败→切页→切回→换页失败→再切回） | 全对 | **True** ✅ |
| 侧边栏路径：当轮可见 + 切走消失 + 切回重现 | True | **True** ✅ |
| Agent 任务失败后仍出现「✅ 已完成」 | False | **False** ✅ |
| 干净态（未失败）报 ✅ 通道就绪 + 失败后下一轮翻 ⚠️ | True | **True** ✅ |
| 问答页失败仍给「✅ 引用校验通过」 | False | **False** ✅ |
| F2 横幅：失败当轮可见 + 重绘一轮后 `last_error` 仍存活 | True/有 | **True/有** ✅ |

→ **Round 12 的 9 项修复在本轮工作区全部有效，没有回归。**

---

## 5. 高风险项修复方案（**交 team-lead 执行，我未改代码**）

### 【修复 A】P1 —— 超时最坏 90s（**高**，必改）

**改 `config.py:123` 与 `config.py:128`**

现状：
```python
123: LLM_TIMEOUT = _env_float("LLM_TIMEOUT", 90.0)
128: LLM_RETRY_BUDGET_SEC = _env_float("LLM_RETRY_BUDGET_SEC", 45.0)
```

改为：
```python
123: LLM_TIMEOUT = _env_float("LLM_TIMEOUT", 25.0)
128: LLM_RETRY_BUDGET_SEC = _env_float("LLM_RETRY_BUDGET_SEC", 15.0)
```

**为什么这两个必须一起改**（只改一个会更糟）：
- 只把 `LLM_TIMEOUT` 降到 30 而预算仍是 45 → 首次 30s 后 30<45 → 继续重试 → 第二次再 30s → 实测会变成 **≈61.5s**，比现在的 90s 好但没根治，而且比 25s 差。
- 一起改成 25/15 → 首次 25s 结束，25 ≥ 15 → 立即 break → **最坏 ≈25s**（缩尺实测已验证该截断语义有效）。

**代价（必须知情）**：单次成功请求的时间窗从 90s 缩到 25s。`glm-4-flash` 常规 2–8s 足够，
但**写作页长综述 / Agent 多步任务**里若某次调用真的需要 >25s，会从"慢但成功"变成"降级占位"。
如果担心这个，折中方案是 `45.0 / 20.0`（最坏 ≈45s）。

改完验证：
```bash
$PY scripts/_degrade_drill_api.py   # 看 S3 的「实测」列与「附加实测 A/E」
```
另建议彩排时真点一次写作页长综述，确认 25s 内出得来。

---

### 【修复 B】P2 —— 429 最坏 30s（中高）

**改 `core/llm.py:47`**

现状：
```python
47: RATE_LIMIT_RETRY_AFTER_CAP = 10.0   # 429 尊重 Retry-After，但封顶 10s，避免交互界面长睡
```

改为：
```python
47: RATE_LIMIT_RETRY_AFTER_CAP = 5.0    # 429 尊重 Retry-After，但封顶 5s，避免交互界面长睡
```

效果：服务端 `Retry-After=60` 时，最坏从 `3×10=30s` 降到 `3×5=15s`。
配合 `MAX_RETRIES=3`（`core/llm.py:41`）不变。

验证：`_degrade_drill_api.py` 的「附加实测 B：429 限流的最坏等待时长」，
看 `Retry-After=60` 那行的「计划退避」应从 `30.0s [10.0,10.0,10.0]` 变成 `15.0s [5.0,5.0,5.0]`。

---

### 【修复 C】P3 —— 降级占位文本被"幻觉核验"当数字列出来（中）

**改 `services/rag.py:883` 与 `services/rag.py:890`**（行号以 14:55 为准，实测会漂移）

现状：
```python
883:         unverified = find_unverified_numbers(text, all_texts) + find_unverified_codes(text, all_texts)
...
890:         unverified = find_unverified_numbers(text, texts) + find_unverified_codes(text, texts)
```

改为：
```python
883:         unverified = [] if _degraded else (find_unverified_numbers(text, all_texts) + find_unverified_codes(text, all_texts))
...
890:         unverified = [] if _degraded else (find_unverified_numbers(text, texts) + find_unverified_codes(text, texts))
```

**依据**：`_degraded` 在 `services/rag.py:857-860` 已经算好了（用 `core.llm.is_degraded_text`），
且在 `:861` 已用它做了"降级时不进自动修复环"的判断——**同一段代码里已经承认了"这是占位文本、不是模型输出"**，
但漏了下面的数字核验，导致占位文案里的 `401 / 403 / 1001 / 429 / 1113` 被当成"模型编的数字"报出来。
这个改动只是把已有的判断延伸到核验环节，不引入新概念。

影响面：**只影响降级态**（`_degraded=True` 时本来就不该有任何核验输出）。正常成功路径 `unverified` 完全不变。

验证：
```bash
$PY scripts/_degrade_drill_api.py   # 附加实测 H：看「判据 H-3」附近的 WARN 列表里
                                    # 不应再有「以下内容未在来源文本中找到对应依据…401、403、1001」
```

---

### 【修复 D】可选 —— S5/S6 折叠区 traceback（低）

**改 `ui/common.py:321-323`**（`show_error` 内）：
```python
321:         tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
322:         if tb.strip():
323:             st.code(tb[-3000:])
```
如果要彻底杜绝评委点开看到 traceback：把 323 行的 `tb[-3000:]` 换成只留最后 3 帧，或整段删掉（异常已由 `log_exception:218` 落盘 `logs/errors.log`）。
**我的建议是不改**——排查时这条很有用，而且默认折叠、不违反"不直出"的要求。由 team-lead 定。

---

## 6. 我没能真正跑到 / 没能验证的部分（诚实清单）

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| 1 | **物理断网**（拔网线 / 关 WiFi） | ❌ **未测** | S4 是 `APIConnectionError` mock 注入。真实断网下 DNS 失败会更快失败（无 90s 风险），但**黑洞路由（SYN 无响应）会退化成连接超时 → 同 P1 的 90s**。这一档我没法在本机安全模拟。 |
| 2 | **真实 90s 挂起** | ❌ **未测** | 用 10s/5s 等比缩尺实测替代（§3.3）。`LLM_TIMEOUT=90` 下的 90s 与"预算关闭时 369s"两条**是推算，非实测**。 |
| 3 | **真实浏览器 F5 刷新** | ⚠️ 半测 | S12 用 AppTest 新建 session 模拟，未真开浏览器按 F5。WebSocket 断连/重连时序、以及"刷新时请求正在飞行"的浏览器行为未覆盖。 |
| 4 | **真实 100MB 文件落盘上传** | ⚠️ 半测 | 未往磁盘写 100MB。用"页数固定 20 页、只放大字节体积"的合法 PDF 喂同一条链路（脚本已说明等价性）。**Streamlit 上传组件本身在 100MB 时的表现（浏览器内存/分片）未测**。 |
| 5 | **并发/多用户下的降级** | ❌ **未测** | 全程单 session。评委同时开两个浏览器的场景没跑。 |
| 6 | **真实额度耗尽**（不是 401 也不是 429，可能是 403 或 200 带错误 body） | ❌ **未测** | 智谱额度耗尽的具体错误码我没真实触发过（要真花钱）。`_classify_error` 对 403 已归类为 `auth`，但**200 + 错误 body 的情况可能落到 `unknown`**。 |
| 7 | **扫描件 PDF 的 OCR 降级**（`scanned_no_ocr`） | ❌ **未测** | S7 的四个子构造都没触发 OCR 分支（`library.py:510`），因为没配多模态 Key。演示若现场传扫描件且 Key 失效，走的是另一条路径。 |
| 8 | 行号稳定性 | ⚠️ | 复核期间 `services/rag.py` 行号从 884 漂到 883。§5 的行号是 14:55 快照，**执行前请重新 grep 确认**。 |

---

## 7. 给彩排脚本的一句话提醒（建议同步给 drill-script）

1. **超时/断网那一档，别在台上等**——默认配置最坏 ≈90s。要么先做修复 A，要么现场直接切「🚀 一键加载演示数据」讲离线链路。
2. **现场演示"Key 失效"时，清空侧边栏输入框没用**（`.env` 兜底）。要真失效得改 `.env` 或换成错 Key。
3. 如果真遇到 429/401，页面上会多出一行"以下内容未在来源文本中找到对应依据：401、403、1001"——**这是已知显示瑕疵（P3），不是系统幻觉**，讲的时候直接说明，别被评委问住。

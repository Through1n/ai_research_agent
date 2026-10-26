# Round 2 · 评委D 评审报告 —— 性能鲁棒性 + LLM 调用统一 + 交付物完整性

> 评委：D（性能 / 鲁棒性 / LLM 统一 / 交付物验收）
> 日期：2026-10-11
> 方法：全部结论基于真实环境（`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`，streamlit 1.63.0）实测，压测一律在 `C:\tmp\judgeD\` 沙箱进行（项目 storage 未写入任何压测数据；中途一次沙箱失效污染已完整回滚，备份见 `storage/library.json.judgeD_20261011_143930.bak`）。

---

## 1. 总评

| 维度 | 得分（0-10） |
|---|---|
| 性能实测达标度 | **7.0** |
| LLM 统一度 | **9.0** |
| 交付物完整度 | **4.5** |
| 全局整洁度 | **7.5** |
| **综合** | **6.5 / 10** |

**一句话结论**：长文档流水线与 LLM 统一重试两项工程化改造**实测全部达标且质量很高**（分块/进度/超时跳过/退避计时全部与宣称一致），但**损坏 PDF 静默入库的幽灵文献缺陷属实且比评委A报告的更严重**（部分损坏 PDF 会以"正常文献"身份入库并丢失 65% 正文、零告警），**Agent 的 Python 沙箱工具因 `[-3000]` 切片 bug 100% 必崩**，且交付物侧存在"README 主截图路径指向不存在的目录、两份技术文档内容分叉、性能数字过时"三项硬伤。

---

## 2. 扣分清单

| 严重度 | 问题 | 文件:行号 | 证据（实测输出） | 扣分 | 修复建议 |
|---|---|---|---|---|---|
| 🔴阻断 | 损坏/非 PDF 文件被**静默入库为 0 页幽灵文献**。`_empty_prepared` 返回 dict 无 `error` 键，UI 的 `elif prepared.get("error")` 永不命中，走 else 分支 `_commit()` | `services/library.py:97-106`（返回键列表实测无 `error`）、`ui/page_library.py:66-79`、`tools/definitions.py:211-219`（Agent 工具路径同样不检查） | 沙箱实测：`C_text.pdf`（纯文本字节）→ `page_count=0` → `commit()` 后 `ctx.papers keys=['C_text.pdf'] page_count=0 chunks=0`，UI 分支判定为"★ else → _commit()【静默入库】" | -1.0 | `_empty_prepared` 增加 `"error": str(error) or None` 键；`commit()` 入口对 `page_count==0 and not full_text` 直接拒绝；`tools/definitions.py:211` 同步检查 |
| 🔴阻断 | **部分损坏 PDF 更危险**：截断 PDF 被 PyMuPDF 修复后照常解析，入库为"看似正常"的文献，正文静默丢失 65%，`failed_pages=[]`、无任何告警 | `services/library.py:229-242`（open_doc 成功即认为健康，无文本量健全性校验） | `A_trunc.pdf`（1/3 截断）：`page_count=541, full_text=309048 字符`，对照完整 PDF `881344 字符` → 文本丢失率 65%，`failed_pages=[]`、`有error键=False`，UI 判定"★ else → _commit()【静默入库】" | -0.5 | 解析后校验：`page_count>0 但 非空页占比<30%` 时置 `error/degraded`，UI 显著告警 |
| 🔴阻断 | **Agent 的 Python 沙箱工具 100% 必崩**：`p.stdout[-3000]` / `p.stderr[-1500]` 是索引不是切片，stdout 或 stderr 短于上限必抛 IndexError。短输出、长输出、空 stderr 全部命中 | `tools/definitions.py:306` | `t_run_python(None,"print('hello')")` → `EXC IndexError: string index out of range`；`"x"*5000` 也崩（stderr 为空串 `''[-1500]` 崩）；经 `Registry.execute`（tools/registry.py:52-55）吞掉后 Agent 只拿到 `{"error":"IndexError:..."}`，**该工具从未产出过一次成功结果**。技术文档 4.4/6.x 与演示脚本宣称的"沙箱执行"实为不可用 | -1.0 | 改为 `p.stdout[-3000:]`、`p.stderr[-1500:]`；补一条 stdout/stderr 均为空的单元测试 |
| 🟡重要 | `json_chat` 在 LLM 不可用/返回友好文案时抛 `ValueError`，违反 `core/llm.py` 文件头"全部失败不抛异常"的统一承诺（调用点全靠外层 try/except 兜住） | `core/llm.py:258-261` | 实测：`json_chat` 对伪响应文案 → `✘ ValueError: 无法解析为 JSON：【LLM 暂时不可用…】`；受影响调用点 `core/planner.py:24`、`core/router.py:53`、`ui/page_library.py:148/282/303` | -0.4 | `json_chat` 解析失败时返回 `{}` 并填 `last_error`，或文档明示"本方法可能抛 ValueError" |
| 🟡重要 | API Key 不做合法性校验：非 ASCII（中文占位符）Key 进 SDK 后在拼 header 时抛 `UnicodeEncodeError`，每次调用白跑 3 轮重试（浪费 9s）才落伪响应 | `core/llm.py:133-144`（`__init__` 未校验 key）、`core/llm.py:162-174` | 项目真实日志 `storage/llm_failures.log` 86 条中 **20 条 UnicodeEncodeError**（root cause 实测：`httpx2/_models.py:84 value.encode("ascii")`，本机 `.env` 的 key 含中文，长度 14）；`query_expansion.py:83` 为高频触发点 | -0.4 | `__init__` 中 `if api_key and not api_key.isascii(): 视为未配置`（available=False + 明确提示"Key 含非法字符"） |
| 🟡重要 | `t_run_python` 临时目录 `ai4s_code_*` 从不清理（无 try/finally、无 rmtree、无 TTL），且因上面的 IndexError 每次"成功失败"都泄漏一个目录；`extern.download` 失败时 `NamedTemporaryFile(delete=False)` 同样泄漏 | `tools/definitions.py:299`（mkdtemp 无任何清理）、`tools/definitions.py:206-209` | 实测调用 2 次即新增 1 个 `ai4s_code_*` 目录（含 cell.py）；对照 `services/writing.py:737/768-772`（wcopilot_tikz_ 有 `finally: rmtree`）与 `ui/page_reading.py:279/314-320`（fig_ 有 finally），同项目内标准不一 | -0.4 | 补 `finally: shutil.rmtree(work, ignore_errors=True)`；`fig_inplace_` 同类已做对，可照抄 |
| 🟡重要 | README 主截图链接指向不存在的 `资料/screenshots/main.png`；"技术文档（PDF）（21 页"实为 19 页 | `README.md:5`、`README.md:199` | `ls 资料/screenshots` → `No such file or directory`；实测 PDF `page_count=19` | -0.5 | 改为 `资料/截图/p*.png` 并重生成截图；页数更正 |
| 🟡重要 | 技术文档双份交付且内容分叉：`technical_doc.md`（无任何截图、概述仅 424 字）与 `技术文档.pdf`（19 页、9 图、结构完整）是**两套不同的文档**，且 md 更晚修改（10-10 15:55 > pdf 14:03）却没有合并回 PDF | `资料/technical_doc.md`（全文 0 个 `![]()` 图片引用）、`资料/技术文档.pdf` | 实测：md `图片引用 ![](): 【无】`；PDF 有 9 张图（图3-1~图6-1）。评审若只读 md 会得到"无配图"结论，若只读 PDF 则丢失 md 第 6.4 节配置表等内容 | -0.5 | 二选一作为唯一事实源：以 PDF/HTML 为准把 md 删掉或重生成，避免评委看到两份互相矛盾的"技术文档" |
| 🟡重要 | 性能宣称数字过时、不可复现：文档/README/视频脚本均宣称 541 页教材"**1523 分块 / 26.3s / 292MB**"，本轮实测同文件为 **1301 分块 / 24.6s / 283MB**（round-1 改动解析后分块数变了，文档没跟着更新） | `README.md:63/167`、`资料/technical_doc.md:55/177`、`资料/技术文档.pdf`（5 处）、`资料/演示视频脚本.md:18` | 实测真实教材：`耗时 24.56s（45.4ms/页）、chunks=1301、peak=283.1MB、toc=181、failed=0`。1523≠1301，"全部可一键复跑"的数字对不上 | -0.4 | 用 `scripts/verify_official.py` 重跑后统一刷新三处数字；或注明"round-1 解析升级后分块数由 1523→1301" |
| 🟡重要 | `commit()` 文件名未清洗：含 `/` 的文件名导致 `UPLOAD_DIR/filename` 建路径失败直接 FileNotFoundError（Windows 下另有非法字符风险，且构成路径穿越面） | `services/library.py:345` | 实测：`filename="a/b/c.pdf"` → `FileNotFoundError: ...uploads\a\b\c.pdf` | -0.3 | 入库前 `os.path.basename()` + 非法字符替换 |
| 🟢轻微 | `cleanup_stale_figure_dirs` docstring 声称"永不抛异常"，实际 `max_age_hours=None` 抛 TypeError、`"abc"` 抛 ValueError（当前调用点只传常量，未实际触发） | `ui/page_reading.py:186-213` | 实测：`None → TypeError`、`'abc' → ValueError`；`-1/0/非法 roots` 均不崩 | -0.1 | 入口 `try: age_h=float(max_age_hours) except: return 0` |
| 🟢轻微 | `storage/uploads/figs/` 为持久插图目录，无 TTL/容量清理（与 fig_* 临时目录双轨） | `ui/page_library.py:190`、`services/parsing.py:591` | 实测现存 3 文件 189.4 KB，随使用线性增长 | -0.1 | 复用 `cleanup_stale_figure_dirs` 思路给 figs/ 加 TTL |
| 🟢轻微 | `Dockerfile` 无 `.dockerignore`：`COPY . .` 会把 `.env`（真实 Key）、`storage/`、`dist/`、`build/`、`.git` 全部打进镜像（密钥入层 + 镜像膨胀） | `Dockerfile:12`、`docker-compose.yml` | `test -f .dockerignore` → 不存在；`.env` 在项目根且被 COPY | -0.2 | 新增 `.dockerignore`（.env/storage/dist/build/.git/version_history）；或 COPY 改为白名单 |
| 🟢轻微 | `requirements.txt` 缺 `matplotlib`/`graphviz`/`pywebview`（代码侧均有 try/except + find_spec 兜底，不崩，但 Docker 镜像里这三级降级直接少两级，`ui/page_writing.py:332` 还把 "matplotlib" 列为可选渲染类型） | `requirements.txt`、`services/writing.py:784/833`、`ui/page_writing.py:332` | 第三方 import 扫描对照 requirements 逐项核对 | -0.1 | requirements 增加可选依赖注释行，或 page_writing 在无 matplotlib 时隐藏该选项 |

---

## 3. 541 页长文档实测数据

### 3.1 合成夹具（`mk541.py` 生成 541 页，0.46MB，878,100 字符）

| 指标 | 实测值 | 判定 |
|---|---|---|
| prepare_pdf 耗时 | **2.21 s（4.1 ms/页）** | ✅ |
| 峰值内存 | **243.6 MB（起始 201.3，增量 +42.3MB，psutil RSS）**；`prepared["peak_mem_mb"]=243.6` 与独立 psutil 采样一致 | ✅ 数字真实 |
| 分块数 | 812 chunks（CHUNK_SIZE=1200/OVERLAP=150） | ✅ |
| 失败页数 | 0 | ✅ |
| 进度回调次数 | **11 次**（50/100/…/500/541），间隔均匀 0.15s | ✅ 恰为"每 50 页 + 收尾" |
| 分批加载（不一次性解析） | ✅ `PDF_BATCH_SIZE=20`（library.py:16），逐批解析+逐批建 chunks（:261-295），full_text 用 StringIO 流式拼接（:259,277-279） | ✅ |
| 单页超时跳过 | ✅ 注入第 100/400 页挂死 + `timeout_per_page=30`：总耗时 62.09s ≈ 基线 + 2×30s；`failed_pages=[{"page":100,"error":"单页处理超过 30 秒，已跳过"},{...400...}]`；`on_page_error` 回调同样收到两页；其余 539 页全部正常，进度回调仍为 11 次 | ✅ 真 30s、真跳过、真继续 |
| 失败后继续 | ✅ 空文本页数恰为 2（仅被注入页），非空 539 页 | ✅ |
| commit 耗时 | 14.15 s（812 chunks 向量化 upsert 为主要开销，属预期） | — |

### 3.2 真实教材（`论文样本/UnderstandingDeepLearning_02_09_26_C.pdf`，21.31MB，541 页）

| 指标 | 本轮实测 | 文档宣称 | 差异 |
|---|---|---|---|
| 耗时 | **24.56 s（45.4 ms/页）** | 26.3 s | ≈一致 |
| 峰值内存 | **283.1 MB**（psutil，`prepared.peak_mem_mb=283.1`） | 约 292 MB | ≈一致 |
| 分块数 | **1301** | **1523** | ❌ 不一致（round-1 解析改动后未更新文档） |
| 失败页 | 0 | — | ✅ |
| 进度回调 | 11 次（50…500,541） | — | ✅ |
| toc | 181 条标题 | — | ✅ |

### 3.3 内存探针 `scripts/_memory_probe.py`

- psutil 已安装（7.2.2）：自验 `峰值 82MB / 增量 +40MB（申请 5×8MB 精确命中）`，后端=psutil，数字为真实进程 RSS ✅
- 屏蔽 psutil 后：自动降级 tracemalloc，`backend_name()=tracemalloc`，`friendly_note()` 给出中文降级说明，`report()` 正常返回，**全程不抛异常** ✅
- 生产接入：`ui/page_reading.py:48` 带 `_NullProbe` 兜底（:54），`services/library.py:221-225` psutil 缺失时 `process=None` 不崩 ✅

### 3.4 磁盘清理实测

| 机制 | 结论 | 证据 |
|---|---|---|
| `fig_*` 临时目录 try/finally | ✅ 有效 | `ui/page_reading.py:279`（mkdtemp）→ `:314-320` finally rmtree |
| 陈旧目录清理函数 | ✅ 真实可用 | 造 48h 陈旧 `fig_stale_dir` + 新鲜 `fig_fresh_dir` + 非 `fig_` 目录 + `fig_` 前缀文件：只删陈旧目录（返回 1），新鲜/非目录/文件全部保留 ✅；`roots` 指向不存在目录返回 0 不崩 ✅ |
| 扫描后自动清理 | ✅ | `ui/page_reading.py:329`（扫描结束自动跑）、`:720`（独立按钮） |
| 系统残留现状 | ✅ 干净 | 系统 temp 下现存 `fig_*` 目录数 = 0 |
| 反例 | ❌ `ai4s_code_*` 泄漏 | 见扣分清单（tools/definitions.py:299） |

---

## 4. LLM 重试机制实测（core/llm.py）

把 API 指向必失败端口 `http://127.0.0.1:9/v1`（连接拒绝 + 计时打点 + time.sleep 间谍）实测：

| 验证项 | 要求 | 实测 | 判定 |
|---|---|---|---|
| 常量 | 3 次退避 | `MAX_RETRIES=3`，`RETRY_BACKOFF=(1.5, 3.0, 4.5)`，`SDK_MAX_RETRIES=0`（实测 SDK client.max_retries=0，无叠加放大） | ✅ |
| 调用次数 | 3 次失败后兜底 | `chat` 实测 **4 次 HTTP 调用**（1 首发 + 3 重试） | ✅ |
| 退避间隔 | 1.5 / 3.0 / 4.5 | **time.sleep 实测序列 = [1.5, 3.0, 4.5]**（精确命中）。注：端到端"调用启动间隔"实测为 3.67/5.05/6.54s，差额即单次 HTTP 请求自身开销 ≈2.1s，非实现偏差 | ✅ |
| 是否抛异常 | 不抛 | `chat`/`stream`/`vision`/`call_llm_with_retry`/无 Key 分支全部返回友好伪响应 `【LLM 暂时不可用，请稍后重试。错误摘要：…】`，`last_error` 同步填充 | ✅（json_chat 除外，见扣分） |
| stream() 兜底 | 应有 | 实测同样 4 次调用 + [1.5,3.0,4.5] sleep，失败 yield 友好文案；且"已吐出内容不再重试"防重复逻辑在（core/llm.py:223-225） | ✅ |
| 失败日志 | prompt 前 2000 字 + model + caller | `storage/llm_failures.log`（沙箱重定向验证）：字段 `ts/error/error_type/model/caller/n_messages/messages_preview` 齐全；超长 prompt 截断为 2018 字符含"已截断"标记；`caller=test_llm_retry.py:146 <module>()` 精确定位；**生产日志 86 条佐证机制真实在跑** | ✅ |
| 模块级入口同通道 | 应一致 | `call_llm_with_retry` 间谍实测同样 4 次调用 + [1.5,3.0,4.5] | ✅ |
| `_backoff_for` 边界 | — | -1/0→1.5、1→3.0、2→4.5、3/4/99→4.5、"x"/None→1.5，钳制安全 | ✅ |

### 向后兼容性（其他考生依赖的接口）

实测签名与行为全部未破坏：

```
LLMClient.__init__(self, api_key='', base_url=None, model=None, vl_model=None, timeout=90.0)
LLMClient.chat(self, messages, model=None, temperature=0.2, tools=None, tool_choice='auto', json_mode=False)
LLMClient.stream(self, messages, model=None, temperature=0.3)
LLMClient.vision(self, prompt, image_bytes, image_mime='png', temperature=0.1)
LLMClient.json_chat(self, messages, model=None, temperature=0.1)
LLMClient.available: property
call_llm_with_retry(messages, *, model=None, temperature=None, max_tokens=None, response_format=None, tools=None, tool_choice=None, **kw)
parse_json(text) / _fake_response(content)
```

`parse_json` 7 组用例（裸 JSON / ```json 围栏 / 前后噪声 / 围栏数组 / list / dict 直通 / 中文嵌套）全部正确解析，非 JSON 抛 `ValueError` ✅；`_fake_response` 结构与 openai 响应兼容（`.choices[0].message.content` / `finish_reason` / `tool_calls`）✅。依赖方实测清单：`services/graph.py`、`library.py`、`parsing.py`、`query_expansion.py`、`rag.py`、`writing.py`、`services/state.py`、`scripts/_smoke_read.py`。

### 裸调用残留清单（`client.chat.completions.create` / `OpenAI(`）

**运行链路零残留。** 全项目（排除 dist/）仅 `core/llm.py:143`（构造 client）与 `core/llm.py:164`、`:207`（chat/stream 两处统一入口）命中，正是统一实现本身。其余命中：

| 文件:行号 | 性质 | 是否计入残留 |
|---|---|---|
| `core/llm.py:143/164/207` | 统一入口自身 | 否 |
| `version_history/app_streamlit_v1.py:102/297` | v1 单体原型历史存档，`scripts/release.py:52` 明确排除打包，无任何 import | 否（归档） |

附：`services/extern.py`、`services/citation_quality.py` 的 `urllib.urlopen` 均为 arXiv/PubMed/GitHub API 调用，非 LLM 调用，不违规。

---

## 5. 交付物缺失清单（话术7 · 8 章节 + 话术8 · 6 项）

### 5.1 技术文档 8 章节 —— 以 `资料/技术文档.pdf`（19 页）为准逐条核对

| # | 章节 | 状态 | 证据与质量评语 |
|---|---|---|---|
| 1 | 项目概述（1000 字） | **缺（字数不足）** | PDF 第 1 章（1 项目概述 → 2 需求分析 之间）实测 **477 汉字**；`technical_doc.md` 第 1 章 **424 汉字**。两版都远低于 1000 字要求（缺口 ~55%）。内容质量尚可（背景/目标用户/赛题定位齐全），主要是量不足、缺"与现有工具的本质差异"一段 |
| 2 | 需求分析 | **有** | PDF 2.1 科研痛点 / 2.2 赛题六大模块覆盖矩阵 / 2.3 非功能需求；md 第 2 章有功能需求表。质量良好 |
| 3 | 系统架构设计（含架构图） | **有** | PDF 3.1 分层架构（文字版四层）+ **图 3-2 系统架构设计页**（真实分层架构图，p8）；md 第 3 章为 ASCII 架构图。质量良好 |
| 4 | AI 技术方案（模型选型+RAG+Prompt+抗幻觉） | **有** | PDF 4.1~4.7：模型选型与 Provider 抽象、Dense+Sparse+RRF+重排、溯源、语义去重、多智能体路由、三层 Guardrails、Function Calling 工具链。覆盖最全的一章，质量高 |
| 5 | 核心功能实现（**每模块配截图**） | **需更新（模块一缺专属截图）** | 图 5-1 问答/图 5-2 阅读翻译/图 5-3 检索追踪/图 5-4 图谱/图 5-5 写作 —— 模块二~六各 1 图；**模块一（PDF 管理与细粒度解析）无专属编号截图**（仅第 3 章的图 3-1"系统主界面（文献库管理页）"勉强覆盖）。且全部截图摄于 10-10 14:03，早于 round-1 对 `ui/*.py` 的集中改动（10-10 16:14 ~ 10-11 04:12） |
| 6 | 测试与部署（含 Docker） | **有，需更新** | PDF 6.1 官方数据集实测 / 6.2 质量保障 / 6.3 错误边界与降级 / 6.4 部署；md 6.3 有 Docker 段。单元测试 18 项本轮实测 `Ran 18 tests ... OK` ✅。但性能数字（1523 块/26.3s）与现状（1301 块/24.6s）不符，"Python 沙箱执行"实测不可用（见 🔴），此章的宣称需要修正 |
| 7 | 创新点 | **有** | PDF 7.1 技术创新 / 7.2 应用创新；md 第 7 章 6 条。质量良好 |
| 8 | 局限性与未来规划 | **有，需补强** | PDF 8.1 当前局限（诚实声明）/ 8.2 后续展望；md 第 8 章"局限性与后续规划"。有局限性清单 ✅，但"未来规划"只有展望性文字，**无结构化 roadmap**（md 全文"未来规划"0 命中、"展望"0 命中） |
| — | 双文档分叉问题 | **缺（治理）** | `technical_doc.md`（20KB、0 图、概述 424 字）与 `技术文档.pdf/技术文档.html`（19 页、9 图）是**内容不同的两套技术文档**，且 md 修改时间更晚却没有回灌 PDF。必须二选一收敛，否则评委看到互相矛盾的交付物 |

### 5.2 README 6 项（话术8）逐条核对

| # | 项目 | 状态 | 证据与质量评语 |
|---|---|---|---|
| 1 | 演示 GIF 或截图 | **缺（链接失效）** | `README.md:5` `![screenshot](资料/screenshots/main.png)` → `资料/screenshots/` 目录**不存在**（实际在 `资料/截图/`）。GitHub 上将显示裂图。`资料/截图/` 有 p0~p8 共 9 张（与 9 个页面一一对应），但摄于 10-10 14:02-03，早于 24 个 UI 文件的集中修改（至 10-11 04:12），**已过时** |
| 2 | 一键运行 3 行命令 | **有** | `README.md:11-15`：`git clone && cd` / `pip install -r requirements.txt` / `streamlit run app.py`。质量合格；另提供 vbs 双击启动、Conda 手动、Docker 三种方式（:113-134），超出要求 |
| 3 | 功能矩阵表格对比竞品 | **有** | `README.md:42-51`：8 项能力 vs 传统 RAG / 通用 Copilot 三列对照，含 ✅/❌/⚠️。质量良好 |
| 4 | 在线演示链接占位 | **有（占位）** | `README.md:7` `[在线演示（待部署）](https://example.com)`。占位符合要求，注意提交前需替换，`example.com` 易被评委视为未完成 |
| 5 | 团队信息 | **有（占位）** | `README.md:236-241`：参赛队伍/组别/成员/指导教师均为 `__________` 占位。符合"占位"要求 |
| 6 | 赛题要求对照表 | **有** | `README.md:210-220`：P0 五项 + P1 四项逐条标注实现位置与 ✅ 状态，可定位到文件/函数。质量良好 |
| + | 附带问题 | **需更新** | `README.md:199` 称技术文档"21 页"实为 19 页；`:63/:167` 的"1523 分块"与实测 1301 不符；`论文样本/UnderstandingDeepLearning_02_09_26_C.pdf` 确认在库 ✅ |

### 5.3 Docker / 视频脚本

| 项 | 状态 | 说明 |
|---|---|---|
| Dockerfile 可构建性 | **基本可构建，需补 .dockerignore** | `python:3.11-slim` + requirements 安装 + `CMD ["streamlit","run","app.py","--server.port=8501","--server.address=0.0.0.0"]`：启动命令正确，CLI 参数会覆盖 `.streamlit/config.toml` 里的 `address="127.0.0.1"`（address 冲突无碍）。`requirements.txt` 覆盖运行链路全部必需依赖（matplotlib/graphviz/pywebview 缺失但均有代码级降级）。**风险**：无 `.dockerignore`，`COPY . .` 会把 `.env`（真实 Key）、`storage/`、`dist/`、`build/`、`.git` 打进镜像层 —— 密钥泄露 + 镜像膨胀，见扣分清单 |
| docker-compose.yml | **可用** | `8501:8501`、`env_file: .env`、`./storage:/app/storage` 持久化、`restart: unless-stopped`，与 Dockerfile 一致 |
| `资料/演示视频脚本.md` 与代码一致性 | **大体一致，2 处过时** | 逐项核对：9 个导航项 ✅（app.py:82-92）、"🚀 一键加载演示数据"/"🗑️ 清空文献库"按钮 ✅（sidebar.py:175/197）、各页面名称 ✅（page_qa"跨文献问答与溯源"/page_reading"版式阅读与智能翻译"/page_search/page_graph/page_writing/page_misc 双页均存在）、三维雷达图 ✅（page_writing.py:174-186）、图谱演示模式 ✅（page_graph.py:57-113）。**过时点**：①"1523 分块 26 秒入库"（:18）与实测 1301 块不符；②"所有代码执行先过 AST 白名单沙箱"（:24）—— 沙箱护栏真实存在（core/sandbox.py），但执行工具本身 100% 崩溃（tools/definitions.py:306），录 demo 时该功能会翻车 |

---

## 6. 全局整洁度问题

| # | 问题 | 证据 | 严重度 |
|---|---|---|---|
| 1 | **round-1/round-2 全部改动未提交**：24 个文件 +7949/-1435 行停留在工作区，且领先 origin/main 20 个提交未推送。一次误操作（checkout/崩溃）即丢失本轮全部工作 | `git status --short`（24 M）+ `git diff --stat` | 🟡 |
| 2 | **`services/wcopilot_dotrender.py` 未加入 git 跟踪，但被 `services/writing.py:779/:870` import**：新 clone 的仓库丢失 Graphviz 三级降级中的两级（有 try/except 不崩，但功能降级）。同类：`scripts/_memory_probe.py` 也是未跟踪却被 `ui/page_reading.py:48` import | `git status` untracked 清单 | 🟡 |
| 3 | `scripts/_smoke_read.py`、`_smoke_ui.py`、`_verify_desktop.py` 等下划线前缀脚本未跟踪，性质是考生自验脚本，提交与否需明确（建议：要么移入 scripts/ 并提交并写明用途，要么删除） | `git status` untracked | 🟢 |
| 4 | 项目根存在**空目录 `config/`**（无任何文件），与 `config.py` 同名易误导（本次评审的沙箱测试即因该目录怀疑过模块解析） | `ls config/` 为空，10-10 22:57 生成 | 🟢 |
| 5 | `storage/` 根目录散落 10+ 运行日志（`_rebuild*.log`、`st8502_*.log`、`shot_*.log` 等）。已 gitignore 不影响交付，但建议归入 `storage/logs/` | `ls storage/` | 🟢 |
| 6 | 运行链路代码**零** TODO/FIXME/XXX/HACK、**零** 顶层 `print(` 调试、**零** 硬编码绝对路径（`core/services/tools/ui/desktop/app.py/config.py` 全扫） | grep 实测 | ✅ |
| 7 | 重复职责检查：MemoryProbe 全项目唯一实现（scripts/_memory_probe.py），page_reading 只有 `_NullProbe` 占位兜底 ✅；`ui/common.py:413-415` 有一段 4 行 psutil RSS 采样与探针职责轻微重叠（可接受）；chunk 构建只有 `parsing.build_chunks_from_texts` 一处实现（prepare_pdf 与 commit fallback 共用）✅；错误契约不统一（`_empty_prepared` 无 error 键 vs `page_search.py` 全用 `r.get("error")`）即第 2 节 🔴 项，属职责重复且不一致的实例 | grep + 沙箱实测 | 🟢 |

---

## 7. 给后续修复的优先级建议（不改代码，仅供排期）

1. 🔴 `tools/definitions.py:306` `[-3000]`→`[-3000:]`（一行，救活整个 Python 工具）+ `_empty_prepared` 补 `error` 键（一行，堵幽灵文献）——两行代码消掉两个 🔴。
2. 🟡 README:5 截图路径 → `资料/截图/`，重录 9 页截图（UI 大改后必须重摄）；统一 `技术文档.pdf` 与 `technical_doc.md` 为单一事实源。
3. 🟡 重跑 `scripts/verify_official.py` 刷新 1523→1301 等三处数字；视频脚本 :18/:24 同步改。
4. 🟡 `git add services/wcopilot_dotrender.py scripts/_memory_probe.py` 并提交全部 24 个改动文件。
5. 🟢 新增 `.dockerignore`；`t_run_python` 补 finally 清理；`commit()` 清洗 filename。

---

*所有实测脚本与原始输出留存于 `C:\tmp\judgeD\`（test_longbook.py / test_corrupt.py / test_diskclean.py / test_llm_retry.py / test_llm_timing.py / test_udl.py / run1.txt / pdftext.txt）。*

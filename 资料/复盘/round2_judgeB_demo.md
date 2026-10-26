# Round2 · 评委B · 演示路径稳定性 + UI 一致性验收审计

> 评审范围：**考生5**（`ui/common.py`、`ui/sidebar.py`、`ui/page_library.py`、`ui/page_graph.py`、`ui/page_search.py`、`ui/page_misc.py`、`ui/page_agent.py`、`app.py`）
> **考生6**（`services/translate.py`、`ui/page_reading.py`、`core/llm.py`）
>
> 实测环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（Python 3.11.16 / streamlit **1.63.0** / pymupdf **1.28.2**），即 `启动科研助手.bat` 中 `conda activate ai_research` 所用环境。
> 实测方式：`streamlit.testing.v1.AppTest` 进程内逐条模拟 + 单元级直测。
> **副作用控制**：所有会写 `storage/` 的测试全部在 `/tmp/judgeb/proj`（源码沙箱副本，独立 storage）中完成，**真实项目 `storage/` 未被写入**；评审期间未修改/新建任何项目文件（本报告除外）。

---

## 1. 总评

| 考生 | 分数 | 一句话结论 |
|---|---|---|
| **考生5**（UI 一致性 + 演示加固） | **6.5 / 10** | 公共层 7 条规范的"基础设施"确实建起来了且被广泛使用，但**演示路径上存在一个必现的阻断级 NameError（版本差异对比）**，另有 6 处成功反馈被 `st.rerun()` 吞掉；自验方法只注入失败路径、声称 AppTest 不能传文件（实测可以），系统性漏检。 |
| **考生6**（页面3 四缺陷 + 性能 + LLM 重试） | **8.5 / 10** | 四个缺陷**逐条实测确实修好**（A1 数据与自报完全一致、A4 中文可反解无乱码），自报 59/59 冒烟我复跑为真，跨页跳转端到端实测通；扣分在 UI 规范细节（spinner 无进度、目录按钮无 emoji）与小资源泄漏。 |

---

## 2. 扣分清单

| 严重度 | 问题 | 文件:行号 | 证据（实测输出 / 代码片段） | 扣分 | 修复建议 |
|---|---|---|---|---|---|
| 🔴阻断 | **「🔍 对比版本差异」100% 抛 `NameError: LONG_TEXT_LIMIT`，演示路径 2 最后一步必翻车** | `ui/page_library.py:373`（`long_block(diff_md, title="版本差异报告", limit=LONG_TEXT_LIMIT)`）；该文件 import 位于 `:9-10`，**未导入 `LONG_TEXT_LIMIT`**（常量定义在 `ui/common.py:25`） | 实测（无效 Key，LLM 走伪响应成功返回）：<br>`🔴 error: 版本差异对比失败：name 'LONG_TEXT_LIMIT' is not defined`（耗时 10.7s 后才报错）<br>静态扫描（`ast` 未绑定名检查）：`ui/page_library.py 行 373 未定义名 LONG_TEXT_LIMIT`（全项目仅此一处真实未绑定名） | **-1.5** | 改为 `from ui.common import ... , LONG_TEXT_LIMIT`，或直接 `limit=300` |
| 🔴阻断（连带） | 该缺陷**只在成功路径暴露**，考生5 的自验只注入了失败路径，因此"0 异常"结论掩盖了它 | `scripts/_smoke_ui.py` 故障注入第 7 项；`资料/复盘/round1_worker5_ui.md:276,288` | 复跑其冒烟脚本：`[7/9] 🔍 对比 1706.03762 版本差异  0.0s exception=0 友好提示=有 OK`（注入的 `library.version_diff` 直接抛错 → 永远走不到 373 行）。真机路径（有 Key / 坏 Key 拿到伪响应）必然 NameError | **-0.5** | 故障注入必须**同时覆盖成功路径**（返回合法非空值） |
| 🟡重要 | **6 处成功反馈被 `st.rerun()` 吞掉**：`st.success(...)` 紧跟 `st.rerun()`，用户点完看不到任何提示 | `ui/sidebar.py:195→196`；`ui/sidebar.py:215→216`；`ui/page_library.py:103→105`、`111→112`、`339→340`、`385→386` | 最小复现（AppTest）：`st.success("done"); st.rerun()` → 重跑后 `at.success == []`。<br>实测路径1：点「🚀 一键加载演示数据」后页面 success 仅剩 `['模型已就绪']`，**"✅ 演示数据已就绪，当前文献库 3 篇" 从未出现**（数据确实入库：library.json = 3 papers / 4 chunks）。<br>实测路径2：点「🗂️ 保留两个版本」后无任何成功提示 | **-0.8** | 用 `st.session_state["_flash"]=...` 在 rerun 后重放提示；或去掉 rerun（多数场景不需要） |
| 🟡重要 | **二次点击「一键加载演示数据」完全无反馈**：`done=0` 时既不 success 也不 info | `ui/sidebar.py:179-196`（`if fn not in ctx.files:` 跳过 → `done=0` → `if done:` 为假） | 代码判定 + 首次点击实测已无提示（见上一条） | **-0.2** | 加 `else: st.info("演示数据已在库中…")` |
| 🟡重要 | **无效/过期 Key 仍显示「✅ 模型已就绪」，随后每个 LLM 操作先卡 9~21s 再报红** | `ui/sidebar.py:127-128`（`if ctx.set_provider(pid, key): st.success("✅ 模型已就绪")`） | 实测填入 `sk-judgeb-invalid-key…`：`🟢 模型已就绪`；随后「对比版本差异」耗时 **10.7s** 报红、「表格化对比」问答耗时 **21.0s** 返回 `【LLM 暂时不可用…Error code: 401 - 令牌已过期或验证不正确】`。现场 Key 填错 = 每次操作干等 10~20 秒 | **-0.5** | 接入时做一次轻量 `models.list()`/ping 校验，失败则 `st.warning("Key 可能无效")` |
| 🟡重要 | **`embed_html` 仍用已过弃用期的 `st.components.v1.html`**（Streamlit 声明 2026-06-01 后移除，今天已 2026-10-11），运行时反复刷警告 | `ui/common.py:313-314`（`_c.html(html, height=..., scrolling=...)`） | 实测每次渲染打印：`Please replace 'st.components.v1.html' with 'st.iframe'. 'st.components.v1.html' will be removed after 2026-06-01.`（单次会话内出现 5+ 次） | **-0.3** | 迁移到 `st.iframe`；注意考生6 已提醒：其 `_HEIGHT_JS`（`services/translate.py:215`）依赖 `streamlit:setFrameHeight`，切换后需重新验证高度自适应 |
| 🟡重要 | **侧边栏「🗺️ 前往该页」提示条滞后一个 run**：侧栏在页面之前渲染，点击溯源按钮写入 payload 的那一轮看不到它 | `app.py:76`（`render_sidebar`）→ `app.py:95-99`（页面渲染）；`ui/common.py:265-300` | 实测：点「📖 定位到《1810.04805v2.pdf》第 1 页」后 `侧边栏按钮: ['🚀 一键加载演示数据…', '🗑️ 清空文献库']`（**没有** 🗺️），`侧边栏 info: []`。必须再发生一次交互（手动切导航）才出现 | **-0.3** | 写入 payload 后立即 `st.rerun()`（QA 页 `_set_jump` 未 rerun），或把 banner 改到主区顶部渲染 |
| 🟡重要 | **规范 6「spinner 带具体进度」在自己页面内也未落实**：`step_text()` 只有 15 处中的少数带 X/Y，其余全是单段文案 | `ui/page_agent.py:82`、`ui/page_library.py:137,281,294,368`、`ui/page_graph.py:211,229,242`、`ui/page_search.py:32,56,113,143,216`、`ui/sidebar.py:202` | 实测输出均为 `🤖 Agent 正在规划并执行任务 …` / `📝 正在生成领域综述 …` 等**无分母**文案；`step_text(action)` 在 `cur/total` 缺省时退化为 `f"{action} …"`（`ui/common.py:40-44`） | **-0.4** | 能算总量的都补 X/Y；算不出总量的至少给阶段名（"①检索 ②重排 ③生成"） |
| 🟢轻微 | **断网降级话术重复一句**：`net_error` 插入的 `fallback_hint` 与内置 `_NET_ACTIONS[1]` 语义重复 | `ui/common.py:159` 与 `:165-167` | 实测断网输出：`🛠️ 怎么办：重试一次；**如需离线演示，请点左侧「🚀 一键加载演示数据」并使用📁/💬/🕸️页面**；**点击左侧「🚀 一键加载演示数据」使用离线数据继续演示**；检查网络/代理后再试` | **-0.1** | `fallback_hint` 非空时替换而非 insert |
| 🟢轻微 | **规范 7「列比例统一」未真正统一** | `ui/page_agent.py:28`（`st.columns(3)`）vs `ui/page_graph.py:209`（`[1,1,1]`）vs `ui/page_library.py:334`（`[2,1]`）vs `ui/page_search.py:237,312`（`[2,1]`） | 同一项目内 4 种写法；报告 §2 称"已消灭全部非常规比例"，但 `[2,1]` 仍在其自有文件里 | **-0.2** | 收敛为 `[1,1]`/`[1,1,1]`/`[2,1]` 三档并写进 `ui/common.py` 常量 |
| 🟢轻微 | **基石/边缘结论一处显示文件名、一处显示标题，观感不一致** | `ui/page_graph.py:128`（`_title()` 回退到文件名） | 实测：`🟢 基石论文：《1706.03762v7.pdf》` 与 `🔵 边缘衍生：《BERT: Pre-training of Deep Bidirectional Transformers…》` 同屏并列 | **-0.1** | 统一回退为"标题 or 去扩展名文件名 + 版本号" |
| 🟢轻微（考生6） | **阅读页 spinner 未走 `step_text`，与全站规范不一致** | `ui/page_reading.py:775`（`with st.spinner("⚡ 正在生成原位 PDF…")`） | 全页唯一的 `st.spinner`，硬编码文案、无进度、未引用公共层 `step_text` | **-0.2** | 改为 `st.spinner(step_text("⚡ 正在生成原位 PDF", 1, 3))` 之类 |
| 🟢轻微（考生6） | **标题树按钮无 emoji，且以全角空格开头（规范 2 反例）** | `ui/page_reading.py:876`（`st.button(f"{'　'*(level-1)}{title} · p{pg}")`） | 与同页其它 9 个按钮（🎨/🔍/🚀/🧹/📥/⚡/🔗/🌐/📥）风格不一致 | **-0.1** | 补 `📑` 前缀 |
| 🟢轻微（考生6） | **临时上传的 PDF 用 `NamedTemporaryFile(delete=False)` 且从不 unlink** | `ui/page_reading.py:519` | 代码判定：无 `os.unlink`，也无 `finally` 清理；每次临时上传在 `%TEMP%` 留一个 2MB+ 文件 | **-0.1** | 在 `render()` 的 `finally` 里按 `st.session_state` 记录的路径清理 |
| 🟢轻微（考生6） | **阅读页无长文本折叠（规范 5）**：划词区整页文本截到 4500 字直出 iframe | `ui/page_reading.py:832`（`[:4500]`）+ `:841` | 全页无 `long_block()` 调用（grep 确认） | **-0.1** | 超过阈值时折叠摘要段 |

> **说明**：`ui/page_qa.py:156`（5 个示例问题按钮无 emoji）、`ui/page_qa.py:106`（`[1.4,1.0,3.0]` 列比例）、`ui/page_writing.py:122`（`st.write(df.head())` 未走 `pretty_table`）属考生2/考生4 文件，**不计入本轮两位考生扣分**，仅在 §5 统计中标注。

---

## 3. 演示路径逐条实测结果

（"异常"＝AppTest `at.exception`；"友好提示"＝是否出现中文 error/warning/info 而非裸 Traceback）

| # | 演示路径 | 是否异常 | 是否有友好降级提示 | 是否达到预期效果 |
|---|---|---|---|---|
| **1** | 打开系统 → 输入 API Key → 点「一键加载演示数据」 | **0 异常**（首屏 7.2s，加载 4.4s） | 未填 Key：`未填 Key：可使用内置离线演示数据` ✅；填假 Key：`✅ 模型已就绪` ⚠️（误导） | **部分达标**：数据确实入库（3 篇 / 4 chunks，library.json 已核对）；但**成功提示被 `st.rerun()` 吞掉，点完界面无任何反馈**（`ui/sidebar.py:195-196`） |
| **2** | 📁 上传 v7 和 v1 → 版本去重预警 → 点对比差异 | **0 异常**（解析 0.9s / 0.8s） | 查重预警 ✅：`⚠️ 语义查重命中：库中已有《1706.03762v7.pdf》，内容相似度 75.9%`；v1 上传命中 `95.8%`；决策三按钮（♻️/🗂️/❌）正常出现 | **❌ 未达标**：最后一步「🔍 对比 1706.03762 版本差异」**必抛** `NameError: LONG_TEXT_LIMIT`（`ui/page_library.py:373`），10.7s 后才弹红色 `❌ 版本差异对比失败：name 'LONG_TEXT_LIMIT' is not defined`。<br>另有：点「🗂️ 保留两个版本」无任何成功反馈；**`演示语料/` 下并没有 v1 PDF**（只有 `1706.03762v7.pdf` 与 `1810.04805v2.pdf`），v1 需从项目根 `论文样本/` 取 |
| **3** | 💬 表格化对比 → 带页码溯源 → **点溯源锚点跳转** | **0 异常**（问答 21.0s） | 无 Key：`⚠️ 请先在左侧填入 API Key。` ✅；坏 Key：产出 6 条溯源锚点 + `🟡 幻觉防护校验未完全通过：答案未包含任何引用角标` ✅ | **✅ 达标（联动确实通）**：点「📖 定位到《1810.04805v2.pdf》第 1 页」→ `🟢 已定位到…第 1 页（第 1 段）`，`qa_jump_page={'source':'1810.04805v2.pdf','page':1,'para_idx':1,'from':'qa'}`；手动切到 📖 → `🟢 已从问答页跳转：《1810.04805v2》第 1 页（第 1 段）`，6 个 tab 的「页码」控件全部置为 **1**。<br>URL 通道同样通：`?p=3&jump_page=7&paper=…` → `🎯 已从问答页跳转到第 7 页`，页码=7。<br>⚠️ 侧边栏「🗺️ 前往该页」滞后一个 run（详见扣分清单） |
| **4** | 📖 上传 PDF → 原生渲染 → 同步翻译 → 划词翻译 | **0 异常** | 无 Key 时两处均 `⚠️ 未检测到可用 API Key，无法调用翻译模型。` + `🛠️ 怎么办：在左侧「接入方式」填写 Key 后重试。` ✅ | **部分达标**：原生渲染 + 栏数检测 ✅（`🟢 双栏：左 6 块 / 右 3 块，中缝 293–306pt`）；划词面板渲染 ✅（含"选中后自动清空选区并复制到剪贴板"说明）；翻译链路**必须 Key 才能出内容** |
| **5** | 🕸️ 演示模式生成图谱 → 一键综述 | **0 异常**（演示图谱 **0.7s**） | 图谱：`🟢 演示图谱已生成：2 节点 · 1 条示意关系` + 顶部 `🟡 当前为演示模式示意图谱…不代表真实引用关系` ✅；综述无 Key：`🟡 领域综述生成需要大模型，但未检测到可用 API Key。` ✅ | **✅ 达标**（离线可演示部分全部出内容） |
| **6** | ✍️ 上传 CSV → 生成图表 → Figure Caption | **0 异常** | Caption 无 Key：`🟡 未配置 API Key，无法生成 Figure Caption。` ✅ | **✅ 达标**：上传 `演示语料/data.csv` 后自动推荐并渲染 3 图（柱状图 / 相关性热力图 / 平行坐标图），每条附 `🧭 推荐依据：…` |
| **7** | 🔍 arXiv 检索 → 演示降级提示 | **0 异常** | 断网注入：`🔴 arXiv 检索失败：HTTPSConnectionPool(host='export.arxiv.org'…)` + `🩺 可能原因：演示现场外网不可达或被代理拦截…` + `🛠️ 怎么办：重试一次；如需离线演示请点左侧「🚀 一键加载演示数据」…` ✅ | **✅ 达标**：真实联网实测 `🟢 检索到 5 篇论文`（1.0s），并识别出 2 条 `💻 开源代码` |

**汇总**：7 条路径 **0 未捕获异常**；**5 条完全达标、1 条部分达标、1 条（路径 2）现场必翻车**。

---

## 4. 页面3 四个缺陷的修复验证（逐条实测）

### A1 双栏渲染：✅ 修好了

- 真实双栏语料 `论文样本/1810.04805v2.pdf`（BERT v2）**全 16 页判为双栏 16/16**；单栏语料 `1706.03762v7.pdf` / `1706.03762v1.pdf` 前 12 页**全部判为单栏 0 误判**。
- 渲染 HTML 结构正确：同时存在 `id="col0"`、`id="col1"`、`class="gutter"`，tip 文案 `📑 检测到双栏版式（左 6 块 / 右 3 块，中缝 293–306pt），已按栏分块渲染`。
- **越界检测（与自报口径一致）**：16 页共 **434 块，越界块 0，最大越界 −1.9px**（即所有块都在栏内并留 2px 余量）——与考生6 自报"434 块 / 越界 0 / 最大 −1.9px"**逐位吻合**。
- **块重叠检测**：用 PyMuPDF 真实 block bbox 复核（而非估算行高），p2 / p10 同栏内**重叠 0 对**；p3 最多 15 对、最大 **7.2pt**，p6 最大 15.6pt —— 用同一算法对**源 PDF 原始 bbox**做 ground-truth 对照，得到完全相同数值（p3: 15 对 / 7.2pt；p6: 1 对 / 15.6pt），证明残留重叠是**源 PDF 固有行距/上下标**，不是渲染引入。
- 渲染 `top` 与 PDF 真实 `y0` **逐块一致**（p2/p6/p10: `tops == PDF y0 → True`；p3 col1 20/21，差 1 个空文本块被 `_block_html` 正常跳过）。
- 结论：按 x 分栏生效、限宽生效、阅读顺序（通栏→左栏→右栏）正确，无渲染引入的串栏/重叠。

### A2 同步滚动：✅ 修好了

`bilingual_html` 生成的 JS 实测字符串检查（8/8 通过）：

| 检查项 | 结果 | 证据片段 |
|---|---|---|
| 按比例同步 | ✅ | `dst.scrollTop = ratio(src) * m;`（`applyRatio`） |
| 比例公式 | ✅ | `function ratio(el){ var m = maxScroll(el); return m <= 0 ? 0 : (el.scrollTop / m); }` |
| `scrollHeight-clientHeight` | ✅ | `var m = el.scrollHeight - el.clientHeight;` |
| `isSyncing` 防抖 guard | ✅ | `if (isSyncing) return;` + `isSyncing = true;` |
| rAF 节流 | ✅ | `if (pendingRAF) return; pendingRAF = window.requestAnimationFrame(...)` |
| **除零保护** | ✅ | `return (m > 0) ? m : 0;`（maxScroll）+ `m <= 0 ? 0`（ratio）+ `if (m <= 0) return;`（applyRatio）三重 |
| 双向绑定 | ✅ | `bindScroll(l, r); bindScroll(r, l);` |
| `data-index` 段落联动 | ✅ | `highlight(idx)` 只按 `getAttribute('data-index')` 匹配，永不按文本查找 |

> 注：考生6 的 A2 行为验证用 Node + DOM 桩（6/6），我未复跑 Node（环境未提供），此条以**源码字符串 + 公式结构**判定为达标，浏览器内实际手感**存疑**（未按话术要求做视觉校验）。

### A3 划词翻译：✅ 修好了（且限制已诚实交代）

`SELECTION_JS` 字符串检查 7/7：`removeAllRanges()` ✅、`s.empty()` 兜底 ✅、`lastHash`+`DEDUP_MS=2500` 去重 ✅、`MIN_LEN=2` 且 `txt.length < MIN_LEN` 忽略 ✅、捕获后 `clearSelection();` ✅、`mouseup` ✅、`dblclick`（+30ms）✅。

> **硬限制（考生6 已如实交代，`round1_worker6_read.md:354-356`）**：`st.components.v1.html` 是单向 iframe，选中文本**无法自动回填** `st.text_area`，只能"捕获→清空→复制→用户 Ctrl+V"。UI 文案已明说（`ui/page_reading.py:838`），不构成隐瞒。自动剪贴板写入在沙箱 iframe 内是否被浏览器策略拒绝——**存疑**（无法无头验证），但面板上保留了「复制」按钮供手动操作。

### A4 中文字体：✅ 修好了（实测生成 + 反解验证，非"看代码猜"）

- `_cjk_font()` → `('china-s', Font)`；`pymupdf.Font("china-s")` → `✅ Font('Droid Sans Fallback Regular')`（**新版 `pymupdf.Font` 路径生效**，老版 `fitz.Font` 兜底分支保留）。
- 实测生成：`in_place_pdf(BERT p1, "本文提出 BERT：基于 Transformer 的双向编码器表示。BERT 在十一个自然语言处理任务上取得新纪录，参数量分别为 1.1 亿与 3.4 亿。")` → 输出 **87,398 字节** PDF。
- **反解验证**：`pymupdf.open(out)[0].get_text()` 提取到完整中文，尾部实测片段：
  `本文提出 BERT：基于 Transformer 的双向编码器表示。BERT\n在十一个自然语言处理任务上取得新纪录，参数量分别为 1.1 亿与 3.4 亿。`
  - `是否含中文『双向编码器』: True`
  - `是否含『自然语言处理』: True`
  - `是否含乱码字符 U+FFFD: False`
- 结论：**没有乱码，文本可被提取**（与考生6 自报"无 U+FFFD"结论一致；字节数不同是因为我用的译文更短，非矛盾）。

---

## 5. UI 7 条规范达标统计

统计口径：9 个页面（🤖 / 📁 / 💬 / 📖 / 🔍 / 🕸️ / ✍️ / 🏗️ / 💡）。"考生5 自有"＝其独占的 6 页 + 侧边栏 + 公共层。

| 规范 | 达标页面数 / 总页面数 | 反例（文件:行号） | 归属 |
|---|---|---|---|
| ① 顶部标题 + 一句话说明 | **9 / 9** ✅ | 无——9 个页面全部在 `render()` 开头调用 `page_header(title, subtitle, icon)`（📁 `:35`、🔍 `:359`、🕸️ `:150`、🤖 `:23`、🏗️ `:62`、💡 `:97`、💬 `:140`、📖 `:618`、✍️ `:394`） | 考生5 公共层 `ui/common.py:62` |
| ② 按钮 emoji 开头 | **7 / 9** | `ui/page_qa.py:156`（示例问题 `"这篇论文提出了什么模型？"` 等 5 个无 emoji，考生2）<br>`ui/page_reading.py:876`（标题树按钮以全角空格开头，考生6） | 考生5 自有页面 **8/8 达标** |
| ③ 输出分级 success/info/warning/error | **9 / 9** ✅ | 无（各页分级调用数：library 22、reading 44、writing 33、search 22、sidebar 9、graph 9、qa 8、misc 3、agent 2） | 全部 |
| ④ 表格美化 HTML 渐变表头 | **4 / 5**（有表格的 5 个页面） | `ui/page_writing.py:122` `st.write(df.head())`（考生4） | library `:249,320` / graph `:125` / search `:126` / misc `:101` ✅ |
| ⑤ 长文本 expander 折叠 | **7 / 9** | `ui/page_reading.py:832,841`（4500 字文本与译文直出，无 `long_block`，考生6）<br>`ui/page_writing.py:240`（大纲全文 `st.markdown` 直出，考生4，部分） | 考生5 自有页面 **6/6 达标** |
| ⑥ spinner 带具体进度 | **严格 0 / 9**；宽松（至少一处带 X/Y 或 progress 条）**5 / 9** | 严格口径下全军覆没：`ui/page_agent.py:82`、`ui/page_library.py:137,281,294,368`、`ui/page_graph.py:211,229,242`、`ui/page_search.py:32,56,113,143,216`、`ui/sidebar.py:202`、`ui/page_qa.py:194`、`ui/page_reading.py:775`、`ui/page_writing.py:143,203,223,280,305,341,381`；<br>仅 `sidebar:178`、`library:183-189`、`search:81,181,320`、`reading:311`（`_progress`）等少数带 X/Y | 主要责任：考生5（自有页面 5 处无进度） |
| ⑦ 列比例统一 | **3 / 11 模块严格统一** | 全项目 6 种写法：`st.columns(3)`（`page_agent.py:28`、`page_qa.py:154`、`page_writing.py:159`）、`[1,1,1]`（`page_library.py:99`、`page_graph.py:209`、`page_reading.py:707`）、`[1,1]`（多处）、`[2,1]`（`page_library.py:334`、`page_search.py:237,312`）、`[1.4,1.0,3.0]`（`page_qa.py:106`）、`st.columns(2)`（`page_writing.py:331`） | 严格统一：仅 graph / misc / common |

---

## 6. 虚报核查（声称做了但实际没有 / 说法与实测不符）

| # | 声称 | 核查结果 | 判定 |
|---|---|---|---|
| 1 | 考生5 §6.1：`"PDF 上传解析路径未实测——AppTest 无法给 file_uploader 灌入真实 PDF 字节"` | **证伪**。streamlit **1.63** 的 `AppTest.file_uploader` 支持 `set_value((filename, bytes, mime_type))`。我实测上传 2.2MB `1706.03762v7.pdf` 与 2.1MB `1706.03762v1.pdf`，**完整走通"上传→解析→语义查重预警→三选一决策"**（0.9s / 0.8s）。该能力断言错误直接导致其漏检路径 2 的 NameError | ❌ **说法不成立** |
| 2 | 考生5 §5.2：`"故障注入 9/9 全部给出中文降级提示"`；§0：`"共 0 未捕获异常"` | 复跑 `scripts/_smoke_ui.py` → **确实 0 异常、9/9 友好提示**（我复现为真）。但该结论**对路径 2 无效**：注入使 `library.version_diff` 抛错 → 代码在 `:369` 就进 except，**永远执行不到 `:373` 的 NameError**。真机成功路径 100% 报错 | ⚠️ **真实但具误导性** |
| 3 | 考生5 §2 规范 2：`"全量核对过我负责的每个 st.button / form_submit_button"` | grep 复核其 8 个文件：全部按钮均以 emoji 开头（🔍♻️🗂️❌🤖🧩🧮📚🗑️🕸️🎭📝🚀⬇️🔍🧹✨🔄👍👎🗺️✅） | ✅ **属实** |
| 4 | 考生5 §2 规范 7：`"已消灭全部非常规比例"` | 其自有文件内仍存在 `st.columns(3)`（`page_agent.py:28`）与 `[2,1]`（`page_library.py:334`、`page_search.py:237,312`）三种写法 | ❌ **不成立**（部分） |
| 5 | 考生5 §3.2：`"清空后同步清理派生 state，避免'文献删了图谱还在'"` | `ui/sidebar.py:208-210` 确实 pop 了 `graph_pack/review/future/struct/meta_view/formula_view/ref_view/pending` | ✅ **属实** |
| 6 | 考生6：`"scripts/_smoke_read.py → PASS 59 / FAIL 0"` | 复跑（补齐 `UnderstandingDeepLearning_02_09_26_C.pdf` 后）→ **`PASS 59 / FAIL 0`**，与自报**完全一致**（含 541 页扫描 1,313,319 字符 / 峰值 305 MB / 3.7s） | ✅ **属实** |
| 7 | 考生6 A1：`"16/16 页判双栏，434 块、越界 0、最大越界 −1.9px"` | 我独立复算：`页数 16 双栏页 16 总块 434 越界块 0 最大越界 -1.9px` —— **逐位吻合** | ✅ **属实** |
| 8 | 考生6 A4：`"96,433 字节 PDF，反解出 79 个汉字，无 U+FFFD"` | 我用不同译文复现：87,398 字节，中文可提取、`U+FFFD` 为 0 —— **结论一致**（数值差异来自输入文本长度不同） | ✅ **属实** |
| 9 | 考生6：`"A4 乱码在当前 PyMuPDF 1.28.2 下未复现，只修了会退化成非 CJK 字体的成因路径"` | 诚实且可验证：旧 `except` 分支退化为无 `fontname` 的 Helvetica 确实会丢中文；新实现在任何失败路径都不再退化 | ✅ **诚实，加分** |
| 10 | 考生6：`"A1 在现有干净语料上未复现肉眼串栏，用夹具复现"` | 我独立验证：干净 arXiv PDF 的 span bbox 本身就在栏内（ground-truth 重叠仅 7.2pt，属行距），与其说法一致 | ✅ **诚实，加分** |
| 11 | 考生6 §未完成 1：`"选中文本无法自动回填 st.text_area，需真自定义组件"` | 代码层面确认为单向 iframe（`publish()` 只 `postMessage` 到无监听器的宿主），UI 文案也未隐瞒 | ✅ **属实** |
| 12 | 两位考生均称"只改自己独占的文件" | 无法从 git 层面归属验证（工作区为单一未提交树，24 个文件已改）。**可验证的是向后兼容性**：实测 9 个页面全部渲染 0 异常、13 个模块 import 正常、`page_header`/`embed_html`/`source_card`/`_log_llm_failure` 等旧签名调用方（page_qa/page_reading/page_writing）均未受影响 | ✅ **未见破坏性副作用**（归属存疑） |

---

## 7. 评分明细

| 维度（10 分制） | 考生5 | 考生6 | 依据 |
|---|---|---|---|
| **真实性** | 6.5 | 9.0 | 考生5：复跑冒烟为真，但"AppTest 不能传文件"属可证伪的错误断言，且 §0 的"0 未捕获异常"对成功路径不成立。考生6：59/59、A1 434 块/−1.9px、A4 无乱码三项独立复算逐位吻合，并主动交代 3 条"未复现"项 |
| **有效性（实测为准）** | 6.5 | 9.0 | 考生5：7 条规范的基础设施是真用了（9/9 页标题统一、4/5 表格美化、7/9 折叠），但规范 6 严格 0/9、规范 7 未统一。考生6：A1–A4 四项全部实测修好，B1/B2 的 541 页 3.7s / 增量 +0MB 复现为真，C 组 4 次调用 9.5s 复现为真 |
| **演示稳定性** | **4.5** | 8.0 | 考生5：路径 2 **必现阻断**（NameError）+ 6 处反馈被吞 + 假 Key 就绪误导（每次操作干等 10~21s）。考生6：阅读页全流程 0 异常、跨页跳转端到端实测通、无 Key/坏 Key 均有中文降级；扣分在 soft-timeout 不杀线程（已交代）与临时文件泄漏 |
| **视觉一致性** | 7.5 | 7.0 | 考生5：渐变表头/标题卡片/空状态/错误话术全站统一，效果好；扣分在列比例与 spinner 文案不统一。考生6：页面 3 大量为 iframe 内渲染，与全站规范耦合弱，但目录按钮无 emoji、无折叠、spinner 硬编码 |
| **无副作用** | 8.5 | 9.0 | 双方均未破坏他人页面调用（实测 9 页 0 异常、13 模块 import OK）；考生5 遗留 `st.components.v1.html` 弃用 API（已过官方移除日期）；考生6 的 `MAX_RETRIES` 3→3 次重试（总 4 次调用）改动已附风险说明与回滚方式 |
| **加权总分** | **6.5** | **8.5** | 演示稳定性权重最高（比赛现场翻车 = 直接失分） |

### 给两位考生的优先级修复清单

**考生5（必须赛前修完）**
1. `ui/page_library.py:373` 导入 `LONG_TEXT_LIMIT`（🔴 阻断，5 分钟工时）
2. 6 处 `st.success + st.rerun` 改为 rerun 后重放提示（🔴 观感等同于"点了没反应"）
3. 无效 Key 不要显示「✅ 模型已就绪」（🟡 现场干等 10~21s）

**考生6（有余力则修）**
1. `ui/page_reading.py:775` spinner 接入 `step_text`（规范 6）
2. `ui/page_reading.py:876` 目录按钮补 emoji（规范 2）
3. `ui/page_reading.py:519` 临时 PDF 文件 finally 清理

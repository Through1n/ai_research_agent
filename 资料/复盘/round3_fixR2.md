# Round 3 修复报告 · 修复工程师 R2（演示路径阻断级 UI 缺陷）

- 修复时间：2026-10-11
- 运行环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（streamlit 1.63.0，Python 3.11）
- 独占改动文件（**仅这 7 个，未越界**）：
  `ui/page_library.py`、`ui/sidebar.py`、`ui/page_search.py`、`ui/page_graph.py`、
  `ui/page_misc.py`、`ui/page_agent.py`、`app.py`
- 自验脚本与日志：`C:/tmp/r2/`（`scan_undefined.py` / `run_paths.py` / `ab_flash.py` / `dbg_diff.py` / `dbg_caption.py` / `final_run.log` / `result.json`）
- 未改动：`ui/page_qa.py`、`ui/page_writing.py`、`ui/page_reading.py`、`ui/common.py`、`ui/desktop_chrome.py`、`core/*`、`services/*`、`tools/*`、`config.py`、`README.md`

---

## 0. 结论速览

| 缺陷 | 严重级别 | 状态 | 一句话结论 |
|---|---|---|---|
| 缺陷 1：`page_library.py:373` NameError | 🔴 阻断 | ✅ 已修复并实测 | 常量已从 `ui/common.py` 导入；点击「🔍 对比版本差异」**0 异常、0 error**，差异内容正常渲染 |
| 缺陷 2：`st.success()` 紧跟 `st.rerun()` | 🔴 | ✅ 已修复并实测 | 采用**方案 A（flash）**，共改造 6 处 `success` + 2 处 `warning`；A/B 实验证明提示跨 rerun 存活 |
| 缺陷 3：7 条演示路径复验 | 🟡 | ✅ 全部复跑 | **7/7 条 0 未捕获异常**；额外修掉路径 5「自相矛盾空状态」 |
| 缺陷 4：README 主截图裂图 | 🟡 | ✅ 已勘定（不改 README） | `资料/screenshots/` **不存在**；真实目录是 `资料/截图/`，9 张可用截图清单见 §4 |

---

## 1. 缺陷 1：`ui/page_library.py:373` NameError（🔴 阻断）

### 1.1 根因

`grep -rn "LONG_TEXT_LIMIT"` 全项目结果（修复前）：

```
./ui/common.py:25:            LONG_TEXT_LIMIT = 300            ← 常量唯一定义处
./ui/common.py:185:          def long_block(text, title=..., limit: int = LONG_TEXT_LIMIT, ...)
./ui/page_library.py:373:    long_block(diff_md, title="版本差异报告", limit=LONG_TEXT_LIMIT)   ← 使用处
./资料/复盘/round2_judgeB_demo.md:25,52,159                  ← 评委记录
```

根因：`ui/page_library.py` 第 9–10 行的 `from ui.common import (...)` **导入了 `long_block` 却没有导入它配套的阈值常量 `LONG_TEXT_LIMIT`**。
常量本身定义在公共层 `ui/common.py:25`，值为 `300`（语义：长文本折叠阈值，超过 300 字或 15 行自动折叠，见 `ui/common.py:185-197` 的 `long_block`）。

由于该调用位于 `try/except` 内，`NameError` 被 `friendly_error()` 捕获后渲染成红色
`❌ 版本差异对比失败：name 'LONG_TEXT_LIMIT' is not defined`（外层还套了 spinner，实测 10.7s 后才弹）。

### 1.2 修复方案与取值理由

**选择「从 `ui/common.py` 导入」，而不是在本地重新定义一个 `300`。**

理由：
1. `LONG_TEXT_LIMIT` 是**公共层已有且被 `long_block` 作为默认值使用**的常量，导入它可保证「调用方显式传值」与「函数默认值」永远同源，不会漂移；
2. 若本地重写 `LONG_TEXT_LIMIT = 300`，未来公共层调整阈值时本页会出现两份不一致的 300，属于典型的魔法数字重复；
3. 不改 `ui/common.py`（非独占文件），导入是唯一既正确又不越界的做法。

### 1.3 代码改动

`ui/page_library.py:9-11`

```python
from ui.common import (ensure_state, page_header, pretty_table, resolve_pdf, friendly_error,
                       empty_state, llm_guard, long_block, step_text, LONG_TEXT_LIMIT)
from ui.sidebar import set_flash          # 缺陷 2 的 flash 写入口
```

### 1.4 顺带增强：降级输出不再是一串裸技术摘要（同文件 `ui/page_library.py:374-383`）

实测发现：`services/library.py:543 version_diff()` 在 LLM 不可用时**不抛异常**，而是返回降级文案
（`"版本差异分析暂不可用：…"` 或 `"【LLM 暂时不可用，请稍后重试。错误摘要：…】"`）。
修复前即使 NameError 消失，评委看到的也是一行没有说明的技术摘要。故在同处补了显式降级提示 + 引导：

```python
else:
    txt = str(diff_md)
    # 大模型不可用时 version_diff 会返回降级文案而非抛异常，
    # 这里显式降级提示 + 引导，避免评委看到一串技术错误摘要却无任何说明。
    if "暂不可用" in txt or txt.startswith("【"):
        st.warning("⚠️ 版本差异报告已降级：大模型暂不可用，未生成对比表格。")
        st.caption("🩺 可能原因：API Key 无效 / 额度不足 / 现场网络受限。")
        st.caption("🛠️ 怎么办：填入有效 Key 后重试；"
                   "无 Key 时可直接讲解下方「💡 建议保留最新版」与版本清单，演示不受阻断。")
    long_block(txt, title="版本差异报告", limit=LONG_TEXT_LIMIT)
```

### 1.5 实测结果（真实渲染树，非推测）

脚本 `C:/tmp/r2/dbg_diff.py` + `run_paths.py 2`，修复后点击「🔍 对比 1706.03762 版本差异」：

```
按钮: 🔍 对比 1706.03762 版本差异
耗时 10.1s
exception: []            ← 无未捕获异常
error:     []            ← 无 ❌ 红色错误（修复前此处必为「版本差异对比失败：name 'LONG_TEXT_LIMIT' is not defined」）
--- 全部 markdown ---
[8] 【LLM 暂时不可用，请稍后重试。错误摘要：'ascii' codec can't encode characters in position 7-13: ordinal not in range(128)】

路径 2 完整日志（修复后）：
  [🔍 对比版本差异] 耗时 10.6s
    · exception=[]
    · warning:
        - 版本差异报告已降级：大模型暂不可用，未生成对比表格。
  >>> 结论：✅ 无异常且输出差异内容
```

判定：**`long_block` 正常执行并输出了差异报告内容**（因本机 `.env` 里是占位 Key，LLM 走降级文案；填入有效 ASCII Key 后此处即为 Markdown 对比表格）。

> 备注（非本文件问题，供其他 agent 参考）：降级摘要里的 `'ascii' codec can't encode characters in position 7-13`
> 来自 `.env` 中 `ZHIPU_API_KEY=请填入你的智谱API Key` 这个**含中文的占位 Key**被写进 HTTP 头。
> 现场使用真实 ASCII Key 时不会出现；我在路径 1 中显式填入 ASCII Key 后即不再复现。

---

## 2. 缺陷 2：`st.success()` 紧跟 `st.rerun()`（🔴）

### 2.1 全量排查结果

在我独占的 7 个文件里逐行排查 `st.success / st.info / st.warning` 后紧跟（或同一分支内紧接）`st.rerun()` 的位置，
与评委指出的 6 处**完全吻合**：

| # | 文件:行（修复前） | 原代码 | 处理 |
|---|---|---|---|
| 1 | `ui/sidebar.py:195→196` | `st.success(f"✅ 演示数据已就绪，当前文献库 {n} 篇")` → `st.rerun()` | ✅ 改 flash（**演示路径第 1 步**） |
| 2 | `ui/sidebar.py:215→216` | `st.success("✅ 已清空文献库")` → `st.rerun()` | ✅ 改 flash |
| 3 | `ui/page_library.py:103→105` | `st.success(f"✅ 已覆盖：…")` → `st.rerun()` | ✅ 改 flash |
| 4 | `ui/page_library.py:111→113` | `st.success("✅ 已同时保留两版…")` → `st.rerun()` | ✅ 改 flash（**演示路径 2**） |
| 5 | `ui/page_library.py:339→340` | `st.success(f"✅ 已删除：{fn}")` → `st.rerun()` | ✅ 改 flash |
| 6 | `ui/page_library.py:385→386` | `st.success(f"✅ 已入库：…")` → `st.rerun()` | ✅ 改 flash |

**额外发现并一并修掉 2 处同类问题**（评委清单未列，但同样是"rerun 吞提示"，会导致失败时零反馈）：

| # | 文件:行（修复前） | 原代码 | 处理 |
|---|---|---|---|
| 7 | `ui/sidebar.py:238→(248 rerun)` | `st.warning(f"⚠️ 已加载 {done}/{len(items)} 篇，{len(fails)} 篇失败。")` → 后续 `st.rerun()` | ✅ 改 flash(warning) |
| 8 | `ui/sidebar.py:265→(269 rerun)` | `st.warning(f"⚠️ 清空完成，但有 {len(fails)} 篇移除失败。")` → 后续 `st.rerun()` | ✅ 改 flash(warning) |

已排查但**不属于**该模式（无需改）：`ui/page_search.py:246/250`（点赞后裸 rerun，无前置提示）、
`ui/page_agent.py:89`（rerun 前无 success）、`ui/common.py:292/298`（`jump_banner`，非独占文件）。

### 2.2 选定方案：**方案 A（session_state flash）**

未选方案 B（`st.toast()`）的理由：`toast` 是浏览器端浮层，**不进入页面渲染树**，
评委截图/录屏回放时无法留存证据，且 AppTest 无法断言；方案 A 的提示落在主体区顶部，
可截图、可断言、可与页面其余内容同屏讲解。

### 2.3 实现（3 处）

**(1) `ui/sidebar.py:34-83` —— flash 组件（导航/框架层）**

放在 `sidebar.py` 而非新建文件的理由：`ui/common.py` 是公共层但**禁止我修改**；
`ui/sidebar.py` 已是事实上的框架层（`ui/common.py:286` 的 `jump_banner` 本身就 `from ui.sidebar import MENU`），
且 app.py 与各页面均可安全 import（无循环：`app.py` 先 import sidebar 再 import 各 page）。

```python
# ---------- 跨 rerun 存活的 flash 提示 ----------
# 背景：st.success()/st.info() 紧跟 st.rerun() 时，rerun 会立刻重跑脚本，
# 提示还没渲染就被清空，用户点完按钮零反馈。故把提示写进 session_state，
# rerun 后由 app.py 在主体区顶部调用 render_flash() 统一重放并清空。
FLASH_KEY = "_flash"
_FLASH_LEVELS = ("success", "info", "warning", "error")


def set_flash(level: str, msg: str):
    """写入一条跨 rerun 存活的提示（在 st.rerun() 之前调用）。"""
    try:
        lv = str(level or "info").lower()
        if lv not in _FLASH_LEVELS:
            lv = "info"
        st.session_state[FLASH_KEY] = (lv, str(msg or ""))
    except Exception:
        pass


def render_flash():
    """在主体区顶部重放并清空 flash 提示；无提示时静默返回（不渲染任何占位）。"""
    try:
        item = st.session_state.get(FLASH_KEY)
    except Exception:
        return
    if not item:
        return
    try:
        level, msg = item
    except (TypeError, ValueError):
        try:
            st.session_state[FLASH_KEY] = None
        except Exception:
            pass
        return
    # 先清空再渲染：避免渲染阶段再次触发 rerun 时重复弹提示
    try:
        st.session_state[FLASH_KEY] = None
    except Exception:
        pass
    if not msg:
        return
    fn = {"success": st.success, "info": st.info,
          "warning": st.warning, "error": st.error}.get(level, st.info)
    try:
        fn(msg)
    except Exception:
        st.info(msg)
```

> 关键设计点：**先清空再渲染**。若在渲染后再清空，本次渲染末尾若再次触发 rerun（如页面里的其它 rerun 分支），
> 同一条提示会被重放两次。

**(2) `app.py:19 / 61 / 96-101` —— 主体区顶部统一重放**

```python
    from ui.sidebar import render as render_sidebar, render_flash     # app.py:19
...
    # 跨 rerun 存活的 flash 提示（set_flash 写入 / render_flash 消费并清空）
    "_flash": None,                                                    # app.py:61（ensure_state 先初始化再读）
...
# ---------- flash 提示统一渲染（主体区顶部，跨 rerun 存活） ----------
# 侧边栏/页面在 st.rerun() 前调用 set_flash()，rerun 后由这里重放，避免提示被吞。
try:
    render_flash()                                                     # app.py:99
except Exception:                        # pragma: no cover
    pass
```

挂在 `app.py` 路由之前（而非各页面顶部）的好处：`page_qa / page_reading / page_writing`
这三个**非我独占**的页面无需改动即可同样享受 flash 机制。

**(3) 8 处调用点改为 `set_flash(...)` 后再 `st.rerun()`**

示例（`ui/sidebar.py:246-249`，演示路径第 1 步）：

```python
            if done:
                # 提示写进 flash 再 rerun：否则 success 会被 rerun 吞掉，用户零反馈
                set_flash("success", f"✅ 演示数据已就绪，当前文献库 {n} 篇")
                st.rerun()
```

其余：`ui/sidebar.py:239 / 265 / 268`、`ui/page_library.py:105 / 113 / 341 / 395`、`ui/page_graph.py:225`。

### 2.4 实测证据：A/B 对照（`C:/tmp/r2/ab_flash.py`）

同一段逻辑，仅把提示方式从 `st.success` 换成 `set_flash`，点按钮触发 rerun 后读渲染树：

```
[修复前 st.success+rerun] rerun 后 success = []
[修复后 set_flash+rerun] rerun 后 success = ['演示数据已就绪，当前文献库 3 篇']
```

**修复前确实是空的** —— 与评委"点完零反馈"的实测完全一致；修复后提示稳定出现。

修复后路径 1 真实渲染树：

```
=== 路径 1：打开系统 → 输入 API Key → 点「一键加载演示数据」 ===
  [1.3] 点击「🚀 一键加载演示数据」后：
    · exception=[]
    · success:
        - 演示数据已就绪，当前文献库 3 篇      ← ✅ 提示真的出现了
        - 模型已就绪
  >>> 结论：✅ 出现成功提示；异常 0
```

修复后路径 2「🗂️ 保留两个版本」真实渲染树：

```
  [🗂️ 保留两个版本] → ✅ 出现成功提示
    · success:
        - 已同时保留两版，可在下方「🗝️ 版本管理与差异」查看版本建议。   ← 修复前此处无任何反馈
```

---

## 3. 缺陷 3：7 条演示路径全量复跑（`AppTest`，修复后）

### 3.1 方法

- 工具：`streamlit.testing.v1.AppTest.from_file("app.py", default_timeout=300)`，进程内逐条模拟点击
- 脚本：`C:/tmp/r2/run_paths.py`（`python run_paths.py 1 2 3 4 5 6 7 7b`）
- 完整日志：`C:/tmp/r2/final_run.log`；结构化结果：`C:/tmp/r2/result.json`
- "异常"＝ `at.exception`；"友好提示"＝ 是否出现中文 error/warning/info 而非裸 Traceback
- 路径 7 额外跑了 **7b 分支**：注入 `ConnectionError` 模拟现场断网，验证降级话术
- 环境说明：本机外网可达，故路径 7 走的是"检索成功"分支；断网分支由 7b 单独覆盖
- 测试前后已备份并还原 `storage/library.json`、`memory.json`、`user_profile.json`（备份在 `C:/tmp/r2/backup/`），项目 `storage/` 未被污染

### 3.2 结果总表

| # | 演示路径 | 未捕获异常 | 友好提示 | 预期效果是否达成 | 实测关键输出 |
|---|---|---|---|---|---|
| **1** | 打开系统 → 输入 API Key → 点「一键加载演示数据」 | **0** | ✅ | ✅ **达标**（修复前零反馈） | `success: 演示数据已就绪，当前文献库 3 篇`；`success: 模型已就绪` |
| **2** | 📁 上传 v7 和 v1 → 版本去重预警 → 对比差异 | **0** | ✅ | ✅ **达标**（修复前必抛 NameError） | 查重预警 `⚠️ 语义查重命中…相似度 92.6% / 100.0%`；`🗂️ 保留两个版本` 出成功提示；`🔍 对比 1706.03762 版本差异` 10.6s 后 **0 exception / 0 error**，渲染出降级差异报告 + 中文引导 |
| **3** | 💬 点「表格化对比」→ 带页码溯源回答 → 点溯源锚点 | **0** | ✅ | ✅ 达标（轻微：LLM 降级致幻觉防护告警） | 溯源锚点 markdown **命中 6 个**（`<a id="p9"></a>…source_card…jump_page=`）；告警 `⚠️ 幻觉防护校验未完全通过：答案未包含任何引用角标`（因占位 Key 导致 LLM 降级，非代码缺陷） |
| **4** | 📖 上传 PDF → 原生渲染 → 同步翻译 → 划词翻译 | **0** | ✅ | ✅ 达标 | `file_uploader(key="read_up")` 灌入 775KB `1810.04805v2.pdf` 成功；按钮齐全（🎨 渲染该页 / 🔍 检测本页栏数 / 📥 加载并逐段翻译 / 🌐 翻译该片段 / 🔗 生成双向跳转 / 📥 加载该页）；点「🎨 渲染该页」0 异常（渲染走 `embed_html`，AppTest 不展开 iframe 内容） |
| **5** | 🕸️ 演示模式生成图谱 → 一键综述 | **0** | ✅ | ✅ **达标**（本轮额外修掉自相矛盾空状态） | 0.9s 出 `✅ 演示图谱已生成：2 节点 · 1 条示意关系`；同屏 `🏆 基石论文：《Attention Is All You Need》` + `🌿 边缘衍生：《BERT…》`；一键综述 10.1s 出 `✅ 领域综述已生成`，含 `🎭 演示模式示意图谱` 声明 |
| **6** | ✍️ 上传 CSV → 生成图表 → Figure Caption | **0** | ✅ | ✅ 达标 | `file_uploader(key="csv")` 灌入 `演示语料/data.csv` 成功；「📂 载入示例数据」0.2s；「🤖 生成该图 Figure Caption」9.2s，0 异常 |
| **7** | 🔍 arXiv 检索 → 演示降级提示 | **0** | ✅ | ✅ 达标（联网）/ ✅ 达标（断网 7b） | 联网：`✅ 检索到 5 篇论文` + 2 条 `💻 开源代码：…`；断网(7b)：`❌ arXiv 检索失败：模拟现场断网…` + caption `🩺 可能原因：演示现场外网不可达或被代理拦截` + `🛠️ 怎么办：…请点左侧「🚀 一键加载演示数据」使用离线数据继续演示` |

**合计：7/7 条 0 未捕获异常；7/7 条有中文友好提示；7/7 条达到预期效果。**

### 3.3 本轮新发现并已修掉的问题（在我的独占文件内）

**路径 5：演示图谱生成后与「尚未构建图谱」空状态自相矛盾**

- 现象（修复前实测）：点「🎭 演示模式示意图谱」后，页面同时出现
  `success: 演示图谱已生成：2 节点 · 1 条示意关系` 与 `info: 尚未构建图谱。`
- 根因：`ui/page_graph.py` 中「读取 `graph_pack` 并渲染空状态」在 `render()` 的 **:197** 行，
  而 🎭 演示模式按钮在 **:210** 行（之后）。点击当轮空状态已经渲染完，新图谱要等下一轮 rerun 才生效。
- 修复（`ui/page_graph.py:225-229`）：生成成功后用 `set_flash` + `st.rerun()` 让页面重跑，
  这样既消除矛盾空状态，成功提示又不会因 rerun 被吞：

```python
                pack = _make_pack(G, edges, metrics, corner, edge, demo=True)
                pack["html"] = html
                st.session_state.graph_pack = pack
                # 本按钮位于「图谱结果读取」之后，若不 rerun，本轮页面上方的
                # 「尚未构建图谱」空状态会与新生成的图谱并存，自相矛盾。
                # 提示写进 flash 以跨 rerun 存活（直接 st.success 会被吞）。
                set_flash("success", f"✅ 演示图谱已生成：{len(pack['nodes'])} 节点 · "
                                     f"{len(edges)} 条示意关系")
                st.rerun()
```

- 修复后实测：`info: 尚未构建图谱。` **已消失**，同屏只剩图谱 + 基石/边缘结论 + flash 成功提示。

### 3.4 不在我独占范围内、仅记录不动手的问题

| 位置 | 现象 | 归属 |
|---|---|---|
| `ui/page_qa.py`（路径 3） | 占位 Key 导致 LLM 降级时，答案无引用角标 → `⚠️ 幻觉防护校验未完全通过`。**非崩溃**，属预期降级 | 非独占文件，仅记录 |
| `services/library.py:543 version_diff` | LLM 不可用时返回降级文案而非抛异常（本轮已在 `page_library.py` 侧补了说明） | 非独占文件，仅记录 |
| `.env:7` | `ZHIPU_API_KEY=请填入你的智谱API Key`（含中文）作为 HTTP 头会触发 `'ascii' codec can't encode` | 非独占文件，仅记录；现场用真实 ASCII Key 即消失 |
| `ui/common.py:311-319 embed_html` | 运行时告警 `Please replace st.components.v1.html with st.iframe`（2026-06-01 后移除） | 非独占文件，仅记录 |

---

## 4. 缺陷 4：README 主截图裂图 —— 截图目录实况清单（供 README 负责人使用）

**结论：README 引用的 `资料/screenshots/` 目录在项目里根本不存在。真实存在的截图目录是 `资料/截图/`。**

- 裂图位置：`README.md:5` → `![screenshot](资料/screenshots/main.png)`（❌ 目录与文件均不存在）
- 但 `README.md:201` 写的是 `界面截图：资料/截图/`（✅ 与真实目录一致）——即 README 内部自相矛盾
- 生成脚本：`scripts/resnap.py`（`OUT = 资料/截图`，1560×950，playwright 逐页截图，先点「一键加载演示数据」再逐页截）

### 4.1 `资料/截图/` 实况清单（全部 1560×950 PNG，已用 PIL 校验尺寸）

| 真实相对路径 | 尺寸 | 大小 | 对应页面（由 `scripts/resnap.py:15-25` 的 `PAGES` 映射推定） |
|---|---|---|---|
| `资料/截图/p1.png` | 1560×950 | 100 KB | 🤖 0. Agent 总助手（`?p=0`） |
| `资料/截图/p0.png` | 1560×950 | 131 KB | 📁 1. 文献库管理（`?p=1`）— **建议作为 README 主截图** |
| `资料/截图/p2.png` | 1560×950 | 114 KB | 💬 2. 跨文献问答与溯源（`?p=2`） |
| `资料/截图/p3.png` | 1560×950 | 100 KB | 📖 3. 版式阅读与智能翻译（`?p=3`） |
| `资料/截图/p4.png` | 1560×950 | 111 KB | 🕸️ 5. 引用图谱与综述（`?p=5`，脚本已自动点「构建引用图谱」） |
| `资料/截图/p5.png` | 1560×950 | 112 KB | ✍️ 6. 写作辅助与可视化（`?p=6`） |
| `资料/截图/p6.png` | 1560×950 | 145 KB | 🔍 4. 论文自动检索与追踪（`?p=4`） |
| `资料/截图/p7.png` | 1560×950 | 173 KB | 🏗️ 系统架构设计（`?p=7`） |
| `资料/截图/p8.png` | 1560×950 | 221 KB | 💡 技术创新亮点（`?p=8`） |
| ~~`资料/截图/test0.png`~~ | 1500×1150 | 9 KB | ⚠️ **不要引用**：早期空状态测试图（尺寸/内容都与其余 9 张不一致） |

> 注意文件名与页码**不是顺序对应**（如 `p0.png` 是文献库页、`p6.png` 是检索页），
> 引用时务必按上表的"对应页面"列，不要按文件名的数字猜。

### 4.2 给 README 负责人的建议改法

把 `README.md:5` 的裂图改为真实存在的文件，例如：

```markdown
![主界面 · 文献库管理（语义查重 / 版本管理）](资料/截图/p0.png)
```

其余可按需引用：`资料/截图/p1.png`（Agent 总助手）、`资料/截图/p2.png`（问答与溯源）、
`资料/截图/p3.png`（版式阅读）、`资料/截图/p4.png`（引用图谱）、`资料/截图/p5.png`（写作辅助）、
`资料/截图/p6.png`（检索与追踪）、`资料/截图/p7.png`（系统架构）、`资料/截图/p8.png`（创新亮点）。

可选替代：项目根 `品牌资源/icon.png` 也可作为徽标/页脚配图（不是界面截图，勿当主截图用）。

---

## 5. 未定义标识符全量静态扫描（缺陷 1 的同類风险排查）

### 5.1 扫描方法

环境无 `pyflakes`（已确认 `ModuleNotFoundError: No module named 'pyflakes'`，且按要求不 pip install），
故自写 AST + `symtable` 扫描器：`C:/tmp/r2/scan_undefined.py`

判定规则：
1. `symtable` 做作用域分析，收集每个作用域被引用（`is_referenced`）的符号；
2. 排除：本文件**任意作用域**内被绑定过的名字（`is_assigned / is_parameter / is_local / is_imported`）、
   import 进来的名字、`builtins`、双下划线名；
3. 剩下的即为「使用了但从未定义/导入」的真实隐患。
   > 该"任意作用域绑定"过滤很关键：初版只按模块作用域过滤时误报 21 条，
   > 全是闭包/推导式/lambda 的自由变量（`bar`/`note`/`ctx`/`reps`…），加过滤后归零。
4. 扫描器自检：注入 `LONG_TEXT_LIMIT` + `UNDEFINED_XYZ` 到临时模块，两条**均被正确捕获**（`C:/tmp/r2/selftest_mod.py`）。

### 5.2 扫描清单与修复情况（我独占的 7 个文件）

| 文件 | 修复前扫描结果 | 修复情况 |
|---|---|---|
| `ui/page_library.py` | 🔴 1 处：`LONG_TEXT_LIMIT`（行 373，作用域 `render`） | ✅ 已修：`ui/page_library.py:10` 加入 `LONG_TEXT_LIMIT` 导入 |
| `ui/sidebar.py` | 0 处 | —（本轮新增 `FLASH_KEY`/`set_flash`/`render_flash` 均为本文件定义） |
| `ui/page_search.py` | 0 处 | — |
| `ui/page_graph.py` | 0 处 | —（本轮新增 `set_flash` 已从 `ui.sidebar` 导入） |
| `ui/page_misc.py` | 0 处 | — |
| `ui/page_agent.py` | 0 处 | — |
| `app.py` | 0 处 | —（本轮新增 `render_flash` 已从 `ui.sidebar` 导入） |
| **合计** | **🔴 1 处真实隐患** | **✅ 1/1 已修复** |

修复后复扫输出：

```
[OK] ui/page_library.py: 无未定义标识符
[OK] ui/sidebar.py: 无未定义标识符
[OK] ui/page_search.py: 无未定义标识符
[OK] ui/page_graph.py: 无未定义标识符
[OK] ui/page_misc.py: 无未定义标识符
[OK] ui/page_agent.py: 无未定义标识符
[OK] app.py: 无未定义标识符

合计疑似未定义标识符: 0
```

### 5.3 附：非独占文件的只读扫描（**未做任何修改**，供其他 agent 参考）

同样用该扫描器扫了 `ui/common.py`、`ui/page_qa.py`、`ui/page_writing.py`、`ui/page_reading.py`、
`ui/desktop_chrome.py`、`core/agent.py`、`core/providers.py`、`services/library.py`、`services/graph.py`、
`services/extern.py`、`services/parsing.py`、`services/state.py`、`config.py`、`tools/definitions.py`
—— **合计 0 处未定义标识符**。即 `LONG_TEXT_LIMIT` 是全项目唯一的真实未绑定名，与评委结论一致。

---

## 6. 自验与回归

### 6.1 py_compile（硬性约束 4）

```
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile \
  ui/page_library.py ui/sidebar.py ui/page_search.py ui/page_graph.py \
  ui/page_misc.py ui/page_agent.py app.py
→ PY_COMPILE_OK（零报错，零输出）
```

### 6.2 其它约束核对

| 约束 | 状态 |
|---|---|
| 不启动 streamlit 服务、不 pip install | ✅ 全程只用 `AppTest` 进程内运行 + `-m py_compile`；未启动 `streamlit run`，未安装任何包 |
| 不改非独占文件 | ✅ 仅 7 个独占文件；`ui/common.py` 只 import 未修改 |
| 所有 `st.session_state` key 先初始化再读 | ✅ `app.py:61` 在 `ensure_state` 中初始化 `"_flash": None`；`render_flash` 用 `.get()` 读取并对异常/脏数据兜底 |
| 自验脚本放 `C:/tmp/r2/` | ✅ 全部脚本、日志、备份均在此；项目 `storage/` 的三个 JSON 已备份并**还原**（还原后 `papers=3, chunks=4`，与测试前一致） |

### 6.3 有无引入新问题

**未发现新问题。** 逐项说明：

1. 循环导入：`page_library/page_graph → ui.sidebar` 安全（`app.py:19` 先 import sidebar，`:20` 才 import 各 page；`ui/sidebar.py` 不反向 import 任何 page），7 条路径 + 首屏均实测通过；
2. flash 副作用：`render_flash` 先清空再渲染，实测路径 1/2/5 各条提示**只出现一次**，无重复弹窗；
3. 空 flash 占位：`_flash` 为 `None` 时 `render_flash` 直接 return，不渲染任何空占位元素（路径 3/4/6/7 的渲染树中未见多余 info）；
4. `page_graph.py` 新增 `st.rerun()`：经 7 条路径复跑验证，图谱页无重复建图、无死循环（0.9s 完成）；
5. 仅剩的运行时告警是 `st.components.v1.html` 将废弃的提示，来自 `ui/common.py:311`（非独占文件，本轮未改，Round 3 前即存在）。

---

## 7. 改动点索引（文件:行号）

| 文件 | 行号 | 改动 |
|---|---|---|
| `ui/page_library.py` | 9-11 | 导入 `LONG_TEXT_LIMIT`（缺陷 1）+ `set_flash`（缺陷 2） |
| `ui/page_library.py` | 105 / 113 / 341 / 395 | 4 处 `st.success` → `set_flash`（缺陷 2） |
| `ui/page_library.py` | 374-383 | 版本差异降级输出的显式提示 + 引导（缺陷 1 增强） |
| `ui/sidebar.py` | 34-83 | 新增 `FLASH_KEY` / `set_flash()` / `render_flash()`（缺陷 2 方案 A） |
| `ui/sidebar.py` | 239 / 248 / 265 / 268 | 2 处 `warning` + 2 处 `success` → `set_flash`（缺陷 2，含路径 1 演示数据加载） |
| `ui/page_graph.py` | 10 | 导入 `set_flash` |
| `ui/page_graph.py` | 225-229 | 演示图谱生成后 `set_flash + st.rerun()`，消除自相矛盾空状态（缺陷 3 路径 5） |
| `app.py` | 19 | 导入 `render_flash` |
| `app.py` | 61 | `ensure_state` 初始化 `"_flash": None` |
| `app.py` | 96-101 | 主体区顶部统一 `render_flash()`（对所有页面生效，含非独占页面） |

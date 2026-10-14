# Round 13 · 话术6 —— 跨平台兼容性检查（过程报告）

负责人：`crossplat`
交付物：`docs/CROSS_PLATFORM.md`、本报告、4 类 .py 修复 + 四门禁实测
解释器：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（未使用 base python）

---

## 1. 扫描覆盖范围与口径

- **运行时模块**（判定「会不会在 Linux 上炸」以此为准）：
  `core/ services/ tools/ ui/ app.py config.py` —— 共 **41 个 `.py`**
- **非运行时**（只盘点、不改）：`scripts/`（开发脚本）、`desktop/`（Windows 桌面壳，`app.py` 不 import）、
  `version_history/`（历史快照）
- 扫描命令与原始计数见 §2–§6，每一处绝对数量都由 `grep ... | wc -l` 或探针脚本得出，不靠印象。

---

## 2. 路径写法（结论：运行时 0 处需改）

| 检查项 | 运行时命中 | scripts/+desktop/ 命中 |
|--------|-----------|------------------------|
| `os.path.join(...)` | **16** | — |
| 盘符路径字面量 `X:\Users` / `X:/Users` / `C:\Program Files` | **0** | **15**（15 条全在 `scripts/`；`desktop/` 为 0） |
| 反斜杠作路径分隔符 | **0** | 仅 `desktop/paths.py` 的**注释** |
| 路径字符串拼接 `+ "/"` | **0** | 3 |
| `os.sep` / `ntpath` / `posixpath` | **0** | 0 |
| Windows-only API（`os.startfile`/`CREATE_NO_WINDOW`/`STARTUPINFO`/`ctypes.windll`/`msvcrt`/`winreg`） | **0** | **21**（`desktop/` 20 + `scripts/_verify_desktop.py` 1） |

16 处 `os.path.join` 逐一判断「在 Linux 上会怎样」：全部由 `os.path.join` 产生分隔符 → 与 `os.sep` 一致 →
**16/16 两个平台都正确**，无一需改。

**假阳性排除**（搜 `\\` 时命中的非路径转义序列，逐条看过，不计入）：
- `services/writing.py:1337/1339/1341/1346/1347/1348/1349/1351/1352` —— LaTeX 命令
  （`\documentclass` / `\usepackage` / `\usetikzlibrary` / `\begin{document}`）
- `services/writing.py:1396` —— `"\\begin{tikzpicture}"` 同上
- `services/library.py:829` —— 注释里的 `split("\n\n")`
- `services/translate.py:407/1154/1156/1168` —— 注入的 JS 字符串（`\s+`、`\u21a9` 等）
- `services/rag.py:710`、`services/query_expansion.py:240`、`ui/page_reading.py:1112` —— 注释/正则里的 `\n`

`scripts/` 那 15 条硬编码 `C:\...` 全部是开发脚本（`apptest_check.py` / `make_scanned.py` /
`make_tech_pdf.py` / `release.py` / `selftest.py` / `_reshoot_shots.py` / `_verify_desktop.py` 等），
不进容器业务路径，**不改**。

根目录唯一定义在 `config.py:13 ROOT = Path(__file__).resolve().parent`，全程 `pathlib` `/` 运算符 →
不存在「拼出来错位」的可能。

---

## 3. 文件编码（结论：0 处需改）

- 缺 `encoding=` 的 `open(` 共 **12** 处：6 处二进制模式、6 处第三方库 `.open()` → **文本模式缺 encoding 的实例为 0**
- `Path.read_text()` / `write_text()` 运行时 **8** 处，**8/8 带 `encoding="utf-8"`**
  （`core/memory.py:18/23/26`、`services/graph.py:528/529`、`services/state.py:90/95`、`ui/sidebar.py:96`）
- 41 个运行时 `.py` 逐个 `bytes.decode('utf-8')` → **41/41 合法 UTF-8**，无 GBK 残留
- 无 `# -*- coding:` 声明：Python 3 默认 UTF-8，两平台一致，**无需补**
- `demo_data/data.csv` 是 UTF-8 **带 BOM**；实测 `pd.read_csv` 后 `df.columns[0] == 'model'`（无 `\ufeff`）→ 无需改
- `sys.getfilesystemencoding()` 实测 `utf-8`

---

## 4. 临时文件（7 处调用点，3 处有泄漏，全部已修）

| # | 位置 | 改动前 | 决策 |
|---|------|--------|------|
| 1 | `services/writing.py:1406` | `finally: shutil.rmtree` ✅ | 不改 |
| 2 | `tools/definitions.py:207` | `os.remove` 只在正常路径；异常不删；`open()` 句柄未关 | **改** |
| 3 | `tools/definitions.py:328` | **完全没有清理** | **改** |
| 4 | `ui/page_reading.py:223` | 前缀过滤 + TTL + `rmtree` ✅ | 不改 |
| 5 | `ui/page_reading.py:312` | `finally: shutil.rmtree` ✅ | 不改 |
| 6 | `ui/page_reading.py:1071` | 只 `close()` 不 `remove()`，且每次 rerun 新建 | **改** |
| 7 | `ui/page_reading.py:1424` | `finally: shutil.rmtree` ✅ | 不改 |

> 📌 **上表的行号是「本轮修复之后」的位置**。我这两处修复分别给
> `tools/definitions.py` 增加了 9 行、给 `ui/page_reading.py` 增加了 9 行，
> 改前的行号是 `definitions.py:206/319` 与 `page_reading.py:1063/1415`（`git show HEAD:<file>` 可复核）。
> 引用这两个文件行号的其他文档（如 `docs/technical_doc.md`）如果写的是旧号，会差 9 行。

硬编码 `/tmp`、`C:\temp`、`%TEMP%`、`Path("tmp")`：**0 处**
（全文只有 `ui/page_reading.py:223` 用 `tempfile.gettempdir()` —— 正确写法）。

**改动前本机 `%TEMP%` 实测残留**（Linux 对应 `/tmp`，可直接类推）：

```
tmp*.pdf          143 个 / 1142.9 MB
ai4s_code_*        19 个
fig_*               0 个
wcopilot_tikz_*     0 个
```

有 `try/finally` 的 3 处残留为 0，没有的 3 处合计 162 个 / 1.14 GB —— 清理逻辑有无与残留量完全对应。

---

## 5. 中文字体（11 行 CSS 已补回退链；PDF 侧无需系统字体）

运行时模块里显式写字体名的共 **11 行**，全部是**浏览器端 / SVG 端** CSS，全部已补回退链。
PDF 侧 `services/translate.py:_cjk_font()` 用 `pymupdf.Font("china-s")` —— 实测解析为
`Font('Droid Sans Fallback Regular')`，是 PyMuPDF **内置**字体（随 wheel 打包），
两平台行为一致 → **容器不需要装任何中文字体包**。

未改的两类（有意为之）：
- `services/translate.py:506` `'Times New Roman',Times,serif` —— PDF 正文是拉丁文本
- `services/graph.py:560` `face:'sans-serif'` —— vis-network 画布标签是英文论文标题

非运行时未改：`version_history/app_streamlit_v1.py:243/273/794`（历史快照，不被引用）、
`scripts/make_tech_pdf.py:41`（本机 Windows 出技术文档 PDF 用）。

---

## 6. requirements.txt（只读分析，未改文件）

逐包给出约束 / 本机实测版本 / wheel tag / Linux 风险，见 `docs/CROSS_PLATFORM.md §5`。

**我没有批准自己改，移交 team-lead 决策的 4 条**：

| 优先级 | 建议 | 理由 |
|--------|------|------|
| 🟢 低 | `plotly>=5.20` → `>=5.20,<8.0` | 补「下一个大版本」上界，与 `streamlit` 同风格；纯 Python，风险极低 |
| 🟢 低 | `arxiv>=2.1` → `>=2.1,<5.0` | 已从 2.x 实际跨到 4.x（本机 4.0.1），锁上界防 5.x 静默破坏；代码只用到 `SortCriterion/Search/Client`，4.x 均在 |
| 🟢 低 | `pillow>=10` → `>=10,<13.0` | 同上 |
| 🔴 高（仅 ARM） | 若交付环境可能是 aarch64，先实测 `chromadb` 整条链（尤其 `onnxruntime`） | slim 镜像无 gcc，缺 wheel 直接构建失败。**本机无 Linux/ARM，未实测** |

**反向建议（别做）**：不要把 `matplotlib` 加回 requirements —— `render_dot` 第③级是 matplotlib，
默认字体 DejaVu Sans 无 CJK 且不回退，会把中文 DOT 标签渲染成方框，**装了更差**。

---

## 7. 逐条修改记录

> 每行格式：`文件:行号 | 改前 | 改后 | 验证方式`

| # | 文件:行号 | 改前 | 改后 | 验证方式 |
|---|-----------|------|------|----------|
| 1 | `tools/definitions.py:6` | （无 `import shutil`） | `import shutil` | 门禁1 `compileall` exit 0；探针 P2 里 `shutil.rmtree` 生效 |
| 2 | `tools/definitions.py:328`（`t_run_python`，函数体 318-358） | `work = tempfile.mkdtemp(prefix="ai4s_code_")`，`except` 后直接 return，**无清理** | 函数末尾新增 `finally: shutil.rmtree(work, ignore_errors=True)` | **实测**：探针 P1 连跑 3 次 → 新增 `ai4s_code_*` **3** 个（残留 16→19）；探针 P2 同样连跑 3 次 → 新增 **0** 个（19→19）；3 次 `rc=0 stdout='hi'` 功能不变 |
| 3 | `tools/definitions.py:207-218` `_ingest_arxiv_paper` | `tmp = NamedTemporaryFile(delete=False)` → `extern.download(...)` → `data = open(tmp.name,"rb").read()`（句柄不关）→ `os.remove(tmp.name)`（异常时不执行） | `tmp.close()` 先交出句柄 → `try: download; with open(...) as fh: data=fh.read()` → `finally: os.remove(tmp.name)` | **实测**：探针 P2 注入下载失败 → 临时 PDF 新增 **0**（改动前异常路径必留 1 个）；探针 P3 成功路径 → `{'ok': True, 'added': '9999.99999.pdf', 'title': 't', 'pages': 1}`，临时 PDF 新增 **0**，字节正常读回 |
| 4 | `ui/page_reading.py:1071-1079` `_resolve_doc` | 每次调用无条件 `NamedTemporaryFile(delete=False)` + 写入，只 `close()` 不 `remove()` | 按「文件名 + 字节数」复用本会话已落盘的同一临时路径，换文件时才重新落盘 | **实测**：探针 P5 用 `git show HEAD:ui/page_reading.py` 的旧版跑同一套 harness → 5 次 rerun 新建 **5** 个、5 条路径互不相同；探针 P4 用现行代码 → 5 次 rerun 新建 **1** 个、5 条路径相同；换同名同尺寸文件新建 **0**；换不同文件新建 **1**（正确）；落盘字节与上传字节**完全一致**（50009 B） |
| 5 | `services/translate.py:309 / 498 / 536 / 772 / 803 / 805 / 1189 / 1191`（8 行） | `'Microsoft YaHei',sans-serif` / `'Microsoft YaHei','Segoe UI',sans-serif` / `"Microsoft YaHei",sans-serif` | 在通用 `sans-serif` 前插入 `'PingFang SC','Noto Sans CJK SC','WenQuanYi Micro Hei'` | 探针 P3 逐行扫描：**8/8 行**均含 `Noto Sans CJK SC` 与 `WenQuanYi Micro Hei`，未补回退 = `[]` |
| 6 | `services/wcopilot_dotrender.py:259` | `font-family="Segoe UI,Microsoft YaHei,sans-serif"`（SVG） | 补同样的 3 个回退 | 探针 P3：`Linux回退=True` |
| 7 | `ui/common.py:121` | `'Segoe UI','Microsoft YaHei',sans-serif` | 补 3 个回退 | 探针 P3：`Linux回退=True` |
| 8 | `ui/page_misc.py:21` | `.arch{font-family:'Segoe UI','Microsoft YaHei',sans-serif;...}` | 补 3 个回退 | 探针 P3：`Linux回退=True` |

合计：**4 类问题、8 个文件位置组、11 行 CSS + 3 段逻辑**。

探针脚本位于系统临时目录（`C:/Users/Administrator/AppData/Local/Temp/_xp_probe1..6.py`），
**未写入仓库**，跑完即弃（P4/P5 自己产生的临时 PDF 已在脚本末尾清理）。

---

## 8. 四门禁实测（串行，改动后）

> ⚠️ 已按要求**串行**执行，`_smoke_ui.py` 与 `_smoke_read.py` 未并行。

### 门禁 1 · compileall
```
$ PY -m compileall -q core services tools ui app.py config.py
GATE1_COMPILEALL exit=0
```

### 门禁 2 · unittest
```
$ PY -m unittest discover -s tests
..................
----------------------------------------------------------------------
Ran 18 tests in 1.698s
OK
GATE2_UNITTEST exit=0
```

### 门禁 3 · _smoke_ui.py
```
$ PY scripts/_smoke_ui.py
  [🤖 0. Agent 总助手]    0.0s  exception=0
  [📁 1. 文献库管理]    0.1s  exception=0
  [💬 2. 跨文献问答与溯源]    0.0s  exception=0
  [📖 3. 版式阅读与智能翻译]    0.9s  exception=0
  [🔍 4. 论文检索与追踪]    0.1s  exception=0
  [🕸️ 5. 引用图谱与综述]    0.0s  exception=0
  [✍️ 6. 写作辅助与可视化]    0.1s  exception=0
  [🏗️ A. 系统架构设计]    0.0s  exception=0
  [💡 B. 技术创新亮点]    0.1s  exception=0
  [1/9] 🔍 检索 arXiv                   0.1s  exception=0 友好提示=有 OK
  [2/9] 🔍 PubMed                     0.1s  exception=0 友好提示=有 OK
  [3/9] 🔍 DuckDuckGo                 0.1s  exception=0 友好提示=有 OK
  [4/9] ✨ 刷新并推荐                     0.1s  exception=0 友好提示=有 OK
  [5/9] 🎧 运行一次追踪                    0.1s  exception=0 友好提示=有 OK
  [6/9] 🎧 批量下载入库                     0.1s  exception=0 友好提示=有 OK
  [7/9] 🔍 对比 1706.03762 版本差异         0.1s  exception=0 友好提示=有 OK
  [8/9] 🕸️ 构建引用图谱                    0.1s  exception=0 友好提示=有 OK
  [9/9] 📑 一键生成领域综述                  0.2s  exception=0 友好提示=有 OK
========================================================================
✅ 冒烟通过，全部页面 0 未捕获异常
GATE3_SMOKE_UI exit=0
```

### 门禁 4 · _smoke_read.py
```
$ PY scripts/_smoke_read.py
（Part 1/2 全 PASS，摘末段）
  ✅ PASS  跨页跳转，识别 qa_jump_page 并提示「已从问答页跳转」
  ✅ PASS  跨页跳转，消费后清理 key（不重复跳转）
  ✅ PASS  跨页跳转，二次运行不再重复提示
================ 汇总 =================
PASS 60 / FAIL 0
GATE4_SMOKE_READ exit=0
```

**改动前基线同样四条全绿**（compileall exit 0 / 18 OK / 9 页 0 异常 / PASS 60 FAIL 0），
改动后复跑仍全绿 → 修复没有引入回归。

---

## 9. 与 `deploy-finish` 的分工边界

- 我看到 `git diff` 里已有 `Dockerfile` / `docker-compose.yml` / `.dockerignore` 的改动（storage 种子、
  命名卷、静态资源放行），**我没有碰这三个文件，也没有重复处理 storage 初始化**。
- 我**只**在报告与 `docs/CROSS_PLATFORM.md §6` 里提出 2 条 Dockerfile 建议（补 `PYTHONUTF8=1 LANG=C.UTF-8`、
  保留 graphviz），属**建议**，未落笔。
- `requirements.txt` 我只做只读分析，4 条建议未改文件。

---

## 10. 补记：与 `deploy-finish` 交叉复核后的两件事

### 10.1 我的两份文档换行符已逐字节验过，是纯 LF

`deploy-finish` 提醒：Windows 上用 `Path.write_text()` / `open(...,'w')` 改交付文件会被默认换行翻译转成 CRLF
（他改 `docker-compose.yml` 时踩到，181 处，已转回 LF）。我逐字节核过自己产出的两个文件：

```
docs/CROSS_PLATFORM.md            CR=0    LF=346     → 纯 LF ✅
docs/review/round13_crossplat.md  CR=0    LF=229     → 纯 LF ✅
（对照：docs/technical_doc.md     CR=960  LF=960     → 仓库既有 .md 在 Windows 工作区是 CRLF）
```

结论：我的产出是 LF，无需转换。补充一句——即便写成 CRLF 也**不构成跨平台故障**：仓库
`core.autocrlf=true`，`git ls-files --eol` 显示 196 个受控文件在索引里全是 `i/lf`（工作区 CRLF 只是检出态），
提交时被规范化，Linux 检出得到 LF；且本项目无 `.sh`、Dockerfile 不 RUN 外部脚本，不存在
「CRLF → bad interpreter」这条经典坑。

我改的 6 个 `.py` 也核过换行**统一**（无混入 LF 的半截文件）：
`services/translate.py` 1481/1481、`services/wcopilot_dotrender.py` 299/299、`tools/definitions.py` 448/448、
`ui/common.py` 876/876、`ui/page_misc.py` 133/133、`ui/page_reading.py` 1740/1740 均为 CRLF。

### 10.2 `app.py` 的权限误导文案已被队友修掉 —— 我上报的那条决策项作废

我在 §7「边界确认」之外单独向 team-lead 上报过一条决策项：`app.py:34` 的兜底 `except` 把
`PermissionError`（真因是挂载权限）显示成「依赖未安装或版本不匹配」，会把排查方向带偏，
而改文案不在本轮给我的 4 类职权内，我没动 `app.py`。

**现已由队友修掉**（`app.py:43`）：

```python
st.error("⚠️ 操作失败：加载依赖模块。" + ("storage/ 目录不可写（容器挂载属主不对）" if isinstance(e, OSError)
                                     else "依赖未安装或版本不匹配，应用无法启动。"))
```

我核过分流正确性：`PermissionError` 是 `OSError` 子类 → 进权限分支 ✅；
`ModuleNotFoundError` / `ImportError` **不是** `OSError` → 仍进「依赖未安装」分支 ✅。
（小瑕疵：`FileNotFoundError` 这类也属 `OSError`，会被归到「storage 不可写」，但 import 期
几乎只可能是权限或依赖两类，可接受。）

`docs/CROSS_PLATFORM.md §6` 第 6 条已据此刷新为「已修复」。
**这条决策项不需要 team-lead 再处理了。**

### 10.3 四门禁在「队友改动合入后」重跑，仍全绿

队友在我上一次门禁之后又改了 `app.py`、`core/memory.py`、`services/state.py`
（P1–P4：权限分流、`_ensure_lib_dict`、`_normalize()`）。我的门禁结果随即失效，已**串行重跑**：

```
G1: -m compileall -q core services tools ui app.py config.py   → 0
G2: -m unittest discover -s tests   → Ran 18 tests in 1.535s  OK              → 0
G3: scripts/_smoke_ui.py            → ✅ 冒烟通过，全部页面 0 未捕获异常        → 0
G4: scripts/_smoke_read.py          → PASS 60 / FAIL 0                        → 0
```

两次全绿（我的改动后一次、队友改动合入后一次）→ 我的 8 处修复在与他人改动共存时无冲突、无回归。

---

## 11. 遗留风险

1. **未在真 Linux 上跑过** —— 所有「Linux 表现」结论来自静态核对 + Windows 实测推导。
   `docs/CROSS_PLATFORM.md §8` 给了「干净 Linux 从零跑起来」的 8 步验证脚本，建议在目标机器上跑一遍。
2. **ARM/aarch64**：`chromadb` 依赖链（尤其 `onnxruntime`）可能无 wheel，slim 镜像无 gcc → 构建失败。
   本机无 ARM，未实测。
3. **阅读页上传的临时 PDF 在会话结束后仍不删除** —— 本轮把「每 rerun 一份」降为「每上传一份」（5→1），
   跨会话仍会累积，但速率降一个量级。彻底清零需要给 `storage/uploads` 加带 TTL 的清道夫，属新增功能，本轮不做。
4. **`scripts/` 下 15 处 `C:\...`** —— 不进容器业务路径，非阻塞，可选清理。
5. **CSS 回退链只对 Linux 客户端生效**；若评委用 Linux 且未装任何 CJK 字体，中文由浏览器自身兜底，
   理论上仍有极小概率不理想。`docs/CROSS_PLATFORM.md §7` 给了客户端装字体命令。

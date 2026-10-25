# 跨平台兼容性说明（Windows 开发 → Linux 容器交付）

> 撰写：Round 13「话术6-跨平台兼容性检查」
> 扫描范围：`core/ services/ tools/ ui/ app.py config.py`（运行时）+ `scripts/ desktop/`（开发/桌面壳，仅盘点不改）
> 解释器：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`
> 本文档中所有「实测」均指在本机 Windows 上真实跑出来的数字；**没有在真·Linux 机器上跑过的项已显式标注「未实测」**。

---

## 0. 一句话结论

**运行时代码里没有一处 Windows-only 的写法**（盘符路径 0 处、Windows API 0 处、文本模式 `open()` 无 `encoding=` 0 处）。
真正的 Linux 风险不在「路径写法」，而在三件不显眼的事：

1. **临时文件无限堆积**（已修 3 处，实测本机残留 143 个临时 PDF / 1.14 GB）
2. **中文字体回退链缺失**（已修 11 行 CSS）——只在 Linux **客户端浏览器**上暴露
3. **`requirements.txt` 的原生扩展在 ARM/aarch64 上可能装不上**（未改，交决策）

PDF 侧的中文字体**不依赖操作系统字体**：`services/translate.py` 用的是 PyMuPDF 内置 `Font("china-s")`
（实测解析为 `Droid Sans Fallback Regular`），随 wheel 走，Linux 上无需装任何中文字体包。

---

## 1. 平台差异清单

| # | 位置 | Windows 表现 | Linux 表现 | 是否阻塞 | 修法 |
|---|------|--------------|-----------|---------|------|
| 1 | `tools/definitions.py:328` `t_run_python` 的 `mkdtemp(prefix="ai4s_code_")` | 每次执行代码泄漏一个目录（实测连跑 3 次残留 3 个） | 同，且容器 `/tmp` 不会自动回收 | ❌ 不阻塞，但长时间演示会涨 | **已修**：`try/finally` + `shutil.rmtree` |
| 2 | `ui/page_reading.py:1071` 阅读页上传 PDF | 每次 rerun 新建一份永不删除的临时 PDF（实测残留 143 个 / 1.14 GB） | 同；Docker 里 `/tmp` 随容器层增长，最坏撑爆可写层 | ⚠️ 演示久+多次上传时有风险 | **已修**：按「文件名+字节数」复用同一临时路径（实测 5 次 rerun 新建数 5 → 1） |
| 3 | `tools/definitions.py:207-218` arXiv 下载临时 PDF | 下载失败时不删临时文件；`open()` 句柄未关闭 | 同 | ❌ 不阻塞 | **已修**：`try/finally` + `with open` |
| 4 | 11 行 CSS 的 `font-family:'Microsoft YaHei',sans-serif` | 有雅黑，正常 | **Linux 客户端浏览器无雅黑/无 Segoe UI** → 落到通用 `sans-serif`；英文语系的 Debian/Ubuntu 默认 sans（DejaVu Sans）**无 CJK 字形** → 中文变方框 □ | ⚠️ 评委若用 Linux 浏览器打开则可见 | **已修**：补 `'PingFang SC','Noto Sans CJK SC','WenQuanYi Micro Hei'` 回退链 |
| 5 | `services/translate.py` PDF 写回用 `Font("china-s")` | 正常 | 正常（PyMuPDF 内置，实测 = `Droid Sans Fallback Regular`） | ✅ 无风险 | 无需改，也**不需要装系统字体** |
| 6 | `services/writing.py:1496` `shutil.which("dot")` | 未装 graphviz → 走零依赖 SVG 兜底 | Dockerfile 已 `apt-get install graphviz` → 走真实 `dot -Tsvg` | ✅ 无风险 | 无需改 |
| 7 | `services/writing.py:1445` `_dot_via_matplotlib` | 不可用（matplotlib 已被有意移出 requirements） | 同样不可用 | ✅ 无风险 | **别把 matplotlib 加回 requirements**：它的默认字体无 CJK且不回退，会把中文标签渲染成方框 |
| 8 | `config.py:22` `os.environ.get("APPDATA")` | 桌面态读 `%APPDATA%` | 仅 `FROZEN` 分支命中，Linux 容器 `FROZEN=False` → 不执行 | ✅ 无风险 | 无需改 |
| 9 | `app.py:15` `LOG_DIR = "logs"`（相对 CWD） | 相对 `启动科研助手.bat` 的工作目录 | 相对容器 `WORKDIR /app`；Dockerfile 已 `mkdir -p /app/logs && chown app:app` | ✅ 无风险（写失败也只是静默丢日志） | 无需改 |
| 10 | `desktop/` 下 `ctypes.windll.user32.MessageBoxW`、`os.startfile`、`subprocess.STARTUPINFO`、`CREATE_NO_WINDOW` | 正常 | **会炸**（Linux 无 user32 / startfile） | ✅ 运行时不加载 | 桌面壳专属，`app.py` 不 import `desktop.*`，不进 Linux 路径 |
| 11 | `scripts/` 下 15 处硬编码 `C:\...` / `C:/...` | 正常 | 路径不存在会失败 | ✅ 开发脚本，不进容器业务路径 | 不修（随仓库走，评委不会跑） |
| 12 | `pandas` 读 `demo_data/data.csv`（带 UTF-8 BOM） | 正常，列名无 BOM | 同 | ✅ 无风险 | 实测 `df.columns[0]` 不含 `\ufeff`，pandas 已处理 |
| 13 | 全部 41 个运行时 `.py` 无 `# -*- coding:` 声明 | Python 3 默认 UTF-8，正常 | 同 | ✅ 无风险 | 实测 41/41 均为合法 UTF-8 |
| 14 | `requirements.txt` 原生扩展（见 §5） | 有 win_amd64 wheel | x86_64 manylinux 有 wheel；**aarch64 未实测** | ⚠️ 仅 ARM 宿主 | 未改，交决策 |

---

## 2. 路径写法扫描（绝对数量）

| 检查项 | 运行时模块命中数 | scripts/+desktop/ 命中数 | 判定 |
|--------|------------------|--------------------------|------|
| `os.path.join(...)` | **16** | — | 全部走 `os.path.join` → 自动适配 `os.sep`，**0 处需改** |
| 盘符路径字面量 `X:\Users` / `X:/Users` / `C:\Program Files` | **0** | **15**（全在 `scripts/`，`desktop/` 为 0） | 运行时 0 处需改 |
| 反斜杠作路径分隔符 | **0** | 仅 `desktop/paths.py` 注释里出现 | 运行时 0 处需改 |
| 路径用字符串拼接 `+ "/"` | **0** | 3（全在 `scripts/`） | 运行时 0 处需改 |
| `os.sep` / `ntpath` / `posixpath` | **0** | 0 | 无平台分支代码 |
| `os.startfile` / `CREATE_NO_WINDOW` / `STARTUPINFO` / `ctypes.windll` / `msvcrt` / `winreg` | **0** | **21**（`desktop/` 20 + `scripts/` 1） | 运行时 0 处需改 |

> ⚠️ 假阳性排除说明：全局搜 `\\` 会命中大量**非路径**转义序列——
> `services/writing.py:1337-1352` 的 LaTeX（`\documentclass` / `\usepackage` / `\usetikzlibrary`）、
> `services/library.py:829` 的 `split("\n\n")`、`services/translate.py` 里的 JS 字符串（`\u21a9` 等）。
> 这些都不是路径，逐条核过，不计入上表。

根目录定义只有一处 `config.py:13 ROOT = Path(__file__).resolve().parent`，全程用 `pathlib` 的 `/` 运算符，
不存在「Windows 上拼出来能跑、Linux 上拼出来错位」的可能。

---

## 3. 文件编码

| 检查项 | 结果 |
|--------|------|
| 运行时模块里**缺 `encoding=` 的** `open(` 调用 | **12** |
| ├─ 其中二进制模式（`"rb"` / `"wb"`） | **6**（`services/extern.py:108`、`services/library.py:546`、`services/parsing.py:763`、`ui/page_reading.py:262`、`ui/page_reading.py:1429`、`tools/definitions.py:211`）—— 二进制模式下 `encoding` 无意义 |
| ├─ 其中第三方库的 `.open()`（`fitz.open` / `Image.open`） | **6**（`services/parsing.py:51/52/709`、`services/translate.py:705`、`services/writing.py:751/1366`）—— 不是内建 `open` |
| └─ **文本模式 `open()` 缺 `encoding=`** | **0** ✅ |
| `Path.read_text()` / `write_text()` | 运行时 8 处，**8/8 均带 `encoding="utf-8"`** ✅ |
| 运行时 41 个 `.py` 是否都是合法 UTF-8 | **41/41 是**（逐个 `bytes.decode('utf-8')` 实测） |
| 缺失 `# -*- coding:` 声明 | 41 个全部没有 —— 但 Python 3 默认 UTF-8，**两个平台行为一致**，无需补 |
| `demo_data/data.csv` 编码 | UTF-8 **带 BOM**（`\xef\xbb\xbf`）；实测 `pd.read_csv` 后首列名为 `'model'`，**无 BOM 残留** ✅ |
| 中文文件名处理 | 上传/入库的文件名全程 `str`，`os.path.join` 拼接；`sys.getfilesystemencoding()` 实测 `utf-8` |

**唯一需要留意的编码环境变量**（见 §6）：容器里若 `LANG`/`LC_ALL` 未设，Python 3.11 靠 PEP 538 的
locale coercion 兜底到 `C.UTF-8`，一般没事；但显式设 `PYTHONUTF8=1 LANG=C.UTF-8` 是零成本加固。

---

## 4. 临时文件

运行时模块共 **7 处** `tempfile` 调用点，逐个核对清理情况：

| # | 位置 | 调用 | 改动前清理情况 | 状态 |
|---|------|------|----------------|------|
| 1 | `services/writing.py:1406` | `mkdtemp(prefix="wcopilot_tikz_")` | `finally: shutil.rmtree` ✅ | 无需改 |
| 2 | `tools/definitions.py:207` | `NamedTemporaryFile(delete=False, suffix=".pdf")` | `os.remove` 在**正常路径上**，异常时不删；且 `open(...)` 句柄未关 | **已修** |
| 3 | `tools/definitions.py:328` | `mkdtemp(prefix="ai4s_code_")` | **完全没清理** | **已修** |
| 4 | `ui/page_reading.py:223` | `tempfile.gettempdir()`（清理扫描用） | 按前缀 `fig_` + TTL 过滤后 `rmtree` ✅ | 无需改 |
| 5 | `ui/page_reading.py:312` | `mkdtemp(prefix="fig_")` | `finally: shutil.rmtree` ✅ | 无需改 |
| 6 | `ui/page_reading.py:1071` | `NamedTemporaryFile(delete=False, suffix=".pdf")` | 只 `close()` 不 `remove()`，且**每次 rerun 都新建** | **已修** |
| 7 | `ui/page_reading.py:1424` | `mkdtemp(prefix="fig_inplace_")` | `finally: shutil.rmtree` ✅ | 无需改 |

**改动前本机临时目录实测残留**（Windows `%TEMP%`，Linux 对应 `/tmp`）：

```
tmp*.pdf          143 个 / 1142.9 MB      ← 主要来自第 6 项
ai4s_code_*        19 个                  ← 第 3 项
fig_*               0 个                  ← 第 5、7 项清理正常
wcopilot_tikz_*     0 个                  ← 第 1 项清理正常
```

即：**有 `try/finally` 的 3 处残留为 0，没有的 3 处合计 162 个 / 1.14 GB**——清理逻辑的有无与残留量完全对应。

硬编码 `/tmp`、`C:\temp`、`%TEMP%`、`Path("tmp")`：**0 处**（全文仅 `ui/page_reading.py:223` 用
`tempfile.gettempdir()` 取系统临时目录，这是正确写法）。

---

## 5. `requirements.txt` 兼容性分析（只读，未改文件）

当前内容（12 个直接依赖）+ 本机实际解析出的版本与 wheel tag：

| 包 | requirements 约束 | 本机实测版本 | 本机 wheel tag | Linux 风险 |
|----|-------------------|-------------|----------------|-----------|
| `streamlit` | `>=1.63,<1.64` | 1.63.0 | `py3-none-any` | 纯 Python。上界 `<1.64` 极窄：将来 1.64 发布后仍锁在 1.63，属「主动保守」，不是 Linux 风险 |
| `PyMuPDF` | `>=1.24.3,<2.0` | 1.28.2 | `cp310-abi3-win_amd64` | 原生扩展。x86_64 manylinux 有 wheel；**aarch64 未实测** |
| `chromadb` | `>=0.5,<2.0` | 1.5.9 | `cp39-abi3-win_amd64` | 传递依赖最重（onnxruntime / grpcio / tokenizers / pyarrow / opentelemetry）。**ARM 上最大风险点** |
| `openai` | `>=1.40,<4.0` | 3.13.0 | `py3-none-any` | 纯 Python ✅ |
| `pandas` | `>=2.0,<4.0` | 3.0.5 | `cp311-cp311-win_amd64` | 原生扩展，主流平台有 wheel |
| `numpy` | `>=1.26,<3.0` | 2.4.6 | `cp311-cp311-win_amd64` | 同上 |
| `plotly` | `>=5.20`（无上界） | 7.0.0 | `py3-none-any` | 纯 Python。无上界 → 将来大版本可能引入不兼容 |
| `networkx` | `>=3.2`（无上界） | 3.6.1 | `py3-none-any` | 纯 Python ✅ |
| `arxiv` | `>=2.1`（无上界） | 4.0.1 | `py3-none-any` | 纯 Python。2.x→4.x 跨了两个大版本；实测代码只用到 `SortCriterion / Search / Client`，4.x 均仍存在 ✅ |
| `pillow` | `>=10`（无上界） | 12.3.0 | `cp311-cp311-win_amd64` | 原生扩展，主流平台有 wheel |
| `python-dotenv` | `>=1.0` | 1.2.3 | `py3-none-any` | ✅ |
| `rank-bm25` | `>=0.2.2` | 0.2.2 | `py3-none-any` | ✅ |
| `psutil` | `>=5.9` | 7.2.2 | `cp37-abi3-win_amd64` | 原生扩展；冷门平台（musl / 小众 arch）可能回落 sdist 需要 gcc |

### 建议放宽 / 替换清单（**我没有改文件，交 team-lead 决策**）

| 优先级 | 建议 | 理由 | 风险 |
|--------|------|------|------|
| 🟢 低 | `plotly>=5.20` → `plotly>=5.20,<8.0` | 与 `streamlit` 同风格：给个「下一个大版本」上界，避免将来解析出未测过的组合 | 极低，纯 Python |
| 🟢 低 | `arxiv>=2.1` → `arxiv>=2.1,<5.0` | 同上，且已从 2.x 跨到 4.x，锁个上界防止 5.x 静默破坏 | 极低 |
| 🟢 低 | `pillow>=10` → `pillow>=10,<13.0` | 同上 | 极低 |
| 🟡 中 | `psutil>=5.9` → 把 `psutil` 标为可选，或在 Dockerfile 预留 gcc | 唯一在冷门平台可能回落 sdist 的包。不过 **cp311 manylinux x86_64 有 wheel**（Dockerfile 第 55-57 行已核过），仅在 musl(Alpine)/ARM 时才有问题 | 改 Dockerfile 属 deploy-finish 职权 |
| 🔴 高（仅 ARM） | 若交付环境可能是 **ARM/aarch64**（Apple M 系列 Docker、ARM 云主机）：需实测 `chromadb` 整条链。`onnxruntime` 是链上最容易缺 aarch64 wheel 的一环 | 一旦缺 wheel，`pip install -r requirements.txt` 会失败或去编译 sdist → 在 slim 镜像里没有 gcc，直接构建失败 | **本机无 Linux/ARM，未实测**。建议：交付前在目标机器上先跑一次 `pip download -r requirements.txt --no-deps -d /tmp/wheels` 验证 |
| ⛔ 不要做 | **不要把 `matplotlib` 加回 requirements.txt** | `services/writing.py:1445` `_dot_via_matplotlib` 是 `render_dot` 的第③级降级。装了 matplotlib 会让**云平台**（无 `dot`）在第③级返回服务端渲染的 PNG，而 matplotlib 默认字体 DejaVu Sans 无 CJK 且不自动回退 → DOT 的中文标签（"交互层""Agent 内核"这类）渲染成方框 □。**装了反而更差** | requirements.txt 第 12-19 行的注释已写明，别删 |

---

## 6. Docker 部署注意事项

> 以下均为**只读观察**，我没有改 `Dockerfile` / `docker-compose.yml` / `.dockerignore` / `.streamlit/config.toml`。
> 若采纳，请由 `deploy-finish` 或 team-lead 落笔。

1. **建议补两个环境变量（零成本加固）**
   ```
   ENV PYTHONUTF8=1 LANG=C.UTF-8 LC_ALL=C.UTF-8
   ```
   理由：镜像基线与 `ENV` 块（`Dockerfile:44-49`）目前**没有设任何 locale**。Python 3.11 靠 PEP 538
   的 locale coercion 兜底到 `C.UTF-8`，正常情况没事；但显式设上可以杜绝「中文文件名 / 中文路径在
   `LC_ALL=C` 下变 ASCII 编解码失败」这一类最难排查的偶发问题。
   ⚠️ **未实测**：本机无 docker，无法验证加与不加的差异。

2. **不需要装中文字体包**。PDF 侧用 PyMuPDF 内置 `china-s`；浏览器侧的中文由**客户端**字体决定，
   容器里装 `fonts-wqy-microhei` 对客户端渲染**没有作用**（已改为 CSS 回退链方案，见 §7）。

3. **`graphviz` 必须保留**（`Dockerfile:81-83`）。它是 `render_dot` 第②级 `dot -Tsvg` 的来源，
   没有它容器会退到第④级「零依赖 DOT→SVG」——能出图但不如图好看。

4. **`USER app` + `/tmp`**：容器以 uid 10001 运行，`/tmp` 是世界可写（1777），`tempfile` 正常可用 ✅。
   但 `/tmp` 里的堆积**直到容器重启才消失**——这正是 §4 那 3 处临时文件泄漏必须在代码层修掉的原因
   （已修）。

5. **静态资源不能从 `.dockerignore` 里漏掉**：`lib/vis-9.1.2/`（`services/graph.py:528-529` 读）、
   `static/`（mermaid.min.js）、`.streamlit/config.toml`、`scripts/`（`ui/page_reading.py:81` 从
   `scripts._memory_probe` 导入）。丢掉不崩，但会静默掉功能。

6. **Linux 宿主用 bind mount 时**：`storage/` 与 `logs/` 必须 `chown 10001:10001`，否则
   `config.py:42` 的 `mkdir` 抛 `PermissionError`（`exist_ok=True` 吞不掉 EACCES），
   发生在 **import 期**，被 `app.py:34` 的兜底 `except` 捕获。
   ✅ **已修复（本轮队友）**：`app.py:43` 现在按 `isinstance(e, OSError)` 分流，
   权限类错误提示「storage/ 目录不可写（容器挂载属主不对）」，并给出
   `chown -R 10001:10001 ./storage ./logs`；非 OSError（如 ModuleNotFoundError）仍走
   「依赖未安装或版本不匹配」分支。不再是误导文案。
   `deploy-finish` 本轮另外把 compose 改成命名卷 `app_storage:/app/storage`，从触发条件上绕开它。

---

## 7. 字体

### 显式指定字体名的位置（运行时模块，共 11 行 CSS，已全部补回退链）

| 位置 | 改前 | 改后 |
|------|------|------|
| `services/translate.py:309` | `'Microsoft YaHei',sans-serif` | `'Microsoft YaHei','PingFang SC','Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif` |
| `services/translate.py:498` | 同上（`font:` 简写） | 同上 |
| `services/translate.py:536` | `'Microsoft YaHei','Segoe UI',sans-serif` | 补 4 个回退 |
| `services/translate.py:772` | `"Microsoft YaHei",sans-serif` | 补 4 个回退（双引号版） |
| `services/translate.py:803` | `'Microsoft YaHei',sans-serif` | 补 4 个回退 |
| `services/translate.py:805` | 同上（`font:` 简写） | 补 4 个回退 |
| `services/translate.py:1189` | `'Microsoft YaHei','Segoe UI',sans-serif` | 补 4 个回退 |
| `services/translate.py:1191` | `'Microsoft YaHei',sans-serif` | 补 4 个回退 |
| `services/wcopilot_dotrender.py:259` | `Segoe UI,Microsoft YaHei,sans-serif` | 补 4 个回退（SVG `font-family`） |
| `ui/common.py:121` | `'Segoe UI','Microsoft YaHei',sans-serif` | 补 4 个回退 |
| `ui/page_misc.py:21` | `'Segoe UI','Microsoft YaHei',sans-serif` | 补 4 个回退 |

**保留原字体名在链首**：Windows/macOS 客户端行为完全不变，只在 Linux 客户端上多出来的回退才生效。

未改的 2 类（有意为之）：
- `services/translate.py:506` `'Times New Roman',Times,serif` —— PDF 正文是**英文/拉丁**文本，衬线字体各平台都有 ✅
- `services/graph.py:560` `face:'sans-serif'` —— vis-network 画布标签是论文标题（英文），通用 sans 足够 ✅

非运行时（未改）：`version_history/app_streamlit_v1.py:243/273/794`（历史快照，不被 `app.py` 引用）、
`scripts/make_tech_pdf.py:41`（Windows 上用 Edge/Chrome 生成技术文档 PDF，只在本机跑）。

### PDF 侧：不需要任何系统字体

`services/translate.py:_cjk_font()` 用 `pymupdf.Font("china-s")`，实测：

```
Font('china-s') = Font('Droid Sans Fallback Regular')
text_length('中文', 10) = 20.0
```

这是 PyMuPDF **内置**字体（随 wheel 打包，不是系统字体），Linux/Windows/macOS 行为一致。
→ **容器里不需要 `apt-get install fonts-wqy-microhei fonts-noto-cjk`**。

### 评委/开发者在 Linux **客户端**上想让中文更好看（可选，非必需）

```bash
# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y fonts-noto-cjk fonts-wqy-microhei
fc-cache -fv

# RHEL / CentOS / Fedora
sudo dnf install -y google-noto-sans-cjk-fonts wqy-microhei-fonts
fc-cache -fv

# Arch
sudo pacman -S noto-fonts-cjk wqy-microhei
fc-cache -fv

# 验证（应能看到 Noto Sans CJK / WenQuanYi 条目）
fc-list :lang=zh | head
```

⚠️ 这是**客户端机器**的事，不是容器镜像的事。代码层的修复（CSS 回退链）已经让「没装字体的 Linux 浏览器」
在装了上述任一字体时自动用上；若一个都没装，浏览器会用它自己的 CJK 兜底，仍不会是方框。

---

## 8. 「在干净 Linux 上从零跑起来」最小验证步骤

> ⚠️ **本机无 Linux，以下步骤未实测**。每一步都写清了「期望看到什么」，便于在目标机器上一眼判定。

### 8.0 前置

```bash
python3 --version          # 期望 Python 3.11.x（Dockerfile 基线 cp311）
git --version
```

### 8.1 编码基线（先确认，后面出问题好排查）

```bash
python3 -c "import sys,locale;print(sys.getfilesystemencoding(), locale.getpreferredencoding(False))"
# 期望：utf-8 utf-8
# 若出现 ascii/ANSI_X3.4-1968 → 先 export LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUTF8=1 再继续
```

### 8.2 装依赖

```bash
cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 期望：全部走 wheel（日志里是 "Downloading xxx.whl"），不应出现 "Building wheel for ..." 卡在编译
# 若卡在 onnxruntime / grpcio / pyarrow 的编译 → 说明平台缺 wheel，跳到 §5 的 ARM 风险项
```

### 8.3 静态资源完整性（丢了不报错，只静默掉功能）

```bash
ls -l lib/vis-9.1.2/vis-network.min.js lib/vis-9.1.2/vis-network.css
ls -l demo_data/*.pdf demo_data/data.csv
ls -l static/
ls -l .streamlit/config.toml
ls -l scripts/_memory_probe.py
# 期望：5 行全部存在（路径大小写必须与上面一致 —— Linux 文件系统大小写敏感）
```

### 8.4 可写目录与属主（Docker 内 uid 10001）

```bash
mkdir -p storage logs
# 非 root 运行前：
sudo chown -R 10001:10001 storage logs
# Docker 内校验：
#   docker compose exec ai4s-agent ls -ld /app/storage /app/logs
#   期望：属主是 app，不是 root
```

> 本节覆盖**裸机 Linux** 场景；Docker / docker compose 场景的对应校验见
> `docs/部署后自检清单.md §1.1`（两者结论一致：属主必须是 uid 10001，不能是 root）。
> 属主不对时的**表现**见 §6 第 6 条（本轮已把误导文案修掉，现在会直接提示「storage/ 目录不可写」）。

### 8.5 启服务

```bash
streamlit run app.py --server.address=0.0.0.0 --server.port=8501
#（Docker 已用命名卷；裸机可以直接跑到这一步）
```

### 8.6 四条门禁（与 Windows 同一套，期望同一结果）

```bash
PY=python3
$PY -m compileall -q core services tools ui app.py config.py   # 期望 exit 0
$PY -m unittest discover -s tests                               # 期望 Ran 230 tests ... OK
$PY scripts/_smoke_ui.py                                        # 期望 exit 0 + 9 页 0 异常
$PY scripts/_smoke_read.py                                      # 期望 PASS 62 / FAIL 0
```

> ⚠️ `_smoke_ui.py` 与 `_smoke_read.py` **必须串行**，不要并行跑
> （后者有依赖 sleep 退避时长的断言，并行会偶发假 FAIL）。

**⚠️ 勘误（本条我最初写错了，由 `deploy-finish` 更正）**

我最初写「四门禁在容器内都跑得了」，依据是「`.dockerignore` 只排了 `storage/` 和 `logs/`」。
**这个依据漏了一条**：`.dockerignore` 原本（HEAD 版第 75 行）还排了 **`tests/`** —— 也就是说
在 Round 13 之前，第 2 条 `python -m unittest discover -s tests` 在容器内会直接
`no tests to run`，四门禁实际只能跑 3 条。

现状：`deploy-finish` 本轮已把 `tests/` 改为不排除（现 `.dockerignore` 第 93-98 行附了理由注释，
代价 44KB），所以**现在四条门禁在容器内确实全部可跑**。
不想付这 44KB 就把那行改回 `tests/`，代价是门禁第 2 条只能回宿主机跑。

另外两点（`deploy-finish` 独立核实后写进他的清单，我引用一下，避免重复劳动）：
- `scripts/_smoke_ui.py:22` 用的是 `streamlit.testing.v1.AppTest`（**进程内渲染**），
  不需要浏览器/playwright；容器内无隐藏依赖。
- `scripts/_smoke_read.py:384` 写死 `available = False`（显式模拟未填 Key），**不需要真 Key**。

> 📌 `PASS 62` / `Ran 230 tests` 是**本机 Windows 实测数字，容器内未实测（本机无 docker）**。
> 第一次在容器里跑时，请先看**有没有 FAIL 项**，不要只比对总数。

### 8.7 浏览器端三看（这是 Linux 与 Windows 差异真正会出现的地方）

1. **首页/文献库**：中文标题、按钮、提示语不是方框 □
2. **版式阅读页**：划选一段英文 → 「划词翻译」气泡里的中文正常显示（对应 `services/translate.py:309/498`）
3. **写作辅助页**：让 Agent 生成一张 Graphviz 拓扑图 → 节点中文标签不是方框
   （容器里有 `dot` → 走 `dot -Tsvg`，浏览器渲染；**不要把 matplotlib 装回来**）

### 8.8 临时目录不涨（验证本轮 §4 的修复）

```bash
export TMPDIR=${TMPDIR:-/tmp}
before=$(ls $TMPDIR | wc -l)
# 在页面上：上传一个 PDF 到阅读页 → 来回切换页码/点几次按钮 → 触发 Agent 跑一次 Python 代码
after=$(ls $TMPDIR | wc -l)
echo "before=$before after=$after"
# 期望：after - before ≤ 1~2（改动前：每 rerun +1，跑一次代码 +1）
ls $TMPDIR | grep -c '^ai4s_code_'   # 期望：0（改动前：跑几次就几个）
```

---

## 9. 遗留风险

| 风险 | 影响 | 谁该跟进 |
|------|------|---------|
| ARM/aarch64 上 `chromadb` 依赖链（尤其 onnxruntime）可能无 wheel | 构建失败（slim 镜像无 gcc） | team-lead 决策：是否要求在 x86_64 上交付；或预留 build-essential |
| 阅读页上传的临时 PDF 在**会话结束后**仍不删除（本轮只把「每 rerun 一份」降为「每上传一份」） | 单次会话内不再堆积；跨会话仍会累积，但速率已降一个量级 | 若要彻底清零，需要给 `storage/uploads` 加一个带 TTL 的清道夫 —— 属新增功能，本轮不做 |
| `scripts/` 下 15 处 `C:\...` 硬编码 | 评委不会跑这些脚本；但仓库里看着不专业 | 可选清理，非阻塞 |
| CSS 回退链只覆盖 Linux **客户端**；若评委用 Linux + 未装任何 CJK 字体的浏览器 | 中文由浏览器自身兜底，理论上仍有极小概率不理想 | 已在 §7 给出客户端装字体命令 |
| 全部「Linux 表现」结论基于静态核对 + Windows 实测推导，**未在真 Linux 上跑过** | — | 建议在目标机器按 §8 跑一遍 |

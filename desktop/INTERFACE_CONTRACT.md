# 桌面版接口契约（INTERFACE CONTRACT）

> 所有 worker 必须严格遵守本契约。契约冲突时停下报告 Organizer，不得自行更改常量。
> 业务代码（ui/ services/ core/ tools/ 的功能逻辑）一行不重写，只做桌面适配的最小注入。

## 1. 环境与解释器（铁律）

- 唯一允许的 Python：`C:\Users\Administrator\anaconda3\envs\ai_research\python.exe`（3.11.16）
  - PowerShell 调用：`& "C:\Users\Administrator\anaconda3\envs\ai_research\python.exe" ...`
  - 禁止使用 PATH 里的默认 `python`（Doubao sandbox 3.13，无业务依赖）
- pip：`& "C:\...\python.exe" -m pip install ...`（包基本已装全）
- 已核验：streamlit 1.63.0、pywebview 5.4（edgechromium 后端可 import）、pyinstaller 6.22.3、
  pyinstaller-hooks-contrib 2026.7、pillow 12.3.0、psutil 7.2.2、streamlit-desktop-app 0.3.4
- WebView2 Runtime 已装：154.0.4258.62（注册表 HKLM WOW6432Node EdgeUpdate Clients）
- git：`C:\Users\Administrator\AppData\Local\GitHubDesktop\app-3.5.8\resources\app\git\cmd\git.exe`
  - main 分支，HEAD=177cac5，backup-20261010 备份分支存在
  - 每个工作包完成必须 commit（conventional commits，如 `feat(desktop): ...`）
  - 禁止 push / reset --hard / force；提交时设置 `$env:GIT_TERMINAL_PROMPT='0'` 等非交互环境变量
- 每个 .py 文件改完必须 `py_compile` 通过
- Shell 只有 PowerShell 5.1（Bash 工具禁用），不能用 && / heredoc 等 Unix 语法

## 2. 全局常量（所有模块共用，禁止改名改值）

| 项 | 值 |
|---|---|
| 桌面模式环境变量 | `AI4S_DESKTOP=1`（launcher 启动 streamlit 子进程时注入） |
| 应用英文名（exe/spec/目录名） | `AI4SResearchAgent` |
| 中文窗口标题 | `AI4S 科研助手` |
| 应用版本 | `v1.0` |
| 用户数据根目录 | `%APPDATA%\AI4SResearchAgent\` |
| 数据子目录 | `storage\`（含 uploads\、chroma\、pdf\）、`logs\`、`config\` |
| Streamlit 日志 | `%APPDATA%\AI4SResearchAgent\logs\streamlit.log` |
| LLM 失败日志 | 保持业务代码现状：`storage\llm_failures.log`（即 `%APPDATA%\...\storage\llm_failures.log`，由 config.STORAGE_DIR 自动重定向，不改 core/llm.py） |
| launcher 自身日志 | `%APPDATA%\AI4SResearchAgent\logs\launcher.log` |
| 窗口默认尺寸 | 1440 x 900 |
| 窗口最小尺寸 | 1200 x 720 |
| 图标源/引用路径 | `assets\icon.ico`（开发态相对项目根；打包后 spec 把 assets 收进 bundle，窗口与 exe 都引用它） |
| 端口 | 动态空闲端口（find_free_port），禁止写死 8501 |
| 单实例锁 | `%APPDATA%\AI4SResearchAgent\app.lock`（msvcrt 不可用于？——用 `portalocker` 未装，故用 socket bind 127.0.0.1 固定锁端口 + lock 文件双保险，见 §5） |

## 3. 路径适配契约（只改 config.py 一处）

现状（不得破坏的引用关系）：
- `config.py`：`ROOT = Path(__file__).resolve().parent`；STORAGE_DIR/UPLOAD_DIR/CHROMA_DIR = ROOT/storage/...；PDF_DIR = ROOT/pdf；DEMO_DIR = ROOT/demo_data
- `services/graph.py:208-210`：`from config import ROOT` 后读 `ROOT/lib/vis-9.1.2/*`（只读静态资源）
- `core/llm.py:26-27`：`from config import STORAGE_DIR` 写 llm_failures.log
- `ui/common.py:131-132`：遍历 UPLOAD_DIR、PDF_DIR
- `.env`：开发态从 ROOT/.env 读；桌面态从 DATA_ROOT/.env 读（不存在则跳过），launcher 不得注入假 Key

改造后 config.py 必须导出的常量（名字不变，值按模式切换）：

```python
FROZEN = getattr(sys, "frozen", False)
DESKTOP = FROZEN or os.environ.get("AI4S_DESKTOP") == "1"

if FROZEN:
    RESOURCE_ROOT = Path(sys._MEIPASS)          # 只读：demo_data、lib、.streamlit、assets、app.py
    DATA_ROOT = Path(os.environ["APPDATA"]) / "AI4SResearchAgent"   # 可写
    ROOT = RESOURCE_ROOT                         # 保证 ROOT/lib 静态资源可读
else:
    RESOURCE_ROOT = ROOT                         # 开发态两者都是项目根
    DATA_ROOT = ROOT
```

路径归属表：

| 常量 | 开发态 | 打包冻结态 | 读/写 |
|---|---|---|---|
| ROOT | 项目根 | RESOURCE_ROOT（sys._MEIPASS） | 只读引用 |
| STORAGE_DIR | ROOT/storage | DATA_ROOT/storage | 写 |
| UPLOAD_DIR | STORAGE_DIR/uploads | 同左（DATA_ROOT 下） | 写 |
| CHROMA_DIR | STORAGE_DIR/chroma | 同左 | 写 |
| PDF_DIR | ROOT/pdf | DATA_ROOT/storage/pdf（保持在 storage 内，业务代码 import 名不变） | 写 |
| DEMO_DIR | ROOT/demo_data | RESOURCE_ROOT/demo_data | 只读 |
| lib（graph.py 经 ROOT 访问） | ROOT/lib | RESOURCE_ROOT/lib | 只读 |
| .env | ROOT/.env | DATA_ROOT/.env（先 DATA_ROOT 再 fallback RESOURCE_ROOT） | 读 |

要求：
- import config 时自动 mkdir 全部 DATA_ROOT 子目录（storage、storage/uploads、storage/chroma、storage/pdf、logs、config），桌面态把 logs 目录也建好
- 冻结态启动时若 DATA_ROOT/.env 不存在，不报错；可从 RESOURCE_ROOT/.env.example 复制一份为 DATA_ROOT/.env（可选，做了更好）
- 新增一个 `DESKTOP_MODE: bool` 布尔常量和 `LOGS_DIR` 常量（开发态 = ROOT/storage 兼容旧日志位置？——不，开发态 LOGS_DIR=ROOT/storage；桌面态=DATA_ROOT/logs），供 launcher 与 ui 使用
- 除 config.py 外，业务文件里出现的 storage 路径一律不动；如发现业务代码硬编码 `"storage"` 相对路径（如 core/llm.py 的 except 分支），在交付说明里列出但不改

## 4. Streamlit 子进程启动契约（desktop/launcher.py）

- 用 `subprocess.Popen`（**禁止 multiprocessing**，PyInstaller 冻结后易炸）
- 冻结态命令行：`[sys.executable, ...]` 不可行（exe 不是 python）；冻结态直接 `sys.executable`（即 AI4SResearchAgent.exe）配合入口 re-exec 子进程模式：
  - 约定：exe 带参数 `--st-server <port> --st-cwd <resource_root>` 时，入口 `desktop/__main__.py` 不走窗口，转而 `from streamlit.web import cli as stcli; sys.argv=["streamlit","run",<RESOURCE_ROOT/app.py>, ...]; stcli.main()`
  - 开发态命令行：`[sys.executable, "-m", "streamlit", "run", str(RESOURCE_ROOT/"app.py"), ...]`
- streamlit 参数（命令行显式传，不依赖 config.toml  alone）：
  `--server.port=<port> --server.address=127.0.0.1 --server.headless=true
   --server.runOnSave=false --global.developmentMode=false --browser.gatherUsageStats=false
   --browser.openInExternalBrowser=false --client.toolbarMode=minimal`
  - Round 16 追加（launcher.py 的 `STREAMLIT_CLI_ARGS`，与 Dockerfile CMD / start_app.sh 口径对齐）：
    `--server.enableStaticServing=true`（写作页 mermaid 本地加载，不依赖热更新包内 config.toml；
    该项默认 false，缺失会静默退化到 CDN）、`--server.maxUploadSize=100`（与应用层
    `MAX_UPLOAD_MB=100` 对齐，上传层即拦，避免超限文件整份读入内存后才被拒）。
  - 注：streamlit 1.63 已移除 `--browser.openInExternalBrowser`，命令行不再传它。
- 子进程 cwd：冻结态 = RESOURCE_ROOT（让 .streamlit/config.toml 被 streamlit 自动发现）；开发态 = 项目根
- 子进程环境：继承父进程并加 `AI4S_DESKTOP=1`；冻结态加 `STREAMLIT_GLOBAL_DEVELOPMENT_MODE=false`
- Windows 标志：`creationflags=subprocess.CREATE_NO_WINDOW`（0x08000000），`startupinfo` 隐藏窗口；stdin=DEVNULL，stdout/stderr 重定向到 LOGS_DIR/streamlit.log（utf-8，追加）
- 健康检查：GET `http://127.0.0.1:<port>/_stcore/health`，轮询间隔 0.5s，超时 60s（首次 embedding 下载可能慢），超时则读日志尾部写错误对话框并退出码非 0
- 退出清理：窗口关闭后用 psutil 杀整棵进程树（`psutil.Process(pid).children(recursive=True)` 逐个 terminate，等 5s 后 kill），兜底 `taskkill /PID <pid> /T /F`；同时清理 streamlit 可能拉起的孤儿 python
- 单实例：
  1. 尝试 `socket.socket().bind(("127.0.0.1", <固定锁端口 47851>))`，bind 失败说明已有实例 → 读取 `%APPDATA%/AI4SResearchAgent/app.lock` 里的 HTTP 端口，健康检查通过则直接退出（聚焦已有窗口在 WebView2 下难做，允许仅退出新实例并弹提示框）；
  2. bind 成功后把当前 streamlit 端口写入 app.lock（JSON：`{"port": n, "pid": n}`），退出时删除；
  3. 锁端口固定 47851，streamlit 服务端口仍动态。

## 5. 窗口契约（desktop/window.py）

- `webview.create_window(title="AI4S 科研助手", url=f"http://127.0.0.1:{port}", width=1440, height=900, min_size=(1200,720), icon=<icon 路径>)`
- GUI 后端：Windows 固定 edgechromium（WebView2），`webview.start(gui="edgechromium")`
- 图标解析：函数 `resolve_icon()` —— 冻结态 `RESOURCE_ROOT/assets/icon.ico`；开发态项目根 `assets/icon.ico`；找不到则传 None（不得崩）
- 菜单（pywebview Menu）：
  - 文件：打开数据目录（os.startfile DATA_ROOT）、查看日志（打开 logs/streamlit.log）、退出
  - 帮助：项目主页（打开 https://github.com/ 占位，见 about.py）、关于
- “关于”用 `webview.windows[0].create_message_dialog`，文案常量全部从 `desktop/about.py` import（Worker 4 提供该文件；Worker 1 若先开发可自建占位，Worker 4 覆盖时双方只允许改 about.py）
- 关闭窗口即退出（不做任务确认弹窗）

## 6. Streamlit 桌面适配契约（Worker 2）

- `.streamlit/config.toml` 在保留现有 [theme]（#1f5fae 蓝色学术风）与 `[browser] gatherUsageStats=false` 基础上追加：
  ```toml
  [server]
  headless = true
  address = "127.0.0.1"
  baseUrlPath = ""
  enableCORS = false
  enableXsrfProtection = false
  runOnSave = false

  [browser]
  gatherUsageStats = false
  openInExternalBrowser = false

  [client]
  showErrorDetails = true
  toolbarMode = "minimal"
  ```
- 新增 `ui/desktop_chrome.py`，导出 `apply()`：
  - 仅当 `os.environ.get("AI4S_DESKTOP")=="1"` 或 `config.DESKTOP_MODE` 时注入，开发浏览器模式零影响
  - `st.markdown("""<style>...</style>""", unsafe_allow_html=True)` 隐藏：`#MainMenu`、`footer`、`header[data-testid="stHeader"]`、`[data-testid="stToolbar"]`、`[data-testid="stStatusWidget"]`、Deploy 按钮、`.stDeployButton`；收紧主区 padding
- `app.py` 仅加 2 行：`from ui import desktop_chrome` + 在 set_page_config 之后立刻 `desktop_chrome.apply()`；其余不动
- 首次启动无 API Key 提示：在 `ui/sidebar.py` 顶部（st.title 之后、接入方式之前）加桌面态专属醒目 `st.warning` 卡片（仅桌面态显示），文案引导在下方"接入方式"处配置，不做弹窗、不做跳转新页面
  - 注意：sidebar.py 的修改 Worker 2 与 Worker 4 都会碰 —— 见 §9 冲突划分

## 7. 打包契约（Worker 3）

- spec 文件：项目根 `AI4SResearchAgent.spec`（需提交）；模式 **onedir**（不用 onefile，启动快、好排查）
- 入口：`desktop/__main__.py`；`console=False`、`icon='assets/icon.ico'`、name=`AI4SResearchAgent`
- datas（源 → bundle 内目标目录，保持相对结构）：
  - `demo_data/` → `demo_data/`
  - `.streamlit/` → `.streamlit/`
  - `lib/` → `lib/`
  - `assets/icon.ico`（及 icon.png）→ `assets/`
  - `app.py` → `.`（根）
  - 业务包 ui/services/core/tools 以源码形式（*.py）整体收进 datas 或作为模块 collect —— 推荐把它们当 hiddenimports 正常编译（它们是纯 Python 包，有 __init__.py），app.py 作为 data 放入根，与 streamlit-desktop-app build.py 做法一致
  - 不收：storage/、pdf/、.env、version_history/、tests/、docs/、scripts/、__pycache__、.git
- collect_all：streamlit、chromadb、pywebview、plotly；collect_data_files 按需补 altair、pyarrow（chromadb 依赖）、streamlit 的 static 资产
- hiddenimports 重点：fitz(pymupdf)、webview.platforms.edgechromium、clr（pythonnet，pywebview Win 后端）、rank_bm25、arxiv、networkx、psutil、PIL、dotenv、openai、pandas、sklearn（如 sandbox 用）、业务包 ui/services/core/tools 全部 collect_submodules
- excludes：tkinter、PyQt5、PySide2、PySide6、matplotlib（注意 core/sandbox.py 白名单字符串里提到 matplotlib，但未实际 import；打包机上若 matplotlib 已装且被 chromadb/plotly 间接拉入则不强排，以实测不报错为准）、notebook、IPython
- `build_desktop.bat`：用 anaconda env 的 pyinstaller，先清 build/dist，结束 pause
- 必须实际跑完打包，产出 `dist/AI4SResearchAgent/AI4SResearchAgent.exe`
- .gitignore 追加：`build/`、`dist/`、`*.spec.bak`（spec 本身提交）

## 8. 资产契约（Worker 4）

- `assets/make_icon.py`：Pillow 脚本，可重复执行；设计 = #1f5fae 圆角方形底 + 白色简洁图形（学术帽优先；画不好就纯色底白色粗体字母 "A"，简洁 > 复杂丑陋）
- 产物：`assets/icon.png`（1024x1024）、`assets/icon.ico`（内嵌 16/32/48/64/128/256）
- `desktop/about.py`：**由 Worker 1 自己创建**（纯字符串常量 APP_NAME、APP_VERSION="v1.0"、TEAM、HOMEPAGE_URL、LICENSE、ABOUT_TEXT），Worker 4 不进入 desktop/
- `installer.iss`（Inno Setup，完整可用但不要求实际编译）：装到 {autopf}\AI4SResearchAgent、开始菜单+桌面快捷方式、卸载器，源 = dist/AI4SResearchAgent/，图标 assets/icon.ico，AppId 用固定 GUID
- `启动桌面版.vbs`（新增，不动旧 vbs）：静默调 anaconda env 的 pythonw.exe `-m desktop`，cwd=项目根
- sidebar 版本标识：**由 Worker 2 完成**（sidebar.py 归 Worker 2），在 `st.caption(f"💾 内存...")` 之后加 `st.caption("桌面版 v1.0")`（开发/桌面都显示）

## 9. 文件所有权（防冲突，越界必须停下报告）

| 文件/目录 | 所有者 |
|---|---|
| desktop/__main__.py、desktop/launcher.py、desktop/window.py、desktop/paths.py（如需要）、desktop/about.py、desktop/__init__.py | Worker 1（about.py 常量也由 Worker 1 一并落地，Worker 4 不得进入 desktop/ 目录） |
| .streamlit/config.toml、ui/desktop_chrome.py、app.py、config.py、ui/sidebar.py | Worker 2（sidebar 两处改动都归 Worker 2：st.title 之后的无 Key 警告卡片 + 最底部"桌面版 v1.0"版本 caption；Worker 4 不得改 sidebar.py） |
| AI4SResearchAgent.spec、build_desktop.bat、.gitignore | Worker 3（Worker 3 可在打包报错时微调 config.py / desktop/*.py 修 hiddenimports，但必须在 commit message 写明改了什么、为什么） |
| assets/（make_icon.py、icon.png、icon.ico）、installer.iss、启动桌面版.vbs | Worker 4（Worker 4 不修改任何业务 .py；docs/desktop_guide.md、docs/desktop_test_report.md、README 桌面章节归 Worker 5） |

## 10. 测试与取证契约

- 任何 pywebview 窗口 / exe 启动测试：subprocess 后台启动 + timeout，**绝不阻塞等窗口关闭**；验证完 `taskkill /PID <pid> /T /F` 或 `Stop-Process -Id <pid> -Force`（先杀子进程树）
- 健康检查证据：保存 `/_stcore/health` 返回体与 HTTP 200、启动耗时秒数、日志最后 20 行
- 打包证据：dist/AI4SResearchAgent 目录体积（MB）、exe 文件大小、`Get-ChildItem -Recurse | Measure-Object`
- 每个工作包提交前：py_compile 全部新增/修改的 .py；git add 明确文件（不用 `git add -A` 扫入 storage 产物）

## 11. 最终交付物清单（5 路合计）

1. desktop/：__main__.py、launcher.py、window.py、about.py（+ 可选 paths.py/__init__.py）
2. .streamlit/config.toml（增强）、ui/desktop_chrome.py、app.py（+2 行）、config.py（双模式路径）、ui/sidebar.py（警告卡片+版本行）
3. AI4SResearchAgent.spec、build_desktop.bat、dist/AI4SResearchAgent/（不入库）
4. assets/make_icon.py、icon.png、icon.ico、installer.iss、启动桌面版.vbs
5. docs/desktop_guide.md、docs/desktop_test_report.md、README.md 桌面版章节、.gitignore 更新

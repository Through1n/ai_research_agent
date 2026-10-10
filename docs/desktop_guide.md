# AI4S 科研助手 · Windows 桌面版指南

本软件以「壳层 exe + 可热更新业务包」的方式分发：双击 `AI4SResearchAgent.exe` 即在原生窗口（WebView2 / Edge Chromium）中运行，无需安装 Python，也不弹出浏览器。

---

## 1. 系统要求

- Windows 10 64 位及以上（已在 Win10/Win11 验证）
- 已安装 **WebView2 Runtime**（Win11 自带；Win10 若缺失会自动提示从微软官网安装）
- 首次启动需联网（用于检查更新；无网也能正常用，只是不检查更新）
- 推荐 ≥ 8 GB 内存（向量库 + 大模型调用开销）

---

## 2. 首次启动

1. 双击 `AI4SResearchAgent.exe`；
2. 首次启动会在后台把内置业务代码复制到数据目录（约 2–5 秒），然后弹出原生窗口；
3. 在左侧「接入方式」选择模型平台并填入 API Key（无 Key 也可点「一键加载演示数据」体验）；
4. 关闭窗口即退出程序（单实例锁：重复双击会聚焦已有窗口）。

---

## 3. 数据目录（%APPDATA%\AI4SResearchAgent\）

桌面版**不读写安装目录**，所有用户数据放在这里：

| 子项 | 作用 |
|---|---|
| `app/` | 当前运行的业务代码（首次从安装目录 `app_bundle/` 复制；热更新会替换这里） |
| `app.backup-<版本>/` | 上一版业务代码备份（更新失败可回滚） |
| `storage/` | 用户上传 PDF、向量库（chroma）、解析产物 |
| `logs/` | `launcher.log`（壳层启动日志）、`streamlit.log`（业务日志）、`updater.log`（更新日志） |
| `updates/` | 已下载待应用的业务包 zip |
| `app.lock` | 单实例锁（含端口、pid） |
| `launcher_state.json` | 当前业务版本号 |
| `.env` | 从 `.env.example` 复制而来的环境变量（API Key、模型名等） |

「文件 → 打开数据目录」可直接打开该目录。

---

## 4. 热更新机制

- 壳层（exe 本体 + `desktop/`）**不参与热更新**，需要换运行时才会重下完整安装包；
- 业务代码（`app.py`、`ui/`、`services/`、`core/`、`tools/` 等）通过 GitHub Release 下发：
  - 启动时静默查询一次（3 秒超时，失败不打扰用户）；
  - 发现新版本且当前运行时满足 `min_runtime_version`，直接下载 SHA256 校验后替换 `app/`；
  - **下次启动生效**（当前窗口继续跑旧版，避免中途重载）；
  - 旧版自动备份到 `app.backup-<旧版本>/`，若新版解压失败会自动回滚；
- 手动检查：「帮助 → 检查更新」。

---

## 5. 从源码打包（开发者）

### 5.1 一键打包

```powershell
# 在项目根目录
build_desktop.bat
```

产物：`dist\AI4SResearchAgent\AI4SResearchAgent.exe`（及同目录依赖）。

### 5.2 发布业务更新（热更新包）

```powershell
# 用 anaconda 环境的 python
C:\Users\Administrator\anaconda3\envs\ai_research\python.exe scripts\release.py `
    --version 1.0.1 `
    --notes "修复双栏PDF串读"
```

产出：

- `dist-release\app-<version>.zip` —— 业务包
- `dist-release\version.json` —— 版本元数据（含 SHA256）
- 自动 `git tag v<version>`（不 push）
- 若装了 `gh` CLI 并已登录，自动 `gh release create`；否则按提示手动上传到 GitHub Release

> 业务版本号在 `version.json` 里；`min_runtime_version` 取 `desktop/version.py` 的 `RUNTIME_VERSION`。
> 如果业务代码用到了新的壳层 API，**必须**同步提升 `RUNTIME_VERSION` 并重新出完整安装包，否则老用户会被 `min_runtime_version` 拦截。

### 5.3 出完整安装包（换壳层时）

1. 先打包 exe：`build_desktop.bat`；
2. 用 Inno Setup 编译 `installer.iss`（产物 `installer\Output\AI4SResearchAgent_Setup.exe`）；
3. 把安装包上传到 GitHub Release，tag 与业务包区分（例如 `v1.0.0-runtime`）。

---

## 6. 常见问题

### 6.1 启动后一直转圈 / 提示「Streamlit 服务未就绪」

- 看「文件 → 查看日志」，打开 `logs\streamlit.log` 末尾；
- 常见原因：杀毒软件拦截了子进程、WebView2 版本过旧、端口被占。

### 6.2 端口被占（47851）

- 47851 是单实例锁端口。如果上次异常退出锁没释放：
  - 打开任务管理器结束所有 `AI4SResearchAgent.exe`；
  - 删除 `%APPDATA%\AI4SResearchAgent\app.lock`；
  - 重新启动。

### 6.3 WebView2 缺失

- 从微软官网下载「WebView2 Evergreen Runtime」安装后重启程序。

### 6.4 杀软误报

- PyInstaller 打的 exe 常被误报。给 `dist\AI4SResearchAgent\` 加白名单，或用 Inno Setup 出的安装包（有签名/路径更友好）。

### 6.5 更新失败 / 想回滚

- 看 `logs\updater.log`；
- 手动回滚：退出程序 → 把 `%APPDATA%\AI4SResearchAgent\app\` 删除，把 `app.backup-<旧版本>\` 改名为 `app\`，重启即可；
- 也可以直接删整个 `app\` 目录，下次启动会从安装目录的 `app_bundle\` 重新复制一份出厂版本。

### 6.6 我想改业务代码自己用

- 直接改项目源码跑 `streamlit run app.py`（开发模式）；
- 桌面版用户不要手动改 `%APPDATA%\...\app\` 下的文件，下次更新会被覆盖。

---

## 7. 目录结构（壳层 vs 业务层）

```
ai_research_agent/
├─ desktop/                # 壳层（打进 exe，不热更新）
│  ├─ __main__.py          # 入口：桌面模式 / --st-server 服务模式
│  ├─ paths.py             # resource_root / data_root / app_bundle / app_dir
│  ├─ launcher.py          # 单实例锁、动态端口、子进程生命周期
│  ├─ window.py            # pywebview 原生窗口 + 菜单
│  ├─ updater.py           # 热更新检查/下载/应用/回滚
│  ├─ version.py           # RUNTIME_VERSION（壳层版本）
│  └─ about.py
├─ app.py / config.py      # 业务代码（打进 app_bundle，热更新）
├─ ui/ services/ core/ tools/ lib/ demo_data/ .streamlit/
├─ AI4SResearchAgent.spec  # PyInstaller 配置
├─ build_desktop.bat       # 一键打包
├─ scripts/release.py      # 业务发版工具
└─ installer.iss           # Inno Setup 完整安装包脚本
```

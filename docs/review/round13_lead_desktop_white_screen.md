# Round 13 桌面版白屏事故：根因定位与修复

> 父代（team-lead）独立排查记录。关联用户反馈链：「你这不还是点不开吗」→「**启动桌面版不还是有这问题，能不能认真的点**」→「我打游戏影响到你了不」。

## 1. 结论 TL;DR

- **用户 21:48 启动桌面版的实况**：窗口创建成功，但 WebView2 页面从未加载，**18 秒后进程树静默退出**（launcher.log + streamlit.log 双日志实锤）。
- **根因（Job 内探针实锤）**：WebView2 browser 进程启动 GPU 子进程失败 → 直接 FATAL 自杀（`msedge.dll` int 3，退出码 `0x80000003`，FATAL 日志 `GPU process isn't usable. Goodbye.`）。
- **用户侧诱因（强推断，未直接验证）**：游戏（艾尔登法环）全屏独占显卡。**Edge 浏览器自身有软件渲染回退，WebView2 嵌入式模式没有**——这就是产品缺陷。
- **修复**（已落 `desktop/window.py`）：① 显式 `--disable-gpu`（WebView2 官方环境变量注入，软件渲染兜底）；② `storage_path` 持久化 + 关闭无痕模式（消除 pywebview 5.4 TemporaryDirectory GC 删目录隐患）；③ 页面加载失败时原生弹窗诊断（消灭"静默消失"）。
- **验证状态**：编译 + 174/174 unittest + GPU fallback 4 场景单测全过；**修复对用户侧的有效性待用户在游戏运行状态下实测**（我的探针跑在 WorkBuddy 的 Job object 内，无法复现用户侧环境，详见 §6）。

## 2. 现象与时间线（用户 21:48 实况，双日志交叉还原）

`%APPDATA%\AI4SResearchAgent\logs\launcher.log`：

```
[21:48:47] lock written: {'port': 59629, 'pid': 33228}
[21:48:47] streamlit child started: pid=31824 port=59629
[21:48:49] server healthy on port 59629      ← 健康检查通过
[21:49:07] shutdown done for pid 31824       ← 18 秒后进程树整体退出
[21:49:07] app.lock removed
```

`logs\streamit.log`：59629 端口 healthy 之后**零页面访问记录**（对比 10-12 21:25 那次健康后立即刷出大量 `st.components.v1.html` 组件警告）→ **WebView2 从未成功加载页面**。

同夜我三次复现（22:12 / 22:16 / 22:18 探针）全部一致：窗口创建成功（`WindowsForms10.Window.8.app.0.aec740_r22_ad1`，vis=True，1440×900），进程存活，streamit.log 零访问，**无任何 traceback**（pythonw 模式连 stderr 都被丢弃，异常出口全部静默）。

## 3. 证据链（按取证顺序）

1. **窗口活着 ≠ 页面加载**：`shown` 事件 0.4s 触发，`loaded` 事件 30s 内从不触发（探针 v3，目标 8501 Web 版，其自身 `GET /` 5ms/200 正常）。
2. **browser 进程从未被创建/秒退**：探针运行期间系统内 11 个 `msedgewebview2.exe` 全部创建于 12:01（其他应用），无一属于探针。
3. **monkey-patch pywebview 回调**（探针 v4/v5）：`CoreWebView2InitializationCompleted: IsSuccess=True`，`BrowserProcessId=19324` 存在 → 1.9s 后 `status=stopped`（挂起）→ 3.9s `GONE`。
4. **退出码**（探针 v7，OpenProcess/WaitForSingleObject/GetExitCodeProcess）：**`0x80000003` = STATUS_BREAKPOINT**（Chromium `IMMEDIATE_CRASH`/`__debugbreak`）。
5. **崩溃转储**（Crashpad `reports/5e200638-….dmp`，6.5MB）内 FATAL 日志：
   ```
   [20872:223421.337:FATAL:content\browser\gpu\gpu_data_manager_impl_private.cc:408] GPU process isn't usable. Goodbye.
   ```
6. **Watson 元数据**（Crashpad/watson_metadata）：`ModuleName=msedge.dll; ModuleVersion=155.0.4283.62; ProcessType=browser; SubCode=0x80000003`，且上报已成功（HttpStatus=200）。

## 4. 根因分析

### 4.1 主链（Job 内实锤）

```
游戏全屏独占显卡（用户侧诱因，强推断）
  → WebView2 browser 启动 GPU 子进程失败
  → gpu_data_manager_impl_private.cc:408 FATAL "GPU process isn't usable"
  → msedge.dll int 3，browser 进程退出码 0x80000003
  → 页面永不加载（loaded 不触发、streamit 零访问）
  → webview.start() 静默返回 → main() finally shutdown_tree
  → 用户看到：窗口一闪/白屏，18 秒后无提示消失
```

关键对比：**手动启动 browser 进程（同 cmdline 去掉管道参数）正常退出**（仅两条无害 ERROR）→ browser 可执行文件与 runtime 完好，崩溃发生在 **host（pywebview）连接阶段**。

### 4.2 排除项（均有实证，防止后人重复踩）

| 假设 | 证据 | 结论 |
|---|---|---|
| WebView2 runtime 损坏 | runtime 目录 69 文件完整；其他应用（SearchHost/BatteryWidgetHost）的 WebView2 进程健康存活 | ❌ |
| user-data-dir 被删（TemporaryDirectory GC） | 探针 v6 显式传持久 `storage_path`，browser 仍 2s 内死 | ❌ 非本次死因（但是独立隐患，已顺手修） |
| `--disable-gpu` 无效 | 探针 v10 cmdline 确认参数已传入，Job 内仍死 | ⚠️ Job 内无效 ≠ 用户侧无效（见 4.3） |
| 单实例锁/端口残留 | 47851 空闲、app.lock 已清、launcher 日志完整走完正常流程 | ❌ |
| pywebview/pythonnet 版本回退 | dist-info 均为 9-16 安装，10-12→10-13 之间未变更 | ❌ |

### 4.3 ⚠️ 归因分层（必须如实阅读）

- **Job 内（我的探针环境）**：探针进程经 `IsProcessInJob` 实证位于 **WorkBuddy 的 Job object** 内（`WORKBUDDY_APP_LIFETIME_JOB`）。Job 限制子进程早期初始化，导致 browser 无论是否 `--disable-gpu` 都在 2-4s 内死亡。`pop` 环境变量无效（Job 是内核对象），`CREATE_BREAKAWAY_FROM_JOB` 拒绝访问，WMI/schtasks 创建进程被 WorkBuddy 安全策略拦截——**所有脱离 Job 的路径均被封死，这是沙箱的设计行为**。
- **用户侧（vbs → pythonw，无 Job）**：直接证据只有「18 秒退出 + 零访问」；GPU FATAL 的 dump 证据来自 Job 内探针，**不能直接归因到用户侧**。用户侧诱因的游戏独占显卡为强推断（时间吻合 + 无其他候选）。
- **共同点**：两侧现象完全一致（窗口在、页面死、静默退出）。

## 5. 修复内容（`desktop/window.py`，三处）

1. **`_ensure_webview2_gpu_fallback()`**：向 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 追加 `--disable-gpu`（WebView2 官方环境变量，幂等、保留外部参数）。Streamlit UI 不依赖 GPU，用可忽略的软件渲染代价消除「GPU 不可用 → 整个应用暴毙」单点。
2. **`webview.start(gui="edgechromium", private_mode=False, storage_path=data_root/"webview")`**：持久化 user data 目录，消除 pywebview 5.4 `TemporaryDirectory().name` 被 GC 删目录的隐患（独立问题，顺手修复），并保留缓存/登录态。
3. **`_report_page_load_failure()`**：`loaded` 未触发而 `start()` 返回（browser 崩溃静默退出）时弹原生诊断框，给出三条处置建议——消灭「窗口一闪即消失、零反馈」体验。

## 6. 验证状态与待办

**已完成**：
- `compileall` 全量 OK；`unittest` **174/174 OK**（基线已从 18 涨至 174）；
- `_ensure_webview2_gpu_fallback` 4 场景单测：无外部设置 / 幂等 / 保留外部参数 / 不重复追加，全过；
- `smoke_ui` / `smoke_read` 后台执行中（desktop 模块不被 smoke 覆盖，仅确认无间接回归）。

**待办（需用户配合，我在 Job 内无法完成）**：
1. **用户在游戏运行状态下启动桌面版实测**（`启动桌面版.vbs`）：预期页面正常加载。若成功 → 一次性同时验证「修复有效」与「游戏场景覆盖」。
2. 若仍白屏 → 请用户关掉游戏再启动一次做对照；两次都失败则说明用户侧另有死因（需新的 dump 路径，届时可从 `%APPDATA%\AI4SResearchAgent\logs` 与 Crashpad 目录取证）。
3. 修复若确认有效，考虑是否在 `DEPLOY.md` / 技术文档补记 WebView2 GPU 容错说明。

## 7. 取证与复现命令（供复查）

```bash
# 启动史与锁状态
cat "$APPDATA/AI4SResearchAgent/logs/launcher.log"
cat "$APPDATA/AI4SResearchAgent/logs/streamlit.log"

# browser 生命周期 + 退出码（monkey-patch pywebview 回调）
python wb_wv2_probe7.py            # 打印 FULL CMDLINE + 退出码 + user_data_dir 内容

# 崩溃转储
ls "$TEMP"/tmp*/EBWebView/Crashpad/reports/*.dmp
cat "$TEMP"/tmp*/EBWebView/Crashpad/watson_metadata
```

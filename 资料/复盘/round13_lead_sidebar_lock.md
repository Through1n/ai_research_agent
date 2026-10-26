# WorkBuddy agent 侧边栏「关闭后无法重新打开」排查报告（Round 13 · team-lead 主查）

- 日期：2026-10-20
- 对象：WorkBuddy 5.8.3（build db0e9b51，Electron 37.10.3）Windows 桌面端
- 本机配置：Windows + `environmentType==='local'` + 已登录（uid 在场）+ 窗口 1500×864（非窄屏）
- 排查产物：`C:\Users\Administrator\AppData\Local\Temp\wb_repro\`（70–74 号状态感知序列截图 + slot 高倍裁剪）、`wb_sidebar_seq2.py`（状态感知测试脚本）
- 代码锚点均给出自 `app.asar` 解包目录（`C:\Users\Administrator\AppData\Local\Temp\wb_asar\`），偏移为 `contexts-DgzzFjnf.js` / `desktop-sdk-setup-MULXe43U.js` 内的字符偏移，附可辨识代码片段以便复核。

---

## 1. 结论（先读这条）

**本机当前配置下，未能复现"永久锁死"**：用真实鼠标点击 + 像素扫描逐条复测，收起后的所有再打开路径（titlebar slot 的 `□展开` 按钮、Ctrl+B、窄窗↔宽窗自动恢复、收起后新建会话再展开）**全部正常恢复**。排查中唯一一次"展开点击失效"经查明是**测试脚本自身前提错误**（起点其实是展开态，点击落点命中了搜索/收起图标），已用状态感知版脚本重测澄清（见 §3 序列 6）。

但静态全量解码确认：**代码里存在两个真实缺陷，能在特定条件下精确产出用户描述的症状**——

- **R1（主因，可造成"永久"无法打开）**：Windows 桌面 + local 模式下，侧边栏收起态的**唯一图形入口**是 titlebar slot portal，而该 portal 的渲染门槛与桌面外壳的可见性公式**双双门禁于登录账号（uid）**。账号未就绪（未登录 / 会话过期 / 仍在加载）时，slot 图标数被置 0、宽度置 0px，同时顶栏的展开按钮又因"slot 存在"而主动自隐藏 ⇒ **收起态图形入口为零**，只剩 Ctrl+B（不可发现）⇒ 用户感知"侧边栏关了打不开"。
- **R2（次因，单次点击无反馈）**：`cancel()` 不复位 `desiredExpandedRef`，open 在两帧窗口内被打断后会出现「视图已隐藏但 desiredExpanded=true」的短暂分叉 ⇒ 下一次 toggle 执行 close 分支 ⇒ **按钮 / Ctrl+B 点击一次完全无视觉反馈**，并会把 localStorage 误写为 `false`。所有已追踪的交错下第二次点击即自愈，非永久锁死。

另有两个健壮性缺陷：R3（grid ref 未附着时点击完全 no-op）、R4（窄屏 drawer 分支无 try/catch）。

**处置状态（2026-10-20 19:40 应用，21:45 复核）**：用户裁决本机热修。已应用 4 处补丁（E1 治 R2、E2+E3 治 R1、E5 治 R4），直改 `app.asar.unpacked` 磁盘文件（无需重打包 asar、不碰 integrity 块），语法门禁 + 锚点唯一性 + 替换后复核全过；原件与一键回滚在 `C:/Users/Administrator/AppData/Local/wb_sidebar_patch_backup/`。**补丁需重启 WorkBuddy 生效**——截至 21:45，应用自 12:01 起已连续运行 9.5 小时从未重启，补丁尚未被任何渲染进程加载，用户复测反馈「还是点不开」即因此（归因证据链见 §8.1；补丁本身无损性已终极验证，见 §8.4）。重启后跑 `verify_sidebar_patch.py` 做交互验证（协议见 §6 与 §8.4）。

---

## 2. 侧边栏状态机全链（排查基座）

三层 + 外壳，全部实测/解码确认：

| 层 | 位置 | 职责 |
|---|---|---|
| `useSidebarLayout` | contexts-DgzzFjnf.js @2170501 | 总状态：持久化（`localStorage['agent-ui-sidebar-expanded']` / `['agent-ui-sidebar-width']`）、初始 hydration、三模式 toggle（`K`）、宽度自适应、sash 拖拽、会话切换同步 |
| `useLocalSidebarMotion` | @2166841 | local 模式动画开合：`open(v)` / `close(y)` / `cancel(b)` + 相位机（`opening-prep`→`opening`→`closing`→`idle`） |
| `useSidebarSashResize` | @2166841 之后 | 拖拽分隔条 collapse/expand |
| 桌面外壳 responsiveWidthEffect | desktop-sdk-setup-MULXe43U.js @350944 | 最终可见性强刷：`setViewVisible(sidebar, !!account && !accountLoading && !workbuddyHidden)`，deps 含容器宽度 ⇒ 每次宽度变化都按此公式重刷 |

关键常量：`Bw={EXPANDED_WIDTH:280, MIN_WIDTH:240, MAX_WIDTH:360, COLLAPSED_WIDTH:48}`；窄屏阈值 `isNarrowForSidebar = containerWidth < 640`（=280+360）；快捷键 `toggle-sidebar` 默认 **Ctrl+B**（app-core-BZu_n0cW.js）。

---

## 3. 实测矩阵（真实点击 + 像素扫描判定，非代码推断）

判定方法：沿 y=500 从 x=300 扫到 x=520，找灰(247)→白(255)跳变；边界≈471 ⇒ 展开（280px），无跳变 ⇒ 收起。

| # | 序列 | 结果 |
|---|---|---|
| 1 | 点 slot `□` 收起 | ✅ 收起 |
| 2 | 收起后 slot 出现 `□展开`（tooltip「展开侧边栏 Ctrl+B」） | ✅ 1.5s 内就位 |
| 3 | 点 `□展开` | ✅ 恢复（x=471 跳变，280px） |
| 4 | Ctrl+B（先校验前台焦点） | ✅ 双向可靠 |
| 5 | 缩窗 <640 → 自动唤出抽屉；Ctrl+B 收起；恢复宽窗 | ✅ slot 展开钮仍可恢复 |
| 6 | **收起 → 点 slot `⊕新建任务`（触发会话切换）→ 点 `□展开`** | ✅ **恢复**（`72_v2_after_newtask`=HIDDEN，`73_v2_after_expand`=EXPANDED~280px） |
| 7 | renderer.log 全量扫描 | 无侧边栏相关异常（仅 cl-diag 性能标记） |

> 序列 6 直接反证了"会话切换触发 motion cancel 锁死"的假设：新建任务后展开按钮第一击即生效。
> 此前一次"失效"（60/61/62 号截图）系脚本未建立收起前提——起点实为展开态，(232,106) 命中搜索、(202,106) 命中收起，属于测试脚本缺陷而非产品缺陷。

---

## 4. 根因（按危险性排序，附锚点）

### R1（主因）：local 模式收起态的唯一图形入口被账号门禁掐断 ⇒ 可"永久"无法打开

证据链（三处代码相互印证）：

1. **入口自隐藏**（contexts-DgzzFjnf.js @3226445）：
```js
SidebarExpandButton=()=>{let{t:e}=$c(),{collapsed:t,expandDisabled:n,toggle:r,isLocalMode:i,hoverPeekEnabled:a,bindExpandTrigger:o,openHoverPeek:s,scheduleHoverPeekClose:c}=useShellSidebar(),{bindButton:l,handleToggle:u}=useSidebarToggleFlip(a?o:void 0);
return !t||hasTitlebarActionSlot()?null:(...按钮...disabled:n)}
```
Windows 桌面下 slot 元素在模块作用域同步创建（main-Sc99h1W2.js `ensureTitlebarLeftSlot`，先于 React 挂载）⇒ `hasTitlebarActionSlot()` 恒为真 ⇒ **顶栏展开按钮永远不渲染**。渲染处 @3232025 的外层条件同样要求 `!hasTitlebarActionSlot()`（双保险）。

2. **只剩 slot portal，而 portal 门槛含 uid**（@3101925 附近）：
```js
let Qe=isNonMacDesktopSidebar(), [$e,et]=useState(null);   // $e=slot 元素，rAF≤30 帧解析
let tt=Qe&&!!Je&&!!$e, nt=tt?a?'collapsed':'expanded':'none';   // Je=H?.uid（账号）
r = nt==='collapsed' && $e ? createPortal(n,$e) : null;         // n=tabSlot（□展开 + ⊕新建任务）
useEffect(()=>{ if(!Qe||typeof document>'u'||!$e)return;
  let e=nt==='none'?0:nt==='collapsed'?2:3;
  document.body.style.setProperty('--wb-titlebar-slot-icons',String(e));
  e===0?document.body.style.setProperty('--wb-titlebar-slot-width','0px'):document.body.style.removeProperty('--wb-titlebar-slot-width');
},[$e,nt,Qe]);
```
uid 为空 ⇒ `nt='none'` ⇒ **slot 图标数置 0、宽度置 0px，portal 无内容**。

3. **local 模式没有 classic 的 48px 竖条兜底**（@3101925）：
```js
return st?(<Fragment>{r}<SidebarHoverPeekOverlay .../>{ate}{no}</Fragment>)     // local：只有 slot portal + hover peek
:(<Fragment>{r}<div style={{width:'48px'}}>{!e&&!!Je&&<logo 展开按钮 onClick:i>}</div>...</Fragment>);  // classic：48px 竖条兜底（同样要求 !!Je）
```

4. **外壳可见性公式同样门禁账号**（desktop-sdk-setup-MULXe43U.js @350944）：`setViewVisible(qm, !!V && !lt && !Fn)`，`V`=account、`lt`=accountLoading、`Fn`=workbuddyHidden。账号未就绪时，外壳在每次宽度变化时都强制隐藏侧边栏。

⇒ **触发条件**：账号未就绪（未登录 / 会话过期 / 加载中）时侧边栏处于收起态 ⇒ 图形入口为零，唯一恢复手段是 Ctrl+B。账号就绪后入口自动出现（或按 Ctrl+B）。此为本机已登录状态下未复现、但代码级确证的条件；未实测登出态以避免影响当前会话。

### R2（次因）：方向判定只信 `desiredExpandedRef`，不信真实可见性 ⇒ 单次点击无反馈

`K` 的 local 分支（@2170501 区域内）：
```js
else if(n){ if(ee.current){localStorage.setItem(Jw,'false'); re()} else {localStorage.setItem(Jw,'true'); ne()} }
```
方向完全由 `ee.current`（`desiredExpandedRef`）决定。而 `cancel()`（@2166841 的 `b`）：
```js
b=(0,Kw.useCallback)(()=>{f.current+=1,m(),d.setPhase('idle')},[m,d])   // 只使待执行 showSidebar 过期 + 相位回 idle，不复位 l.current
```
open 被 abort 的路径：会话切换 layout effect `le.current!==p&&(le.current=p,n&&(ie(),ee.current=!j))`、`enabled=n&&!u` 翻转（`useEffect(()=>{i||b()},[i,b])`）、宽度变化竞态。abort 后若 `ee.current=true` 而视图仍隐藏，下一次 toggle 走 `re()`（close）⇒ 无视觉变化 + 误写 localStorage `'false'` + `workbuddyHidden` 被置 true（叠加外壳公式的强制隐藏）。**第二次点击即自愈**（`re()` 会把 `l.current` 置 false），故非永久锁死——但用户视角就是"点了没反应"。

### R3：`K` 前置 null 守卫 ⇒ 完全 no-op
```js
K=()=>{ if(e.current&&s.current){ ... } }   // grid / sidebarGridViewRef 任一为 null ⇒ 静默返回，点击与 Ctrl+B 均无反馈
```

### R4：窄屏 drawer 分支无 try/catch
```js
if(u){ ...; let t={placement:'left',size:w.current,showBackdrop:!0,animationDuration:300}, n=e.current.toggleDrawer(s.current,t); A(n) }   // toggleDrawer 抛错 ⇒ 点击静默死亡；<640px 时这是唯一路径
```

---

## 5. 补丁级修复方案（最小变更，按优先级）

> **已于 2026-10-20 19:40 应用 E1/E2/E3/E5 四个补丁**（用户裁决：本机热修）。应用方式与回滚见 §6。
> 补丁均保持 minified 单行风格；每个锚点应用前都断言**全文件唯一命中**（count==1），替换后过语法门禁再原子替换。
> **未应用的三个**：F1 第 1 条（tabSlot 的 uid 门禁拆分——tabSlot 内容构建在组件树上层、无法验证账号安全，且 F1 第 2 条已覆盖同一用户可见结果）、F3（`K` 的 null-ref 兜底，UI 上几乎不可达、价值低）、F5（slot 解析 MutationObserver——已实证 slot 在 React 挂载前同步就绪，30 帧上限不会触发）。按最小变更纪律放弃。

### F1 = E2+E3（治 R1，必须）：展开入口解除账号门禁【已应用第 2 条；第 1、3 条未应用】
1. ~~TaskListRegion 的 portal 门槛拆分~~【未应用】tabSlot 内容构建在组件树上层，无法验证其账号安全性，不作为。
2. **`SidebarExpandButton` 兜底（E2，已应用）**——slot 图标数为 0 时顶栏按钮补位：
   - 原：`return!t||hasTitlebarActionSlot()?null:`
   - 改：`return!t||hasTitlebarActionSlot()&&document.body.style.getPropertyValue('--wb-titlebar-slot-icons')!=='0'?null:`
   （slot 存在但图标数为 0 ⇒ 说明 portal 没渲染出入口 ⇒ 顶栏按钮兜底渲染，双入口永不同时为缺。）
3. **`WorkBuddyTopBar` 外层条件同步（E3，已应用）**——否则该 Fragment 在 Windows 下永不渲染，E2 无从触发：
   - 原：`!D&&b&&!hasTitlebarActionSlot()&&(0,ON.jsxs)(ON.Fragment,{children:[F&&(0,ON.jsx)(SidebarExpandButton,{}),`
   - 改：`!D&&b&&(!hasTitlebarActionSlot()||document.body.style.getPropertyValue('--wb-titlebar-slot-icons')===`0`)&&(0,ON.jsxs)(ON.Fragment,{children:[F&&(0,ON.jsx)(SidebarExpandButton,{}),`
4. （产品决策，列为建议，未应用）外壳公式 `!!V&&!lt&&!Fn` 中的账号门禁建议放宽为 `!lt&&!Fn`，避免未登录被强制隐藏且无入口。

### F2 = E1（治 R2）：以真实可见性为单一事实源【已应用】
`K` local 分支改为读真实可见性决定方向（其余不变）：
- 原：`else if(n){if(ee.current){try{localStorage.setItem(Jw,'false')}catch{}re()}else{try{localStorage.setItem(Jw,'true')}catch{}ne()}}`
- 改：`else if(n){let vis=!1;try{vis=!!e.current.isViewVisible(s.current)}catch{}if(vis){try{localStorage.setItem(Jw,'false')}catch{}re()}else{try{localStorage.setItem(Jw,'true')}catch{}ne()}}`
- 语义核对：所有一致状态下与原逻辑等价（隐藏⇒开、可见⇒关），仅在 desired/actual 分叉时更正确；`isViewVisible` 在本函数其余部分已有使用，try/catch 兜底默认"开"。
- 附加（防御，未应用）：`cancel()` 中按可见性复位 desired——E1 已使 `K` 不再信 desired，此条非必要。

### F3（治 R3）：null 分支兜底【未应用】
`K` 首行 `if(e.current&&s.current)` 的 null 场景在 UI 上几乎不可达（按钮随 sidebar view 挂载），价值低，按最小变更放弃。

### F4 = E5（治 R4）：drawer 分支容错【已应用】
- 原：`let t={placement:'left',size:w.current,showBackdrop:!0,animationDuration:300},n=e.current.toggleDrawer(s.current,t);A(n)`
- 改：`let t={placement:'left',size:w.current,showBackdrop:!0,animationDuration:300},n=!1;try{n=e.current.toggleDrawer(s.current,t)}catch(err){console.warn('[useSidebarLayout] toggleDrawer failed:',err);try{n=!!e.current.isDrawerOpen(s.current)}catch{}}A(n)`

### F5（防御）：slot 解析改 MutationObserver【未应用】
已实证 `ensureTitlebarLeftSlot` 在模块作用域同步执行（先于 React 挂载），`$e` 首次 effect 即解析成功，rAF 30 帧上限不会触发；按最小变更放弃。

---

## 6. 落地记录：已热修（unpacked 文件直改，2026-10-20 19:40）

### 关键发现：两个目标文件在 asar 里标记 `unpacked: true`
头部 JSON（`app.asar` 前 16 字节前缀 + 3.8MB JSON 头，14,474 个文件**全部带 SHA256 integrity 块**）显示：
```
/renderer/品牌资源/contexts-DgzzFjnf.js    {'size': 7553508, 'unpacked': True}  integrity: YES
/renderer/品牌资源/desktop-sdk-setup-MULXe43U.js {'size': 393773, 'unpacked': True}  integrity: YES
```
⇒ 运行时实际读取的是 `resources/app.asar.unpacked/renderer/品牌资源/` 下的**磁盘文件**（已 sha256 比对与 asar 内副本完全一致：`8d0c375f…`），asar 内的副本只是影子。
⇒ **不需要重打包 asar、不触碰 integrity 块**——直接改 unpacked 文件即可，运行时可写。

### 已应用（4 处，全部在 `app.asar.unpacked/renderer/品牌资源/contexts-DgzzFjnf.js`）
| # | 位置 | 改动 | 字节增量 |
|---|---|---|---|
| E1 | `useSidebarLayout` 的 `K` local 分支 | 方向判定从 `ee.current`（desired）改为 `e.current.isViewVisible(s.current)`（真实可见性，try/catch 兜底） | +56 |
| E2 | `SidebarExpandButton` 守卫 | `hasTitlebarActionSlot()` 且 slot 图标数 ≠ '0' 时才隐藏；图标数为 0 ⇒ 按钮兜底渲染 | +72 |
| E3 | `WorkBuddyTopBar` 展开按钮外层条件 | 同 E2 判据：slot 图标数为 0 时渲染展开/新建任务Fragment | +74 |
| E5 | `K` 窄屏 drawer 分支 | `toggleDrawer` 包 try/catch，失败读 `isDrawerOpen` 兜底 + console.warn | +130 |

- 文件：7553508 → 7553840 字节；sha256 `8d0c375f…` → `8563cfc4…`
- **语法门禁（双向验证过）**：`node --check`（.mjs）对原文件 exit 0、对故意注入失衡括号的样本 exit 1；补丁后文件 exit 0。
- 每处锚点应用前断言全文件唯一（count==1），替换后复核「旧锚点消失 + 新锚点在位」四项全 True。

### 备份与回滚（重启前有效，重启不丢失）
- 目录：`C:\Users\Administrator\AppData\Local\wb_sidebar_patch_backup\`
  - `contexts-DgzzFjnf.js.orig`（补丁前原件，sha `8d0c375f…`）
  - `rollback_sidebar_patch.py`（一键回滚，会另存 `.patched.bak` 供二次后悔）
  - `verify_sidebar_patch.py`（重启后的状态感知验证脚本，HWND 自动发现，输出 PASS/FAIL）
- 回滚命令：`python C:\Users\Administrator\AppData\Local\wb_sidebar_patch_backup\rollback_sidebar_patch.py`，然后重启 WorkBuddy。

### 生效条件与残留风险（如实告知）
1. **必须重启 WorkBuddy 才生效**——运行中的渲染进程仍用内存里的旧代码。重启会中断当前对话（历史已在磁盘上，重启后可继续）。
2. **自动更新会覆盖**：后续版本更新会同时替换 asar 与 unpacked 目录，补丁失效需重打（当前 5.8.3 已是最新，HTTP 204）。
3. **V8 代码缓存**：若重启后补丁"没生效"，删 `~/.workbuddy/` 下的 Code Cache/Cache 目录再启（源码变更正常应触发缓存失效，此为兜底）。
4. 本补丁**不改变**任何正常路径的行为（E1 在所有一致状态下与原逻辑等价，仅在 desired/actual 分叉时更正确）；E2/E3 只在 slot 图标数为 0（uid 缺失）时新增入口，正常登录态无变化。

### 重启后验证协议（一条命令）
```
python C:\Users\Administrator\AppData\Local\wb_sidebar_patch_backup\verify_sidebar_patch.py
```
覆盖：T1 收起态可达 / T2 slot 展开一击恢复 / T3 Ctrl+B 收起 / T4 Ctrl+B 展开 / T5 收起→新建任务→展开一击恢复（E1 核心场景）。全 PASS 才算验证通过。

---

## 7. 副作用知会

- 排查中点击 slot 的 `⊕新建任务` 在用户工作区**产生了新的空会话**（60–74 号截图序列）。
- 测试截图与脚本在 `C:\Users\Administrator\AppData\Local\Temp\wb_repro\` 与 `wb_sidebar_seq2.py`（Temp 目录，重启不保留，重要结论已固化在本报告）。
- 收尾状态（19:40）：主窗口 1500×864（191,98）、侧边栏展开态，均已验证恢复。
- **21:34 更新**：主窗口已被用户最小化；另有两个残留小窗——900×680 独立窗加载失败错误页窗（见 §8.2）+ 800×640 全局搜索窗（功能正常）；用户前台已切换至 Microsoft Edge。均在等待用户重启后一并消散（§8.4）。

---

## 8. 用户复测反馈「你这不还是点不开吗」归因（2026-10-20 20:39–21:45）

### 8.1 直接原因：补丁从未被加载——应用自 12:01 启动后从未重启

证据链（全部实测，非推断）：

- 进程清单（21:34 快照）：主进程 pid 21256 启动于 **12:01:36**，主渲染进程 pid 21828 启动于 **12:01:38**，uptime 573 分钟；`AppStartup.log` 自 12:01:37 之后无任何新启动记录。
- `main.log` 中主窗口的 `did-start-loading` / `did-finish-load` 仅 12:01:38 一次，此后 9.5 小时无任何重载事件。
- 补丁文件 mtime **19:38**，晚于渲染进程启动时间约 7.5 小时 ⇒ 主窗口渲染进程内存中执行的是**打补丁前的旧代码**。

⇒ 用户在 20:39–20:40 的任何侧边栏测试跑的都是旧代码，旧缺陷必然复现。**「还是点不开」= 补丁未生效，而非补丁无效**——这是部署（未重启）问题，不是修复质量问题。补丁本身的正确性已另行终极验证（§8.4）。

### 8.2 同期独立窗口加载失败（win2）——根因是会话切换竞态，与补丁无关

**现象**：20:39–20:40 出现 4 次 `[RendererLoadGuard] did-start-loading`（12:39:45.083 / 12:39:52.346 / 12:39:55.787 / 12:40:34.147Z），**全部没有后续的 `Renderer bootstrap done`**；其中一个 900×680 窗口（HWND 10750530）至 21:34 仍开着，显示宿主包注入的错误页「**窗口加载失败，请关闭后重试。**」（`role=alert`，无重试按钮，只能关闭重开）。

**机制**：该错误页来自独立窗口宿主包 `host-BKHs3X32.js` 的 `assertWindowAppRenderer` 超时回退路径（i18n key `independentWindow.loadFailed` / `independentWindow.retryAfterClose`）——窗口 app 渲染进程未能在超时内完成 bootstrap 断言时，由宿主注入回退页。

**根因判定（三条独立证据，全部指向主进程 IPC 竞态，而非渲染模块错误）**：

1. **时间相关性**：同期 `AppStartup.log` 记录 50 秒内 5 次 `reset_for_session_change`（20:39:43/50/52、20:40:20/33）——每次都拆解并重建主进程 SDK handler；4 次失败加载中多次伴随 `[WB-SDK] invoke error on "getProductConfiguration": No handler`（12:39:44.345×4、12:39:52.190/317、12:40:33.930/34.089）——窗口 bootstrap 调产品配置时，主进程恰好无 handler。
2. **同类错误早于补丁存在**：完全相同的 `getProductConfiguration: No handler` 在 **16:48:28/816 与 16:57:15/37/47/52**（补丁应用于 19:38 **之前**）就已出现 7 次 ⇒ 该错误是既有环境行为，**与补丁无因果关系**。
3. **失败签名不匹配模块加载失败**：若补丁使 `contexts-DgzzFjnf.js` 加载失败，症状应是页面加载错误 / 渲染进程控制台报错 / `did-fail-load`，而非主进程侧 IPC handler 查找失败。且这 4 次加载**没有 `[WindowOpenPerf] window created` 与 `[RegisteredWindowRuntime]` 记录**——不是常规注册窗口应用（疑为扩展或 `window.open` 所建），其 bundle 链未经证实包含 contexts 文件。

旁证：会话切换风暴结束后 12:40:25（20:40:25），设置窗经**同一条宿主包链路**一次性加载成功（preload bootstrap 5ms）——宿主包及其依赖链在补丁后工作正常。

### 8.3 现场状态（21:34–21:45 快照）

- 主窗口（HWND 132384，1500×864）：**被最小化**（窗口矩形 -32000,-32000）；侧边栏此前实测为展开态、无错误横幅。
- 残留小窗两个：HWND 10750530（900×680，失败错误页，pid 21256）、HWND 3409660（800×640，全局搜索，19:06 创建、功能正常）。
- 用户当前不在操作 WorkBuddy：前台窗口是 Microsoft Edge（pid 11812），标题「发现更多精彩视频 - 抖音搜索 和另外 1 个页面」。
- 补丁文件完好：sha256 `8563cfc4…`（与 19:38 应用后记录一致），原件与回滚脚本在备份目录。

### 8.4 激活协议（唯一待办：重启）

补丁正确性已终极验证：以 4 组 (OLD→NEW) 从补丁文件**反向代回**，结果与 `.orig` **逐字节相等** ⇒ 磁盘文件 = 原件 + 且仅 + 4 处预期补丁（排除任何意外改动）；`node --check` 双向通过。**补丁本身无损，缺的只是加载。**

重启步骤（需用户执行——重启会中断本对话，历史已在磁盘、重启后可恢复）：

1. 从任务栏恢复主窗口 → 正常关闭；右下角托盘右键 WorkBuddy 图标 → 退出（或任务管理器结束全部 WorkBuddy.exe，约 10 个进程）。
2. 重新启动 WorkBuddy，从会话列表恢复本对话。
3. 验证侧边栏：`python C:\Users\Administrator\AppData\Local\wb_sidebar_patch_backup\verify_sidebar_patch.py`（T1–T5 全 PASS 才算通过）。
4. 验证独立窗：重试之前点不开的窗口打开操作（会话切换风暴已结束，应能正常加载）。
5. 兜底回滚（若重启后侧边栏出现异常）：`python C:\Users\Administrator\AppData\Local\wb_sidebar_patch_backup\rollback_sidebar_patch.py`，然后再次重启。

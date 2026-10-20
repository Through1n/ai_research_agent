# Round 13 桌面版侧边栏「收起后找不到展开入口」：根因定位与修复

> 父代（team-lead）独立排查记录。关联用户反馈链：「这次侧边栏收起之后还是没找到展开的选项」（附主界面截图：侧边栏收起态、左上角无任何展开控件）。
> 与 `round13_lead_desktop_white_screen.md` 是同一轮里的**两个相互独立的 bug**：同属桌面外壳层，但根因无关（白屏是 WebView2 GPU 崩溃，本文是 CSS 继承埋控件）。

## 1. 结论 TL;DR

- **现象**：桌面版侧边栏一旦收起，界面上找不到任何展开入口（用户截图实证），只能重启应用。
- **根因**：`ui/desktop_chrome.py` 在桌面模式注入的 CSS 把 `header[data-testid="stHeader"]` 与 `[data-testid="stToolbar"]` 整体设为 `visibility: hidden`；而 Streamlit 1.63 中**侧边栏收起态唯一的图形展开按钮 `stExpandSidebarButton` 正好渲染在 stHeader → stToolbar 内**，被 visibility 继承链连带埋掉。
- **证据**：从本机 streamlit 1.63.0 静态包 `index.ByR4Z2EF.js` 的 `hb` 组件逐层确认 DOM 层级与渲染条件（`d = hasSidebar && !isSidebarOpen` → 按钮**仅在收起态存在**）。
- **修复**（`ui/desktop_chrome.py`）：显式放行该按钮（`visibility: visible !important`，双 testid 兼容 1.63 与旧版）+ 悬停底色提升可发现性；两条隐藏规则本身不动，桌面壳观感不变。
- **验证**（2026-10-20 23:20 实测）：新增单测 7/7；真实 Chromium DOM 级验证 16/16（含**修复前版本复现 bug** 与修复后可点击）；四门禁全绿——compileall OK、unittest **181/181**、smoke_ui 9 页面 0 异常、smoke_read **61/61**。
- **边界**：探针跑在 WorkBuddy 的 Job object 内，起不了 pywebview 桌面壳（见白屏报告 §6），**真机桌面版效果待用户在游戏/日常状态下启动确认**。

## 2. 现象与触发条件

- 用户截图：AI4S 主界面，侧边栏收起，左上角无展开控件，主区无「☰」类入口。
- **浏览器开发模式无此问题**：外壳 CSS 仅桌面模式注入（`ui/desktop_chrome.py` 的 `apply()` 由 `app.py:51` 调用，门控 `DESKTOP_MODE or AI4S_DESKTOP=1`）→ 桌面版必现、开发模式零影响。这也解释了为什么此前多轮测试都没抓到它。
- 侧边栏**展开态**同样无此问题：展开时 stToolbar 根本不渲染（见 §3.2），隐藏 header 零代价。只有「收起」这一个动作会触发。

## 3. 根因

### 3.1 DOM 层级证据（streamlit 1.63.0 前端包，逐层核对）

`Lib/site-packages/streamlit/static/static/js/index.ByR4Z2EF.js` 中的头部组件 `hb`（压缩代码，已格式化摘录）：

```js
hb = ({hasSidebar: e, isSidebarOpen: t, onToggleSidebar: n, ...}) => {
  let u = o && !t,              // 有 logo 且侧栏收起
      d = e && !t,              // 有侧栏且侧栏收起 → 渲染展开按钮
      f = c && i,               // 工具栏右侧内容
      p = u || d || r || f;     // 任一存在才渲染 stToolbar
  return s(lb, {className: `stAppHeader`, "data-testid": `stHeader`, ...,
    children: p ? s(ub, {className: `stAppToolbar`, "data-testid": `stToolbar`, ...,
      children: a(fb, {children: [
        a(pb, {children: [u ? s(cb, {children: o}) : null,
          d && s(db, {children: s(Yr, {kind: G.HEADER_NO_PADDING, onClick: n,
            "data-testid": `stExpandSidebarButton`,                       // ← 展开按钮
            children: s(U, {size: `xl`, iconValue: `:material/keyboard_double_arrow_right:`})})})]}),
        r, f ? s(mb, {children: i}) : null]})}) : null})}
```

层级归纳：

```
stAppViewContainer
├── stSidebar（展开态才可见）
│   └── stSidebarContent → stSidebarHeader → stSidebarCollapseButton（「«」收起按钮，在侧栏内部，不受影响）
└── stHeader ← 被 desktop_chrome.py 隐藏
    └── stToolbar ← 同样被隐藏
        └── fb → pb → db → stExpandSidebarButton ← 收起态唯一展开入口，被连带埋掉
```

> 注：`stSidebarCollapsedControl` 这个旧 testid 在 1.63.0 包内已不存在（grep 零命中），展开按钮已更名为 `stExpandSidebarButton`。旧项目记忆里「控件在 header 内」的判断方向正确，但 testid 已过时——按本仓纪律，修复同时兼容两个 testid。

### 3.2 为什么以前没暴露

| 侧栏状态 | `d = hasSidebar && !isSidebarOpen` | stToolbar 是否渲染 | 隐藏 header 的代价 |
|---|---|---|---|
| 展开 | false | 否（本项目无 logo/导航/右侧内容，`p` 全假） | 零——本来就没有可见内容 |
| 收起 | true | 是，且**只含展开按钮** | **把唯一入口埋掉** |

即：隐藏 header 在展开态是「免费」的桌面壳美化，直到用户第一次点收起才引爆。

## 4. 修复内容（`ui/desktop_chrome.py`，三处）

1. **放行规则**（`:39-42`）：`[data-testid="stExpandSidebarButton"]` 与旧版 `stSidebarCollapsedControl` 设 `visibility: visible !important`。
   - 为什么用「覆盖」而不是「删掉隐藏规则」：该按钮仅在收起态存在于 DOM，静态规则天然条件化，无需任何 JS 状态判断；
   - 为什么必须带 `!important`：Streamlit 的 emotion 运行时注入样式优先级可能压过裸规则；
   - **为什么隐藏规则不能用 `display: none`**：`display` 不参与继承、后代无法覆盖——谁把 `:28-29` 两条改成 display，本修复会静默失效（单测 `test_hidden_ancestors_use_visibility_not_display` 钉死这一点）。
2. **悬停底色**（`:45-49`）：取主题 `primaryColor #1f5fae` 的 8% 不透明度圆角底。默认按钮只是个灰色箭头，用户原话是「找不到」——给一点可点击暗示。
3. **模块 docstring 事故记录**（`:1-15`）：写清根因、DOM 依据与「改前先读测试」的指引，防止下一个人再踩。

## 5. 验证（分层，2026-10-20 23:20 实测）

| 层 | 手段 | 结果 |
|---|---|---|
| 单测 | `tests/test_desktop_chrome.py`（新增 7 条：放行规则存在性 / visibility 而非 display / 悬停色 / 注释剥离器双向反测 / 三种模式门控） | **7/7 OK** |
| 真实浏览器 DOM | `scripts/_verify_sidebar_expand.py`：无头 Chromium 加载「按 1.63 结构复刻的 DOM」+「本模块真实 `_CSS`」，断言计算样式、几何位置、点击命中、悬停计算色 | **16/16 PASS** |
| 回归复现 | 同脚本用 git 历史版本 CSS 渲染 | 按钮 `visibility=hidden`、`is_visible()=False`——**用户反馈的 bug 被稳定复现**，证明测试对象没跑偏 |
| 四门禁 | compileall / `unittest discover` / `_smoke_ui.py` / `_smoke_read.py`（后两者串行） | OK / **181/181** / 9 页面 0 异常 / **61/61** |

截图（`docs/shots/round13_sidebar_expand_{bug,fixed}.png`）：**按 Streamlit 1.63 前端结构复刻的 DOM + 桌面外壳真实 CSS，非应用真实截图**（合成页面标题已内置标注，避免图注撒谎）。左为修复前（左上角空白），右为修复后（`»` 按钮就位且悬停有底色）。

同时按纪律同步了漂移的测试计数：`docs/technical_doc.md` 与 `README.md` 中的「单元测试 18/18」「阅读页 60/60」实测已分别为 **181/181** 与 **61/61**（我的改动使单测从 174 增至 181，旧文档数字此前多轮未随涨随改）。

## 6. 边界与待办

1. **真机桌面版待用户侧确认**：我的探针在 WorkBuddy Job object 内无法启动 pywebview 壳（白屏报告 §6 已论证），以上验证是「真实 CSS + 复刻 DOM」，差最后一公里——请用户在桌面版点一次收起、再点左上角 `»` 展开，确认入口出现且侧栏正常开合。
2. **退守方案（若真机仍有问题）**：在 `ui/sidebar.py` 主区加显式「☰ 展开侧边栏」按钮。需要客户端读取 `localStorage` 的 `stSidebarCollapsed-<appId>` 或轮询 DOM，复杂度明显更高，暂不实施；真机验证失败再启动。
3. **与白屏修复的关系**：两者独立（CSS 继承 vs GPU 崩溃），但同属桌面外壳层；若用户侧白屏修复（`--disable-gpu`）也未生效，会同样表现为「打不开」，需按白屏报告的症状分支区分。

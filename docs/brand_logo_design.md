# MoreHand 品牌徽标 · 样式设计与应用方案

> 对应产品需求（2026-10-25）：为软件角标设计具体图片样式并应用到界面，要求
> ① 风格贴合 MoreHand 产品特征、融入 AI agent 视觉元素、专业协调；
> ② 展示内容（图片 + 文字）限定在明确的最大框定范围内，不得溢出；
> ③ 内容超出框定范围时支持框内上下滑动，或提供点击进入详情的交互；
> ④ 输出完整样式设计与实现方案，多尺寸清晰、交互流畅。
>
> 本文是该需求的**唯一设计文档**；侧栏「品牌徽标」区块的图注直接指向本文件。
> 配套资产：`assets/make_logo.py`（生成器）、`assets/logo.svg`（矢量源）、
> `assets/logo.png`（1024² 主图）、`assets/icon.png`（256²）、`assets/icon.ico`（7 档）。
> 配套验真：`scripts/_verify_brand_badge.py`（5 步几何 / 计算样式断言，全部实测通过）。

---

## 1. 设计概念：「多一只手的 AI 帮手」变成可识别的形

MoreHand 的产品含义是「多一只手的 AI 帮手」。徽标把这句口号落成一个可缩到
16×16 仍能读出的形：

| 视觉元素 | 产品语义 | AI agent 语义 |
|---|---|---|
| squircle 圆角方底 | 现代桌面应用的图标惯例（iOS/macOS/Windows 11 同款形制），专业、不突兀 | — |
| 品牌蓝对角渐变 | 与界面强调色 `#1f5fae` 同色相的深浅两端，图标与界面「是一家」 | — |
| 抽象向上手掌（五根圆头粗线） | MoreHand 的 **Hand**：伸出来帮忙的那只手 | 手掌即**神经网络**：五根手指 = 五条链路，关节节点 = 能力单元，掌缘弧把它们收成一张网 |
| 关节浅色节点 | 手指的关节 | 网络中的**节点**（能力） |
| 右上四角星 spark | 灵感、灵光一现 | AI **生成**动作的通用记号（各类 AI 产品的共同视觉语言） |

整体只用**品牌强调色系 + 白/浅蓝白**，不引入第三个色相——这与界面层「严禁第三色相」
的设计系统约束（`ui/design.py` 唯一真源）一致，保证图标放在任务栏、侧栏、页签里都像同一个产品。

---

## 2. 设计令牌

### 2.1 色板（`BLUE_LIGHT@assets/make_logo.py:34` 起）

| 令牌 | 值 | 用途 |
|---|---|---|
| `BLUE_LIGHT` | `#2F7CC9` | 渐变起点（左上），比界面强调色 `#1f5fae` 亮一档——图标底要压住白色线条，太深则白线发闷 |
| `BLUE_DEEP` | `#174E8E` | 渐变终点（右下） |
| `WHITE` | `#FFFFFF` | 手指线、指尖、spark |
| `NODE_TINT` | `#D6E6FA` | 关节节点（细模专属）：比纯白低一档对比，让「节点」在视觉上从属于手指主线 |

渐变方向为左上 → 右下对角（`(x+y)/2` 归一化，`assets/make_logo.py:62` `_gradient_bg`），
与 spark 的右上位置错开，保证 spark 落在最亮的一端。

### 2.2 几何（归一化坐标，画布 1024²）

| 参数 | 值 | 说明 |
|---|---|---|
| `CANVAS` | 1024 | 主图边长（`assets/make_logo.py:39`） |
| `CORNER_RATIO` | 0.219 | squircle 圆角比例 224/1024（`assets/make_logo.py:40`） |
| `FINGERS` | 5 组 (起点, 终点) | 拇指向左外张开，中指最长，见 `assets/make_logo.py:44` |
| `PALM_ARC` | 二次贝塞尔 3 点 | 掌缘弧，把五根散线收成手掌轮廓（`assets/make_logo.py:51`） |
| `SPARK` | (0.80, 0.24) | spark 中心，右上、不压手（`assets/make_logo.py:41`） |

手指全部用**圆头线**绘制：`_round_line()`（`assets/make_logo.py:74`）先画线段再在两端补等径圆，
避免小尺寸下端头被像素栅栏切断——这是 16px 下「五根手指还数得出来」的关键之一。

---

## 3. 三档「光学尺寸」：多尺寸清晰的实现方式

徽标要在任务栏（16px）、Alt+Tab（32/48px）、侧栏（46px）、页面页签（favicon 32px）、
徽标卡（150px）五处出现。**线性缩放不可行**：1024² 的图直接缩到 16² 会糊成一团蓝块，
内部细节（节点/spark/弧）在 16px 下全部变成噪点。

因此 `draw_logo(size)`（`assets/make_logo.py:102`）按目标尺寸分三档独立绘制，参数不按比例缩放：

| 档位 | 尺寸 | 笔画宽 | 内容 | 设计理由 |
|---|---|---|---|---|
| 细模 | ≥ 64 | `size×0.055` | 掌缘弧 + 关节节点 + 指尖 + spark | 尺寸够大，全细节 = 「手 + 网络 + 生成」三层语义都在 |
| 中模 | 32–63 | `size×0.055` | 掌缘弧 + spark，**去关节节点** | 节点半径缩到 ~1px 时会闪烁成灰点，反伤清晰度 |
| 剪影模 | < 32 | `size×0.105` | **只留五根加粗圆头线**，去弧/节点/spark | 16px 下任何内部细节都是噪点；宁可只要「一只手」的剪影 |

笔画加粗系数 0.105 ≈ 细模的 1.9 倍：小尺寸下人眼对线宽的感知按面积而非线性衰减，
不加粗就会「看不见」。下限 `max(3, …)` 保证 16px 时笔画仍有 3px（不到 3px 时抗锯齿会吃掉整根线）。

`icon.ico` 内每个尺寸都是**单独绘制**后塞进容器的（不是把大图缩一缩），
见 `ICO@assets/make_logo.py:225` 注释与 `_write_ico()`（`assets/make_logo.py:184`）：
手写 ICO 容器 `ICONDIR(6) + N×ICONDIRENTRY(16) + N×PNG 数据`，PNG 条目 Vista+ 通用；
256 的宽高字段按规范写 0。不用 Pillow 的 `save(sizes=[...])`（那条路是把大图**缩**到各尺寸，
16px 必糊）；也不用 `save_all=True`（本机 Pillow 的 ICO 编码器未注册 SAVE_ALL，直接 KeyError）。

六尺寸实测预览（已入库证据）：

```
docs/shots/_brand/logo_optical_sizes.png    256 / 64 / 48 / 32 / 24 / 16 六连图
docs/shots/_brand/badge_card.png            侧栏徽标卡元素截图（有界框 + 滚动）
docs/shots/_brand/badge_in_sidebar.png      侧栏品牌区 + 展开后的徽标卡
```

判据：每档都能独立读出「一只手」的剪影；16px 下能数出五根手指、不糊成色块。

---

## 4. 生成与复现

```bash
& "C:\Users\Administrator\anaconda3\envs\ai_research\python.exe" assets\make_logo.py
```

输出四个文件（`assets/make_logo.py:211` `main`）：

| 文件 | 尺寸 | 去向 |
|---|---|---|
| `logo.svg` | 矢量（viewBox 0 0 1 1） | 设计稿复用；与 Pillow 几何一一对应（`_svg()`，`assets/make_logo.py:150`） |
| `logo.png` | 1024² | 徽标卡内的大图（data URI 源） |
| `icon.png` | 256² | Streamlit 页面图标 favicon、侧栏品牌区 |
| `icon.ico` | 16/24/32/48/64/128/256 | exe 窗口 / 任务栏 / Alt+Tab |

改徽标只需改 `make_logo.py` 顶部的令牌与几何再重跑——**不要手改 png/ico**，
下一轮生成会覆盖手改结果，且三处应用点会不一致。

---

## 5. 应用方案：同一张图，三处同源

需求④要求「实际应用到软件角标」。落地为三处，全部读同一份资产文件，
不复制、不改色、不另做版本：

### 5.1 任务栏 / 窗口角标（桌面版）

链路：`assets/icon.ico` → PyInstaller spec 的 `icon=`（`AI4SResearchAgent.spec:200`）
→ exe 内嵌图标 → Windows 任务栏 / 标题栏 / Alt+Tab。

pywebview 5.4 的 `create_window` 不支持 `icon` 参数，窗口图标只能由 exe 自身决定
（`create_window@desktop/window.py:142` 注释已说明）；开发态（直接跑源码）用系统默认图标，属预期。

### 5.2 页面页签图标（favicon）

`app.py:38` `_page_icon()` 调 `config.py:16` `brand_logo("icon.png")` 取仓库内
`assets/icon.png`，交给 `st.set_page_config(page_icon=…)`。取不到图时退回 🖐️，
不影响启动。

### 5.3 侧栏品牌区 + 「品牌徽标」区块

`ui/sidebar.py:347` `_brand_block()`：品牌名 / 副标题 + 46px 徽标图（`st.image`），
替换原来的纯文字 `st.title(BRAND)`。

`ui/sidebar.py:369` `_brand_badge_block()`：藏在「关于」折叠区里的徽标详情区块，
**默认收起、点击展开**（需求③的「点击进入查看详情」），内含 150px 徽标图 +
品牌名 / 副标题 + 构成 / 用色 / 光学尺寸 / 规格四节文字。

**关键实现：框内图片必须用 data URI。** 需求②要求图片与文字**一起**限定在框定范围内，
而 `st.image` 是 Streamlit 顶层元素，塞不进自建 HTML 容器——图片会跑到框外，
框只圈住文字。因此 `ui/common.py:1066` `brand_logo_data_uri()` 把图缩到 512² 见方
转 base64 data URI，以 `<img>` 形式放进同一个 `.ai-brand-badge` div 内
（lru_cache 缓存，重复渲染不重复编码）。

### 5.4 冻结态（exe）下的资产可见性

冻结态 `app.py` 跑在 `%APPDATA%\AI4SResearchAgent\app\`，读不到 exe 根目录的 `assets/`。
故 spec 把三份资产打进 app_bundle（`AI4SResearchAgent.spec:50` 起）：

```python
("assets/icon.png", "app_bundle/assets"),
("assets/logo.png", "app_bundle/assets"),
("assets/logo.svg", "app_bundle/assets"),
```

`config.py:16` `brand_logo()` 依次尝试「模块同级 assets/」与「cwd/assets/」两条路径，
两条都命中同一份文件，保证开发态与冻结态同源。

---

## 6. 需求②③的实现：有界框 + 框内滚动 + 点击看详情

### 6.1 有界框（`ui/common.py:258` `.ai-brand-badge`）

```css
.ai-brand-badge{
  max-height:var(--brand-badge-max-h,320px);   /* 明确的最大框定范围 */
  overflow-y:auto;overflow-x:hidden;           /* 超出才滚动；横向一律不允许溢出 */
  background:var(--c-surface);border:1px solid var(--c-border);border-radius:var(--r);
  padding:var(--s-4);margin:var(--s-2) 0;min-width:0;
  -webkit-overflow-scrolling:touch;            /* iOS/WebView 惯性滚动 */
  overscroll-behavior:contain;                 /* 滚到头不带动整页滚动 */
}
```

要点：

- `max-height` 而非 `height`：内容少时框自动收矮，不留一片空白；内容多时锁死 320px。
- `overflow-x:hidden` + `min-width:0`：横向**任何情况**都不溢出（宽内容如 base64 图
  由 `img{max-width:100%}` 兜底）。全站仅此一个构件允许出现滚动条
  （`.ai-brand-badge@ui/common.py:248` 注释写明：这是产品需求驱动的唯一例外）。
- `overscroll-behavior:contain`：移动端 / WebView 里滚到框底不会把整页带着走——
  这是「交互流畅」在小屏上的关键一条。
- 边框 / 圆角 / 内边距全部走 `--c-*`、`--r`、`--s-*` 令牌，与设计系统一致。

### 6.2 交互：点击进入详情

徽标卡整体位于「关于」折叠区内（`_brand_badge_block@ui/sidebar.py:513`），默认收起。用户点一下
「品牌徽标」才展开——首屏不被大图占位（符合首屏减法口径），
需要看规格的人又能完整看到四节文字 + 大图。图注引导到本文档：

> 完整设计规范与实现方案见 `docs/brand_logo_design.md`

### 6.3 验真：`scripts/_verify_brand_badge.py`（5 步，全绿）

验的不是「渲染没报错」，而是**计算样式与几何**（无头 Chromium 加载真实页面）：

| 步 | 断言 | 实测值 |
|---|---|---|
| 1 | 侧栏真有徽标图 + 品牌名 + 副标题 | 三元素均在 |
| 2 | favicon 指向徽标文件 | `/media/deb4855b….png`（即 `icon.png`） |
| 3 | 点击「品牌徽标」后徽标卡才出现 | 点击前不存在、点击后渲染 |
| 4 | `max-height` computed 生效；`overflow-y:auto`；横向不溢出；**内容确实超出** | `320px` / `auto` / `scrollWidth 173 ≤ clientWidth 173` / `scrollHeight 1249 > clientHeight 318` |
| 5 | 图片右缘 ≤ 框右缘；可滚动到底 | `220 ≤ 237`；`scrollTop` 可到 `931` |

第 4 步的「内容确实超出」是**防空转断言**：若内容不超出，这条检查会失败，
防止「锁了高度但根本没内容可滚」的假通过。第 5 步的图片右缘断言对应需求②
最容易被破坏的那一点（`st.image` 放框外时该断言立刻翻红——开发中确实这样翻过一次）。

---

## 7. 需求验收对照

| 需求 | 实现 | 验真 |
|---|---|---|
| ① 风格贴合产品特征 + AI agent 元素 + 专业协调 | squircle + 品牌蓝渐变 + 手掌=神经网络 + spark；色板不超界面色相 | 六尺寸预览目检；令牌与 `ui/design.py` 同源 |
| ② 图片与文字限定在最大框定范围内 | `.ai-brand-badge` `max-height` + `overflow-x:hidden` + 图走 data URI 进同框 | 第 4/5 步：横向不溢出、图片右缘 ≤ 框右缘 |
| ③ 超出可框内滑动或点击看详情 | `overflow-y:auto` + `overscroll-behavior:contain`；区块藏折叠区、点击展开 | 第 3/4/5 步：点击出现、`scrollHeight>clientHeight`、可滚到底 |
| ④ 完整设计文档 + 实现方案 + 多尺寸清晰 + 交互流畅 | 本文档 + `make_logo.py` 可复现 + 三档光学尺寸 + 三处应用点 | `scripts/_verify_brand_badge.py` 5 步全绿；`_verify_theme.py` / `_verify_layout_grid.py` / `_verify_iframe_assets.py` 门禁同步通过 |

---

## 8. 变更影响面（改徽标前必读）

改任何视觉参数前，按此清单同步，否则会「改了生成器但界面没变」或门禁假红：

1. `assets/make_logo.py` 顶部令牌 / 几何 → 重跑生成四件套；
2. 若动色板：`ui/design.py` 的强调色系、`ui/common.py` 徽标卡 CSS 里的令牌引用要一起看
   （目前徽标卡只用中性 + 令牌，不硬编码徽标色，改色板不会破坏卡片）；
3. 侧栏徽标卡内的「用色 / 光学尺寸 / 规格」四节文字是**手写常量**，改令牌后要手工同步文字；
4. 重打包 exe 才会换任务栏角标：`build_desktop.bat` → `dist\AI4SResearchAgent\`；
   只改 `assets/icon.ico` 不打包，运行中的 exe 任务栏图标不会变；
5. `scripts/_verify_brand_badge.py` 的断言选择器（`.ai-brand-badge`、`品牌徽标` 文案）
   若随改版变动，脚本要同步——AppTest / Playwright 均精确匹配 label。

---

## 9. 已知边界

- **开发态窗口图标是系统默认的**：pywebview 不支持 `create_window(icon=…)`，
  只有打包成 exe 后任务栏 / 标题栏才是新徽标。这是框架限制，非缺陷。
- **剪影模（<32px）不含 spark 与关节节点**：这是有意的 readability 取舍，
  16px 下三层语义不可能共存。需要完整语义的场景请用 ≥64px 的 `logo.png`。
- **`.build_id` 与徽标无关**：徽标资产随 app_bundle 分发，是否刷新取决于业务代码
  替换决策表（见技术文档 §3.4 与 `tests/test_desktop_app_copy.py`），
  只换图标不动业务代码时，老安装的任务栏角标仍要重打包 exe 才会变。

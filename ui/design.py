# -*- coding: utf-8 -*-
"""全站唯一设计令牌层（Design Token Layer）—— AI4S 科研助手视觉重做的地基。

=============================================================================
这个文件是全站视觉的**唯一真源**。任何页面、任何组件、任何 iframe 都不得再写死
颜色、圆角、间距、字号：一律引用这里的令牌（CSS 侧用 var(--x)，Python 侧用本模块
导出的常量）。
=============================================================================

一、定位与用法
--------------
- 页面侧：在页面渲染时调用一次 `apply()`（幂等），它会把 `TOKENS_CSS` 注入文档。
- iframe 侧（`components.v1.html`）：**CSS 变量穿透不进 iframe**（实测：srcdoc 是独立
  文档，取父窗口 `:root` 变量返回空，只会走 fallback）。因此每个 iframe 文档必须
  自带一份写死字面量的 `IFRAME_TOKENS_CSS`，用 `inject_into_head(html)` 注入。
- 内联样式：Python 侧写 style 字符串时，请用本模块常量（S1..S8 / R / CTL_H / FS_*）

二、受保护的既有约束（改动前必读，违反会打挂测试或破坏契约）
------------------------------------------------------------
1. **主色必须是 `#1f5fae`**，其 8% 柔和色必须是精确的 `rgba(31, 95, 174, 0.08)`。
   这是 `.streamlit/config.toml` 的 theme.primaryColor，也是浏览器级回归脚本
   `scripts/_verify_sidebar_expand.py` 按字符串精确断言的值。
2. **整份 CSS 禁止把 `display` 设成 none 值**（连注释里也不许出现该字面量——
   扫描器会连同注释一起检测，故本注释刻意不把它写全）。
   `tests/test_desktop_chrome.py:46-47` 有断言；历史上用它修外观曾静默失效
   （display 不参与继承，后代无法覆盖）。本文件全局不隐藏任何元素。
3. **`.streamlit/config.toml` 只允许同步三个中性值**（backgroundColor /
   secondaryBackgroundColor / textColor）——它们必须与下面的令牌同值，否则
   Streamlit 原生组件在令牌 CSS 生效前会闪一帧旧色。**primaryColor 禁止改动**
   （#1f5fae 被 `scripts/_verify_sidebar_expand.py` 按字符串精确断言）。
   `app.py` 与 `ui/desktop_chrome.py` 仍不得修改（`desktop/INTERFACE_CONTRACT.md`
   锁死窗口与侧边栏行为契约）。本模块是独立新文件，靠注入生效。
4. **DOM 里的字符串必须保持完整**：`.ai-ellipsis` / `.ai-clamp-N` 都是**纯 CSS 截断**，
   `ellipsis()` 只包一层带 `title` 的 span，绝不截断文本本身——AppTest 用完整文件名
   去 select，Python 侧截断会静默打挂用例。
5. **模块导入时零副作用**：不在模块级执行任何 `st.*` 调用，`streamlit` 在函数内惰性
   导入。
6. **配色政策（2026-10-24 西式简洁重构立）**：界面层（页面 / 侧边栏 / 弹层 /
   图谱可视化）只允许「中性灰阶 + 唯一强调色 #1f5fae 系」——任何第三种色相
   （第二强调蓝、荧光笔黄、装饰渐变）都算回归，由 `scripts/_verify_theme.py`
   静态扫描 + 计算样式双断言看守。**唯一豁免**：阅读页 PDF 文档面
   （`services/translate.py` 的 native_page_html / bilingual_html）保留
   「荧光笔」语义色（`.blk:hover` 半透明黄 + `.mk` 标题标记）——那是内容级标注
   （模仿纸上荧光笔），不是界面装饰；该面同时保留 Times 衬线字体还原纸质文档。

三、关键实测证据（2026-10-24，streamlit 1.63.0 + 真机 headless Chromium）
-------------------------------------------------------------------------
- `[data-testid="stHorizontalBlock"]` 默认 `data-test-wrap="true"`、`gap:16px`、
  `flex-wrap:wrap`；子列 testid 是 **`stColumn`**（不是 `column`），三列时 computed
  `flex: 1 1 calc(33.3333% - 10.667px)`、宽 416px（1440 视口 / 80px 内边距）。
- `[data-testid="stVerticalBlock"]` 默认 `gap:16px` —— 已是 8 的倍数，本层只做"钉死"
  不改值，避免叠加成双倍间距。
- `.block-container`（= `[data-testid="stMainBlockContainer"]`）默认内边距
  `96px 80px 160px`、`max-width:none`。
- `[data-testid="stHeader"]` 实测 `position:absolute`、高 **60px**、`z-index:999990`、
  背景透明。若顶部内边距只给 32px，首个元素 top=32 而 header 底边=60 → **被压住 28px**
  （实测）。故顶部内边距 = 视觉留白 32px + header 让位 60px，见 `--header-h`。
- `[data-testid="stTabs"] [role="tablist"]` 默认 `flex-wrap:nowrap` +
  `overflow:auto hidden` → 标签多时会横向溢出，必须改成 wrap。
- 精确 testid 选择器（必要时叠加稳定类，见下）**不需要 `!important`** 即可压过
  Streamlit 的 emotion 类；而 `button{}` / `input{}` 这类通用选择器不加 important
  会失败。本文件因此只用精确选择器。
- `[data-testid="stSelectbox"]` 内部**没有** `data-baseweb="select"`（1.63 已换成
  react-aria 实现）：真实层级是 `stSelectbox > div.react-aria-ComboBox > div > input+button`，
  实测根高 68px（含 24px 标签）。故下拉框只能从 `stSelectbox > div` 这一层下手。

四、为什么有些规则写成 `[data-testid="x"].x` 这种"属性 + 稳定类"双命中形式
---------------------------------------------------------------------------
emotion 注入的是**单类选择器**（`.st-emotion-cache-xxxx`，特异度 0,1,0）。单写一个
属性选择器 `[data-testid="x"]` 特异度也是 0,1,0，胜负取决于 `<style>` 插入顺序——不
可控。**叠加稳定类名**（Streamlit 给每个元素额外挂了 `stHorizontalBlock` / `stColumn`
这类稳定类）把特异度抬到 0,2,0，就能在不使用 `!important` 的前提下稳定取胜。
本文件**仅**在实测证明"加了稳定类仍不生效"的地方使用 `!important`。
实测结论：**当前 0 处需要**。2026-10-24 在真 streamlit 页（1440 视口）逐项量过
计算样式，下列规则全部以纯精确选择器生效（括号内为实测 computed 值）：
  · 主容器 max-width 1280px / padding 92px 40px 48px  ✓
  · stHorizontalBlock gap 16px / flex-wrap wrap        ✓
  · stColumn min-width min(280px,100%)（basis 保持 calc(33.3333% - 16px)，权重未抹平）✓
  · 按钮 height 36 / radius 8px / padding 0 16px / box-shadow none ✓
  · 输入框 RootElement 36px、内层 field 34px                        ✓
  · metric / uploader / alert / container 圆角 8px、内边距 16px     ✓
  · tablist flex-wrap wrap、overflow-x visible                      ✓
若将来某条规则失效，正确的修法是先做同样的真机实测，再决定是否加 `!important`，
并在改动处写下实测依据——不要凭感觉加。

五、关于 `st.container(border=True)` 的实证结论与本层的折中
-------------------------------------------------------------
真机 DOM 取证（storage/scratch 探针，已删除）：普通 `st.container()` 与
`st.container(border=True)` **都**渲染成
`[data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"]`，且外层
LayoutWrapper 的 class/属性**完全一致**（`st-emotion-cache-18kf3ut e1dhag914`，
`width="100%" height="auto" overflow="visible"`）。唯一差异是子级 emotion 哈希类：

    普通容器  → st-emotion-cache-q25c8l  border:0px none   radius:0px    padding:0px
    带边框容器 → st-emotion-cache-1egzdpf border:1px solid  radius:8px    padding:15px

哈希类是构建期产物、跨版本/跨构建必变，**不能作为选择器**。而 `[data-testid="stLayoutWrapper"]`
在本项目里还同时包裹 `stHorizontalBlock`、`stExpander`，无法用父级区分。
→ 因此采取折中 **(ii)**：**不给它加边框**（否则全站每个普通 container 都会长出一道
边框，属于"多余装饰"），只把圆角与内边距统一到令牌（见下方 §6 规则）。
代价：带边框容器与普通容器在视觉上不再有区别；若将来需要区分，正确做法是等
Streamlit 暴露稳定标识（如 `stVerticalBlockBorderWrapper`）或在业务层显式加
`.ai-card` 类，而不是依赖 emotion 哈希。

六、栅格为什么用 `min-width` 而不是 `flex-basis: 280px`
--------------------------------------------------------
指令原文给的写法是 `flex: 1 1 var(--col-basis); min-width: 0; width: auto`。
实测发现两个问题：
1. 覆盖 `flex-basis` 会把 `st.columns([2,1])` 这类**权重**抹平成等宽（Streamlit 用
   `calc(百分比 - gap)` 编码权重）；
2. `min-width: 0` + 百分比 basis 时，flex 项会一路收缩而**永不换行**——正是需求要
   杜绝的"换行塌陷 / 挤压"。
故改为：**不覆盖 flex-basis**，用 `min-width: min(var(--col-basis), 100%)` 提供下限。
`min()` 保证下限永不超过容器宽度（不会撑破），同时让"容器装不下 N 列"时真正触发
`flex-wrap` 换行。防长内容撑破则由两条机制承担，而非 `min-width:0`：
  · 所有文本容器 `overflow-wrap: anywhere`（把 min-content 收到字符级）；
  · `pre/code`、表格、图表容器统一 `max-width:100%` + `overflow-x:auto`。
实测：1440 → 三列并排；768 → 换行成 2+1（`offsetTop` 递增）；390 → 每列独占一行。

- 2026-10-24 西式简洁重构（精细化第二轮，用户要求"中性色 + 单一强调色 + 无衬线
  + 去装饰 + 任何情况整齐"）：
  · 色板由蓝灰调（#f7f9fc / #e3e8ef / #1d2733 / #5a6678 / #8a97a8 / #eef4fb /
    #cdd6e2）整体更换为真中性锌灰阶（#fafafa / #e4e4e7 / #18181b / #52525b /
    #a1a1aa / #f4f4f5 / #d4d4d8），强调色不动；config.toml 三个中性值同步。
  · pills 实测渲染为 `stButtonGroup`（`.stPills` 类在 1.63 已不存在），其默认
    9999px 全圆角 + 32px 高与"组件圆角/尺寸一致"冲突 → 收敛到 --r / --ctl-h。
  · 按钮与控件标签补 `overflow-wrap:anywhere` + `min-width:0`：长标签换行
    而非撑破栅格（"任何情况下整齐"的收尾规则）。
  · 页面级残留（第二强调蓝 #3b6fb6、分区头渐变、图谱第三套金/蓝配色、写死灰阶
    #888/#333/#999/#173a63、6/10px 圆角、10/20px 间距）全部清入令牌或中性阶，
    由 `scripts/_verify_theme.py` 静态扫描 + 计算样式断言看守。
"""
from __future__ import annotations

import html as _html
import re as _re

__all__ = [
    # Python 侧常量
    "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8",
    "R", "R_MARK", "CTL_H",
    "FS_DISPLAY", "FS_TITLE", "FS_BODY", "FS_CAPTION", "FS_MICRO",
    "LH_DISPLAY", "LH_TITLE", "LH_BODY", "LH_CAPTION", "LH_MICRO",
    # CSS
    "TOKENS_CSS", "IFRAME_TOKENS_CSS",
    # 函数
    "tokens_css", "iframe_tokens_css", "inject_into_head",
    "apply", "ensure", "ellipsis", "ratio_box",
]


# =============================================================================
# (a) Python 侧常量 —— 供未来内联样式使用，保证全站同一套数
#     间距严格 8px 倍数，仅 S1=4、S3=12 是允许的半档。
# =============================================================================
S1, S2, S3, S4 = 4, 8, 12, 16
S5, S6, S7, S8 = 24, 32, 40, 48

R = 8            # 统一圆角
R_MARK = 2       # 仅供 PDF 批注这类"功能性标记"使用，不参与卡片/控件圆角
CTL_H = 36       # 按钮 / 输入框等单行控件的统一高度

FS_DISPLAY, FS_TITLE, FS_BODY, FS_CAPTION, FS_MICRO = 22, 16, 13.5, 12, 11.5
LH_DISPLAY, LH_TITLE, LH_BODY, LH_CAPTION, LH_MICRO = 1.3, 1.4, 1.65, 1.5, 1.5


# =============================================================================
# (b) TOKENS_CSS —— 页面级 CSS：:root 变量层 + 全部规则
# =============================================================================
TOKENS_CSS = r"""
/* ==========================================================================
   AI4S 全站设计令牌（ui/design.py :: TOKENS_CSS）—— 唯一真源，勿在别处写死样式
   纪律：① 禁止把 display 设成 none 值（含注释，故此处不写出该字面量）；
   ② 主色 #1f5fae / 8% 态 rgba(31, 95, 174, 0.08)
   为浏览器级脚本按字符串精确断言的值，禁止改写；③ 去阴影改 1px 边框，
   唯一保留的 box-shadow 是键盘焦点环（可访问性，非装饰，见 §7）。
   ========================================================================== */

/* ---- 1. 变量层 ---------------------------------------------------------- */
:root{
  /* 颜色：真中性色阶（不掺蓝相）+ 唯一强调色。
     2026-10-24 西式简洁重构：原蓝灰调（#f7f9fc/#e3e8ef/#1d2733/#5a6678/#8a97a8）
     整体换成中性灰阶（锌灰系），强调色仍是唯一彩色 #1f5fae；
     config.toml 的 backgroundColor/secondaryBackgroundColor/textColor 三值必须同步。 */
  --c-bg:#fafafa;                        /* 页面底色（config.toml backgroundColor） */
  --c-surface:#ffffff;                   /* 卡片 / 输入面 */
  --c-subtle:#f4f4f5;                    /* 柔和块面（config.toml secondaryBackgroundColor） */
  --c-border:#e4e4e7;                    /* 默认分隔线 */
  --c-border-strong:#d4d4d8;             /* 可交互元素描边 */
  --c-text:#18181b;                      /* 正文（config.toml textColor） */
  --c-text-2:#52525b;                    /* 次级文字 */
  --c-text-3:#a1a1aa;                    /* 辅助 / 占位 */
  --c-on-accent:#ffffff;                 /* 强调色底上的文字（徽章 / 主按钮） */
  --c-accent:#1f5fae;                    /* 唯一强调色（config.toml primaryColor） */
  --c-accent-hover:#1a4f92;              /* 强调色按压态 */
  --c-accent-soft:rgba(31, 95, 174, 0.08);   /* 8% 柔和色：被回归脚本精确断言，勿改 */
  --c-accent-ring:rgba(31, 95, 174, 0.28);   /* 焦点环 */

  /* 间距：8px 基准，S1/S3 为半档 */
  --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px;
  --s-5:24px; --s-6:32px; --s-7:40px; --s-8:48px;

  /* 圆角 / 控件 */
  --r:8px;
  --r-mark:2px;                          /* 仅 PDF 批注等纯功能性标记 */
  --ctl-h:36px;

  /* 字阶与行高（28 / 18 / 13.5 / 12 / 11.5，层级单调递减）。
     2026-10-24 二次放大：用户反馈「重启后没啥变化」——Round 21 首轮中性化在
     Round 13 浅色基底上只是细微改良（#f7f9fc→#fafafa 肉眼几乎无感）。西式简洁的
     两个肉眼可辨信号是**留白**与**层级**：display 22→28、title 16→18，配合分区
     间距 32→40 与侧边栏轻灰分层，一眼可辨。 */
  --fs-display:28px;  --lh-display:1.3;
  --fs-title:18px;    --lh-title:1.4;
  --fs-body:13.5px;   --lh-body:1.65;
  --fs-caption:12px;  --lh-caption:1.5;
  --fs-micro:11.5px;  --lh-micro:1.5;

  /* 字体：统一无衬线，中文优先雅黑 / 苹方 */
  --font-sans:-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
              "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei",
              "Helvetica Neue", Arial, sans-serif;

  /* 布局 */
  --layout-max:1280px;                   /* 主区最大宽度 */
  --col-basis:280px;                     /* 列的最小基准宽度（低于此值触发换行） */
  --header-h:60px;                       /* stHeader 实测高度，用于顶部让位 */
  --pad-top:32px; --pad-x:40px; --pad-bottom:48px;   /* ≥1200px 档 */
}

/* ---- 2. 基础复位（只做盒模型兜底，不改任何组件外观） -------------------- */
*,*::before,*::after{box-sizing:border-box;}
html,body{font-family:var(--font-sans);}
body{background:var(--c-bg); color:var(--c-text);}

/* ---- 3. 主容器：最大宽度 + 居中 + 响应式内边距 -------------------------- */
/* 顶部内边距 = 视觉留白 --pad-top + header 让位 --header-h。
   实测：只给 32px 时首个元素 top=32 被 60px 高的 stHeader 压住 28px。 */
.block-container[data-testid="stMainBlockContainer"]{
  max-width:var(--layout-max);
  margin-inline:auto;
  padding-top:calc(var(--pad-top) + var(--header-h));
  padding-left:var(--pad-x);
  padding-right:var(--pad-x);
  padding-bottom:var(--pad-bottom);
}

/* ---- 4. 栅格：防挤压 / 防错位核心 --------------------------------------- */
[data-testid="stHorizontalBlock"].stHorizontalBlock{
  display:flex;
  flex-wrap:wrap;                 /* 实测默认已是 wrap，显式钉死防回归 */
  gap:var(--s-4);                 /* 实测默认 16px，同值钉死不叠加 */
  align-items:stretch;
  min-width:0;
}
/* ⚠️ 子列 testid 是 stColumn（不是 column）。
   不覆盖 flex-basis（那会抹平 st.columns([2,1]) 的权重）；
   用 min-width 下限触发换行，min() 保证下限不超过容器宽、不会撑破。 */
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"].stColumn{
  flex-grow:1;
  flex-shrink:1;
  min-width:min(var(--col-basis), 100%);
  width:auto;
}

/* 垂直节奏：实测默认 gap=16px，同值钉死，避免叠加成双倍 */
[data-testid="stVerticalBlock"].stVerticalBlock{
  gap:var(--s-4);
  min-width:0;
}
[data-testid="stElementContainer"]{min-width:0;}

/* ---- 5. 文本：字阶层级 + 溢出处理 --------------------------------------- */
/* 所有文本容器：断行但不破坏英文单词（不用 break-all） */
[data-testid="stMarkdown"],[data-testid="stMarkdown"] p,
[data-testid="stMarkdown"] li,[data-testid="stMarkdown"] h1,
[data-testid="stMarkdown"] h2,[data-testid="stMarkdown"] h3,
[data-testid="stMarkdown"] h4,[data-testid="stText"],
[data-testid="stCaptionContainer"],[data-testid="stHeading"]{
  overflow-wrap:anywhere;
  word-break:break-word;
  min-width:0;
}
[data-testid="stHeading"] h1,[data-testid="stMarkdown"] h1{
  font-size:var(--fs-display); line-height:var(--lh-display);
  font-weight:700; color:var(--c-text);
  margin:0 0 var(--s-3); letter-spacing:-0.01em;
}
[data-testid="stMarkdown"] h2{
  font-size:var(--fs-title); line-height:var(--lh-title);
  font-weight:700; color:var(--c-text); margin:var(--s-5) 0 var(--s-2);
}
[data-testid="stMarkdown"] h3,[data-testid="stMarkdown"] h4{
  font-size:var(--fs-title); line-height:var(--lh-title);
  font-weight:600; color:var(--c-text); margin:var(--s-4) 0 var(--s-2);
}
[data-testid="stMarkdown"] p,[data-testid="stMarkdown"] li{
  font-size:var(--fs-body); line-height:var(--lh-body); color:var(--c-text);
  margin-bottom:var(--s-4);
}
[data-testid="stMarkdown"] li{margin-bottom:var(--s-1);}
[data-testid="stCaptionContainer"]{
  font-size:var(--fs-caption); line-height:var(--lh-caption); color:var(--c-text-2);
}
[data-testid="stWidgetLabel"],[data-testid="stWidgetLabel"] p{
  font-size:var(--fs-caption); line-height:var(--lh-caption);
  color:var(--c-text-2); font-weight:500;
  overflow-wrap:anywhere; min-width:0;   /* 长标签（论文名/列名）不撑破控件 */
}
a{color:var(--c-accent); text-decoration:none;}
a:hover{text-decoration:underline;}

/* 纯 CSS 截断工具类：DOM 内字符串保持完整（AppTest 用完整文件名 select） */
.ai-ellipsis{
  display:block; max-width:100%;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.ai-clamp-2,.ai-clamp-3{
  display:-webkit-box; -webkit-box-orient:vertical; overflow:hidden; max-width:100%;
}
.ai-clamp-2{-webkit-line-clamp:2;}
.ai-clamp-3{-webkit-line-clamp:3;}
[title]{cursor:help;}

/* 代码块：横向可滚动而不是撑破栅格 */
pre,code{overflow-x:auto; max-width:100%;}
[data-testid="stCode"],[data-testid="stCode"],[data-testid="stMarkdownPre"]{max-width:100%;}

/* ---- 6. 图片与图表：比例与溢出规则 -------------------------------------- */
img{max-width:100%; height:auto; display:block;}
[data-testid="stImageContainer"],[data-testid="stImageContainer"] img{max-width:100%;}
/* 实测：iframe 外层句柄是 stIFrame，高度写在父 stElementContainer 上——
   只限制宽度，不动高度，避免与父容器高度计算打架。 */
[data-testid="stIFrame"]{max-width:100%;}
[data-testid="stIFrame"] iframe{max-width:100%;}
[data-testid="stPlotlyChart"],[data-testid="stVegaLiteChart"],
[data-testid="stMermaidChart"],[data-testid="stCustomComponentV1"]{
  max-width:100%; overflow:hidden;
}
[data-testid="stHtml"]{max-width:100%; overflow-x:auto;}
[data-testid="stTable"],[data-testid="stDataFrame"]{max-width:100%; overflow-x:auto;}
/* 固定比例容器（可选工具）；比例由调用方通过 --ai-ratio 传入 */
.ai-ratio{
  position:relative; width:100%;
  aspect-ratio:var(--ai-ratio, 16 / 9);
  overflow:hidden; border-radius:var(--r); background:var(--c-subtle);
}
.ai-ratio > *{width:100%; height:100%; object-fit:cover; display:block;}

/* ---- 7. 组件统一：按钮 / 输入 / 容器（全部精确 testid，去阴影改 1px 边框） */
/* 按钮 */
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-primary"]{
  min-height:var(--ctl-h);
  border-radius:var(--r);
  padding:0 var(--s-4);
  display:inline-flex; align-items:center; justify-content:center;
  font-family:var(--font-sans);
  font-size:var(--fs-body); line-height:var(--lh-body); font-weight:500;
  box-shadow:none;                       /* 去装饰性阴影 */
  min-width:0; overflow-wrap:anywhere;   /* 长标签换行而非撑破栅格 */
  transition:background-color .15s ease, border-color .15s ease, color .15s ease;
}
[data-testid="stBaseButton-secondary"]{
  background:var(--c-surface); color:var(--c-text);
  border:1px solid var(--c-border-strong);
}
[data-testid="stBaseButton-primary"]{
  background:var(--c-accent); color:var(--c-on-accent); border:1px solid var(--c-accent);
}
[data-testid="stBaseButton-secondary"]:hover{
  background:var(--c-accent-soft); border-color:var(--c-accent); color:var(--c-accent);
}
[data-testid="stBaseButton-primary"]:hover{
  background:var(--c-accent-hover); border-color:var(--c-accent-hover);
}
[data-testid="stBaseButton-secondary"]:active,
[data-testid="stBaseButton-primary"]:active{background:var(--c-accent-soft);}
/* pills / button_group：允许换行避免溢出；圆角与高度对齐按钮（Streamlit 默认
   9999px 全圆角 + 32px 高，与"组件圆角/尺寸一致"的要求冲突，此处收敛到 --r/--ctl-h）。
   1.63 的 st.pills 渲染为 stButtonGroup（.stPills 类已不存在，双写选择器防版本回退）。
   选中/未选两态都显式给令牌色：Streamlit 默认选中态是 primary 的 mix 近似蓝、
   未选态边框用主题 borderColor，都会跑出色板。 */
[data-testid="stButtonGroup"]{flex-wrap:wrap; gap:var(--s-2);}
[data-testid="stButtonGroup"].stButtonGroup button,
.stPills button{
  border-radius:var(--r);
  min-height:var(--ctl-h);
  overflow-wrap:anywhere;
  font-family:var(--font-sans);      /* 按钮 UA 默认 Arial，必须显式覆盖（门禁实测） */
  font-size:var(--fs-body); line-height:var(--lh-body); font-weight:500;
}
[data-testid="stBaseButton-pills"]{
  background:var(--c-surface); color:var(--c-text); border:1px solid var(--c-border-strong);
}
[data-testid="stBaseButton-pills"]:hover{
  background:var(--c-accent-soft); border-color:var(--c-accent); color:var(--c-accent);
}
[data-testid="stBaseButton-pillsActive"]{
  background:var(--c-accent); color:var(--c-on-accent); border:1px solid var(--c-accent);
}
[data-testid="stBaseButton-pillsActive"]:hover{
  background:var(--c-accent-hover); border-color:var(--c-accent-hover);
}

/* 输入框：⚠️ 实测证明必须同时设外层与内层，否则内容溢出盒子 */
[data-testid="stTextInputRootElement"],
[data-testid="stNumberInputContainer"]{
  height:var(--ctl-h);
  min-height:var(--ctl-h);
  padding-block:0;
  padding-inline:var(--s-3);
  border-radius:var(--r);
  box-shadow:none;
  display:flex; align-items:center;
}
/* 下拉 / 多选：1.63 无 data-baseweb="select"，实测层级 stSelectbox > div.react-aria-ComboBox。
   外层与内层同时给高度，否则内层 40px 会溢出 36px 的外框。 */
[data-testid="stSelectbox"] > div,
[data-testid="stMultiSelect"] > div{
  height:var(--ctl-h);
  min-height:var(--ctl-h);
  padding-block:0;
  border-radius:var(--r);
  box-shadow:none;
  display:flex; align-items:center;
}
[data-testid="stSelectbox"] > div > div,
[data-testid="stMultiSelect"] > div > div{
  height:100%; min-height:0;
  border-radius:var(--r);
  display:flex; align-items:center;
}
/* 多行文本域只给下限：st.text_area(height=...) 的业务高度必须保留 */
[data-testid="stTextAreaRootElement"]{
  min-height:var(--ctl-h);
  border-radius:var(--r);
  box-shadow:none;
}
[data-testid="stTextInputField"],
[data-testid="stNumberInputField"],
[data-testid="stTextArea"] textarea,
textarea{
  height:100%;
  min-height:0;
  font-family:var(--font-sans);
  font-size:var(--fs-body); line-height:var(--lh-body);
  border-radius:calc(var(--r) - 1px);
}
[data-testid="stTextArea"] textarea,textarea{
  min-height:var(--ctl-h);
  overflow-y:auto;
}

/* 容器：见文件头 §5 —— 不给 container 加边框，只统一圆角与内边距 */
[data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"].stVerticalBlock{
  border-radius:var(--r);
  padding:var(--s-4);
  min-width:0;
}

/* Expander */
[data-testid="stExpander"]{box-shadow:none; min-width:0;}
[data-testid="stExpander"] details,
[data-testid="stExpanderDetails"]{
  border:1px solid var(--c-border);
  border-radius:var(--r);
  background:var(--c-surface);
  box-shadow:none;
}
[data-testid="stExpander"] summary{
  border-radius:var(--r);
  padding:var(--s-2) var(--s-3);
  font-size:var(--fs-body); line-height:var(--lh-body);
  font-weight:600; color:var(--c-text);
}

/* Tabs：实测默认 nowrap + overflow:auto hidden → 标签多时横向溢出，必须 wrap */
[data-testid="stTabs"] [role="tablist"]{
  flex-wrap:wrap;
  gap:var(--s-2);
  overflow-x:visible;
}
[data-testid="stTabs"] [role="tab"]{
  border-radius:var(--r);
  padding:var(--s-1) var(--s-3);
  min-height:var(--ctl-h);
  font-size:var(--fs-body); line-height:var(--lh-body);
  display:inline-flex; align-items:center;
}
/* 表单控件 UA 默认字体是 Arial（按钮 / input / textarea / select 全中）：
   门禁实测 Chrome 不给这些控件继承 body 字体。作者样式天然压过 UA，
   用元素选择器统一兜底，保证"字体统一无衬线"在任何组件上都成立。 */
button,input,textarea,select{font-family:var(--font-sans);}

/* Metric */
[data-testid="stMetric"]{
  background:var(--c-surface);
  border:1px solid var(--c-border);
  border-radius:var(--r);
  padding:var(--s-3) var(--s-4);
  box-shadow:none; overflow:hidden; min-width:0;
}
[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] p{
  font-size:var(--fs-caption); line-height:var(--lh-caption); color:var(--c-text-2);
}
[data-testid="stMetricValue"]{
  font-size:var(--fs-display); line-height:var(--lh-display);
  font-weight:600; color:var(--c-text);
  overflow-wrap:anywhere; word-break:break-word;
}
[data-testid="stMetricDelta"],[data-testid="stMetricDelta"] div{
  font-size:var(--fs-caption); line-height:var(--lh-caption); color:var(--c-text-2);
}

/* 上传区 */
[data-testid="stFileUploader"]{box-shadow:none; min-width:0;}
[data-testid="stFileUploaderDropzone"]{
  border:1px dashed var(--c-border-strong);
  border-radius:var(--r);
  background:var(--c-subtle);
  padding:var(--s-4);
  box-shadow:none;
  font-size:var(--fs-caption); line-height:var(--lh-caption); color:var(--c-text-2);
}

/* 四类 alert：只统一圆角 / 内边距 / 字号，语义配色沿用 Streamlit */
[data-testid="stAlertContainer"],[data-testid="stAlert"]{
  border-radius:var(--r);
  padding:var(--s-3) var(--s-4);
  font-size:var(--fs-body); line-height:var(--lh-body);
  box-shadow:none;
}
[data-testid="stAlertContainer"] p,[data-testid="stAlert"] p{
  font-size:var(--fs-body); line-height:var(--lh-body); margin:0;
}

/* 侧边栏：轻灰底与主区 #fafafa 分层——白侧边栏+近白主区的区分度不足，
   是「没啥变化」的共犯之一；#f4f4f5 一档灰阶差即让结构可辨（西方简洁惯例） */
[data-testid="stSidebar"]{
  background:var(--c-subtle);
  border-right:1px solid var(--c-border);
}
[data-testid="stSidebarContent"]{
  padding:var(--s-5) var(--s-4);
}

/* ---- 6b. 侧边栏导航：把 st.radio 做成「导航行」而非表单单选 -----------------
   为什么改：radio 的原生外观是「圆点 + 文本」，加上菜单里的「0./1.」序号，
   第一眼像问卷控件而不是导航，是「没重设计」的最强视觉信号（实测截图确认）。
   DOM 实测（streamlit 1.63.0）：选项行 = label[data-testid="stRadioOption"]，
   选中态由属性 data-selected="true" 给出（无需 :has 猜状态）；圆点节点是
   「紧邻 stMarkdownContainer 之前的那个空 div」，用 :has(+ …) 精确命中。
   纪律：这里只把圆点的视觉占位收为 0（0 宽 0 高 + 透明），**不隐藏任何控件**，
   原生 radio input 仍在（视觉上由 Streamlit 自己裁成 1px），键盘与读屏可用。 */
/* 行节距：36px 行高 + 4px 间距 = 40px（8px 基准的整数倍）。Streamlit 默认 gap 会被
   压到 2px → 节距 38px，脱离间距令牌，实测（1440 视口）导航行 pitch 全为 38px。 */
[data-testid="stSidebar"] [data-testid="stRadioGroup"]{gap:var(--s-1);}
[data-testid="stSidebar"] label[data-testid="stRadioOption"]{
  display:flex; align-items:center; min-height:36px;
  padding:var(--s-1) var(--s-3); margin:0;
  border-radius:var(--r); cursor:pointer;
  color:var(--c-text-2);
  transition:background .12s ease, color .12s ease;
}
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover{
  background:var(--c-border); color:var(--c-text);
}
[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"]{
  background:var(--c-accent-soft); color:var(--c-accent); font-weight:600;
}
/* ⚠️ 文字色必须写成**显式状态色**，不能靠 inherit：Streamlit 在 label 与文字之间
   还有一层容器（实渲 DOM：label > div > [圆点] + [stMarkdownContainer] > p），
   那层容器自带正文色。`inherit` 只往上一层取色，于是无论写在哪一层都会取到正文黑，
   选中态蓝与未选中态灰同时失效、导航层级被抹平（2026-10-24 真机实测：
   label 计算色是强调蓝，文字实际渲染成正文黑）。 */
[data-testid="stSidebar"] label[data-testid="stRadioOption"] [data-testid="stMarkdownContainer"]{
  color:var(--c-text-2);
}
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover [data-testid="stMarkdownContainer"]{
  color:var(--c-text);
}
[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] [data-testid="stMarkdownContainer"]{
  color:var(--c-accent);
}
[data-testid="stSidebar"] label[data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p{
  margin:0; font-size:var(--fs-body); line-height:var(--lh-caption);
  color:inherit; font-weight:inherit;
}
[data-testid="stSidebar"] label[data-testid="stRadioOption"] div:has(+ div[data-testid="stMarkdownContainer"]){
  width:0; min-width:0; height:0; margin:0; padding:0; opacity:0; overflow:hidden;
}
/* 导航分组小标签（eyebrow）：11.5px / 7% 字距 / 三级灰，西式简洁的层级手段之一 */
.ai-nav-eyebrow{
  font-size:var(--fs-micro); line-height:var(--lh-micro);
  letter-spacing:.07em; font-weight:700; color:var(--c-text-3);
  margin:0 0 var(--s-1); padding:0 var(--s-3);
}

/* ---- 7b. Streamlit 原生提示条：info 收进中性+强调色系 ---------------------
   为什么（2026-10-24 逐页真机审查）：st.info 默认底色是 Streamlit 自带的蓝，
   与全站唯一强调蓝**不是同一个蓝**——一屏出现两种蓝，正是"仅用一种强调色"
   要禁止的情形，而且它出现在首页第一屏最显眼的位置。
   处理：info 改为中性底 + 1px 发丝边 + 左边 3px 强调色；语义由图标承担。
   success / warning / error 保留语义色（状态色不是装饰色，删掉会伤可用性）。 */
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]){
  background:var(--c-subtle); border:1px solid var(--c-border);
  border-left:3px solid var(--c-accent); border-radius:var(--r);
  color:var(--c-text-2);
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) svg,
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) [data-testid="stAlertContentInfo"]{
  color:var(--c-accent);
}

/* 键盘焦点环：唯一允许的 box-shadow —— 可访问性，不是装饰 */
:focus-visible{outline:none; box-shadow:0 0 0 3px var(--c-accent-ring);}

/* ---- 8. 响应式断点 ------------------------------------------------------ */
/* 桌面壳最小窗宽 1200px，故 1199 / 767 / 480 三档主要服务 Web / 平板 / 手机 */
@media (max-width:1199px){
  :root{--pad-top:24px; --pad-x:32px; --pad-bottom:40px; --col-basis:240px;}
}
@media (max-width:767px){
  :root{--pad-top:16px; --pad-x:16px; --pad-bottom:32px; --col-basis:100%;}
  /* 窄屏标题降一档，避免长标题独占两行把节奏打散；降档带全套度量（字号+行高） */
  [data-testid="stHeading"] h1,[data-testid="stMarkdown"] h1{
    font-size:var(--fs-title); line-height:var(--lh-title);}
}
@media (max-width:480px){
  :root{--pad-top:16px; --pad-x:16px; --pad-bottom:24px; --col-basis:100%;
        --fs-display:24px;}
  [data-testid="stBaseButton-secondary"],
  [data-testid="stBaseButton-primary"]{padding:0 var(--s-3);}
}
"""


# =============================================================================
# (d) IFRAME_TOKENS_CSS —— 给 iframe 用的迷你令牌块
#   已实测：CSS 变量无法穿透进 components.v1.html 的 iframe（srcdoc 是独立文档，
#   取父窗口 :root 变量返回空，只走 fallback）。所以这里必须写死字面量值。
#   注意：字面量与 :root 变量一一对应，改动 design.py 的令牌时这里要同步。
# =============================================================================
IFRAME_TOKENS_CSS = r"""
/* AI4S iframe 迷你令牌（ui/design.py :: IFRAME_TOKENS_CSS）
   CSS 变量穿透不进 srcdoc iframe（实测），故此处全部写死字面量，
   与页面 --c-* / --fs-* / --font-sans 保持一致。 */
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:#fafafa;
  color:#18181b;
  font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
              "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei",
              "Helvetica Neue", Arial, sans-serif;
  font-size:13.5px;
  line-height:1.65;
  -webkit-font-smoothing:antialiased;
}
p,li,h1,h2,h3,h4,td,th,div{overflow-wrap:anywhere;word-break:break-word}
img{max-width:100%;height:auto;display:block}
pre,code{overflow-x:auto;max-width:100%}
table{max-width:100%;border-collapse:collapse}
a{color:#1f5fae;text-decoration:none}
a:hover{text-decoration:underline}
:focus-visible{outline:2px solid rgba(31, 95, 174, 0.28);outline-offset:2px}
"""


# =============================================================================
# (e) 函数
# =============================================================================
_FLAG_KEY = "_ai_design_tokens_injected"   # 本轮幂等旗标（值=本轮 _run_seq）
_RATIO_RE = _re.compile(r"^\s*\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*$")


def tokens_css() -> str:
    """返回页面级令牌 CSS（纯 CSS 文本，不含 <style> 包裹）。"""
    return TOKENS_CSS


def iframe_tokens_css() -> str:
    """返回 iframe 迷你令牌 CSS（写死字面量，不含 <style> 包裹）。"""
    return IFRAME_TOKENS_CSS


def inject_into_head(html: str) -> str:
    """把 IFRAME_TOKENS_CSS 插进 `<head>...</head>` 内部。

    安全约束（有验证脚本硬依赖）：
    · 找不到 `</head>` 时**原样返回**，绝不拼到 DOCTYPE 之前（那会让文档进入怪异模式）；
    · 不删除原有的 `</head>`，只在它前面插入一个 `<style>` 块。
    """
    if not isinstance(html, str) or not html:
        return html
    m = _re.search(r"</head\s*>", html, _re.IGNORECASE)
    if not m:
        return html
    block = "<style>" + IFRAME_TOKENS_CSS + "</style>"
    return html[:m.start()] + block + html[m.start():]


def apply() -> None:
    """向页面注入 TOKENS_CSS —— **每次运行注入一次**（本轮幂等，不是会话幂等）。

    ⚠️ 这里曾经是"会话级幂等"（session_state 旗标置 True 后永久跳过），这是一个
    真实事故（2026-10-24 定位）：Streamlit 每次 rerun 都会重建整个元素树，上一轮
    st.markdown 注入的 <style> 不保留；旗标让第二轮起跳过注入 → 用户第一次点击
    侧栏后全站令牌 CSS 从 DOM 消失，界面静默退回 Streamlit 默认外观（实测导航后
    document 内查不到 --c-on-accent）。用户反馈"重启后没啥变化"的根因即此。

    现按 app.py 维护的 `_run_seq`（每轮 +1）判定本轮是否已注入：同一轮内多次调用
    只注入一次，跨轮必重新注入。若上游没有 _run_seq（如单页级测试），则每调用都注入，
    宁可多一份 <style> 也绝不丢样式。

    任何异常都静默忽略——令牌层不得影响页面渲染。
    """
    try:
        import streamlit as st                     # 惰性导入：模块级零副作用
        seq = st.session_state.get("_run_seq")
        if seq is not None and st.session_state.get(_FLAG_KEY) == seq:
            return                                  # 本轮已注入
        st.markdown("<style>" + TOKENS_CSS + "</style>", unsafe_allow_html=True)
        st.session_state[_FLAG_KEY] = seq if seq is not None else True
    except Exception:
        pass


def ensure() -> None:
    """`apply()` 的同义入口，语义上表示"确保令牌已注入"。"""
    apply()


def ellipsis(text, lines=None) -> str:
    """返回带 `title`（悬停显示完整原文）的 HTML span，配合 .ai-ellipsis / .ai-clamp-N
    做**纯 CSS 截断**。

    ⚠️ DOM 里的字符串保持完整——AppTest 用完整文件名去 select，Python 侧截断会
    静默打挂用例。HTML 特殊字符一律转义，防止注入。

    :param lines: None/1 → 单行截断；2 / 3 → 多行截断；其它正整数 → 一律按 3 行兜底。
    """
    raw = "" if text is None else str(text)
    esc = _html.escape(raw, quote=True)            # 同时转义 " ' & < >
    if lines == 2:
        cls = "ai-clamp-2"
    elif lines == 3:
        cls = "ai-clamp-3"
    elif lines:
        cls = "ai-clamp-3"
    else:
        cls = "ai-ellipsis"
    return f'<span class="{cls}" title="{esc}">{esc}</span>'


def ratio_box(html_or_img, ratio: str = "16 / 9") -> str:
    """按给定宽高比包裹内容（配合 .ai-ratio）。

    ratio 会做白名单校验（只接受 `数字 / 数字`），非法值回退 16 / 9，
    避免调用方把任意字符串拼进 style 造成 CSS 注入。
    """
    r = ratio if isinstance(ratio, str) and _RATIO_RE.match(ratio) else "16 / 9"
    inner = "" if html_or_img is None else str(html_or_img)
    return f'<div class="ai-ratio" style="--ai-ratio:{_html.escape(r, quote=True)}">{inner}</div>'

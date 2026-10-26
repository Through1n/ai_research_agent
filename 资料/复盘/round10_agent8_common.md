# Round 10 — Agent 8（公共 UI 组件库 `ui/common.py` 统一与精简）复盘

> 角色：全站唯一公共组件负责人。独占修改文件：`ui/common.py`（仅此一个），未触碰任何其他文件。
> 真实解释器：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（conda env `ai_research`）。

## 0. 设计系统调研（WebSearch 提炼的 2-3 条可落地原则）

参考 Linear Design System（designsystems.one / designmd.cc）与通用 design-principles 规范，提炼出本模块落地原则：

1. **统一间距尺度（4px 网格）**：所有 padding/margin 收敛到 8/12/16/24 这类 4 的倍数，卡片 padding 对称（如 `12px 16px`），不出现 10px/18px 之类的离群值。
2. **单一强调色 + 收口圆角**：全站只用一个 accent 蓝（`#3b6fb6`）贯穿左侧竖条/徽章/链接；圆角收敛到一套（卡片 10px / 徽章 12px / 小控件 6px），不混用多套半径，减少视觉噪音。
3. **全站唯一渐变 + 令牌化**：浅蓝→浅紫作为唯一的「卡片渐变」，页头与表头同源；所有颜色/阴影抽成模块级令牌常量，避免散落的魔法十六进制字面量（color noise）。

## 1. 抽出的视觉令牌（模块级私有常量，不对外暴露）

新增 `ui/common.py` 顶部「视觉令牌」区块（约 30 个 `_C_*` 常量），覆盖：

- 强调色：`_C_PRIMARY`(#3b6fb6)、`_C_PRIMARY_DK`(#2f5a96)
- 文字：`_C_INK`(#173a63)、`_C_INK_TBL`(#17325a)、`_C_SUB`(#4a5a70)、`_C_CELL`(#26313f)、`_C_SOURCE`(#27518a)
- 边框：`_C_BORDER`、`_C_BORDER_TBL`、`_C_BORDER_HEAD`、`_C_BORDER_CELL`、`_C_BORDER_CELL2`
- 渐变/底色：`_C_BG_BLUE`(#eef4fc)、`_C_BG_PURPLE`(#f6f1fd)、`_C_BG_SOFT`(#f7faff)、`_C_BG_ZEBRA`、`_C_BG_ZEBRA2`、`_C_BG_HOVER`
- 思考块：`_C_THOUGHT_BG`、`_C_THOUGHT_BD`
- 尺度：`_C_RADIUS`(10px)、`_C_RADIUS_BADGE`(12px)、`_C_RADIUS_SM`(6px)、`_C_SHADOW`、`_C_PAD`(12px 16px)、`_C_MARGIN`(2px 0 14px)

这些仅是内部常量，未加入任何 `__all__`，未改变对外接口。

## 2. 内部实现改动（仅动内部，签名不变）

| 函数 | 改动 |
|---|---|
| `page_header` | 内联渐变/边框/圆角/阴影/文字色改为引用 `_C_*` 令牌；**渲染观感与改动前一致**（渐变起止色原本就是 `#eef4fc→#f6f1fd`）。 |
| `_PRETTY_CSS`（模块级常量） | 由硬编码十六进制改为 `%` 格式化拼接 `_C_*` 令牌；**有意为之的一致性改动**：表头渐变从原本的 `#e2edfb→#efe7fb` 改为与页头同源的 `#eef4fc→#f6f1fd`（全站统一一种浅蓝→浅紫渐变，更一致，肉眼差异极小）。 |
| `html_table` | 深蓝表头渐变与单元格分隔/斑马纹改为引用 `_C_PRIMARY`/`_C_PRIMARY_DK`/`_C_BORDER_CELL2` 等令牌；渲染观感不变。 |
| `source_card` | 卡片底色/左侧竖条/圆角/阴影/标题色/徽章底色改为令牌；卡片圆角由 8px 统一为 `_C_RADIUS`(10px)；**合并去重**：原内联 `t.replace("&",...).replace("<",...).replace(">",...)` 改为复用全站统一的 `_esc()` 转义 helper（消除文件内重复的转义逻辑，防注入语义不变）。 |
| `render_trace` | 思考块底色/竖条/圆角改为令牌（`_C_THOUGHT_BG`/`_C_THOUGHT_BD`/`_C_RADIUS_SM`），观感不变。 |

`_PRETTY_CSS`、`_C_SHADOW` 等本就是/已改为模块级常量，避免每帧重复拼接大段 CSS。

## 3. 合并/删除的私有 helper

- **删除的私有 helper：无。** 本文件内的私有 helper（`_esc`、`_to_frame`、`_jump_target_label`、嵌套 `_panel`）均被使用，且 `_esc` 实际被 `ui/page_writing.py` 显式 `from ui.common import ... _esc` 导入（属对外依赖），因此**不可删、不可改名**。
- **去重（合并相似逻辑）**：`source_card` 内联的 HTML 转义改为统一调用 `_esc`，消除与 `_esc` 重复的转义实现。
- 公有历史函数（`render_styled_table`/`html_table`/`source_card`/`render_trace`/`memory_mb`/`require_library`/`resolve_pdf`/`embed_html` 等）与全部任务要求的导出函数（`page_header`/`pretty_table`/`ensure_state`/`empty_state`/`friendly_error`/`show_error`/`llm_guard`/`llm_failure_banner`/`classify_llm_error`/`long_block`/`safe_render`/`safe_call`/`require_library`/`jump_banner`/`render_task_panel`/`source_card`/`render_trace`/`memory_mb`/`embed_html`/`at` 等）**全部保留，签名零变更**。

## 4. 导出签名变更声明

**导出签名零变更。** 所有 `def` 的函数名、参数顺序、默认值、返回语义与改动前完全一致；新增内容仅为模块级 `_C_*` 常量与 `_PRETTY_CSS` 内部的字符串格式化，不新增任何对外依赖、不修改 `__all__`、不新增顶层 `import`。

## 5. 全站 import 冒烟结果

执行：`python -m compileall -q ui/common.py` → **COMPILE_OK**。

逐模块隔离导入（`__import__`）结果：

```
OK    ui.common
OK    ui.sidebar
OK    ui.page_agent
OK    ui.page_library
OK    ui.page_qa
SYNTAX ui.page_reading  -> .../ui/page_reading.py line 1292
OK    ui.page_search
OK    ui.page_graph
OK    ui.page_writing
OK    ui.page_misc
```

- `ui.common` 与**除 page_reading 外的 8 个页面全部 IMPORT_OK**。
- `ui.page_reading` 存在 **pre-existing（非本次引入）的 `SyntaxError`**：`ui/page_reading.py:1292` 出现一行**悬空的 `except Exception as e:`**（1291 与 1292 连续两个 `except`，之间无 `try`）。该文件由 agent5-reading 修改（`git status` 显示其已改动），错误位于我**无权修改**的文件中，与 `ui/common.py` 改动无关（`SyntaxError` 是解析期错误，发生在任何 import 绑定之前）。因此该失败**不是本 agent 误删/改名导出函数导致**。

> 注：按铁律我未修改 `ui/page_reading.py`，已将此问题同步给 agent5-reading 修复。

## 6. 风险点

1. **低风险 — 表头渐变微调**：`_PRETTY_CSS` 表头渐变由 `#e2edfb→#efe7fb` 改为与页头一致的 `#eef4fc→#f6f1fd`，属有意的「全站统一一种渐变」一致性改动，肉眼差异极小；若评委要求 100% 像素级不变，可回退该处。
2. **低风险 — 卡片圆角统一**：`source_card` 圆角 8px→10px（对齐 `_C_RADIUS`）。属一致性收敛，观感变化微乎其微。
3. **零风险 — 转义去重**：`source_card` 改用 `_esc` 后额外转义了引号（`quote=True`），在 `<div>` 文本节点中无害，且更防注入。
4. **阻塞项（非本 agent 责任）**：`ui.page_reading.py:1292` 语法错误会阻断「全 9 页一次性 import」冒烟，需 agent5-reading 修复后方能全绿。本 agent 的 `ui/common.py` 自身 compileall 通过、且除该文件外全站 import 正常。

## 7. 一句话结论

导出签名零变更；`ui/common.py` 通过 `compileall`；除 agent5-reading 文件的既有语法错误外，全站 8 个页面 import 全部通过；风险仅为两处有意为之的一致性微调（表头渐变同源、卡片圆角统一）。

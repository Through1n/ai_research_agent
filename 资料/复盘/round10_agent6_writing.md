# Round10 Agent6 — 写作辅助与可视化页整洁化（ui/page_writing.py）

> 角色：6 号选手，独占文件 `ui/page_writing.py`。仅动 UI/文案/布局，未改 `services/writing` 任何调用、未改 `ui/common.py` / `app.py`。

## 一、参考来源（WebSearch，诚实记录）

1. **Notion AI 输入/输出设计拆解**（northbase.design/systems/notion/ai-input-output）
   - 块级结构（block-first）+ 分栏 + Callout/Toggle 实现模块化折叠；
   - 顶部一句话说明 + mode tabs + 上下文指示（"Current page" chip）+ 明确 loading 状态（Thinking/Reading/Editing 动画点）。
2. **Grammarly 全新设计（TechCrunch 2025-08-18）+ Grammarly 交互设计案例（ZOOZ）**
   - 基于文档的 block-first 界面：可插入表格/列/分隔符/列表/标题，富文本块强调信息；
   - 建议卡片（suggestion card）颜色编码区分类型；就地高亮 + Accept/Discard 明确操作。

### 提炼的可落地整洁原则（3 条）
- **P1 分区卡片化**：4 个功能区各自包成独立带边框卡片（`st.container(border=True)`），层次清晰，替代原来一条 `st.markdown("---")` 硬分隔。
- **P2 上下文指示 + 空状态引导**：每个卡片顶部统一一句话说明（"💡 …"）讲清用途与何时需要 Key；空数据用 `ui/common.empty_state` 统一引导，不再零散 `st.caption` / `st.error`。
- **P3 留白与一致性**：图标导航、统一色板观感；"载入示例 / 生成"类主操作对齐（CSV 卡片上传框与示例按钮用 `st.columns([2,1])` 同行对齐），说明话术统一。

## 二、改动清单（只动 ui/page_writing.py）

### 2.1 导入复用 common 组件
- `from ui.common import embed_html, page_header, show_error, _esc, empty_state, pretty_table`
  - 新增复用 `empty_state`（空状态引导）、`pretty_table`（References 溯源表格统一样式）。
  - 原有已复用的 `page_header` / `show_error` / `embed_html` / `_esc` 保持不变。

### 2.2 render() — 分区卡片化
- 4 个分区由「`st.markdown("---")` 平铺」改为 4 个 `with st.container(border=True):` 包裹的卡片。
- `render(ctx)` 签名不变，仅内部包裹方式变化。

### 2.3 四个分区统一说明 + 空状态
- `_csv_section`：新增 `💡` 说明；上传框与「📂 载入示例数据」按钮用 `st.columns([2,1])` 同行对齐（原来示例按钮在上传框下方、标签冗长）；空数据改 `empty_state(...)`。
- `_outline_section`：新增 `💡` 说明；文献库为空改 `empty_state(...)`（原 `st.error` 改为温和信息引导，功能等价——仍阻止生成）。
- `_diagram_section`：新增 `💡` 说明。
- `_translate_section`：新增 `💡` 说明。

### 2.4 References 溯源表格复用 pretty_table
- `_render_reference_provenance` 中 `st.dataframe(pd.DataFrame(rows), ...)` 改为 `pretty_table(rows)`，统一浅色渐变表头 + 斑马纹，与全站表格风格一致；保留异常降级分支。

### 2.5 未改动（明确）
- 所有 `writing.*` 调用（infer_chart_plan / figure_caption / reference_whitelist / outline_body_stream / finalize_outline / diagram / try_compile_tikz / render_dot / translate_to_english / translate_to_chinese / llm_available 等）签名与位置不变；
- session_state key 与 `_SS_DEFAULTS` 不变；
- 流式输出、白名单校验、占位串告警、三级降级渲染等逻辑原样保留。

## 三、自检结果
- `python -m compileall -q ui/page_writing.py` → COMPILE_OK
- `python -c "import ui.page_writing"` → IMPORT_OK（确认 `from services import writing` 及 `ui.common` 导入均成功）
- Streamlit 版本 1.63.0，支持 `st.container(border=True)`（≥1.32 即支持）。
- 未启动 `streamlit run`，未触发任何真实 LLM / API 调用；未读取/打印/回显任何密钥（`.env` 未触碰）。

## 四、风险点（诚实）
- **卡片嵌套与布局**：`st.container(border=True)` 包裹含 `st.file_uploader` / `st.columns` / `st.expander` / `st.plotly_chart` / `embed_html` 的复杂内容，Streamlit 1.63 下均支持；但边距/间距观感需在真实 `streamlit run` 下目测确认（本次未启动，仅做语法/导入验证）。
- **CSV 上传框移入 columns**：`cf = c_up.file_uploader(...)` 在列内，行为与原 full-width 上传一致；若后续有「上传即自动触发」依赖全宽布局的逻辑，需目测确认无回归（当前代码为显式 `if cf:` 处理，无回归）。
- **空状态语气变化**：文献库为空时由 `st.error`（红）改为 `empty_state`（蓝 info）。这是有意弱化阻断感、提升整洁度；逻辑上仍 `return` 不生成框架，功能不变。若评审要求保留红色强提示，可回退该行。
- **pretty_table 渲染差异**：`pretty_table` 用 HTML 表格（已 escape），与原文 `st.dataframe` 的排序/选择交互不同（HTML 表不可点选列排序）。References 溯源以"只读展示"为目的，此差异可接受；如需可交互表格可保留 `st.dataframe`。

## 五、结论
界面由"4 段 `---` 平铺"变为"4 张带边框分区卡片"，每卡顶部统一说明、空数据统一引导、主操作对齐，整体更整洁、层次更清晰；严格复用 `ui/common` 组件，未触碰写作生成逻辑与密钥。compileall / import 均通过；唯一待真实运行目测的是卡片观感（未启动 streamlit，属约束要求）。

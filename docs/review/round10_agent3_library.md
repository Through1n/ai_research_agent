# Round 10 · Agent3 文献库管理页整洁化 · 复核记录

- 负责人：agent3-library
- 独占文件：`ui/page_library.py`（未改动 `ui/common.py` / `app.py` / `services/library.py`）
- 日期：2026-10-13
- 模式：仅动 UI / 文案 / 布局，功能与调用逻辑不变

## 一、参考（WebSearch，真实检索）

1. **Zotero 库视图**（来源：libguides.oneonta.edu、libguides.nps.edu、zotero.org/support/sorting）
   - 中间栏 Item List 用「列」呈现元数据：Title / Creator / Year / Attachments / Tags，列可排序、可显隐。
   - 同一论文的重复项 / 多版本单独归到 **Duplicates** 分组，并给出「合并 / 保留主版本」建议。
   - 三栏分区：左 Collections+Tags、中 Item List、右 Item Details，元数据分区清晰。
2. **Paperpile**（来源：paperpile.com、wp.paperpile.com/changelog、appmus.com/software/paperpile）
   - 独立、**清晰的拖拽上传区**（Upload files 对话框，拖到窗口即弹出），上传区与列表区解耦。
   - 工具栏按钮按逻辑分组（folders/labels/trash 一组，edit/cite/share 一组），状态可见、留白舒适。
   - 「干净现代界面、批量操作」是核心设计取向。

## 二、提炼并落地的整洁原则（2-3 条）

- **P1 整齐列表格呈现文献库（借鉴 Zotero Item List + Paperpile 干净列表）**
  原「当前文献库」用 `st.columns` 逐行拼 markdown（`**标题** \`版本\` · 文件名 · 年份`），散且不易扫读。
  改为 `pretty_table`，固定列：篇名 / 版本 / 年份 / 片段数 / 文件名，沿用 common 的渐变表头 + 斑马纹。
- **P2 分区与层级一致、留白舒适（借鉴 Paperpile 工具栏分组 + Zotero 三栏分区）**
  原 5 个 `st.subheader` 层级/视觉不统一。新增本地 `_section(title, desc)` 标题卡片
  （与 `page_header` 一致的蓝色左侧强调条 #3b6fb6 + 浅蓝渐变底），统一大标题→分区标题→子内容层级；
  每个分区配一行灰色说明，减少视觉噪音。
- **P3 上传区独立明确 + 版本去重单独分组对比（借鉴 Paperpile 拖拽上传 + Zotero Duplicates）**
  上传区保持独立显眼（`file_uploader` 置顶 + 分区说明）；同一论文多版本用 `st.container(border=True)`
  卡片分组，差异对比结果用 `long_block` 自动折叠（长内容折叠，满足「并排/折叠」的折叠项），按钮加 `key` 防重复。

## 三、具体改动（均在 `ui/page_library.py`）

1. 顶部新增 `from html import escape as _h`（标准库，无新增外部依赖），新增本地 `_section()` 标题卡片助手。
2. 五个分区标题由 `st.subheader(...)` 统一替换为 `_section(title, desc)`，并补一行中文说明：
   - ⬆️ 上传 PDF（自动语义查重 / 扫描件 OCR）
   - 🪪 元数据与排版识别
   - 🔬 细粒度结构解析
   - 📚 当前文献库
   - 🗝️ 版本管理与差异
3. 「当前文献库」列表：移除逐行 `st.columns` + 行内删除按钮，改为
   `pretty_table(rows, title="✅ 当前共 N 篇文献", max_rows=MAX_FAILED_SHOW)`，
   行数据含「篇名/版本/年份/片段数/文件名」（`片段数` = 该 source 的 chunk 计数，复刻原 `_commit` 的算法）。
   删除改为独立「🗑️ 删除文献」卡片：`selectbox` 选文献 + 「确认删除所选文献」按钮，调用 `library.remove_paper` 逻辑不变。
4. 「版本管理与差异」：每个版本组用 `with st.container(border=True)` 包裹，标题/版本清单/保留建议层级清晰；
   对比按钮加 `key=f"lib_diff_{gid}"`（原无 key，多版本时潜在 widget key 冲突），结果标题改为 `版本差异报告（{gid}）`。
5. 复用 common 组件：`page_header` / `pretty_table` / `empty_state` / `friendly_error` / `llm_guard` / `long_block` / `step_text` / `LONG_TEXT_LIMIT` 全部沿用，**未重复造轮子**。

## 四、未改动（保证功能不变）

- `render(ctx)` 签名、导出函数（`_init`/`_commit`/`_run_rebuild`/`_render_library_health`/`_struct_error_hint` 等）签名均未变。
- 入库/查重/解析/删除/版本对比的调用逻辑（`library.prepare_pdf` / `library.commit` / `library.remove_paper` /
  `library.group_versions` / `library.version_diff` / `parsing.*`）原样保留，仅改展示层。
- 未读取/打印/回显任何 `.env` 中的 API Key；未触发真实 LLM 调用（任务禁止）。

## 五、自检

- `python -m compileall -q ui/page_library.py` → COMPILE_OK（通过）
- `python -c "import ui.page_library"` → IMPORT_OK（通过，无导入期副作用）

## 六、风险点（诚实列示）

1. **删除交互形态变化**：原「每行一个删除按钮」→「下拉选择 + 确认按钮」。功能等价（可删任意一篇），
   但 UX 不同；若评审期望逐行删除按钮，观感会略不同。删除仍走 `library.remove_paper` + `set_flash` + `st.rerun`，逻辑一致。
2. **版本差异「并排」未实现**：`version_diff` 返回的是 Markdown 文本，拆成 v1/v7 两栏并排需解析其结构，
   风险高且易破坏格式；按任务「如并排/折叠」的二选一，采用 `long_block` 折叠方案，长内容自动收起，
   点击展开。若评审强求并排，需后续解析 diff markdown。
3. **`_section` 用 `unsafe_allow_html=True`**：标题/说明经 `html.escape` 转义（防注入），与现有 toc 树渲染一致，无 XSS 风险；
   但理论上依赖 common 的转义约定，已在文件内闭环。
4. **`st.container(border=True)` 兼容性**：common.py 的 `jump_banner` 已使用同 API，环境 Streamlit 版本支持，导入自检通过。
5. **空库/无多版本时**：列表区与版本区仍走 `empty_state`，无回归。

## 七、结论

仅展示层整洁化，功能零改动；分区标题统一、文献库列表表格化、版本对比折叠化，均复用 common 组件；
compileall 与 import 自检通过。

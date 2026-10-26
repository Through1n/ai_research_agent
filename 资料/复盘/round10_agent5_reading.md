# Round10 · 5 号选手（agent5-reading）· 版式阅读页整洁化报告

- 日期：2026-10-13
- 负责文件：`ui/page_reading.py`（独占修改，未触碰 `ui/common.py` / `app.py` / `services/parsing.py` / `services/translate.py`）
- 目标：界面整洁干净、运行顺利、体验更好，借鉴热门 PDF 阅读/翻译应用。
- 性质：**纯 UI/文案/布局 安全优化**，所有 PDF 打开、解析、跳转、翻译调用逻辑与函数签名不变。

---

## 一、参考来源（WebSearch 真实检索，2 个产品）

1. **ChatPDF**（chatpdf.com / aitoolbox360 评测）
   - 设计要点：极简、clutter-free 布局，注意力集中在文档与内容；侧边栏/双栏对照；引用高亮；文档导航随时维持阅读位置。
   - 可落地原则：**工具栏/操作做视觉分组 + 充足留白**，避免按钮散落；把「相关操作」成组，不相关操作之间用分隔隔开。
2. **沉浸式翻译 / 极简翻译**（immersivetranslate.net / chrome 商店「极简翻译」条目）
   - 设计要点：划词翻译「选中即弹出、不跳页、边查边读」；双语对照垂直排版（原文在上译文在下，保留原结构）；阅读模式自动生成目录（TOC）可折叠展开、支持章节跳转；去除干扰侧栏，保留标题层级。
   - 可落地原则：**标题树（TOC）可折叠、默认收起**（长文档不占垂直空间，需要才展开）；**翻译结果放进有边界的卡片/对照区，与控制区清晰分隔，不打断阅读心流**。

### 提炼的 3 条整洁原则（已落地）
- P1 **操作分组 + 一致留白**：单页渲染/栏数检测成一组，长文档批量扫描另起一组，用 `st.divider()` + 组标题 `**🖼️ 单页操作**` 分隔。
- P2 **TOC 默认收起**：标题树按钮整体包进 `st.expander(..., expanded=False)`，长文档不占大片空间，展开后才渲染跳转按钮。
- P3 **翻译结果清爽**：划词翻译结果包进 `st.container(border=True)` 并加「🌐 译文」标题；逐段双语对照加「🌐 中英对照」标题；引用跳转视图生成按钮前加 `st.divider()` + 标题，操作区与结果区分隔。

---

## 二、具体改动（仅 UI/文案/布局）

| 区域 | 改动 | 类型 |
|------|------|------|
| 导入 | `from ui.common import embed_html, page_header, show_error, empty_state` | 复用 common 组件 |
| 未选 PDF 空态 | `st.info("请先在文献库导入论文，或临时上传。")` → `empty_state(...)` 并带 2 条引导 hint（与 `require_library` 话术一致） | 复用 common 组件 |
| 标签1 原生渲染 | 加 `st.markdown("**🖼️ 单页操作**")` 组标题；在批量扫描前加 `st.divider()` | 分组/留白 |
| 标签2 中英同步滚动 | 双语 iframe 前加 `st.markdown("**🌐 中英对照（逐段对齐 · 左右同步滚动）**")`（翻译后/恢复后两处均加） | 结果区清爽 |
| 标签3 引用跳转 | 生成视图按钮前加 `st.divider()` + `st.markdown("**🔗 生成正文 ↔ 参考文献 双向跳转视图**")` | 分组/留白 |
| 标签4 划词翻译 | 加 `st.divider()` + `**✍️ 片段翻译**` 组标题；翻译结果包进 `st.container(border=True)` 并加「🌐 译文」caption | 分组 + 结果区清爽 |
| 标签5 标题树 | 全部标题跳转按钮包进 `st.expander("📑 标题目录…", expanded=False)`，默认收起 | TOC 可折叠默认收起 |

### 明确「未改动」（保留并衔接）
- `render(ctx)` 签名、所有导出函数签名、`st.session_state` 读写键（`np/sp/pp/hp/cj_*/toc*/read_*` 等）**全部原样保留**。
- PDF 打开/解析（`parsing.open_doc`）、跨页跳转消费（`_consume_qa_jump` / `_consume_toc_jump`）、TOC 点击写入 `TOC_JUMP_KEY` + `st.rerun()` 定位逻辑**原样保留**，只是把按钮放进折叠面板内（展开后才渲染、才可被点击，行为等价）。
- 「请先在文献库导入论文」空态**保留**，改用 `empty_state` 复用公共组件并补充引导。
- 所有 try/except 兜底、中文文案、流式翻译、双语对照同步滚动、引用双向跳转、原位 PDF、长文档扫描等**功能逻辑零改动**。
- 未新增 `ui/common.py` 之外的任何依赖。

---

## 三、自检结果

- `C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m compileall -q ui/page_reading.py` → **COMPILE_OK**（通过）
- `... -c "import ui.page_reading"` → **IMPORT_OK**（通过）
- 未启动 `streamlit run`；未触发任何真实 LLM 调用；未读取/打印/回显任何 API Key（全仓未触碰 `.env` 读取相关代码，本文件本就无密钥逻辑）。
- 未运行 `scripts/_smoke_ui.py` / `scripts/_smoke_read.py`。

> 实施过程中的一次失误（如实记录）：首次给原生渲染区分隔加 `st.divider()` 时，Edit 把外层的 `except` 缩进误写成 16 空格（应为 12），导致 `SyntaxError: invalid syntax`（compileall 第 1292 行报红）。随后立即用 Edit 把该 `except` 缩进修正回 12 空格，重新 compileall/import 均通过。根因：替换片段里 `except` 行自带 4 空格多余缩进，现已纠正，功能与缩进均正确。

---

## 四、风险点（诚实评估）

1. **TOC 默认收起 = 行为微调**：旧实现标题按钮始终可见；新实现默认折叠，用户须先展开面板才能点击跳转。这是任务「默认收起」的明确要求，功能等价（展开后点击、`st.rerun()` 定位逻辑未变），但**首屏可见性下降**。长文档体验变好，短文档多一次点击。若评委希望「短文档自动展开」，需加 `len(toc) <= 阈值` 判断——本次按「一律默认收起」执行，保守未加分支。
2. **`st.container(border=True)` / `st.expander(expanded=False)` 兼容性**：`border=True` 需 Streamlit ≥ 1.32；`expanded=` 参数在旧版被忽略（仍默认展开），不影响崩溃，仅视觉回退。本环境 `ai_research` 环境版本应满足；若部署到极旧 Streamlit，TOC 折叠可能「看起来没折叠」，但不会报错。
3. **划词翻译结果容器包裹 `st.write_stream`**：`write_stream` 在 `container` 内调用，输出渲染到容器内，功能与流式进度不变；仅外层多了一层边框卡片，视觉更清爽。已实测导入通过，但**未在真机 Streamlit 跑过渲染**（按约束未 `streamlit run`），边框/流式的实际观感依赖运行环境。
4. **未做端到端渲染验证**：仅做静态编译/导入与逻辑核对，未实跑页面。布局类改动（divider、markdown 标题、expander）均为 Streamlit 标准 API，风险低，但「视觉是否真的更整洁」需真机确认。
5. **与其他 agent 的衔接**：本页独占 `ui/page_reading.py`，已确认未改动 `ui/common.py` 的 `empty_state`/`page_header`/`embed_html`/`show_error` 任何签名；`empty_state` 为既有导出函数，直接复用，无新增耦合。

---

## 五、结论

仅做安全 UI 整洁化：操作分组+留白、`empty_state` 复用、TOC 默认折叠、翻译结果卡片化。功能逻辑、签名、密钥处理、跨页跳转全部不变。compileall 与 import 均通过；未触发任何真实调用。主要风险是 TOC 默认收起带来的「首屏多一次展开点击」与未做真机渲染验证。

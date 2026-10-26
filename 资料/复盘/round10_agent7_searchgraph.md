# Round10 · Agent7（searchgraph）· 🔍检索与追踪页 / 🕸️引用图谱与综述页 整洁化

> 独占修改文件：`ui/page_search.py`、`ui/page_graph.py`
> 禁止修改：`ui/common.py`、`app.py`、`services/graph.py`、`services/rag.py`（含图谱构建/检索逻辑）
> 环境：真实解释器 `C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（conda `ai_research`，Python 3.11）

---

## 一、参考来源（WebSearch 调研）

1. **Semantic Scholar**（Allen Institute for AI）
   - 首页布局：醒目搜索框 + 登录用户的「个性化推荐 / Research Feeds」在下方直接露出。
   - 结果页分区清晰（标题/作者/年份/被引数/TLDR 摘要/PDF 链接），并设置筛选侧栏。
   - 价值点：分区清晰、个性化推荐/订阅(alerts)放在醒目位置。
   - 来源：https://www.hkmu.edu.hk/oetools/semantic-scholar/ 、https://wpnewsify.com/blog/semantic-scholar-review-...
2. **Connected Papers / ResearchRabbit**（可视化文献发现工具对比）
   - Connected Papers：顶部单输入框即生成交互式图谱，Prior Works / Derivative Works 面板与图谱明确区分；界面「clean, distraction-free, zero learning curve」。
   - ResearchRabbit：按 collection 组织，图谱与「持续推荐/前沿发现」分成两个清晰的工作流。
   - 价值点：图谱可视化区与文本/综述/推荐区要分区明确；交互要低学习成本。
   - 来源：https://tooliverse.ai/compare/connected-papers-vs-researchrabbit 、https://scientistinprogress.com/blog/researchrabbit-review-scientists/

### 提炼的可落地整洁原则（2-3 条）
- **原则 A · 分区清晰、层级一致**：每个功能大区用统一层级的标题 + 卡片边框 + 留白分隔，子区用一致的 `st.subheader`，避免功能挤在一起。
- **原则 B · 图谱与综述/文本生成区分明**：把「可视化图谱」与「一键综述 / Future Work 生成」放进两个不同的分区卡片，互不混淆。
- **原则 C · 订阅/推荐区突出**：动态追踪订阅、个性化推荐用区别于中性区的强调色（琥珀色卡片）置于页面靠前位置，借鉴 Semantic Scholar 的 Research Feeds 露出方式。

---

## 二、改动清单

### `ui/page_search.py`（🔍 检索与追踪页）
- 新增模块级常量 `_SEC_CSS` 与函数 `_sec_head(title, icon, highlight)`：
  - 用内联 `<style>` + 渐变左边框卡片呈现统一分区标题（与 `common.page_header` 同色系；`highlight=True` 时切琥珀色 `#e0a23b` 背景 `#fdf6ea`，用于突出重点区）。
  - 仅依赖标准库 `from html import escape as _esc`，未引入 `ui/common.py` 之外的任何新依赖。
- `render(ctx)` 重构为三段式分区（保持 `render(ctx)` 签名与外部调用方式不变）：
  1. **重点区（highlight）· ⏰ arXiv 动态追踪订阅**：`_track(ctx)` 包在 `st.container(border=True)` 中。
  2. **重点区（highlight）· ⭐ 个性化论文推荐**：`_recommend(ctx)` 包在边框容器中。
  3. **中性区 · 🔍 论文检索与抓取**：`_arxiv` / `_others`(PubMed+网页) / `_batch` / `_quality` 依次排在同一个边框卡片内，彼此用 `st.markdown("---")` 留白分隔；其内部 `st.subheader` 标题层级保持一致。
  - 每个分区均保留独立的 `try/except` + `show_error` 异常隔离（沿用 `ui.common` 组件），单区失败不影响其余区。
- 移除了 `_track`、`_recommend` 内部原有的 `st.subheader`（改由 `_sec_head` 统一提供标题，避免重复标题），其余内部函数（arXiv/PubMed/网页/批量/引用含金量）的 `st.subheader` 子标题保留，形成「分区标题 → 子区标题」的一致层级。
- 未改动任何外网调用逻辑（`extern.search_arxiv/search_pubmed/web_search/latest_arxiv`、`df._ingest_arxiv_paper/t_batch_download`、`track.*` 调用方式与位置不变）。

### `ui/page_graph.py`（🕸️ 引用图谱与综述页）
- 新增模块级 `_SEC_CSS` 与 `_sec_head(title, icon)`（同色系，无高亮色，因本页重点是「区分」而非「强调」），同样仅依赖标准库 `html.escape`。
- `render(ctx)` 重构为两个明确分区（保持签名与 `services/graph` 调用方式完全不变）：
  1. **区一 · 🕸️ 引用图谱可视化**：`构建引用图谱` 按钮 + 图谱结果渲染（`_render_pack`）+ `尚未构建图谱` 空状态 + `🎭 演示模式示意图谱` 按钮，全部包在同一边框卡片内。演示模式按钮保留在可视化区（它产出的是示意图谱），并保留其 `set_flash` + `st.rerun` 逻辑。
  2. **区二 · 📝 领域综述与前沿方向生成**：`📝 一键生成领域综述` 与 `🚀 Future Work 前沿扫描` 两个按钮（改为 `c2, c3 = st.columns([1,1])` 两列，原三列布局中的 demo 按钮已上移），以及综述/前沿结果 `long_block` 展示，包在独立边框卡片内。
- 保留并复用 `ui.common` 组件：`page_header` / `require_library`（空库引导保留）/ `pretty_table` / `empty_state` / `friendly_error` / `llm_guard` / `long_block` / `step_text` / `at` / `embed_html`。
- 所有 `services.graph` 调用（`build_graph` / `analyze` / `classify_papers` / `graph_html` / `review_structure` / `generate_review` / `explore_future`）签名与传参**逐字保持不变**。
- 空库引导 `require_library(ctx)` 调用位置与行为保持不变（仍在第一分区之前拦截）。

---

## 三、复用 common 组件情况
- `page_search.py`：`ensure_state` / `page_header` / `net_error` / `friendly_error` / `show_error` / `pretty_table` / `empty_state` / `llm_guard` / `long_block` / `step_text`。
- `page_graph.py`：`embed_html` / `require_library` / `page_header` / `pretty_table` / `ensure_state` / `friendly_error` / `empty_state` / `llm_guard` / `long_block` / `step_text` / `at`。
- 未新增对 `ui/common.py` 之外的第三方依赖；分区标题样式均为页面内联 `<style>`，不修改 `ui/common.py`。

---

## 四、自检结果
- `python -m compileall -q ui/page_search.py ui/page_graph.py` → **通过（COMPILE_OK）**。
- `python -c "import ui.page_search, ui.page_graph"` → **通过（IMPORT_OK）**。
- 未运行 `streamlit run`，未触发任何真实 LLM / 外网调用（仅模块级导入 + 纯 UI 结构改造）。
- 未运行 `scripts/_smoke_ui.py` / `scripts/_smoke_read.py`。

---

## 五、风险点与诚实声明
- **风险 1（低风险）**：`_sec_head` 使用内联 `<style>` 注入；若 Streamlit 未来对 `unsafe_allow_html` 更严格或存在样式命名冲突，可能需回退为纯 `st.subheader`。目前与 `common.page_header` 的 class 命名无冲突（本页用 `.sec` / `.sec.hl`）。
- **风险 2（低风险）**：`render` 中把原本「构建按钮 + 三列按钮」拆成两个分区后，按钮 `key` 由 Streamlit 自动生成（依赖 `key=` 参数），未改变任何显式 `key`，故不会触发 session_state 键冲突或控件状态丢失。
- **风险 3（行为一致性）**：`_track` / `_recommend` 仅移除内部 `st.subheader`，功能逻辑（订阅读写、推荐打分、点赞/踩）一行未改；`render` 中各子函数调用顺序调整为「追踪→推荐→检索四区」，各子函数相互独立、使用各自 session_state key，顺序调整不影响正确性。
- **诚实声明**：本次为**纯展示层整洁化**，未触碰图谱构建 / 检索 / RAG 等底层逻辑（归其他 agent）。未读取/打印/回显任何 `.env` 中的 API Key。所有外网与 LLM 调用路径保持原样、未被触发。

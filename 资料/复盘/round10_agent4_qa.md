# Round 10 · Agent4（💬 跨文献问答与溯源页整洁化）

> 独占修改文件：`ui/page_qa.py`（未改动 `ui/common.py` / `app.py` / `services/rag.py`）
> 环境：Windows / Python 3.11（conda `ai_research`）/ Streamlit 1.63.0
> 自检：`compileall -q ui/page_qa.py` ✅ 通过；`python -c "import ui.page_qa"` ✅ 通过
> 约束遵守：未启动 `streamlit run`、未触发真实 LLM 调用、未读取/打印任何 API Key。

---

## 一、参考来源（WebSearch，1–2 个热门学术问答应用）

| 应用 | 内联引用 / 来源设计要点（提炼） | 参考链接 |
|---|---|---|
| **SciSpace (Typeset)** / **Elicit** | ① 答案逐句"line-by-line inline citations"，每条结论可点回到来源论文；② "Chat with PDF" 模式答案来自文档具体段落并带引用；③ 界面极简、几乎零学习成本（Elicit 被反复强调 "Simple interface"）。 | https://scispace.com/compare/elicit ；https://www.hkmu.edu.hk/oetools/scispace/ |
| **Consensus** | ① 答案内联引用角标，hover 看支撑原句、点击跳到原文对应段落（Citation Grounding）；② 顶部 TL;DR 速览、下方证据可展开；③ **"Suggested Follow-Up Questions"** 可点击提示问题（即示例问题 chips）；④ 调试/证据类信息后置、不喧宾夺主。 | https://docs.consensus.app/core-features/citation-grounding ；https://consensus.app/home/resources/consensus-libguide/ |

> 说明：以上为公开帮助中心/官方文档与第三方评测的共识性描述，未截图、未抓取私有数据。

---

## 二、落地的整洁原则（3 条，均来自上述参考）

1. **答案内联引用 + 来源可点跳转（inline citation grounding）**：答案中的【n】角标本页已具备，本次保持并强化"来源卡片→页码徽章→一键定位原文"的闭环，来源可点、可跨页跳阅读页。
2. **来源卡片清爽、统一对齐**：每条来源独立卡片，页码徽章靠右、相关度/段落元数据一行、跳转按钮贴右对齐、卡片间留白一致，避免信息堆叠。
3. **示例问题 chips 一行 + 证据/调试默认折叠（信息分层）**：借鉴 Consensus 的 Suggested Follow-Up Questions 与 Elicit 极简界面，把 5 个示例问题改成原生胶囊（pills）一行排列，降低首屏决策成本；溯源与检索调试默认折叠，高级信息后置，主答案优先。

---

## 三、具体改动（`ui/page_qa.py`）

| 区域 | 改前 | 改后 | 目的 |
|---|---|---|---|
| 示例问题（`render` 顶部） | `st.columns(3)` + 5 个 `st.button` 按钮 | `st.pills(..., selection_mode="single")` 一行胶囊，点击直接填入并 `rerun` | 紧凑 chips、降低首屏决策成本 |
| 本页样式 | 无 | 新增模块常量 `_QA_CSS`，`render` 内 `st.markdown(_QA_CSS)` 注入一次（仅作用于本页） | 答案分隔线、来源卡片间距、pills/按钮圆角统一 |
| 答案区（历史渲染） | `st.markdown(answer)` 直接渲染 | 前置 `qa-answer-start` 细分隔线（无用户内容，零注入风险），答案仍原样渲染 | 答案与来源视觉分层 |
| 溯源区 | `st.expander("🔍 溯源（N 条来源…）")` | `**📚 答案来源**` 标题 + `st.expander("共 N 条来源 · 点击展开可定位原文", expanded=False)` | 标题更直观、默认折叠 |
| 来源卡片 `_render_source` | 跳转按钮文案长「📖 定位到《…》第 N 页」、列宽 `[1.4,1.0,3.0]` | 按钮文案「📖 定位第 N 页」`use_container_width` 贴右对齐；卡片下方加 `qa-src-gap` 统一留白 | 三列对齐、卡片间距一致 |
| 检索调试 `_render_debug` | `st.expander("🛠️ 检索调试", expanded=False)` | `st.expander("🛠️ 检索调试（高级）", expanded=False)` | 明确"高级"、默认折叠、不喧宾夺主（**其本身改前已 `expanded=False`，本次未改变折叠默认行为，仅文案更清晰**） |

### 关键签名保持（诚实确认）
- `render(ctx)` 函数签名：**未变**。
- `anchor_card(source, page, score, para_idx, n)` / `_render_source(rid, i, c)` / `_render_debug(run)` 导出/内部函数签名：**未变**。
- `rag.answer(ctx, q, use_rerank=..., source_filter=..., compare_papers=..., progress=..., on_token=...)` 调用：**逐字保留**，未改任何参数、顺序或语义。本次只动展示层（UI/文案/布局）。
- 未触碰 `ui/common.py`、`app.py`、`services/rag.py`。

---

## 四、自检结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 语法编译 | `python -m compileall -q ui/page_qa.py` | ✅ 0 错误 |
| 模块导入 | `python -c "import ui.page_qa"` | ✅ 成功，且 `render`/`anchor_card` 等导出存在 |
| 密钥安全 | 全文件 grep `.env` / `API_KEY` / `getenv` | 本文件无任何密钥读取/打印/回显逻辑（符合约束） |

---

## 五、风险与限制（诚实列）

1. **未做真实交互渲染**：按约束未启动 `streamlit run`、未触发 LLM，因此"胶囊一行排列、卡片对齐、折叠效果"属代码层推断，未经浏览器肉眼验证。改动均为 Streamlit 1.63 已支持的标准组件（`st.pills` 自 1.37 起存在），回归风险低。
2. **`st.pills` 返回值处理**：`selection_mode="single"` 返回标量或 `None`；代码同时兼容 `str` 与长度为 1 的 `list` 两种可能返回形态，并在点击后 `st.session_state["qa_ex_pills"] = None` 复位，避免"已选胶囊"残留造成视觉误导。
3. **CSS `:has()` 圆角选择器**：`.qa-ex-wrap ...` 与 `div:has(>.stPills)` 用于给胶囊/按钮加圆角；旧浏览器不支持 `:has()` 时**仅不生效**（按钮保持直角），不会报错或破坏布局——优雅降级。
4. **答案渲染仍原样 markdown**：本次未对答案文本做额外 HTML 包裹（仅前一个无用户内容的分隔线 div），与改前行为一致；未引入新的注入面（原有 `st.markdown(answer)` 裸渲染行为被完整保留）。
5. **潜在视觉耦合**：`_QA_CSS` 中 `.stButton>button{border-radius:16px}` 仅在本页 `render` 注入，不影响其他页面；但若后续别处复用 `qa-ex-wrap` 类名需留意作用域。

---

## 六、结论（一句话）

示例问题改为原生 `st.pills` 一行胶囊、答案与「📚 答案来源」来源卡片分层更清晰、溯源与调试默认折叠后置；`rag.answer` 调用与全部导出签名保持不变，编译/导入自检通过，仅展示层优化、零逻辑改动。

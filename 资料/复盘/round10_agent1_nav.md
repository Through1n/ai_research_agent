# Round 10 · Agent1 导航与侧边栏整洁化 · 评审记录

> 角色：1 号选手，负责「导航与侧边栏整洁化」。
> 独占文件：`ui/sidebar.py`、`ui/page_misc.py`（其余 agent 不改动）。
> 真实解释器：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（conda env `ai_research`）。
> 说明：本文为追加式评审记录，诚实列出参考来源、改动与行号、以及风险点。

---

## 1. 参考来源（WebSearch，2026-10-13）

本任务要求借鉴热门应用的侧边栏/导航设计。实际检索并提炼了以下来源（均为公开网页，未读取任何密钥/本地文件）：

1. **Linear 侧边栏克隆解析**（DEV Community, dev48v）
   - URL: https://dev.to/dev48v/i-cloned-linears-sidebar-in-50-lines-of-html-one-file-zero-npm-40ej
   - 要点：固定左侧栏 + 灵活主区（app-shell）；分组标题用小型、弱化（uppercase / 灰色）标签；激活项用更强背景；项与项之间留白；底层 footer 放版本/状态。
2. **SaaS UI Patterns / 导航组件**（saasui.design）
   - URL: https://www.saasui.design/blog/how-to-use-saas-ui-patterns-building-design-system
   - 要点：现代 SaaS 主导导航模式是「可折叠侧边栏 + 分组（section）+ 图标+文字标签 + 激活态高亮」；间距用统一尺度（4/8px 基准）；克制用色。
3. **Sidebar Navigation Design（Girard Media）**
   - URL: https://www.girardmedia.com/blog/sidebar-navigation-design-web-applications
   - 要点：顶层项控制在 7±2（Miller's Law）；用 section header（小型、弱化、非交互标签）分组；侧边栏宽度 240–300px；折叠/展开要有平滑过渡与留白。
4. **Notion 侧边栏清理指南**（NotionFlows / blocs.me）
   - URL: http://notionflows.com/blog-post/clearing-up-your-notion-sidebar-a-notion-consultants-guide-to-navigating-with-clarity
   - URL: https://blocs.me/blog/how-to-make-notion-look-professional
   - 要点：「Less is more」；顶层只保留 5–8 页；用分隔线（divider）分组；用 toggle/折叠隐藏次要内容；危险/低频操作不要与核心导航争夺注意力。

### 提炼的 3 条可落地整洁原则
- **原则 A · 层次与分组**：用小型、弱化的分组标题 + 分隔线把相关项分组；核心功能平铺、展示/维护类收进底部或折叠区；顶层项 ≤ 7±2。
- **原则 B · 留白与间距纪律**：统一间距尺度，分区之间留白（分隔线/卡片），避免拥挤；按钮用 `use_container_width` 保持一致宽度。
- **原则 C · 弱化危险/演示操作**：破坏性操作（清空文献库）视觉次级化并加二次确认；演示按钮配次要说明，不移除功能，避免与导航争夺注意力。

---

## 2. 改动文件与行号

### 2.1 `ui/sidebar.py`（纯视觉/文案/布局，未碰导航状态机）

| 位置 | 改动 | 对应原则 |
|---|---|---|
| L313（新增） | 主区 radio 前加 `st.markdown("**🧭 功能导航**")` 分组标题，强化层次 | A |
| L338（新增 `st.markdown("---")`） | 任务中心面板前加分隔线，与导航区分组（面板保持紧凑、未改动 `render_task_panel`） | A/B |
| L356 `---` + L359（新增 caption） | 演示/维护区加分组分隔线 + 弱化说明「🧪 演示与维护 · 无 Key 也能体验离线功能」 | C/B |
| L359–L361 | 「一键加载演示数据」按钮加 `use_container_width=True` 与 `help=`（不覆盖已有论文）做视觉弱化 + 说明 | C |
| L407–L437（新增） | 「清空文献库」改为**二次确认**流程：首按钮置 `st.session_state["_confirm_clear"]`，点击后弹出 `st.warning` + 「✅ 确认清空 / ❌ 取消」两列按钮；确认才执行原清空逻辑并 `rerun`，取消则复位 flag。`disabled=_clearing` 防止确认期间重复点击。功能完全保留。 | C |
| 原清空逻辑整体内迁 | 原 `if st.button("🗑️ 清空文献库"): ...` 内的 `library.remove_paper` / 清缓存 key / `set_flash` / `st.rerun` 全部原样保留，仅移入「确认清空」分支；空库时仍 `st.info("📭 文献库已经是空的")` | C |

> 注：演示加载与清空按钮的**执行体（含 `st.status`/`st.spinner` 降级分支、`set_flash`、`st.rerun`）逻辑一字未改**，仅外部包了一层确认 UI。

### 2.2 `ui/page_misc.py`（系统架构 / 技术创新亮点，排版整洁化）

| 位置 | 改动 | 对应原则 |
|---|---|---|
| L71 `st.divider()`（新增）+ L73 `with st.container(border=True):` | 架构图与下方要点之间加分隔线；两张要点卡（success/info）包进带边框容器，形成卡片+留白 | B |
| L107 `st.divider()`（新增）+ L107 `st.markdown("#### 🔍 逐项说明")` | 创新总览表与逐项说明之间加分隔线 + 二级标题，层级清晰 | A/B |
| L109–L113（改写） | 原「逐项 expander」改为**两列网格卡片**：`st.columns(2)` + `with st.container(border=True): st.markdown(f"**{i}. {t}**"); st.caption(d)`。标题层级（#/标题/说明）分明，卡片化。 | B/A |

> 导出函数 `render_arch(ctx)`、`render_innov(ctx)` 签名与调用方式**未变**；`_ARCH_DETAIL`/`_ARCH_HTML`/`_INNOV` 数据与文案未改（仅中文展示格式调整）。

---

## 3. 导航状态机保护（关键：未改动）

以下已验证正确的导航核心**完全未触碰**（仅在其周围新增纯静态 markdown / 分隔线 / 按钮分组 UI）：
- `st.session_state["_nav_url_applied"]`（URL 深链首次应用）— 未改。
- `st.session_state["_nav_main_shadow"]` / `_nav_ready` / `_nav_main`（主区 radio 点击判定）— 未改。
- `_pick(item)`（统一选中入口，写 menu + 同步 `?p=`）— 未改。
- 折叠区按钮 `_nav_about_*`（避免两组 radio 互殴）— 未改。
- 主区 `st.radio("🧭 功能导航", MENU_MAIN, key="_nav_main", label_visibility="collapsed")` 的 `key` 与 `index` 逻辑 — 未改；仅在它前面加了一个 `st.markdown` 标题。

---

## 4. 静态自检结果（真实解释器）

```bash
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m compileall -q ui/sidebar.py ui/page_misc.py
# 退出码 0，无报错 → 通过

C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -c "import ui.sidebar, ui.page_misc; print('IMPORT_OK')"
# 输出 IMPORT_OK → 通过
```

- 未启动 `streamlit run`，未触发任何真实 LLM 调用。
- 未读取/打印/回显 `.env` 或任何 API Key；未改动 `.env`。
- 仅对自有文件做静态检查；未运行 `scripts/_smoke_ui.py` / `scripts/_smoke_read.py`。

---

## 5. 我认为可能存在的风险（如实列出）

1. **清空按钮二次确认的交互变化（低风险）**：原「清空」一步到位，现需两步。若评审/用户期望「一键清空」单步完成，体验会变「更稳妥但多一步」。功能等价，非缺陷。
2. **`type="primary"` / `use_container_width` / `st.container(border=True)` 的 Streamlit 版本兼容（极低风险）**：本仓库 `ui/sidebar.py` L149 已在使用 `st.container(border=True)`，且已用 `st.status`/`st.query_params`，故这些 API 在本环境可用；仅当运行环境 Streamlit 极旧才会报错，但现有代码已依赖更高版本。
3. **确认态 flag 残留（极低风险）**：`_confirm_clear` 在确认/取消分支均 `st.rerun()` 前复位；若用户在确认弹窗出现期间刷新页面（非 rerun），flag 可能残留为 True 使首按钮 `disabled`。下次点击「取消」或「确认」即可复位，不影响功能。
4. **`st.divider()` 兼容性（极低风险）**：`st.divider()` 在较新 Streamlit 可用；与 `st.markdown("---")` 等价，可随时回退为 `---`。
5. **page_misc 创新页卡片化后页面更长（无功能风险）**：原用 expander 折叠 12 项，现为两列卡片全展开，首屏更长但信息密度更直观；若偏好折叠可改回 expander。

---

## 6. 一句话结论

**本次改动是否可能破坏导航？** 不可能——所有导航状态机（`_nav_main_shadow` / `_nav_url_applied` / `_nav_about_*` / `_pick` / 主区 radio / URL 深链）均原样保留，仅新增纯展示性的标题、分隔线与「清空二次确认」UI，compileall 与 import 自检均通过。

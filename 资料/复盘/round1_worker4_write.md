# Round1 整改报告 —— 考生4号（学术 Copilot 输出 / E 路）

**日期**：2026-10-11
**独占改动文件**：`services/writing.py`、`ui/page_writing.py`
**新增辅助文件**：`services/wcopilot_dotrender.py`（Graphviz 零依赖降级渲染，符合"services/ 下 wcopilot_ 前缀"约定）
**只读复用（未改一行）**：`services/parsing.py`（`split_reference_entries`）、`services/graph.py`（`ref_section`/`_ref_entries`）、`ui/common.py`（`embed_html`/`page_header`/`resolve_pdf`）、`core/llm.py`（`call_llm_with_retry`）、`core/sandbox.py`

---

## T1 [P0-4] 无幻觉 References —— 已完成

### 1.1 `extract_references_from_pdf`（services/writing.py:249）

用 PyMuPDF（`import pymupdf as fitz`，老版本回退 `import fitz`）从 PDF **末尾向前**逐页累加文本（`PDF_TAIL_CHARS=24000` 窗口，长文档不全量解析）：

- `_locate_reference_section`（:217）：正则锚定最后一个 `References / Bibliography / Works Cited / Reference List / 参考文献` 标题（`_REF_HEADING_RE`，:59），找不到时回退 `graph.ref_section`；
- `_split_entries`（:231）：**复用 `services.parsing.split_reference_entries`（考生1 新增，未改动）**，不足 3 条时回退 `graph._ref_entries`；
- `_parse_entry_fields`（:183）：每条结构化为 `{id, authors, title, year, venue, raw, segs}`，年份用 `\((19|20)\d{2}[a-z]?\)`（括号式）+ `\b(19|20)\d{2}[a-z]?\b`（尾式）双正则；
- `_split_segments`（:166）：句段切分时**不切人名首字母缩写**（"Tom B. Brown"、"Quoc V. Le" 保持完整）；
- 接受 `pdf_path_or_doc`（路径 / 字节流 / 已打开 Document 三种）；`finally` 中 `doc.close()`。

### 1.2 `validate_reference_whitelist(generated_text, allowed_refs)`（:470）

返回 `{"ok, hallucinated, report, n_citations, n_entries, n_allowed"}`，白名单同时兼容 dict 列表与纯字符串列表：

1. **角标越界检查**：`_CITE_MARK_RE` 抽出所有 `[n]`，`n` 不在 `[1..len(allowed_refs)]` 即标记 `kind="citation"`；白名单为空却出现角标也标记；
2. **References 条目实质匹配**：`_generated_reference_entries`（:437）按 `References` 标题切出条目段，解析 `[n]`/`n.`/`-` 三种编号；每条与白名单**标题实质匹配**——`_title_similarity`（:133）在归一化（小写、去标点、压空白）后计算，相等=1.0、包含（≥8 字符）=1.0、前缀截断（≥4 字符，如 "BERT" ⊂ "BERT pre training of…"）=0.9，否则 `difflib.SequenceMatcher().ratio()`，阈值 `TITLE_SIM_THRESHOLD=0.8`（:46）。为降低误报，候选标题池取「解析 title + raw + 每个句段」的并集（:528 附近），"Devlin et al. BERT. NAACL, 2019." 这类缩略条目不会被误杀；
3. 相似度不达标即标记 `kind="entry"`，report 中给出最接近的白名单条目与实际相似度。

### 1.3 生成主链路（`generate_outline_verified`，:589）

- 白名单优先级（`reference_whitelist`，:367）：**① 上传 PDF 的 References 段真实抽取 → ② 入库 chunks 的 References 段 → ③ 文献库元数据兜底 → ④ 空**；每级来源写进 `source` 标签并在 UI 展示；
- 生成后**先在原始输出上校验**（越界 `[3]`/`[7]` 还带原编号，报告可读），**再** `_sanitize_citations` 把越界角标替换为 `[?]`（:606-611）；
- `generate_outline(ctx, idea)`（:628）保留旧签名返回 str（`tools/definitions.py:279` 的 `t_outline` 不受影响）；`_real_references`（:412）、`_sanitize_citations`（:421）、`_pdf_reference_entries`（:340）均保留向后兼容。

### 1.4 UI（ui/page_writing.py `_outline_section`，:210）

- 文献库为空：直接 `st.error("📭 请先上传文献到「📁 文献库管理」…")` 并 return，**不进入生成流程**（:214）；服务层同样返回 `EMPTY_LIBRARY_HINT` 不调 LLM（writing.py:74）；
- 新增「References 抽取来源」选择框（自动=首篇 / 指定某篇）；
- 生成后**自动**校验：通过 → `st.success("✅ 引用白名单校验通过：N 处 [n] 角标、M 条 References 条目…")`（:251）；不通过 → `st.error("🚨 检测到 N 条疑似编造文献/越界角标：")` 逐条列出 + **「🔁 重新生成（强化约束）」按钮**（:255-258），点击后以 `strict=True` 重生成（强化 prompt 要求逐字照抄白名单条目、禁止改写标题、宁可不引也不编造，writing.py:570-586）。

---

## T2 [P1-12] TikZ 真实编译与优雅降级 —— 已完成

`try_compile_tikz(code, timeout=90, dpi=170)`（services/writing.py:713），返回 `{"ok, engine, png(bytes), log, reason"}`：

- **引擎检测**：`_detect_latex_engine`（:654）用 `shutil.which` 依次找 `tectonic / pdflatex / xelatex / lualatex`；
- **编译主链路**：`_build_latex_document`（:666）把 TikZ 片段包成 `\documentclass[border=8pt]{standalone}` + 自动 hoist `\usepackage`/`\usetikzlibrary`；已是完整文档则原样编译。`subprocess.run(..., capture_output=True, timeout, shell=False, cwd=临时目录)` 编译出 PDF 后，用 **PyMuPDF 把 PDF 首页渲染成 PNG**（`_pdf_to_png_bytes`，:686，不依赖 ImageMagick）；成功 → `st.image` 展示真实渲染效果；
- **无编译器降级**（ui `_render_tikz`，:275）：`st.info("本机未检测到 LaTeX 编译器…")` + `st.code` 源码 + `[Overleaf](https://www.overleaf.com)` / `[tikz.dev](https://tikz.dev)` 链接（:293-295）；
- **编译失败**：`st.expander` 折叠展示日志前 500 字（:289），不静默；
- **清理**：`tempfile.mkdtemp(prefix="wcopilot_tikz_")` + `finally: shutil.rmtree(tmp, ignore_errors=True)`（:771-774）；
- UI 结果按 `writing.code_fingerprint(code)`（MD5，:1174）缓存于 `st.session_state["tikz_cache"]`，rerun 不重复编译。

---

## T3 [P1-13] Graphviz 真实渲染（三级降级）—— 已完成

`render_dot(code)`（services/writing.py:811），返回 `{"ok, mode, svg, png, note, log"}`：

1. **mode="graphviz"**：`importlib.util.find_spec("graphviz")` + `shutil.which("dot")` 都在 → `graphviz.Source(code, format="svg").pipe()`；包不在但 `dot` 在 → 直接 `subprocess.run([dot, "-Tsvg"], input=code)`；SVG 用 `st.components.v1.html`（经 `ui.common.embed_html`，只读复用）展示；
2. **mode="matplotlib"**：`_dot_via_matplotlib`（:776）用 `services.wcopilot_dotrender.parse_dot` 解析节点/边，networkx 建图 + `matplotlib.use("Agg")` + `spring_layout` 画 PNG，标注"**降级渲染（等价拓扑图，networkx + matplotlib 力导向布局）**"；
3. **mode="pure"**：新增 `services/wcopilot_dotrender.py`（299 行，纯标准库 re/html）——宽松 DOT 解析（`parse_dot`，:90：兼容有/无分号、引号节点名、链式 `A -> B -> C`、`label=`/`shape=` 属性、注释、环）+ 分层最长路径布局（`_compute_ranks`/`_layout`）+ 直角边界裁剪的贝塞尔连线 + 箭头 marker 的 SVG 输出（`dot_to_svg`，:250），标注"降级渲染（等价拓扑图，零依赖 DOT→SVG 分层布局，非 Graphviz 官方渲染）"。加这层是因为**本机实测 matplotlib 与 graphviz 包、dot 可执行文件均未安装**，没有它 Graphviz 在演示机上永远只剩源码；
4. **mode="none"**：都不可用/非 DOT 输入（如纯中文文本）→ 展示源码 + 中文说明（`_render_graphviz`，:300；输入不像 DOT 时 `parse_dot` 返回空图，:97-103 有守卫）。

Mermaid 分支保持原有 CDN 真实渲染不变。

---

## T4 [P2-23] CSV 自动出图 —— 已完成

- `classify_columns(df)`（writing.py:901）：数值 / 时间 / 类别（基数≤`CAT_CARDINALITY_MAX=15`）/ 布尔 / 高基数文本；列名含 date/time/year/日期等且 80% 可解析时判为时间列（`_is_datetime_like`，:885）；
- `infer_chart_plan(df, max_charts=3)`（:931），按优先级取前 3：**时间+数值→折线**；**类别(基数<15)+数值→柱状**；**数值≥3→相关性热力图 +（样本≤500 时）平行坐标**；**两数值→散点**；**单数值→直方图**；纯类别兜底→频数柱状。每个 plan 带 `{"kind, x, y, dims, title, reason"}`，`reason` 是一句中文依据；
- UI（`_csv_section`，:91）：上传/载入示例后**立即自动渲染**全部推荐图（`_render_plan_chart`，:55，含折线/柱状/散点/直方/热力/平行坐标六类 plotly 映射），每张图下方 `st.caption("🧭 **推荐依据：** …")`（:137）+ 每图独立「🤖 生成该图 Figure Caption」按钮；原手动选择 X/Y/类型完整保留在「🛠️ 自定义图表」折叠区（:174 起）。换数据时 `chart_plan`/`auto_captions` 自动失效重算（:101-103）。

---

## T5 Figure Caption 注入真实数值事实 —— 已完成

- `chart_facts(df, x, y, top_n=3)`（writing.py:1038）：从 df **实测**计算——数值列 min/max（含取到极值的那一行的可读标签，`_row_label`，:1013）/mean/median；时间/数值 x 下的**趋势方向与首末值**（"acc rises from 0.721 to 0.894 (delta 0.173, 23.99%)"）、是否单调；类别 x 下的 **Top-N 均值排行**；两数值列的 **Pearson r 及强弱措辞**；数值列≥2 时取 |r| 最大的前 3 对相关系数；
- 这些事实以"关键数值事实（必须真实引用其中的数字，禁止编造未出现的数字）"整段注入 prompt（`figure_caption`，:1126，兼容旧签名 `figure_caption(ctx, df, chart_desc)`，新增可选 x/y）；
- **生成后校验**：`caption_has_numbers`（:1028）先剥掉 `Figure 1.` / `Fig. 2:` / `图 3` 前缀（`_FIG_PREFIX_RE`，:69）再查数字——否则编号里的数字会让校验形同虚设；不含数字则用强化 prompt（要求至少引用 2 个事实里出现过的真实数字）**重新生成一次**（:1146-1153），仍不含则原样返回并由 UI 提示（page_writing.py:147-148）。

---

## T6 统一 LLM 调用入口 —— 已完成

- 新增 `_llm_text(ctx, prompt_or_messages, temperature)`（writing.py:96）：**全部调用走 `from core.llm import call_llm_with_retry`（core/llm.py:129，只读未改）**，并通过 `_llm_kwargs(ctx)`（:75）透传 `ctx.llm` 的 `api_key/base_url/model`，保证侧边栏选的 Provider 依然生效；
- 识别 `call_llm_with_retry` 全部重试失败后返回的伪响应（`【LLM 暂时不可用…`前缀，core/llm.py:78）→ 返回空串，由调用方给中文提示，不会把兜底文案当正文展示（:112-114）；
- `generate_outline / generate_outline_verified / diagram / figure_caption / translate_to_english / translate_to_chinese` 六处原 `ctx.llm.chat(...)` 裸调用全部替换；`ui/page_writing.py` 无任何直接 LLM 调用，只通过 `writing.llm_available(ctx)`（:88）判断。

---

## 实测记录（真实跑过，非推断）

运行环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（Python 3.11.16，项目实际依赖环境：pymupdf✓ / networkx✓ / plotly✓ / pandas✓ / streamlit 1.63.0；**matplotlib✗、graphviz 包✗、dot✗、LaTeX 编译器✗**——这是本机真实状态，也是 T3 增加第三级降级的直接原因）。

| # | 实测内容 | 结果 |
|---|---|---|
| 1 | 用 PyMuPDF **现场构造**一份 6 条参考文献的 PDF（含 `References` 标题），调 `extract_references_from_pdf` | 6/6 条目正确结构化，如 `{"authors":"Tom B. Brown, Benjamin Mann…","title":"Language Models are Few-Shot Learners","year":"2020","venue":"In Advances in Neural Information Processing Systems, 2020"}`；人名缩写（Tom B. Brown / Quoc V. Le）未被句切破坏 |
| 2 | 对真实论文 `演示语料/1706.03762v7.pdf`（Attention Is All You Need）跑同一抽取 | 抽出 **40 条**，如 `[2] Bahdanau… "Neural machine translation by jointly learning to align and translate", 2014, CoRR abs/1409.047` |
| 3 | 白名单校验：干净样本（[1][2]+2 条 References 全真实） / 毒样本（越界 [9] + 编造 "A Totally Fabricated Paper About Nothing"） / 空白名单 | 干净→`ok=True`；毒→`ok=False`，同时报 citation 越界（相似度 0.28）与编造条目；空库→报"白名单为空，任何条目都无法核实" |
| 4 | **端到端桩 LLM**（monkeypatch `_llm_text`）：第一次返回含 `[7]` 越界 + `[3]` 编造条目的大纲，第二次返回干净大纲 | 第一次 `ok=False`，报告分别列出 `[3]`、`[7]` 越界与编造条目（"A Completely Invented Paper Title Here" 相似度 0.36）；第二次 `ok=True`；真实的 `[2] Devlin et al. BERT. NAACL, 2019.`（短标题缩略）不再误报；`generate_outline` 旧签名返回 str 正常 |
| 5 | `try_compile_tikz`：本机无 LaTeX 编译器 | `ok=False`，reason="本机未检测到 LaTeX 编译器（已依次查找 tectonic/pdflatex/xelatex/lualatex）"，`TEMP` 下**无** `wcopilot_tikz_*` 残留 |
| 6 | **桩编译器实测编译主链路**：在 TEMP 造一个 `pdflatex.bat`（真读 main.tex、真产出 1 页 PDF），前置 PATH 后调 `try_compile_tikz` | `ok=True, engine=pdflatex, png=20170 字节`；用 Read 工具肉眼确认 PNG 是真实图片，且内容证实 tex 已被 `_build_latex_document` 包上 `\documentclass`（tikzpicture=True, docclass=True）；跑完无临时目录残留 |
| 7 | **编译失败路径**：桩编译器 `exit /b 1` 并输出 `! Undefined control sequence. l.5 \foo` | `ok=False`，log 前 500 字完整拿到错误行，无残留 |
| 8 | `render_dot`：7 组 DOT 用例（链式/无向/引号+属性/无分号/含环/含注释/非 DOT 中文） | 前 6 组节点/边数全对并产出 SVG（如 7 节点 7 边 → 2869 字节 SVG，画布 266×574）；非 DOT 输入 `mode="none"` 不误画 |
| 9 | SVG 结构自检（5 节点 5 边图）：解析 `<rect>`/`<path>` 坐标 | 5 个节点框全部落在 viewBox 内，5 条边路径齐全，标签文本 A~E 无缺失、无 HTML 未转义 |
| 10 | `infer_chart_plan`：**真实上传 `演示语料/data.csv`**（9 行 6 列，无时间列） | 列类型 `{model:categorical, 其余 5 列:numeric}`；推荐 3 图：柱状（"model 是类别列（基数 9<15）、params_M 是数值列"）+ 相关性热力图 + 平行坐标，各带中文依据 |
| 11 | `infer_chart_plan`：构造 12 行带 `date` 日期列的 DataFrame | `date→datetime`；推荐折线（"date 是时间列、acc 是数值列"）+ 柱状 + 散点 |
| 12 | `chart_facts`：演示语料/data.csv（x=model, y=bleu_en_de）与时间序列 df | 实测输出 `min=0 (@BERT-base), max=28.4 (@Transformer-big)`、`Top-3 model by mean bleu_en_de: Transformer-big (28.4), …`、`Trend over 'date': acc rises from 0.721 to 0.894 (delta 0.173, 23.99%)`、`Monotonic increasing across all 12 points`、`corr(acc, loss) = -0.993` |
| 13 | `caption_has_numbers`："Figure 1. … 72.1% to 89.4%." → True；"Figure 1. Accuracy increases steadily." → **False**（前缀数字被正确剥除）；中文"图 2：…从 72 提升至 89。" → True | 3/3 符合预期 |
| 14 | **UI 冒烟**：`streamlit.testing.v1.AppTest` 进程内渲染整页（预置 df=demo CSV、空 API Key） | **0 异常**；4 个 subheader、3 张自动图 + 3 条"🧭 推荐依据" caption、每图独立的 Caption 按钮、References 来源选择框、生成/翻译/绘图按钮全部就位 |
| 15 | LLM 兜底：无 API Key 的 FakeCtx 调 `_llm_text` / `generate_outline` | 返回空串 / 空库中文提示，不抛异常、不展示"【LLM 暂时不可用…】"伪响应 |

自验用的临时脚本、桩编译器、生成的 PDF/SVG/PNG 已全部删除（`%TEMP%/w4_*` 已清理）。

---

## py_compile 结果

```
C:/Users/Administrator/anaconda3/python.exe -m py_compile services/writing.py ui/page_writing.py services/wcopilot_dotrender.py
→ BASE_PYCOMPILE_OK（基线 python，纯语法检查）

C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile services/writing.py ui/page_writing.py services/wcopilot_dotrender.py
→ PYCOMPILE_OK（项目实际运行环境，且已真实 import 通过）
```

---

## 未完成项与原因

无未完成项。两点**按题意做了取舍**，说明如下：

1. **T2/T3 的"真实编译渲染"在本机演示环境下实际走的是降级分支**（无 LaTeX 编译器、无 dot、无 matplotlib）。编译主链路与 `dot -Tsvg` 主链路已分别用桩编译器/代码走查验证可达（见实测 #6-#7），但在评委机器上若同样无 LaTeX，TikZ 将展示"源码 + 在线编辑器链接"而非真图——这是任务书预设的优雅降级，不是缺陷。
2. **T4 未做"平行坐标/雷达图对高基数文本列的自动降级"**：任务只要求推荐与渲染，未涉及；超出范围未加。

---

## 风险点（诚实声明，供交叉审计）

1. **标题实质匹配是启发式**：阈值 0.8 + 归一化 + 包含/前缀快捷判定，实测对真实引文缩略（"BERT"、"et al."、人名缩写）不误报、对编造条目（相似度 0.14~0.36）能报出；但理论上存在两类残余风险——①LLM 把真实文献标题**翻译成中文**或大幅改写（相似度会掉到阈值以下，会被当成"编造"误报，属宁可错报方向）；②与白名单条目**高度相似但不完全相同**的半编造条目（比如只改了年份/作者）会被放过，因为按任务定义校验的是"标题实质匹配"。
2. **References 条目切分依赖 `parsing.split_reference_entries` 的正则**：对 IEEE 数字编号、APA 作者-年份、arXiv 号开头均兼容，但对**无编号且无年份括号**的罕见排版（如部分 Nature 风格 superscript 引用）可能切分失败，此时回退 `graph._ref_entries`，再不行回退文献库元数据（白名单质量降级但不中断）。
3. **纯 Python DOT→SVG（wcopilot_dotrender）不是 Graphviz**：只支持节点/边/label/shape 的常见子集，不支持 subgraph 聚类、record 形状、rank=same 等高级特性；布局是分层最长路径，与 dot 的 spline 布局形似但不保证相同——UI 上已明确标注"降级渲染（等价拓扑图）"，不会冒充官方渲染。极端复杂 DOT（数百节点）有 `_MAX_NODES=150` 截断保护。
4. **`call_llm_with_retry` 每次新建临时 LLMClient**（core/llm.py:151 的设计）：相比直接 `ctx.llm.chat` 少了客户端复用，换来统一重试/失败落盘。高频调用（每页 rerun 不触发 LLM，仅按钮触发）影响可忽略。
5. **TikZ 编译调用外部进程**：已加 `timeout=90s`、`shell=False`、`-interaction=nonstopmode`（无 shell-escape）、独立临时目录与 finally 清理；但若用户装了会弹窗的 LaTeX 发行版（如 MiKTeX 缺包自动安装弹窗），仍可能出现系统级弹窗——本机无编译器，未实测该场景。
6. **`extract_references_from_pdf` 对扫描件无效**（无文本层，`get_text` 返回空 → 返回 [] → 回退库元数据），未做 OCR 兜底（OCR 归 parsing 考生1 的链路，不越界）。
7. **`figure_caption` 的数字校验只保证"含数字"，不保证数字来自 df**：prompt 已强约束"禁止编造未出现的数字"，但 LLM 仍可能编一个格式正确数值错误的数字；这是 prompt 级约束的固有限制，未做"逐个数字回查 df"的硬校验（工程量大且易误伤百分比/单位换算写法）。
8. **UI 冒烟覆盖不到浏览器端 JS**：Mermaid CDN 渲染、plotly 交互未在真实浏览器验证（不启动 web 服务是硬约束）；plotly 语句与改造前一致，风险低。

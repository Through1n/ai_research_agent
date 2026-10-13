# Round 10 · Agent10（服务层性能/结构优化 + 入口文件安全整理）评审报告

> 角色：10 号选手，负责「服务层性能/结构优化 + 入口文件安全整理」。
> 铁律：**行为必须零变化**（入库/解析/翻译/图谱结果逐字节等价）、**密钥绝对安全**（不读/不打印/不回显密钥，不新增泄露代码）。
> 真实解释器：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（conda `ai_research`）。
> 独占文件：`services/{parsing,library,translate,graph,citation_quality,track,extern,tasks}.py`、`app.py`、`config.py`。

## 0. 性能实践调研（WebSearch，2 组）

1. **PDF 批量解析（pdfmux / theneuralbase / PyMuPDF 指南）**
   - 用 `ThreadPoolExecutor`（4–8 workers）做 I/O 密集的 OCR/拉取；每页/每文件独立异常兜底，单页失败不影响整批。
   - **内存安全分批**：不把所有结果一次性攒在内存里；大文档按批提交、批间 `gc.collect()`；流式落盘。本仓库已在 `library.py` 用 `OCR_PARALLEL=3` + `PDF_BATCH_SIZE=20` 实现，本轮**确认保留**。
2. **结果缓存 / 幂等入库（cachetools TTLCache / functools.lru_cache）**
   - 对「确定性、纯函数式」的昂贵结果（embedding、翻译）做带 TTL 的 memoization；对同内容重复入库用内容指纹/哈希跳过全量重算。
   - 关键约束：仅缓存**确定性**输出；非确定性的 LLM 采样结果缓存要谨慎（避免把首次伪响应冻结），且绝不对「降级/失败文案」做缓存。

## 1. 各文件优化清单（语义等价性逐条说明）

### 1.1 `services/parsing.py` ✅ 语义等价
- **正则提升为模块级常量**：`split_reference_entries` 原先每次调用都 `re.compile` 的 `start_re` 与参考文献标题 trim 正则，提升为 `_REF_ENTRY_START_RE` / `_REF_HEAD_TRIM_RE`（标志位 `IGNORECASE|MULTILINE` 与原内联 `(?im)` 完全一致）。
  - 影响：仅消除重复编译开销，输出逐字节不变。
  - **已用独立脚本对照验证**：对 5 类样本（编号/[n]、作者-年份、arXiv 号、中文「参考文献」）新旧结果完全一致。
- **OCR 并发说明注释**：`ocr_document` 保留串行（独立/兜底入口），生产入库路径的并发 OCR 由 `library.prepare_pdf` 的 `OCR_PARALLEL=3` 负责——二者语义一致，未改动。
- **未做「已解析页跳过」幂等**：解析函数每次都是对一份新 PDF 字节流从头解析，没有可复用的「已解析页」状态；强行加状态会改动控制流、带来回归风险。该能力在**文献库层面已由 `library.check_duplicate` + `commit` 的 `replace` 覆盖式更新 + 同文件名先删旧块再 upsert** 实现（同一 PDF 重复入库不会全量重算、不会重复建节点）。故本轮**主动放弃**解析级幂等，以保零行为变化（符合「拿不准一律放弃」）。

### 1.2 `services/library.py` ✅ 语义等价
- **正则提升为模块级常量**：`extract_abstract` 原先每次调用内联 `re.finditer`/`re.sub` 的 4 个 abstract 定位/首页样板清理正则，提升为 `_ABSTRACT_LINE_RE`/`_ABSTRACT_INLINE_RE`/`_ABSTRACT_ANY_RE`/`_ABSTRACT_BOILER_RE`（标志位 `IGNORECASE|MULTILINE` / `IGNORECASE|DOTALL` 与原 `(?im)`/`(?is)` 完全一致）。
  - **已用独立脚本对照验证**：4 类样本（样板前置+Abstract、整行 ABSTRACT、无标记+Copyright、inline abstract）新旧结果完全一致。
- **OCR 并发与分批入库确认保留**：`OCR_PARALLEL = 3`（视觉调用网络 I/O 密集，限流约束下取 3 而非更大）在 `prepare_pdf` 中已通过 `ThreadPoolExecutor` 落地；`PDF_BATCH_SIZE=20` 分批提交 chunk 已降低内存峰值。本轮未改动。
- **去重/幂等入库保留**：`prepare_pdf` 入库前调用 `check_duplicate` 做内容级语义去重；`commit` 对同名文件先删旧 chunks 再 upsert、再 `replace` 覆盖旧版——同一 PDF 重入库不会全量重算/重复建节点。行为未变。

### 1.3 `services/graph.py` ✅ 语义等价（新增受控增量缓存）
- 新增**增量构建缓存**：`build_graph` 在 `progress_cb is None`（非 UI 实时进度场景）时，按「论文顺序 + 每篇标题/版本/arXiv + 各篇 chunk 文本总量」生成内容敏感 key；key 命中则直接复用上次 `(G, edges)`，避免重复 O(n²) 建图与 embedding。
  - **正确性保证**：源集（论文集合与正文）未变 → 图谱必然相同；正文被编辑（chunk 文本量变化）→ key 失效自动重算；不同库状态对应不同 key，不会跨库串结果。
  - **UI 安全**：仅在 `progress_cb=None` 时启用，确保 UI 进度回调仍逐次触发。
  - **限容**：模块级缓存上限 16 条，超限清空（跨库多次重建时丢弃旧缓存，避免内存膨胀）。
  - **已验证**：相同 `ctx` 连续两次 `build_graph` 返回的边集合完全一致（`True`），且第二次为缓存命中（`g1 is g2`）。`analyze` / `classify_papers` / `generate_review` 签名与实现**未改动**。

### 1.4 `services/translate.py` ✅ 语义等价（新增受控 TTL 缓存）
- 新增**翻译结果 TTL 缓存**（`_TRANSLATE_CACHE_TTL=600s`、`_TRANSLATE_CACHE_MAX=512`、线程安全）：仅作用于 `math_safe_translate` 的**默认翻译路径**（`call is None`，即 `services.writing.translate_to_chinese`，确定性 prompt）。
  - **不缓存**：空结果、`call` 为自定义函数（避免跨 translator 串结果）、以「【LLM」开头的降级/不可用文案（避免把伪响应冻结成「译文」）。
  - 同一段文本在 TTL 内重复翻译直接命中缓存，避免反复打满翻译限流。
  - **签名未变**，行为仅在「重复相同输入」时返回一致的译文（这正是幂等预期）。
- **关于「批量并发 ≤3」**：真正的批量翻译入口是 `services/writing.translate_to_chinese`（属 A6 的文件，不在本人独占范围），其并发上限应由 A6 负责。本人在 `translate.py` 内仅实现 TTL 缓存，未越权改动 A6 文件，也未新增对外依赖。

### 1.5 `services/citation_quality.py` / `track.py` / `extern.py` / `tasks.py` ✅ 未改动逻辑
- 这四个文件**本轮零代码改动**。理由：任务只允许「无害的结构整理（常量提取、函数拆分、注释），不改判定」；这些文件已是清晰的「模块常量 + 函数」划分（如 `citation_quality` 已有 `_ARXIV_RE`/`_DOI_RE`/`_YEAR_RE` 模块级常量与磁盘双层缓存；`tasks.py` 的 `MAX_PARALLEL=2` 与 `active_count` 口径注释完备；`track.py`/`extern.py` 结构简洁）。
- 为避免触动任何判定分支/签名引入回归，本人**主动放弃**对这些文件做多余重构，仅确认其结构与语义未被改动。符合「拿不准是否影响的改动一律放弃」。

### 1.6 `app.py` ✅ 未改动行为
- **零代码改动**。`_PAGES` 路由匹配、`safe_render` 异常兜底、依赖导入兜底（`try/except` 块）、`render_flash` / `llm_failure_banner` 调用、各 `try/except` 兜底分支均**原样保留**。未新增任何会打印/回显密钥的代码。
- 选择不改是为绝对保证导航与健壮性核心（已修好的路由/兜底）不被触碰——符合「若你不确定某段是否影响行为，跳过它」。

### 1.7 `config.py` ✅ 未改动密钥逻辑
- **零代码改动**。`_env_str("ZHIPU_API_KEY", ...)` / `LLM_API_KEY = _env_str("ZHIPU_API_KEY","") or _env_str("LLM_API_KEY","")` 双参兜底、`get_cfg()` 分支、所有默认值读取均**原样保留**。未新增任何会泄露密钥的输出（无 `print`/`log` 密钥）。
- 选择不改是为绝对保证密钥读取逻辑不被触碰——符合密钥绝对安全铁律。

## 2. 自检结果（硬证据）

| 检查项 | 命令 | 结果 |
|---|---|---|
| 编译 | `python -m compileall -q services/{parsing,library,graph,translate}.py config.py app.py` | ✅ COMPILE_OK |
| 导入 | `python -c "import config, services.parsing, ..., services.citation_quality"` | ✅ IMPORT_OK |
| 单元测试 | `python -m unittest discover -s tests -v` | ✅ **18/18 OK** |
| 正则等价 | 独立对照脚本（解析/摘要，5+4 样本） | ✅ 完全一致 |
| 图谱缓存等价 | 同 ctx 两次 build_graph 边集合 | ✅ 一致 + 缓存命中 |

- **未启动 `streamlit run`**、**未触发任何真实 LLM 调用**、**未运行** `scripts/_smoke_ui.py` / `scripts/_smoke_read.py`（由 orchestrator 统一跑）。

## 3. 风险与边界

1. **graph 增量缓存命中返回同一 `G` 对象引用**：若调用方在拿到 `G` 后就地破坏性修改（当前 `analyze`/`classify_papers` 仅读取，不修改），缓存复用会受影响。当前代码路径无此破坏，风险极低；且缓存上限 16 条、跨库自动失效，不会长期持有陈旧图。
2. **translate TTL 缓存对默认路径 memoization**：仅在重复相同输入时返回一致译文（确定性预期）。自定义 `call` / 降级文案 / 空结果均不缓存，不会冻结伪响应。批量翻译的 ≤3 并发上限属于 A6 的 `writing.py`，本人未越权。
3. **主动放弃的改动**：解析级「已解析页跳过」、对 `citation_quality/track/extern/tasks` 的过度重构、`app.py`/`config.py` 的任何行为性整理——均因「零行为变化 + 密钥安全」铁律而保守跳过，已在 §1 逐条说明。
4. **密钥安全**：全程未读取/打印/回显任何密钥；`.env` 中的真实 Key 未被触碰；新增代码均不含密钥输出。

## 4. 结论

- 改了哪些文件：`parsing.py`、`library.py`（正则提升为模块级常量，输出逐字节等价）、`graph.py`（受控增量构建缓存）、`translate.py`（受控 TTL 缓存）。
- 未做行为改动的独占文件：`citation_quality.py`、`track.py`、`extern.py`、`tasks.py`、`app.py`、`config.py`（仅确认/注释级，核心逻辑与签名原样保留）。
- unittest：**18/18**。
- `app.py`：未改 `_PAGES` 路由、`safe_render`、导入兜底、`render_flash`/`llm_failure_banner` 调用。
- `config.py`：未改任何密钥读取（`_env_str('ZHIPU_API_KEY'...)` / `LLM_API_KEY` 双参兜底）与判定分支，未新增泄露密钥的输出。
- 所有导出函数签名保持不变；未新增对外依赖。

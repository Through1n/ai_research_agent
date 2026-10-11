# 第一轮考生1号：文档感知与长文档模块修复报告

## 修改范围

本轮仅修改以下两个 Python 文件：

- `services/parsing.py`
- `services/library.py`

并新增本交付报告 `docs/review/round1_worker1_doc.md`。未修改 `config.py`、`graph.py`、`ui/` 或其他 `.py` 文件。

## T1 [P0-3] 双栏 PDF 文本提取

- 修改函数：`_detect_two_columns`、`extract_page_blocks_column_aware`、`extract_page_text_column_aware`、`native_page_texts`、`_blocks`。
- 位置：`services/parsing.py:96-201`；入库调用点为 `services/library.py:264-267`。
- 解决的问题：不再直接按 PyMuPDF 默认 block 顺序生成 RAG 页面文本。先读取 `page.get_text("dict")` 的 bbox、line、span，再对窄文本块中心做一维双峰聚类；同时检查左右簇数量、中心间距、中线位置和纵向重叠，降低单栏误判。
- 阅读顺序：双栏时按 `column`、`y0`、`x0` 排序，即左栏自上而下后右栏自上而下；块内 line 也按 `y0/x0` 排序。单栏时保留原始 block 顺序。
- 块结构：每个块新增 `column`，单栏固定为 0，双栏为左 0/右 1；原 `_blocks(doc)` 仍返回 `(page_idx, text)`，未破坏旧签名。

关键代码（`services/parsing.py:168-173`）：

```python
for block in blocks:
    x0, _, x1, _ = block["bbox"]
    block["column"] = 0 if (x0 + x1) / 2.0 < split_x else 1
return sorted(blocks, key=lambda block: (
    block["column"], block["bbox"][1], block["bbox"][0], block["order"]
))
```

## T2 [P1-8] Figure ↔ caption 关联

- 修改函数：`_caption_blocks`、`_caption_distance`、`_nearest_caption`、`extract_figures`。
- 位置：`services/parsing.py:497-605`。
- 解决的问题：从当页文字块中识别以 `Figure N`、`Fig. N`、`图 N` 开头的图注，保留其 bbox；从 `page.get_image_rects(xref)` 取得图片 bbox，只在水平重叠比例至少 15% 的候选中选择垂直距离最近者。
- 输出变化：保留原有 `fig["desc"]`，新增原文 `fig["caption"]` 和 `fig["bbox"]`；无法定位 bbox 或图注时 `caption` 严格为空字符串，不生成内容。
- 容错：页面取图、图片 bbox、图片解码、保存和图注匹配均有局部 `try/except`，单图失败继续后续图片。

关键代码（`services/parsing.py:574-583`）：

```python
image = Image.open(io.BytesIO(data["image"]))
if image.width < 150 or image.height < 150:
    continue
try:
    rects = page.get_image_rects(xref) or []
except Exception:
    rects = []
caption, image_bbox, caption_index = _nearest_caption(rects, captions, used_captions)
if caption_index is not None:
    used_captions.add(caption_index)
```

## T3 [P1-11] 541 页长文档工程化

- 修改函数：`_extract_page_from_raw`、`_extract_page_with_timeout`、`_notify_pdf_progress`、`prepare_pdf`、`commit`。
- 位置：`services/library.py:109-337`、`services/library.py:340-351`。
- 分批处理：模块级常量位于 `services/library.py:16-18`，每 20 页形成一批；每批立即生成 chunks，并用 `page_offset` 保持真实页码。`commit` 优先消费预生成的 `_chunks`，避免提交阶段再次对整篇页面文本切块。
- 超时保护：每页使用独立守护线程，并在线程内打开独立 PyMuPDF 文档实例，主线程 `join(timeout)`；超过默认 30 秒立即把该页置空、写入 `failed_pages`，继续下一页。独立文档避免超时线程与主文档并发访问同一 PyMuPDF 对象。
- 错误恢复：打开失败返回兼容空结果；单页异常记录 `{"page": 1-based页码, "error": 原因}`，并安全调用旧有 `on_page_error(page_index, reason)`。
- 进度：第 50、100……页及最后一页调用 `progress_cb(done, total, msg)`；若回调只接受旧的两个参数，则自动回退到 `progress_cb(done/total, done)`。
- 返回兼容：旧字段全部保留，新增 `failed_pages`、`toc`、内部 `_chunks`；`prepare_pdf` 原有位置参数和可选参数未删除。

关键代码（`services/library.py:171-177`）：

```python
thread = threading.Thread(target=worker, daemon=True,
                          name=f"pdf-page-{page_index + 1}")
thread.start()
thread.join(timeout)
if thread.is_alive():
    return None, None, f"单页处理超过 {timeout:g} 秒，已跳过"
```

## T4 [P2-19] 层级树回退

- 新增/修改函数：`heading_page_data`、`infer_headings_from_text`、`extract_toc`；`prepare_pdf` 增加空 TOC 回退。
- 位置：`services/parsing.py:350-468`；`services/library.py:307-311`。
- 解决的问题：无内置书签或 `get_toc()` 异常时，不再只返回空目录。
- 推断规则：按字符数加权统计 span 字号众数作为正文尺寸；候选行必须少于 80 字，并满足“大于正文字号”“flags 含 16/font 名含 Bold”“行首为 1./1.1/第1章/第1节”等条件之一。
- 层级：数字编号按点号深度决定；“章/篇/部分”为一级、“节”为二级；无编号标题按候选字号从大到小映射层级。返回结构与 `doc.get_toc()` 一致：`[(level, title, page), ...]`。

关键代码（`services/parsing.py:415-423`）：

```python
size = float(line.get("size", 0.0) or 0.0)
bold = bool(line.get("bold")) or bool(int(line.get("flags", 0) or 0) & 16)
bold = bold or "bold" in str(line.get("font", "")).lower()
number_match = _HEADING_NUMBER_RE.match(title)
cn_match = _HEADING_CN_RE.match(title)
numbered = bool(number_match or cn_match)
larger = bool(body_size and size >= body_size + max(0.8, body_size * 0.08))
bold_heading = bool(bold and (not body_size or size >= body_size * 0.95) and len(title) <= 60)
if not (larger or bold_heading or numbered):
    continue
```

## T5 [P2-20] 参考文献切分增强

- 新增函数：`split_reference_entries(text)`。
- 位置：`services/parsing.py:662-708`。
- 支持格式：`[1]`、`1.`、`1)`、行首 `(2020)`、`Author (2020)`、年份后缀字母及 `arXiv:2401.xxxxx`/版本号。
- 折行处理：以下一个起始标记为边界截取条目，再把条目内部的换行、缩进和重复空白合并；无起始标记时按空行段落回退；最后按大小写无关键去重。
- 函数整体失败时返回空列表，不向上抛异常。

关键代码（`services/parsing.py:674-680`）：

```python
r"\[\d{1,4}\][ \t]+|"
r"\d{1,4}[.)][ \t]+|"
r"\((?:19|20)\d{2}[a-z]?\)[ \t]+|"
r"arXiv\s*:\s*\d{4}\.\d{4,5}(?:v\d+)?\b|"
r"(?:[A-ZÀ-ÖØ-Þ][\wÀ-öø-ÿ'’\-]+(?:\s*,?\s*(?:[A-Z]\.?|[A-ZÀ-ÖØ-Þ][\wÀ-öø-ÿ'’\-]+)){0,5}"
r"(?:\s+(?:et\s+al\.?|and\s+[A-ZÀ-ÖØ-Þ][\wÀ-öø-ÿ'’\-]+))?)"
r"[.,]?[ \t]+\((?:19|20)\d{2}[a-z]?\)"
```

## T6 统一 LLM 调用入口

- 导入位置：`services/parsing.py:11`、`services/library.py:11`。
- 修改函数：`ocr_page_text`（`services/parsing.py:221-249`）、`describe_figures`（`services/parsing.py:608-642`）、`version_diff`（`services/library.py:450-473`）。
- 解决的问题：删除 `llm.vision(...)` 和 `ctx.llm.chat(...)` 裸调用，统一走 `core.llm.call_llm_with_retry`；视觉调用自行构造 OpenAI 兼容的 base64 `image_url` 消息，并透传当前 provider 的 `api_key`、`base_url`、`vl_model`。
- 失败行为：读取统一入口返回的伪响应；外围仍有 `try/except`，确保响应结构异常不会中断 OCR、图表描述或版本比较。

关键代码（`services/parsing.py:628-634`）：

```python
response = call_llm_with_retry(
    messages,
    model=getattr(llm, "vl_model", None),
    temperature=0.1,
    **_llm_kwargs(llm),
)
fig["desc"] = _llm_response_text(response)
```

## 向后兼容说明

1. `_blocks(doc)` 仍返回 `(page_idx, text)` 二元组；带 `column` 的详细块通过新增函数获取。
2. `prepare_pdf` 原签名参数顺序未变，`timeout_per_page` 默认值仍为 30；旧两参数进度回调继续可用。
3. `build_chunks_from_texts(page_texts)` 旧调用可用，新 `page_offset=0` 为可选参数。
4. `extract_figures` 保留 `page/index/path/desc`，只新增 `caption/bbox`。
5. `prepare_pdf` 保留全部原返回字段，只增加 `failed_pages/toc/_chunks`；`commit` 对没有 `_chunks` 的历史 prepared 对象仍回退到原切块流程。

## 未完成项和原因

- 功能任务 T1-T6 均已实现，无主动省略项。
- 未执行真实 541 页 PDF 端到端运行：指定 Anaconda 解释器可以完成 `py_compile`，但当前 shell 中实际导入 `pymupdf` 和 `openai` 均报 `ModuleNotFoundError`；遵守“不 pip install”约束，未修改环境。已使用模拟 PyMuPDF 页面、模拟文档和模拟 LLM 模块完成核心逻辑冒烟。

## 自验结果

指定命令：

```text
C:/Users/Administrator/anaconda3/python.exe -m py_compile services/parsing.py services/library.py
```

结果：退出码 `0`，stdout/stderr 均为空，两个文件语法编译通过。

补充自验：

- `git diff --check -- services/parsing.py services/library.py`：退出码 `0`；仅提示 Git 后续可能把 `library.py` 的 LF 转为 CRLF，无空白错误。
- 模拟双栏页面：得到 column 序列 `[0,0,0,0,1,1,1,1]`。
- 参考文献样例：`[1]`、`2.`、`Smith (2020)`、`arXiv:2401.12345` 正确切为 4 条。
- 标题样例：`1. Introduction` 推断为 `(1, "1. Introduction", 1)`。
- 超时样例：0.2 秒任务配 0.05 秒阈值能返回超时原因；51 页模拟入库仅在第 50 页和最后第 51 页回调。
- 旧进度回调：两参数函数收到 `(0.5, 50)`，兼容路径生效。
- 图注最近邻：同一水平范围内正确选择距离图片更近的 `Figure 1. Near`。

## 仍存在的风险点

1. 双栏检测是几何启发式。三栏、极不对称双栏、跨栏文本占主导或每栏不足两个有效文本块时会保守回退单栏；个别杂志版式仍可能误判。
2. 图注关联仅覆盖 PyMuPDF 可列举的内嵌栅格图和同页图注。纯矢量图、多子图共用一个 caption、跨页 caption 或图片无 `get_image_rects` bbox 时会得到空 caption。
3. Python 线程无法安全强制终止。超时页会被主流程立即跳过，但对应守护线程可能继续运行到 PyMuPDF/LLM 调用自行返回；连续大量超时页可能短时保留多个后台线程。
4. 为避免 PyMuPDF 文档对象跨线程并发，每页线程会从原始 bytes 打开独立文档；这是安全优先方案，但超长且 xref 很大的 PDF 会有重复打开成本。
5. 为保持 `prepare_pdf` 旧返回结构，最终仍必须同时返回 `page_texts` 和 `full_text`，因此无法做到完全零拷贝；本轮降低的是解析和 chunks 构建过程的额外峰值，并按 20 页批量生成 chunks。
6. 标题推断对 OCR 页面没有可靠字号/粗体元数据，只能依赖编号规则；References 中很短的编号条目也存在被误识别为标题的可能。
7. 参考文献规则未覆盖所有引文风格，例如完全无年份、无编号且无空行分隔的连续作者条目；这类输入只能作为一个回退段落返回。
8. 当前指定 Python 环境缺少可导入的 `pymupdf/openai`，所以本轮没有真实 PDF 与真实视觉 LLM 集成验证；部署环境依赖完整性需要后续统一确认。

# 第一轮 · 考生6号（页面3 阅读与翻译 + 性能鲁棒性 + LLM 统一重试）修复报告

- **独占文件（只改这 3 个）**：`services/translate.py`、`ui/page_reading.py`、`core/llm.py`
- **新建文件**：`scripts/_memory_probe.py`（B1 性能探针）、`scripts/_smoke_read.py`（冒烟自验）
- **未改动**：`ui/common.py`、`ui/page_qa.py`、`ui/page_writing.py`、`services/parsing.py`、`services/library.py`、`services/rag.py`、`services/graph.py`、`config.py`、`app.py` 等（可 `git diff` 核对）
- **未启动 `streamlit run`、未 pip install、未调真实 LLM API**
- **环境事实（重要）**：本机 `C:/Users/Administrator/anaconda3/python.exe`（base）**缺 `pymupdf` / `openai`**，项目实际可运行环境是 `C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（3.11.16 / pymupdf 1.28.2 / streamlit 1.63.0 / psutil 7.2.2 / openai 3.13.0）。所有"实测"均在该环境跑出；两个解释器都做了 `py_compile`（base 只做语法检查）。

---

## 任务组 A：页面 3 四个已知缺陷专项修复

### A1 原生渲染双栏 PDF 文字块坐标重叠

| 项 | 内容 |
|---|---|
| 旧实现 | `services/translate.py:49-75` `native_page_html()`：把每个 **span** 绝对定位到页面坐标（`position:absolute;left:x;top:y`），`white-space:pre` 且**没有任何宽度约束**；`_ordered_dict_blocks` 最后又 `sorted(..., key=_block_y)` 全局按 y 重排，DOM 顺序与栏序无关 |
| 问题 | ①块/行宽度超过栏宽时文字会一路画到另一栏上（串栏）；②DOM 顺序不是"左栏读完读右栏"，跨栏复制/选中会串行；③块内 span 若被 PyMuPDF 返回重叠 bbox，会互相压字 |
| 新实现函数 | `detect_columns()` `services/translate.py:60`；`_assign_column()` `:144`；`_block_html()` `:166`；`native_page_html()` `:374` |

**1）双栏检测（:60）**——x 方向"覆盖率剖面"找中缝，而不是朴素地要求"存在一段完全空白"：

```python
# services/translate.py:60-125（节选）
cov = [0.0] * _COL_BINS                       # _COL_BINS = 140 (:29)
for x0, y0, x1, y1 in blocks:
    h = max(0.0, y1 - y0)                     # 用块的"竖直高度"做权重，而不是块数量
    for i in range(int(x0/bin_w), int(x1/bin_w)+1):
        cov[i] += h
region = sorted(cov[s:e+1]); med = region[len(region)//2]
thr = med * _LOW_COVER_RATIO                  # _LOW_COVER_RATIO = 0.25 (:30)
# 找最宽的"低覆盖连续段" → 中缝；宽度 < _MIN_GUTTER(12pt, :28) 视为单栏
if len(left) >= 2 and len(right) >= 2:        # 两侧各有 ≥2 块才敢判双栏
    res["n_cols"] = 2; res["gap"] = (g0, g1)
    res["bounds"] = [(min(b[0] for b in left), g0), (g1, max(b[2] for b in right))]
```
> 为什么不用"完全空白带"：实测通栏标题 / 通栏图注 / 超宽行内公式会把中缝填满，朴素算法直接退化成单栏。用覆盖率（带 y 权重）后，一个通栏标题只贡献 ~14pt，正文栏贡献 ~240pt，中缝仍是明显低谷。

**2）分栏容器渲染（:374）**——每块按 `_assign_column()` 打上栏号，渲染进**独立容器**：

```python
# services/translate.py:415-450（节选）
def _col_div(idx, c0, c1):                    # 左右栏各自一个 position:absolute 容器
    return ('<div class="col" id="col%d" style="left:%.1fpx;width:%.1fpx;height:%.1fpx;">%s</div>'
            % (idx, c0, max(20.0, c1 - c0), ph, body))
# 单块：rel_x 是"相对所在栏左边界"的 x，max_w 被夹到栏内剩余宽度
max_w = (c1 - c0) - rel_x - 2.0
if max_w < 12.0:                              # 块起点已越出栏右边界 → 夹回栏内
    rel_x = 0.0; max_w = max(12.0, c1 - c0 - 2.0)
# 跨中缝的块（通栏标题/图注）→ -1，单独进 #full 层，DOM 顺序排在最前
body_layers = gutter + full_html + cols_html  # 中缝(底) → 通栏 → 左右栏(顶)，也是阅读顺序
```

- **y 坐标原样保留**（`top:{y0}px`，与旧版一致），只是被限定在所属栏的容器内；
- **限宽**：`max-width` + 行内 `white-space:pre-wrap`（`:166-213` 的 `_block_html`），超宽自动换行而不是溢出；
- **栏间分隔**：`.gutter` 元素（`services/translate.py:424` 生成 / `:448` CSS）画在中缝位置，浅色斜纹 + 两侧虚线；
- **块内改为常规流**（每个 block 一个 `.blk` 绝对定位 div，内部 line 用 `<div class="ln">` 常规流），杜绝块内 span 重叠；
- **`st.components.v1.html` 适配**：`_HEIGHT_JS`（`:215`）用 `ResizeObserver` + `window.parent.postMessage({type:'streamlit:setFrameHeight'})` 上报高度（全部 try/catch，不支持时静默失败），Python 侧仍按 `min(int(ph*1.18)+60, 900)` 给兜底高度；配色全浅色（页面 `#fff`，背景 `#f5f7fa`，hover `#fff3b0`）。

**实测（真实 PDF，`论文样本/1810.04805v2.pdf`，BERT v2 确实是双栏）**：16/16 页全部判为双栏，共渲染 **434 个块，越界块 0，全局最大越界 −1.9px**（即所有块都严格在栏内，还留 2px 余量）。
**实测（自建夹具，复现"串栏"场景）**：故意插入一行从 x=60 起、实际跨度到 x=595 的超宽行 → 旧规则下会一路压到右栏；新渲染被 `max-width` 夹成 **248px（栏宽 250px）**，越界 0。

> **诚实交代**：在 `论文样本/` 现有干净 arXiv 语料上，**旧渲染并没有复现肉眼可见的串栏**——实测 BERT p2/p7/p11/p12 共 748~908 个 span，超过本栏右边界的 **0 个**（PyMuPDF 给的 span bbox 本身就落在栏内）。本修复提供的是**结构性保证**（任何 bbox 异常 / 行内长公式 / OCR 层 / 通栏元素都不会串栏）+ **阅读顺序修正**（左栏→右栏）+ **块内重叠消除**，并用自建夹具把"会串栏"的场景复现出来验证了兜底有效。

---

### A2 同步滚动翻译左右不同步

| 项 | 旧（`services/translate.py:78-110`） | 新（`services/translate.py:465-564`） |
|---|---|---|
| 同步方式 | `src.scrollTop/(src.scrollHeight-src.clientHeight\|\|1)` 算完直接赋给 `dst.scrollTop`——**没乘目标的可滚动距离**，两栏内容高度不同时必然错位 | `dst.scrollTop = ratio(src) * maxScroll(dst)`，纯**比例**同步 |
| 除零 | `\|\|1` 只在 `src` 侧兜底，`dst` 侧 `scrollHeight-clientHeight` 为 0 时会算出 `ratio*0` | `maxScroll()` 统一返回 `max(0, ...)`，`m <= 0` 直接 return（`:487-492`） |
| 抖动/死循环 | `syncing=true` 后用 `requestAnimationFrame(()=>syncing=false)` **同一帧**释放，而程序化 scroll 事件在下一个渲染帧才派发 → 回弹互相触发 | `isSyncing` + **双重释放**：`requestAnimationFrame(release)`（下一帧，scroll 事件已派发完）+ `setTimeout(release, 200)`（后台标签页 rAF 不触发时兜底）（`:495-520`） |
| 节流 | 无（scroll 高频触发直接同步） | `pendingRAF` 保证**一帧最多同步一次**（`:512`） |
| 高亮 | 用 `q.dataset.i === i` 比较（其实是 data 属性，但查找用的是 `p[data-i=...]`，可工作；问题在于纯文本重复时视觉跳错） | 统一 `data-index`，`highlight()` 只按 `getAttribute('data-index')` 匹配，`t = b.querySelector('p[data-index="' + idx + '"]')`（`:530-548`），**永不按文本查找** |

```js
// services/translate.py:487-520（关键片段）
function maxScroll(el){ var m = el.scrollHeight - el.clientHeight; return (m > 0) ? m : 0; }
function ratio(el){ var m = maxScroll(el); return m <= 0 ? 0 : (el.scrollTop / m); }
function bindScroll(src, dst){
  src.addEventListener('scroll', function(){
    if (isSyncing) return;          // 正在同步 → 忽略被同步方回调，防互相触发死循环
    if (pendingRAF) return;         // rAF 节流：一帧最多同步一次
    pendingRAF = window.requestAnimationFrame(function(){
      pendingRAF = 0; isSyncing = true;
      try{ applyRatio(src, dst); }
      finally{ window.requestAnimationFrame(release);   // 下一渲染帧再释放
               releaseTimer = window.setTimeout(release, 200); }  // rAF 不触发时的兜底
    });
  }, {passive:true});
}
```

**实测（Node 22 + 自写极简 DOM 桩，6/6 通过）**：
| 场景 | 结果 |
|---|---|
| 左栏 500/1500（1/3）→ 右栏 | 右栏 `scrollTop = 500`（比例一致）✅ |
| 被同步方回弹触发 scroll | 左栏保持 500 不变（isSyncing 生效）✅ |
| 二次手动滚动（标志位已释放） | 左 1200/1500 → 右 1200 ✅ |
| `scrollHeight <= clientHeight`（除零） | 不崩、右栏保持 0 ✅ |
| 段落高亮（6 段） | 只有 `data-index=3` 的左右两段被置 active ✅ |

---

### A3 划词翻译后选区未清空

> 行号说明：任务单给的 `ui/page_reading.py:102-117` 是旧版位置；当前文件的划词翻译区在 `:826-864`（`render()` 内 `tabs[4]`），选区本身发生在**嵌入的 iframe HTML 里**，所以修复主战场在 `services/translate.py` 的 JS。

| 项 | 实现 | 位置 |
|---|---|---|
| 捕获即清空 | `clearSelection()`：`window.getSelection().removeAllRanges()` → 失败回退 `document.getSelection().empty()` → 再 `document.activeElement.blur()`（不影响 INPUT/TEXTAREA） | `services/translate.py:262-272`（`SELECTION_JS` 内） |
| hash 去重 | `lastHash = h32(txt)`（djb2 变体）+ `DEDUP_MS = 2500`；`if (h === lastHash && now-lastAt < DEDUP_MS) return;` | `:278-282` / `:317-322` |
| 长度 < 2 忽略 | `MIN_LEN = 2`，`if (txt.length < MIN_LEN) return;`（先 `replace(/\s+/g,' ').trim()`） | `:279` / `:318` |
| 触发时机 | `mouseup` / `touchend`(+30ms) / `dblclick`(+30ms)；另外 `window` 捕获阶段滚动时若还有残留选区也清掉 | `:325-331` |
| 宿主回传 | `window.parent.postMessage({type:'ai4s:selection', text})` + 写 `parent.document#ai4s_sel_sink`（均 try/catch） | `:305-315` |
| 剪贴板 | `navigator.clipboard.writeText` → 失败回退 `execCommand('copy')` | `:274-291` |
| Python 侧 | `tabs[4]` 用 `selectable_text_html()`（`services/translate.py:734`）渲染可选中文本；另有 `_cache_key()`（`ui/page_reading.py:445`）+ `st.session_state["read_tr_cache"]` 做**译文级去重**，同一段不会重复调用模型 | `ui/page_reading.py:826-864` |

**实测（Node 22 + DOM 桩，7/7 通过）**：
| 场景 | 结果 |
|---|---|
| 正常划词 ≥2 字 | `removeAllRanges` 被调用 1 次，选区变空 ✅ |
| 回传宿主 | `postMessage {type:'ai4s:selection', text:'attention is all you need'}` ✅ |
| 同一段重复触发 | 不再处理（removeCalls 1→1）✅ |
| 换新文本 | 正常触发（1→2）✅ |
| 长度 1（误触/单击） | 完全忽略，选区保留 ✅ |
| 无 `removeAllRanges` 的浏览器 | 回退 `empty()` 也清空 ✅ |
| 剪贴板写入 | 被调用（供 Ctrl+V）✅ |

> **诚实交代（硬限制）**：`st.components.v1.html` 是**单向**嵌入（非自定义组件），JS 拿到的选中文本**无法自动回填**到 Streamlit 的 `st.text_area`。当前方案是"捕获 → 立即清空选区 → 自动复制到剪贴板 → 用户到下方输入框 Ctrl+V"。要做成"选中即自动填框"必须写真正的 Streamlit 自定义组件（需要新增前端目录 + 注册组件），超出本轮独占文件范围，未做。

---

### A4 原位 PDF 输出中文字体乱码

| 项 | 旧（`services/translate.py:113-138`） | 新（`services/translate.py:567-706`） |
|---|---|---|
| 字体兼容 | 直接 `np.insert_text(..., fontname="china-s")`，**except 分支回退到不带 fontname 的 insert_text（默认 Helvetica，CJK 必然丢失/乱码）** | `_cjk_font()`（`:567`）：`importlib` 依次试 `pymupdf.Font("china-s")` / `fitz.Font("china-s")`，取不到也返回 `"china-s"` 名字而非回退 Helvetica |
| 排版 API | `insert_text` 逐行写，手写 `max_chars = (pw-40)/6.5` 估算，固定 `lines[:17]` 硬截断，**没有省略号** | `page.insert_textbox(box, cand, fontname="china-s", fontfile=None, fontsize=fs, color=(0,0,0), align=0)` |
| 放不下（返回负值） | 无处理 | 三级回退：①字号 9→8→7→6→5 逐级缩小重试；②`_fit_chars()`（`:584`）用 `font.text_length()` 精确估算可容纳字数，截断并追加 `…`；③`_insert_lines_fallback()`（`:601`）逐行 `insert_text`（仍带 `fontname="china-s"`） |
| 老版本兼容 | — | `except TypeError` 捕获老版 `insert_textbox` 不接受 `fontfile/align` 的情况，改用精简参数重试（`:678-686`） |
| 编码 | — | 全程 `unicode` 字符串；`nd.save(out_path, garbage=3, deflate=True)`（异常回退 `nd.save(out_path)`）；`ui/page_reading.py:783` 用 `open(out, "rb")` 读原始字节 → `st.download_button(..., mime="application/pdf")`，不做任何 `str()`/编解码中转 |
| 目录 | `tempfile.mkdtemp()` 直接拼路径，无清理 | `tmp_dir = tempfile.mkdtemp(prefix="fig_inplace_")` + **`finally: shutil.rmtree(tmp_dir, ignore_errors=True)`**（`ui/page_reading.py:799-802`） |

```python
# services/translate.py:672-690（关键片段）
for fs in (9.0, 8.0, 7.0, 6.0, 5.0):          # ①缩小字号重试
    cand = text
    try:
        keep = _fit_chars(font, text, fs, box.width, box.height)   # ②按可容纳字数截断
        cand = text[:keep]
        if keep < len(text): cand = cand.rstrip() + "…"
        rest = np.insert_textbox(box, cand, fontname=fontname, fontfile=None,
                                 fontsize=fs, color=(0, 0, 0), align=0)
        if isinstance(rest, (int, float)) and rest >= 0: done = True; break
    except TypeError:                          # 老版本不接受 fontfile/align
        ...
if not done: _insert_lines_fallback(np, box, text, fontname)       # ③逐行兜底
```

**实测（真实 PDF `论文样本/1706.03762v7.pdf`，120 字中文译文）**：生成 96,433 字节 PDF → 用 PyMuPDF 反解文本，提取到 **79 个连续汉字**（`是一种完全基于注意力机制的模型摒弃循环与卷积结构编码器六层堆叠…`），**无 U+FFFD 替换符**。
**超长回退实测**：输入 2,880 字 → 触发截断，落盘 1,078 字，文件仍正常生成（省略号实际渲染为 `⋯`，是 Droid Sans Fallback 的字形映射，非乱码）。

> **诚实交代**：**旧实现在当前 PyMuPDF 1.28.2 下并没有复现乱码**——我照旧代码跑了一遍（`insert_text(fontsize=8, fontname="china-s")`），得到 25 个汉字、无 U+FFFD。也就是说"乱码"的真实触发路径是旧代码 `except` 分支**回退到不带 `fontname` 的 `insert_text`（默认 Helvetica）**，以及老版本 PyMuPDF 无内置 china-s 时抛异常后静默丢字。本轮把这条回退路径彻底堵死（任何失败都不再退化成非 CJK 字体），并补齐 `insert_textbox` + 负值回退链。这一条**我不能声称"修好了可复现的乱码"**，只能说修掉了乱码的成因路径并加了兜底。

---

## 任务组 B：性能与鲁棒性

### B1 内存监控

**新文件 `scripts/_memory_probe.py`**：`MemoryProbe` 类，接口 `start()` / `sample()` / `peak_mb()` / `report()` / `text()` / `stop()` / `reset()` / `delta_mb()` / `elapsed()`。

- 后端自动降级：`psutil`（进程 RSS，最准）→ `tracemalloc`（Python 层分配峰值，Windows 也有；本机无 `resource` 模块）→ `none`（只统计耗时，`peak_mb()` 返回 0.0）；
- 全程 try/except，**探针缺失/失败都不会崩页面**；
- `friendly_note()` 在 psutil 缺失时返回中文说明（"数值通常小于进程真实 RSS"）；
- 采样表上限保护：超过 4096 条自动裁剪（`:101-104`）。

接入点：`ui/page_reading.py:92 _new_probe()`（拿不到真探针时用同接口的 `_NullProbe`，`:54`），用于 ①`_long_doc_scan()` 长文档扫描（`:268`）②`_translate_pairs()` 批量翻译（`:401`）③原位 PDF 生成（`:772`）④引用跳转扫描（`:812`）。扫描完成后 `st.info(f"✅ 扫描完成：{total} 页 / {chars:,} 字符 / 抽取图片 {figs} 个 · 峰值内存 {peak:.0f} MB / 耗时 {elapsed:.1f} 秒")`。

**实测**：
| 场景 | 结果 |
|---|---|
| 探针自校准（申请 32MB） | `backend=psutil`，`start 41MB → peak 111MB，delta +40MB`（精确） |
| BERT 15 页扫描 | 39,498 字符 · **峰值 235 MB** · 0.2s |
| BERT 15 页 + 抽图 | 抽取 3 张图 · **峰值 257 MB** · 0.5s |
| 541 页 `UnderstandingDeepLearning_02_09_26_C.pdf` | 1,313,319 字符 · **峰值 283~306 MB** · 3.4~3.6s（多次跑） |
| **旧做法 vs 新做法对照（541 页，同一进程）** | 旧 `''.join(p.get_text() for p in doc)`：**74 → 127 MB，增量 +53 MB**；新逐页处理：**124 → 125 MB，增量 +0 MB**（字符数都是 1,313,319） |

### B2 长文档处理加固

全部集中在 `ui/page_reading.py`：

| 要求 | 实现 | 行号 |
|---|---|---|
| 不一次性 `get_text()` | `_scan_one_page(doc, pno)`（`:239`）单页取文本 + 判栏数，取完 `del`，主循环不再持有全文 | `:239-252`, `:290-300` |
| 分批释放 | 每 `BATCH_FLUSH = 50` 页 `gc.collect()` + `probe.sample()` | `:39`, `:334-338` |
| 每 50 页更新进度 | `if i % PROGRESS_EVERY == 0 or i == total: _progress(pb, i/total, ...)`；`_progress()`（`:132`）兼容不支持 `text=` 的旧 Streamlit | `:38`, `:339-340` |
| 单页 > 30s 跳过并记录 | `_run_with_timeout(_scan_one_page, PAGE_TIMEOUT=30.0, doc, pno)`（`:154`）→ 单线程池 + `future.result(timeout=30)`，`TimeoutError` 转 `(False, None, "timeout>30s")`，页码进 `timed_out[]`，最后 `st.warning("⏱️ N 页处理超时（> 30s）已跳过：...")` | `:154-184`, `:291-312`, `:357-359` |
| 解析失败记录并继续 | `_run_with_timeout` 吞掉所有异常 → 页码进 `failed[]` → `st.warning("⚠️ N 页解析失败已跳过：...")` | `:303-312`, `:360-362` |
| 图片临时目录清理 | `fig_dir = tempfile.mkdtemp(prefix="fig_")`，**`finally: shutil.rmtree(fig_dir, ignore_errors=True)`**；原位 PDF 的 `fig_inplace_*` 目录同样 `finally` 清理 | `:280-284`, `:345-349`, `:768`, `:799-802` |
| 清理陈旧临时文件 | `cleanup_stale_figure_dirs(max_age_hours=24)`（`:186`）扫描 `tempfile.gettempdir()` 下 `fig_*` 目录，`mtime` 超 24h 就 `rmtree`；扫描结束自动跑一次，也有独立按钮「🧹 立即清理陈旧图片临时目录」 | `:186-213`, `:350-352`, `:795-801` |
| 引用跳转不再全库 join | 旧 `"".join(p.get_text() for p in doc)` 全量拼接 → 新 `_find_citation()`（`:565`）**从后往前逐页扫描**（`CITE_SCAN_CAP = 260` 页上限），命中即停，尾部文本只在必要时累积 ≤60KB 供 `ref_section()` 兜底 | `:565-614` |

**实测**：
- `_run_with_timeout`：`sleep(2)` 配 `timeout=0.5` → `(False, None, "timeout>0.5s")`，**不抛异常**；`raise ValueError` → `(False, None, "ValueError: 模拟页面解析崩溃")`；正常函数 → `(True, 42, None)`。
- `cleanup_stale_figure_dirs(24)`：48h 前的 `fig_stale_*` 目录被删除（removed ≥1 且路径消失）；新建的 `fig_fresh_*` 目录**未被误删**。
- AppTest 里勾选"抽取页面图片"跑 15 页：抽取 3 张图后，`fig_*` 目录数 `before=1 after=1`（**无残留**）；UI 输出"扫描完成：15 页 / 39,498 字符 / 抽取图片 3 个 · 峰值内存 257 MB / 耗时 0.5 秒"。

> **诚实交代**：Python **无法强行杀掉**阻塞中的工作线程。`_run_with_timeout` 超时后主线程不再等待、直接记录页码并继续下一页，但那个后台线程会跑完才退出（PyMuPDF 的 C 调用释放 GIL，不会卡死主线程）。这是"软超时"，不是抢占式中断。

### B3 翻译/LLM 调用并发保护与超时

| 机制 | 实现 | 行号 |
|---|---|---|
| 并发上限 | `MAX_TRANSLATE_WORKERS = 3`，`ThreadPoolExecutor(max_workers=min(3, len(todo)))` | `:35`, `:427` |
| 单次超时 | `LLM_CALL_TIMEOUT = 60.0`；每个任务内部再套 `_safe_translate(..., timeout=60)` → `_run_with_timeout`；外层 `fut.result(timeout=75)` 兜底 | `:36`, `:352`, `:429-433` |
| 顺序还原 | `futs = {ex.submit(...): i}`，按下标回填 `pairs[i]`，并发不串行乱序 | `:428-447` |
| 本地缓存 | `_cache_key()`（md5）→ `st.session_state["read_tr_cache"]`，命中直接跳过调用（实测全命中时提示"♻️ 全部 N 段命中本地缓存"） | `:445-451`, `:385-399` |
| 失败兜底 | `_safe_translate` 返回 `(译文, err)`；失败段落保留原文，`st.warning("⚠️ 以下段落翻译失败（已保留原文，未影响其余段落）：第 N 段（原因）")` + `st.caption("🛠️ 怎么办：…")` | `:352-368`, `:449-453` |
| 无 Key 守卫 | 所有 LLM 分支先判 `ctx.llm.available`，否则 `st.warning("⚠️ 未检测到可用 API Key，无法调用翻译模型。")` 而不是崩 | `:757-759`, `:786-788`, `:856-858` |
| 页面级兜底 | `render()` 整个 doc 生命周期套 `try/finally: doc.close()`；每个 tab 内部再包一层 `try/except → st.warning` | `:617-620`, `:884-887` |

**实测**：AppTest 在无 API Key 下点「📥 加载并逐段翻译（段落对齐）」→ 无异常、出现中文 warning `未检测到可用 API Key，无法调用翻译模型。`（页面不崩）。

---

## 任务组 C：统一 LLM 重试机制（`core/llm.py`）

### C1 退避严格统一为 1.5 / 3.0 / 4.5

单一来源常量（`core/llm.py:29-33`）：

```python
MAX_RETRIES = 3                     # 退避重试次数（不含首次尝试）
RETRY_BACKOFF = (1.5, 3.0, 4.5)     # 第 1/2/3 次重试前的等待秒数
SDK_MAX_RETRIES = 0                 # 关掉 SDK 自带重试，避免 3×3 叠加
FAIL_LOG_LIMIT = 2000
```
`_backoff_for(attempt)`（`:36`）集中取值；`_create`（`:150`）、`stream`（`:190`）、`vision`（`:232`）**全部走同一份实现**。

**语义说明（按需求"或明确说明当前实现已满足语义"这一条）**：
- 旧 `_create` 只有 **3 次尝试**、两次 sleep（1.5 / 3.0），第 3 次失败直接 `break`——**4.5 这个退避值根本不会被用到**；
- 新实现 = **1 次立即尝试 + 3 次退避重试（1.5 / 3.0 / 4.5）= 最多 4 次调用**，3 次重试全部失败后才返回伪响应。这与需求"3 次失败后返回友好伪响应"一致（3 次**重试**失败），且让 `RETRY_BACKOFF` 三个值都真实生效，也与原 docstring「3 次指数退避（1.5s/3s/4.5s）」的意图吻合。
- 同时把 `OpenAI(..., max_retries=2)` 改成 `max_retries=0`：否则 SDK 自身还要再重试 2 次，最坏 4×3=12 次 HTTP 请求。改后恒定 ≤4 次。

**实测**：桩化 client（每次 `create` 抛异常）→ `stub.calls = 4`，总耗时 **9.25s**（1.5+3.0+4.5=9.0 + 调度开销）。

### C2 `call_llm_with_retry` 与 `_create` 行为一致

`call_llm_with_retry`（`:264`）仍构造临时 `LLMClient` 后**委托 `client._create(**kwargs)`**，所以重试/兜底语义天然一致；额外新增 `timeout` 透传（`kw.pop("timeout", None)`），其余参数一个没动。3 次重试失败后返回 `_fake_response`，**不抛异常**。

### C3 失败日志增强

`_log_llm_failure(err, messages, model=None, caller=None)`（`:97`）——**签名向后兼容**（两个新参数都带默认值，旧的两参调用行为不变）。新增：
- `_dump_messages()`（`:82`）：`json.dumps(ensure_ascii=False, default=str)` 后**截断到前 2000 字符**并追加 `...[已截断，共 N 字符]`；
- `_caller_info()`（`:68`）：`inspect.stack()` 跳过本模块帧，返回 `文件:行号 函数名()`；
- 记录字段：`ts / error_type / error(≤800) / model / caller / n_messages / messages_preview`；
- 写文件用 `errors="replace"`，**整个函数包在 try/except 里**，落盘失败绝不影响主流程。

实测落盘记录（`storage/llm_failures.log`）：
```json
{"ts":"2026-10-11 04:22:08","error_type":"RuntimeError","error":"stub boom 4",
 "model":"vl","caller":"_smoke_read.py:285 part1()","n_messages":1,
 "messages_preview":"[{\"role\": \"user\", \"content\": [{\"type\": \"text\", ..."}
```

### C4 `stream()` 补重试

旧 `stream()` **完全没有重试**（一次异常直接 yield 错误）。新实现（`:190-230`）：
- 同样 `for attempt in range(MAX_RETRIES + 1)` + `_sleep_backoff(attempt)`（1.5/3.0/4.5）；
- **关键保护**：`produced` 标志——一旦已经 yield 过任何内容就**不再重试**（否则用户会看到重复输出），直接结束并给出提示；
- 全部失败 → `_log_llm_failure` + yield 一条中文兜底文案，**不抛异常**。

实测：桩化 4 次全失败 → `calls=4`、`chunks=['【LLM 暂时不可用，请稍后重试。错误摘要：stub boom 4】']`、耗时 9.0s。

### C5 `vision()` 统一重试

旧 `vision()` 走 `self.chat()`（间接有重试）。新实现（`:232-256`）改为**直接 `self._create(model=self.vl_model, messages=[msg], temperature=...)`**，与 chat/stream 同一通道；另外补了 base64 编码失败的兜底（`return "【图片编码失败，无法调用多模态模型。】"`）和返回结构异常的兜底。

实测：`calls=4`，返回 `【LLM 暂时不可用，请稍后重试。错误摘要：stub boom 4】`。

### C6 向后兼容（最关键）

**没有删除、没有改名、没有改签名**。已逐项核对：

| 对外符号 | 状态 |
|---|---|
| `_fake_response(content)` | 原样保留（`:58`） |
| `parse_json(text)` | 原样保留（`:304`） |
| `LLMClient(api_key='', base_url=None, model=None, vl_model=None, timeout=90.0)` | 签名一致（`:133`），仅新增属性 `self.last_error` |
| `LLMClient.available / chat / stream / vision / json_chat / _create` | 签名全部一致 |
| `call_llm_with_retry(messages, *, model, temperature, max_tokens, response_format, tools, tool_choice, **kw)` | 签名一致（`:264`），新增从 `**kw` 里取 `timeout` |
| `_log_llm_failure(err, messages)` | 两参调用完全兼容 |

**全项目 import 回归实测**（`ai_research` 环境逐个 `importlib.import_module`）：`core.llm` / `services.graph` / `services.library` / `services.parsing` / `services.rag` / `services.writing` / `services.state` / `services.query_expansion` / `core.guardrails` / `ui.page_reading` / `ui.page_qa` / `ui.common` / `tools.definitions` —— **13/13 全部 OK**。

---

## 跨文件对接：消费 `qa_jump_page`

`ui/page_reading.py:454 _consume_qa_jump(ctx, tps)`，完全按考生2 报告 §"给考生6"的约定实现：

```python
# ui/page_reading.py:454-510（节选）
payload = st.session_state.get(JUMP_KEY)      # JUMP_KEY = "qa_jump_page" (:34)
if not isinstance(payload, dict) or not payload.get("source"): return None
page = min(max(1, int(payload.get("page", 1) or 1)), max(1, int(tps or 1)))
para = int(payload["para_idx"]) if payload.get("para_idx") else None
_drop_state(("np", "sp", "pp", "hp"))         # 删掉 widget 状态，否则新跳转会被旧状态吞掉
st.session_state["read_jump_page"] = page; st.session_state["read_jump_para"] = para
st.success(f"🎯 已从问答页跳转：《{label}》第 {page} 页{tail}")
st.toast(f"已从问答页跳转 → 第 {page} 页", icon="🎯")
st.session_state[JUMP_KEY] = None             # 清理，避免重复跳转
for k in ("jump_page", "paper"): del st.query_params[k]
```

- 论文定位：`render()` 里先取 `prefer = payload["source"]`，传给 `_resolve_doc(ctx, prefer)`（`:513`），用 `selectbox(index=sel_opts.index(prefer))` 预选中——**不用 `st.session_state["read_sel"] = ...`，避免 Streamlit 的 "default value + session_state" 警告**；
- 页码定位：算出 `default_page`，6 个 tab 的 `st.number_input(..., value=default_page, key="np"/"sp"/"pp"/"hp")` 全部用它；
- 段落：存进 `read_jump_para`（`para_idx`），成功/提示文案里带"（第 N 段）"。**注意：`para_idx` 是考生2 的估算值，阅读页只做到"提示 + 记录"，没有做视觉高亮**——原因见"未完成项"。

**实测（AppTest）**：
1. 预置 `session_state["qa_jump_page"] = {"source":"1810.04805v2.pdf","page":7,"para_idx":3,"from":"qa"}` → success 文案 `已从问答页跳转：《1810.04805v2》第 7 页（第 3 段）` ✅
2. 消费后 `qa_jump_page` 变成 `None` ✅
3. 二次 `run()` 不再出现跳转提示（不重复跳转）✅
4. `jump_page` / `paper` query_params 已删除 ✅

---

## 实测结果汇总（全部真实跑出）

| # | 项目 | 命令 / 方式 | 结果 |
|---|---|---|---|
| 1 | `py_compile`（base anaconda3） | `anaconda3/python.exe -m py_compile services/translate.py ui/page_reading.py core/llm.py scripts/_memory_probe.py` | **0 报错** |
| 2 | `py_compile`（实际环境） | `envs/ai_research/python.exe -m py_compile <同上> scripts/_smoke_read.py` | **0 报错** |
| 3 | 阅读页 AppTest 冒烟 | `envs/ai_research/python.exe scripts/_smoke_read.py` | **PASS 59 / FAIL 0** |
| 4 | A2/A3 JS 行为模拟 | Node 22 + 自写 DOM 桩 | A2 **6/6**、A3 **7/7** |
| 5 | JS 语法检查 | `node --check` 导出的 8 个 `<script>` 块 | **8/8 OK** |
| 6 | A1 真实双栏 PDF | `论文样本/1810.04805v2.pdf` 全 16 页 | 16/16 判双栏，**434 块、越界 0、最大越界 −1.9px** |
| 7 | A1 串栏复现 | 自建双栏夹具 + 超宽行（60→595pt） | 旧规则必串栏；新渲染夹到 **248px ≤ 栏宽 250px**，越界 0 |
| 8 | A4 真实中文叠加 | `论文样本/1706.03762v7.pdf` p1 + 120 字译文 | 96,433 字节 PDF，反解出 **79 个汉字，无 U+FFFD** |
| 9 | A4 超长回退 | 2,880 字译文 | 截断到 **1,078 字**落盘，文件正常 |
| 10 | B1 探针校准 | 申请 32MB | psutil 后端，`+40MB` 精确测到 |
| 11 | B1+B2 长文档 | `UnderstandingDeepLearning_02_09_26_C.pdf` **541 页** | 1,313,319 字符 · **峰值 283~306 MB** · **3.4~3.6s** |
| 12 | B2 内存对照 | 541 页：一次性 join vs 逐页 | **+53 MB → +0 MB**（字符数相同） |
| 13 | B2 超时/失败 | `_run_with_timeout` | 超时→`timeout>0.5s`、异常→`ValueError:…`、正常→42，**均不抛** |
| 14 | B2 临时目录 | 勾选抽图扫 15 页 + 陈旧目录清理 | 抽 3 图后 `fig_*` **无残留**；48h 旧目录被删、新目录不误删 |
| 15 | C 重试 | 桩化 client 全失败 | `_create` / `stream` / `vision` 均 **4 次调用、总退避 9.25s**、返回中文伪响应 |
| 16 | C 兼容 | 13 个相关模块逐个 import | **13/13 OK**，签名逐项核对无变化 |

---

## 未完成项 / 部分完成项（诚实交代）

1. **A3 选中文本无法自动回填 `st.text_area`**：`st.components.v1.html` 是单向 iframe，没有回传通道。做到"自动填框"需要写真正的 Streamlit 自定义组件（新增前端目录 + 组件注册），超出本轮独占文件范围。当前是"捕获即清空 + 复制到剪贴板 + 用户 Ctrl+V"。
2. **`para_idx` 只做了提示，没做视觉高亮**：考生2 的 `para_idx` 是基于 chunk 累加的估算值（他自己也注明"不能保证与 PDF 视觉段落一一对应"）。要精确高亮需要 `services/parsing.py` 产出原生段落坐标（考生1 的文件，我不可改）。当前只 `st.success` 提示"第 N 段"并把值存进 `read_jump_para`。
3. **A4 乱码未复现**：见 A4 末尾交代——旧代码在当前 PyMuPDF 1.28.2 下能正常出中文。我修的是"会退化成非 CJK 字体的回退路径"+"超长截断无省略号"+"老版本兼容"，而不是"修好了一个能复现的乱码"。
4. **A1 在现有语料上未复现肉眼串栏**：见 A1 末尾交代——干净 arXiv PDF 的 span bbox 本身就在栏内。修复价值在于结构性保证 + 阅读顺序 + 超宽行兜底（用夹具复现验证）。
5. **未跑真实 LLM**：环境有 `openai` 包但没有可用 API Key，所有 LLM 路径都用"无 Key 分支 + 桩化 client"验证，没有真实网络调用。真实模型的限流/超时表现未实测。
6. **未启动 `streamlit run`**：只用 `streamlit.testing.v1.AppTest` 做无头渲染，未做真人浏览器交互验证（A2/A3 的 JS 用 Node + DOM 桩验证，不是真浏览器）。
7. **`_run_with_timeout` 是软超时**：不能强杀线程（见 B2 交代）。真实 30s 卡死的页（如损坏的巨型矢量图）后台线程仍会占一会儿资源。
8. **`detect_columns` 只判 1/2 栏**：三栏及以上按"最宽中缝"退化成 2 栏处理（Left=最宽中缝左侧、Right=右侧），三栏论文会把第 2、3 栏合并成一栏。语料里没有三栏 PDF，未做。

## 风险点

1. **C 组把调用次数从 3 提到 4**：硬失败时最长多等 9s。若别的 agent 有"3 次调用"的断言会失败（我 grep 过 `tests/test_core.py`，没有 llm 重试相关断言）。若评审认为应严格 3 次尝试，把 `core/llm.py:29` 的 `MAX_RETRIES` 改成 2 即可（退避自动变 1.5/3.0）。
2. **`SDK_MAX_RETRIES` 从 2 改成 0**：SDK 层的瞬断重试没了，全部收敛到应用层。若某个端点依赖 SDK 的 `Retry-After` 处理，可能略有差异。
3. **`_consume_qa_jump` 会删 `np/sp/pp/hp` 的 widget 状态**：这是为了让新跳转生效（否则被旧状态吞掉）。副作用是用户手动设的页码在跳转时会被覆盖——这是预期行为。
4. **`selectable_text_html` 的自动剪贴板写入**：在无用户手势的 iframe 里可能被浏览器策略拒绝（已 try/catch，失败时面板上仍有「复制」按钮可手动点）。
5. **`st.components.v1.html` 已被 Streamlit 标记弃用**（运行时会打印 "Please replace with st.iframe"），来源在 `ui/common.py:embed_html`（考生5 的文件，我不可改）。我的 `_HEIGHT_JS` 高度自适应依赖 components 的 `streamlit:setFrameHeight` 消息，若将来切到 `st.iframe` 需要重新验证。
6. **541 页扫描峰值 283~306 MB** 是在 AppTest 进程里（本身基线 ~235 MB），真实浏览器会话的基线不同，**数字不能直接横向比较**，看增量（+0~70 MB）更有意义。
7. **覆盖率剖面判双栏是启发式**：`_LOW_COVER_RATIO=0.25` / `_MIN_GUTTER=12pt` 是在 BERT（真实双栏）和 att v7（真实单栏）上调的，只在这两个语料上验证过；遇到图多、表多、跨栏表格的版式仍可能误判（误判为双栏时，最多是渲染分栏，不会报错）。

## 自验命令（可复现）

```bash
cd /c/Users/Administrator/ai_research_agent

# 1) 语法检查（两个解释器都过）
C:/Users/Administrator/anaconda3/python.exe -m py_compile \
    services/translate.py ui/page_reading.py core/llm.py scripts/_memory_probe.py
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile \
    services/translate.py ui/page_reading.py core/llm.py scripts/_memory_probe.py scripts/_smoke_read.py

# 2) 阅读页冒烟 + A/B/C 组自验（59 项断言）
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe scripts/_memory_probe.py   # 探针自校准
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe scripts/_smoke_read.py

# 3) 只改了这 3 个文件（其余是别的 agent 的改动，非我触碰）
git diff --stat -- services/translate.py ui/page_reading.py core/llm.py
```

## 需要其他 agent 配合的跨文件约定

- **给考生1（`services/parsing.py`）**：如果 chunk / 段落能带上原生 `para` 序号或段落 bbox，阅读页可以直接用 `para_idx` 做像素级高亮（当前只能提示）。
- **给考生5（`ui/common.py`）**：`embed_html()` 内部用的是 `st.components.v1.html`（Streamlit 已标记弃用）。若改成 `st.iframe`，请告知——我的 `_HEIGHT_JS` 高度自适应（`services/translate.py:215`）需要重新验证是否仍生效。
- **给考生2（`ui/page_qa.py`）**：`qa_jump_page` 约定已按你的结构消费（含 `para_idx`），消费后会置 `None` 并删掉 `jump_page`/`paper` query_params，**不会**重复跳转。
- **给所有调用方（`core/llm.py`）**：`_log_llm_failure` 新增两个**带默认值**的可选参数（`model` / `caller`），旧的两参调用行为完全不变；`RETRY_BACKOFF` / `MAX_RETRIES` / `SDK_MAX_RETRIES` 三个常量是全局唯一来源，如需调整改常量即可，不要在各自调用点写死 sleep。

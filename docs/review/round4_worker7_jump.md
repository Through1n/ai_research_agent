# Round4 · 考生7 交付报告：模块三 正文引用标记 ↔ References 双向无损跳转

- 考生：考生7
- 独占改动文件（仅这两个，未触碰其它任何文件）：
  - `services/translate.py`
  - `ui/page_reading.py`
- 真实运行环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（Python 3.11.16）
- 自验脚本：`C:/tmp/w7/verify_w7.py`（未写入项目 `storage/`）
- 基线：`scripts/_smoke_read.py` 改动前 PASS 59 / FAIL 0；改动后 **PASS 59 / FAIL 0**（回归零新增失败）

---

## 一、T1（核心）：正文引用标记 ↔ References 双向无损跳转

### 1.1 改了什么

| 位置 | 函数/内容 | 作用 |
|---|---|---|
| `services/translate.py:816-885` | `build_reference_index(doc, max_entries=600)` | **真实抽取** References 并建立编号索引 |
| `services/translate.py:887-905` | `find_reference_start_page(doc, back_scan=40)` | 从后往前定位 References 标题所在页（用于自动切出正文窗口） |
| `services/translate.py:779-787` | `_BRACKET_CITE_RE` / `_PAREN_CITE_RE` | 识别 `[11]` `[1,2]` `[11, 12]` `[3-7]` `(Smith et al., 2020)` |
| `services/translate.py:925-955` | `_split_cite_numbers()` | 拆分编号；区间只取两端（**不改原文字符**） |
| `services/translate.py:957-971` | `_author_year_ref()` | 作者-年份标记 → 编号（姓氏+年份双条件命中，命中不了就不建链） |
| `services/translate.py:973-1031` | `_render_cite()` / `_link_citations()` | 生成 `<a class="cit" id="cit-N-k" href="#ref-N">`；匹配不上时输出纯文本 |
| `services/translate.py:1033-1123` | `_CITE_JUMP_JS` | 锚点滚动 + 临时 class 高亮 + 悬停 tooltip + 第 k/m 处循环 |
| `services/translate.py:1157-1233` | `citation_links_html(body_pages, refs, tip_len=60)` | 左正文 / 右 References 双栏 HTML |
| `ui/page_reading.py:1037-1171` | 标签页「🔗 引用跳转」重写 | 接真实索引；窗口页码控件；统计输出；无索引时降级 |
| `ui/page_reading.py:704-727` | `_ensure_ref_index()` | session_state 缓存（key 先初始化再读） |

**优先复用、未重写抽取逻辑**（`services/translate.py:821-843`）：

```python
try:
    from services import writing
    items = writing.extract_references_from_pdf(doc) or []   # 考生4，字段级解析，优先
    ...
except Exception:
    ...
if not refs:                                                  # 降级：考生1 的文本切分
    from services import parsing
    raw = parsing.extract_references_raw(doc) or ""
    m = re.search(r"(?im)^[ \t]*(references|bibliography|works\s+cited|参考文献)[ \t]*$", raw)
    if m: raw = raw[m.end():]
    for i, entry in enumerate(parsing.split_reference_entries(raw) or [], 1):
        refs.append((i, str(entry), ""))
```

### 1.2 正向（正文 → 参考文献）

生成的 HTML 实测片段（`pdf/1706.03762v1.pdf`，第 3 页正文）：

```html
...more difficult to learn dependencies between distant positions [<a class="cit"
id="cit-11-1" href="#ref-11" data-ref="11" data-idx="1"
title="Gradient flow in recurrent nets: the difficulty of learning "
data-tip="Gradient flow in recurrent nets: the difficulty of learning ">11</a>].
In the Transformer this ...
```

- `title` + `data-tip` 双通道 tooltip（原生 tooltip 兜底 + JS 自定义浮层），内容是**该条参考文献标题前 60 字**
- JS：`goto('ref-' + n)` → `scrollIntoView({behavior:'smooth',block:'center'})` → `classList.add('hit')` → 2.6s 后移除

### 1.3 反向（参考文献 → 正文，含第 k/m 处循环）

```html
<div class="ref" id="ref-11"><span class="rn">[11]</span><span class="rt">Sepp Hochreiter,
Yoshua Bengio, Paolo Frasconi, and Jürgen Schmidhuber. Gradient flow in recurrent nets:
the difficulty of learning long-term dependencies, 2001</span><div class="rb">
<a class="back" href="#cit-11-1" data-ref="11" data-total="2" data-idx="0">↩ 回到正文（2 处）</a>
</div></div>
```

JS 循环游标（`services/translate.py:1073-1091`）：

```js
var idx = (parseInt(b2.getAttribute('data-idx'), 10) || 0) + 1;
if(idx > total){ idx = 1; }                 // 到最后一处后回到第 1 处
b2.setAttribute('data-idx', idx);
if(goto('cit-' + n2 + '-' + idx)){
  b2.textContent = '↩ 第 ' + idx + '/' + total + ' 处引用（点击继续）';
}
```

### 1.4 "无损"如何保证（逐条对应）

| 要求 | 实现 |
|---|---|
| 不丢失阅读位置 | 纯 iframe 内 `scrollIntoView`，不触发 Streamlit rerun |
| 不重置翻译状态 | 不写任何 widget key、不碰 `read_tr_cache` |
| 不改变当前页码 | 不动 `np/sp/pp/hp`；`href="#ref-N"` 是纯锚点，不会提交表单 |
| 临时高亮 | CSS `@keyframes ai4sflash` 2.6s 后自动 `classList.remove('hit')` |
| 映射真实不靠猜 | `writing.extract_references_from_pdf` 真实抽取，编号取自 PDF 中 `[1]..[36]` |

### 1.5 优雅降级

- References 抽不到：`ui/page_reading.py:1044-1051` 输出中文 warning + 中文"怎么办"，**引用标记仍显示但不可点击**（`_link_citations` 走 `_esc()` 纯文本分支）
- 越界编号 `[999]`：不建链（`A20` 实测通过）
- 区间 `[3-7]` 的中间编号（4/5/6）没有独立锚点：该条显示「↩ 正文未检索到该标记（可能以 [a-b] 区间形式引用）」，不产生死链

### 1.6 实测结果（1706.03762v1.pdf）

```
References 标题页：p10（共 15 页）
References 抽取：36 条 | source=services.writing.extract_references_from_pdf | status=ok
编号：1..36 连续，与 PDF 中 [1]..[36] 完全一致

stats = {'pages': 9, 'markers': 58, 'linked': 58, 'unlinked': 0,
         'occurrences': 71, 'refs': 36, 'refs_in_body': 36}

锚点配对校验（字符串级）：
  href="#ref-N" 目标集合 36 个 / id="ref-N" 集合 36 个 / 死链 []
  'id="ref-11"' x1 与 'href="#ref-11"' x2 同时存在   ← 赛题点名样例
  反向按钮 36 个（= References 条数），data-ref 与 href 编号一致，第 1 处锚点全部存在
  正文锚点 id="cit-N-k" 共 71 个，序号 k 均 <= 该条总处数 m（越界 0 个）
  被多次引用的文献 16 条，样例 [(34,8), (9,6), (2,5), (33,5), (16,4)]  → 第 k/m 处导航有意义
```

旁证（同一份代码在其它语料上的抽取条数）：
`1810.04805v2.pdf`（BERT）= **56 条**（与考生4 修复后的口径一致）；
`UnderstandingDeepLearning_02_09_26_C.pdf`（541 页）= 59 条（标题页 p526）。

页面上的实际输出（AppTest 抓取）：

```
✅ References 抽取成功：36 条（来源：services.writing.extract_references_from_pdf，标题页 p10）
🔗 双向跳转已生成：正文 9 页 / 引用标记 58 个（可跳转 58、未匹配 0）/ 引用出现 71 处 /
   References 36 条（其中 36 条在正文中被引用）
```

---

## 二、T2：标题树导航点击定位

### 2.1 现状核查

`ui/page_reading.py` 原第 865-883 行：无书签时只输出一行 `st.caption("未检测到内置书签目录。")`，**推断回退完全没接**。已接上。

### 2.2 改了什么

| 位置 | 内容 |
|---|---|
| `ui/page_reading.py:655-675` | `_infer_headings()` → 调 `parsing.infer_headings_from_text`（用 `parsing.extract_page_blocks_column_aware` + `parsing.heading_page_data` 喂字号/粗体元数据） |
| `ui/page_reading.py:579-620` | `_tidy_headings()` —— 编号行与标题行合并、剔除首页作者行、剔除正文碎片、去重 |
| `ui/page_reading.py:622-653` | `_chapter_headings()` —— 字号无差异时按「Chapter N / 第 N 章」兜底 |
| `ui/page_reading.py:677-702` | `_ensure_headings()` —— session_state 缓存，避免每次 rerun 重扫 |
| `ui/page_reading.py:537-569` | `_consume_toc_jump()` —— 消费 `read_toc_jump`，消费后清空（不重复跳转） |
| `ui/page_reading.py:1173-1202` | 标题树标签页重写（可点击 + 跳转提示） |
| `services/translate.py:175` / `:390` | `_block_html(..., highlight=None)` / `native_page_html(doc, page_num, highlight=None)` —— 命中标题的 span 加 `class="sp mk"` 黄色高亮 |
| `ui/page_reading.py:904-917` | 原生渲染传入 `highlight=标题`，渲染后提示「🔎 已高亮标题：…」 |

关键片段（`ui/page_reading.py:655-673`）：

```python
blocks = parsing.extract_page_blocks_column_aware(doc[i])
pages.append(parsing.heading_page_data(blocks))
raw = parsing.infer_headings_from_text(pages) or []
tidy = _tidy_headings(raw, tps)
if not tidy:
    tidy = _chapter_headings(doc, tps, cap)   # 字号无差异时的章节号兜底
```

高亮（`services/translate.py:196-200`）：

```python
s_text = str(s.get("text", ""))
mk = " mk" if (hl and hl in s_text.lower()) else ""
inner.append('<span class="sp%s" style="font-size:%.1fpx;color:%s;%s">%s</span>'
             % (mk, sfs, rgb, style, _esc(s_text)))
```

### 2.3 实测结果

| PDF | 内置书签 | 推断条数 | 说明 |
|---|---|---|---|
| `1810.04805v2.pdf`（BERT） | **0** | **97** | 推断回退被真正触发 |
| `LongBook_540p_fixture.pdf` | **0** | **4** | 走 Chapter 兜底（该夹具字号无差异） |
| `1706.03762v1.pdf` | 22 | 48（未用，内置优先） | 内置书签存在时不走推断 |
| `UnderstandingDeepLearning_02_09_26_C.pdf` | 181 | 418（未用） | 同上 |

BERT 推断出的前若干条（真实标题，编号已与标题行合并）：

```
BERT: Pre-training of Deep Bidirectional Transformers for | Abstract | 1 Introduction |
2 Related Work | 2.1 Unsupervised Feature-based Approaches | 2.2 Unsupervised Fine-tuning
Approaches | 2.3 Transfer Learning from Supervised Data | 3 BERT | Model Architecture |
Input/Output Representations | 3.1 Pre-training BERT | Task #1: Masked LM | ...
```

AppTest 端到端（点「3.2.1 Scaled Dot-Product Attention」）：

```
[PASS] D6 标题树渲染出可点击项  | 22 个按钮，样例 ['1 Introduction · p1', '2 Background · p2', ...]
[PASS] D8 二次运行消费 read_toc_jump 并提示「标题树定位」
       | 标题树定位：第 3 页「3.2.1 Scaled Dot-Product Attention」（已切到「📄 原生渲染」标签并高亮该标题）
[PASS] D9 标题树跳转被消费后不重复触发
[PASS] D11 原生渲染区提示已高亮标题  | ['🔎 已高亮标题：3.2.1 Scaled Dot-Product Attention']
[PASS] C6 原生渲染可高亮标题（命中 span 带 class mk）  | p1 「BERT: Pre-training of ...」
[PASS] C7 不传 highlight 时行为不变（无 mk，兼容旧调用）  ← 保证 _smoke_read.py 旧断言不受影响
```

---

## 三、T3：公式保留（整篇翻译 / 划词翻译）

### 3.1 现状核查

原有翻译链路 `ui/page_reading.py:_safe_translate()`（`:365`）直接把原文原文丢给 `writing.translate_to_chinese`，**没有任何公式保护**。`writing.py` 我不能改，所以在我的 `_safe_translate` 里做「翻译前占位 → 翻译后还原」，`_translate_pairs()`（逐段翻译）复用同一入口，因此**整篇翻译、划词翻译、原位 PDF 三条链路全部覆盖**。

### 3.2 改了什么

| 位置 | 内容 |
|---|---|
| `services/translate.py:1236-1247` | `_MATH_RE`：`$$..$$` / `$..$` / `\(..\)` / `\[..\]` / LaTeX 命令（`\beta_1`、`\frac{a}{b}`、含 `=` 扩展）/ `key=value` 数值（`BLEU=28.4`）/ 百分数 / 小数 |
| `services/translate.py:1248` | `_RESTORE_RE = re.compile(r"@\s*@\s*FM\s*(\d+)\s*@\s*@")` —— 容忍模型在占位符里插空格、甚至把 `@@` 拆开 |
| `services/translate.py:1251-1281` | `protect_math()` / `restore_math()` |
| `services/translate.py:1283-1300` | `math_safe_translate()` 便捷封装 |
| `ui/page_reading.py:373-379` | `_safe_translate()` 接入保护/还原 |

```python
# ui/page_reading.py:373-379
masked, store = protect_math(txt)                                   # T3：先占位
ok, val, err = _run_with_timeout(writing.translate_to_chinese, timeout, ctx, masked)
if ok:
    out = str(val or "").strip()
    if not out:
        return "", "模型返回空内容"
    return restore_math(out, store), ""                             # T3：原样还原
```

### 3.3 实测（构造含 `$E=mc^2$`、`\beta_1=0.9`、`BLEU=28.4` 的文本）

输入：

```
The mass-energy equivalence is $E=mc^2$, and we set \beta_1=0.9 for the optimizer.
Our model achieves BLEU=28.4 on WMT 2014, 2.0 BLEU better than the best,
with only 3.5% of the training cost. See also $$\sum_{i=1}^{n} x_i$$ and \(a+b\).
```

占位后（送进模型的文本）：

```
The mass-energy equivalence is @@FM0@@, and we set @@FM1@@ for the optimizer.
Our model achieves @@FM2@@ on WMT 2014, @@FM3@@ BLEU better than the best,
with only @@FM4@@ of the training cost. See also @@FM5@@ and @@FM6@@.
```

保护清单：

```
['$E=mc^2$', '\\beta_1=0.9', 'BLEU=28.4', '2.0', '3.5%', '$$\\sum_{i=1}^{n} x_i$$', '\\(a+b\\)']
```

**对抗性模拟模型**（刻意破坏公式：`=`→`等于`、`$`→`美元`、`\`→`＼`、并把 `@@` 拆成 `@ @`）输出还原后：

```
该模型在 WMT 2014 上达到了 The mass-energy equivalence is  $E=mc^2$ , and we set
 \beta_1=0.9  for the optimizer. Our model achieves  BLEU=28.4  on WMT 2014,  2.0
 BLEU better than the best, with only  3.5%  of the training cost. See also
 $$\sum_{i=1}^{n} x_i$$  and  \(a+b\) . ，显著优于此前最佳结果。
```

逐项断言（7/7 PASS）：

| 片段 | 结果 |
|---|---|
| `$E=mc^2$` | 原样保留 |
| `\beta_1=0.9` | 原样保留 |
| `BLEU=28.4` | 原样保留 |
| `2.0` / `3.5%` | 原样保留 |
| `$$\sum_{i=1}^{n} x_i$$` | 原样保留 |
| `\(a+b\)` | 原样保留 |

另通过：无占位符泄漏（`@@`/`FM0` 均不在译文中）、无公式文本不被误伤、空输入/回调抛异常均不冒泡。

> 说明：环境内无可用 API Key（`.env` 未配置真实 key），无法跑真实 LLM 端到端；上述用的是**刻意破坏公式的模拟模型**，比真实模型更严格。真实模型下占位符更不容易被破坏。

---

## 四、硬性约束自检

| # | 约束 | 结果 |
|---|---|---|
| 6 | `python -m py_compile services/translate.py ui/page_reading.py` | **零报错**（COMPILE OK） |
| 7 | `python scripts/_smoke_read.py` | **PASS 59 / FAIL 0**（与基线一致，零回归） |
| 1 | 未启动 streamlit、未 pip install | 未执行 |
| 2 | 未改非独占文件 | `git status` 仅这两个文件（见下） |
| 3 | 新代码块 try/except 兜底 | 所有新增函数顶层/关键分支均有 `except Exception`；页面每个标签页外层已有 `try/except` |
| 4 | `st.session_state` key 先初始化再读 | `read_ref_index`、`read_toc_cache` 均走 `st.session_state.get(...)` + 类型判断 + 缺省初始化；`read_hl`、`read_toc_jump` 读取处全部包 try/except |
| 5 | 真实跑测 1706.03762v1.pdf | 见 §1.6 |

自验脚本汇总：`C:/tmp/w7/verify_w7.py` → **PASS 53 / FAIL 0**（Part A 引用跳转 22 项 / Part B 公式 12 项 / Part C 标题树 9 项 / Part D AppTest 端到端 10 项）。

---

## 五、未完成项 / 风险点（诚实交代，供交叉审计）

1. **未做真实 LLM 端到端公式验证**：环境无可用 API Key，T3 只验证了「保护→模拟破坏性模型→还原」这条链路。真实模型若把占位符整段吞掉（不输出 `@@FM0@@`），该公式会丢失——这是占位符方案的固有风险，目前无兜底（未做"缺失占位符时按序回填"）。
2. **`[3-7]` 区间只链接两端**（3 和 7），中间的 4/5/6 无独立锚点，因此这几条在 References 面板显示「正文未检索到该标记」。这是为了**保持原文字符一字不改**（`[3-7]` 不展开成 `[3][4][5][6][7]`）而做的取舍，tooltip 里仍能看到全部条目的标题。
3. **作者-年份标记映射偏保守**：`(Smith et al., 2020)` 要求「姓氏 + 年份」在条目原文里同时命中才建链；姓氏命中不了（机构署名、缩写、中文译名）时**宁可不建链**，不做"同年唯一"兜底，避免错链。代价是部分括号式引用不可点击。
4. **正文窗口有上限**：`CITE_BODY_CAP = 40` 页 / `TOC_SCAN_CAP = 120` 页 / `TOC_RENDER_CAP = 220` 条。541 页的书默认只渲染正文前 40 页的引用，超出部分需手动调整「正文结束页」。这是长文档防卡取舍，非功能缺失。
5. **标题树推断有噪声**：BERT 推断 97 条里混有少量表格/正文残片（如 `BERTBASE (L=12, H=768, A=12, Total Param-`）。已用三条规则（年份开头、小写开头、首页非章节名）过滤，但无法完全消除——`parsing.infer_headings_from_text` 我不能改，只能在下游 `_tidy_headings` 里洗。
6. **iframe 内的双向跳转未做真实浏览器点击验证**：只做了 HTML 字符串级断言（锚点 id/href 成对、JS 关键字存在）与 AppTest 无异常验证；未启动 streamlit 服务做人工点击（受"不要 streamlit run"约束）。滚动定位在 `st.components.v1.html` 的 iframe 里依赖 `scrollIntoView`，若评委环境 iframe 高度自适应异常（`_HEIGHT_JS` 上报失败），可能需要手动调 `height`。
7. **References 抽取本身依赖考生4 的 `_locate_reference_section`**：扫描件 / 非标准标题的 PDF 会走「未定位到 References 标题」分支返回空列表，此时页面走降级（warning + 不可点击），不是本轮的 bug。

---

## 六、改动文件清单

```
services/translate.py   （+约 560 行 / 改 3 处：_block_html、native_page_html、CSS）
ui/page_reading.py      （+约 300 行 / 改 4 处：_safe_translate、render 开头、tab4、tab6）
```

未改动：`ui/common.py`、`ui/page_qa.py`、`services/parsing.py`、`services/library.py`、`core/*`、`services/rag.py`、`services/writing.py`（仅 import 复用）。

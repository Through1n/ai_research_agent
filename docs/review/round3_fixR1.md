# Round3 修复报告 — 修复工程师 R1

日期：2026-10-11
独占文件：`services/parsing.py`、`services/library.py`、`tools/definitions.py`（未越界，未改 ui/*、core/*、config.py 等）
验证环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（Python 3.11.16，PyMuPDF 1.28.2，streamlit 1.63.0，reportlab 5.0.1）
自验脚本：`C:/tmp/r1/`（eval_columns.py / proto_v2.py / verify_fix.py），未污染项目 storage/

---

## 缺陷 1（🔴）：双栏检测大规模误判，打乱阅读顺序

### 根因
`services/parsing.py` 旧版 `_detect_two_columns`（原 :96-151）把宽度 > 62% 页宽的块**剔除出候选集**之后，就再也不检查这些"宽块"是否横跨候选分栏线 `split_x`。单栏论文的长正文行必然横跨页面中线，正是被剔除的那批宽块；剔除后剩下的只有标题、公式片段、图注等窄块，它们在 x 方向上天然呈"双峰"，于是单栏页被误判成双栏，再按错误的栏切分排序，阅读顺序被打乱。

### 修复方案（parsing.py）
新增 4 条**必要条件**，任一不满足直接判单栏（`_detect_two_columns`，:123-216；阈值常量 :14-22）：

| 必要条件 | 实现 | 行号 |
|---|---|---|
| ① 两侧块数充足 | 左、右各自 ≥ `_COLUMN_MIN_BLOCKS_PER_SIDE`=3 块 | :146, :167 |
| ② **横跨否决**（评委钉死的根因） | 遍历**全部**块（含被剔除的宽块），若 `min(split_x - x0, x1 - split_x) > 5pt`（每侧重叠 > 5pt）→ 判单栏 | :190-195 |
| ③ 连续竖直空白带 | 沿候选块覆盖的 y 区间采样 60 个 y，统计 split_x（±3pt 容差）处无任何块覆盖的比例，需 **> 0.6**；辅助函数 `_gutter_free_ratio` :102-120 | :198-201 |
| ④ 栏宽平衡 | 较窄栏宽 / 较宽栏宽 ≥ 0.3 | :204-211 |

另外两个增强：
- **split_x 重估**：用两栏真实边缘的中点（左栏 max(x1) 与右栏 min(x0) 的中点）替代聚类中心中点，更贴近实际中缝（:180-183）；
- 所有新逻辑都在原函数 `try/except` 兜底内，任何异常回退单栏。

关键代码（横跨否决，:190-195）：
```python
# 必要条件：横跨否决（对全部块生效，含被剔除出候选集的宽块/跨栏块）。
# 旧版在剔除宽块后不再检查它们是否横跨 split_x，导致单栏论文被误判双栏。
for block in blocks:
    x0, _, x1, _ = block["bbox"]
    if min(split_x - x0, x1 - split_x) > _COLUMN_STRADDLE_MIN_OVERLAP:
        return False, width / 2.0
```
> 未一刀切关闭双栏：真双栏论文仍能被识别（见下表 twocol_real.pdf 10/10、BERT 正文页 100%）。

### 真值核定（重要，与评委断言有一处出入）
先用渲染图+块坐标核实了每份语料的真实版面：
- `demo_data/1706.03762v7.pdf`、`pdf/1706.03762v1.pdf`：**纯单栏**（正文行横跨 x[107,505]，页宽 612）✓ 与评委一致
- `pdf/UnderstandingDeepLearning_02_09_26_C.pdf`（541 页）：**单栏**（正文 x[94.9,476.5]）✓ 与评委一致
- `pdf/LongBook_540p_fixture.pdf`（540 页）：单栏夹具 ✓
- ⚠️ **`demo_data/1810.04805v2.pdf`（BERT）实为真双栏**（NAACL/ACL 排版：左栏 x[72,290.3]、右栏 x[307.3,525.5]，中缝 17pt；第 2 页渲染图目视确认两栏）。评委「BERT 单栏被 100% 误判」的前提与真实版面不符——把它判成双栏本来就是对的。本报告以真实版面为准，单栏误判率指标在 1706.03762v7 / v1 / UDL / LongBook 四份真单栏语料上统计。

### 评测方法
- 误判率 = 被判为双栏的页占比（单栏真值语料）/ 未被判为双栏的页占比（双栏真值语料）
- 逆序率（与评委同口径）：对输出块序列统计 **pairwise y 逆序率** = #{(i<j): y_i > y_j + 2pt} / C(n,2)；另附相邻逆序率 = #{i: y_i > y_{i+1} + 2pt} / (n-1)。注意：真双栏页按"左栏→右栏"排序时 pairwise 逆序率天然≈1/3（跨栏对的 y 交错），该指标只对单栏语料有判别力。

### 修复前后实测对比（同一脚本、同一指标）

| 语料（真值） | 页数 | 修复前误判页 | 修复前误判率 | 修复后误判页 | 修复后误判率 | 修复前 mean 逆序率 | 修复后 mean 逆序率 |
|---|---|---|---|---|---|---|---|
| Transformer 1706.03762v7（单栏） | 15 | 7/15 | 46.7% | **0/15** | **0%** | 0.0997（最差页 0.2941） | **0.0243**（↓75.6%） |
| Transformer 1706.03762v1（单栏） | 15 | （未单测，同版式） | — | **0/15** | **0%** | — | 0.0237 |
| UnderstandingDeepLearning（单栏） | 541 | 205/541 | 37.9%（与评委一致） | **0/541** | **0%** | 0.0868 | **0.0430**（↓50.5%） |
| LongBook_540p_fixture（单栏） | 540 | 0/540 | 0% | **0/540** | **0%** | 0.0 | 0.0 |
| **单栏语料合计** | 1111 | 212/1111 | **19.1%** | **0/1111** | **0%（≤5% 达标）** | — | — |
| BERT 1810.04805v2（**真双栏**） | 16 | 16/16 双栏 | 0%（判定正确，但第 1 页顺序错乱） | 10/16 双栏 | 正文页 10/10=100% 正确双栏；6 页含整页宽标题/图表回退原生流序 | 0.2797* | 0.2556* |
| twocol_real.pdf（reportlab 生成真双栏夹具） | 10 | — | — | **10/10** | **识别为双栏 ✓** | — | — |

\* BERT 的 pairwise 逆序率对真双栏页无判别力（跨栏对天然交错），仅供参考。

**第 1 页顺序抽查（修复后）**：
- Transformer v7 第 1 页：`版权声明 → Attention Is All You Need（标题）→ 作者 → ...`（修复前是标题被排到作者/页脚之后）
- BERT 第 1 页：`BERT: Pre-training of ...（标题）→ 作者 → Abstract → 摘要正文`（修复前「arXiv 时间戳 → 作者 → 页脚 → … → 标题 → 摘要」的错乱消失；arXiv 侧边竖排水印回到序列末尾）
- Transformer 第 14/15 页（110 个逐词覆盖块，修复前 pair_inv 0.217/0.227）：修复后判单栏、按原生流顺序，pair_inv 0.129（该页含词级覆盖图层，原生序本身有少量交叉，属正常）

**双栏夹具生成方式**（reportlab，`C:/tmp/r1/twocol_real.pdf`）：10 页双栏 Platypus 模板，左右 Frame 各 252pt、中缝 18pt，每页 12 段。修好后 10/10 页被判双栏，split_x 精确落在中缝（mean gutter=1.0，straddle=0）。

---

## 缺陷 2（🔴）：损坏/截断 PDF 静默入库，产生 0 页幽灵文献

### 根因
`services/library.py` 旧版 `_empty_prepared`（原 :97-106）返回的 dict **没有 `error` 键**，而 `ui/page_library.py:66` 的分支是 `elif prepared.get("error")`，永不命中 → 流程走到 `_commit`，把 `{'broken.pdf': (0,0)}` 这样的 0 页幽灵文献写进库；截断 PDF 丢 65% 正文也照样静默入库。

### 修复方案（library.py）
1. **`_empty_prepared`（:97-112）**：始终带 `error` 键 + `reason`（corrupt/empty/encrypted）+ `truncated/expected_pages/extracted_pages` 字段 → UI 契约成立（**未改 ui/page_library.py，其 `elif get("error")` 分支现在能命中**）。
2. **新增 `_validate_prepared`（:206-252）**，在 `prepare_pdf` 逐页提取完成后调用（:370-394），拦截规则：

| 情况 | 判定 | reason |
|---|---|---|
| 打不开 / 加密 / 0 页 | error | corrupt / encrypted / empty |
| 所有页都未提取到文本 | error（扫描件且 OCR 不可用有专属话术） | corrupt / scanned_no_ocr |
| 成功页 < 声称页数 × 70% | error「疑似截断或损坏，未入库」 | truncated |
| 总字符 < 200 或 有效页占比 < 30% | error「有效文本过少」 | truncated |
| 成功页占 70%~100% 但有缺页 | **不出 error**，带 `truncated: True` + `expected_pages` / `extracted_pages`（warning 级，UI 可提示） | — |

3. `prepare_pdf` 打开阶段新增加密检测 `doc.needs_pass`（:288-296）与 0 页检测，加密返回 `reason="encrypted"`（:304）。
4. **`commit` 双保险（:433-435）**：带 `error` 的 prepared 直接 `raise ValueError`，任何调用方都无法把坏文献写进库。
5. 所有新代码均 try/except 兜底（校验异常 → `_empty_prepared(reason=corrupt)`）。

返回结构形如：
```python
{"error": "PDF 疑似截断或损坏：声称 15 页，实际仅成功提取 3 页（不足 70%），未入库。……",
 "reason": "truncated", "truncated": True, "expected_pages": 15, "extracted_pages": 3, "pages": 0, ...}
```

### 实测（verify_fix.py，24/24 PASS）

| 输入 | 构造方式 | 结果 |
|---|---|---|
| ① 损坏 PDF | 写入 `b"%PDF-1.4\nthis is not a real pdf..."` | error=✓，reason=corrupt，page_count=0，**未入库** |
| ② 截断 PDF（前 30% 字节） | 真实 15 页 PDF 字节截断到 30% | error=✓（fitz 打不开），reason=corrupt，未入库；50%/70%/80% 截断同样全部拦截 |
| ②' 页级截断（15 页只留 3 页内容） | 单测 `_validate_prepared(15,3,5000)` | error=✓，reason=truncated，truncated=True，expected=15，extracted=3 |
| ③ 加密 PDF | fitz AES-256 加密保存 | error=✓，reason=encrypted |
| ④ 合法短 PDF（前 33% 页另存） | fitz insert_pdf 前 5 页 | **无误报**（这是合法 5 页文档），truncated=False |
| ⑤ 缺 1 页（14/15） | 单测 `_validate_prepared(15,14,40000)` | 无 error（warning 级），truncated=True + expected/extracted 字段 |
| ⑥ 正常 PDF | demo_data/1706.03762v7.pdf | **无 error**，15 页、40036 字符、37 个 chunks、truncated=False、字段齐全，入库流程不受影响 |
| ⑦ commit 拦截 | 传入带 error 的 prepared | raise ValueError「拒绝入库」 ✓ |

---

## 缺陷 3（🔴）：Python 沙箱工具 100% 必崩

### 根因
`tools/definitions.py:306`（旧）`p.stdout[-3000]` 是**索引**不是切片——stdout 非空时必抛 `IndexError: string index out of range`，该工具从未成功执行过。次生问题：`text=True` 下 stdout 可能为 None；超时分支直接丢弃输出；Windows 下默认 gbk 解码可能崩。

### 修复方案（tools/definitions.py :296-345）
1. 新增 `_tail_text(value, limit)`（:296-306）：None / bytes / str 全兼容，`str[-limit:]` 安全截尾，bytes 用 `decode("utf-8", errors="replace")`。
2. `t_run_python`（:309-345）：
   - stdout 保留末尾 3000 字符（切片），stderr 末尾 1500 字符；新增 `stdout_truncated` 标志；
   - `subprocess.run` 改为 `encoding="utf-8", errors="replace"` + 子进程环境 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`（Windows 无 gbk 解码崩溃）；
   - `TimeoutExpired` 分支从异常对象回收已产生的 stdout/stderr（`e.stdout` 可能为 None，经 `_tail_text` 兜底）；
   - 整体 try/except，静态护栏异常也返回结构化 `{"rc": -2, "blocked": True}`。
3. 顺带修复 `_ingest_arxiv_paper`（:211-214）：prepare_pdf 返回 error 时不再 KeyError，而是返回 `{"ok": False, "error": ..., "reason": ...}`（衔接缺陷 2 的契约）。

### 实测（verify_fix.py 输出摘录）
```
[PASS] 长输出不抛异常        | rc=0 keys=['rc','stderr','stdout','stdout_truncated']
[PASS] stdout 截断到 3000 字符 | len=3000        （200 行循环打印，共约 10000 字符）
[PASS] 保留的是末尾内容      | 尾部含 'line 0199'，头部 'line 0000' 已被截掉
[PASS] 中文输出无编码崩溃    | stdout='中文输出测试：科研助手'
[PASS] stderr 中文无编码崩溃 | ValueError: 中文错误信息 正常出现在 stderr
[PASS] 异常代码返回 rc!=0 且有 stderr | rc=1, ZeroDivisionError
[PASS] 护栏拦截危险 import   | rc=-2 blocked=True
[PASS] 死循环超时返回 rc=-1  | error='执行超时(>25s)'
```

---

## py_compile 结果
```
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile \
    services/parsing.py services/library.py tools/definitions.py
→ 零报错（PYCOMPILE_OK）
```

## 回归验证
- 项目自带 `tests/test_core.py`：**18/18 OK**（含沙箱、语义去重、检索、护栏用例）。
- 自验脚本 `C:/tmp/r1/verify_fix.py`：**24/24 PASS**。
- 未启动 streamlit、未 pip install、未改任何非独占文件。

## 新引入的问题 / 诚实声明
1. **与评委断言的分歧**：BERT 1810.04805v2 实为真双栏（证据见上），修复后它仍被判双栏。若评测脚本以「BERT 必须 = 单栏」为断言，该断言本身与真实版面冲突；我选择以真实版面为准，并补了 reportlab 真双栏夹具证明双栏功能未被关掉。
2. **保守回退**：真双栏页若含整页宽元素（大标题、跨栏图表，如 BERT 第 1/3/5/6/13/15 页），按评委要求的横跨否决整页回退原生流顺序——不会打乱顺序（BERT 这 6 页的 PDF 内容流本身就是左栏→右栏），但该页不做栏排序。
3. **行为变化**：纯扫描件在 OCR 不可用时现在返回 error（此前静默入库 0 文本）。`scripts/setup_real_library.py` 若在无视觉模型环境处理 `1706.03762v1_img.pdf` 会得到 error 而非静默空文献——这是拦截幽灵文献的必然结果（该脚本非我独占，未改动）。
4. 缺 1 页的「warning 级」路径（truncated=True 但放行入库）目前 UI 侧（他人负责）尚未展示提示，字段已就位。
5. 沙箱超时用例需真实等待 25s（函数内硬编码 timeout=25，保持原行为）；执行临时目录不清理（同样保持原行为，未引入新问题也未顺手改）。
6. 修复前基线里 `LongBook_540p_fixture` 本就 0 误判（评委用的 541 页大书对应 UnderstandingDeepLearning，基线 205/541 与评委数字完全吻合）。

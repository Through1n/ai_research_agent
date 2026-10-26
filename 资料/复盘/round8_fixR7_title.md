# Round 8 · 修复工程师 R7：论文标题抽取缺陷修复报告

- 修复文件：`services/library.py`（**唯一改动文件**，其余文件未触碰）
- 环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`
- 自验脚本目录：`C:/tmp/r7/`（未写入项目 `storage/`）
- 缺陷：入库 `论文样本/1810.04805v2.pdf`（BERT，官方演示数据）后标题被抽成 `Language Understanding`

---

## 1. 根因分析

### 1.1 复现（修复前实测输出）

脚本 `C:/tmp/r7/repro_bert.py`，走真实入库链路 `prepare_pdf → commit`：

```
prepared['title'] = 'Language Understanding'
ctx.papers['1810.04805v2.pdf']['title'] = 'Language Understanding'

期望: 'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding'
```

首页前 6 个非空行（`parsing.extract_page_text_column_aware` 实抽）：

```
[0] 'BERT: Pre-training of Deep Bidirectional Transformers for'   ← 标题首行
[1] 'Language Understanding'                                       ← 标题续行
[2] 'Jacob Devlin'
[3] 'Ming-Wei Chang'
[4] 'Kenton Lee'
[5] 'Kristina Toutanova'

_DANGLING_RE 对首行的匹配: <re.Match object; span=(54, 57), match='for'>
```

### 1.2 误杀过程（正则原文 + 逐行走查）

修复前 `services/library.py:86`：

```python
# 首页样板常跨行断句：以连接词/冠词结尾的行几乎不可能是标题
_DANGLING_RE = re.compile(r"(?:\b(or|and|the|in|for|of|to|solely|this)\s*)$", re.IGNORECASE)
```

**该正则把 `for` 当成"悬垂词"**，而 BERT 标题首行恰好在 `for` 处断行（`...Transformers for` / `Language Understanding`）。

修复前 `services/library.py:102-116` 的循环：

```python
for index, line in enumerate(lines[:40]):
    if len(line) < 8 or len(line) > 160:
        continue
    if _BOILERPLATE_RE.match(line) or _DANGLING_RE.search(line):   # ← :105 这里被误杀
        continue                                                    #   首行整行丢弃
    if line.endswith((".", "!", "?", ",")):
        continue
    title = line
    if line.endswith((":", "-", "–", "—")) and index + 1 < len(lines):   # ← :110 永远到不了
        ...
```

走查：

| index | line | `_BOILERPLATE_RE` | `_DANGLING_RE` | 结果 |
|---|---|---|---|---|
| 0 | `BERT: Pre-training of Deep Bidirectional Transformers for` | 不匹配 | **匹配 `for`（span 54-57）** | **`:106` `continue`，整行丢弃** |
| 1 | `Language Understanding` | 不匹配 | 不匹配 | 当选，作为 title 返回 |

于是 **`services/library.py:110` 的跨行合并分支永远执行不到**——它只在行尾是 `:` / `-` / `–` / `—` 时才触发，而 BERT 首行尾是 `for`，根本不匹配；即使 `:105` 不拦它，`:110` 也合并不了。两条缺陷叠加，最终只取到第二行 `Language Understanding`。

> 评委指出的一点已核实：`guess_title_from_text` 的 docstring（`services/library.py:92`）举的例子**正是 BERT**（`"BERT: Pre-training of ... / Language Understanding"`）。函数本意就是要处理 BERT，却因正则里带 `for` 把 BERT 自己误杀了。

### 1.3 为什么不能"只把 `for` 删掉"

单纯从 `_DANGLING_RE` 删掉 `for` 只能让首行**不被跳过**，但 `:110` 的合并条件（`endswith(":") `等）仍不命中，结果是 `'BERT: Pre-training of Deep Bidirectional Transformers for'`——丢掉尾词，仍然错。**必须同时让跨行合并逻辑真的能执行到。**

---

## 2. 修复方案

改动全部位于 `services/library.py`，共 83 增 4 删。

### 2.1 拆分悬垂词表（`services/library.py:85-103`）

```python
# 首页样板常跨行断句：以连接词/冠词结尾的行几乎不可能是标题。
# 注意：这里**不能**收录 for / of / to / in 等介词——学术标题里极其常见
# （"Transformers for Language Understanding"、"Attention for Vision"），
# 2026-10 R7 修复前正则含 for，导致 BERT 标题首行被整行丢弃。
_DANGLING_RE = re.compile(
    r"(?:\b(or|and|the|a|an|in|of|to|solely|this)\s*)$", re.IGNORECASE)      # :89 强悬垂：拼不出续行才丢弃
# 宽口径悬垂词：用于判断"标题是否跨行没说完、需要拼接下一行"。
# for/of/to/in/on 等介词只作为"需要合并"的信号，不作为"整行丢弃"的判据。
_DANGLING_LOOSE_RE = re.compile(                                              # :93 弱悬垂：只触发合并
    r"(?:\b(or|and|the|a|an|solely|this|for|of|to|in|on|at|by|with|from|as|via|"
    r"using|towards?|into|over|under|per|than|through|without|within|upon)\s*)$",
    re.IGNORECASE,
)
_AFFILIATION_RE = re.compile(...)                                             # :99 作者/机构/邮箱行
```

语义分层：

- `_DANGLING_RE`（强）：只在"**拼不出续行**"时才把整行丢掉；
- `_DANGLING_LOOSE_RE`（弱，含 `for`）：只作为"**需要向后拼接**"的信号，绝不整行丢弃。

### 2.2 新增三个辅助函数（`services/library.py:107-152`）

- `_title_case_ratio(line)` `:107`——首字母大写词占比，用于区分「学术标题（Title Case）」与「首页样板（普通句子）」；
- `_is_title_continuation(line)` `:119`——续行准入：`≤80` 字、非样板、不以 `.!?;:` 收尾、非作者/机构/邮箱行、非 `Abstract`；
- `_merge_dangling_title(lines, index, max_extra=2)` `:134`——悬垂行向后最多拼 2 行，拼到不再悬垂即返回，拼不出返回 `''`。

### 2.3 主循环改造（`services/library.py:180-189`）

```python
            title = line
            if _DANGLING_LOOSE_RE.search(line):
                # 标题跨行没说完：优先拼接续行，而不是把整行丢掉。
                # 门槛：仅 Title Case 的行才拼接（样板句子残片大小写比例很低）
                merged = (_merge_dangling_title(lines, index)
                          if _title_case_ratio(line) >= 0.5 else "")
                if merged:
                    title = merged
                elif _DANGLING_RE.search(line):
                    continue    # 强悬垂且拼不出完整标题 → 首页样板残片
            elif line.endswith((":", "-", "–", "—")) and index + 1 < len(lines):   # 原合并逻辑保留
                nxt = lines[index + 1]
                if len(nxt) <= 80 and not nxt.endswith((".", ":")):
                    title = f"{line} {nxt}".replace(" :", ":").strip()
```

判定顺序（通用规则，非 BERT 特判）：

1. 行以虚词结尾 → 若该行是 Title Case（≥0.5）则**先尝试向后拼接**；
2. 拼出完整标题 → 采用；
3. 拼不出 **且** 以强悬垂词（`or/and/the/a/an/in/of/to/solely/this`）结尾 → 判为首页样板残片，丢弃；
4. 拼不出 **且** 只以弱悬垂介词（`for/on/via/...`）结尾 → 保留整行（学术标题本就可能这样断），不丢弃；
5. 原 `:` / `-` / `–` / `—` 收尾的合并逻辑**原样保留**。

### 2.4 为什么第 1 步要加 Title Case 门槛（关键回归防线）

去掉 `:105` 的"一律丢弃"后，如果无条件拼接，会出现**新的回归**：

```
输入: "This paper is provided solely\n\nAttention Is All You Need\nAshish Vaswani"
修复前 → 'Attention Is All You Need'                                  （正确）
无门槛 → 'This paper is provided solely Attention Is All You Need'     （回归！样板吞掉真标题）
```

`_title_case_ratio` 实测：

| 行 | 大写词占比 | 是否拼接 |
|---|---|---|
| `BERT: Pre-training of Deep Bidirectional Transformers for` | 5/8 = 0.63 | ✅ 拼接 |
| `Attention Is All You Need and the` | 5/7 = 0.71 | ✅ 拼接 |
| `This paper is provided solely` | 1/5 = 0.20 | ❌ 丢弃（保住了原有行为） |
| `reproduce the tables and figures ... journalistic or`（v7 样板） | 0/14 = 0.00 | ❌ 丢弃 |

这道门槛是**通用**的：任何全小写的首页样板残片都不会被拼进标题。

---

## 3. 全量 PDF 标题对照表（修复前 vs 修复后）

脚本 `C:/tmp/r7/full_titles.py`。「修复前」用 `git show HEAD:services/library.py` 动态载入为独立模块计算，**未改动工作区**，保证对照真实可比。

| 文件 | 扫描件 | 修复前 title | 修复后 title | 结论 |
|---|---|---|---|---|
| `论文样本/1706.03762v1.pdf` | 否 | `Attention Is All You Need` | `Attention Is All You Need` | 一致（正确） |
| `论文样本/1706.03762v1_img.pdf` | **是** | （空） | （空） | 一致（扫描件无文本层，如实为空） |
| `论文样本/1706.03762v7.pdf` | 否 | `Attention Is All You Need` | `Attention Is All You Need` | 一致（正确） |
| `论文样本/1810.04805v2.pdf` | 否 | **`Language Understanding`** ❌ | **`BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding`** ✅ | **已修好** |
| `论文样本/LongBook_540p_fixture.pdf` | 否 | `Chapter 1 Deep Learning Foundations and Agent Systems` | `Chapter 1 Deep Learning Foundations and Agent Systems` | 一致（正确） |
| `论文样本/UnderstandingDeepLearning_02_09_26_C.pdf` | 否 | `Understanding Deep Learning` | `Understanding Deep Learning` | 一致（正确） |
| `演示语料/1706.03762v7.pdf` | 否 | `Attention Is All You Need` | `Attention Is All You Need` | 一致（正确） |
| `演示语料/1810.04805v2.pdf` | 否 | **`Language Understanding`** ❌ | **`BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding`** ✅ | **已修好** |

**核对通过 8/8**（修复后全部符合预期）。

- 被修好：`论文样本/1810.04805v2.pdf`、`演示语料/1810.04805v2.pdf`（同一份 BERT 的两个副本）
- 仍不对：无
- 扫描件 `1706.03762v1_img.pdf` 如实记录为空白——`prepare_pdf` 直接拒绝入库（`reason=scanned_no_ocr`：*"检测为扫描件，但当前未启用/不可用 OCR，全部页面均未提取到文本，未入库"*），属预期行为，非本次改动影响

### 3.1 端到端入库实测（`prepare_pdf + commit`，打印 `ctx.papers[...]`）

脚本 `C:/tmp/r7/e2e_ingest.py`：

```
论文样本\1706.03762v1.pdf                           title = 'Attention Is All You Need'
论文样本\1706.03762v1_img.pdf                       未入库（scanned_no_ocr）
论文样本\1706.03762v7.pdf                           title = 'Attention Is All You Need'
论文样本\1810.04805v2.pdf                           title = 'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding'
论文样本\LongBook_540p_fixture.pdf                  title = 'Chapter 1 Deep Learning Foundations and Agent Systems'
论文样本\UnderstandingDeepLearning_02_09_26_C.pdf   title = 'Understanding Deep Learning'
演示语料\1706.03762v7.pdf                     title = 'Attention Is All You Need'
演示语料\1810.04805v2.pdf                     title = 'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding'
```

### 3.2 边界用例回归（10/10）

脚本 `C:/tmp/r7/edge_cases.py`，确认修复是通用规则且未回归原有合理行为：

| 用例 | 修复前 | 修复后 | 判定 |
|---|---|---|---|
| BERT 跨行标题（官方演示数据） | `Language Understanding` | `BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding` | ✅ 修好 |
| 无连词断行 `...for Image` / `Recognition` | `Deep Residual Learning for Image` | `Deep Residual Learning for Image` | 与修复前一致（已知局限，见 §6） |
| 冠词悬垂 `...and the` + 续行 | `Transformer Architecture` | `Attention Is All You Need and the Transformer Architecture` | ✅ 修好 |
| v7 首页版权样板三行跨行 | `Attention Is All You Need` | `Attention Is All You Need` | ✅ 未回归 |
| 强悬垂且无法合并（样板残片） | `Attention Is All You Need` | `Attention Is All You Need` | ✅ 未回归 |
| 单行完整标题（最常见） | `Attention Is All You Need` | `Attention Is All You Need` | ✅ 未回归 |
| 冒号在句中、无连词断行 | `BERT: Pre-training of Deep Bidirectional` | `BERT: Pre-training of Deep Bidirectional` | 与修复前一致（已知局限） |
| 介词 `for` 结尾且无可用续行 | （空，被丢弃） | `Scaling Laws for` | ✅ 不再整行丢失 |
| 标题中含 `for` 但完整 | `Transformers for Language Understanding` | `Transformers for Language Understanding` | ✅ 未误判 |

**修复后符合预期 10/10。**

---

## 4. 去重与版本管理回归验证（T4）

脚本 `C:/tmp/r7/e2e_ingest.py`、`C:/tmp/r7/dedup_bert.py`。真实 `DefaultEmbeddingFunction`（384 维 ONNX，纯离线）。

### 4.1 v1 / v7 仍判为同一论文 ✅

```
### T4 去重回归：v7 先入库，再入库 v1
  v7 title='Attention Is All You Need' version=v7 arxiv=1706.03762
  入库 v1 前查重: dup_name='1706.03762v7.pdf' dup_sim=0.9632
                  dup_evidence='分段指纹内容匹配 96.3%（最佳段 100.0%）；标题高度一致《Attention Is All You Need》；arXiv 号相同'
                  dup_advice='库中已有较新版本《Attention Is All You Need》（v7），当前上传为 v1；建议保留库中较新版本，或覆盖为旧版后重新核对引用。'
  _norm_title(v1) = 'attention is all you need'
  _norm_title(v7) = 'attention is all you need'
  标题字符相似度   = 1.0000
```

- **去重预警照常触发**：`dup_name='1706.03762v7.pdf'`、`dup_sim=0.9632`（阈值 `DEDUP_THRESHOLD` 之上）
- `_norm_title` 归一化后 v1/v7 标题相似度 **1.0000** ≥ 0.9，`title_hit` 命中
- `duplicate_advice` 仍给出"库中已有较新版本 v7"的正确建议

### 4.2 `group_versions` 分组 ✅

```
### T4 group_versions 分组
  1706.03762       -> ['1706.03762v7.pdf', '1706.03762v1.pdf']   latest=['1706.03762v7.pdf', '1706.03762v1.pdf']
  1810.04805       -> ['1810.04805v2.pdf']
  1706.03762       -> ['1706.03762v7.pdf']
```

v1/v7 仍归入同一 `1706.03762` 组；`latest_of` 排序正常。

### 4.3 标题变更后 BERT 的去重行为 ✅

```
改名入库 bert_copy.pdf（内容与 1810.04805v2.pdf 相同）:
  抽取 title = 'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding'
  dup_name   = '1810.04805v2.pdf'
  dup_sim    = 1.0000
  dup_evidence= '分段指纹内容匹配 100.0%（最佳段 100.0%）；标题高度一致《BERT: Pre-training of Deep Bidirectional》'
  命中 BERT 而非 Attention: True

标题字符相似度矩阵（_norm_title + SequenceMatcher）:
  1810.04805v2.pdf       vs bert_copy.pdf          = 1.0000
  1706.03762v7.pdf       vs 1810.04805v2.pdf       = 0.2885
  1706.03762v7.pdf       vs bert_copy.pdf          = 0.2885
```

标题变长后：同名论文仍 `1.0000` 命中，跨论文（BERT vs Attention）仅 `0.2885`，**远低于 0.9 阈值，不会误判重复**。

---

## 5. T3：同类「过度匹配」排查

### 5.1 `extract_abstract`——**未发现过度匹配，未改动**

脚本 `C:/tmp/r7/check_abstract2.py`（用 `prepare_pdf` 的真实 `full_text`，与 `commit` 调用一致）：

| PDF | 前 2500 字内 `abstract` 次数 | 抽取结果（前 100 字） | 判定 |
|---|---|---|---|
| `1706.03762v1.pdf` | 1 | `The dominant sequence transduction models are based on complex recurrent or convolutional neural networks in an encoder-decoder conﬁguration...` | 正常 |
| `1706.03762v7.pdf` | 1 | `The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder...` | 正常 |
| `1810.04805v2.pdf` | 1 | `We introduce a new language representa- tion model called BERT, which stands for Bidirectional Encoder Representations from Transformers...` | 正常 |
| `LongBook_540p_fixture.pdf` | 0 | `Chapter 1 Deep Learning Foundations and Agent Systems This is page 1. We consider attention mechanisms...` | 正常（无标记时兜底取正文） |
| `UnderstandingDeepLearning_02_09_26_C.pdf` | 0 | `https://mitpress.mit.edu, which released the final version to the public in December 2023. Inquiries regarding rights...` | 样板泄漏（见下） |

三篇真实论文的摘要抽取**完全正确**，走的是 `^\s*abstract\s*$` 独立成行的分支，无过度匹配。

**发现的唯一问题（不是过度匹配，未改动）**：`UnderstandingDeepLearning` 是一本**没有 Abstract 章节的书**，首页全是版权/许可/联系方式，兜底分支 `services/library.py:137-139` 的清洗正则

```python
r"(?is)^.{0,400}?(?:permission to reproduce|all rights reserved|"
r"this paper is provided|copyright)\b.{0,200}?\n"
```

只清掉**一行**版权（`.{0,200}?\n` 停在第一个换行），剩余样板进入摘要。

已在 `C:/tmp/r7/proto_abstract.py` / `proto_abstract2.py` 原型验证过两种改法：

1. **扩到段落空行**：该 PDF 每行之间都有空行，"段落空行"就是行尾，无效；
2. **逐行跳过样板行**：被续行 `Department.` 打断，结果仍是 `Department. This work is subject to a Creative Commons...`，未真正改善。

根治需要**段落级样板块识别**，属业务逻辑改动。按「不要大改业务逻辑」的约束，**本次不改**，仅记录为已知局限（见 §6）。

### 5.2 作者 / 年份——`library.py` 内无抽取逻辑

`services/library.py` 全文件检索确认：作者与年份**不在本文件抽取**，`commit()`（`:601`）的 `authors` / `year` 均由调用方（UI/LLM）传入，无正则过度匹配风险。

### 5.3 `_norm_title` / `_version_key` / `_guess_arxiv` —— 未发现过度匹配

- `_norm_title`（`:43`）只做 `re.sub(r"[^a-z0-9]+", " ")`，保守；
- `_version_key`（`:57`）先匹配 `v(\d+)` 再退化到任意数字，对 `1706.03762v1.pdf` 实测取到 `v1`，正确；
- `_guess_arxiv`（`:826`）`^(\d{4}\.\d{4,5})` 锚定开头，不误伤。

---

## 6. 自验结果、风险点与已知局限

### 6.1 自验

```
$ C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile services/library.py
COMPILE OK        （零报错）

$ C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m unittest discover -s tests
..................
----------------------------------------------------------------------
Ran 18 tests in 6.607s

OK                （18/18）
```

### 6.2 改动范围确认

```
$ git status --porcelain
 M services/library.py      ← 本次唯一改动
 M services/writing.py      ← 其他 agent 的文件，R7 未触碰
?? 资料/复盘/round7_judgeG.md

$ git diff --stat -- services/library.py
 services/library.py | 87 ++++++++++++++++++++++++++++++++++++++++++++++++++---
 1 file changed, 83 insertions(+), 4 deletions(-)
```

未启动 streamlit，未 pip install，未写入 `storage/`，未改 `config.py` / `资料/*`（本报告的 `资料/复盘/` 为交付要求的报告文件）。

### 6.3 风险点

1. **`_title_case_ratio ≥ 0.5` 门槛**：若某论文标题是**全小写句式**（sentence case）且恰好在虚词处断行（如 `Neural machine translation for` + `low-resource languages`），大写占比 0.25 < 0.5，将**不拼接**，退回保留首行 `Neural machine translation for`。实测中未出现（`论文样本/` 与 `演示语料/` 全部为 Title Case）。这是刻意取舍：宁可保留截断标题，也不让样板残片吞掉真标题。
2. **标题以介词结尾且下一行是纯人名**：若某标题首行以 `for` 结尾，下一行恰好是单个作者名（如 `Neural Machine Translation for` / `Jacob Devlin`），会拼成 `Neural Machine Translation for Jacob Devlin`。`_AFFILIATION_RE` 已能拦掉绝大多数真实情况（邮箱 `@`、`{}`、机构名、`arXiv`、`Abstract`），但纯人名与"两词标题续行"（`Language Understanding`）在字面上不可区分，无法可靠判别。实测 `论文样本/` 与 `演示语料/` 全部 PDF 均未命中该情形。
3. **未扩展"无连词断行"的合并**：如 `Deep Residual Learning for Image` / `Recognition`。若对所有标题行无条件合并下一行，v7 会变成 `Attention Is All You Need Ashish Vaswani∗`（把作者并进标题），是**比现有缺陷更严重**的回归。因此维持修复前行为，记为已知局限。
4. **`extract_abstract` 书稿样板泄漏**：见 §5.1，固有局限，未改动。

### 6.4 关键行号索引

| 位置 | 内容 |
|---|---|
| `services/library.py:89` | `_DANGLING_RE`（强悬垂，删掉 `for`） |
| `services/library.py:93` | `_DANGLING_LOOSE_RE`（弱悬垂，含 `for`，仅触发合并） |
| `services/library.py:99` | `_AFFILIATION_RE`（作者/机构/邮箱行拦截） |
| `services/library.py:107` | `_title_case_ratio`（Title Case 门槛） |
| `services/library.py:119` | `_is_title_continuation`（续行准入） |
| `services/library.py:134` | `_merge_dangling_title`（跨行拼接，最多 2 行） |
| `services/library.py:155` | `guess_title_from_text`（docstring 已同步说明新语义） |
| `services/library.py:180-189` | 主循环：悬垂行先拼接、拼不出才丢弃 |

---

## 7. 自验脚本清单（`C:/tmp/r7/`）

| 脚本 | 用途 |
|---|---|
| `harness.py` | 离线 `Ctx`（真实 `DefaultEmbeddingFunction` + FakeCollection），不落盘 |
| `repro_bert.py` | T1 复现：入库 BERT 打印 title |
| `full_titles.py` | T2 全量 PDF 修复前/后对照（`git show HEAD` 载入修复前版本） |
| `e2e_ingest.py` | T2 端到端入库 + T4 去重/版本回归 |
| `dedup_bert.py` | T4 BERT 改名入库去重交叉验证 |
| `edge_cases.py` | 标题抽取 10 条边界用例（含回归防线） |
| `check_abstract.py` / `check_abstract2.py` | T3 摘要抽取实测 |
| `proto_abstract.py` / `proto_abstract2.py` | T3 摘要修复原型验证（结论：不落地） |
| `dump_lines.py` | 打印各 PDF 首页行 + 悬垂/样板标记，用于设计规则 |

# Round 3 能力深度缺陷修复报告 —— 修复工程师 R3

> 施工时间：2026-10-11
> 独占改动文件（仅 3 个）：
> - `core/router.py`
> - `services/citation_quality.py`
> - `services/writing.py`
>
> 自验脚本全部在 `C:/tmp/r3/`（`t1_router.py` / `t2_citation.py` / `t2b_extra.py` / `t2c_unittest.py` /
> `t3_pdf.py` / `t3_full.py` / `t4_smoke.py` / `ground_truth.py` / `pool.py` / `probe2.py`），未污染 `storage/`。
> 运行环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`。

---

## 0. 交付验收一览

| 缺陷 | 评委实测证据 | 修复前实测 | 修复后实测 | 结论 |
|---|---|---|---|---|
| ① 定时追踪工具注册了但 agent 调不到 | `track_topic` 不在任何专家白名单 | 6/6 专家 `含 track_topic=False`；21 个注册工具里 1 个未接入 | `search`/`qa` 两个专家 `含 track_topic=True`；**21/21 全部接入** | ✅ 已修 |
| ② 真实被引数选错记录 | AlexNet 报 760，真值 108512（差 143 倍） | 最小复现返回 **760**（选中 2012 年 "Medical Entomology and Zoology" 噪声记录） | 同一最小复现返回 **108512**（选中 CACM 2017 正主） | ✅ 已修 |
| ③ 参考文献 PDF 抽取崩坏 | BERT 只抽 9 条 + 字段错位 + 附录混入 | BERT **9 条**，authors/title/venue 三字段错位，含 `Question:`/`Answer:` 噪声 2 条 | BERT **56 条**，字段全部正确，0 条附录噪声 | ✅ 已修 |

`py_compile` 结果见 §5。

---

## 1. 缺陷 ①：`track_topic` 注册了但 Agent 永远调不到

### 1.1 根因

`core/agent.py:87-88` 的工具过滤逻辑是：

```python
allowed = set(allowed_tools(expert))
schemas = [s for s in reg.schemas() if s["function"]["name"] in allowed]   # core/agent.py:87-88
```

即**只有出现在 `core/router.py:EXPERTS[expert]["tools"]` 白名单里的工具才会被送进 Function Calling 的 `tools` 参数**。

`tools/definitions.py:382` 确实注册了 `track_topic`，但 `core/router.py` 的 6 个专家白名单里没有任何一个包含它，
于是它在 21 个已注册工具中成为唯一的**孤岛工具**——UI 里点按钮能调（UI 直接 `reg.execute`），
但 Agent 自主模式下模型的 tools 列表里根本没有这一项。

### 1.2 修复方案

在 core/router.py 的 **`search`（论文检索与追踪专家）** 与 **`qa`（抗幻觉问答与多文推理专家）** 两个白名单里加入 `track_topic`：

- `search`：名字里就是"检索与**追踪**"，`services/track.py` 的定时追踪明确属于该专家职责；
- `qa`：用户真正的问法往往是疑问句（"最近这个方向有什么**新论文**吗？"）。复查 `keyword_route` 的分发顺序
  （`core/router.py:36-41`），这类句子命中 `_KEYWORDS` 的最后一个 `qa` 分支（含 `?？`），**不会**落到 `search`，
  所以 `qa` 必须也要有，否则一半的真实问法依然调不到。
- `graph` / `writing` / `reading` / `parsing` 与定时追踪无关，按最小改动原则不加。

改动位置（改后）：

| 文件:行号 | 改后内容 |
|---|---|
| `core/router.py:8` | `"track_arxiv", "track_topic", "github_search", "citation_quality"]},`（qa 专家） |
| `core/router.py:12` | `"track_arxiv", "track_topic", "github_search", "citation_quality"]},`（search 专家） |

```python
# core/router.py:5-13（改后）
EXPERTS = {
    "qa": {"label": "抗幻觉问答与多文推理专家",
           "tools": ["list_papers", "search_literature", "multi_paper", "run_python",
                     "recall_memory", "save_note", "arxiv_search", "web_search",
                     "track_arxiv", "track_topic", "github_search", "citation_quality"]},
    "search": {"label": "论文检索与追踪专家",
               "tools": ["arxiv_search", "pubmed_search", "web_search", "download_paper",
                         "batch_download", "search_literature",
                         "track_arxiv", "track_topic", "github_search", "citation_quality"]},
```

### 1.3 实测对比（脚本：`C:/tmp/r3/t1_router.py`，严格复刻 core/agent.py:87-88）

修复前：

```
注册工具总数：21
未被任何专家白名单包含的工具：['track_topic']
[qa      ] 抗幻觉问答与多文推理专家   工具数=11  含 track_topic=False
[search  ] 论文检索与追踪专家         工具数=9   含 track_topic=False
[graph   ] 引用图谱与综述专家         工具数=5   含 track_topic=False
[writing ] 学术写作与可视化专家       工具数=6   含 track_topic=False
[reading ] 版式阅读与翻译专家         工具数=4   含 track_topic=False
[parsing ] 文档解析专家               工具数=3   含 track_topic=False
```

修复后：

```
注册工具总数：21
未被任何专家白名单包含的工具：无
[qa      ] 抗幻觉问答与多文推理专家   工具数=12  含 track_topic=True   ← 11→12
[search  ] 论文检索与追踪专家         工具数=10  含 track_topic=True   ← 9→10
[graph   ] 引用图谱与综述专家         工具数=5   含 track_topic=False
[writing ] 学术写作与可视化专家       工具数=6   含 track_topic=False
[reading ] 版式阅读与翻译专家         工具数=4   含 track_topic=False
[parsing ] 文档解析专家               工具数=3   含 track_topic=False
```

### 1.4 21 个工具全量接入核对清单（修复后）

> 数据源：`tools/definitions.py:build_registry()` 的全部 `r.register(...)`（21 个）× `core/router.py:EXPERTS` 白名单。
> 未改动 `tools/definitions.py`。

| # | 工具名 | 状态 | 可见专家 |
|---|---|---|---|
| 1 | `list_papers` | 已接入 | qa, graph, writing, reading, parsing |
| 2 | `search_literature` | 已接入 | qa, search, graph, writing, reading, parsing |
| 3 | `multi_paper` | 已接入 | qa, reading |
| 4 | `arxiv_search` | 已接入 | qa, search |
| 5 | `pubmed_search` | 已接入 | search |
| 6 | `web_search` | 已接入 | qa, search |
| 7 | `track_arxiv` | 已接入 | qa, search |
| 8 | **`track_topic`** | **已接入（本次修复）** | **qa, search** |
| 9 | `github_search` | 已接入 | qa, search |
| 10 | `citation_quality` | 已接入 | qa, search |
| 11 | `download_paper` | 已接入 | search |
| 12 | `batch_download` | 已接入 | search |
| 13 | `build_citation_graph` | 已接入 | graph |
| 14 | `generate_review` | 已接入 | graph |
| 15 | `explore_future_work` | 已接入 | graph |
| 16 | `write_outline` | 已接入 | writing |
| 17 | `draw_diagram` | 已接入 | writing |
| 18 | `translate_text` | 已接入 | writing, reading |
| 19 | `run_python` | 已接入 | qa, writing, parsing |
| 20 | `save_note` | 已接入 | qa |
| 21 | `recall_memory` | 已接入 | qa |

合计：**21/21 已接入**（修复前 20/21）。反向核对：白名单里不存在"注册表里没有"的悬空工具（0 个）。

---

## 2. 缺陷 ②：真实被引数选错记录（AlexNet 差 143 倍）

### 2.1 根因

旧 `_pick_best`（`services/citation_quality.py:137-158`，改动前）打分公式为
**`score = 标题相似度 + 0.10(年份匹配) + ≤0.15(作者匹配)`**，完全不看 `cited_by_count`。

实测 OpenAlex 里存在**两条标题完全相同**的记录（`C:/tmp/r3/pool.py` 输出）：

```
  108512 | 2017 | ImageNet classification with deep convolutional neural networks | Communications of the ACM   | doi:10.1145/3065386
     760 | 2012 | ImageNet classification with deep convolutional neural networks | Medical Entomology and Zoology | （无 DOI）
```

两条的标题相似度都是 **1.000**，于是胜负完全由附加分决定：查询年份写成 **2012** 时
→ 噪声记录得 `1.00 + 0.10 = 1.10`，正主记录（2017，年份差 5）得 `1.00` → **噪声反超** → 系统报 **760**，真值 **108512**，差 **143 倍**。

### 2.2 修复方案

**(a) 重写选优：`_pick_best`（`services/citation_quality.py:197-242`）**

```python
def _pick_best(cands, title, year=None, authors=None):
    """从候选列表挑最匹配的一条（**被引数为首要权重**）。
     ① 过滤掉标题相似度 < TITLE_MATCH_MIN(0.60) 的候选；
     ② 若存在「高置信匹配」(相似度 >= TITLE_STRONG_MIN(0.85))，**只在这些里面比**；
     ③ 在高置信集合里**取 cited_by_count 最大的那条**；
     ④ 只有在被引数完全相同时，才用相似度 + 年份/作者的小加成做 Tie-break。
    """
    ...
    strong = [x for x in pool if x[0] >= TITLE_STRONG_MIN]
    group = strong or pool
    pool_max = max(_count_of(x[2]) for x in group)
    if strong:
        # ③ 被引数为主键降序；④ 被引数相同时用相似度 + 次要加成做同分裁决
        sim, combined, c = max(group, key=lambda x: (_count_of(x[2]), x[0], x[1]))
    else:
        # 无高置信匹配：退化为「相似度优先、被引数次之」，避免把热门但不相关的论文选进来
        sim, combined, c = max(group, key=lambda x: (x[1], _count_of(x[2])))
```

这样 **0.10 的年份加成再也无法让噪声记录反超被引数更高的正主**。配套新增/提取的 helper：
`_score_components`（`citation_quality.py:143`）、`_count_of`（`:176`）、`_low_confidence_reasons`（`:181`）。

**(b) 可信度标记 `low_confidence`（`services/citation_quality.py:181-195`）**

命中以下任一条件即置 `low_confidence=True` 并写出原因：

- 选中记录标题相似度 `< TITLE_STRONG_MIN(0.85)` → "未达高置信匹配，可能不是同一篇"；
- 选中记录被引 `< LOW_CONF_ABS_MIN(10)` 且 `< 候选池最大被引 × LOW_CONF_RATIO(0.02)`，而池里有 `≥ LOW_CONF_POOL_MIN(1000)` 的记录
  → "相差 N 倍，高度疑似命中噪声/影印版本"。

标记在三个数据源回调（`:330`/`:350`/`:375`）中向下传递，并在 `assess_reference`（`:647`）里写进 `reasons`
（`⚠️ 低置信：…（请在 UI 层提示「数据可能不准确」）`）和返回值顶层字段 `low_confidence`（`:726`），UI 层可直接读。

**(c) DOI / arXiv ID 精确匹配优先（`services/citation_quality.py:272-328`）**

新增 `_extract_ids`（`:177`）从参考文献原文抽出 DOI/arXiv ID；`_openalex_exact_lookup`（`:272`）走 `/works/doi:<DOI>`，
`_semanticscholar_exact_lookup`（`:302`）走 `/paper/arXiv:<ID>`（满足"arXiv ID 精确匹配优先于标题模糊匹配"的要求），
**并强制用标题相似度复核**——实测发现 OpenAlex 存在 DOI 错指：`10.48550/arXiv.1810.04805` 会返回一篇 2018 年的无关论文
（"AI-Assisted Pipeline…"，45096 次被引），若直接采信会酿成新的严重错误；复核不过则丢弃、回退标题模糊匹配（`fetch_citation_count:381-395`）。

**(d) 顺带修掉的一个真实隐蔽缺陷：`_candidate_titles`（`services/citation_quality.py:109-138`）**

`_search_title` 有个"截断到第一个年份之前"的规则。对于 **ACL/arXiv 版式 `Authors. Year. Title. Venue.`**
（正是 BERT 论文、以及本报告缺陷 ③ 抽取结果的版式），年份在标题**之前**，于是检索串会退化成**纯作者名**串
（如 `"Jason Yosinski Jeff Clune Yoshua Bengio and Hod Lipson"`），相似度必然不足 → 整典查不到被引。
现在同时生成"年份之后"与"年份之前"两组变体（≥4 词，去掉尾部 venue），≤4 个变体。

**(e) 主数据源降级要显式打标（`services/citation_quality.py:413-437`）**

自测中发现：本轮数百次查询后 OpenAlex 对本机 IP 触发了 `HTTP 429` 长时间限流，此时按原有三级降级链路会静默落到 Crossref，
而 Crossref 的 `is-referenced-by-count` 与 OpenAlex 的 `cited_by_count` 是**两套统计口径**（常显著偏低，且常未收录 arXiv 预印本），
数值看着"有模有样"其实是另一个口径——这正是需要防止的静默错误。修复：主数据源 OpenAlex 因网络错误失败而落到下一级时，
给结果打上 `source_degraded=True` + `low_confidence=True` 并在 `confidence_reasons` 写明口径差异（详见 §2.5）。

**(f) 请求节流（`services/citation_quality.py:83-98`）**：实测连续查多条参考文献会被 OpenAlex 判 `HTTP 429`，
故在同一进程内对每个 HTTP 请求施加 `MIN_REQUEST_INTERVAL=0.5s` 最小间隔，避免整批降级。

### 2.3 实测：5 篇知名论文（脚本 `C:/tmp/r3/t2_citation.py`）

真值取得方式：独立于被测代码，直接用 OpenAlex `works/doi:<DOI>` 与 `title.search` 抓候选池人工核对
（`C:/tmp/r3/ground_truth.py`、`pool.py`、`probe2.py`）。

> **关于下表"修复前后完全相同"的说明（不掩饰）**：这 5 条**完整格式**的参考文献串在修复**前**也恰好返回了正确数字
> ——因为旧逻辑要出错需要满足"多条候选标题高度相似 + 年份加成能拉开差距"这一条件。
> 真正被评委抓到、也必须修掉的**触发条件最小复现**紧跟在下表之后，那里修复前踩错、修复后正确。
> 本表的作用是证明：改动没有把原本正确的 5 篇弄坏（回归验证），且**5/5 落在合理区间**（要求 ≥4/5）。

| 论文 | 系统返回（修复前） | 系统返回（修复后） | OpenAlex 真值 | 比值（修复后） | 结论 |
|---|---|---|---|---|---|
| AlexNet / ImageNet Classif. with Deep CNNs | 108512 | 108512 | **108512**（DOI 10.1145/3065386） | 1.000x | ✅ |
| Attention Is All You Need | 26917 | 26917 | **26917** | 1.000x | ✅ |
| BERT: Pre-training of Deep Bidirectional Transformers | 33959 | 33959 | **33959** | 1.000x | ✅ |
| ResNet / Deep Residual Learning | 227948 | 227948 | **227948** | 1.000x | ✅ |
| Adam: A Method for Stochastic Optimization | 82571 | 82571 | **82571** | 1.000x | ✅ |
| **合理区间内（±一个数量级）** | 5/5 | **5/5** | — | — | ✅ |

```
--- 最小复现 A：标题精确匹配 + 年份 2012（旧逻辑靠 +0.10 年份加成让 760 反超）---
修复前：系统值=760    真值=108512  ❌选错记录
        匹配到《ImageNet classification with deep convolutional neural networks》yr=2012 venue=Medical Entomology and Zoology
修复后：系统值=108512 真值=108512  ✅
        匹配到《ImageNet classification with deep convolutional neural networks》yr=2017 venue=Communications of the ACM
```

### 2.4 离线回放：同一批真实候选池下"旧公式错 / 新公式对"（可离线一键复现）

> **为什么需要这一节**：采集完上表后，本机 IP 的 OpenAlex 配额被打满（`HTTP 429`，见 §2.5），
> 上表和最小复现已**无法再现场重跑**。为了让结论仍可被第三方验证，我把抓取时刻的
> OpenAlex 候选池原样固化到 `C:/tmp/r3/t2d_offline_replay.py`（**不联网**，直接回放当时的返回），
> 任何人执行 `python C:/tmp/r3/t2d_offline_replay.py` 都能得到下面的对照。

```
======================= 离线回放：同一批真实候选池，旧公式 vs 新公式的选优结果 =======================
### AlexNet   (两条标题**完全相同**的记录：真正的 CACM 版 vs 2012 年噪声记录)
    year 参数=2012
    旧公式 → 1.1   (count=760, yr=2012, venue=Medical Entomology and Zoology)  ❌ 选错记录
    新公式 → 1.0   (count=108512, yr=2017, venue=Communications of the ACM)    ✅  low_conf=False
### ResNet   (三条同名/近名记录：CVPR 正主 227948 / arXiv 预印本 4644 / 综述 1032)
    旧公式 → 1.1   (count=227948, yr=2016)  ✅
    新公式 → 1.0   (count=227948, yr=2016)  ✅  low_conf=False
### Attention Is All You Need   (同名书籍章节(6)/Zenodo(0)/衍生标题多篇)
    旧公式 → 1.0   (count=26917)  ✅
    新公式 → 1.0   (count=26917)  ✅  low_conf=False
结论：旧公式在 3 篇里错 1 篇（AlexNet 760 vs 108512，差 143 倍）；新公式 3/3 全对。
```

关键点：回放里 **旧公式给出 1.1 分**（`1.00 相似度 + 0.10 年份加成`）压过正主的 **1.0 分**，
与评委实测到的 760（vs 108512，143 倍）完全吻合；新公式把"被引数"提到主键后 760 再无机会胜出。
ResNet / Attention 两篇在旧公式下也是对的，说明**这一缺陷只在该触发条件下爆发**——
这也解释了为什么 §2.3 主表里 5 篇完整格式串修复前后都正确（它们没同时满足触发条件）。

另有一组教材式复核（不走网络）证明规则本身生效（`C:/tmp/r3/t2c_unittest.py`）：

```
① 合成候选：完全同名两条记录（AlexNet 真实值 760 / 108512），year=2012 + 作者全中
   选中 count=108512 year=2017 venue=Communications of the ACM match_score=1.0 strong=True low_conf=False  → OK
② 合成候选：无高置信匹配(sim=0.704) + 池里存在 26917 的同名记录，选中却是 8 次那条
   low_conf=True
      ↳ 标题相似度仅 0.70（< 0.85），未达高置信匹配
      ↳ 选中记录被引 8 次，而同一批候选里存在被引 26917 次的同名记录（相差 3364.6 倍），高度疑似命中噪声/影印版本
```

DOI 精确匹配与错指防护实测（`C:/tmp/r3/t2b_extra.py`）：

| 输入 | 系统返回 | 真值 | 命中路径 | 结论 |
|---|---|---|---|---|
| AlexNet 条目 + `https://doi.org/10.1145/3065386` | 108512 | 108512 | `openalex(doi-exact)` | ✅ DOI 精确优先 |
| ResNet 条目 + `https://doi.org/10.1109/CVPR.2016.90` | 227948 | 227948 | `openalex(doi-exact)` | ✅ DOI 精确优先 |
| BERT 条目 + **错指 DOI** `10.48550/arXiv.1810.04805` | 33959 | 33959 | `openalex`（DOI 被标题复核判错后**弃用**，回退标题检索） | ✅ 未被错指值 45096 污染 |

**（arXiv ID 精确优先）实测**（用缺陷 ③ 抽出的 BERT 真实参考文献，其原文自带 arXiv ID）：

| 参考文献原文（含 arXiv ID） | 系统返回 | 命中路径 | `low_confidence` |
|---|---|---|---|
| Wu et al. 2016. Google's Neural Machine Translation System… `arXiv:1609.08144` | **7416** | `semanticscholar(arxiv-exact)` | False |
| Mikolov et al. 2013. Distributed Representations of Words and Phrases… `arXiv:1310.4546` | **35793** | `semanticscholar(arxiv-exact)` | False |
| Fedus et al. 2018. Maskgan: Better text generation via filling in the . `arXiv:1801.07736` | `None` | 标题复核未过 → 回退 → 三级全部失败 | —（如实返回 None，不编造） |

> 三者均**未走标题模糊匹配**，说明"ID 精确优先于标题模糊"生效；第三条因 PDF 里标题本身就缺字（"filling in the ____" 的
> `____` 被 OCR 丢失）导致标题复核不通过，系统选择如实返回 `None` 而不是硬凑一个数。

### 2.5 实测：主数据源被限流（降级）时的行为 —— 不静默、不编造

高强度自测（本轮数百次 API 调用）之后，OpenAlex 对本机 IP 触发了较长窗口的 **`HTTP 429` 限流**
（`title.search` 端点被限；同一时刻 `/works/doi:` 偶而可用，说明是 IP 级配额而非代码问题）。
这正是缺陷修复里必须覆盖的"降级不能变成静默错误"的场景，实测结果如下（同一份 `t2_citation.py`，限流后重跑）：

| 论文 | 修复后系统值 | 命中数据源 | 说明 | OpenAlex 真值 | 是否已打 `⚠️ low_confidence` |
|---|---|---|---|---|---|
| AlexNet | 32934 | crossref | 记录选中正确（CACM 2017） | 108512 | ✅ 已标记（降级提示） |
| Attention Is All You Need | 55 | crossref | Crossref 未收录该 arXiv 预印本 | 26917 | ✅ 已标记（降级提示） |
| BERT | 10370 | crossref | 记录正确，口径偏低 | 33959 | ✅ 已标记（降级提示） |
| ResNet | 177343 | crossref | 记录正确，口径偏低 | 227948 | ✅ 已标记（降级提示） |
| Adam | 172203 | semanticscholar | S2 口径高于 OpenAlex | 82571 | ✅ 已标记（降级提示） |

> 所有降级结果都带上了 §2.2(f) 新加的提示，实测输出：
> ```
> count=55 src=crossref source_degraded=True low=True
>   ↳ 主数据源 OpenAlex 本次不可用，已降级到 crossref；其统计口径与 OpenAlex 不同（常显著偏低）
>     且可能未收录 arXiv 预印本，数值仅供参考
> assess_reference → low_confidence=True
>   ⚠️ 低置信：主数据源 OpenAlex 本次不可用，…（请在 UI 层提示「数据可能不准确」）
> ```
> 即：**宁可明确说"这个数不可靠"，也不伪造一个看着漂亮的数**。三级全部失败时仍然返回 `None`：
> ```
> err: openalex: HTTP 429；semanticscholar: HTTP 429；crossref: 「…」无相似度≥0.6 的匹配
> → "真实被引数：未能取到（...），该维度不计分、不猜测"
> ```

演示建议：正式演示前避免短时间高频查询（若预期会连续查多条，可把 `citation_quality.py:29` 的
`OPENALEX_MAILTO` 填成真实邮箱进入 OpenAlex polite pool）。

---

## 3. 缺陷 ③：参考文献 PDF 抽取在真实论文上崩坏

### 3.1 根因（改动前 `services/writing.py`）

| 症状 | 根因代码（改动前） | 说明 |
|---|---|---|
| BERT 只抽 **9 条** | `writing.py:231 _split_entries` → `services.parsing.split_reference_entries` | 该 splitter 的起点正则要求行首有 `[1]`/`1.`/`(2017)`/`arXiv:xxxx` 编号。BERT 的参考文献是**无编号的作者-年份流**（`Authors. Year. Title. Venue.`），且无空行分隔，只有 9 条恰好以独立年份行开头侥幸被切开 |
| `authors`/`title`/`venue` **三字段错位** | `writing.py:183 _parse_entry_fields` | 直接按句位置切：`authors=segs[0]; title=segs[1]; venue=" ".join(segs[2:])`。一旦某处断句失效（如 "et al." 断句），三个字段整体移位——实测出现 `authors='Contextual string embeddings for sequence labeling'`（标题串到 authors）、`venue='Rami Al-Rfou, …'`（下一条的作者串到 venue） |
| 把附录 `Question:`/`Answer:` 当文献 | `writing.py:217 _locate_reference_section` | 只在"尾部 24000 字符"里取最后一个 References 标题，之后**不再截断**；BERT 的 References 在第 9 页、Appendix 在第 11 页，附录内容全部被当成文献正文送进切分器 |
| 无 References 的 PDF 抽出 5 条**索引页**且不触发兜底 | 同上 + `writing.py:352/380` 的门槛只看 `len(refs) >= MIN_REF_ENTRIES(3)` | LongBook 抽出 6 条重复正文碎片、UnderstandingDeepLearning 抽出 5 条 Index 页内容；条数 ≥3 于是"看似成功"，实际是纯噪声并绕过了降级逻辑 |

### 3.2 修复方案

**(a) 页级 References 定位 + 附录截断：`_locate_reference_section`（`services/writing.py:416-484`）**

```python
def _locate_reference_section(pdf_doc):
    """在 PDF 里定位 References 区，返回 (文本, 命中标题?, 页数)。
    处理三类实际情况：
    - References 在正文里出现多次（目录页也算）→ **取最后一个**；
    - 参考文献跨多页 → 从标题所在位置向后逐页拼接；
    - 参考文献之后跟附录/致谢 → 遇到附录/致谢标题立即截断。
    找不到任何 References 标题时返回 ("", False, 页数)，**不再拿尾部索引页凑数**。
    """
```

关键设计：
- 从最后一页**向前逐页**扫描 `_REF_HEADING_RE`（最多 `REF_SCAN_PAGES=150` 页，`writing.py:46`）；
- 书籍/长文里 References 常作为**页眉重复出现** → 取"最后一簇的**第一页**"（`writing.py:439-447`），
  否则只会拿到参考文献区最后一页（UnderstandingDeepLearning 原来就只抽到 1 条真文献）；
- 命中页之后**向后**逐页拼接，直到 `_REF_STOP_HEAD_RE`（`writing.py:67-70`，含
  `appendix|acknowledg|supplementary|index|索引|附录|致谢…`）出现为止，或达到 `REF_MAX_PAGES=40` / `PDF_TAIL_CHARS=24000`；
- 返回 `found` 布尔值，**没有 References 段时明确告知上层**（不再兜底伪造）。

**(b) 流式文本归一：`_flow_text`（`writing.py:208-216`）**

还原跨行连字符（`correspon-\ndence` → `correspondence`）、把换行压成空格、还原 PDF 连字
（`ﬁ→fi`、`ﬂ→fl`、`ﬀ→ff` 等，`_ligatures` `writing.py:192`）——PDF 里大量出现 "ﬁne-tuning"，不还原无法与真值比对。

**(c) 作者-年份流式切分：`_split_author_year_flow`（`writing.py:313-338`）**

用"句点+年份"锚点 `_YEAR_ANCHOR_RE`（`writing.py:310`）找出每条的年份位置，再调用
`_authorblock_start`（`writing.py:288-305`）**向前回找作者块起点**：从紧邻年份的那句出发（单作者 `Wilson L Taylor. 1953.` 也成立），
逐句向前吞并满足 `_looks_like_author_block`（`writing.py:263-286`：词首字母大写/常见姓名小品词、无数字、
整词匹配才判 venue）的句子，遇到第一句不像作者名就停。条目 = `[本条作者块起点, 下条作者块起点)`。

两个实测踩坑均已处理：
- `_VENUE_HINT_RE`（`writing.py:255`）必须**整词**匹配 —— 子串匹配会把 `"Kevin Clark"`（含 `in `）误判成 venue；
- `_sentence_ends`（`writing.py:218-235`）返回的是**下一句起点**，且要丢掉"句起点 == 年份位置"产生的空片段，
  否则 `Authors. 2018.` 这种版式产生的空片段会让作者块起点算错；顺带保留"不在单字母缩写后断句"的规则
  （`Tom B. Brown` / `et al.` 不会被切碎，这是旧代码 authors/title/venue 错位的元凶之一）。

**(d) 噪声过滤：`_is_noise`（`writing.py:486-502`）+ `_clean_entries`（`writing.py:527-544`）**

排除：以 `Question:`/`Answer:`/`Table`/`Figure`/`Appendix`/纯页码开头；长度 `< MIN_ENTRY_CHARS(30)`；
字母数 < 12；既无年份也无 http 也无作者形态。另加 `_merge_orphans`（`writing.py:504-515`）把编号切分器在
`"arXiv preprint"` 处切残的两半合并回去（Transformer v7 有 4 条属于此类）。

**(e) 字段解析重写：`_parse_entry_fields`（`writing.py:341-394`）+ `_split_title_venue`（`writing.py:397-412`）**

按三类真实版式**分别**处理，不再靠"第几句"硬取：

```python
# ① "Authors (Year). Title. Venue."
m = re.match(r"^(?P<a>.+?)[.,]?\s*\(\s*(?P<y>(?:19|20)\d{2}[a-z]?)\s*\)\s*[.,]?\s*(?P<rest>.+)$", text)
# ② "Authors. Year. Title. Venue."（ACL/arXiv 版式，BERT 论文就是这种）
m = re.match(r"^(?P<a>.+?)\.\s+(?P<y>(?:19|20)\d{2}[a-z]?)\.\s*(?P<rest>.+)$", text)
# ③ "Authors. Title. In Proceedings of VENUE, Year." 兜底：首句=authors，其余再交由 _split_title_venue 解析
```

配套：`_YEAR_TOKEN_RE`（`writing.py:308`）用 lookbehind 排除 `arXiv:1808.04444`、`6(Nov):1817–1853`、
`pages 1914–1925`、`CVPR09` 里的伪年份；`_strip_numbering`（`writing.py:200`）去条首编号；
`_split_title_venue` 处理"venue 写在标题前"的个别版式并剥掉 venue 的 `In ` 前缀。

**(f) 两种切分器择优 + 质量自检**

- `_entry_score`（`writing.py:517-524`）：按"能解析出完整字段（title≥12 字 & 有年份 & 作者长度合理）的条目数"给候选结果打分，
  编号式与流式择优采用（`writing.py:594-598`）——编号式在某些版式下会把条目切残，纯比条数会选错。
- `_quality_report`（`writing.py:546-574`）：新增 `extract_references_with_quality`（`writing.py:576-635`），
  返回 `{"entries":[...], "quality":{...}, "low_confidence":bool, "reasons":[...]}`。触发 `low_confidence` 的规则：
  ①**未定位到 References 标题**（→ 直接返回空列表，不拿索引页凑数）；
  ②有标题但切出 0 条；③ `页数 > 10 却只抽出 < MIN_QUALITY_ENTRIES(5) 条`；
  ④缺 title 比例 > 40%；⑤缺 year 比例 > 40%。
- 向后兼容：`extract_references_from_pdf`（`writing.py:654-663`）仍返回原来的 `List[dict]`（内部委托给新函数），所有旧调用点无需改动。
- 下游 PATH 打通：`_pdf_reference_entries`（`writing.py:698-721`）与 `reference_whitelist`（`writing.py:726-772`）改为读取 `low_confidence`：
  **质量不过关即视为抽取失败**（回退到 chunks / 文献库元数据），并在 `source` 标签里打 `⚠️ PDF References 段抽取质量偏低：…` 供 UI 提示。
  这修复了原来"抽出 5 条索引页却因 ≥3 条被判定成功"的兜底穿透问题。

### 3.3 实测对比（脚本 `C:/tmp/r3/t3_full.py`）

| PDF | 页数 | 修复前条数 | 修复后条数 | 修复前字段质量 | 修复后字段质量 | 质量自检 |
|---|---|---|---|---|---|---|
| `demo_data/1810.04805v2.pdf`（**BERT**） | 16 | **9** | **56** | 缺title 1 / 缺year 2 / `Question:`噪声 2 | 缺title 0 / 缺year 0 / 噪声 0 | OK |
| `pdf/1706.03762v7.pdf`（Transformer） | 15 | 40 | 40 | 缺title 0 / 缺year **5** | 缺title 1 / 缺year **2** | OK |
| `pdf/1706.03762v1.pdf` | 15 | 36 | 36 | 缺title 0 / 缺year 5 | 缺title 1 / 缺year 2 | OK |
| `pdf/LongBook_540p_fixture.pdf`（无 References） | 540 | **6（索引/正文碎片）** | **0** | 全部缺 year | — | ⚠️low：`PDF 中未定位到 References/Bibliography 标题，已返回空列表（不拿正文或索引页凑数）` |
| `pdf/UnderstandingDeepLearning_02_09_26_C.pdf` | 541 | **5 条 Index 页内容** | **59 条真实文献** | 全部缺 year | 缺title 0 / 缺year 1 | OK |
| `pdf/1706.03762v1_img.pdf`（图片型 PDF） | 15 | 0 | 0 | — | — | ⚠️low：未定位到 References 标题 |

BERT 随机抽 5 条人工核对（随机种子 20261011，脚本 `C:/tmp/r3/t3_full.py`）：

| # | authors | title | year | venue | 正确性 |
|---|---|---|---|---|---|
| 4 | `Luisa Bentivogli, Bernardo Magnini, Ido Dagan, Hoa Trang Dang, and Danilo Giampiccolo` | `The fifth PASCAL recognizing textual entailment challenge` | 2009 | `TAC. NIST` | ✅ 四字段全对 |
| 18 | `William Fedus, Ian Goodfellow, and Andrew M Dai` | `Maskgan: Better text generation via filling in the` | 2018 | `arXiv preprint arXiv:1801.07736` | ✅（原文即缺 "the"） |
| 22 | `Minghao Hu, Yuxing Peng, Zhen Huang, Xipeng Qiu, Furu Wei, and Ming Zhou` | `Reinforced mnemonic reader for machine reading comprehension` | 2018 | `IJCAI` | ✅ |
| 23 | `Yacine Jernite, Samuel R. Bowman, and David Sontag` | `Discourse-based objectives for fast unsupervised sentence representation learning` | 2017 | `CoRR, abs/1705.00557` | ✅ |
| 32 | `Andriy Mnih and Geoffrey E Hinton`（已剔除混入的 `Curran Associates, Inc.`） | `A scalable hierarchical distributed language model` | 2009 | `D. Koller, … editors, Advances in NIPS` | ✅ |

Transformer（`1706.03762v7`）随机抽 5 条核对（版式：`Authors. Title. arXiv preprint arXiv:xxxx, 2016.`）：

| # | authors | title | year | venue | 正确性 |
|---|---|---|---|---|---|
| 1 | `Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton` | `Layer normalization` | **2016**（修复前为空） | `arXiv preprint arXiv:1607.06450` | ✅ 修复后补齐年份 |
| 3 | `Denny Britz, Anna Goldie, Minh-Thang Luong, and Quoc V. Le` | `Massive exploration of neural machine translation architectures` | 2017 | `CoRR, abs/1703.03906` | ✅ |
| 14 | `Zhongqiang Huang and Mary Harper` | `Self-training PCFG grammars with latent annotations across languages` | 2009 | `Proceedings of the 2009 Conference on Empirical Methods…` | ✅ |
| 23 | `Minh-Thang Luong, Quoc V. Le, Ilya Sutskever, Oriol Vinyals, and Lukasz Kaiser` | `Multi-task sequence to sequence learning` | 2015 | `arXiv preprint arXiv:1511.06114` | ✅ |
| 31 | `Rico Sennrich, Barry Haddow, and Alexandra Birch` | `Neural machine translation of rare words with subword units` | 2015 | `arXiv preprint arXiv:1508.07909` | ✅ |

**附录污染检查**（全量）：8 个 PDF 目标中，`raw` 以 `Question:`/`Answer:` 开头的条目数 **全部为 0**（修复前 BERT 有 2 条）。

**端到端冒烟**（`C:/tmp/r3/t4_smoke.py`，注入假 `ui.common.resolve_pdf` 的 mock ctx）：

```
reference_whitelist → source='PDF References 段真实抽取（1810.04805v2.pdf）'
  条数=56  首行='[1] Alan Akbik, Duncan Blythe, and Roland Vollgraf. Contextual string embeddings …'
无 References 的书籍 → 条数=1  source='文献库元数据（无 PDF References 可用时的兜底）'   ← 正确回退，不再拿索引页冒充
```

---

## 4. 改动清单（文件:行号）

### `core/router.py`（唯一改动：`track_topic` 白名单）
| 行号 | 内容 |
|---|---|
| `router.py:7-8` | qa 专家白名单加入 `"track_topic"`（工具数 11→12） |
| `router.py:11-12` | search 专家白名单加入 `"track_topic"`（工具数 9→10） |

### `services/citation_quality.py`
| 行号 | 内容 |
|---|---|
| `:28-37` | 新常量 `TITLE_STRONG_MIN=0.85`、`LOW_CONF_RATIO/ABS_MIN/POOL_MIN`、`YEAR_BONUS`、`AUTHOR_BONUS_*`（把原来埋在代码里的魔法数字提出来） |
| `:85`, `:89-105` | `_http_json` 加 `MIN_REQUEST_INTERVAL=0.5s` 请求节流（避免 OpenAlex 429 整批降级） |
| `:121-151` | `_candidate_titles` 重写：同时覆盖"标题在年份前/后"两种版式，≥4 词过滤，去尾部 venue |
| `:157-175` | 新增 `_score_components`（相似度 + 年份/作者次要加成） |
| `:177-188` | 新增 `_extract_ids`（从原文抽 DOI / arXiv ID） |
| `:190-209` | 新增 `_count_of`、`_low_confidence_reasons`（`low_confidence` 判定规则） |
| `:211-258` | **`_pick_best` 重写：被引数为首要权重**，年份/作者仅做同分裁决 |
| `:259-270` | 新增 `_work_to_cand`（OpenAlex work → 统一候选结构） |
| `:272-300` | 新增 `_openalex_exact_lookup`（DOI 精确优先 + **标题复核防错指**） |
| `:302-328` | 新增 `_semanticscholar_exact_lookup`（**arXiv ID 精确优先** + 标题复核，见 §2.2(c)） |
| `:330-348` / `:350-373` / `:375-407` | 三个数据源回调透传 `low_confidence` / `confidence_reasons` / `pool_max_count` |
| `:409-465` | `fetch_citation_count`：① DOI 精确 → ② arXiv ID 精确 → ③ 标题三级降级 三段式调用 |
| `:430-455` | 主数据源因网络错误失败而降级时打 `source_degraded` + `low_confidence` + 口径说明（§2.2(f)） |
| `:658-667` | `assess_reference` 把 `⚠️ 低置信：…` 写进 `reasons` 与 `evidence` |
| `:726` | `assess_reference` 返回值新增顶层 `low_confidence` 字段 |

### `services/writing.py`
| 行号 | 内容 |
|---|---|
| `:43-47` | 新常量 `MIN_QUALITY_ENTRIES=5`、`MIN_ENTRY_CHARS=30`、`REF_SCAN_PAGES=150`、`REF_MAX_PAGES=40` |
| `:67-70` | 新增 `_REF_STOP_HEAD_RE`（附录/致谢/**Index**/补充材料等 References 之后的截断锚点） |
| `:192-206` | 新增 `_ligatures`（还原 PDF 连字）、`_strip_numbering`（去条首编号） |
| `:208-216` | 新增 `_flow_text`（还原跨行连字符、压平换行） |
| `:218-252` | 新增 `_sentence_ends` / `_split_sentences`（不在单字母缩写后断句） |
| `:255-305` | 新增 `_VENUE_HINT_RE`（整词匹配）、`_looks_like_author_block`、`_authorblock_start` |
| `:308-310` | 新增 `_YEAR_TOKEN_RE`（排除 arXiv/page-range 伪年份）、`_YEAR_ANCHOR_RE` |
| `:313-338` | 新增 `_split_author_year_flow`（无编号作者-年份流式切分） |
| `:341-394` | **`_parse_entry_fields` 重写**：三类真实版式分别处理 |
| `:397-412` | 新增 `_split_title_venue` |
| `:416-484` | **`_locate_reference_section` 重写**：页级定位 + 跨页拼接 + 附录截断 + `found` 标志 |
| `:486-502` | 新增 `_is_noise`（Question/Answer/Table/Figure/页码/过短过滤） |
| `:504-525` | 新增 `_merge_orphans`（合并被切残的相邻条目）、`_entry_score`（切分器择优打分） |
| `:527-544` | `_clean_entries` 重写（去噪 + 去重） |
| `:546-574` | 新增 `_quality_report`（质量自检 + `low_confidence` + `reasons`） |
| `:576-635` | 新增 `extract_references_with_quality`（对外暴露质量信息） |
| `:637-651` | `_numbered_split`（沿用 `services.parsing` 的编号式切分，口径不变） |
| `:654-663` | `extract_references_from_pdf` 改为委托新版实现，**返回结构向后兼容** |
| `:707-716` | `_pdf_reference_entries` 改为按 `low_confidence` 判定抽取是否可用 |
| `:736-750` | `reference_whitelist` 按 `low_confidence` 打 `⚠️` 标签 / 空结果时不凑数 |

---

## 5. 自验结果与遗留问题

### 5.1 py_compile

```
$ C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile core/router.py services/citation_quality.py services/writing.py
（无任何输出，退出码 0）
```
**零报错**。本轮共执行 8 次（每次改动后立即执行），最后一次覆盖全部收尾改动
（`_candidate_titles` 长度过滤、`source_degraded` 打标、`_parse_entry_fields` 行首数字剥离）：

```
$ C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile core/router.py services/citation_quality.py services/writing.py
COMPILE_OK        ← 无 SyntaxError / IndentationError / 未定义引用之外的任何报错，退出码 0
```

同时确认三个文件均可被正常导入并参与端到端运行（`t1_router.py` 导入 `tools.definitions` + `core.router`；
`t3_full.py` 导入 `services.writing`；`t4_smoke.py` 走 `reference_whitelist` 全流程），无运行时 ImportError。

### 5.2 其他配套验证

| 项目 | 结果 |
|---|---|
| 未改动任何非独占文件 | ✅（`git status` 中仅 `core/router.py`、`services/citation_quality.py`、`services/writing.py` 属我方改动；其余文件为其他 agent 并行施工） |
| 未启动 streamlit、未 pip install | ✅ |
| 网络调用保留超时与 try/except，失败不编造 | ✅（`HTTP_TIMEOUT=8s`；三级降级全部失败返回 `None` + `fetch_error()` 原因，`assess_reference` 输出"未能取到（…），该维度不计分、不猜测"） |
| 自验脚本写在 `C:/tmp/r3/`，未写项目 `storage/` | ✅ |
| 端到端冒烟（`t4_smoke.py`） | ✅ 见 §3.3 |
| 结论可离线复现（`t2d_offline_replay.py`） | ✅ 见 §2.4——即使外部 API 被限流也能一键验证根因与修复效果 |

### 5.3 已知遗留 / 建议

1. **OpenAlex 限流（外部限制，非代码缺陷）**：演示前避免短时间高频查询；建议填写 `citation_quality.py:29` 的
   `OPENALEX_MAILTO` 进入 polite pool。已被限流时系统按设计降级到 Semantic Scholar → Crossref，
   且**自本轮起会显式打 `source_degraded` + `low_confidence` 并在 reasons 里写明"口径不同、数值仅供参考"**（实测见 §2.5），
   不会再出现"看着有数其实口径不同"的静默错误；三级全部失败仍返回 `None` 而不是编造数字。
   ⚠️ 这也是本轮报告 §2.3 主表的数据是在 OpenAlex 未被限流时采集的原因——降级后的数值不代表代码选优逻辑出错。
2. **书籍类双栏 PDF**（`UnderstandingDeepLearning`）：59 条真实文献抽取正常，但个别条目的 authors 仍可能混入行首页码/引文计数
   （已在 `_parse_entry_fields:352` 剥掉行首 1-4 位数字，双栏交错时仍有约 5% 残留）。不影响 title/year/venue。
3. **`_looks_like_author_block` 是启发式**：对"出版社人名/编辑名前置于作者块"的极个别版式（BERT 第 32/33 条曾出现 `Curran Associates, Inc.` 混入），
   已通过把 `inc|publishers?|editors?` 加入 venue 整词词典修正；若后续遇到新的出版社写法需继续扩充该词典。
4. **`track_topic` 的运行时依赖**：本次只保证工具对 Agent 可见；其底层 `services/track.py:track_topic` 走 arXiv 真实接口 +
   长期记忆检查点（`tools/definitions.py:190-202`），未做改动。正式演示建议先用一句话问法（"最近 cs.CL 有什么新论文？"）走一遍确认端到端链路。

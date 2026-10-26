# Round2 · 评委C 能力深度验收审计（引用图谱挖掘 + 学术 Copilot 输出）

- 评委：C（能力深度 / 真实性 / 有效性）
- 审计对象：考生3（`services/graph.py`、`services/extern.py`、`services/citation_quality.py`、`services/track.py`、`tools/definitions.py`）
  + 考生4（`services/writing.py`、`ui/page_writing.py`、`services/wcopilot_dotrender.py`）
- 运行环境（唯一实测解释器）：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`
  （Python 3.11.16 / networkx 3.6.1 / streamlit 1.63.0 / openai 3.13.0）
- 审计方式：**全部结论来自实测**，未实测的项明确标注「未复核」。只读项目代码，未修改任何 `.py/.md/.json/.html`，未 git 操作、未 pip install。
  测试脚本全部落在 `C:/tmp/judgec/`（项目外）。
- 本机真实依赖状态（实测）：`pymupdf ✓ / networkx ✓ / plotly ✓ / pandas ✓`；
  **`matplotlib ✗ / graphviz 包 ✗ / dot 可执行文件 ✗ / 全部 LaTeX 编译器 ✗`**

---

## 1. 总评

| 考生 | 得分 | 一句话结论 |
|---|---|---|
| **考生3（引用图谱）** | **7.0 / 10** | 图算法是真的、可追溯、不编造——frontier 五维手算复现一致、review 真注入社区发现/PageRank/骨干边、future 判定真走图遍历、GitHub/被引数字都来自真实 API；但**赛题要求的「定时追踪」工具注册了却永远调不到（🔴）**，且端到端真实被引存在**选错记录、数字差 143 倍**的实锤（🔴）。 |
| **考生4（学术 Copilot）** | **7.7 / 10** | 无幻觉 References 校验、CSV 自动出图、Figure Caption 数字全部经实测溯源对得上，零依赖 SVG 渲染器真的画出了图、TikZ 无编译器时明确提示——是两人中"实测跑得通"程度最高的；但 **PDF References 抽取只对排版规整的论文有效，对同属演示数据的 BERT 论文完全崩坏（🔴）**，且报告只报了好 case。 |

**一句话总评**：两位考生都**没有**"只在 prompt 里提一嘴"的造假式实现（这是我重点排查的两类问题之一，实测结果：frontier/review/future/chart_facts 全部真用数据）。真正扣分的是**"数字取错"（考生3 的 AlexNet 760 vs 108512）**和**"关键路径覆盖率不足"（考生3 的 track_topic 断链、考生4 的 BERT PDF 抽取崩坏）**。

---

## 2. 扣分清单

### 2.1 考生3

| 严重度 | 问题 | 文件:行号 | 证据（实测输出） | 扣分 | 修复建议 |
|---|---|---|---|---|---|
| 🔴 阻断 | **定时追踪工具 agent 永远看不到**：`track_topic` 已注册进 Registry，但 `core/router.py` 六个专家白名单无一包含它，而 `core/agent.py:88` 只把白名单内 schema 发给 LLM | `core/router.py:4-23`、`core/agent.py:88`、`tools/definitions.py:349` | 实测：注册表 21 个工具，`track_topic` 是**唯一**不在任何专家白名单的（"注册了但永不被 agent 看到的工具: `['track_topic']`"）；模拟 agent.py:88 过滤后 6 个专家 `含 track_topic=False`；关键词兜底路由「追踪一下这个方向的最新进展」→ `search` 专家，该专家也无 `track_topic` | **-0.8** | 把 `"track_topic"` 加入 `router.py:9-12`（search）与 `router.py:6-8`（qa）。此举只增不改、零风险，考生3 因"不能改别人的文件"而留下阻断项，**赛题交付看整体，不是看文件归属** |
| 🔴 阻断 | **真实被引取错记录，数字差 143 倍**：AlexNet 返回 **760**，OpenAlex 同一查询首条真值是 **108512** | `services/citation_quality.py:137-158` `_pick_best` | 实测：`fetch_citation_count("ImageNet Classification with Deep Convolutional Neural Networks", year=2012)` → `{'count': 760, 'venue': 'Medical Entomology and Zoology', 'authors': ['Krizhevsky'], 'match_score': 1.1}`；直接打 OpenAlex 前 3 条：`108512 (2017, Communications of the ACM)` / `760 (2012, Medical Entomology and Zoology)` / `237`；按 DOI `10.1145/3065386` 精确查真身 = **108512**。根因：两条标题归一化后完全相同（sim 均为 1.0），噪声那条靠**年份 +0.10 加成**反超（1.10 > 1.00），而 `_pick_best` 完全不看 `cited_by_count` | **-0.8** | ①相似度并列时按 `cited_by_count` 做 tie-break；②年份加成仅在 `abs(Δyear)<=1` 时给，且加成后 `score = min(score, sim + 0.25)`；③优先取 `publication_year` 与权威来源（CACM/ NeurIPS 等）匹配的记录 |
| 🟡 重要 | **venue 维度端到端失效**：字符串条目被整条原文当 venue 传给 `venue_detail`，导致 30 分维度几乎恒为「未知」，且原文含 `arXiv` 字样即被判"预印本、不参与等级评定" | `services/citation_quality.py:438`（`return (cands[0], "", year, s)` —— venue=整条原文） | 实测 `t_citation_quality` 真实调用（Attention PDF 前 6 条）：**5 条 `venue_tier` 全为「未知」**；其中「Jimmy Lei Ba… Layer normalization. arXiv preprint…」的 reason = `命中离线等级字典：预印本/未正式发表，不参与等级评定`；其余 4 条 = `离线字典未收录该 venue（Dzmitry Bahdanau, Kyunghyun Cho, and Yos…）`（可见传进去的是整条原文） | **-0.5** | 字符串条目改用正则从原文尾部抽 venue 片段；或当本地 venue 为空时优先用 API 返回的 `venue`（现有代码 `citation_quality.py:481` 因 `venue` 非空而永不触发） |
| 🟡 重要 | **frontier 的 external 维度引入错误数据**：阈值 0.60 过松导致张冠李戴，并反向污染总分 | `services/citation_quality.py:27` `TITLE_MATCH_MIN`、`services/graph.py:393-416` | 实测合成图：`detect_frontier(use_external=True)` 对《Contrastive Pretraining on Protein Sequences》给出 `外部真实被引 3 次（semanticscholar，匹配《A tri-modal contrastive learning framework for pro》相似度 0.625）→ 外部热度得分 0.20`；该论文 score 由 **65.7 被拉到 58.9**。0.625 刚过阈值，但匹配的是一篇完全不相干的论文 | **-0.3** | `TITLE_MATCH_MIN` 提到 0.75，并要求「作者姓氏或年份至少一项佐证」才计入 external；无佐证时按"不可得"处理（现有分支已支持，只差收紧阈值） |
| 🟡 重要 | **`_norm()` 遇 list 崩溃 + 失败缓存 key 不含参数**：一次错误调用会把该标题锁死 300 秒 | `services/citation_quality.py:36-41`（`_TITLE_CACHE[key]` 用 list 做 dict key）、`:254`（`key = _norm(title)` 不含 authors/year） | 实测 `fetch_citation_count("Attention Is All You Need", authors=["Ashish Vaswani", "Noam Shazeer"], year=2017)` → `None`，`fetch_error` = `openalex: TypeError: unhashable type: 'list'；semanticscholar: HTTP 429；crossref: TypeError: unhashable type: 'list'`；**随后在同一进程改回不传 authors 重测，仍返回 `None`**（`FAILURE_TTL=300` 内复用失败缓存） | **-0.3** | `_norm` 开头 `key = s if isinstance(s, str) else str(s)`；`_author_bonus` 入参统一 `str()`；缓存 key 拼入 `year/authors` |
| 🟡 重要 | **frontier 未接入 UI，页面仍展示旧"入度=0 孤儿"**：语义与标签都已过时 | `ui/page_graph.py:52 / :101 / :233`（2 参调用）、`ui/page_graph.py:130`（标签"边缘衍生（暂未被他文引用）"） | 实测 2 参调用 `classify_papers(G, metrics)` 返回的第二项 = `['C2024']`（入度=0 孤儿），非 frontier；全仓 grep 确认 `scripts/bench_graph.py:47`、`scripts/selftest.py:59`、`scripts/setup_real_library.py:70`、`scripts/verify_official.py:79`、`tests/test_core.py:181` 也全是 2 参 | **-0.2** | 改 3 参调用或直接调 `detect_frontier(ctx, G, metrics)` 并按 `score/reasons` 渲染；标签改为"前沿/潜力论文" |
| 🟡 重要 | **"引用含金量"UI 页未接真实数据**，仍是裸 `ctx.llm.chat` 主观判断（且违反 T7 统一 LLM 入口） | `ui/page_search.py:181`（`out = ctx.llm.chat([...])`），报告称在 `71-77` 行号已漂移 | 实测该处 prompt = `判断每条引用是实质性引用还是应付式引用…`，纯主观，无 `real_citations`/`venue_tier` | **-0.1** | 换成 `citation_quality.assess_references(lines)` 渲染 |
| 🟢 轻微 | **`match_score` 可 > 1.0**，展示"相似度 1.25"不严谨 | `services/citation_quality.py:157` | 实测返回 `match_score` = `1.0` / `1.1` / `1.25`（BERT 带年份+作者加成） | **-0.05** | `c["match_score"] = round(min(1.0, score), 3)` |
| 🟢 轻微 | **Future Work 关键词把段落标题词抽进来**：`future`、`present` 成为匹配关键词，易与任意含该词的论文误命中 | `services/graph.py:711-731` `_FW_STOP` 未含 `future` | 实测 `keywords=['future', 'extend', 'protein', 'docking', 'molecular']`；另一条 `keywords=['present', 'protein', ...]` | **-0.05** | `_FW_STOP` 补 `future`/`present`/`work`/`section`/`conclusion` |

**考生3 合计扣 3.05 → 7.0 / 10**

### 2.2 考生4

| 严重度 | 问题 | 文件:行号 | 证据（实测输出） | 扣分 | 修复建议 |
|---|---|---|---|---|---|
| 🔴 阻断 | **对 BERT 论文（`演示语料/1810.04805v2.pdf`，演示数据两篇之一）References 抽取完全崩坏**：只抽 9 条，authors/title/venue 三字段错位，还把附录 Q&A 当文献 | `services/writing.py:183-214` `_parse_entry_fields`（硬假设"作者. 标题. venue."顺序） | 实测：`[1] authors='Contextual string embeddings for sequence labeling'`（实为标题）、`title='In Proceedings of the 27th International Conference on Computational Linguisti'`（实为 venue）、`venue='Rami Al-Rfou, Dokook Choe, Noah Constant, Mandy Guo, and Llion Jones 2'`（实为作者）；`[5] authors='arXiv:1810.06638'`；`[7][8][9]` 抽出 `Question: Does BERT really need such a large amount of pre-t…`、`Answer: Yes, BERTBASE achieves almost 1.0% additional accuracy on MNLI…` | **-0.8** | 按"最后一个句号段落 = venue / 首个年份前 = 作者"做多模板回退；用"是否含 `(19|20)dd` + 是否以大写人名开头"过滤非文献行（Q&A 以 `Question:`/`Answer:` 开头可直接排除）；`split_reference_entries` 对 BERT 只切出 9 条（真值约 50 条），需针对"标题换行+venue 换行+作者换行"排版补规则 |
| 🔴 阻断 | **无 References 章节的 PDF 把索引页（Index）当参考文献抽出，且因 ≥3 条而不触发库元数据兜底** | `services/writing.py:282-286`（`len(full.strip()) < 200` 只校验长度）+ `:231-246`（`len(entries) < MIN_REF_ENTRIES` 才回退） | 实测 `论文样本/UnderstandingDeepLearning_02_09_26_C.pdf` → 抽出 **5 条**：`[1] title='145, 157 elastic net, 156 ensemble, 145, 157 flooding, 159 Frobenius norm, 140 …'`、`[3] title='Index 525 segmentation, 178 U-Net, 199 self-attention, 208–215 as routing, 235 …'`。5 ≥ `MIN_REF_ENTRIES=3`，`reference_whitelist` 直接采纳，白名单被垃圾填满 | **-0.5** | 抽完后加"文献性校验"：条目须含 4 位年份或 `et al./arXiv/DOI/pp.` 中至少一项，否则判为抽取失败并回退；`_locate_reference_section` 找不到锚点时应**直接返回空**而非回退后 65% |
| 🟡 重要 | **年份抽取错误**：Dropout 论文抽成 **1929**（真值 2014） | `services/writing.py:189` `_YEAR_LOOSE_RE`（`\b(19\|20)\d{2}[a-z]?\b` 全串兜底） | 实测 `演示语料/1706.03762v7.pdf` 第 `[33]` 条：`year='1929'  title='Dropout: a simple way to prevent neural networks from overfitting'`；另有 `[21][22][38]` year 为空 | **-0.3** | 年份限定 1950~当前年+1；优先取括号内年份，其次取条目尾部年份 |
| 🟡 重要 | **零依赖 SVG 渲染器含回边时分层 rank 反转，画出的拓扑顺序与真实边方向矛盾** | `services/wcopilot_dotrender.py:180-196` `_compute_ranks`（DFS 环检测 `return 0` 后未回补，且 `if p in preds` 过滤掉了无前驱源点使 rank 传播断裂） | 实测：`digraph { A -> B -> C; C -> B; }` → `ranks = {'A': 0, 'C': 1, 'B': 2}`，即 **C 画在 B 之上**，但边是 `A→B→C`；实际渲染的 5 节点例子 ranks = `{'Raw PDF':0, 'Chunk':1, 'Parser':2, 'Vector DB':2, 'Answer':3}`，视觉流向 `Raw PDF→Chunk→Parser`，而真实边是 `Raw PDF→Parser→Chunk` | **-0.3** | 先做 SCC 缩点/Tarjan 拓扑序，或忽略回边（`rank(dst) <= rank(src)` 的边不参与 rank 传播）后再最长路径分层 |
| 🟡 重要 | **PDF 换行断字未合并**，标题带连字符空格 | `services/writing.py:166-180` `_split_segments` / PDF 取文本环节 | 实测：`'Convolu- tional sequence to sequence learning'`、`'Deep residual learning for im- age recognition'`、`'Effective approaches to attention- based neural machine translation'` | **-0.2** | 取文本后统一 `re.sub(r"(\w)-\s+(\w)", r"\1\2", text)` |
| 🟡 重要 | **Graphviz 三级降级在本机只有第 3 级生效**：真实 `dot` 路径在演示环境永不触发 | `services/writing.py:811-881` | 实测：`dot -> None`、`graphviz 包 -> False`、`matplotlib -> 未安装`；`render_dot` 返回 `mode='pure'` | **-0.1** | 已在报告风险点 3 诚实声明，不重复扣；建议随包附带 `dot` 便携版或改标为"零依赖渲染为主路径" |
| 🟢 轻微 | **`chart_facts` 的 "Lowest" 在并列值时取值任意**，文案会误导 | `services/writing.py:1083` `agg.index[-1]` | 实测 `chart_facts(df, x='model', y='bleu_en_de')` 输出 `Lowest: GPT-3 (0).`，而 BERT-base / BERT-large / GPT-1 / GPT-2 的 `bleu_en_de` **同为 0** | **-0.05** | 并列时输出"最低值 X（共 N 个并列）"或不输出 |
| 🟢 轻微 | **DOT 注释解析不干净**，多出 phantom 节点 | `services/wcopilot_dotrender.py:32-35` `_strip_comments` | 实测 `digraph { // comment\n A -> B; /* blk */ B -> C; }` → `nodes=4`（应为 3） | **-0.05** | 行内 `/* */` 前的 token 需重新切分语句 |

**考生4 合计扣 2.30 → 7.7 / 10**

---

## 3. 实测记录（输入 → 真实输出）

### 3.1 考生3

#### 实测 1：`fetch_citation_count` 真实被引（干净进程，不传 authors）

```
输入：fetch_citation_count("Attention Is All You Need", year=2017)
输出：{'count': 26917, 'source': 'openalex', 'matched_title': 'Attention Is All You Need',
      'match_score': 1.0, 'year': 2025, 'venue': '',
      'authors': ['Ashish Vaswani','Noam Shazeer','Niki Jitendra Parmar','Jakob Uszkoreit','Llion Jones'],
      'query_used': 'Attention Is All You Need'}

输入：fetch_citation_count("BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding", year=2019)
输出：{'count': 33959, 'source': 'openalex', 'match_score': 1.1, 'year': 2019,
      'venue': 'Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies (NAACL-HLT)',
      'authors': ['Jacob Devlin','Ming‐Wei Chang','Kenton Lee','Kristina Toutanova']}

输入：fetch_citation_count("Deep Residual Learning for Image Recognition", year=2016)
输出：{'count': 227948, 'source': 'openalex', 'matched_title': 'Deep Residual Learning for Image Recognition', 'match_score': 1.1, 'year': 2016}

输入：fetch_citation_count("ImageNet Classification with Deep Convolutional Neural Networks", year=2012)   ★问题项
输出：{'count': 760, 'source': 'openalex', 'match_score': 1.1, 'year': 2012,
      'venue': 'Medical Entomology and Zoology', 'authors': ['Krizhevsky']}

输入（对照，直接打 OpenAlex 同查询）：
  cited_by_count=108512  year=2017  venue='Communications of the ACM'   title='ImageNet classification with deep convolutional neural networks'
  cited_by_count=  760   year=2012  venue='Medical Entomology and Zoology' title='ImageNet classification with deep convolutional neural networks'
  cited_by_count=  237   year=2014  venue='AASRI Procedia'
输入（按 DOI 10.1145/3065386 精确查）：display_name='ImageNet classification with deep convolutional neural networks', cited_by_count=108512, year=2017
```

#### 实测 2：三级降级链（monkeypatch 破坏各级）

```
[级1 OpenAlex 人为报 503]
输出：{'count': 10370, 'source': 'crossref', 'matched_title': 'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding', 'match_score': 1.1, ...}
      → 真的跳过 OpenAlex，落到 Crossref ✅

[级1 503 + 级2 429]
输出：{'count': 10370, 'source': 'crossref', ...}     → 继续降级到 Crossref ✅

[三级全破坏]
输出：None
      fetch_error = "openalex: TypeError: ...；semanticscholar: TypeError: ...；crossref: TypeError: ..."
      → 全失败返回 None，不编造数字 ✅
```

> 注：Crossref 口径（10370）与 OpenAlex（33959）差异 3.3 倍，属数据源口径差异，非缺陷；但 UI 需标明来源。

#### 实测 3：`detect_frontier` 五维是否真用（5 节点合成图，stub embedding）

```
图：A2018(in=3,out=0) B2025(in=1,out=1) C2024(in=1,out=2) D2010(in=0,out=0) E2025b(in=0,out=2)
边(a→b 表示 a 引用 b)：B2025→A2018, C2024→A2018, C2024→B2025, E2025b→A2018, E2025b→C2024

输出（use_external=False）：
score=79.8 E2025b
   components = {'low_indegree': 1.0, 'recency': 0.9, 'out_degree': 1.0, 'interdiscipline': 0.395}
   - 本地被引 0 次（入度低得分 1.00）
   - 发表年份 2025（时间衰减得分 0.90）
   - 出度 2（引用文献数量得分 1.00）
   - 跨学科距离 0.39（自身vs引用文献质心 0.38，引用文献两两差异 0.41）
score=69.6 C2024     components={'low_indegree':0.75,'recency':0.8,'out_degree':1.0,'interdiscipline':0.376}
score=65.7 B2025     components={'low_indegree':0.75,'recency':0.9,'out_degree':0.5,'interdiscipline':0.465}
score=21.7 D2010     components={'low_indegree':1.0,'recency':0.0,'out_degree':0.0}
   - 无可计算向量邻居，跨学科维度跳过
   - 入度=0 且出度=0，判为孤立噪声节点而非前沿 (-0.20)      ★噪声惩罚真的生效
score=17.1 A2018     components={'low_indegree':0.25,'recency':0.2,'out_degree':0.0}

手算复现（W={'low_indegree':0.25,'recency':0.20,'out_degree':0.15,'interdiscipline':0.25,'external':0.15}）：
  E2025b   手算 79.9 / 代码 79.8  一致
  C2024    手算 69.6 / 代码 69.6  一致
  B2025    手算 65.7 / 代码 65.7  一致
  D2010    手算 21.7 / 代码 21.7  一致
  A2018    手算 17.1 / 代码 17.1  一致

embedding 不可用（stub 抛异常）：
  E2025b components={'low_indegree':1.0,'recency':0.9,'out_degree':1.0}
    - embedding 不可用，跨学科维度跳过      ★缺失维度权重归一化，不零分惩罚 ✅

换图对照：
  空图 -> []
  随机图(8节点 seed=7) -> 8 条，top3 = [(3, 71.0), (1, 58.4), (4, 57.6)]      ★输出随 G 变化 ✅
```

#### 实测 4：`generate_review` 是否真用 G 和 metrics（回显式 FakeLLM 抓 prompt）

```
review_structure 原始输出：
  n_nodes=5 n_edges=5
  clusters: #0「diffusion/generation/models」1 篇 avg 2010 reps=['D2010']；
            #1「attention/contrastive/cross」4 篇 avg 2023 reps=['A2018','B2025','C2024']
  timeline: [(2010,['D2010']), (2018,['A2018']), (2024,['C2024']), (2025,['B2025','E2025b'])]
  backbone: 5 条（均带 evidence='sim-evidence'）
  pr_top:   [('A2018',0.403),('B2025',0.1906),('C2024',0.1691),('D2010',0.1187),('E2025b',0.1187)]

注入 LLM 的 prompt 原文（节选）：
  "你在为本领域撰写一篇**基于引用图谱的**中文综述（900字内）。图谱由 networkx 从本地文献库
   真实解析的引用关系计算而来：共 5 个节点、5 条引用边，已做社区发现聚簇。…
   ## 一、研究脉络（时间线）… - 2010：《Diffusion Models for Molecular Generation》(2010) …
   ## 二、主要方向（按主题簇）- 主题簇 #0「diffusion/generation/models」（1 篇，平均年份 2010）
      代表论文（PageRank 最高 1 篇）：《Diffusion Models for Molecular Generation》(2010) …
   ## 三、关键基石（PageRank Top）- 《Transformer Attention for Molecules》(2018)  PageRank=0.4030 …
   ## 四、分歧与展望 … 引用骨干边（谁引用了谁，构成发展脉络）：
      - 《Contrastive Pretraining on Protein Sequences》 → 引用 → 《Transformer Attention for Molecules》（证据：sim-evidence）…"

换成 8 节点随机图（seed=11）后 prompt：
  "共 8 个节点、18 条引用边 …
   - 主题簇 #0「randpaper1/randpaper2/randpaper4」（4 篇，平均年份 2019.8）…
   - 《RandPaper1》(2019)  PageRank=0.2189 …
   跨簇边：《RandPaper1》 → 引用 → 《RandPaper6》（跨主题簇连接）"
  → 与真实图 prompt **不相同** ✅（证明 prompt 内容完全由 G/metrics 计算而来，不是写死模板）

空图：走「图谱为空」旧分支，prompt = 摘要拼接版 ✅

LLM 不可用时确定性输出（节选）：
  ### 一、研究脉络（时间线，按论文年份排序，共 5 个节点 / 5 条引用边）
  - **2010**：《Diffusion Models for Molecular Generation》(2010) …
  ### 二、主要方向（networkx 社区发现聚簇，按簇平均年份排序）
  - **主题簇 #0「diffusion/generation/models」**（1 篇，平均年份 2010）：代表论文 …
  ### 三、关键基石（PageRank Top）…  ### 四、分歧与展望 …
  > 注：LLM 不可用，以下综述由 networkx 图谱结构确定性生成（未编造内容）。
  #### 图谱结构明细（networkx 计算所得，非 LLM 生成）
```

#### 实测 5：`explore_future_structured` 是否真走图遍历

```
构造：A(2018) 正文含 "Future Work: we plan to extend our approach to protein docking and molecular dynamics simulation."
      B(2020)《Protein Docking with Molecular Dynamics Simulation》引用 A
      C(2021)《A Survey of Numerical Integration》引用 A（只命中 1 个关键词）
      D(2022) 与 A 无引用关系
      边：B→A, C→A

_reachable_citers(G,'A') = {'B': 1, 'C': 1}      _reachable_citers(G,'D') = {}

_future_status(ctx, G, 'A', ['extend','protein','docking','molecular','dynamics'])
  → ('已解决', ['《Protein Docking with Molecular Dynamics Simulation》(2020) 以 1 跳引用了本文，命中关键词 4 个：protein、docking、molecular、dynamics'])
_future_status(ctx, G, 'A', ['protein'])
  → ('存疑', ['…命中关键词 1 个：protein（仅命中 1 个关键词，证据不足）', '《A Survey of Numerical Integration》(2021) …'])
_future_status(ctx, G, 'D', kws)
  → ('开放', ['引用图中无任何论文引用了该文献（无 1/2 跳后继）'])

反向对照（删掉 B→A 这条边）：
  A 的状态: 存疑   （删边前: 已解决）      ★判定随图结构变化，是真图遍历 ✅

explore_future 输出（LLM 不可用 → 确定性）：
  > 引用图可达性判定：共 4 条，已解决 1 条、存疑 3 条、开放 0 条。
  | Future Work: we plan to extend our approach to protein docking and molecular dynamics simulation in the near future. | **已解决** | 《Baseline Study of Force Fields》(2018)：《Protein Docking with Molecular Dynamics Simulation》(2020) 以 1 跳引用了本文，命中关键词 3 个：protein、docking、molecular |
  段落兜底的条目均打 ⚠️段落兜底 且状态降为「存疑」 ✅
```

#### 实测 6：`track_topic` 可见性 + 功能本体

```
注册表共 21 个工具：['list_papers','search_literature','multi_paper','arxiv_search','pubmed_search','web_search',
  'track_arxiv','track_topic','github_search','citation_quality','download_paper','batch_download',
  'build_citation_graph','generate_review','explore_future_work','write_outline','draw_diagram','translate_text',
  'run_python','save_note','recall_memory']

>>> track_topic 是否在任一专家白名单中: False
>>> 注册了但永不被 agent 看到的工具: ['track_topic']

模拟 core/agent.py:88 的过滤（schemas = [s for s in reg.schemas() if s["function"]["name"] in allowed]）：
  expert=qa        可见 schema 数=11  含 track_topic=False
  expert=search    可见 schema 数=9   含 track_topic=False
  expert=graph     可见 schema 数=5   含 track_topic=False
  expert=writing   可见 schema 数=6   含 track_topic=False
  expert=reading   可见 schema 数=4   含 track_topic=False
  expert=parsing   可见 schema 数=3   含 track_topic=False

关键词兜底路由：
  '最近有什么新论文'            -> expert=qa      有 track_topic=False
  '追踪一下这个方向的最新进展'    -> expert=search  有 track_topic=False
  '定时追踪 cs.AI'             -> expert=search  有 track_topic=False

功能本体（真实 arXiv，stub memory）：
  第1次 track_topic("cs.AI", 3) → {"count":3,"new_count":3,"new":[{"arxiv_id":"2610.12466v1",
      "title":"On the estimation and validity of AI time horizons---a statistical look at the METR plot",
      "published":"2026-10-08"}, {"arxiv_id":"2610.12463v1",…},{"arxiv_id":"2610.12452v1",…}]}
  第2次 同参数 → count=3 new_count=0, new=[]         ★检查点增量检测真实生效 ✅
  t_track_topic(topic="cs.CL", 2) → {"mode":"topic","count":2,"new_count":2,
      "new":[{"arxiv_id":"2610.12427v1","title":"FastBench: Can Streaming VLMs Perceive High-Dynamic Real-World Streams?",
              "github":["https://github.com/Ashone3/FastBench."]}, …]}
```

#### 实测 7：`github_repo_info` 真实仓库

```
https://github.com/huggingface/transformers
  stars=167374  forks=34809  open_issues=2347  license=Apache-2.0
  pushed_at=2026-10-11T01:24:19Z  last_push_days=0  archived=False
  has_readme=True  readme_chars=17082  readme_source=api.github.com/readme
  reproducibility=高  score=6.5  degraded=False
  reasons:
    - stars 167374 ≥ 500，社区关注度高 (+2.0)
    - 存在 README（17082 字，来源 api.github.com/readme）(+1.5)
    - README 内容较完整（≥1500 字）(+0.5)
    - 有明确开源许可证 Apache-2.0 (+1.0)
    - 最近推送在 0 天内，维护活跃 (+1.5)
    - 综合得分 6.50 → 复现性评级「高」

https://github.com/owner-does-not-exist-zzz/repo-zzz-999
  → degraded=True, error="GitHub API HTTP 404", reproducibility=未知, stars=None   ★不编造 ✅
not-a-github-link
  → degraded=True, error="无法从 URL 解析 owner/repo：not-a-github-link"              ★不编造 ✅
```

#### 实测 8：端到端 `t_citation_quality`（真实 PDF → 真实被引）

```
输入：extract_references_from_pdf(演示语料/1706.03762v7.pdf) 前 6 条的 raw
输出：avg_score=19.2  grade_distribution={'A':0,'B':0,'C':6}
      network_assessed=5  real_citation_hits=4
      note: "前 5 条已查询真实被引（OpenAlex→Semantic Scholar→Crossref）；其中 4 条取到真实数字，其余标注为不可得，未做任何数值猜测"

  ref [1] Layer normalization            → level=低含金量 real_citations=None venue_tier=未知 score=0.0
  ref [2] Neural machine translation…    → level=中含金量 real_citations=14357 venue_tier=未知 score=45.0
  ref [3] Massive exploration of NMT…    → level=中含金量 real_citations=459   venue_tier=未知 score=40.0
  ref [4] Long short-term memory-networks→ level=中含金量 real_citations=1083  venue_tier=未知 score=45.0
  ref [5] Learning phrase representations→ level=中含金量 real_citations=25198 venue_tier=未知 score=45.0
```

### 3.2 考生4

#### 实测 9：`extract_references_from_pdf` 真实 PDF（3 份）

```
[A] 演示语料/1706.03762v7.pdf（Attention Is All You Need）→ 抽出 40 条 ✅
  抽查 5 条（人工核对）：
   [1] 'Layer normalization'                                        authors='Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton'   ✅正确
   [2] 'Neural machine translation by jointly learning to align and translate' 2014  venue='CoRR, abs/1409.0473, 2014'                ✅正确
   [13] 'Long short-term memory' 1997                                                                                               ✅正确
   [20] 'Adam: A method for stochastic optimization' 2015                                                                           ✅正确
   [35] 'Sequence to sequence learning with neural networks' 2014                                                                   ✅正确
  瑕疵：[9] 'Convolu- tional sequence to sequence learning'（断字）
        [11] 'Deep residual learning for im- age recognition'（断字）
        [33] year='1929' 'Dropout: a simple way to prevent neural networks from overfitting'  ★年份错（真值 2014）
        [21][22][38] year 为空

[B] 演示语料/1810.04805v2.pdf（BERT）→ 只抽 9 条，字段三错位 + 混入附录 Q&A  ★🔴
   [1] authors='Contextual string embeddings for sequence labeling'      ← 这是标题
       title='In Proceedings of the 27th International Conference on Computational Linguisti'  ← 这是 venue
       venue='Rami Al-Rfou, Dokook Choe, Noah Constant, Mandy Guo, and Llion Jones 2'          ← 这是作者
   [5] authors='arXiv:1810.06638'  title='Wilson L Taylor'  year=1953
   [7] title='(2018a): MNLI Multi-Genre Natural Language Inference is a large-scale, crowdso…'
   [8] authors='Question: Does BERT really need such a large amount of pre-t…'
       title='Answer: Yes, BERTBASE achieves almost 1.0% additional accuracy on MNLI when tr…'   ★附录 Q&A 被当文献
   [9] authors='Question: Does MLM pre-training converge slower than LTR pre…'

[C] 论文样本/UnderstandingDeepLearning_02_09_26_C.pdf（无 References 章节）→ 抽出 5 条索引页内容  ★🔴
   [1] '145, 157 elastic net, 156 ensemble, 145, 157 flooding, 159 Frobenius norm, 140 …'
   [2] 'L2, 155 REINFORCE, 392 reinforcement learning, 374–401 action value, 378 advan…'
   [3] 'Index 525 segmentation, 178 U-Net, 199 self-attention, 208–215 as routing, 235 …'
   [5] 'Index 527 conditional, 345 decoder, 338 disentanglement, 342 encoder, 338 esti…'
   → 5 ≥ MIN_REF_ENTRIES(3)，不会回退到库元数据，直接被采纳为白名单
```

#### 实测 10：`validate_reference_whitelist`（幻觉检测 vs 误杀）

```
白名单 = Attention PDF 前 8 条真实条目

场景A（LLM 编造：越界 [11][99] + "A Totally Fabricated Paper About Nothing" + "Quantum Blockchain for Neural Alchemy"）：
  ok = False  n_citations=8  n_entries=4  n_allowed=8
  🚨 检测到 5 条疑似编造文献/越界角标：
    - [citation] [11] 角标超出真实文献范围 [1..8]
    - [citation] [99] 角标超出真实文献范围 [1..8]
    - [entry] "A Totally Fabricated Paper About Nothing…" 最高相似度仅 0.36（阈值 0.8）
    - [entry] "Quantum Blockchain for Neural Alchemy…"    最高相似度仅 0.44（阈值 0.8）
  → 抓幻觉有效 ✅

场景B（逐字照抄真实白名单 8 条）：
  ok = True  report="✅ 引用白名单校验通过：10 处 [n] 角标、8 条 References 条目，全部命中 8 条真实文献。"
  → 零误杀 ✅

场景C（常见书写变体）：
  通过  "[1] Ba et al. Layer normalization. arXiv preprint, 2016."
  通过  "[2] Bahdanau et al. Neural machine translation by jointly learning to align and translate. 2014."
  通过  "[1] Layer normalization."
  通过  "[2] Bahdanau, D., Cho, K., Bengio, Y. Neural Machine Translation by Jointly Learning to Align and Translate. CoRR, 2014."
  → 缩写/大小写/去作者均不误杀 ✅

场景D（半编造：真实标题 + 篡改 venue/年份）：
  "[1] … Layer normalization. Nature, 1999."      → ok=True（被放过）
  "[2] … Neural machine translation…. FakeConf, 1800." → ok=True（被放过）
  → 属报告风险点 1 已声明的固有限制
```

#### 实测 11：TikZ 编译 + Graphviz 三级降级

```
本机环境：tectonic/pdflatex/xelatex/lualatex → 全部 None；dot → None；graphviz 包 → False；matplotlib → 未安装

try_compile_tikz(真实 tikzpicture 代码)：
  ok=False  engine=""  png=None
  reason="本机未检测到 LaTeX 编译器（已依次查找 tectonic/pdflatex/xelatex/lualatex），无法本地渲染。已降级为源码展示 + 在线编辑器入口。"
  >>> 是否静默失败: False     ★明确提示，非静默 ✅
  UI 侧 page_writing.py:293-295 另有 st.info + Overleaf / tikz.dev 链接 ✅

render_dot(5 节点 DOT，含 rankdir=LR 与一条回边 C->B[label="retry"])：
  ok=True  mode='pure'  log="未找到 dot 可执行文件（PATH 中无 graphviz）。"
  note="降级渲染（等价拓扑图，零依赖 DOT→SVG 分层布局，非 Graphviz 官方渲染）"
  svg = 2119 字符

SVG 结构自检（★不是空 SVG）：
  <svg> 根标签: 1        viewBox: 0 0 266 382
  <rect> 节点框: 6（含 1 个背景 rect，实际节点 5）
  <path> 连线: 6（5 条边 + 1 个箭头 marker path）
  <text> 标签: 6
  <marker> 箭头: 1
  标签文本: ['retry', 'Raw PDF', 'Parser', 'Chunk', 'Vector DB', 'Answer']
  viewBox=(0,0,266,382) 节点框数=5 越界数=0
  未转义裸 '&' : 0
  实际节点坐标: Raw PDF y=28 / Chunk y=124 / Parser y=220 / Vector DB y=220 / Answer y=316
                ★含回边时 Chunk 被画在 Parser 之上，与真实边方向 Parser→Chunk 矛盾（见 2.2 🟡）

parse_dot 7 组用例：
  链式 nodes=3 edges=2 directed=True svg=1218
  无向 nodes=3 edges=2 directed=False svg=1162
  引号+属性 nodes=2 edges=1 svg=901
  无分号 nodes=3 edges=2 svg=1218
  含环 nodes=2 edges=2 svg=1027
  含注释 nodes=4 edges=2 svg=1431   ★应为 3 节点（注释解析残留）
  非DOT中文 nodes=0 edges=0 svg=None  ★不误画 ✅

非 DOT 输入走 render_dot：mode='none' ok=False → 展示源码 ✅
```

#### 实测 12：`infer_chart_plan` / `chart_facts` / `caption_has_numbers`（演示语料/data.csv，9 行 6 列）

```
classify_columns：
  {'model':'categorical','params_M':'numeric','bleu_en_de':'numeric','bleu_en_fr':'numeric',
   'train_gpu_days':'numeric','infer_ms_per_sent':'numeric'}      ★与人工判定一致

infer_chart_plan（3 个推荐）：
  1) kind=bar      x=model y=params_M
     reason: "因为检测到 model 是类别列（基数 9<15）、params_M 是数值列，推荐柱状图（做分组对比）。"
  2) kind=heatmap  dims=['params_M','bleu_en_de','bleu_en_fr','train_gpu_days','infer_ms_per_sent']
     reason: "因为检测到 5 个数值列（>=3），推荐相关性热力图（一次性看清两两相关强度）。"
  3) kind=parallel dims=同上
     reason: "因为检测到 5 个数值列（>=3）且样本量 9 适中，推荐平行坐标图（观察多维样本分布与分簇）。"
  ★依据文案与列类型完全一致 ✅

chart_facts(df, x='model', y='bleu_en_de')：
  Table shape: 9 rows x 6 columns.
  Column 'bleu_en_de': min=0 (@BERT-base), max=28.4 (@Transformer-big), mean=10.99, median=0.
  Top-3 model by mean bleu_en_de: Transformer-big (28.4), Transformer-base (27.3), LSTM-SOTA (25.2).
  Lowest: GPT-3 (0).
  corr(params_M, train_gpu_days) = 1.000 (|r|=1.000).
  corr(bleu_en_de, bleu_en_fr) = 0.999 (|r|=0.999).
  corr(params_M, infer_ms_per_sent) = 0.966 (|r|=0.966).

数字溯源核对（★逐条对得上，无幻觉）：
  df['bleu_en_de'] 实值 [27.3, 28.4, 0.0, 0.0, 0.0, 0.0, 0.0, 18.0, 25.2]
  min=0.0  max=28.4  mean=10.9889  median=0.0                 ← 与 facts 完全一致 ✅
  idxmin 行 = {'model':'BERT-base', …}                         ← @BERT-base 一致 ✅
  idxmax 行 = {'model':'Transformer-big','bleu_en_de':28.4,…}  ← @Transformer-big 一致 ✅
  groupby top3 = [('Transformer-big',28.4),('Transformer-base',27.3),('LSTM-SOTA',25.2)]  ← 一致 ✅
  |r| Top3 = [(1.0,'params_M','train_gpu_days'),(0.999,'bleu_en_de','bleu_en_fr'),(0.966,'params_M','infer_ms_per_sent')] ← 一致 ✅
  ★唯一瑕疵：Lowest 报 "GPT-3 (0)"，但 BERT-base/BERT-large/GPT-1/GPT-2 同为 0（并列取最后一个，任意）

chart_facts(df, x='params_M', y='infer_ms_per_sent')：
  Trend over 'params_M': infer_ms_per_sent rises from 12 to 210 (delta 198, 1650%).
  Pearson correlation between 'params_M' and 'infer_ms_per_sent': r=0.966 (strong positive).
  → df 中 params_M 28→175000 对应 infer 12→210，首末值正确 ✅

caption_has_numbers（前缀剥除有效）：
  True  <- "Figure 1. Accuracy increases steadily from 72.1% to 89.4%."
  False <- "Figure 1. Accuracy increases steadily."          ★前缀数字被正确剥除 ✅
  True  <- "图 2：准确率从 72 提升至 89。"
  False <- "Figure 3. No numbers at all here."

figure_caption（桩 LLM 抓 prompt）——真实数字确实进入 prompt：
  "关键数值事实（必须真实引用其中的数字，禁止编造未出现的数字）：
   Table shape: 9 rows x 6 columns.
   Column 'bleu_en_de': min=0 (@BERT-base), max=28.4 (@Transformer-big), mean=10.99, median=0.
   Top-3 model by mean bleu_en_de: Transformer-big (28.4), Transformer-base (27.3), LSTM-SOTA (25.2).
   …
   corr(params_M, infer_ms_per_sent) = 0.966 (|r|=0.966)."
  ★prompt 中 28.4 / 27.3 等 df 真实数字齐全 ✅
```

#### 实测 13：`ui/page_writing.py` 进程内冒烟（注入 stub ctx 后调 render）

```
异常数: 0     error 数: 0
subheader: ['📊 CSV 数据可视化（自动出图）', '✍️ 无幻觉论文框架（生成后自动白名单校验）',
            '🏗️ 架构拓扑图（真实渲染）', '🌐 学术翻译']
button: 4
★与考生4 报告"实测 #14（0 异常、4 个 subheader）"一致 ✅
（注：直接 AppTest 跑页面模块本身渲染 0 组件——模块只定义 render(ctx)，需注入 ctx 才有输出；
  此点不影响结论，但也说明该报告的"UI 冒烟"依赖自建 harness，非页面独立可跑）
```

---

## 4. 虚报核查（声称做了 vs 实际）

### 考生3

| 报告声称 | 核查结论 | 证据 |
|---|---|---|
| OpenAlex `Attention Is All You Need` → 26917；`BERT` → 33959 | ✅ **属实** | 干净进程实测 26917 / 33959，完全一致 |
| S2 稳定 429 | ✅ **属实** | 实测 `semanticscholar: HTTP 429` |
| Crossref 通但检索质量弱 | ✅ **属实** | 降级到 Crossref 时返回 10370（BERT），口径不同但能命中 |
| `assess_reference(Attention/NeurIPS/2017)` → 高含金量/26917/75.0 | ✅ **属实**（干净进程） | 实测 `level=高含金量 real_citations=26917 venue_tier=A类 score=75.0` |
| 三级降级"全部失败返回 None 不编造" | ✅ **属实** | 三级全破坏 → `None` + 明确 error |
| GitHub `huggingface/transformers` stars 167127 / Apache-2.0 / readme 17082 | ✅ **属实**（stars 随时间戳自然增长到 167374） | 实测 readme_chars=17082、license=Apache-2.0 完全一致 |
| `classify_papers` 2 参/3 参均正常、向后兼容 | ✅ **属实** | 实测 2 参返回 2-tuple 且解包正常；全仓 8 处调用方全为 2 参，无破坏 |
| `review_structure` 聚出 2 个主题簇 + 时间线 + 骨干边 | ✅ **属实** | 实测 2 簇 + 4 个年份时间线 + 5 条骨干边 |
| `track_topic` 第 1 次 new_count=2、第 2 次 0 | ✅ **属实** | 实测 3/3 → 然后 3/0 |
| ❗「T5 未闭环：agent 仍看不到 track_topic」 | ✅ **诚实承认，但仍是交付阻断** | 实测确认 21 个工具中唯一孤儿。考生3 归因"不能改 `core/router.py`"，但赛题按整体交付评分，见 🔴 |
| ❗`ui/page_search.py:71-77 _quality` 未接 | ✅ 诚实承认，但**行号已漂移**（实测在 `ui/page_search.py:181`），且该处还是裸 `ctx.llm.chat`，违反其自称的 T7 统一入口 | 实测 prompt 为纯主观判断 |
| ❗报告"实测"只展示 Attention 的 `26917 → 高含金量` | ⚠️ **呈现不完整**：端到端 `t_citation_quality` 跑 Attention PDF 的真实参考文献时，命中率仅 4/5，且 **5 条 venue_tier 全为「未知」**（venue 维度恒零）。报告未提端到端表现 | 实测 8 |

### 考生4

| 报告声称 | 核查结论 | 证据 |
|---|---|---|
| `1706.03762v7.pdf` 抽出 40 条，`[2] Bahdanau… Neural machine translation by jointly learning to align and translate, 2014, CoRR abs/1409.047` | ✅ **属实** | 实测 40 条，`[2]` 完全一致 |
| 白名单校验：干净→ok=True、毒样本→ok=False、空库→报"白名单为空" | ✅ **属实** | 实测场景 A/B/C 全部吻合，缩写变体不误杀 |
| `try_compile_tikz` 无编译器 → `ok=False` + 明确 reason，TEMP 无残留 | ✅ **属实** | 实测 reason 完整、非静默 |
| `render_dot` 7 组 DOT 用例；非 DOT 不误画 | ✅ **基本属实**（用例不同无法逐字节比对，量级一致：5 节点 5 边 → 2119 字节 / 266×382） | 实测 7 组，非 DOT → `mode=none` |
| SVG 结构自检：节点框全在 viewBox 内、边齐全、标签无缺失、无未转义 | ✅ **属实** | 实测 5 框越界 0、6 path、6 text、未转义 0 |
| `infer_chart_plan` demo CSV → 柱状 + 热力 + 平行坐标 | ✅ **属实** | 实测完全一致 |
| `chart_facts` → `min=0 (@BERT-base), max=28.4 (@Transformer-big)`、`corr(acc, loss)=-0.993` 类输出 | ✅ **属实**（后者为其自建时间序列 df，我用 demo CSV 得到的 corr 也逐条对得上） | 实测 12 |
| `caption_has_numbers`：3/3 符合预期 | ✅ **属实** | 实测 4 例全对 |
| UI 冒烟 `AppTest` 0 异常、4 个 subheader | ✅ **属实** | 注入 ctx 后实测 0 异常、4 个 subheader 完全吻合 |
| ❗「实测记录」表只列了 `1706.03762v7.pdf` 一篇真实 PDF | ⚠️ **选择性呈现（隐瞒坏 case）**：同目录的 `1810.04805v2.pdf`（BERT，同为「一键加载演示数据」会加载的第二篇）抽取**完全崩坏**（9 条、字段三错位、混入附录 Q&A），报告一字未提 | 实测 9[B] |
| ❗声称「不足 3 条则回退库元数据」是安全兜底 | ⚠️ **兜底闸门防不住"抽到 ≥3 条垃圾"**：实测无 References 的 PDF 抽出 5 条索引页内容，因 ≥3 被直接采纳，白名单被垃圾填满 | 实测 9[C] |
| ❗声称零依赖渲染器是"等价拓扑图" | ⚠️ **含回边时不等价**：层级顺序与真实边方向矛盾（C 画在 B 之上，但边是 A→B→C） | 实测 11 |
| 声称「无未完成项」 | ⚠️ 与上述 3 条冲突，过于乐观 | — |

---

## 5. 交叉接口问题

| # | 接口 | 核查结果 | 证据 |
|---|---|---|---|
| 1 | **考生4 → 考生1**：复用 `split_reference_entries` | ✅ **真的 import 了，字段对得上**。`writing.py:235` 内 `from services.parsing import split_reference_entries`；该函数（`services/parsing.py:662`）返回 **`list[str]`**（实测 `['[1] Vaswani et al. Attention is all you need. NeurIPS, 2017.', …]`，元素类型 `[str,str,str]`）；`writing._parse_entry_fields(i, raw)` 接受 str 并正确解析出 `title='Attention is all you need'` | 实测 10（脚本 `t10_contract.py` [4]） |
| 2 | **考生4 → 考生3**：复用 `graph.ref_section` / `graph._ref_entries` | ⚠️ **依赖了私有下划线函数**。`writing.py:241` 与 `:360` 调用 `graph._ref_entries`（`services/graph.py:90`，带 `_` 前缀的内部函数）。考生3 若重构 `_ref_entries` 会**直接打断写作链路**且无编译期报错。建议考生3 提供一个公开别名 `ref_entries` 供外部使用 | grep：`writing.py 引用 graph 的符号: ['_ref_entries', 'ref_section']` |
| 3 | **考生3 → 考生1/UI**：`classify_papers` 增加可选第三参 `ctx` | ✅ **向后兼容成立**。实测 2 参调用仍返回 2-tuple 且第二项保持旧语义（入度=0 孤儿）；全仓 9 处调用方（`ui/page_graph.py:52/101/233`、`tools/definitions.py:247`、`scripts/bench_graph.py:47`、`scripts/selftest.py:59`、`scripts/setup_real_library.py:70`、`scripts/verify_official.py:79`、`tests/test_core.py:181`）**全部为 2 参**，无一被打断 | 实测 10（脚本 `t10_contract.py` [1][2]） |
| 4 | **考生3 → 工具层**：`_graph()` 第 5 项由「孤儿列表」变「frontier 列表」，`t_build_graph` 的 `edge_derivative` 由 `list[str]` 变 `list[dict]` | ✅ **无其他消费方被打断**。全仓 grep `edge_derivative` 只在 `tools/definitions.py:263` 与陈旧的 `dist/AI4SResearchAgent/_internal/` 打包副本中出现；`t_review` / `t_future` 的解包不受影响 | grep 全仓 |
| 5 | **考生3 → UI**：`detect_frontier` / `assess_references` 未接线 | 🔴 **接口存在但无人消费**。`detect_frontier` 只在工具链（`tools/definitions.py:249`）被调用；`ui/page_graph.py` 仍是 2 参 `classify_papers`，页面标签仍是"边缘衍生（暂未被他文引用）"。`assess_references` 全仓零调用方 | 实测 6、实测 10 |
| 6 | **考生4 → 考生1**：`ui/common.py` 的 `resolve_pdf` | ⚠️ 未在本次审计范围内单独复现（考生4 声称只读复用），但 `_resolve_pdf`（`writing.py:306-325`）内 `from ui.common import resolve_pdf` 是**服务层反向依赖 UI 层**，分层上有坏味道；实测在 `ui.common` 导入失败时有三层兜底，未崩溃 | 代码走查 |

---

## 6. 无副作用核查

| 项 | 结论 |
|---|---|
| 项目文件变更 | ✅ 未修改任何 `.py/.md/.json/.html`（`git status` 的 M 项全部来自其他考生的既有改动，非本次审计产生；本次仅新增本报告文件） |
| 网络调用 | 只读 GET：OpenAlex / Semantic Scholar / Crossref / GitHub API / arXiv。无写操作 |
| 持久化副作用 | ⚠️ 仅 1 处：`core/llm.py` 的追加式失败日志 `storage/llm_failures.log` 新增 2 条（`2026-10-11 14:13:19 graph.py:55 _llm_text()` / `14:14:18`），由我在无 API Key 下调用 `generate_review`/`explore_future` 触发，属该模块设计的失败落盘行为，非破坏性 |
| 临时文件 | 全部落在 `C:/tmp/judgec/`（项目外）；`try_compile_tikz` 因本机无编译器未创建任何 `wcopilot_tikz_*` 目录（与考生4 报告一致） |
| 进程/服务 | 未启动 streamlit web 服务；`AppTest` 为进程内运行，未占端口 |
| 缓存污染 | `citation_quality._CACHE` 为进程内内存缓存，不落盘 |

---

## 7. 评分明细

| 维度（0-10） | 考生3 | 考生4 | 说明 |
|---|---|---|---|
| 真实性（是否真做、数据是否真） | 8.0 | 8.5 | 考生3 真实 API + 真实图算法，但 AlexNet 选错记录、venue 恒零；考生4 真实 PDF/真实数字/真实 SVG，仅渲染器 rank 反转 |
| 有效性（实测能否达成目标） | 6.5 | 6.5 | 考生3 有 track_topic 断链 + 端到端 venue 失效；考生4 有 BERT PDF 崩坏 + 索引页污染 |
| 赛题契合度 | 6.0 | 8.0 | 考生3「定时追踪」作为自主工具调用的一环未闭环 → 扣重；考生4 的无幻觉 References / 自动出图 / Caption 全部命中赛题明列要求 |
| 鲁棒性 | 6.0 | 6.0 | 考生3 `_norm(list)` 崩溃 + 缓存 key 缺参数；考生4 PDF 排版假设脆弱 + 兜底闸门失效 |
| 无副作用 | 9.5 | 9.5 | 两人均未产生破坏性副作用 |
| **加权平均** | **7.0** | **7.7** | |

**两人共同优点（值得肯定）**：
- 都没有出现"只在 prompt 里提一嘴"的造假：`detect_frontier` 手算复现一致、`generate_review` prompt 随 G 变化、`explore_future_structured` 判定随删边变化、`chart_facts` 每个数字都能在 df 里对上——这是我重点排查的两类最伤分问题，两位考生**均未命中**。
- 失败路径全部"不编造、明确降级"：三级 API 全失败 → `None` + 原因；GitHub 404/限流 → `degraded=True` + 文案；TikZ 无编译器 → 明确提示 + 在线编辑器链接。

**最需要优先修的 3 件事**：
1. 🔴 把 `"track_topic"` 加进 `core/router.py` 的 `search`/`qa` 白名单（一行改动，解除赛题阻断）。
2. 🔴 修 `citation_quality._pick_best` 的 tie-break（并列相似度时按 `cited_by_count` 取大），消除"真实被引差 143 倍"的数字事故。
3. 🔴 修 `writing._parse_entry_fields` 对非"作者.标题.venue."排版的适配 + 抽完后的"文献性校验"，让 BERT 这篇演示论文的 References 不再崩坏、索引页不再被当白名单。

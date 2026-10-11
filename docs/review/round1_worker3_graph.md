# Round1 · 考生3号 自审报告（引用图谱挖掘 + 自主工具调用）

- 时间：2026-10-11
- 独占改动文件（仅这 5 个，其余 .py 未碰）：
  `services/graph.py`、`services/extern.py`、`services/citation_quality.py`、`services/track.py`、`tools/definitions.py`
- 约束遵守：未启动 streamlit / web 服务，未 pip install；所有网络调用均带 timeout + try/except，
  失败返回结构化降级信息，不编造数据。

---

## 0. 实测环境与外部 API 连通性（真实 curl / Python 实测，非"未实测"）

测试环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（openai 3.13.0 / networkx 3.6.1，项目实际运行环境）。
注意：`anaconda3` 根环境**没有 openai 模块**，只能做语法检查；运行时验证一律用 `envs/ai_research`。

| 接口 | 实测结果 | 证据 |
|---|---|---|
| OpenAlex `api.openalex.org/works?filter=title.search:...` | ✅ 通 | `Attention Is All You Need` → `cited_by_count=26917`；`BERT: Pre-training...` → `33959`，且能带出 venue `NAACL-HLT` |
| Semantic Scholar `graph/v1/paper/search` | ❌ 稳定 429 | 连续 3 次请求均返回 `{"message":"Too Many Requests...","code":429}`（无 key 共享 IP 限流） |
| Crossref `api.crossref.org/works?query.bibliographic=` | ✅ 通（但检索质量弱） | HTTP 200，`is-referenced-by-count` 字段存在；实测对 "Attention is all you need" 返回的是 `Is Attention All You Need?`(55) 等偏题结果，相似度校验后会判为未命中 |
| GitHub `api.github.com/repos/{o}/{r}` | ✅ 通 | `huggingface/transformers` → stars 167127 / forks 34795 / license Apache-2.0 / pushed_at 2026-10-10 / archived false |
| GitHub README `raw.githubusercontent.com/.../HEAD/README.md` | ❌ 不通 | curl 退出码 35（SSL connect error），`%{http_code}=000` |
| GitHub README 兜底 `api.github.com/repos/{o}/{r}/readme` | ✅ 通 | base64 解码后 17082 字符 |
| arXiv（`arxiv` 库，经 `t_track_topic` 实测） | ✅ 通 | `cs.AI` 返回真实论文 `2610.12466v1`、`2610.12463v1`（2026-10-08） |

**关键发现（已写进代码设计）**：
1. OpenAlex 的 `filter=title.search:` 值里**不能出现逗号**，即使 percent-encode 成 `%2C` 也会被 edge 拒绝
   （实测报错 `A filter value contains an unescaped comma`）。因此在 `citation_quality.py:84 _search_title()`
   统一清洗标题（去逗号/括号/年份/arXiv/DOI）。
2. Semantic Scholar 在本网络长期 429，所以 OpenAlex→S2→Crossref 的降级链在**实际运行中基本是
   OpenAlex 命中或全部未命中**两种结果，这一点在报告"风险点"里如实标注。

---

## T1 [P0-5] 引用含金量评估 —— 真实被引数据链路

文件：`services/citation_quality.py`（重写，保留 `score_reference/score_references` 旧签名与返回结构）

### 1.1 `fetch_citation_count(title, authors=None, year=None)` — `citation_quality.py:245`

三级降级，每级 8s 超时（`HTTP_TIMEOUT=8`，`:25`），每级最多试 2~3 个标题变体：

```python
_LOOKUPS = (                                   # citation_quality.py:238
    ("openalex", _openalex_lookup),            # :162  /works?filter=title.search:  → cited_by_count
    ("semanticscholar", _semanticscholar_lookup),  # :190 /graph/v1/paper/search → citationCount
    ("crossref", _crossref_lookup),            # :213  /works?query.bibliographic → is-referenced-by-count
)

for name, fn in _LOOKUPS:
    for q in _candidate_titles(title):
        try:
            res = fn(q, year, authors)
            if res is not None:
                res["query_used"] = q
                _cache_put(key, True, data=res); return res
        except urllib.error.HTTPError as e:
            errs.append(f"{name}: HTTP {e.code}")
        except Exception as e:
            errs.append(f"{name}: {type(e).__name__}: {str(e)[:80]}")
_cache_put(key, False, err="；".join(errs))     # 全部失败：缓存失败原因
return None                                     # ← 绝不返回编造数字
```

- 标题匹配守卫 `_pick_best()`（`:137`）：`difflib` 相似度 + 年份命中 +0.10 + 作者姓氏命中 +0.15，
  **低于 `TITLE_MATCH_MIN=0.60`（`:27`）判为未命中**，避免拿别论文的被引数冒充。
  返回值带 `matched_title` / `match_score` / `source` 供审计。
- 标题清洗 `_search_title()`（`:84`）+ `_candidate_titles()`（`:99`）：从参考文献原文里剥离作者/年份/venue，
  生成多个查询变体（实测修复了"整条参考文献原文查不到"的问题，见下）。
- 缓存：`_CACHE`（`:33`）成功永久缓存（进程内），失败缓存 `FAILURE_TTL=300` 秒（`:26`）防雪崩；
  `fetch_error(title)`（`:60`）读取最近一次失败原因。

实测（同一进程）：

```
fetch_citation_count("Ashish Vaswani et al. Attention Is All You Need. ... (NeurIPS), 2017.")
→ {'count': 26917, 'source': 'openalex', 'matched_title': 'Attention Is All You Need',
   'match_score': 1.0, 'authors': ['Ashish Vaswani', 'Noam Shazeer', ...], 'query_used': 'Attention Is All You Need'}

fetch_citation_count("J. Devlin, M. Chang, K. Lee, K. Toutanova. BERT: ... 2019.")
→ {'count': 33959, 'source':'openalex', 'venue': '...(NAACL-HLT)'}

fetch_citation_count("Zzz Nonexistent Paper Title Qqq 12345")   → None
fetch_error(...) → "openalex: 无相似度≥0.6 的匹配；semanticscholar: HTTP 429；crossref: 无相似度≥0.6 的匹配"
```

### 1.2 `venue_tier(venue_name)` — `citation_quality.py:350`（字典 `_VENUE_RULES` 在 `:279`）

内置离线字典，返回 `A类/B类/C类/未知`；配套 `venue_detail()`（`:338`）给出原因：

- A 类（CCF-A/顶会顶刊）：NeurIPS、ICML、ICLR、CVPR、ICCV、ECCV、ACL/TACL、EMNLP、NAACL、AAAI/IJCAI、
  KDD、SIGIR、WWW、TPAMI、IJCV、JMLR、SIGMOD/VLDB/ICDE/TODS/TOIS、SIGCOMM/OSDI/SOSP/安全顶会、
  ICSE/FSE/ISSTA/TOSEM、CHI/UIST/SIGGRAPH/TOG、ACM MM、Nature/Science/Cell/PNAS
- B 类：ICASSP、COLING/EACL/CoNLL、AISTATS/UAI/COLT/ECML、ICIP/ICPR/BMVC/WACV、MICCAI、TNNLS/TKDE/TMLR 等
- C 类：IEEE Access、Neurocomputing/KBS/Information Sciences、MDPI/PLOS 综合刊、Procedia/LNCS/Workshop
- 水刊黑名单（命中判 C 类并注明）：SCIRP、IOSR、IJERT、IRJET、IJSER、IJARCS、IJCA、
  "International Journal of Advanced Research" 等
- 预印本（arXiv/bioRxiv/preprint/under review）→ **"未知" + "预印本未正式发表，不参与等级评定"**

实测：`venue_tier("Advances in Neural Information Processing Systems (NeurIPS) 2017")="A类"`、
`venue_tier("arXiv preprint")="未知"`、`venue_tier("Some Unknown Journal")="未知"`、
`venue_tier("IEEE Access")="C类"`。

### 1.3 `assess_reference(entry, citing_paper_text=None)` — `citation_quality.py:441`

四维综合，输出 `{level: 高/中/低含金量, real_citations: int|None, venue_tier, score, reasons[], evidence{}}`：

| 维度 | 实现 | 位置 |
|---|---|---|
| (a) 真实被引 | ≥500→+45 / ≥100→+40 / ≥30→+30 / ≥10→+20 / ≥1→+10 / 0→+0；**取不到 → +0 并在 reasons 写明"不计分、不猜测"** | `:456-480` |
| (b) venue 等级 | A+30 / B+20 / C+5 / 未知+0 并说明 | `:484-489` |
| (c) 是否 Related Work 凑数 | `_position_flag()`（`:402`）在引用方正文里定位标题短语（`_locate` `:370`），判断命中位置是否**全部**落在 Related Work/文献综述窗口（`_related_work_window` `:391`）→ 凑数 −20；正文多处命中 +10；定位不到则如实标注"无法判定"、不扣分 | `:491-497` |
| (d) 主题相关性 | `_relevance()`（`:418`）标题实词在引用方正文的覆盖率：≥50% +20 / ≥25% +10 / 否则 −10（疑似凑数） | `:503-512` |

等级：≥60 高含金量 / ≥35 中含金量 / 否则低含金量。**真实被引取不到时最高只能到 50 分（中），不会误判为高**。

实测：`{'title':'Attention Is All You Need','year':2017,'venue':'NeurIPS'}` →
`{'level':'高含金量','real_citations':26917,'venue_tier':'A类','score':75.0, reasons:[...4 条...]}`

### 1.4 接入工具链（未改 schema）

`tools/definitions.py:139 t_citation_quality` 内部升级：前 `CITATION_NETWORK_BUDGET=5` 条走真实网络链路
（常量在 `definitions.py:15`），保留原有 `avg_score/grade_distribution/top_refs/bottom_refs`，
新增 `assessed[]`（含 `real_citations`/`venue_tier`/`reasons`）与 `note`。
实测同一函数对真实文献返回 `real_citations=26917 → 高含金量`，对编造条目返回 `null → 低含金量`。

> ⚠️ **需要 UI 配合**：`ui/page_search.py:71-77 _quality` 目前仍是纯 LLM 主观判断，**我没改**（别人的文件）。
> 建议 page_search owner 把 `ctx.llm.chat(prompt)` 换成 `assess_references(refs, citing_paper_text=...)` 渲染，
> 才能让"引用含金量"页展示真实被引数。

---

## T2 [P1-6] frontier 前沿论文识别模型

文件：`services/graph.py`

### 2.1 `detect_frontier(ctx, G, metrics, use_external=True)` — `graph.py:299`

取代"入度=0 即边缘"的孤儿节点逻辑。五维打分，权重常量 `_FRONTIER_WEIGHTS`（`graph.py:35`）：

```python
_FRONTIER_WEIGHTS = {"low_indegree": 0.25, "recency": 0.20,
                     "out_degree": 0.15, "interdiscipline": 0.25, "external": 0.15}
```

| 维度 | 计算 | 代码位置 |
|---|---|---|
| ① 本地入度低 | `1 - in_deg/(n-1)` | `:338-342` |
| ② 时间新近性 | `1 - (cur_year - y)/10`，**年份缺失取中性 0.30 并在 reasons 注明"未做推断"** | `:344-352` |
| ③ 高出度 | `out_deg / max_out`（引用大量文献 = 综述/跨学科倾向） | `:354-357` |
| ④ 跨学科 | `自身向量 vs 引用文献质心距离`（`:366`）+ `引用文献两两平均距离`（`_mean_pairwise_distance :287`） | `:359-377` |
| ⑤ 外部真实被引 | 复用 T1 的 `fetch_citation_count`，`ext = log10(1+c)/3`；**取不到则该维度不计分并在 reasons 说明** | `:393-416` |

- **噪声排除**：入度=0 且出度=0 → −20 分并注明"判为孤立噪声节点而非前沿"（`:379-383`）——
  这正是审计 D-#2 指出的"孤儿≠前沿"。
- **缺失维度权重归一化**：`wsum = Σ 可用维度权重`（`:385`、`:421`），不会因为某维度不可得而把论文误杀到 0 分。
- **外部请求节流**：只对预打分 Top-`FRONTIER_EXTERNAL_TOPK=5`（`graph.py:24`）查网络。
- 返回 `[{"paper","title","score","reasons":[...],"components":{...}}]`（按 score 降序，最多 `FRONTIER_TOP_N=10`），
  每条都带可解释理由。

实测（5 节点合成图，embedding 用 stub）：

```
69.2 Contrastive Pretraining on Protein Sequences(2025)
     本地被引 0（1.00）/ 2025（衰减 0.90）/ 出度 1（0.50）/ 跨学科距离 0.33
67.1 Cross Domain Survey of Molecular Representations(2024)   ← 高出度+跨学科，不是孤儿
     本地被引 1（0.75）/ 2024（0.80）/ 出度 2（1.00）/ 跨学科距离 0.29
```

### 2.2 向后兼容 & 字段名契约

- `classify_papers(G, metrics, ctx=None)` — `graph.py:240`：**仍是 2-tuple**，`ui/page_graph.py:22` 的
  `corner, edge = classify_papers(G, metrics)` 不受影响；传入 `ctx` 时第二项才升级为 frontier 列表。
- `tools/definitions.py:255 t_build_graph` 保留字段名 `edge_derivative`，内容改为 frontier 结果：
  ```python
  "edge_derivative": [{"paper": f["title"], "score": f["score"], "reasons": f["reasons"]} for f in frontier]
  ```
  `_graph()`（`definitions.py:240`）返回 5-tuple 的第 5 项由"孤儿列表"改为 frontier 列表，
  `t_review`/`t_future` 的解包不受影响。

> ⚠️ **需要 UI 配合**：`ui/page_graph.py:46-54` 目前展示的是 `classify_papers(G, metrics)`（2 参调用）
> 得到的旧"入度=0"列表。请 page_graph owner 改为 `classify_papers(G, metrics, ctx)` 或
> 直接调 `detect_frontier(ctx, G, metrics)`，并按 `score/reasons` 渲染，才能展示前沿论文与理由。

---

## T3 [P1-7] 图谱驱动的自动综述

文件：`services/graph.py`，`generate_review(ctx, G, metrics, cornerstone)` — **签名不变**（`graph.py:644`）

### 3.1 新增骨架抽取（纯 networkx 计算，不经过 LLM）

- `_communities(G)`（`:482`）：`nx.community.greedy_modularity_communities` → 失败降级
  `nx.community.louvain_communities(seed=42)` → 再失败降级 `nx.weakly_connected_components`。
- `review_structure(ctx, G, metrics, cornerstone)`（`:510`）：
  - 每个簇取 PageRank 最高的 `REVIEW_REPS_PER_CLUSTER=3` 篇作代表（`:538`）
  - 簇按平均年份排序形成时间脉络并重排 `#id`（`:544-546`）
  - 引用骨干边：按两端 PageRank 之和取 Top `REVIEW_EDGE_BACKBONE=15`（`:556`），保留 `evidence`
  - 跨簇桥接边 `bridges` Top5（供"分歧与展望"）
  - 时间线 `timeline` 按年份分组、`pr_top` PageRank Top5
- `_cluster_label()`（`:499`）：簇标签=成员标题高频实词 top3（统计得出，非 LLM 编造）

### 3.2 prompt 注入与结构化输出

`generate_review` 把「主题簇+簇代表 / 时间线 / 引用骨干边 / 跨簇边 / PageRank Top / 基石清单」
注入 prompt（`_structure_blocks` `:564`），要求 LLM 严格用四个 Markdown 小标题：

```
## 一、研究脉络（时间线） / ## 二、主要方向（按主题簇） / ## 三、关键基石（PageRank Top） / ## 四、分歧与展望
```

并写死三条硬性约束：只用给出的论文与边、禁止引入库外文献、每个小标题至少点名 1-2 篇。

**LLM 兜底**：`_llm_ok()`（`:63`）判断是否为伪响应；不可用时改由
`_deterministic_review()`（`:595`）**完全用图谱数据**生成同样四段结构的 Markdown，不编造。
最后统一追加 `_structure_appendix()`（`:627`）「图谱结构明细（networkx 计算所得，非 LLM 生成）」，
把簇代表、骨干边原始依据附在文末供核查。

实测（LLM 不可用分支）：

```
### 一、研究脉络（时间线，按论文年份排序，共 5 个节点 / 5 条引用边）
- **2018**：《Transformer Attention for Molecules》(2018)
- **2025**：《Contrastive Pretraining on Protein Sequences》(2025)、《Diffusion Models ...》(2025)
### 二、主要方向（networkx 社区发现聚簇，按簇平均年份排序）
- **主题簇 #0「attention/diffusion/generation」**（2 篇，平均年份 2021.5）：代表论文 ...
- **主题簇 #1「contrastive/cross/discovery」**（3 篇，平均年份 2023）：代表论文 ...
### 三、关键基石（PageRank Top）  ### 四、分歧与展望
```

---

## T4 [P1-14] Future Work 判定用引用图可达性

文件：`services/graph.py`

### 4.1 结构化引擎 `explore_future_structured(ctx, G)` — `graph.py:833`

```python
def _reachable_citers(G, src):          # graph.py:774
    """G 边 a→b 表示 a 引用 b，故引用者 = predecessors；1 跳=直接引用，2 跳=B→X→src"""
    res = {m: 1 for m in G.predecessors(src) if m != src}
    for x in list(res):
        for m in G.predecessors(x):
            if m != src and m not in res: res[m] = 2
    return res
```

`_future_status(ctx, G, src, keywords)`（`:793`）判定：
- 存在 B 在 1/2 跳内引用 A，且 B 标题/摘要命中 **≥2 个**该 future work 关键词，且 B 年份 ≥ A−1 → **"已解决"**
- 仅命中 1 个关键词，或 B 年份早于 A → **"存疑"**（并写明"年份早于本文，不作为已解决证据"）
- 无任何关键词命中 → **"开放"**

`_future_snippets()`（`:742`）仍做正则抽取，但**正则失配的 `text[-900:]` 兜底被显式标记**
`snippet_fallback=True`，并在输出里打 `⚠️段落兜底`、状态降为"存疑"（审计 D-#4 指出的误当 Future Work 问题）。

输出：`[{"item","source_paper","source_title","year","keywords","status","evidence":[],"snippet_fallback"}]`

### 4.2 `explore_future(ctx, G)` — `graph.py:876`（返回类型仍为 Markdown 字符串，UI/工具契约不变）

流程：结构化判定 → 渲染确定性 Markdown 表（`_future_markdown` `:863`）→ **LLM 只做最后一步语义确认**
（prompt 明确"你的任务不是重新判断"，只做确认/改判"部分解决"+ 提炼开放方向）→ LLM 不可用时直接输出
确定性判定结果，不做语义改写、不编造证据。

实测：

```
| 提出的方向（来源） | 状态 | 判断依据（引用图证据） |
| Future Work: we plan to extend to protein docking ... | **存疑** | 《Contrastive Pretraining on Protein Sequences》(2025)
     以 2 跳引用了本文，命中关键词 1 个：protein（仅命中 1 个关键词，证据不足） |
> 引用图可达性判定：共 5 条，已解决 0 条、存疑 3 条、开放 2 条。
```

---

## T5 [P1-9] 定时追踪注册为 agent 可调用工具

### 5.1 `services/track.py:57 track_topic(ctx, topic, max_results=8, only_new=True)`

新增便捷函数（复用**已有** `diff_new`/`extern` 能力，不重写增量逻辑）：
- `_fetch_topic()`（`track.py:49`）：`cs.AI` 形式走 `extern.latest_arxiv`，关键词走 `extern.search_arxiv(sort="submitted")`
- 调用既有 `diff_new(ctx, topic, items)` 做检查点比对；抓取/比对失败都返回带 `error` 的结构化结果

### 5.2 `tools/definitions.py:190 t_track_topic` + 注册 `definitions.py:349`

```python
r.register("track_topic", "定时追踪：按 arXiv 分类（如 cs.AI）或关键词抓取最新论文，返回「上次检查点之后」的新增论文…",
           {"type": "object", "properties": {
               "topic": {...}, "arxiv_id": {...},
               "max_results": {"type":"integer","default":8},
               "only_new": {"type":"boolean","default":True}}}, t_track_topic)
```

入参 `topic` 或 `arxiv_id` 二选一；传 `arxiv_id` 时内部复用既有的 `t_track_arxiv` 单篇版本追踪。

实测（真实 arXiv 网络）：

```
第 1 次 t_track_topic(topic="cs.AI", max_results=2)
→ {"count":2,"new_count":2,"new":[{"arxiv_id":"2610.12466v1",...},{"arxiv_id":"2610.12463v1",...}]}
第 2 次（同参数）→ {"count":2,"new_count":0}      # 检查点生效，增量检测真实
```

> 🔴 **阻塞项（需 router owner 配合，我不能改 `core/router.py`）**：
> `core/router.py:4-23` 的 `EXPERTS[*]["tools"]` 里**没有 `track_topic`**，
> 而 `core/agent.py:88` 只把 `allowed_tools(expert)` 内的 schema 发给 LLM：
> `schemas = [s for s in reg.schemas() if s["function"]["name"] in allowed]`。
> 因此**现在 agent 循环仍然看不到这个工具**。请把 `"track_topic"` 加入 `search` 专家
> （`router.py:9-12`）并视需要加入 `qa` 专家（`router.py:6-8`），T5 才算真正闭环。

---

## T6 [C-7] GitHub 复现性真实化

文件：`services/extern.py`（我选择写在 extern.py，与既有 `detect_github` 同处一处）

- `github_repo_info(url)` — `extern.py:159`
  - `parse_github_repo()`（`:125`）支持 https / ssh / `.git` 形式
  - 真实 GET `https://api.github.com/repos/{owner}/{repo}`（`GITHUB_TIMEOUT=8`，`:14`）取
    `stargazers_count / forks_count / open_issues_count / license.spdx_id / pushed_at / archived`
  - `pushed_at` → `last_push_days`（`:198-203`）
  - `_fetch_readme()`（`:133`）两级：`raw.githubusercontent.com/.../HEAD/README.md`
    → 失败降级 `api.github.com/repos/{o}/{r}/readme`（base64 解码）
  - 评分（可解释加权，`:212-258`）：stars 档位 +0~2.0、有 README +1.5（≥1500 字再 +0.5）、
    许可证明确 +1.0、最近推送 ≤180 天 +1.5 / ≤365 +1.0 / ≤730 +0.5、archived −3.0
    → `≥4.0 高 / ≥2.5 中 / 否则低`，`reasons[]` 逐条列出加分与依据
  - 限流/失败：`HTTPError 403/429` → `degraded=True` + 明确文案
    "GitHub API 限流或禁止访问（HTTP 403）：未认证额度 60 次/小时，可配置 GITHUB_TOKEN 提额。本次不做任何推测性评级"
- 支持 `GITHUB_TOKEN`（`extern.py:13`，读环境变量）提额；无 token 时按 60 次/小时工作。

实测：

```
github_repo_info("https://github.com/huggingface/transformers")
→ stars 167127, forks 34795, open_issues 2342, license Apache-2.0, last_push_days 0,
  archived false, has_readme true, readme_chars 17082（来源 api.github.com/readme，因 raw 域名不可达）
  reproducibility "高"，reasons 6 条，score 6.5
github_repo_info("not-a-github-link") → {"error":"无法从 URL 解析 owner/repo…","degraded":true,"reproducibility":"未知"}
```

`tools/definitions.py:103 t_github_search` 已改为调用它：新增
`forks/last_push_days/archived/has_readme/readme_chars/reproducibility(高/中/低)/reasons`，
同时**保留旧字段** `stars/license/open_issues/pushed_at/repro_score(A/B/C)` 以兼容既有调用方。

---

## T7 统一 LLM 调用入口

`graph.py:21` 新增 `from core.llm import call_llm_with_retry`；新增统一入口
`_llm_text(ctx, prompt_or_messages, temperature=LLM_TEMPERATURE)`（`graph.py:39`）与 `_llm_ok()`（`:63`）。

```python
def _llm_text(ctx, prompt_or_messages, temperature=LLM_TEMPERATURE):
    err = "已跳过 ctx.llm（PREFER_CTX_LLM=False）"
    if PREFER_CTX_LLM:                       # graph.py:31，默认 True
        try:
            llm = getattr(ctx, "llm", None)
            if llm is not None and getattr(llm, "available", False):
                return (llm.chat(messages, temperature=temperature).content or "").strip()
            err = "ctx.llm 不可用（未配置 API Key 或 provider 未就绪）"
        except Exception as e:
            err = f"ctx.llm 调用异常：{type(e).__name__}: {str(e)[:100]}"
    try:
        resp = call_llm_with_retry(messages, temperature=temperature)   # core/llm.py:129
        return (getattr(resp.choices[0].message, "content", "") or "").strip()
    except Exception as e:
        return f"【LLM 调用失败：{err}；降级调用 core.llm.call_llm_with_retry 也失败：…】"
```

- 替换的裸调用（grep 已确认 5 个文件内 `ctx.llm.chat / .stream / .json_chat` **零残留**）：
  - `graph.py:660` 与 `:701` generate_review（原 `:349 ctx.llm.chat`）
  - `graph.py:909` explore_future（原 `:444 ctx.llm.chat`）
- **为什么保留 `ctx.llm` 优先**：`ctx.llm` 是用户在侧边栏选的 provider/key（`services/state.py:97-113`），
  直接用 `call_llm_with_retry` 会读 config 默认 provider，可能覆盖用户选择。故做成常量开关
  `PREFER_CTX_LLM`（`graph.py:32`），置 False 即 100% 走 `call_llm_with_retry`。
  两条路径最终都由 `core/llm.py` 统一重试 + 落盘 + 伪响应兜底，不会抛异常。
- 另 4 个文件（`extern.py` / `citation_quality.py` / `track.py` / `tools/definitions.py`）**原本就没有裸 LLM 调用**，
  本次也未新增。

---

## 自验结果

```
$ C:/Users/Administrator/anaconda3/python.exe -m py_compile \
    services/graph.py services/extern.py services/citation_quality.py services/track.py tools/definitions.py
py_compile(anaconda3 3.11): PASS          # 无输出即通过，无语法错误

$ C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile <同上>
py_compile(ai_research env): PASS
```

运行时冒烟（均在 `envs/ai_research` 下、不启 streamlit）：
1. `citation_quality`：真实被引命中 2/2（26917、33959），编造条目返回 None 且原因可查。
2. `graph`：5 节点合成图上 `classify_papers` 2 参/3 参两种调用均正常；`detect_frontier` 输出带理由的排序结果；
   `review_structure` 聚出 2 个主题簇 + 时间线 + 骨干边；`generate_review` 在 LLM 不可用时输出确定性四段结构；
   `explore_future_structured` 给出 已解决/存疑/开放 三态。
3. `tools`：`build_registry()` 共 21 个工具，`track_topic` 已在册且 schema 正确；
   `t_track_topic` 真实抓取 + 检查点生效；`t_github_search` 返回真实 stars/许可证/复现性；
   `t_citation_quality` 返回真实被引与等级。

---

## 未完成项 / 未做项（诚实清单）

1. **T5 未闭环**：`track_topic` 没进 `core/router.py` 的专家工具白名单，agent 仍看不到（详见 T5 阻塞项）。
2. **UI 未联动**（都不是我的文件）：
   - `ui/page_search.py:71-77 _quality` 仍是 LLM 主观判断，未接 `assess_references`；
   - `ui/page_graph.py:22` 仍以 2 参调用 `classify_papers`，看不到 frontier 的 score/reasons；
   - `ui/page_graph.py` 综述/前沿展示无需改（返回仍是 Markdown 字符串），但"图谱结构明细"是新增段落。
3. **Semantic Scholar 实际不可用**（本网络 429），三级降级在真实运行中基本表现为
   "OpenAlex 命中 / 全失败"二选一；已保留代码与降级顺序，恢复网络后自动生效。
4. **未做 PubMed / arXiv / DuckDuckGo 的重新实测**（本次未改动这些函数）。
5. **未做端到端 PDF→图谱→综述的真实文献库跑通**：约束不允许启动 streamlit，且未构造真实 PDF 库；
   所有图算法验证用的是合成图 + stub embedding。
6. **`venue_tier` 的等级字典是人工内置的**，CCF 目录覆盖不全（主要覆盖 AI/ML/NLP/CV + 部分系统/安全/软工），
   未覆盖学科一律返回"未知"并说明，不会瞎猜。
7. **未做"按论文标题主动搜索 GitHub 仓库"**（审计 3.7 提到的第②点），本次只做了"给定 URL 查仓库真实指标"。

---

## 风险点（欢迎交叉审计，虚报会被抓）

1. **S2 基本不可用**：实测连续 429，若评审环境同样限流，"三级降级"实际是两级；若 OpenAlex 也挂了，
   引用含金量的 `real_citations` 会大量为 `None`（此时只按 venue/位置/相关性打分，最高 50 分=中含金量，不会虚高）。
2. **标题相似度阈值 0.60 是双刃剑**：过松会张冠李戴（拿别的论文被引数），过紧会漏配。
   我用「作者姓氏加成 +0.15、年份加成 +0.10」缓解，并在返回值里暴露 `matched_title/match_score` 供核查，
   但**短标题/泛标题（如 "Introduction"）仍可能误配**。
3. **OpenAlex 记录本身有噪声**：实测 "Attention Is All You Need" 在 OpenAlex 里 `publication_year=2025`
   （与常识 2017 不符），年份加成因此失效；`matched_title/venue` 我原样透出，不做修正也不隐藏。
4. **GitHub 未认证限流 60 次/小时**：演示时反复点会触发，届时返回"限流，未做推测性评级"而非假数据；
   可通过 `GITHUB_TOKEN` 环境变量提额（`extern.py:13`）。
5. **README 抓取依赖 raw 域名**：本网络 `raw.githubusercontent.com` 不通（curl exit 35），
   全部走 `api.github.com/readme` 兜底（会额外消耗 1 次 API 配额）；若两路都失败，`has_readme=false` 且不加分。
6. **frontier 的"跨学科"是向量距离近似**，项目没有学科/领域标签元数据，无法做真正的跨学科判定；
   本地库很小时（<10 篇）该维度区分度有限。
7. **frontier 外部被引只查 Top-5**：其余论文缺少 external 维度（按权重归一化处理），
   不同论文之间的 score 并非完全同维度可比，已在 reasons 中逐条注明哪些维度计入。
8. **社区发现在稀疏图上会退化**：论文少/引用边少时，`greedy_modularity` 可能把每个节点切成独立簇，
   综述退化为"逐篇罗列"但仍保持四段结构与真实数据，不会编造簇含义。
9. **embedding 依赖失败会静默降级**：`ctx.embed` 抛异常时跨学科维度跳过（`_paper_vectors` 返回 {}，
   `graph.py:268-286`），reasons 会写明"embedding 不可用，跨学科维度跳过"。
10. **py_compile 只验证语法**：未跑单元测试（仓库 `tests/` 未针对这些函数补充用例），
    逻辑正确性靠上述手工冒烟验证。
11. **向后兼容面**：`classify_papers` 增加**可选**第三参 `ctx`、`_graph()` 第 5 项语义变化、
    `edge_derivative` 内容由字符串列表变对象列表 —— 若还有其他调用方依赖旧语义需同步调整。
    `analyze()` / `build_graph()` / `generate_review()` / `explore_future()` 签名与返回类型未变。

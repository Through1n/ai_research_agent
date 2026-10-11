# Round 8 · R8 修复报告：`detect_frontier` 把错配被引数当「外部真实被引」展示并参与排序

- **修复工程师**：R8
- **独占文件**：`services/graph.py`（唯一改动，46 增 / 3 删）
- **缺陷等级**：阻断级（抗幻觉失败——「数据张冠李戴」比「没有数据」更糟）
- **环境**：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（streamlit 1.63.0 + networkx）
- **自验脚本**：`C:/tmp/r8/`（未污染 `storage/`）
- **结论**：T1 ✅ 已修｜T2 ✅ 全量排查完成（清单见 §4）｜T3 ✅ R5 修复复验通过（附 1 处残留风险）｜T4 ✅ 回归通过｜`py_compile` 零报错｜`unittest 18/18 OK`

---

## 1. 根因：`services/graph.py:408`（修复前）只检查 `isinstance(count, int)`

修复前（`git show HEAD:services/graph.py`，`detect_frontier` 起始于 `:299`）消费被引数的代码块：

```python
# services/graph.py:393-416（修复前）
    # ⑤ 外部真实被引：只对预打分 Top-K 查询，控制网络开销
    if use_external:
        try:
            from services.citation_quality import fetch_citation_count   # :396
        except Exception:
            fetch_citation_count = None                                   # :398
        if fetch_citation_count:
            for item in pre[:FRONTIER_EXTERNAL_TOPK]:                     # :400
                info = ctx.papers.get(item["paper"], {})                  # :401
                try:
                    real = fetch_citation_count(info.get("title", item["paper"]),   # :403
                                                year=_node_year(ctx, item["paper"]))
                except Exception as e:
                    real = None
                    item["reasons"].append(f"外部被引查询异常：{type(e).__name__}（不计分）")
                if real and isinstance(real.get("count"), int):           # :408  ← 根因
                    c = max(0, real["count"])                             # :409
                    ext = min(1.0, math.log10(1 + c) / 3.0)               # :410
                    item["comps"]["external"] = ext                       # :411  ← 脏数据参与计分
                    item["reasons"].append(
                        f"外部真实被引 {c} 次（{real.get('source')}，匹配《{str(real.get('matched_title'))[:50]}》"
                        f"相似度 {real.get('match_score')}）→ 外部热度得分 {ext:.2f}")   # :412-414 ← 脏数据写成确定性表述
                else:
                    item["reasons"].append("外部被引数据不可得（三级数据源均失败或未匹配），该维度不计分")  # :416
```

`:408` 的判定条件只有 `isinstance(real.get("count"), int)`，**完全没有读** 上游 `fetch_citation_count` 已经诚实写出的四个可信度标注：

| 上游标注（真实返回值） | 含义 | `:408` 是否读取 |
|---|---|---|
| `title_attribution_unconfirmed=True` | 匹配到的是**另一篇论文**，被引数不属于本篇 | ❌ 未读 |
| `low_confidence=True` | 口径不完整/疑似噪声 | ❌ 未读 |
| `source_degraded=True` | 由上游限流降级而来 | ❌ 未读 |
| `error_kind="rate_limited"` | 本次查询结果不可信 | ❌ 未读 |

于是 `:409` 把错配的 55 次取出来、`:411` 写进 `comps["external"]` 参与加权打分、`:412-414` 写成
**「外部真实被引 55 次（crossref）」** 这种确定性表述，再经 `tools/definitions.py:266`
（`"edge_derivative": [{"paper":…, "score":…, "reasons": f["reasons"]}]`）直达 UI。

**实测确认上游返回值**（`C:/tmp/r8/probe_fetch.py`，真实联网，命中磁盘缓存 0.00s）：

```json
{"count": 55, "cited_by_count": 55, "source": "crossref",
 "matched_title": "Is Attention All You Need?",
 "title_attribution_unconfirmed": true, "match_score": 0.88, "year": 2025,
 "venue": "From Human Attention to Computational Attention",
 "authors": ["Mineault Patrick"],
 "low_confidence": true, "source_degraded": true, "error_kind": "rate_limited"}
```

与评委 G 的实证完全一致：系统**已经诚实标注**，只是 `detect_frontier` 这一个消费者没读。

---

## 2. 修复方案

### 2.1 新增可信度闸门 `_external_citation_trust`（`services/graph.py:299-330`）

```python
def _external_citation_trust(real):
    """判定 ``fetch_citation_count`` 的返回值能否当作「本篇论文的真实被引数」参与计分。

    返回 ``(usable: bool, count: int|None, why: str)``。**只有同时满足**下列条件才判定为可用：
      ① 无 ``title_attribution_unconfirmed``——命中的是另一篇标题近似的论文，其被引数不属于本篇；
      ② 无 ``low_confidence`` / ``source_degraded``，且 ``error_kind`` 为空——口径不完整或由降级而来，
         数值不可当作该论文的被引总数（Crossref 级无条件置 ``low_confidence``，即属此类）；
      ③ ``count``/``cited_by_count`` 是**正整数**——None 或 0 只代表「没有可用证据」，
         绝不能写成"被引 0 次"来给无数据论文打低分（那是对无数据的惩罚，等同于编造）。

    取不到可信值时返回 usable=False，调用方必须写中性表述并把该维度按「缺失」处理
    （剩余权重归一化，不加分也不扣分）。
    """
    if not isinstance(real, dict):
        return False, None, "外部数据源未返回可用结果"
    if real.get("title_attribution_unconfirmed"):
        return False, None, (
            f"命中《{str(real.get('matched_title'))[:50]}》与查询标题词序不一致"
            f"（相似度 {real.get('match_score')}），标题归属未确认，该被引数不归属本篇")
    if real.get("low_confidence") or real.get("source_degraded") or real.get("error_kind"):
        return False, None, (
            f"{real.get('source') or '外部数据源'} 口径不完整或由降级而来"
            f"（low_confidence={real.get('low_confidence')}，source_degraded={real.get('source_degraded')}，"
            f"error_kind={real.get('error_kind') or '无'}），数值不可当作本篇被引总数")
    c = real.get("cited_by_count")
    if not isinstance(c, int):
        c = real.get("count")
    if not isinstance(c, int):
        return False, None, "返回值缺 count/cited_by_count 整数字段"
    if c <= 0:
        return False, None, "外部数据源返回 0/无记录（可能是未收录或口径限制，不能据此断定该论文零被引）"
    return True, c, ""
```

### 2.2 `detect_frontier` 消费点改为「先过闸门」（`services/graph.py:446-459`）

```python
                # 只采信「可信度标注干净 + 正整数被引」的结果；其余一律不计分且写中性表述。
                # 绝不能把错配/降级/口径不全的数字写成「外部真实被引 N 次」——那是把别的论文的
                # 含金量算到本篇头上，比"没有数据"更糟（抗幻觉失败）。
                usable, c, why = _external_citation_trust(real)          # :449
                if usable:
                    ext = min(1.0, math.log10(1 + c) / 3.0)              # :451
                    item["comps"]["external"] = ext                      # :452  仅可信时计分
                    item["reasons"].append(
                        f"外部真实被引 {c} 次（{real.get('source')}，匹配《{str(real.get('matched_title'))[:50]}》"
                        f"相似度 {real.get('match_score')}）→ 外部热度得分 {ext:.2f}")   # :453-455
                else:
                    item["reasons"].append(
                        f"外部被引数据不可用（{why}），该维度不计分、按剩余维度归一化处理"
                        f"（不加分也不扣分）")                            # :457-459
```

同步更新了维度说明文档串 `services/graph.py:341-344`。

### 2.3 设计要点（对应任务书要求）

| 任务书要求 | 落地方式 |
|---|---|
| 必须检查 `title_attribution_unconfirmed` / `low_confidence` / `source_degraded` / `error_kind` | `_external_citation_trust` 三项分支全覆盖（`:314` / `:318` / `:328`） |
| 不可信的数值绝不参与计分 | 不可信时**不写** `item["comps"]["external"]`（`:452` 不执行） |
| 不能写成"外部真实被引 N 次" | `:457` 改为中性表述 `外部被引数据不可用（原因），该维度不计分、按剩余维度归一化处理（不加分也不扣分）` |
| 该维度得中性值，不加权也不扣分 | 不设 key → 走模块既有约定「缺失分量按剩余权重归一化」（`_FRONTIER_WEIGHTS` 注释 `:34`、`out` 循环 `:465-469`），**不做零分惩罚** |
| 保留可信数据时的原有行为 | 可信时 `comps["external"]` 与文案逐字保持不变（T4 实测见 §5.1） |
| `cited_by_count` 为 None 或 0 也正确处理 | `:326` 缺字段 → 不可用；`:328` `c <= 0` → 不可用，理由写明「不能据此断定该论文零被引」（边界实测见 §5.2） |

**未做任何数值推断/补全**：拿不到就是拿不到，reasons 里只陈述"不可用 + 原因"，不写任何数字。

---

## 3. 实测对比（真跑，非描述）

### 3.1 真实联网 + 修复前（`HEAD:services/graph.py`）

命令：`python C:/tmp/r8/repro_frontier.py real`（ctx：`att.pdf`/`bert.pdf`/`resnet.pdf`，图谱 2 节点 1 边）

```
=== detect_frontier 输出（按 score 降序） ===

[1] BERT: Pre-training of Deep Bidirectional Transformers   score=65.1
    components: {"low_indegree": 1.0, "recency": 0.3, "out_degree": 1.0, "interdiscipline": 0.165, "external": 1.0}
    - 本地被引 0 次（入度低得分 1.00）
    - 发表年份 2019（时间衰减得分 0.30）
    - 出度 1（引用文献数量得分 1.00）
    - 跨学科距离 0.16（自身vs引用文献质心 0.16，引用文献两两差异 -）
    - 外部真实被引 10371 次（crossref，匹配《BERT: Pre-training of Deep Bidirectional Transform》相似度 0.794）→ 外部热度得分 1.00   ← ✗

[2] Attention Is All You Need   score=14.3
    components: {"low_indegree": 0.0, "recency": 0.1, "out_degree": 0.0, "external": 0.583}
    - 本地被引 1 次（入度低得分 0.00）
    - 发表年份 2017（时间衰减得分 0.10）
    - 出度 0（引用文献数量得分 0.00）
    - 无可计算向量邻居，跨学科维度跳过
    - 外部真实被引 55 次（crossref，匹配《Is Attention All You Need?》相似度 0.88）→ 外部热度得分 0.58   ← ✗✗ 张冠李戴

=== 是否出现「外部真实被引」确定性表述 ===
命中条目数：2
  ✗ BERT → 外部真实被引 10371 次（crossref，…相似度 0.794）→ 外部热度得分 1.00
  ✗ Attention Is All You Need → 外部真实被引 55 次（crossref，…相似度 0.88）→ 外部热度得分 0.58
```

> 注：本次复现的 BERT 侧 Crossref 返回 10371（匹配标题被截断、相似度 0.794，同样带 `title_attribution_unconfirmed=True`），
> 与评委 G 记录的 70.3 分/第 1 名在**数值**上因文献库构成不同而略有差异，**但缺陷形态完全一致**：
> BERT 排第 1、`Attention Is All You Need` 那条 "55 次" 逐字复现。
> 两条都是同一个根因：Crossref 级无条件 `low_confidence=True`，且本例两条都 `title_attribution_unconfirmed=True`。

### 3.2 真实联网 + 修复后

```
### 模式：真实网络调用 fetch_citation_count
图谱：节点 2，边 1

[1] BERT: Pre-training of Deep Bidirectional Transformers   score=60.2
    components: {"low_indegree": 1.0, "recency": 0.3, "out_degree": 1.0, "interdiscipline": 0.207}
    - 本地被引 0 次（入度低得分 1.00）
    - 发表年份 2019（时间衰减得分 0.30）
    - 出度 1（引用文献数量得分 1.00）
    - 跨学科距离 0.21（自身vs引用文献质心 0.21，引用文献两两差异 -）
    - 外部被引数据不可用（命中《BERT: Pre-training of Deep Bidirectional Transform》与查询标题词序不一致（相似度 0.794），标题归属未确认，该被引数不归属本篇），该维度不计分、按剩余维度归一化处理（不加分也不扣分）

[2] Attention Is All You Need   score=3.3
    components: {"low_indegree": 0.0, "recency": 0.1, "out_degree": 0.0}
    - 本地被引 1 次（入度低得分 0.00）
    - 发表年份 2017（时间衰减得分 0.10）
    - 出度 0（引用文献数量得分 0.00）
    - 无可计算向量邻居，跨学科维度跳过
    - 外部被引数据不可用（命中《Is Attention All You Need?》与查询标题词序不一致（相似度 0.88），标题归属未确认，该被引数不归属本篇），该维度不计分、按剩余维度归一化处理（不加分也不扣分）

=== 是否出现「外部真实被引」确定性表述 ===
命中条目数：0
```

- `components` 里 **`external` 键已消失** → 该维度不再参与加权（`:465-469` 按剩余权重归一化）。
- reasons 由「外部真实被引 55 次（crossref）」→「外部被引数据不可用（…标题归属未确认…），该维度不计分」。
- `Attention Is All You Need` 分数 14.3 → **3.3**（脏数据不再给它虚高的外部热度）。

### 3.3 同进程 A/B（不联网，mock 出限流降级 + 归属未确认的脏返回）

命令：`python C:/tmp/r8/ab_compare.py`（旧版从 `git show HEAD:services/graph.py` 导出为 `C:/tmp/r8/_graph_old.py`，同一 ctx、同一 mock 数据）

```
############ 修复前（HEAD:services/graph.py） ############
[1] BERT: Pre-training of Deep Bidirectional Transformers   score=59.4
    components={"low_indegree": 1.0, "recency": 0.3, "out_degree": 1.0, "interdiscipline": 0.186, "external": 0.583}
    - 外部真实被引 55 次（crossref，匹配《Is Attention All You Need?》相似度 0.88）→ 外部热度得分 0.58   ← ✗
[2] Attention Is All You Need   score=14.3
    components={"low_indegree": 0.0, "recency": 0.1, "out_degree": 0.0, "external": 0.583}
    - 外部真实被引 55 次（crossref，匹配《Is Attention All You Need?》相似度 0.88）→ 外部热度得分 0.58   ← ✗

############ 修复后（当前 services/graph.py） ############
[1] BERT: Pre-training of Deep Bidirectional Transformers   score=59.6
    components={"low_indegree": 1.0, "recency": 0.3, "out_degree": 1.0, "interdiscipline": 0.186}
    - 外部被引数据不可用（命中《Is Attention All You Need?》与查询标题词序不一致（相似度 0.88），标题归属未确认，该被引数不归属本篇），该维度不计分、按剩余维度归一化处理（不加分也不扣分）
[2] Attention Is All You Need   score=3.3
    components={"low_indegree": 0.0, "recency": 0.1, "out_degree": 0.0}
    - 外部被引数据不可用（命中《Is Attention All You Need?》与查询标题词序不一致（相似度 0.88），标题归属未确认，该被引数不归属本篇），该维度不计分、按剩余维度归一化处理（不加分也不扣分）
```

> BERT 分数 59.4 → 59.6 微升：因为脏的 `external=0.583` 低于它其余维度的加权均值，剔除后按剩余权重归一化反而略升。
> 这是**中性化**的正确表现——既不因脏数据加分，也不因"没有数据"扣分。

---

## 4. T2：全项目 `fetch_citation_count` 消费者排查清单

排查方式：`grep -rn "citation_quality|assess_reference|real_citations|score_references|matched_title|cited_by_count|外部真实被引" --include=*.py .`（排除 `dist/` 与 `services/citation_quality.py` 自身）。

### 4.1 `services/graph.py` 内部（我的独占文件）

| # | 位置 | 消费方式 | 修复前是否读可信度标注 | 风险 | 处置 |
|---|---|---|---|---|---|
| 1 | `services/graph.py:403`（旧 `:408-416`）→ 现 `:441-459` | `detect_frontier` 维度⑤，写 reasons + 参与打分 | ❌ **未读** | **阻断** | ✅ **已修**（`_external_citation_trust` 闸门 `:299` + 消费点 `:449`） |
| 2 | `services/graph.py:261` | `classify_papers` 调 `detect_frontier(use_external=False)` | — | 无（该路径不查外部） | 无需改 |
| 3 | `services/graph.py` 其余 | 无任何其它 `fetch_citation_count` 调用（grep 确认仅 `:434`/`:441` 两处引用） | — | 无 | — |

### 4.2 我**不能改**的文件（逐条列出，供 owner 修复）

| # | 文件:行号 | 消费点 | 是否读可信度标注 | 风险等级 | 说明 |
|---|---|---|---|---|---|
| 4 | `tools/definitions.py:266-267` | `t_build_graph` → `"edge_derivative": [{"paper","score","reasons": f["reasons"]}]` | ❌ 未读（纯透传） | **中→已由上游消除** | 本缺陷到 UI 的「最后一公里」。现在源端 `reasons` 不再产生"外部真实被引 N 次"的确定性表述，危害已消除；但**它本身没有任何过滤**，建议后续在此处也加一道 `title_attribution_unconfirmed/low_confidence` 的白名单式断言（防御性双保险） |
| 5 | `tools/definitions.py:164` | `t_citation_quality` → `assess_reference(ref, use_network=True)` | ❌ 未读 | **低** | 依赖 R5 把 `real_citations` 置 None（已验证生效，见 §5）；`if a.get("real_citations") is not None: real_hits += 1`（`:168`）因此不会误计 |
| 6 | `tools/definitions.py:171` | `assessed[].reasons = a.get("reasons", [])[:4]` | ❌ 未读 | **低（但脆弱）** | 只取前 4 条 reasons。当前排序下第 4 条恰为「很可能不是同一篇论文」（实测 §5），**运气好**；一旦 `citation_quality.py` 调整 reasons 顺序，关键告警就会被截掉。建议改为按关键词筛选保留告警条 |
| 7 | `tools/definitions.py:181` | `"real_citation_hits": real_hits` | ❌ 未读 | 低 | 统计口径依赖 #5 的 None 化，正确 |
| 8 | `services/citation_quality.py:1003-1055` | `assess_reference` 自身消费 `fetch_citation_count` 返回值 | ✅ **已读**（`:1018 error_kind`、`:1020 title_attribution_unconfirmed`、`:1050 low_confidence`） | **低**（R5 已修，见 §5） | 残留缺口见 §5.3 |
| 9 | `services/citation_quality.py:1050-1054` | `low_confidence` 分支（**仅**在 `title_attribution_unconfirmed=False` 的 `else` 分支里才置位） | ⚠️ 半读 | **中** | 归属未确认时走 `:1020-1031`，**不会** 置 `evidence["low_confidence"]`，导致返回顶层 `low_confidence=False`（实测 False）。下游若只看顶层 `low_confidence`（如 `services/writing.py:856` 的 `if not res["low_confidence"]` 系列）就会**漏判**。建议 R5/owner 把 `low_confidence` 置位移到 `if real:` 公共分支 |
| 10 | `services/citation_quality.py:1004` | `cnt = real["count"]` | ✅ 配合 `:1031 cnt = None` | 低 | — |
| 11 | `services/citation_quality.py:1203-1205` | `score_references`（离线规则版，不联网） | — | 无 | 不消费外部被引 |
| 12 | `scripts/_verify_live_api.py:454-476` | 验收脚本 | ✅ **完整读**（`cited_by_count`/`source`/`error_kind`/`title_attribution_unconfirmed`/`source_degraded`/`low_confidence`） | 无 | 这是判官脚本，应保持 |
| 13 | `ui/page_search.py` | — | — | **无风险（不存在该消费者）** | 任务书假设 `ui/page_search.py` 存在消费者；实测 grep `citation|被引|assess|reference` 仅命中第 18 行的检索关键词字典，**没有任何 `fetch_citation_count` / `real_citations` 消费**。此条为误报，记录备查 |

**结论**：全项目**活跃**的 `fetch_citation_count` 返回値消费点共 3 处（`services/graph.py:441`、`services/citation_quality.py:1003`、`scripts/_verify_live_api.py:455`），其中 1 处是验收脚本；业务侧 2 处中，`graph.py` 本轮已修，`citation_quality.py` 由 R5 已修。UI 侧均为透传，无独立消费。

---

## 5. T3：R5 `assess_reference` 修复的**独立复验**

脚本：`C:/tmp/r8/verify_r5.py`，真实联网，条目 `{"title":"Attention Is All You Need","authors":"Ashish Vaswani et al.","year":2017,"venue":"Advances in Neural Information Processing Systems"}`。

```
=== 先确认 fetch_citation_count 原始数据（数据层未隐瞒）===
count=55 source=crossref matched='Is Attention All You Need?' unconfirmed=True low_conf=True degraded=True error_kind=rate_limited

=== assess_reference(entry) ===
real_citations = None   (期望 None)
level = 低含金量  score = 30.0
low_confidence(top) = False
evidence = {
  "citation_source": "crossref",
  "matched_title": "Is Attention All You Need?",
  "match_score": 0.88,
  "citation_error_kind": "rate_limited",
  "title_attribution_unconfirmed": true,
  "raw_candidate": {"count": 55, "source": "crossref", "matched_title": "Is Attention All You Need?"},
  "position": "无正文",
  "relevance": null
}
reasons:
  - ⚠️ 低置信：Crossref 仅统计其注册范围内的引用…，数值偏低属正常，**不可当作该论文的被引总数**（…）
  - ⚠️ 低置信：匹配到的《Is Attention All You Need?》与查询标题**词序不一致**（相似度 0.88），很可能不是同一篇论文，其被引数不可归属给查询标题（…）
  - ⚠️ 低置信：上游数据源本次不可用（数据源限流 HTTP 429），已降级到 crossref；…数值仅供参考（…）
  - ⚠️ 匹配到的《Is Attention All You Need?》经词序校验很可能**不是同一篇论文**，其被引数 55 **不计入本篇**；真实被引维度不计分、不猜测
  - 发表venue「Advances in Neural Information Processing Systems」评级 A类：…（+30）
  - 引用位置：未提供引用方正文，跳过引用位置判定（不扣分）
  - 主题相关性：无正文或未抽到有效标题词，跳过该维度

=== 判定 ===
real_citations 为 None : True
reasons 含『归属未确认/不是同一篇』: True
```

### 5.3 R5 修复的**残留缺口**（记录，不改 `citation_quality.py`）

1. **顶层 `low_confidence` 未置位（风险：中）**：实测 `assess_reference(...)["low_confidence"] = False`。
   原因：`citation_quality.py:1050` 的 `if real.get("low_confidence")` 位于 **`:1032` 的 `else` 分支内**，
   归属未确认时走 `:1020-1031` 直接 `cnt = None`，**跳过**了 `:1050`，故 `evidence["low_confidence"]` 从未被置 True，
   而返回值的 `"low_confidence": bool(evidence.get("low_confidence"))`（`:1094`）因此为 False。
   任何只看顶层 `low_confidence` 的下游会漏判这条最脏的数据。
2. **`evidence["raw_candidate"]` 仍携带 `count: 55`**：数据层保留是对的（可审计），但若有下游直接渲染 `raw_candidate.count` 就会重现本缺陷。建议 owner 明确「`raw_candidate` 仅供调试，禁止展示」。
3. `assess_reference` 的 `score` 未因"拿不到被引"被惩罚（只有 venue +30），符合"不计分、不猜测"，✅ 行为正确。

---

## 6. T4：回归验证（未破坏原有能力）

### 6.1 网络正常 / 可信数据：仍正常给出带真实被引数的 reasons

脚本：`python C:/tmp/r8/repro_frontier.py mock_trust`（mock：openalex、count=42000、`title_attribution_unconfirmed=False`、`low_confidence=False`、`error_kind=""`）

```
### 模式：mock 可信数据（count=42000, low_confidence=False）
[1] BERT: Pre-training of Deep Bidirectional Transformers   score=66.5
    components: {"low_indegree": 1.0, "recency": 0.3, "out_degree": 1.0, "interdiscipline": 0.221, "external": 1.0}
    - 外部真实被引 42000 次（openalex，匹配《BERT: Pre-training of Deep Bidirectional Transform》相似度 1.0）→ 外部热度得分 1.00   ← ✅ 保留原行为

[2] Attention Is All You Need   score=22.7
    components: {"low_indegree": 0.0, "recency": 0.1, "out_degree": 0.0, "external": 1.0}
    - 外部真实被引 42000 次（openalex，匹配《Attention Is All You Need》相似度 1.0）→ 外部热度得分 1.00   ← ✅
```

`external` 维度照常进入 `components`、照常参与加权、文案逐字与修复前一致。

### 6.2 无外部数据 / `count=0`：中性，不被惩罚

脚本：`python C:/tmp/r8/neutral_check.py`

```
=== fetch 返回 None（三级源全失败） ===
  BERT … score=60.3 components={'low_indegree':1.0,'recency':0.3,'out_degree':1.0,'interdiscipline':0.211}
     - 外部被引数据不可用（外部数据源未返回可用结果），该维度不计分、按剩余维度归一化处理（不加分也不扣分）
  Attention Is All You Need … score=3.3
     - 外部被引数据不可用（外部数据源未返回可用结果），该维度不计分、按剩余维度归一化处理（不加分也不扣分）

=== 可信源但 count=0 ===
  BERT … score=60.3
     - 外部被引数据不可用（外部数据源返回 0/无记录（可能是未收录或口径限制，不能据此断定该论文零被引）），该维度不计分、…
  Attention Is All You Need … score=3.3
  旧逻辑（external=0 计分）会得到 score=51.3  →  修复前后差值 +9.0
  旧逻辑（external=0 计分）会得到 score=2.7   →  修复前后差值 +0.6

=== 返回缺 count/cited_by_count 字段 ===
  BERT … score=60.3
     - 外部被引数据不可用（返回值缺 count/cited_by_count 整数字段），该维度不计分、…
```

关键边界点：修复前 `count=0` 会走 `:409-411` 得到 `external=0.0` 并被计入加权，实测把 BERT 从 60.3 **压到 51.3（-9.0）**——
正是任务书说的「把 0 当成确实被引 0 次而给低分」的惩罚。修复后该维度缺失、按剩余权重归一化，回到 60.3（中性）。

---

## 7. 自验结果

```
$ C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m py_compile services/graph.py
py_compile OK            （零报错）

$ C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m unittest discover -s tests -v
...
Ran 18 tests in 7.975s
OK                       （18/18 全通过）
```

改动范围（`git status --short`）：仅 `M services/graph.py`；`services/library.py`、`services/writing.py`、
`ui/page_writing.py` 的 M 状态是其他 agent 的既有改动，本轮未触碰。
自验脚本全部落在 `C:/tmp/r8/`（`_ctx.py` / `probe_fetch.py` / `repro_frontier.py` / `ab_compare.py` /
`neutral_check.py` / `verify_r5.py` / `_graph_old.py`），未写 `storage/`。

---

## 8. 风险点与遗留建议

1. **【P2 · 语义倒置，未修，需 owner 决策】** `external` 分量 `ext = log10(1+c)/3` 表示「外部热度」，
   但它被当作**正向**分量加进「前沿度」分数（`services/graph.py:451` + `:465-469`）。
   即：**被引越多 → 前沿分越高**，语义上与"前沿/边缘"相反（BERT 拿到 external=1.0 反而被推上第 1 名，
   评委 G 记录的 70.3 分/第 1 名即由此放大）。本轮严格限定在"脏数据入口"范围内，
   **未改公式**（改公式会改变前沿识别的整体排序语义，超出 T1 授权）。建议 owner 单独评估：
   改为 `1 - log10(1+c)/3`（热度越高越不前沿）或把该分量用作**负向证据**。
2. **【收紧的副作用】** Crossref 级无条件 `low_confidence=True`（`citation_quality.py:683`），
   因此在 OpenAlex/S2 被限流的当前环境下，**外部被引维度将恒定不可用**（reasons 明示原因）。
   这是刻意选择：`citation_quality.py` 自己的文档串就写明 Crossref 值「不可当作该论文的被引总数」，
   把它计进分数等同于采信错配数字。网络恢复后 OpenAlex 真值会重新参与计分（§6.1 已验证通路完好）。
3. **【下游仍无闸门】** `tools/definitions.py:266` 与 `:171` 都是无过滤透传（§4 表 #4/#6）。
   本轮从源端消除了危害，但**纵深防御缺失**：任何未来新增的 reasons 文案若再写"真实被引 N 次"，UI 仍会照单展示。
   建议后续在 `tools/definitions.py` 出口加一道统一的可信度白名单断言。
4. **【reasons 截断脆弱】** `tools/definitions.py:171` 的 `[:4]` 依赖 reasons 顺序，见 §4 表 #6。
5. **【缓存时效】** `fetch_citation_count` 成功结果缓存 7 天（`SUCCESS_TTL`）。
   本次复现命中磁盘缓存（0.00s）拿到的是限流期间的脏结果；网络恢复后需等缓存过期或清理才能刷到真值。
   这属于上游既有设计，本轮未改（非独占文件）。

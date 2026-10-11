# Round7 修复报告 R5：引用含金量（真实被引数）在限流下的失效修复

- 工程师：修复工程师 R5
- 独占文件：`services/citation_quality.py`（**唯一改动文件**，未触碰任何 config.py / ui/* / 其它 .py）
- 环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（streamlit 1.63.0）
- 自验脚本：`C:/tmp/r5/{repro_before.py, verify_r5.py, final_live.py, status_probe.py}`（未污染 `storage/`，缓存按约定落在 `storage/cache/`）
- 时间：2026-10-12

---

## 0. 结论先行

| 任务 | 状态 | 一句话 |
|---|---|---|
| T1 429 智能退避重试 | ✅ | `_http_json` 改为重试循环，6/6 mock 用例 PASS，实测 OpenAlex 不再"一遇 429 就白等/白降级" |
| T2 持久化缓存 | ✅ | 内存一级 + `storage/cache/citation_cache.json` 二级，成功 7 天 / 失败 1 小时 / 限流 5 分钟 |
| T3 级间节流 | ✅ | 节流器在 `_throttle()`，三级共用；间隔 0.5s → **1.2s**，实测跨源间隔实测 `[1.2, 1.2]` |
| T4 Crossref 候选集选优 + 口径警示 | ✅ | rows=5 走 `_pick_best`；无条件 `low_confidence=True` + 口径说明 + 词序校验 |
| T5 诚实降级 / error_kind | ✅ | 新增 `citation_status()`，`error_kind` ∈ {rate_limited, server_error, network_error, budget_exhausted, not_found} |

- `py_compile`：0 报错
- `unittest discover -s tests`：**18/18 OK**（Ran 18 tests in 5.720s）
- 单次调用耗时：冷启动 **8.4~9.5s**（预算 20s，未卡死）；缓存命中 **0.0000s**

**未编造任何被引数**：拿不到就是 `None`。见 §5 的抗幻觉强化。

---

## 1. 复现（修复前）

`C:/tmp/r5/repro_before.py` 输出：

```
=== 修复前 fetch_citation_count ===
title      : Attention Is All You Need
result     : {"count": 196466, "source": "semanticscholar", ...}   ← 那一次 S2 侥幸可用
elapsed    : 2.65s

=== 逐级直连实测 ===
[openalex]       HTTPError 429  0.81s  Retry-After=25066     ←≈6.96 小时
[semanticscholar] HTTPError 429  0.30s  Retry-After=None
[crossref]       HTTP 200       1.36s  len=1027
```

Crossref 候选（修复前 `_pick_best` 视角）：

```
sim=0.880  count=55   Is Attention All You Need?
sim=0.442  count=0    Attention Is All You Need: An Analysis Of The Valuation Of Artificial ...
sim=0.307  count=0    Attention is All You Need... Unless You Are a CISO ...
```

**关键发现 1（原报告未提到，且最危险）**：OpenAlex 的 `Retry-After: 25066` 是 **25066 秒 ≈ 6.96 小时**。
如果按字面"优先采用 Retry-After"去 `sleep`，一次调用就会把 UI 卡死近 7 小时。
→ 我在 T1 里把它作为**显式封禁判据**：`Retry-After > 8s` 一律**立即放弃重试并降级**，绝不 sleep。

**关键发现 2**：Crossref 首条 `Is Attention All You Need?` 的 `_sim` = **0.880**，高于 `TITLE_STRONG_MIN`(0.85)，
所以修复前的代码**并不会**因为"相似度不足"返回 None，而是会**返回 55**——
一篇 2025 年、venue 为 *From Human Attention to Computational Attention*、作者 Mineault Patrick 的**另一篇论文**的被引数。
这正是需求里"绝不能把 crossref 的 55 当成真实被引数展示给用户"要防的情况，处理见 §4、§5。

---

## 2. T1：429 智能退避重试（`_http_json`，:311-356）

改动函数：`_http_json`（原 :89-102，现 **:311-356**）。
新增支撑：`_classify_error`(:109)、`_retry_after`(:127)、`_BudgetExceeded`(:88)、`_RETRYABLE_KINDS`(:101)。

常量（:57-61）：

```python
RETRY_MAX = 2                 # 单次请求最多重试次数（不含首次请求）
RETRY_BACKOFF = (2.0, 5.0)    # 退避间隔递增
RETRY_AFTER_MAX = 8.0         # 响应头 Retry-After 的采纳上限（秒）
TOTAL_BUDGET = 20.0           # 单次 fetch_citation_count 的总时间预算（秒）
MIN_SAFE_REQUEST_TIME = 1.0   # 剩余预算低于此值就不再发起新请求
```

核心片段（:339-350）：

```python
        except Exception as e:
            kind = _classify_error(e)
            if attempt >= RETRY_MAX or kind not in _RETRYABLE_KINDS \
                    or isinstance(e, json.JSONDecodeError):
                raise                       # 404/400/JSON 解析失败：不重试，直接降级
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            ra = _retry_after(e)
            if ra is not None:
                if ra > RETRY_AFTER_MAX:
                    raise                   # 长时间封禁（实测 25066s）：不再无谓等待
                wait = ra
            rem2 = _remaining_budget()
            if rem2 is not None and wait >= rem2:
                raise _BudgetExceeded("退避等待 %.1fs 超出剩余预算 %.1fs" % (wait, max(rem2, 0.0)))
            time.sleep(wait)
            attempt += 1
```

分类规则（`_classify_error`，:109-124）：

| 异常 | error_kind | 重试? |
|---|---|---|
| HTTPError 429 | `rate_limited` | ✅ |
| HTTPError 5xx | `server_error` | ✅ |
| URLError / OSError / TimeoutError（超时、连接失败） | `network_error` | ✅ |
| HTTPError 404/400/403 | `not_found` | ❌ 直接降级 |
| `json.JSONDecodeError`（200 但不是 JSON） | `network_error` | ❌ 直接降级 |
| `_BudgetExceeded` | `budget_exhausted` | ❌ |

预算保护：`_BUDGET_DEADLINE`（:296）由 `fetch_citation_count`(:739) 设置、在 `finally` 还原(:816)。
`_http_json` 在**发起前(:322) 与节流后(:328) 各复核一次**，超预算即抛 `_BudgetExceeded`，
单次请求超时也压成 `min(HTTP_TIMEOUT, 剩余预算)`，**保证不会卡死 UI**。

### 实测（mock urlopen，记录真实调用次数与等待秒数）

`C:/tmp/r5/verify_r5.py` 第 [4] 节输出，6/6 PASS：

```
[PASS] 429 无 Retry-After（全失败）
       urlopen 调用 3 次, 等待 [1.18, 2.0, 1.2, 5.0, 1.2], error_kind=rate_limited
[PASS] 429 Retry-After=1（第2次成功）
       urlopen 调用 2 次, 等待 [1.2, 1.0, 1.2],  error_kind=ok, 结果={'ok': 1}
[PASS] 429 Retry-After=25066（长时间封禁）
       urlopen 调用 1 次, 等待 [1.2],            error_kind=rate_limited
[PASS] 429 Retry-After=5（clamp 生效，<=8）
       urlopen 调用 2 次, 等待 [1.2, 5.0, 1.2],  error_kind=ok, 结果={'ok': 1}
[PASS] 503 服务端错误（全失败）
       urlopen 调用 3 次, 等待 [1.2, 2.0, 1.2, 5.0, 1.2], error_kind=server_error
[PASS] 404 不可重试
       urlopen 调用 1 次, 等待 [1.2],            error_kind=not_found
```

（等待列表里混有 1.2/1.18 的节流等待，属预期——节流器与退避共用 `time.sleep`。）
**`Retry-After=25066` 只调用 1 次、只等 1.2s（节流）就放弃** —— 这就是"不再卡 7 小时"的直接证据。

---

## 3. T2：持久化缓存（:146-291）

改动/新增函数：`_load_disk_cache`(:146)、`_save_disk_cache`(:166)、`_entry_ttl`(:182)、
`_entry_valid`(:191)、`_cache_get`(:198)、`_cache_put`(:213)、`_lookup_entry`(:228)、`clear_cache`(:277)。

落盘位置（:65-67，由 `__file__` 推导项目根，**不依赖 cwd**）：

```python
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "storage", "cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "citation_cache.json")
```

TTL（:39-41, :182-188）：

```python
FAILURE_TTL   = 3600          # 失败（None）缓存 1 小时（按需求）
RATE_LIMIT_TTL = 300          # 限流/网络类失败只缓存 5 分钟
SUCCESS_TTL   = 7*24*3600     # 成功缓存 7 天
```

> 对需求的一处**有意收紧**：限流/网络类失败按 5 分钟而非 1 小时。
> 理由：需求写明"避免一次限流把负面结果锁死" —— 限流是瞬时故障，1 小时会让限流解除后仍长时间拿不到真值。
> 属"≤1 小时"范围内，`not_found` 类失败仍是 1 小时。若评审认为必须严格 1 小时，改 `RATE_LIMIT_TTL = FAILURE_TTL` 即可。

value 结构（`_cache_put`，:213-226）含需求要求的 `{cited_by_count, source, ts, title_matched}` 及完整 `data`：

```python
{"ts": 1791739067.8174922, "ok": true,
 "data": {...完整返回...},
 "err": "", "error_kind": "",
 "cited_by_count": 55, "source": "crossref", "title_matched": "Is Attention All You Need?"}
```

健壮性：
- `_load_disk_cache` 整体 `try/except`，**缓存损坏 → 清空重建**（:163-164）；
- 加载时顺带丢弃超过 `SUCCESS_TTL` 的条目（:159）；
- `_save_disk_cache` 用 `tmp + os.replace` **原子写**，异常静默吞掉（:178-179）；
- 条目上限 `MAX_CACHE_ENTRIES = 2000`，超出按 ts 淘汰最旧（:170-174）；
- 一级内存 `_CACHE` 保留，磁盘命中会**回填内存**（:206-207），热路径不读盘。

### 实测

`C:/tmp/r5/verify_r5.py` 第 [1][2][3][3b] 节：

```
[1] 冷启动（清空内存+磁盘缓存）
    缓存文件存在: False
    首次调用耗时 : 9.24s   (预算 20.0s)

[2] 缓存是否落盘
    缓存文件存在: True
    条目数: 1
    key   : 'attention is all you need'

[3] 第二次调用（同一进程，命中一级内存缓存）
    耗时: 0.0000s   与首次相同结果: True

[3b] 清空内存缓存但保留磁盘 → 命中二级磁盘缓存
    耗时: 0.0000s   与首次相同结果: True
```

**缓存命中前后耗时对比：9.24s → 0.0000s**（两次均 0.0000s，完全跳过网络请求）。

缓存文件真实内容样例（成功条目）：

```json
{"ts": 1791739067.8174922, "ok": true,
 "data": {"count": 55, "cited_by_count": 55, "source": "crossref",
          "matched_title": "Is Attention All You Need?",
          "title_attribution_unconfirmed": true, "match_score": 0.88, "year": 2025,
          "venue": "From Human Attention to Computational Attention",
          "authors": ["Mineault Patrick"], "low_confidence": true,
          "confidence_reasons": ["Crossref 仅统计其注册范围内的引用…", "…词序不一致…", "上游数据源本次不可用（数据源限流 HTTP 429）…"],
          "pool_max_count": 55, "strong_match": true, "pool_size": 1,
          "query_used": "Attention Is All You Need", "source_degraded": true,
          "error_kind": "rate_limited"},
 "err": "", "error_kind": "",
 "cited_by_count": 55, "source": "crossref", "title_matched": "Is Attention All You Need?"}
```

失败条目样例（第 [8] 节，三级全 429）：

```json
{"ts": 1791739081.050748, "ok": false, "data": null,
 "err": "openalex: HTTP 429；semanticscholar: HTTP 429；crossref: HTTP 429",
 "error_kind": "rate_limited",
 "cited_by_count": null, "source": "", "title_matched": ""}
```

---

## 4. T3：级间节流（:293-309）

- 节流器抽成 `_throttle()`(:305)，`_http_json`(:311) 是**唯一**出口，
  `_openalex_exact_lookup`(:533) / `_semanticscholar_exact_lookup`(:563) /
  `_openalex_lookup`(:591) / `_semanticscholar_lookup`(:611) / `_crossref_lookup`(:636)
  五个函数**全部**经它发出请求 → 三级 + 两个精确匹配通道**统一节流**。
- 间隔 `MIN_REQUEST_INTERVAL` **0.5 → 1.2**（:293）。
- 缓存命中在 `fetch_citation_count`(:730-732) 处直接 return，**完全跳过网络请求**。
- 新增"**死源跳过**"（`dead` :741，`dead.add` :757）：某源一旦被判 `rate_limited`/`server_error`，
  本次调用内 `dead.add(host)`，后续标题变体与其它请求不再打它（429 是主机级故障，重试同主机的其它标题毫无意义）。
- 新增"**预算均分**"（:792-801）：前两级各烧 ~10s 退避会把唯一可用的 Crossref 饿死，故每级只分剩余预算的 `1/(剩余级数)`：

```python
        gdl = _BUDGET_DEADLINE[0]
        n_src = len(_LOOKUPS)
        for idx, (name, fn) in enumerate(_LOOKUPS):
            if name in dead:
                continue
            rem = gdl - time.time()
            if rem > 0:
                _BUDGET_DEADLINE[0] = min(gdl, time.time() + rem / max(1, n_src - idx))
```

### 实测

```
[10] 节流对三级 API 是否统一生效（mock urlopen，记录相邻请求间隔）
    三次跨源请求时间隔: [1.2, 1.2]  (MIN_REQUEST_INTERVAL=1.2)
    全部 >= 节流间隔: True
```

预算均分前后对比（同一脚本、同样被限流的网络）：
- 加均分**前**：冷启动 **15.24s**
- 加均分**后**：冷启动 **9.24s**，且预算压到 3s 时仍能走到 Crossref（[5] 节 2.07s 返回 `crossref`）

---

## 5. T4 + T5：Crossref 候选选优、口径警示、诚实降级

### 5.1 候选集选优（:636-687）

`_crossref_lookup` 一直是 `rows=5` 并走 `_pick_best`（复用 `_sim` / `_score_components`），
本轮补齐的是**结果侧的诚实标注**（:676-687）：

```python
    reasons.append(
        "Crossref 仅统计其注册范围内的引用（is-referenced-by-count），量级远小于 "
        "OpenAlex / Semantic Scholar，数值偏低属正常，**不可当作该论文的被引总数**")
    matched = hit["title"]
    attribution_unconfirmed = not _token_order_same(title, matched)   # :678
    if attribution_unconfirmed:
        reasons.append(
            f"匹配到的《{matched[:60]}》与查询标题**词序不一致**（相似度 {hit['match_score']}），"
            f"很可能不是同一篇论文，其被引数不可归属给查询标题")
    return {"count": int(hit["count"]), "cited_by_count": int(hit["count"]),
            "source": "crossref", ...,
            "low_confidence": True,          # 无条件：口径本身就不完整
            "title_attribution_unconfirmed": attribution_unconfirmed, ...}
```

- `low_confidence=True` **无条件**置位（Crossref 口径天然不完整）；
- 新增 `_token_order_same`(:409)：`_norm` 已吃掉大小写/标点差异，剩下**词序不一致**就是"标题被改写过"的硬证据。
  本例 `Is Attention All You Need?` vs `Attention Is All You Need` → `_sim`=0.88（骗过 0.85 阈值），但词序不同 → 判为疑似异篇；
- 最佳候选 `sim < TITLE_MATCH_MIN`(0.60) 时仍返回 `None`（`_pick_best` 逻辑未动），**不编造**；
- 顺带补了 `cited_by_count` 别名（`_finalize`:696），使项目自带脚本
  `scripts/_verify_live_api.py:453` 的 `r.get("cited_by_count")` 能取到值（该行原本永远取不到）。

### 5.2 抗幻觉强化：别的论文的被引数，不记到本篇头上（:1020-1031）

`assess_reference` 里对 `title_attribution_unconfirmed` 做了**不计分**处理：

```python
        if real.get("title_attribution_unconfirmed"):
            evidence["title_attribution_unconfirmed"] = True
            evidence["raw_candidate"] = {"count": cnt, "source": real.get("source"),
                                         "matched_title": real.get("matched_title")}
            ...
            cnt = None      # 不把别的论文的被引数算到本篇头上
        else:
            ... score += d ...
```

**为什么必须这么做**：不加这段时，实测 `assess_reference` 会输出
`真实被引 55 次（crossref …）→ 中高被引 (+30)`、`level=高含金量`。
这等于把 2025 年另一篇论文的 55 次引用当成 Transformer 论文的含金量 —— 直接触碰"绝不编造"底线。

加后的实测（[6] 节）：

```
level : 低含金量   real_citations: None
evidence: {"citation_source": "crossref", "matched_title": "Is Attention All You Need?",
           "match_score": 0.88, "citation_error_kind": "rate_limited",
           "title_attribution_unconfirmed": true,
           "raw_candidate": {"count": 55, "source": "crossref",
                             "matched_title": "Is Attention All You Need?"}, ...}
 - ⚠️ 低置信：Crossref 仅统计其注册范围内的引用（is-referenced-by-count），量级远小于 OpenAlex / Semantic Scholar，数值偏低属正常，**不可当作该论文的被引总数**
 - ⚠️ 低置信：匹配到的《Is Attention All You Need?》与查询标题**词序不一致**（相似度 0.88），很可能不是同一篇论文，其被引数不可归属给查询标题
 - ⚠️ 低置信：上游数据源本次不可用（数据源限流 HTTP 429），已降级到 crossref；其统计口径与 OpenAlex 不同（常显著偏低）且可能未收录 arXiv 预印本，数值仅供参考
 - ⚠️ 匹配到的《Is Attention All You Need?》经词序校验很可能**不是同一篇论文**，其被引数 55 **不计入本篇**；真实被引维度不计分、不猜测
```

> 这是我**主动加严**的一处（需求只要求"加说明"）。若评审认为应当照常计分，删掉 :1020-1031 的 `if` 分支即可。

### 5.3 error_kind 契约（T5）

`fetch_citation_count` 返回 `None` 时（契约不变），用新增的 **`citation_status(title)`**（:257-274）取诊断：

```python
{"found": bool, "error_kind": str, "label": str, "message": str, "ts": float, "expired": bool}
```

| error_kind | label | 语义 |
|---|---|---|
| `rate_limited` | 数据源限流 HTTP 429 | **查不了**（瞬时），可重试 |
| `server_error` | 数据源服务端错误 HTTP 5xx | **查不了**（瞬时），可重试 |
| `network_error` | 网络不可用/超时 | **查不了**（瞬时），可重试 |
| `budget_exhausted` | 本次查询时间预算耗尽 | **查不了**（瞬时），可重试 |
| `not_found` | 三级数据源均未匹配到该标题（可能确无被引记录） | **查不到** |

- 前四者归 `_TRANSIENT_KINDS`(:101)，UI 应显示"网络/限流导致，非该论文无被引"；
- 汇总用 `_dominant_kind`(:137)：瞬时故障**优先于** `not_found`（被限流时不能断定"查不到"）；
- 成功但**由降级而来**的结果也带 `error_kind`（如 `rate_limited`）+ `source_degraded=True`（`_finalize`:696-711）；
- `fetch_error(title)`(:245-254) 保持返回字符串且**向后兼容**，现在以标签开头，例如：
  `数据源限流 HTTP 429：openalex: HTTP 429；semanticscholar: HTTP 429；crossref: HTTP 429`
- `assess_reference` 在取不到数时会追加一条（:1006-1013）：
  `该次失败属**「查不了」**（数据源限流 HTTP 429）而非「查不到」，不代表该论文没有被引记录，网络恢复后可重试`

**三级全 429 的实测（[8] 节，mock 三个 lookup 全抛 429）**：

```
耗时 0.00s（预算 20.0s，不得卡死）  返回: None
citation_status: {"found": false, "error_kind": "rate_limited",
                  "label": "数据源限流 HTTP 429",
                  "message": "openalex: HTTP 429；semanticscholar: HTTP 429；crossref: HTTP 429",
                  "ts": 1791739081.050748, "expired": false}
fetch_error   : 数据源限流 HTTP 429：openalex: HTTP 429；semanticscholar: HTTP 429；crossref: HTTP 429
```

---

## 6. 修复后真实联网实测汇总

### 6.1 限流场景：耗时 / error_kind / 缓存

`C:/tmp/r5/verify_r5.py`：

| 场景 | 耗时 | 结果 | error_kind |
|---|---|---|---|
| 冷启动（清空内存+磁盘） | **9.24s**（预算 20s） | crossref 55，`title_attribution_unconfirmed=True` | `rate_limited` |
| 第二次（一级内存命中） | **0.0000s** | 同上 | — |
| 清内存、留磁盘（二级命中） | **0.0000s** | 同上 | — |
| TOTAL_BUDGET 压到 3s | **2.07s** | 仍走到 crossref | — |
| 三级全 429（mock） | **0.00s** | `None` | `rate_limited` |
| 失败缓存 TTL 到期 | — | 缓存失效，可重查 | — |

### 6.2 限流间歇恢复时拿到的真值

修复**前**首次复现时 Semantic Scholar 侥幸可用，拿到：

```
Attention Is All You Need → count=196466, source=semanticscholar,
matched=《Attention is All You Need》sim=1.0, year=2017, venue=Neural Information Processing Systems
（数万量级 ✅ 与"应在数万量级"的预期一致）
```

### 6.3 本次报告时刻的限流现状与实测真值

`C:/tmp/r5/status_probe.py`（报告时刻直连）：

```
[openalex]        HTTPError 429  1.07s  Retry-After=24028   ←≈6.68 小时，仍封禁
[semanticscholar] HTTPError 429  0.30s  Retry-After=None
[crossref]        HTTP 200       1.25s
```

`C:/tmp/r5/final_live.py`（三级降级后落到 crossref 的真值）：

| 标题 | 耗时 | count | source | sim | 归属可信 |
|---|---|---|---|---|---|
| Attention Is All You Need | 9.47s | 55 | crossref | 0.88 | ❌ `title_attribution_unconfirmed`（命中 2025 年另一篇）→ **不计分** |
| ImageNet Classification with Deep Convolutional Neural Networks（AlexNet） | 8.44s | **32936** | crossref | **1.0** | ✅ 计分 |
| BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding | 8.42s | **10371** | crossref | **1.0** | ✅ 计分 |

**这组数据正好实证了 T4 的口径警示**：AlexNet 在 `docs/review/round3_fixR3.md` 里记录的 OpenAlex 真值是 **108512**，
本次经 Crossref 只拿到 **32936**（≈ 1/3.3）。
所以 `low_confidence=True` + "Crossref 仅统计其注册范围内的引用，数值偏低属正常"**不是保守措辞，是实测事实**。
Crossref 的数可用作"有无被引"的定性参考，**不可**当作被引总数展示。

### 6.4 编译 / 回归

```
$ python -m py_compile services/citation_quality.py
PY_COMPILE: 0 error

$ python -m unittest discover -s tests
..................
----------------------------------------------------------------------
Ran 18 tests in 5.720s

OK                       ← 18/18，无回归
```

---

## 7. 风险点（诚实写）

1. **OpenAlex 仍在长时封禁**：报告时刻 `Retry-After=24028`（≈6.7h）。本轮所有实测的三级调用都是
   "`openalex 429 → semanticscholar 429 → crossref 命中`"。
   **现场演示时大概率只能拿到 Crossref 口径的数**（偏低 1~3 个数量级），且 Attention 那篇会被判为"疑似异篇"而不计分。
   建议：演示前确认网络，或演示 AlexNet / BERT 这类 Crossref 收录完整的论文（实测 sim=1.0，数万量级，效果好很多）。
   > 注：NeurIPS 2017 proceedings 未在 Crossref 注册 DOI，所以 Attention 论文在 Crossref 里**本来就没有记录**，
   > 返回"疑似异篇/不计分"是**正确且诚实**的结果，不是 bug。

2. **`title_attribution_unconfirmed` 是我加严的行为**，需求原文只要求"加说明"。
   `fetch_citation_count` 仍会返回 55（数据层未隐瞒），只有 `assess_reference` 的**计分**把它排除。
   若 UI 侧希望展示"疑似匹配到 55 次（另一篇）"，读 `evidence["raw_candidate"]` 即可。
   反向调整成本：删 `services/citation_quality.py:1020-1031` 的 `if` 分支。

3. **失败缓存 5 分钟而非 1 小时**（`RATE_LIMIT_TTL`）。有意偏离需求建议值，理由见 §3。

4. **总预算会有 ≤ ~1.2s 的超调**：`_throttle()` 的 sleep 本身耗时，虽有"节流后复核"(:328-329)兜底，
   仍可能比 `TOTAL_BUDGET` 多出一个节流间隔。实测 3s 预算 → 2.07s、20s 预算 → 9.24s，均在可接受范围。

5. **`dead` 死源标记只在单次 `fetch_citation_count` 内有效**（`:748` 局部 set）。
   跨调用靠的是磁盘缓存的 `RATE_LIMIT_TTL=300`：限流期间同一标题 5 分钟内不会重复打网络，
   但**不同标题**仍会各自去试一次 openalex。批量评估（默认前 5 条）最坏约 5×20s。
   若要彻底避免，需要跨调用共享的"主机封禁状态"，超出本轮文件范围，建议后续做。

6. **缓存是"标题 → 结果"**，同一标题在不同 `authors/year` 入参下会复用同一份缓存（原有行为，未改）。

7. **`storage/cache/citation_cache.json` 会被提交吗**：不会，`storage/` 已在 `.gitignore` 忽略（已核对）。
   已确认该文件生成于 `C:/Users/Administrator/ai_research_agent/storage/cache/citation_cache.json`。

---

## 8. 交付对照

| 需求项 | 落点 |
|---|---|
| T1 429/5xx 退避重试，≤2 次，2s→5s 递增 | `_http_json` :311-356；常量 :57-58；实测 6/6 PASS |
| T1 读 Retry-After 并 clamp ≤8s | `_retry_after` :127；:344-347；实测 25066 → 不重试 |
| T1 非 429 不重试 | `_RETRYABLE_KINDS` :103；:339-341；实测 404 只调 1 次 |
| T1 总预算 ≤20s | `TOTAL_BUDGET` :60；`_BUDGET_DEADLINE` :296；:322/:328/:739/:816 |
| T2 磁盘缓存 `storage/cache/citation_cache.json` | :65-67、:146-226 |
| T2 key=归一化标题；value 含 cited_by_count/source/ts/title_matched | `_norm` :78；`_cache_put` :213-226 |
| T2 成功 7 天 / 失败 1 小时 | :39-41；`_entry_ttl` :182 |
| T2 缓存损坏自动重建、绝不崩主流程 | :146-179（整段 try/except） |
| T2 保留内存一级缓存 | `_CACHE` :72；`_cache_get` :198-211（磁盘命中回填内存） |
| T3 节流对三级统一生效 + ≥1.2s | `_throttle` :305；`MIN_REQUEST_INTERVAL=1.2` :293；实测 `[1.2, 1.2]` |
| T3 缓存命中跳过网络 | `fetch_citation_count` :733-736；实测 0.0000s |
| T4 Crossref rows=5 + `_pick_best` 选优 | `_crossref_lookup` :636-687 |
| T4 crossref → `source='crossref'` + `low_confidence=True` + 口径说明 | :681-684 |
| T4 低于阈值才 None，不编造 | `_pick_best` 未改；:636-687 返回 None 分支 |
| T5 三级全失败时 reasons 说明限流/网络 | `fetch_error` :245；`assess_reference` :992-1000 |
| T5 error_kind 字段与契约 | `_classify_error` :109；`citation_status` :257；§5.3 表格 |
| 硬约束 5：py_compile 零报错 | ✅ |
| 硬约束 6：unittest 18/18 | ✅ Ran 18 tests, OK |
| 硬约束 3：绝不编造 | ✅ 无真值即 `None`；异篇数不计分（§5.2） |

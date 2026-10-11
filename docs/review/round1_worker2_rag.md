# 第一轮 · 考生2号（抗幻觉 RAG）修复报告

- 独占文件（本次只改这 4 个 .py）：`services/rag.py`、`core/guardrails.py`、`services/query_expansion.py`、`ui/page_qa.py`
- 未改动：`ui/common.py`（考生5）、`services/library.py`/`services/parsing.py`（考生1）、`core/llm.py`（考生6）、`config.py`、`ui/page_reading.py`
- 未启动 streamlit、未 pip install、未跑 web 服务
- 环境事实：本机 anaconda3 缺 `chromadb` / `openai` / `rank_bm25`，**`tests/test_core.py` 无法真实运行**（ImportError: rank_bm25），故另写了桩化自验脚本（见文末"自验"）

---

## T1 [P0-1] 原文出处锚点可点击跳转

### 1.1 `ui/page_qa.py:51-77` 自建增强版溯源卡片 `anchor_card()`（不 import/改写 `ui/common.py:source_card`）

```python
def anchor_card(source, page, score=0.0, para_idx=None, n=None):
    badge = (f'<a href="#p{int(page)}" style="text-decoration:none;color:#fff;display:inline-block;">'
             f'第 {int(page)} 页</a>')
    cross = (f'<a href="?p={READING_PAGE_IDX}&jump_page={int(page)}&paper={name}" target="_self" '
             f'style="color:#27518a;font-size:11.5px;">📖 打开阅读页</a>')
    return (f'<a id="p{int(page)}"></a>' + ...)   # 页内锚点落点 + 卡片
```

三处跳转：①`<a id="p{N}">` 锚点落点；②页码徽章 `href="#p{N}"` 页内跳转；③`?p=3&jump_page=N&paper=X` 跨页跳阅读页（`READING_PAGE_IDX = 3`，见 `ui/page_qa.py:24`）。
卡片内容按赛题要求补齐：编号【n】、论文名、页码、**相关度分数**、（新增）页内段号。

### 1.2 `ui/page_qa.py:80-95` `_set_jump()` —— 写结构化 session_state + query_params

```python
payload = {"source": str(source), "page": int(page),
           "para_idx": int(para_idx) if para_idx else None, "from": "qa"}
st.session_state[JUMP_KEY] = payload          # JUMP_KEY = "qa_jump_page" (page_qa.py:23)
st.query_params["jump_page"] = str(payload["page"])
st.query_params["paper"] = str(payload["source"])
st.success(f"已定位到《{...}》第 {page} 页（第 {para_idx} 段），可到📖版式阅读页查看。")
```

### 1.3 `ui/page_qa.py:98-116` `_render_source()` —— 按钮 + 折叠原文片段

- `st.button(f"📖 定位到《{source[:16]}》第 {page} 页", key=f"jump_{rid}_{i}")` → 调 `_set_jump()`
- 三栏：跳转按钮 / `相关度 x.xx｜第 k 段` / `st.expander("原文片段（前 200 字）")`（`SNIPPET_LEN = 200`，`page_qa.py:21`）
- `rid` 取自 run 的自增 `id`（`page_qa.py:204`），避免历史记录增多时 widget key 串位

### 1.4 `services/rag.py:124-158` 段落级坐标 `annotate_paragraphs(sources, ctx=None)`（不动 library.py）

```python
c["para_idx"]  = int(before) + 1     # 页内第几段（1-based）
c["para_span"] = max(int(n), 1)
```
按 `(source, page)` 把 `ctx.chunks` 分组，累加该 chunk 之前同页 chunk 的段落数得到段序号；返回的是 chunk **浅拷贝**，不污染 `ctx.chunks` / `library.json`。`library.py:378-379` 的 chunk 字段（id/source/page/page_end/text）**一个字节都没动**。
`build_context()`（`rag.py:315-325`）会把坐标写进 prompt：`[1] 来源：《X》 第5页第3段`。

### 1.5 消费方约定（**需要考生6 配合，见文末**）
`ui/page_reading.py:43-60` 目前只消费 `query_params["paper"] / ["jump_page"]`，**尚未读 `st.session_state["qa_jump_page"]`**。我已在 `_set_jump()` 里双写，所以当前跳转链路已可用（走 query_params）。

---

## T2 [P0-2] 幻觉防护闭环

### 2.1 `ui/page_qa.py:190-215` 主问答流程调用 `rag.answer()`（不再内联重写）

```python
result = rag.answer(ctx, q.strip(), use_rerank=use_rerank,
                    source_filter=source_filter,
                    compare_papers=list(compare_papers) or None)
```
说明：审计时（报告 §2.1）指出的"内联重写"在本轮开始前已被人改回 `rag.answer`（`page_qa.py` 旧版 :34 已调用）；我在此基础上把单篇过滤/多篇对比/拒答/可疑引用全部**经 rag.answer 传入与回传**，UI 不再有自己的检索或校验分支。本文件内**没有任何 `ctx.llm.chat` 直调**。

### 2.2 `core/guardrails.py:127-150` `validate_citations` 增加内容一致性校验

```python
def validate_citations(answer, n_sources, source_texts=None, threshold=SUSPICION_THRESHOLD):
    ...角标范围校验（原逻辑不变）...
    if source_texts:
        issues.extend(check_citation_support(answer, list(source_texts), threshold))
```
- `check_citation_support`（`guardrails.py:83-105`）：按句切开答案 → 每句归属其出现的【n】→ 与第 n 个来源 chunk 文本比对 → 低于阈值报 `可疑引用【n】：...重叠仅 x%（阈值 15%）`
- `_overlap`（`guardrails.py:51-65`）：**刻意为"覆盖率"而非 Jaccard**——
  ```python
  uni = len(su & ou) / len(su); bi = (len(sb & ob) / len(sb)) if sb else uni
  return 0.5 * uni + 0.5 * bi
  ```
  原因：来源 chunk 约 1200 字，其 n-gram 数是答案句的几十倍，Jaccard 会被稀释到 ~3%，**正常引用也会被判可疑**（自验实测：同一句 Jaccard=7% 被误杀，覆盖率=100% 正确通过）。
- `_anchor_tokens`（`guardrails.py:38-49`）只取英文词/数字锚点（含数字单独拆出，兼容 `110M`/`110 million`、`beta_1`/`beta 1`）；**答案句锚点 < 2 个时跳过判定**（纯中文句无法判定，宁漏报不误报）
- 向后兼容：两参调用 `validate_citations(answer, n)` 行为完全不变（`tools/definitions.py`、`core/agent.py` 等无需改）

### 2.3 `core/guardrails.py:152-...` `validate_grouped_citations()`（对比模式）

校验 `【P1-2】` 型角标（`GROUP_CITATION_RE`，`guardrails.py:25`）是否存在于 index，并复用同一套内容一致性校验。

### 2.4 `services/rag.py:410-413` 拒答闸门

```python
if top < RELEVANCE_FLOOR:      # RELEVANCE_FLOOR = 0.35 (rag.py:16)
    return _refuse(f"最高相关度 {top:.2f} 低于阈值 {RELEVANCE_FLOOR}，共 {len(sources)} 个候选片段", sources, debug)
```
`REFUSAL_TEXT = "未在文献库中找到可靠依据，为避免幻觉我不作答。"`（`guardrails.py:31`）。
分数口径 `score_sources()`（`rag.py:168-222`）：`0.5 × 向量余弦(取各变体最大值) + 0.5 × BM25 归一`，返回 0~1；向量不可用退化为 BM25 归一。
拒答时仍回传 top3 片段（`_refuse`，`rag.py:368-371`），UI 用 `st.warning` 展示并保留人工核对入口。

### 2.5 `services/rag.py:415-433` 修复环最多重跑 1 次

```python
repairs = 0
while issues and repairs < max_repair:      # max_repair = MAX_REPAIR = 1
    ... new_text = _llm_complete(ctx, repair, temperature=0.0)
    issues = check(text); repairs += 1
debug["repairs"] = repairs
```
自验实测：幻觉答案共触发 **2 次 LLM 调用**（1 次生成 + 1 次修复），不会死循环。修复后仍有问题 → `ui/page_qa.py:228-229` 用 `st.warning` 逐条列出；拒答时 `ui/page_qa.py:225-226` 用 `st.warning("🛑 ...")` 展示拒答文案。

---

## T3 [P1-10] 多篇跨文献对比（QA 页真实化）

| 改动 | 位置 |
|---|---|
| `st.multiselect` 勾选 2~5 篇（`MAX_COMPARE_PAPERS=5`） | `ui/page_qa.py:169-182` |
| 按论文分组召回 `retrieve_per_paper()` | `services/rag.py:272-310` |
| 分组上下文 `build_grouped_context()` → `[P1-1][P1-2][P2-1]` | `services/rag.py:328-342` |
| 对比 prompt（分论文陈述 + 强制 Markdown 对比表）`compare_messages()` | `services/rag.py:353-363` |
| 对比模式引用校验 `validate_grouped_citations()` | `core/guardrails.py:152-...` |

防饿死（`rag.py:286-298`）：单篇不足 `per_paper=3`（`COMPARE_PER_PAPER`，`rag.py:19`）条时，用 `sparse_search(..., source=name, allow_zero=True)` **放宽阈值**（不再要求 BM25 score>0，按分数倒序补齐）。自验实测 `{"att.pdf": 3, "bert.pdf": 3}`。

---

## T4 [P2-16] 单篇范围锁定

- `services/rag.py:225-226`：`retrieve(..., source_filter=None)`，**默认 None 行为完全不变**（`tools/definitions.py:28`、`tests/test_core.py:130`、`scripts/selftest.py:46` 三处既有调用方不受影响）
- 过滤放大召回再截断：`rag.py:233-236`（`dense_k/sparse_k = max(k, final_k*3)`）+ `rag.py:248-249` dense 结果按 source 过滤 + `sparse_search(source=...)`
- UI：`ui/page_qa.py:160-167` `st.radio("全部文献"/"单篇") + st.selectbox`；与多篇对比互斥（`:177-179`）

---

## T5 [P2-17] 查询扩展增强

`services/query_expansion.py` 重写：

| 项 | 位置 | 说明 |
|---|---|---|
| 返回 `variants: List[str]`（首元素=原查询） | `:134-166` `expand_queries()` | 旧返回值 `[原查询, 扩展串?]` 是新返回值的子集，**调用点无需改**（`rag.py:240`） |
| LLM 返回 JSON 列表（cross_lingual / synonyms 同义上位词 / abbreviations 简写展开） | `:92-131` `_llm_expand()` + `_EXPAND_PROMPT:71-77` | 解析失败降级：先 `parse_json`，再收任意字符串列表值，最后按行拆 |
| 离线兜底同义/简写 | `:65-76` `_offline_variants()` + `_ABBREV:29-45` | 12 条学术缩写展开（BERT/GPT/RNN/LLM/SOTA…），每条独立成 variant |
| `_offline_expand()` 保持返回字符串 | `:54-62` | **向后兼容 `tests/test_core.py:22,135` 的导入与断言** |
| dense 路消费 variants + RRF(k=60) | `services/rag.py:244-252` | `for v in variants[:MAX_VARIANT_DENSE]: d_rankings.append(dense_search(...))` → `rrf_fuse(d_rankings + [s_ids], final_k)` |

`debug` 新增 `dense_variant_ranks`（各变体的向量召回榜），UI 在调试区展示"dense 路消费 N 个查询变体"（`page_qa.py:124-126`）。

---

## T6 统一 LLM 调用入口

- `services/rag.py:26-44` `_llm_complete()`：`call_llm_with_retry(messages, model=..., temperature=..., api_key=ctx.llm.api_key, base_url=ctx.llm.base_url)`，**复用 ctx.llm 上已配置的 provider 选型**，不会退化成"永远用 config 的智谱配置"。`llm_rerank`（`:106`）走 `_llm_json` → 同一入口。
- `services/query_expansion.py:78-90` `_complete()`：同上。
- `core/guardrails.py`：无 LLM 调用（纯规则）。`ui/page_qa.py`：无 LLM 调用（只读 `ctx.llm.available`）。
- 已 grep 确认四个文件内**无残留 `ctx.llm.chat` / `json_chat`**；`core/llm.py` 只读未改。

---

## 未完成 / 部分完成项（诚实交代）

1. **阅读页消费 `qa_jump_page` 未落地**：我不能改 `ui/page_reading.py`，只能靠已存在的 `query_params` 链路（`page_reading.py:43-60`）。若考生6 未补读 session_state，跨页跳转仍可用，但"段落级定位（para_idx）"在阅读页不会生效——阅读页只有页码粒度。
2. **未跑真实 Streamlit / 真实 LLM**：环境缺 `chromadb`、`openai`、`rank_bm25`，无法起服务也无法真调模型。所有结论来自桩化自验 + 静态推理。
3. **拒答闸门阈值 0.35 未用真实语料标定**：默认 embedding（`chroma DefaultEmbeddingFunction`，英文 MiniLM）对中文查询的余弦偏低，可能导致**相关但中文提问的查询被误拒**。缓解：dense 分取"所有变体（含英文扩展词）的最大值"。若演示时误拒频繁，把 `rag.py:16` 调到 0.25 即可。
4. **`para_idx` 是估算值**：基于"同页前序 chunk 的段落数累加"，而 `parsing.py` 的切块有 150 字重叠，重叠段落可能被重复计数；不能保证与 PDF 视觉段落一一对应。
5. **查询扩展的 LLM 分支未真跑过**：只验证了 prompt 构造与解析降级路径（无 Key → 离线分支已验证）。
6. **多篇对比未做"每篇至少 N 条"的语义兜底**：BM25 完全无词面重合时（如纯中文问中文论文且词表未命中），`allow_zero` 会按 0 分倒序取前 N 条，可能是弱相关片段。

---

## 自验

### py_compile（硬性要求项）
```
C:/Users/Administrator/anaconda3/python.exe -m py_compile \
    services/rag.py core/guardrails.py services/query_expansion.py ui/page_qa.py
→ 零报错（退出码 0）
```

### 桩化运行时自验（临时脚本 `%TEMP%/w2_selftest.py`，未写入项目目录）
用 types.ModuleType 桩掉 `chromadb` / `openai` / `rank_bm25`，构造 FakeCtx（att.pdf + bert.pdf 文本切块），跑通 **25 项断言全部 PASS**，含：
- 查询扩展返回 List[str]、离线简写展开、`_offline_expand` 旧断言仍成立
- `retrieve` 的 debug 键齐全、`sources` 带 `score/para_idx/para_span`、`dense_variant_ranks>1`、`top_score∈[0,1]`
- `source_filter` 只回 bert.pdf；默认 None 仍跨篇
- 分组检索每篇 3 条、`[P1-1]/[P2-1]` 编号与 index 正确
- **有依据引用不报可疑（覆盖率 100%）**、无依据引用报"可疑引用 0%"、角标越界仍报、两参调用兼容
- 拒答闸门触发文案正确；修复环共 2 次 LLM 调用（上限 1 次修复）
- `tests/test_core.py` 因缺 `rank_bm25` **未能运行**（不是我改出来的问题，改前同样跑不了）

---

## 仍存在的风险点

1. **内容一致性是启发式**：只用英文/数字锚点覆盖率，判不出"表述正确但换词"的引用，也判不出"整句抄自别的来源却标了本来源"的情形；阈值 0.15 偏松（偏松=少误杀、多漏报）。
2. **可疑引用会进入修复环**：若 LLM 修不动（自验中重复输出同一句），issues 会保留并在 UI 以 `st.warning` 明示——这是设计意图（宁可明示也不静默），但演示时可能出现"带警告的答案"。
3. **`st.query_params` 双写的副作用**：写入 `jump_page/paper` 后 URL 参数会一直保留，之后每次进阅读页都会跳该页，直到用户手动改页码。
4. **session_state 体积**：`qa_runs` 存了每条 run 的 sources（含 chunk 文本副本，每条 ≤1200 字 ×6），长会话会缓慢增长；未做条数上限。
5. **对比模式角标格式依赖 LLM 遵守 prompt**：若模型仍输出【1】而不是【P1-1】，`validate_grouped_citations` 会报"未包含任何【Pn-m】角标"并触发一次修复；修复失败则以 warning 展示。
6. **`score_sources` 每次问答额外做 (1+N变体+N候选) 次本地 embedding**：对 6 条候选约 10 次短文本编码，541 页长文场景未见压力，但未做实测。

---

## 需要其他 agent 配合的跨文件约定

### 给考生6（`ui/page_reading.py`，我不可改）
- **session_state key**：`st.session_state["qa_jump_page"]`
- **结构**：
  ```python
  {"source": "1706.03762v7.pdf",   # 论文文件名，等于 ctx.files 中的元素
   "page": 5,                      # int，1-based 页码
   "para_idx": 3,                  # int 或 None，页内段序号（1-based，估算值）
   "from": "qa"}                   # 固定字符串，标识来源页
  ```
- **期望行为**：阅读页 `render()` 开头若该 key 非空，则 ①把 `st.session_state["read_sel"]` 置为该 `source`（若存在），②把 `np/sp/pp/hp` 等页码控件置为 `page`，③读完后可保留或清空该 key（清空更安全，避免下次误跳）。
- **已并行的兜底**：我同时写了 `st.query_params["jump_page"]` / `["paper"]`，`page_reading.py:43-60` 现有代码已能消费，所以**不配合也能跳**；配合后能拿到 `para_idx` 做段落高亮。
- 另需在 **MENU**（`ui/sidebar.py` MENU / `app.py:_PAGES`）中保持 `📖 版式阅读与翻译` 的索引为 **3**，我在 `ui/page_qa.py:24` 的 `READING_PAGE_IDX = 3` 是按这个顺序写死的。

### 给考生1（`services/library.py` / `services/parsing.py`）
- 我没有改 chunk 字段。`para_idx/para_span/score` 是**检索后由 `rag.annotate_paragraphs()` 加在浅拷贝上**的，不会写回 `ctx.chunks`，也不会污染 `library.json`。
- 若后续 chunk 增加 `section/para` 原生字段，`rag.py:132-158` 可直接改成优先读原生字段（当前未读）。

### 给考生5（`ui/common.py`）
- 我在 `ui/page_qa.py` 内自建了 `anchor_card()`，**没有 import 也没有改写** `common.source_card`。两者可并存；QA 页用自己的增强版。

### 给所有调用方
- `rag.answer()` 新增可选参数（`source_filter=None / compare_papers=None / per_paper=3 / max_repair=1`），**旧调用 `rag.answer(ctx, q, use_rerank=...)` 行为不变**，仅多返回 `refused` / `top_score` 两个键。
- `rag.retrieve()` 新增 `source_filter=None`；`sparse_search()` 新增 `source=None, allow_zero=False`；`build_context(sources, ctx=None)` 第二参可选——**均为带默认值追加**，现有调用点无需改动。
- `core/guardrails.validate_citations(answer, n_sources, source_texts=None, threshold=0.15)` 向后兼容。

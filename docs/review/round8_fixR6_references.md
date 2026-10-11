# Round 8 · 修复工程师 R6：References 占位废稿修复报告

- 修复对象：赛题模块六核心卖点「无幻觉 References」
- 独占文件：`services/writing.py`、`ui/page_writing.py`（**未改动任何其他 .py / config.py / docs/***，
  `git status` 里 `services/library.py` 的改动来自其他 agent，与本次无关）
- 运行环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（streamlit 1.63.0，Provider=zhipu，model=glm-4-flash）
- 实测素材：`pdf/1706.03762v7.pdf`（Attention Is All You Need，入库后得到 40 条真实参考文献）
- 自验脚本：`C:/tmp/r6/{repro.py, diag.py, offline_check.py, fault_injection.py, whitelist_check.py, final_dump.py}`，
  日志：`C:/tmp/r6/{before.log, after.log, final.log, final_dump.log}`，**未污染 storage/**

---

## 一、根因分析（不是猜的，是实测定位的）

### 1.1 复现结果（`C:/tmp/r6/before.log`）

```
[whitelist] source=PDF References 段真实抽取（1706.03762v7.pdf）
[whitelist] n=40                      ← 白名单本身完全正常，40 条真实文献
[gen] 耗时 141.2s
[gen] 字数 4881
[gen] validation.ok=True n_citations=2 n_entries=0 n_allowed=40   ← 关键异常
[gen] report=✅ 引用白名单校验通过：2 处 [n] 角标、0 条 References 条目，全部命中 40 条真实文献。

---- References 章节原文 ----
[1..40] (List the references as per the given real literature sources [1..40].)
```

### 1.2 排除掉的伪原因（逐条核实）

| 猜测原因 | 核实结论 |
|---|---|
| 白名单 `_real_references(ctx)` 返回条目太少/为空 | ❌ **不成立**：`reference_whitelist` 实测返回 **40 条**带作者+标题+年份的真实条目，source=`PDF References 段真实抽取（1706.03762v7.pdf）`，且 `validate_reference_whitelist(refs_text, refs_list)` 自校验 `ok=True / 40 条` |
| 生成是分段（Abstract/Intro/References 分开生成） | ❌ **不成立**：旧实现 `generate_outline_verified`（旧 writing.py:1091-1127）只有**一次** `_llm_text` 调用；第二次调用只发生在 UI 点了「重新生成（强化约束）」时 |
| `max_tokens` 不够 | ❌ **不成立**：`core/llm.py:264` 的 `call_llm_with_retry` 在 `max_tokens=None` 时**不下发**该参数，写作模块也从未传过 |
| 校验发现幻觉触发了 strict 重生成 | ❌ **不成立**：实测 `validation.ok=True`，根本没进 strict 分支 |
| **prompt 约束不够强 + LLM 自由发挥** | ✅ **根因 1**：旧 prompt（旧 writing.py:1072-1088）把 References 与正文放在同一份输出要求里、且明文写了
> `Title / Abstract … / Conclusion / References`
> `Related Work 与 References 只能引用下列真实文献，按 [n] 编号`

   glm-4-flash 面前摆着 9322 字符（≈3.3k tokens）的 40 条文献列表，要它照抄一遍成本极高，
   于是它选择了最省 token 的写法——用一行元指令代替整章：
   `[1..40] (List the references as per the given real literature sources [1..40].)`。
   **即便 prompt 再怎么加"必须逐字照抄"，只要这一节的产出仍依赖 LLM，就永远存在再次偷懒的概率。**

### 1.3 根因 2（更要命）：校验器把"0 条 References"判成了 PASS

```
[gen] validation.ok=True  n_entries=0
[gen] report=✅ 引用白名单校验通过：… 0 条 References 条目 … ← 废稿被判"无幻觉"
```

`validate_reference_whitelist`（旧 writing.py:837）的第二步调 `_generated_reference_entries(text)`：
占位行 `[1..40] (…)` 既不匹配 `^\[(\d{1,4})\]\s*`（`1..40` 里有 `.`），也不以 `-*• ` 开头，
所以被整行忽略 → `entries=0` → 后面的标题相似度循环**一次都没进** → `hallucinated` 为空 → `ok=True`。

这就是评委 G 说的"验收放水"的技术形态：**缺 Detection，不是阈值问题**。
（本次修复是**收紧**——新增"章节缺失/条目数不足"判定，`TITLE_SIM_THRESHOLD=0.8` 一个字没动。）

### 1.4 根因 3：耗时结构是"一次 LLM 串行调用 + 偶发的 90s 超时重试"

`diag.log` 拆调用次数/耗时：

```
[LLM] 1 次调用耗时=60.6s 输入字符=9682 输出字符=4154
[gen] 总耗时 61.3s   ← 正常样本
[t0.1] before.log: 141.2s ≈ 90s 超时（core/llm.py:289 默认 timeout=90）+ 1.5s 退避 + 第 2 次 ~50s
```

即：**References 一旦要求 LLM 写，就要多吐 ~9300 字符（≈2.6k tokens）的输出**，既慢又有偷懒风险。

---

## 二、修复方案总览

改成 **「LLM 只写正文 + 系统确定性拼接 References」**：

```
旧：  prompt(idea + 40条白名单) ──LLM──> 正文 + References（LLM 自由写） ──> 校验（0 条也能过）
新：  prompt(idea + 40条白名单，明令禁止写 References) ──LLM──> 正文
      └─> _strip_generated_references（删掉模型自带的 References）
      └─> sanitize_placeholders（占位串检测/清洗）
      └─> _sanitize_citations（越界 [n]→[?]）
      └─> build_references_section（确定性拼接，逐条照抄白名单）
      └─> validate_reference_whitelist（收紧版：章节缺失/条目不足必判 FAIL）
```

真实文献的作者/标题/年份/venue **本来就在白名单里**，拼一遍即可，让 LLM 重写纯属自找风险。

---

## 三、逐任务改动明细

### T1（P0）References 真实生成 · services/writing.py

| 改动 | 位置 | 说明 |
|---|---|---|
| 新增 `build_references_section()` | `services/writing.py:187-202` | 确定性拼接，`_ref_line` 与注入 prompt 的条目文本同源 |
| 新增 `_strip_generated_references()` | `services/writing.py:204-217` | 删掉 LLM 自带的 References 段（取最后一个标题往后切），避免双 References/占位行残留 |
| 重写 `_outline_prompt()` | `services/writing.py:1113-1148` | 明确禁止输出 References；约束引用范围、引用数量、篇幅 |
| 重写 `generate_outline_verified()` | `services/writing.py:1152-1223` | 5 段流水线；新增返回字段 `body / placeholder_issues / elapsed_sec / references_deterministic` |
| `_ref_line()` 去双句点 | `services/writing.py:297-311` | `"et al.".` → `"et al."` |
| **收紧** `validate_reference_whitelist()` | `services/writing.py:1036-1057` | 新增 References 章节缺失 / 条目数不足判定（阈值未动） |
| 白名单条目补齐年份 | `services/writing.py:534` + `491` | `_TAIL_PAGE_NOISE_RE` 剥掉 PDF 双栏混进来的 citation count / 页码 |

`build_references_section` 核心代码（`services/writing.py:187`）：

```python
def build_references_section(refs_list, heading=REFERENCES_HEADING):
    lines = [heading, ""]
    for i, r in enumerate(refs_list or [], 1):
        lines.append(_ref_line(i, r))
    return "\n".join(lines)
```

`generate_outline_verified` 关键片段（`services/writing.py:1196-1216`）：

```python
body, _  = _strip_generated_references(raw)      # 先删模型自带的 References
body, iss = sanitize_placeholders(body)          # 再清占位串
body, k  = _sanitize_citations(body, set(range(1, len(refs_list) + 1)))
residual = scan_placeholders(body)               # 残留占位如实上报
ref_section = build_references_section(refs_list)     # ← 确定性拼接，不上 LLM
text = body.rstrip() + "\n\n" + ref_section + "\n"
validation = validate_reference_whitelist(text, refs_list)   # 对最终全文校验
```

校验收紧片段（`services/writing.py:1036-1057`，**只加判定，未放宽任何阈值**）：

```python
entries = _generated_reference_entries(text)
result["n_entries"] = len(entries)
if n_allowed > 0:
    has_heading = bool(list(_GEN_REF_HEADING_RE.finditer(text)))
    min_entries = min(MIN_REF_ENTRIES, n_allowed)
    if not has_heading:
        result["hallucinated"].append({"kind": "section", ...,
            "reason": f"生成文本中未找到 References/参考文献 章节，但白名单有 {n_allowed} 条真实文献…"})
    elif result["n_entries"] < min_entries:
        result["hallucinated"].append({"kind": "section", ...,
            "reason": f"References 章节只有 {result['n_entries']} 条（< {min_entries}），疑似未生成或被占位符替代"})
```

### T2（P0）占位串零泄漏 · services/writing.py

| 改动 | 位置 |
|---|---|
| `_PLACEHOLDER_RULES`（10 条规则） | `services/writing.py:102-116` |
| `_NUM_RANGE_RE` / `_NUM_RANGE_MIN_SPAN` | `services/writing.py:117-118` |
| `scan_placeholders()` | `services/writing.py:121-144` |
| `sanitize_placeholders()` | `services/writing.py:146-184` |

覆盖：`[Placeholder]` / `{Placeholder}` / `<Placeholder>` / `TODO` / `TBD` / `FIXME` / `XXX` /
`(List all the references…)` / `(insert/add/fill in … references …)` / `to be added|filled|completed|written` /
中文 `待补|占位符|此处省略|以下略|略去` / 编号区间 `[1..40]`。

**防误伤设计**：编号区间只在**跨度 ≥ 5** 时才判定（`_NUM_RANGE_MIN_SPAN=5`），
因此合法写法 `[1, 3, 7]`、`[2, 5]`、`2017-2025` **不会被误删**（已写成断言，见离线自检 A 段）。
清洗策略：逐行删除命中片段 → 删空的行整行丢弃 → 原有空行保留（不破坏 Markdown）。

清理后的结果通过 `out["placeholder_issues"]` 上抛 UI，**UI 必须显式提示**，不静默输出（`ui/page_writing.py:311-318`）。

### T3（P1）降耗时

1. **结构性削减**：References 章节不再由 LLM 产出 → 直接省掉约 **9300 字符（≈2.6k tokens）的输出 token**，
   这是本项目里唯一一次由我控制的、确定性的时间削减，且不砍任何功能。
2. **篇幅控制**：`OUTLINE_TARGET_WORDS = 900`（`services/writing.py:72`），prompt 明确要求要点式 bullet + 短句。
3. **UI 结果缓存**（`ui/page_writing.py:43/268-282`）：同一 `(Idea, 文献来源)` 直接复用上次结果，
   演示时来回点按钮/重跑页面不再重复付费，UI 上给出 `⚡ 命中缓存` 提示。
4. 未做的事（诚实说明）：**没有**为求快而降低 `MAX_REF_ENTRIES`、砍文献条目、关校验或减少章节。

> 必须坦白：glm-4-flash 单次调用延迟抖动极大（实测 44.6s ~ 158.4s），长尾来自
> `core/llm.py:289` 的 `timeout=90.0` 触发一次重试（1.5s 退避后再来一轮）。
> 我不改 `core/llm.py`（非独占文件），因此**单次调用的尾延迟我无法消除**，详见风险点 4。

### T4（P1）UI 让"无幻觉"可被看见 · ui/page_writing.py

| 改动 | 位置 |
|---|---|
| 新增 `_render_reference_provenance()` | `ui/page_writing.py:213-252` |
| 重写 `_outline_section()` | `ui/page_writing.py:254-330` |

- 逐条摊平成表格：`# / 作者 / 标题 / 年份 / 出处 / 来路 PDF`（`st.dataframe`，`ui/page_writing.py:241`），
  每条都能追到具体是哪篇入库 PDF 的参考文献表——每条 meta 上新增的 `source_pdf` 字段来自
  `reference_whitelist`（`services/writing.py:924-925`）与 `pdf_path` 分支（`services/writing.py:1176`）。
- 顶部绿条明确口径："🔒 References 章节共 N 条，全部为确定性拼接：编号 [1..N] 逐条照抄自
  《xxx.pdf》正文末尾的 References 段，未经大模型改写或生成，因此不存在编造空间。"
- `validate_reference_whitelist` 不通过 → `st.error` 逐条列出（含新增的 `kind="section"` 章节缺失），
  并给出允许范围 `[1..N]`；通过 → `st.success`（`ui/page_writing.py:314-321`）。
- 文献库为空：**既不生成引用也给出明确指引**（`ui/page_writing.py:258` 拦截 + `:221-224` 空态提示），
  返回 `EMPTY_LIBRARY_HINT`（`services/writing.py:55`），`_render_reference_provenance([])` 会再兜一次 `st.error`。
- 抽取质量为低置信时（`source` 以 `⚠️` 开头）→ `st.warning` 提示（`ui/page_writing.py:249`）。

---

## 四、实测对比（真实 LLM 调用，非 mock）

### 4.1 修复前 · References 章节（真实输出，`C:/tmp/r6/before.log`）

```
# References

[1..40] (List the references as per the given real literature sources [1..40].)
```

- 白名单可用条目 40 条，正文 References 可用条目 **0 条**
- `validation.ok=True`（假阳性）
- 占位串：1 处（`[1..40]` + `(List the references…)`）
- 耗时 **141.2s**（另一无重试样本 61.3s）

### 4.2 修复后 · References 章节（真实输出，`C:/tmp/r6/final.log`，前 8 条）

```
# References

[1] Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization. arXiv preprint arXiv:1607.06450, 2016.
[2] Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. Neural machine translation by jointly learning to align and translate. CoRR, abs/1409.0473, 2014.
[3] Denny Britz, Anna Goldie, Minh-Thang Luong, and Quoc V. Le. Massive exploration of neural machine translation architectures. CoRR, abs/1703.03906, 2017.
[4] Jianpeng Cheng, Li Dong, and Mirella Lapata. Long short-term memory-networks for machine reading. arXiv preprint arXiv:1601.06733, 2016.
[5] Kyunghyun Cho, Bart van Merrienboer, Caglar Gulcehre, Fethi Bougares, Holger Schwenk, and Yoshua Bengio. Learning phrase representations using rnn encoder-decoder for statistical machine translation. CoRR, abs/1406.1078, 2014.
[6] Francois Chollet. Xception: Deep learning with depthwise separable convolutions. arXiv preprint arXiv:1610.02357, 2016.
[7] Junyoung Chung, Çaglar Gülçehre, Kyunghyun Cho, and Yoshua Bengio. Empirical evaluation of gated recurrent neural networks on sequence modeling. CoRR, abs/1412.3555, 2014.
[8] Chris Dyer, Adhiguna Kuncoro, Miguel Ballesteros, and Noah A. Smith. Recurrent neural network grammars. Proc. of NAACL, 2016.
```

以上全部是 1706.03762v7.pdf（Attention Is All You Need）正文末尾 References 段的真实条目，
逐字对照结果（`final.log:32-39`）：

```
解析出的条目数=40
  [1] authors='Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey …' title='Layer normalization' year='2016' venue='arXiv preprint arXiv:1607.0645…'
  [2] authors='Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua B…' title='Neural machine translation by jointly learning to …' year='2014' venue='CoRR, abs/1409.0473'
  [3] authors='Denny Britz, Anna Goldie, Minh-Thang Luong, a…' title='Massive exploration of neural machine translation …' year='2017' venue='CoRR, abs/1703.03906'
  [4] authors='Jianpeng Cheng, Li Dong, and Mirella Lapata'    title='Long short-term memory-networks for machine readin…' year='2016' venue='arXiv preprint arXiv:1601.0673…'
  [5] authors='Kyunghyun Cho, Bart van Merrienboer, Caglar G…' title='Learning phrase representations using rnn encoder-…' year='2014' venue='CoRR, abs/1406.1078'
前 5 条中字段齐全（作者+标题+年份）的=5/5
与白名单逐字一致的条目数=40/40
```

### 4.3 指标总览

| 指标 | 修复前 | 修复后 |
|---|---|---|
| References 条目数 | **0**（占位一行） | **40**（真实条目） |
| 与白名单逐字一致 | — | **40/40** |
| `validate_reference_whitelist` | `ok=True`（**假阳性**） | `ok=True, 40 条条目 / 47 处角标 / 40 条白名单` |
| 占位串（产品口径 `scan_placeholders`） | 1 处 | **0 处** |
| 占位串（脚本独立正则） | 1 处 | **0 处** |
| 耗时（含入库，同一支 API） | 141.2s / 61.3s | **44.6s / 53.2s**（另一抖动样本 158.4s，见风险点 4） |
| LLM 输出 token 负担 | 若真写 References 需额外 ~9300 字符 | **0**（确定性拼接） |

### 4.4 故障注入（不消耗 LLM，证明修的是"管线"不是"运气"）

`C:/tmp/r6/fault_injection.py`：把 `_llm_text` 打桩成旧版那坨垃圾输出
（`# References\n\n[1..3] (List the references…)` + 正文 `[Placeholder]` + `TODO` + `待补充`），
管线输出：

```
## Method
We adopt linear attention.        ← 真实内容保留，只删占位片段
复杂度分析.

# References

[1] A. Vaswani et al. Attention Is All You Need. NeurIPS, 2017.
[2] J. Devlin et al. BERT: Pre-training of Deep Bidirectional Transformers. NAACL, 2019.
[3] J. L. Ba et al. Layer Normalization. arXiv preprint arXiv:1607.06450, 2016.

残留占位串: []          validation.ok: True entries: 3
```

断言全绿：`[1..3]`/`[Placeholder]`/`TODO`/`待补充` 均不在最终文本里，且 "linear attention" 没被误删。

### 4.5 离线自检（`C:/tmp/r6/offline_check.py`）

- A 段：5 处占位全检出，`[2, 5]`、`2017-2025`、`xxx should stay xxx` 不误伤 ✓
- B 段：清洗后真实内容保留、占位行整行丢弃 ✓
- C 段：确定性 References 自校验 ok；**占位版 References 判 FAIL**（旧代码判 PASS）；
  完全缺失 References 判 FAIL；编造条目 `Totally Fabricated Paper` 仍判 FAIL（阈值没放宽）✓
- 白名单修复对比（`C:/tmp/r6/whitelist_check.py`）：缺年份条目 **2 → 0**（40 条全部有年份）

### 4.6 单元测试与编译

```
> python -m py_compile services/writing.py ui/page_writing.py   → COMPILE_OK（零报错）
> python -m unittest discover -s tests
..................
Ran 18 tests in 7.084s
OK                                    ← 18/18 通过
```

UI 侧还做了无 Streamlit 服务的裸函数冒烟：`_render_reference_provenance(refs, source)` 与空 refs 分支
均无异常抛出（bare-mode warning 属正常）。

---

## 五、风险点（诚实清单）

1. **References 的"真实度"上限 = 白名单抽取质量**。本方案把参考文献从 PDF 抽出来后不再加工，
   所以抽取阶段的错误会被 1:1 带进最终稿。当前 1706.03762v7.pdf 上仍有 1/40 条字段错位：
   第 38 条（Wu et al.《Google's Neural Machine Translation System》）因作者名单过长，
   `_parse_entry_fields` 的作者/标题切分没切开，标题整体落进了 authors 字段
   （**文本内容完整无损，也不算幻觉**，但 UI 表格的"标题"列会退化为 `raw` 长串）。
   根因在 `_sentence_ends`（`services/writing.py:358`）对 `et al.` 的保护使它没在该处断句；
   改它会影响全模块的参考文献抽取口径，风险大于收益，故本轮**不动**，留给你决定是否单开一轮。
2. **耗时抖动没有根治**。实测 44.6s / 53.2s / 158.4s 三个样本差异巨大，长尾是
   `core/llm.py:289` 的 `timeout=90.0` 打满后触发一次重试。我只能削减自己产生的输出，
   消不掉网络与客户端超时；建议下一轮由 core/llm.py 的 owner 考虑：把 timeout 降到 45~60s、
   或改走 `stream()` 让 UI 尽早出字（流式已存在，`LLMClient.stream`）。
   在修复前评委看到的 139s，与修复后偶发的 100s+ 属于同一类抖动，不是我这次改动引入的。
3. **`sanitize_placeholders` 有可能误删合法文本**：`[N..M]` 规则已用跨度 ≥5 兜底，
   但 `(insert/add/fill in … references …)` 这类规则命中学术论文里的正常措辞（如 "we fill in the details"）
   的概率不为零。当前策略是删片段 + 通过 `placeholder_issues` 上抛 UI 提示，用户可以"重新生成"，
   属可接受降级；若日后发现误删率上升，应优先收窄规则而不是关掉整个清洗。
4. **UI 缓存只在 session 内生效**（`st.session_state.outline_cache`），换 Idea 或换文献来源自动 miss；
   strict 重新生成**不读缓存**（避免强化约束版本被旧结果掩盖）。
5. **`validate_reference_whitelist` 新增"章节缺失即失败"是行为收紧**，
   若将来有调用方拿不带 References 的中间文本去校验，会拿到 `ok=False`。
   全仓检索确认当前只有 `services/writing.py` 与 `scripts/_verify_live_api.py:378` 两处调用，
   后者自带 References 章节且期望抓住幻觉，不受影响。
6. **未放宽任何阈值**：`TITLE_SIM_THRESHOLD` 仍为 0.8，`MIN_REF_ENTRIES=3` 仍为 3，
   本次只新增了判定项，没有一项是放松。此外 `services/writing.py` 内部已将 audit 口径写进
   模块 docstring（`services/writing.py:19-31`），后续维护者改动前请先读。

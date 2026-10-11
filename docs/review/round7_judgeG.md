# Round7 验收审计 —— 评委 G

- 评委：评委 G
- 审计对象：`scripts/_verify_live_api.py` 声称的 **19/19 PASS** + 3 个缺陷修复，commit `5dbf937`
- 环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（streamlit 1.63.0），.env 内智谱 Key 真实可用
- 自验脚本：`C:/tmp/judgeG/t1~t6`（**全部跑在临时目录**，未写 `storage/`；仅只读检查了 R5 已生成的 `storage/cache/citation_cache.json`）
- 实测 LLM 调用：5 次（OCR 视觉 1、综述 1、写作框架 1、QA 2）+ 划词翻译 3 次，**未重复跑全流程**
- 时间：2026-10-12

> 审计原则：**跑通只是及格线，输出质量才是比赛得分点。** 本轮是项目首次接真 API，价值应体现在
> "真跑才暴露的问题被修掉"，而不是"终于有一张全绿的验收单"。

---

## 1. 总评

### **5.5 / 10**

**一句话结论**：三个修复里有 2 个是真修、且有硬证据（429 退避 6/6 mock PASS、磁盘缓存 9s→0.0000s、
`_node_year` 让时间线从"不可用"变成 2017/2018），OCR 与细粒度问答的质量也确实经得起抽查；
**但验收脚本自身有 2 条恒真/硬编码断言、4 条只验长度的断言，"19/19"里至少 8 条不构成质量证据**，
且被这些弱断言放过的输出里藏着三个真问题：写作框架的 References 是占位符 `[1-40] (List all the references...)》、
综述把 BERT 叫成《Language Understanding》、图谱前沿判定把错配的 55 次引用当"外部真实被引"直接进 UI。
**跑通了，但没验收住质量。**

| 维度 | 得分 | 说明 |
|---|---|---|
| 修复真实性 | 8/10 | 429 退避、磁盘缓存、`_node_year` 三处实现正确，边界有瑕疵 |
| 验收脚本可信度 | 3/10 | 2 条恒真、4 条只验长度、1 条硬编码 True |
| 输出质量 | 4/10 | OCR/QA/翻译好；综述标题错的、写作框架 References 是占位符 |
| 回归安全 | 9/10 | 18/18 + 双冒烟 59/59 全绿，签名兼容 |

---

## 2. 放水核查结论（最重要）

先把 19 条逐一从 `scripts/_verify_live_api.py` 定位，再判定是否构成"质量证据"。

| # | 组 | 用例 | 行号 | 断言原文 | 判定 |
|---|---|---|---|---|---|
| 1 | ocr | 识别为扫描件 | `:184` | `scanned is True` | 🟢 有实质（布尔属性，非长度） |
| 2 | ocr | 首页 OCR 输出文本 | `:208` | `len(txt.strip()) > 200` | 🟡 **只验长度** |
| 3 | qa | **v7 入库** | `:255` | `record(..., True, ...)` | 🔴 **硬编码 True，零断言** |
| 4 | qa | 问答 β1/β2 | `:265` | `len(hit) >= 1`（期望 2 个值） | 🟡 **命中一半即过** |
| 5 | qa | ↳ 溯源锚点 1 | `:275` | `len(pg) > 0` | 🟢 有实质（页码硬要求） |
| 6 | qa | 问答 GPU | `:265` | `len(hit) >= 1`（期望 P100 / 8） | 🟡 同 #4 |
| 7 | qa | ↳ 溯源锚点 2 | `:275` | `len(pg) > 0` | 🟢 有实质 |
| 8 | qa | 问答 BLEU | `:265` | `len(hit) >= 1`（期望 28.4 / 41.8） | 🟡 同 #4 |
| 9 | qa | ↳ 溯源锚点 3 | `:275` | `len(pg) > 0` | 🟢 有实质 |
| 10 | guard | 引用内容一致性校验 | `:305` | 有依据 0 issue **且** 无依据 >0 issue | ✅ **有实质**（正反双向） |
| 11 | guard | 拒答闸门 | `:322-323` | 答案含未找到/无法/不确定等 | ✅ 有实质（关键词法略糙但方向对） |
| 12 | compare | 跨文献对比 | `:353` | `("BERT" in ans or "bert" in ans.lower()) and len(ans) > 100` | 🟡 **只验"提到 BERT"，不验对比是否正确** |
| 13 | write | References 白名单幻觉校验 | `:380` | `not r.get("ok") or r.get("hallucinated")` | ✅ **有实质**（我做了反向用例，判别力成立） |
| 14 | write | Figure Caption 事实抽取 | `:393` | `any(ch.isdigit() for ch in str(facts))` | 🟡 **只要有任意数字字符** |
| 15 | write | **论文框架生成** | `:412` | `len(txt) > 200` | 🔴 **只验长度，且实测内容不可用** |
| 16 | graph | 图谱构建 | `:434` | `G.number_of_nodes() > 0` | 🟡 **极弱**（2 节点即过） |
| 17 | graph | **自动综述生成** | `:437` | `len(str(rev)) > 200` | 🔴 **只验长度，且实测综述把 BERT 叫错名** |
| 18 | graph | **Future Work 结构化判定** | `:441` | `n_fw >= 0` | 🔴 **恒真断言**（`len()` 永不为负） |
| 19 | extern | 被引数 / 限流诚实降级 | `:466`/`:474` | `unconfirmed or low_confidence`；或 `cnt > 1000 且未标不可信` | ✅ 有实质 |

### 统计

- **🔴 放水（恒真 / 零断言 / 放行坏输出）：4 条** → #3、#15、#17、#18
- **🟡 阈值过松（几乎必然通过）：6 条** → #2、#4、#6、#8、#12、#14、#16（7 条，含 #16）
- **✅ 有实质判别力：7 条** → #1、#5、#7、#9、#10、#11、#13、#19（8 条）

> 也就是说：**19 条里只有 8 条（42%）是真在验东西，4 条（21%）是纯放水。**

### 逐条给证据

**#3 硬编码 True（`_verify_live_api.py:253-256`）**
```python
t0 = time.time()
ctx = ensure_papers("1706.03762v7.pdf")
record("qa", "v7 入库", True,                      # ← 字面量 True
       f"{len(getattr(ctx, 'chunks', []))} chunks / {time.time() - t0:.1f}s")
```
即使 `commit` 后 `chunks == 0`（解析彻底失败），这条依然 PASS。chunks 数只出现在 evidence 字符串里，**不参与判定**。

**#18 恒真（`:439-442`）**
```python
fw = explore_future_structured(ctx, G)
n_fw = len(fw) if isinstance(fw, list) else 0
record("graph", "Future Work 结构化判定", n_fw >= 0,   # ← len() 恒 >= 0
       f"抽出 {n_fw} 条 / 状态分布={[f.get('status') for f in fw[:5]] ...}")
```
`fw` 返回 `[]`（一条都没抽出来）时 `n_fw = 0`，`0 >= 0` → **PASS**。这是整个脚本里最直白的一条放水。
（顺带：若 `explore_future_structured` 抛异常，会落到 `:443` 的 except 记 FAIL；但只要不抛就一定 PASS。）

**#15 / #17 只验长度** —— 见 §4.3 / §4.1 的实测输出，两处都是"字数达标但内容不能用"。

**#14 `any(isdigit)` 实测（`C:/tmp/judgeG/t6_local.py`）**
```
纯字符串 df（无任何数值列）facts = "Table shape: 2 rows x 2 columns."
纯字符串 df 断言 any(isdigit) = True        ← 没有真实数值事实，照样 PASS
空 df 断言 = False
```
即该断言实际只等价于"df 非空"，**完全没有验证 chart_facts 抽到了正确的数值事实**。
（正常 df 下 facts 本身是对的，所以这不是功能 bug，是断言无判别力。）

**#12 跨文献对比** —— 只要求答案里出现 "BERT" 三个字母，既不要求提到 Transformer，也不要求对比结论正确。
一篇只复述 BERT 而完全没做对比的答案同样 PASS。

### 关于 graph 组的"2 节点刷 PASS"（重点质疑项）

**我的判断：成立，而且比"样本小"更严重——它不是"说服力不足"，是"结论本身已被证伪"。**

实测（`C:/tmp/judgeG/t4_graph_write.py`）：
```
节点数 = 2   边数 = 1
节点 '1706.03762v7.pdf' title='Attention Is All You Need'
节点 '1810.04805v2.pdf' title='Language Understanding'      ← 见 §4.1
边 '1810.04805v2.pdf' → '1706.03762v7.pdf'
   evidence='参考文献条目高度匹配（100%）...'
review_structure: timeline = [(2017, [...]), (2018, [...])]
                  clusters = [(0, 'attention/language/understanding', 2, 2017.5)]
                  backbone = 1 条, bridges = 0 条
```

赛题要求的是「针对**批量文献（如 20 篇）**的引用图谱挖掘」。在 2 节点 1 边上：

1. **社区发现无意义**：`_communities` 只能返回 1 个簇（实测 `clusters` 长度 1），
   而综述的第二节标题是「主要方向（按主题簇）」——一个簇谈何"主要方向"。
2. **PageRank 无意义**：2 个节点、1 条边，PageRank 0.6491 / 0.3509 是拓扑的必然结果，
   不构成"基石论文识别"的证据（实测基石清单还是`（无）`，见综述原文）。
3. **bridges = 0**：`bridges`（跨主题簇引用边，赛题"分歧点"卖点）在单簇图上**恒为 0**，
   即这条能力在 2 节点样本下**根本没被执行到**，却被算进 PASS。
4. **20 篇渲染性能未被验证**：`bench_graph.py` 存在，但 `_verify_live_api.py` 没跑它，
   19 条里没有任何一条覆盖 20+ 篇规模。

**结论**：graph 组 3 条（#16/#17/#18）在 2 节点样本下**全部自动通过**，其中 #18 还是恒真。
这不是"最小样本刷 PASS"，而是**用最小样本把三条断言全部刷成必然通过**。🔴

---

## 3. 扣分清单

| 严重度 | 问题 | 文件:行号 | 证据（实测输出） | 扣分 | 修复建议 |
|---|---|---|---|---|---|
| 🔴 | Future Work 断言恒真：`n_fw >= 0` 永不失败，抽 0 条也 PASS | `scripts/_verify_live_api.py:441` | `n_fw = len(fw) if isinstance(fw,list) else 0; record(..., n_fw >= 0, ...)` | 0.5 | 改为 `n_fw >= 1 且 至少 1 条 status ∈ {已解决,存疑,开放} 且 evidence 非空`；并断言 `snippet_fallback=True` 的比例 < 50% |
| 🔴 | "v7 入库" 硬编码 `True`，chunks=0 也 PASS | `scripts/_verify_live_api.py:255` | `record("qa","v7 入库", True, ...)` | 0.3 | 改为 `len(ctx.chunks) > 0 and len(ctx.paper_order) >= 1` |
| 🔴 | 论文框架生成只验 `len(txt)>200`，实测 References 是占位符、3 处 `[Placeholder]` 明文泄漏 | `scripts/_verify_live_api.py:412` | 实测 4515 字，`# References` 正文 = `[1-40] (List all the references according to the given numbers)`；`[Key Conclusion Placeholder: ...]`、`## Formulas Placeholder`、结尾 Note 自认 "should be replaced"；耗时 **139.3s** | 0.7 | 断言升级：References 段必须 ≥5 条且标题全部命中白名单（复用已有的 `validate_reference_whitelist`）；断言输出中不得出现 `Placeholder`/`TODO`/`e.g.`；对 139s 加超时预算 |
| 🔴 | **图谱前沿判定把错配的 55 次当"外部真实被引"直接进 UI 并计分** | `services/graph.py:403-414`；上游 `tools/definitions.py:252,266` | 实测 `detect_frontier` 输出：`"外部真实被引 55 次（crossref，匹配《Is Attention All You Need?》相似度 0.88）→ 外部热度得分 0.58"`，且写入 `components['external']=0.583`（权重 0.15），BERT 因此被排到第 1（70.3 分）。该字符串经 `t_build_graph` 的 `"reasons": f["reasons"]` 直达前端 | 0.7 | `services/graph.py:408` 的 `if real and isinstance(real.get("count"), int):` 增加 `and not real.get("title_attribution_unconfirmed") and not real.get("low_confidence")`，与 `assess_reference:1020-1031` 的不计分口径对齐 |
| 🔴 | 综述生成只验长度，实测把 BERT 命名为《Language Understanding》 | `scripts/_verify_live_api.py:437` | 实测综述：`"2018年，论文《Language Understanding》() ..."`；四节齐全但第二节只有 1 个簇；"近期趋势"讲的是 2018 年（当前 2026） | 0.5 | 断言升级：综述必须包含每篇论文的**正确标题**；`timeline` 非空；`n_nodes>=N` 时才允许"主要方向/分歧"措辞 |
| 🟡 | BERT 标题抽取成 "Language Understanding"（跨行标题合并失败） | `services/library.py:86,105,110` | `1810.04805v2.pdf` 首页原生文本 = `BERT: Pre-training of Deep Bidirectional Transformers for\nLanguage Understanding`。第 1 行以 `for` 结尾 → 被 `_DANGLING_RE`（`:86`，含 `for`）在 `:105` 判为"悬空行"直接 `continue`，**永远轮不到 `:110` 的续行合并**。函数 docstring `:92` 明确写着"学术论文首页常有跨行标题（BERT: ...）"——**注释点名的用例就是它自己没通过的用例** | 0.4 | `:105` 的 `_DANGLING_RE` 过滤改为"先尝试续行合并、合并后仍悬空才丢弃"；或把合并触发条件从"以 `:`/`-` 结尾"放宽为"以连接词/冠词/介词结尾" |
| 🟡 | graph 组仅 2 节点 1 边，无法支撑赛题"20 篇批量文献"，且 `bridges` 恒为 0 | `scripts/_verify_live_api.py:430,434` | 实测 `2 节点 / 1 边 / 1 簇 / bridges=0`；`bench_graph.py` 未被 19 条覆盖 | 0.5 | 用 `scripts/make_longbook.py` 或 demo_data 造 ≥20 篇的 fixture 跑 graph 组；断言 `n_nodes>=20`、`len(bridges)>=1`、单次 build 耗时阈值 |
| 🟡 | 3 条细粒度问答期望 2 个值但只要求命中 1 个 | `scripts/_verify_live_api.py:265` | `ok = len(hit) >= 1`；`expect=["0.9","0.98"]` / `["28.4","41.8"]` / `["P100","8"]` | 0.2 | 改为 `len(hit) == len(expect)`。注：我实测严格化后 **2/2 仍全命中**（见 §6），所以这只是断言松，不是功能缺陷 |
| 🟡 | Figure Caption 断言仅 `any(isdigit)` | `scripts/_verify_live_api.py:393` | 纯字符串 df → facts=`"Table shape: 2 rows x 2 columns."` → 断言 **True** | 0.1 | 改为断言 facts 含真实统计量关键字（min/max/mean/correlation/trend）且数值与 df 一致 |
| 🟡 | OCR 只验 `len>200` | `scripts/_verify_live_api.py:208` | 实测 2790 字符、词级准确率 93.9%、召回 98.5%（**质量确实好**），但断言本身不保证质量 | 0.1 | 增加与原 PDF 的相似度断言（本项目 `pdf/1706.03762v1.pdf` 就是同文的原生版，可直接比对，实测 `SequenceMatcher=0.960`） |
| 🟡 | Future Work "已解决" 判定基于通用词 `layer`/`layers` | `services/graph.py:830-841` | 实测：`keywords=['attention','convolutional','layer','layers','future']`，证据 `《Language Understanding》以 1 跳引用了本文，命中关键词 2 个：layer、layers` → 判 `已解决`。命中"层"这种通用词不构成"该 future work 已被解决"。另 1 条 `snippet_fallback=True`，item 抓到的是 `[MASK]/RND` 表格说明，根本不是 Future Work 段落 | 0.2 | 关键词命中需加权 IDF，排除 `layer/model/network` 等通用词；`snippet_fallback=True` 时禁止输出"已解决" |
| 🟡 | 19/19 结论无运行日志留存，不可复现 | commit `5dbf937` | `grep -rln "19/19\|PASS 19" docs/ scripts/` → **空**。仓库内只有 commit message 里的自述，没有产物 | 0.1 | 把 `_verify_live_api.py` 的完整 stdout + 生成的综述/框架/OCR 文本作为 `docs/review/round7_evidence/` 提交 |
| 🟢 | `_node_year` 不认 `arXiv:` 前缀、两位年份 `"17"` 被解析成公元 17 年 | `services/graph.py:484,489` | 边界实测：`arxiv_id='arXiv:1706.03762'` → `None`（期望 2017）；`year='17'` → `17`（期望 2017）。其余 17/20 通过（含 `math.GT/0309136`→2003、`hep-th/9901001`→1999、月份 13/00 → None） | 0.1 | 正则前缀段加 `A-Za-z:` 兼容；`int(raw)` 后加 `if 1900 <= v <= 2100 else None` 的长度校验 |
| 🟢 | 综述里年份显示为空括号 `《Attention Is All You Need》()` | `services/graph.py:586`（`_fmt`）、`:617` | 实测注入 prompt 的时间线块 = `- 2017：《Attention Is All You Need》()`——`_fmt` 仍取 `papers[n].get('year','')`（空），没用刚修好的 `_node_year` | 0.05 | `_structure_blocks._fmt` / `_deterministic_review._fmt` 改用 `_node_year(ctx, n)` |
| 🟢 | 跨文献对比只验含 "BERT" | `scripts/_verify_live_api.py:353` | 不验是否提及 Transformer、不验对比结论 | 0.05 | 增加"同时提及两者 + 出现对比性表述"的断言 |
| 🟢 | 三级全 429 时冷调用实测 16.81s，逼近 20s 预算（报告称 9.24s） | `services/citation_quality.py:60` | 实测 `冷调用（网络全429）: 16.81s 返回=None 网络调用=9次` | 0.05 | 报告数字需注明场景；或对 `dead` 源做跨调用记忆（报告 §7.5 已自认此项未做） |
| | | | **合计** | **≈4.5** | **10 − 4.5 = 5.5** |

---

## 4. 三个修复复验

### 4.1 修复 1：`services/graph.py::_node_year` arXiv YYMM 回退 —— ✅ **真修好了**（有保留意见）

**代码审查**：正则 `^(?:[a-z\-]+(?:\.[A-Za-z]{2})?/)?(\d{2})(\d{2})[.\d]`（`services/graph.py:489`）

**边界实测（`C:/tmp/judgeG/t1_node_year.py`，20 例，17 通过）**

| 输入 | 返回 | 判定 |
|---|---|---|
| `year='', arxiv_id='1706.03762'` | **2017** | ✅ |
| `year='', arxiv_id='1810.04805'` | **2018** | ✅ |
| `year='', arxiv_id='1706.03762v7'` | 2017 | ✅ |
| `year='', arxiv_id='math.GT/0309136'` | **2003** | ✅ 旧格式覆盖 |
| `year='', arxiv_id='hep-th/9901001'` | **1999** | ✅ 跨世纪 |
| `year='', arxiv_id='cs.CL/0401001'` | 2004 | ✅ |
| `year='', arxiv_id='1713.03762'`（月 13） | None | ✅ 非法月份正确拒绝 |
| `year='', arxiv_id='1700.03762'`（月 00） | None | ✅ |
| `year='', arxiv_id=''` | None | ✅ |
| `year='', arxiv_id='arXiv:1706.03762'` | **None** | ❌ 期望 2017（前缀不兼容） |
| `year='17'` | **17** | ❌ 期望 2017（两位年份被当成公元 17 年） |
| `year='n/a', arxiv_id='1810.04805'` | 2018 | ✅ 脏值回退 |

**修复前提核实**：报告称"1706.03762v7.pdf、1810.04805v2.pdf 的 `year` 均为空串"——**属实**。
实测完整入库链路 `prepare_pdf → commit` 后：`year=''`、`arxiv_id='1706.03762'/'1810.04805'`。

**杠杆验证（确定性，未调 API）**：
```
A) 修复后（year 空 + arxiv_id 有值）
   timeline = [(2017, ['a.pdf']), (2018, ['b.pdf'])]
   注入 prompt 时间线块 = "- 2017：《Attention Is All You Need》()\n- 2018：《BERT》()"
B) 假设未修复（year 空 + arxiv_id 也空）
   timeline = []
   注入 prompt 时间线块 = "（无年份数据，时间线不可用）"
   确定性综述 = "文献元数据中无可用年份，时间线维度不可得（不做推断）"
```
修复前后差异确凿，**"无法按时间线描述 → 2017/2018 时间线"的转变是真的**。

**但是（保留意见）**：修复后的实测综述里，两篇论文的名字是
`《Attention Is All You Need》()` 和 **"《Language Understanding》"**——
BERT 的标题被抽错了（见 §4.4 / 扣分表 🟡）。所以"综述变好了"这句话只对了一半：
**年份对了，主角名字错了**。在赛题"综述要能读"的标准下，这仍然是废稿。

---

### 4.2 修复 2：`services/citation_quality.py`（R5）—— ✅ **T1~T4 真修好了，但存在 🔴 泄漏**

#### (a) 429 退避重试 —— mock 实测 **6/6 PASS**（`C:/tmp/judgeG/t2_citation.py`，拦截 `urlopen`）

| 场景 | urlopen 调用次数 | 记录的等待 | error_kind | 判定 |
|---|---|---|---|---|
| 429 无 Retry-After（全失败） | 3（1+2 重试） | `[2.0, 1.2, 5.0, 1.2]` | `rate_limited` | ✅ 2s→5s 递增 |
| 429 `Retry-After=1` | 2 | `[1.0, 1.2]` | ok | ✅ 采纳 Retry-After |
| 429 `Retry-After=25066` | **1** | `[]` | `rate_limited` | ✅ **只调 1 次、不等 6.96h** |
| 429 `Retry-After=5` | 2 | `[5.0, 1.2]` | ok | ✅ clamp≤8 生效 |
| 503（全失败） | 3 | `[2.0, 1.2, 5.0, 1.2]` | `server_error` | ✅ |
| 404 | 1 | `[]` | `not_found` | ✅ 不重试 |

（等待列表中的 `1.2` 是 `MIN_REQUEST_INTERVAL` 节流，与退避共用 sleep，属预期）
**R5 报告 §2 声称的 6/6 与实际一致，未虚报。**

#### (b) 磁盘缓存落盘 —— ✅ 属实

只读检查 R5 已生成的文件（未修改）：
```
storage/cache/citation_cache.json   1243 bytes  存在=True
条目数 1  key='attention is all you need'
  ok=True source=crossref cited_by_count=55
  title_matched='Is Attention All You Need?' error_kind=''
```
另在临时目录（`C:/tmp/judgeG`，未污染 `storage/`）复跑一遍落盘流程：
```
冷调用（网络全 429）: 16.81s  返回=None  网络调用=9 次
缓存文件存在: True   落盘条目数: 1   ok=False error_kind=rate_limited
   err="openalex: HTTP 429；semanticscholar: HTTP 429；crossref: HTTP 429"
```

#### (c) 缓存命中不打网络 —— ✅ 属实

```
第二次（一级内存命中）   : 0.0000s   新增网络调用=0 次
清内存留磁盘（二级命中） : 0.0000s   新增网络调用=0 次
```
冷 16.81s → 命中 0.0000s，**网络调用次数增量确为 0**，不是靠"耗时短"推断的。

#### (d) 🔴 重点核查：错配的 55 次会不会泄漏成"该论文被引 55 次"？—— **会。**

R5 报告 §5.2 只证明了 `assess_reference` 不计分，我复现了这一点：
```
(a) assess_reference:
    level          = 低含金量
    real_citations = None          ← ✅ R5 声称属实
    reasons 含 "其被引数 55 **不计入本篇**；真实被引维度不计分、不猜测"
```

**但 `assess_reference` 不是唯一出口。`services/graph.py:403-414` 的 `detect_frontier` 完全绕过了这个保护：**

```python
# services/graph.py:403
real = fetch_citation_count(info.get("title", item["paper"]), year=_node_year(ctx, item["paper"]))
...
# :408  ← 只检查 count 是不是 int，没看 title_attribution_unconfirmed / low_confidence
if real and isinstance(real.get("count"), int):
    c = max(0, real["count"])
    ext = min(1.0, math.log10(1 + c) / 3.0)
    item["comps"]["external"] = ext                      # ← 计分（权重 0.15）
    item["reasons"].append(
        f"外部真实被引 {c} 次（{real.get('source')}，匹配《{real.get('matched_title')}》..."  # ← 展示
```

实测（注入 R5 报告里那份 crossref 返回体）：
```
[70.3] BERT
   components={'low_indegree': 1.0, 'recency': 0.2, 'out_degree': 1.0, 'external': 0.583}
   - 外部真实被引 55 次（crossref，匹配《Is Attention All You Need?》相似度 0.88）→ 外部热度得分 0.58   ★泄漏
[14.3] Attention Is All You Need
   - 外部真实被引 55 次（crossref，匹配《Is Attention All You Need?》相似度 0.88）→ 外部热度得分 0.58   ★泄漏
```

**这条 reason 会原样进 UI**：`tools/definitions.py:252` 调 `detect_frontier(ctx, G, metrics)`
（默认 `use_external=True`），`:266` 把 `f["reasons"]` 塞进 `edge_derivative` 返回给前端。
即用户在「引用图谱」里会看到：**"BERT —— 外部真实被引 55 次"**，而 55 是 2025 年另一篇论文的被引数。

> R5 报告 §7.2 自己写了一句"若 UI 侧希望展示『疑似匹配到 55 次（另一篇）』，读 `evidence["raw_candidate"]` 即可"，
> 说明作者**知道数据层仍会返回 55**，但没有排查另一个消费者 `detect_frontier`。
> 这是"改了一处、漏了另一处"的典型。🔴

---

### 4.3 修复 3：OCR 首次实测通过 —— ✅ **真过了，且质量确实好**

`pdf/1706.03762v1_img.pdf`（扫描件）第 1 页，对比同文原生版 `pdf/1706.03762v1.pdf` 第 1 页：

```
detect_scanned: True          ocr_supported: True
OCR 输出: 2790 字符 / 8.3s     原 PDF 首页原生文本: 2719 字符
整体字符级相似度(SequenceMatcher): 0.960
词级：原 PDF 392 词 / OCR 383 词
      漏识别 6 词 / 多识别 0 词 / 错字 18 词
      词级准确率 ≈ 93.9%   词级召回 ≈ 98.5%
```

**差异明细逐条看过，"错字"里绝大多数是连字修正，属于改进而非错误**：

| 原 PDF（含连字） | OCR 输出 | 性质 |
|---|---|---|
| `con guration.` | `configuration.` | ✅ 修正 |
| `signi cantly` | `significantly` | ✅ 修正 |
| `rmly` | `firmly` | ✅ 修正 |
| `ef ciency` | `efficiency` | ✅ 修正 |
| `ht 1` | `h_{t-1}` | ✅ 转成 LaTeX |
| `nikip` | `nip` | ❌ **真错字**（Niki Parmar 邮箱 `nikip@google.com` → `nip@google.com`） |
| `arXiv:1706.03762v1 [cs.CL] 12 Jun 2017` | *丢失* | ❌ **漏一行**（整行 arXiv 页眉未识别） |

**关键数字全部保住**：`28.4` ✅、`41.0` ✅（v1 原文确为 41.0，不是 v7 的 41.8，OCR 没串版本）。
输出还是带 Markdown 结构（`# Attention Is All You Need`、`## Abstract`），可直接喂下游 RAG。

**结论**：OCR 这条是本轮**质量最扎实的一项**，2793 字符不是刷出来的。唯一问题是
`scripts/_verify_live_api.py:208` 的断言 `len>200` 配不上这个质量——建议把 0.960 相似度写成断言。

---

## 5. 回归测试

| 项目 | 命令 | 结果 |
|---|---|---|
| 单元测试 | `python -m unittest discover -s tests` | **`Ran 18 tests ... OK`（18/18，8.448s）** ✅ |
| UI 冒烟 | `python scripts/_smoke_ui.py` | **✅ 冒烟通过：全部页面 0 未捕获异常**（9 页面 + 9 项故障注入全部"友好提示=有"）✅ |
| 阅读冒烟 | `python scripts/_smoke_read.py` | **PASS 59 / FAIL 0** ✅（含 541 页长文档扫描 6.0s / 峰值 306MB） |
| 编译 | （t1~t6 全部 import 成功） | 无异常 ✅ |

**签名兼容性核查**（`services/citation_quality.py` +466 行是否破坏调用方）：

```
grep 调用点：
  scripts/_verify_live_api.py:454-455  fetch_citation_count("Attention Is All You Need")            → 单参调用 ✅ 兼容
  tools/definitions.py:12              from . import score_references, assess_reference              → import 未变 ✅
  tools/definitions.py:149             score_references(refs)                                        → 离线版签名未动 ✅
  tools/definitions.py:164             assess_reference(ref, use_network=True)                       → 签名未动 ✅
  services/graph.py:403                fetch_citation_count(title, year=...)                         → 兼容 ✅
  tests/test_core.py:181               classify_papers(G, m) 二元调用                                 → ctx 有默认值 ✅
```

实测 `assess_reference` / `fetch_citation_count` / `score_references` 全部按旧签名调用成功，
**未发现回归**。唯一新增的是 `citation_status()`（纯新增，不影响旧调用方）。

---

## 6. 我抽样发现的**新问题**

### 6.1 🔴 BERT 标题被抽成《Language Understanding》（`services/library.py:86,105`）

这是"只有真跑才会暴露"的问题里**最严重的一个**，而 round7 的 19 条断言完全没覆盖"标题正确性"。

- 实测入库后 `ctx.papers['1810.04805v2.pdf']['title'] == 'Language Understanding'`
- 根因：`services/library.py:105` 的 `_DANGLING_RE`（`:86`，词表含 `for`）先把第 1 行
  `BERT: Pre-training of Deep Bidirectional Transformers for` 判为"悬空行" `continue` 掉，
  **导致 `:110` 的续行合并逻辑永远执行不到**；第 2 行 `Language Understanding` 被当作标题返回。
- 反讽点：`guess_title_from_text` 的 docstring（`:92`）写的是
  "学术论文首页常有跨行标题（**"BERT: Pre-training of ... / Language Understanding"**）…
  因此这里会合并紧邻的续行" —— **注释里举的例子，正是它自己跑不通的例子。**
- 影响面：综述、图谱节点标签、前沿判定、查重（`_norm_title` 比对）、UI 文献列表**全部显示错名**。
  实测生成的综述通篇把 BERT 称为《Language Understanding》。

### 6.2 🟡 写作 Copilot 的核心卖点"无幻觉 References"在实测里是占位符

`generate_outline_verified(ctx, "Efficient attention for long documents")`：

```
字数 4515 / 耗时 139.3s / ok=None
...
# References
[1-40] (List all the references according to the given numbers)

Note: The placeholders [Placeholder for the mathematical formulation of the AHA mechanism.]
and [Key Conclusion Placeholder] should be replaced with the actual mathematical equations
and key conclusions derived from the research.
```

- `# References` 下**只有一行占位符**，没有任何一条真实文献 —— 而这恰恰是赛题"参考文献无幻觉"的核心卖点
- `[Key Conclusion Placeholder]`（`:5`）、`## Formulas Placeholder`（`:39-40`）、
  `[Translation datasets: e.g., ...]`（`:45-46`）共 **3 处占位符明文泄漏**
- `## Agent Architecture` 出现在一篇论文大纲里 —— 模板串味
- `ok=None`（`generate_outline_verified` 没把校验结果放进 `ok`），`_verify_live_api.py:413` 打印的"校验=?"也是空的
- **139.3s**：UI 里用户要干等 2 分 19 秒

`len(txt) > 200` → PASS。**这是本轮最典型的一处"用长度冒充质量"。**

### 6.3 ✅ 细粒度问答与划词翻译：抽查合格（正面结论）

严格化复测（把 `len(hit)>=1` 改成全命中）：
```
Q: Adam 优化器的 β1 和 β2 分别设置为多少？
   期望 ['0.9','0.98']  实际命中 ['0.9','0.98']   35.1s  溯源 6 条  页码 [5,4,2,4,7,11]
   答案: "Adam 优化器的 β1 和 β2 分别设置为 0.9 和 0.98【5】。" + 对比表格
   原断言 PASS | 严格断言 PASS
Q: WMT 2014 英德和英法 BLEU？
   期望 ['28.4','41.8']  实际命中 ['28.4','41.8']  15.0s  溯源 6 条  页码 [1,8,8,11,7,10]
   原断言 PASS | 严格断言 PASS
```
**结论：QA 组的 3 条虽然断言松，但功能本身是真过关的**（这点要给项目记功）。

划词翻译（`services/translate.math_safe_translate`，3 句抽样）：
```
EN: The dominant sequence transduction models are based on complex recurrent or ...
ZH: 主导的序列转换模型基于编码器-解码器配置中的复杂循环或卷积神经网络。（原文回附）
EN: Our model achieves 28.4 BLEU on the WMT 2014 English-to-German translation task, ...
ZH: 我们的模型在WMT 2014英语到德语的翻译任务中实现了28.4 BLEU，超过了现有最佳结果，包括超过2 BLEU的集成模型。
EN: In this work we propose the Transformer, a model architecture based solely on attention ...
ZH: 在这项工作中，我们提出了Transformer，这是一种仅基于注意力机制的模型架构，完全摒弃了循环和卷积。
```
术语准确、数字未失真，2.5~7.2s。**唯一小瑕疵**：第 1 句译文后回附了英文原文（可能是"公式安全"策略的副作用），
略显冗余，但不影响可用性。

---

## 7. 虚报核查

| 声称 | 核查 | 结论 |
|---|---|---|
| "19/19 PASS" | 19 条中 4 条恒真/硬编码/放行坏输出，6~7 条阈值过松；**8 条（42%）才真在验东西** | ⚠️ **数字属实，含金量虚高** |
| "OCR 输出 2793 字符" | 实测 2790 字符（VLM 非确定性，差异合理），词级准确率 93.9% | ✅ 属实且质量达标 |
| "细粒度问答 3/3 命中真值 + 溯源锚点 6/6 带页码" | 严格化复测 2/2 全命中、页码齐全 | ✅ 属实 |
| "论文框架生成 4481 字" | 实测 4515 字 | ✅ 字数属实，**但内容是占位符废稿**（见 §6.2） |
| "综述 1390 字" | 实测 2669 bytes（中文约 1300+ 字） | ✅ 量级属实 |
| "`_node_year` 修复后综述 1091→1390 字、出现『2017年』" | 确定性验证 timeline 从 `[]` → `[(2017),(2018)]`，实测综述确有"2017年…2018年" | ✅ 属实 |
| "429 退避 6/6 mock PASS" | 我独立 mock 复现，**6/6 一致** | ✅ 属实 |
| "磁盘缓存 9.24s→0.0000s" | 落盘 ✅、0.0000s ✅、网络调用增量 0 ✅；但冷启动我实测 **16.81s**（三级全 429 场景），9.24s 只在特定网络状态下成立 | ⚠️ 落盘/命中属实，冷启动数字**场景依赖**，报告未注明 |
| "R5：Attention 那篇在 `assess_reference` 中不计分" | 实测 `real_citations=None` ✅ | ✅ 属实，**但只修了一个消费者**，`detect_frontier` 仍泄漏（§4.2d 为 🔴） |
| "OCR 首次实测通过" | 确实是首次（此前 Key 为占位串） | ✅ 属实 |
| "未污染 storage/" | `_verify_live_api.py` 用 `make_ctx()` 改写模块级常量到临时目录 ✅；但 R5 的缓存按设计落在 `storage/cache/`（已 gitignore） | ✅ 属实 |
| 未声称但值得指出：**没有留存 19/19 的运行日志** | `grep` 全仓库无产物 | 🟡 结论不可复现 |

---

## 8. 给下一轮的硬性整改清单（按优先级）

1. **🔴 修 `services/graph.py:408`** —— `detect_frontier` 必须像 `assess_reference` 一样拒绝
   `title_attribution_unconfirmed` / `low_confidence` 的被引数，否则 UI 会显示"BERT 被引 55 次"。
2. **🔴 修 `services/library.py:105`** —— 跨行标题合并，先把 BERT 的名字改对；这条会同时改善综述、图谱、查重、UI 四处。
3. **🔴 重写 `_verify_live_api.py` 的 4 条放水断言**（`:255` 硬编码、`:412`、`:437`、`:441`），
   并把写作框架的 References 真实性（复用 `validate_reference_whitelist`）做成硬断言。
4. **🔴 graph 组换 ≥20 篇 fixture**，断言 `n_nodes>=20`、`bridges>=1`、build 耗时阈值——
   否则赛题"批量文献引用图谱挖掘"这条**根本没被验证过**。
5. **🟡 收紧 `:265` 为全命中、`:393` 为真实统计量、`:353` 为"两篇都提到且做了对比"**。
6. **🟡 沉淀证据**：把本轮 stdout + 生成的综述/OCR/框架原文提交到 `docs/review/round7_evidence/`，
   让"19/19"可被第三方复算。

---

*评委 G · 2026-10-12 · 所有结论均附行号或实测输出；实测脚本留存于 `C:/tmp/judgeG/t1~t6`，未改动任何项目代码。*

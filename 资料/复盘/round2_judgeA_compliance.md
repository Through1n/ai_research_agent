# 第二轮 · 评委A 赛题合规验收审计报告

- 评审对象：考生1（`services/parsing.py`、`services/library.py`）+ 考生2（`services/rag.py`、`core/guardrails.py`、`services/query_expansion.py`、`ui/page_qa.py`）
- 评审原则：**只做只读审计**。未修改任何 `.py/.md/.json/.html`，未执行 git 操作，未 pip install，未启动 streamlit。
- 验证脚本全部写在系统临时目录 `C:/Users/Administrator/AppData/Local/Temp/ja_probe*.py`。
- 运行环境修正（重要）：两位考生报告均称"本机缺 `pymupdf`/`chromadb`/`openai`/`rank_bm25`，无法真实运行"。**这是误判**——他们用的是 base `anaconda3/python.exe`；项目真实环境是 `启动科研助手.bat` 里 `conda activate ai_research` 指向的
  `C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`，该环境 `pymupdf / numpy / chromadb / openai / rank_bm25 / PIL / psutil / streamlit` **全部齐备**，且 `python -m unittest discover -s tests -v` **18/18 通过**。本报告全部实测均在该环境下完成。

---

## 一、总评

| 考生 | 真实性 | 有效性 | 赛题契合度 | 鲁棒性 | 无副作用 | **加权总分** |
|---|---|---|---|---|---|---|
| 考生1（文档感知） | 9 | 4 | 5 | 5 | 4 | **5.8 / 10** |
| 考生2（抗幻觉 RAG） | 10 | 8 | 8 | 8 | 10 | **8.8 / 10** |

（权重：真实性 30%、有效性 30%、赛题契合度 15%、鲁棒性 15%、无副作用 10%）

- **考生1（5.8）**：代码写得诚实，T1–T6 六个任务**全部真实落地、无 stub**，T3（541 页工程化）实测扎实（541 页 / 15.9s / 0 失败页 / 1301 chunks / 11 次进度回调），T5 参考文献切分、T6 统一 LLM 入口也都实测有效。但**唯一的 P0 卖点「双栏 PDF 提取」在真实语料上是反向有害的**：在演示用的 arXiv 论文上误判率 46.7%~100%，误判页产生 20%~42% 的乱序，直接污染入库文本；同时新引入的 `_empty_prepared` 吞异常导致损坏 PDF 被静默入库。**一句话：工程量足，但核心功能没在真实数据上验证过就宣称"无省略项"，P0 项实际翻车。**
- **考生2（8.8）**：**零虚报**。逐条核对报告里 20 余处"文件:行号"，函数全部存在且逻辑完整，行号基本准确。实测抗幻觉闭环每一环都真的 work：引用校验能区分有依据/编造、拒答闸门真触发且 0 次 LLM 调用、修复环真上限 1 次、source_filter 真生效、expand_queries 真返回 `List[str]` 且 dense 路真消费变体。两处实质短板：内容一致性校验对**纯中文句完全失效**（演示的大多数场景），以及拒答阈值被 BM25 归一主导、名不副实。**一句话：本轮最扎实的一块，扣分点集中在"中文抗幻觉"这个赛题主战场上。**

---

## 二、扣分清单（按严重度排序）

| 严重度 | 问题描述 | 文件:行号 | 证据（实测输出 / 代码片段） | 扣分 | 建议怎么修 |
|---|---|---|---|---|---|
| 🔴阻断 | **双栏检测在真实语料上大规模误判，并把单栏页面的阅读顺序打乱**。这是"多模态细粒度文档感知"的 P0 展示项，演示时评委点开任何一篇演示论文的溯源原文片段就能看到错乱文本 | `services/parsing.py:96-176`（`_detect_two_columns` / `extract_page_blocks_column_aware`） | 全量扫描（真值：这三份都是**单栏**排版）：<br>`1706.03762v7.pdf` 15 页 → 判定双栏 **7 页（46.7% 误判）**，p14/p15 逆序率 **42.1%**、p13 **37.4%**<br>`1810.04805v2.pdf` 16 页 → 判定双栏 **16 页（100% 误判）**，p1 逆序率 **36.4%**、p15 **25.6%**<br>`UnderstandingDeepLearning`(541p) → 判定双栏 **205 页（37.9% 误判）**，p137 逆序率 **38.5%**<br>具体错乱（v7 p1，单栏）：栏感知输出顺序为 `[0]arXiv:1706.03762v7…` `[1]Ashish Vaswani∗` `[2]Google Brain avaswani@…` `[3]Llion Jones∗…` `[4]†Work performed while…` `[5]…copyright…` `[7]Attention Is All You Need` —— **页脚脚注被提到论文标题之前，作者被拆成两截**。<br>v7 p5：三个小节标题 `3.2.3 / 3.3 / 3.4` 被整体提到正文之前（输出位置 1/3/4，正文被挤到 5 之后） | -2.5（有效性/契合度/无副作用三处计） | **根因**：新版删掉了旧版那条最有效的否决规则。旧版 `dist/AI4SResearchAgent/_internal/app_bundle/services/parsing.py:59-82` 是：<br>`crossed = any(b[0] < x_mid < b[2] for b in text_blocks)` → 有块跨中线即判单栏。<br>新版 `parsing.py:106` 先把 `block_width > width*0.62` 的宽块**剔除出候选**，再用剩下的窄块做双峰聚类，最后**从不检查被剔除的宽正文块是否横跨 split_x**。<br>实测证实：v7 的 7 个误判页「横跨分栏线的块数」**全部 > 0（0/7 满足双栏必要条件）**；UDL **0/205**；v7 p5 有 **17 个块横跨 split_x=275.8**。<br>**修法**：在 `_detect_two_columns` 返回 `True` 前加回否决——`if any(b["bbox"][0] < split_x < b["bbox"][2] for b in blocks): return False, width/2.0`（或要求横跨块占比 < 5%）。合成真双栏 PDF 上正向路径已验证可用（column 序列 `[0,0,0,0,0,0,1,1,1,1,1,1]`，split_x=314.5），加回否决不会破坏它 |
| 🔴阻断 | **损坏/加密/打不开的 PDF 被静默入库为 0 页幽灵文献**（本轮新引入的回归）。旧版是抛异常→UI 弹友好错误；新版吞掉异常→UI 走 `_commit` 分支 | `services/library.py:97-106`（`_empty_prepared`）、`:216`、`:242`；消费方 `ui/page_library.py:64-79` | 实测 `prepare_pdf(ctx, data=b"%PDF-1.4\nthis is definitely not a valid pdf body\n%%EOF\n", filename="broken.pdf")`：<br>`返回 keys: ['_chunks','dup_evidence','dup_name','dup_sim','embedding','failed_pages','filename','fingerprints','full_text','page_count','page_texts','peak_mem_mb','raw','scanned','toc']`<br>`prepared.get('error') = None` ← **没有 `error` 键**<br>`prepared.get('scanned') = False`，`prepared.get('dup_name') = None`<br>→ `page_library.py:66 elif prepared.get("error")` 不命中 → 落到 `:79 else: _commit(ctx, prepared)`<br>`commit 后 ctx.papers: {'broken.pdf': (0, 0)}`（0 页、0 chunk 的空文献进了库）<br>对照旧版 `dist/.../library.py:104`：`doc = parsing.open_doc(raw)` 裸调用，异常由 `page_library.py:80 except` 捕获并 `friendly_error` 提示 | -1.5（鲁棒性/无副作用） | `_empty_prepared` 返回值里补 `"error": str(error)`；或保留旧行为——文件级失败（读文件失败 / 打开失败）直接向上抛，只让**页级**失败走 `_empty_prepared`。同时 `page_library.py` 应在 `_commit` 前校验 `page_count>0 and chunks` |
| 🟡重要 | **`failed_pages` / `peak_mem_mb` 只写不读**，全仓库无消费方（grep 仅命中 `library.py` 自身）。541 页场景若大量页超时，用户看到的是"✅ 已入库" | `services/library.py:105`、`:258`、`:270`、`:335` | 实测：15 页全部注入 2s 慢页 + `timeout_per_page=0.05` → `failed_pages 数量=15`、`page_count=15`、`非空页文本数=0`，但 UI 无任何提示，`_commit` 照常入库一个 15 页 / 0 chunk 的空壳论文 | -0.5（鲁棒性） | `ui/page_library.py` 的 `_commit` 前：`if prepared.get("failed_pages"): st.warning(f"⚠️ {len(...)} 页解析失败/超时：" + …)`，并给出"重新上传 / 关闭其他程序"引导 |
| 🟡重要 | **TOC 回退在无书签论文上产出大量噪声**，而 UI 会把条数当成绩展示 | `services/parsing.py:391-468`（`infer_headings_from_text`）、`:672-682`（`_HEADING_NUMBER_RE`）；展示点 `ui/page_library.py:211-212` | 实测 `1810.04805v2.pdf`（16 页、**0 条内置书签**）→ `extract_toc` 返回 **140 条（平均 8.8 条/页）**，前 30 条里包含：<br>`(l1,p1) 'arXiv:1810.04805v2 [cs.CL] 24 May 2019'`（时间戳当一级标题）<br>`(l3,p1) 'Jacob Devlin' / 'Kenton Lee' / 'Kristina Toutanova' / 'Google AI Language'`（作者名、单位当三级标题）<br>`(l3,p1) '{jacobdevlin,mingweichang,kentonl,kristout}@google.com'`（邮箱）<br>`(l1,p2) '2014) methods.'`、`(l1,p3) '2017) and machine translation (McCann et al.,'`（**正文残句当一级标题**）<br>`(l2,p1) 'Encoder' / 'Representations' / 'Transformers.'`（标题被按词拆成 3 条）<br>UI 会显示 `✅ 解析完成：标题树 140 项 · …`<br>根因之一：`_HEADING_NUMBER_RE = ^\s*(\d+(?:\.\d+){0,5})[.)]?\s+\S` 会把 `2014) methods.` 这类"年份 + 右括号"开头的正文行匹配成编号标题 | -0.8（有效性/契合度） | ①编号规则排除 `19xx/20xx` 年份（`(?!(?:19\|20)\d{2}[.)])` 前置否定）；②候选行追加约束：行尾不为句号/逗号、该行在页面右侧无续行、字号差 ≥1.5pt（当前 `body_size+max(0.8, body*0.08)` 太松）；③同页相邻候选按 y 相邻 + 同字号合并（解决标题被拆行）；④输出前按 `(page, title)` 去重并按页排序，条数上限（如 ≤ 页数×1.5） |
| 🟡重要 | **内容一致性校验对纯中文句完全失效**，UI 却照样打"✅ 引用校验通过"——这是赛题"抗幻觉"主战场上的盲区 | `core/guardrails.py:35-49`（`_ANCHOR_TOKEN` / `_anchor_tokens`）、`:99-100`（`<2 个锚点直接 skip`）；`ui/page_qa.py:231` | 实测（source_texts 为含 P100/28.4 BLEU 的英文原文）：<br>`validate_citations("该模型使用了八块英伟达显卡进行训练【1】，参数量非常大【2】。", 2, source_texts=src)` → **`[]`（判为通过）** ← 一句完全编造的中文结论被放行<br>对照：`"Training used 512 Google TPU v4 chips【1】…"` → `['可疑引用【1】：…重叠仅 10%（阈值 15%）…']` ✅<br>`"训练使用了 512 块 TPU v4 芯片【1】，BLEU 为 41.5【1】…"` → `['…重叠仅 6%…','…重叠仅 0%…']` ✅（含数字锚点时生效）<br>原因：`_ANCHOR_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-\.]*")` 不收汉字；纯中文句锚点数 < 2 → `check_citation_support` 直接 `continue` | -0.8（有效性/契合度） | 给中文加 2-gram 汉字窗口锚点（`re.findall(r"[\u4e00-\u9fff]", text)` 后滑窗成 bigram），**覆盖率口径不变**（分子/分母都用答案侧），阈值可保持 0.15；或者在无法判定时不要显示"✅ 通过"，改为"⚠️ 内容一致性未覆盖（纯中文句），仅校验了角标范围" |
| 🟡重要 | **拒答闸门 0.35 名不副实**：实际等价于"BM25 是否零命中"，不是语义相关度阈值 | `services/rag.py:168-222`（`score_sources`）、`:411`（闸门） | `score_sources` 里 `sparse[c] = best[i]/mx`，**top chunk 的 sparse 归一恒等于 1.0** → 只要 BM25 有任一非零命中，`top_score ≥ 0.5×1.0 = 0.5 > 0.35` 必然放行；反之 BM25 全零时 `score = 0.5×cos`，中文查询 vs `chromadb DefaultEmbeddingFunction`（英文 MiniLM）余弦约 0.05~0.2 → `top_score ≈ 0.03~0.1` 必然拒答。<br>实测（fake embed + 真实 BM25Plus）：无关查询 `量子纠缠在拓扑绝缘体中的应用…` → `top_score=0.1820` → 拒答，**LLM 调用次数=0** ✅；相关查询 → `top_score=0.7422` → 放行。闸门功能成立，但**阈值失去区分度** | -0.6（有效性） | BM25 归一改为对"本次会话历史最高 BM25 分"或"全局固定参考分"归一，而不是对当次 `max` 归一；或提高 dense 权重到 0.6 并单独设 `dense_floor`。若来不及改，至少在 debug 区把 `dense/sparse` 两个分量都显示出来，别只显示合成分 |
| 🟡重要 | **`para_idx` 对跨页 chunk 语义错误**（考生1↔考生2 的接口错位） | `services/rag.py:132-158`（`annotate_paragraphs`按 `(source,page)` 分组）；`services/parsing.py:308-354`（chunk 可跨页） | 实测 `1706.03762v7` 15 页 → 36 个 chunk，其中 **11 个（30.6%）`page != page_end`**，如 `page=1 page_end=2`、`page=4 page_end=5`。<br>`annotate_paragraphs` 把整块算作**起始页**的段落 → `chunk2: page=1-2 para_idx=21 para_span=3`，UI 显示"第 1 页 · 第 21 段"，但该 chunk 有内容落在第 2 页，跳转会指错位置 | -0.5（有效性/交叉接口） | `annotate_paragraphs` 里对 `page != page_end` 的 chunk 只在 `page == page_end` 时给 `para_idx`，否则只给 `page`/`page_end` 区间；或由考生1 在 `build_chunks_from_texts` 里保证 chunk 不跨页（多页文本各自 flush） |
| 🟡重要 | **150 字 chunk 重叠导致段落计数膨胀 + 检索 top-k 冗余**（沿用旧逻辑，非本轮引入，但被本轮的 `para_idx` 放大） | `services/parsing.py:348-351` | 实测：36 个 chunk 中 **35/35** 相邻对满足 `chunks[i].text.startswith(chunks[i-1].text[-150:])`。<br>`annotate_paragraphs` 用 `_split_paragraphs` 计数时重叠段被重复计入 → 页内最大段号被 inflation（第 1 页标到 **23 段**）。同时 RAG 的 top-6 里有大量 150 字重复文本，白占上下文窗口 | -0.4（有效性） | 重叠只在"`page` 相同且相邻"时补；或把 `page_offset` 批次边界处的重叠去掉（当前 `library.py:288-291` 已在批次间补了一次重叠，与 `build_chunks_from_texts` 内部的重叠叠加） |
| 🟢轻微 | 报告事实错误：两位考生都称"环境缺依赖、tests 无法真实运行" | `资料/复盘/round1_worker1_doc.md:148,179`；`round1_worker2_rag.md:6,188` | 实测 `C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m unittest discover -s tests -v` → **`Ran 18 tests in 11.741s / OK`**（18/18 通过）。两位考生用的是 base `anaconda3/python.exe`，而项目启动脚本 `启动科研助手.bat` 用的是 `conda activate ai_research` | -0.2 | 后续自验统一用 `anaconda3/envs/ai_research/python.exe`；`tests/test_core.py` 实际覆盖了 `retrieve / validate_citations / _offline_expand / check_duplicate`，完全可以当回归基线用 |
| 🟢轻微 | `_GLOSSARY` 键带前导空格，扩展串输出带空格 | `services/query_expansion.py:19`（`" GPU": " gpu gpu graphics"`）、`:23`（`" BLEU": " bleu"`） | 实测 `_offline_expand('GPU 是什么')` → `' gpu gpu graphics'`（前导空格）；`expand_queries` 返回的 variant 在 `page_qa.py:123` 的调试区直接展示，观感不佳 | -0.1 | 去掉 key/value 的前导空格（不影响 `key in low` 的匹配语义） |
| 🟢轻微 | 超时文案显示的是 clamp 后的值，与传入值不符 | `services/library.py:168`、`:177` | 传入 `timeout_per_page=0.05`，实测 `failed_pages` 里写的是 `'单页处理超过 0.1 秒，已跳过'`（`timeout = max(0.1, float(...))` 被钳到 0.1） | -0.1 | 文案改用原始 `timeout_seconds` 打印，或去掉 0.1 的下限钳制 |
| 🟢轻微 | 短 PDF 没有中间进度，进度条 0→100% 跳变 | `services/library.py:281`（`PDF_PROGRESS_INTERVAL = 50`） | 实测 15 页 PDF：新三参回调只收到 `[(15, 15, '已处理 15/15 页')]`；旧两参回调只收到 `[(1.0, 15)]` | -0.1 | 间隔改为 `max(1, min(50, total // 20))`，保证至少 20 次回调 |
| 🟢轻微 | `qa_runs` 无条数上限、`query_params` 写入后长期残留 | `ui/page_qa.py:203`（append）、`:88-90`（写 query_params） | 考生2 报告"仍存在的风险点 3、4"已自陈，代码确实未做限制 | -0.2 | `qa_runs` 保留最近 20 条；`jump_page/paper` 在阅读页消费后清空 |

---

## 三、虚报核查

**结论：未发现 stub、空实现、只在注释里写实现的情况。** 两位考生声称的每一个函数都真实存在且逻辑完整。但发现 3 处"声称与实测不符"：

| # | 报告声称 | 实测结论 | 定性 |
|---|---|---|---|
| 1 | 考生1（T1）："对窄文本块中心做一维双峰聚类；同时检查左右簇数量、中心间距、中线位置和纵向重叠，**降低单栏误判**"；风险点只写"个别杂志版式仍可能误判" | 在**普通单栏 arXiv 论文**上误判率 **46.7%（v7）/ 100%（BERT）/ 37.9%（UDL 541p）**，不是"个别"。误判页产生 20%~42% 逆序 | **声称与实测不符（严重低估）**，代码存在但效果反向 |
| 2 | 考生1："功能任务 T1-T6 均已实现，无主动省略项" | T1 产出的 `column` 字段**全仓库无任何下游消费方**（grep 仅命中 `parsing.py` 内部）；`build_chunks_from_texts`/`commit` 都不写入 chunk，chunk 里没有 `column`。功能只"实现了一半"（排序生效，字段无人用） | **功能未闭环**，非 stub 但等于白做 |
| 3 | 考生2："本机 anaconda3 缺 `chromadb`/`openai`/`rank_bm25`，`tests/test_core.py` **无法真实运行**（ImportError: rank_bm25）" | 项目真环境 `anaconda3/envs/ai_research` 依赖齐全，`unittest discover -s tests` **18/18 通过** | **报告事实错误**（用错了解释器），考生1 同款表述同样错误 |

**未发现虚报的具体核对项**（逐条打开代码确认存在且非空）：

- 考生1：`_detect_two_columns`(96) / `extract_page_blocks_column_aware`(154) / `extract_page_text_column_aware`(178) / `native_page_texts`(194) / `_blocks`(294) / `_caption_blocks`(497) / `_caption_distance`(511) / `_nearest_caption`(528) / `extract_figures`(550) / `_extract_page_from_raw`(135) / `_extract_page_with_timeout`(154) / `_notify_pdf_progress`(109) / `prepare_pdf`(200) / `commit`(340) / `heading_page_data`(357) / `infer_headings_from_text`(391) / `extract_toc`(453) / `split_reference_entries`(662) —— 全部存在并有实质逻辑。
- 考生2：`anchor_card`(51-77) / `_set_jump`(80-95) / `_render_source`(98-116) / `annotate_paragraphs`(rag:132-158) / `build_context`(rag:315-325) / `validate_citations`(guard:127-150) / `check_citation_support`(guard:83-105) / `_overlap`(guard:51-65) / `_anchor_tokens`(guard:38-49) / `validate_grouped_citations`(guard:152+） / 拒答闸门(rag:411) / `_refuse`(rag:368-371) / 修复环(rag:418-429) / `retrieve_per_paper`(rag:272-310) / `build_grouped_context`(rag:328-342) / `compare_messages`(rag:353-363) / `expand_queries`(qe:134-174) / `_llm_expand`(qe:104-131) / `_offline_variants`(qe:65-76) / `_offline_expand`(qe:54-62) / `_llm_complete`(rag:26-44) / `_complete`(qe:78-90) —— 全部存在并有实质逻辑，行号与报告基本吻合。

---

## 四、交叉接口问题（考生1 ↔ 考生2）

| # | 问题 | 证据 | 结论 |
|---|---|---|---|
| 1 | **chunk 字段契约本身是对齐的** ✅ | 考生1 的 `commit`（`library.py:377-379`）写入 `{id, source, page, page_end, text}`；考生2 的 `annotate_paragraphs`（`rag.py:143-147`）只读 `source/page/id/text`，并在**浅拷贝**上加 `para_idx/para_span/score`，不回写 `ctx.chunks`。实测 `sources[0] keys = ['id','page','page_end','para_idx','para_span','score','source','text']`，`ctx.chunks` 未被污染 | 通过，不扣分 |
| 2 | **考生1 的 `column` 未进入 chunk，考生2 也未消费** | 全仓库 grep `column` 仅命中 `parsing.py` 内部（89/155/158/166/169/170/172/280/286/287/288）。`build_chunks_from_texts` 返回的 chunk 只有 `text/page/page_end` | 🟡 考生1 的 `column` 是死字段；考生2 报告"若后续 chunk 增加 section/para 原生字段可直接改读"属未来式，当前未对接。**不算扣分**（报告未声称已对接），但 T1 的价值只剩"影响排序"，而排序恰好是坏的 |
| 3 | **跨页 chunk（30.6%）+ `para_idx` 按 `(source,page)` 分组 → 段落坐标错位** | 见扣分清单 🟡#7 | 🟡 真实错位，需修 |
| 4 | **150 字重叠 → `para_idx` 计数膨胀** | 见扣分清单 🟡#8 | 🟡 考生2 报告风险 5 已自陈"估算值"，实测确认存在 |
| 5 | **`READING_PAGE_IDX = 3` 与菜单索引一致** ✅ | `ui/page_qa.py:24 READING_PAGE_IDX = 3`；`ui/sidebar.py:17 MENU[3] = "📖 3. 版式阅读与智能翻译"`；`sidebar.py:168 st.query_params["p"] = MENU.index(...)` | 通过，不扣分 |
| 6 | **`qa_jump_page` 消费方已落地（考生2 报告"未完成项 1"已过时）** ✅ | 实测 `ui/page_reading.py:453-480`（`JUMP_KEY = "qa_jump_page"`）、`:622-669`（`read_jump_page` / `read_jump_para`）、`ui/common.py:266 jump_banner()` 均已消费该 key，**且已读 `para_idx`**（→ `read_jump_para`）。考生2 报告"段落级定位不会生效"的判断现在不成立 | 不算扣分，但**报告需更新**，否则会被评委误判为"链路断了" |
| 7 | **阅读页 `query_params` 兜底链路存在** ✅ | `ui/page_reading.py:503-504, 667-669` 消费 `query_params["jump_page"] / ["paper"]` | 通过 |

---

## 五、实测记录

环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（Python 3.11.16），`PYTHONIOENCODING=utf-8`，工作目录 `C:/Users/Administrator/ai_research_agent`。脚本全部位于 `C:/Users/Administrator/AppData/Local/Temp/`。

### 0. 基线（证明可真实运行）
```
$ python -m unittest discover -s tests -v
Ran 18 tests in 11.741s
OK
```
全部 8 个受审模块 `importlib.import_module` 均 OK（`services.parsing / services.library / services.rag / core.guardrails / services.query_expansion / ui.page_qa / ui.page_reading / ui.common`）。

### 1. `ja_probe1_cols.py` — 真实 PDF 双栏判定扫描（输入：论文样本/ 与 演示语料/ 下 6 份 PDF，每份前 40 页）
```
1706.03762v1.pdf                    pages= 15  前40页判定双栏=  8
1706.03762v1_img.pdf                pages= 15  前40页判定双栏=  0   (扫描件，无文本层)
1706.03762v7.pdf                    pages= 15  前40页判定双栏=  7
1810.04805v2.pdf                    pages= 16  前40页判定双栏= 16
LongBook_540p_fixture.pdf           pages=540  前40页判定双栏=  0
UnderstandingDeepLearning_...pdf    pages=541  前40页判定双栏=  4
```

### 2. `ja_probe2_detail.py` — 逐页打印 column / bbox / 文本（输入：v1、v7、BERT 的第 1/2/5 页）
关键输出（v7 p1，单栏，被判 `two_col=True split_x=238.1`）：
```
column 序列: [0,0,0,0,0, 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1]
col=0 c= 24.3 y0=213.9 | arXiv:1706.03762v7  [cs.CL]  2 Aug 2023
col=0 c=168.4 y0=233.5 | Ashish Vaswani∗
col=0 c=166.4 y0=246.0 | Google Brain avaswani@google.com
col=0 c=168.7 y0=283.5 | Llion Jones∗ Google Research llion@google.com
col=0 c=200.2 y0=687.9 | †Work performed while at Google Brain. ‡Work performed …
col=1 c=305.7 y0=148.8 | Attention Is All You Need          ← 标题被排到了脚注之后
col=1 c=306.7 y0=413.3 | The dominant sequence transduction models are based on …  (摘要)
```
（v7 p5，单栏，被判 `two_col=True split_x=275.8`）：`3.2.3 / 3.3 / 3.4` 三个小节标题被整体前移到正文之前。

### 3. `ja_probe3_text.py` — 栏感知输出 vs PyMuPDF 原文顺序的逆序率
```
=== 1706.03762v7 page 1: 参与比较=5 逆序对=3 （逆序率 30.0%）
=== 1706.03762v7 page 5: 参与比较=10 逆序对=3 （逆序率 6.7%）
=== 1810.04805v2 page 2: 参与比较=10 逆序对=0 （逆序率 0.0%）
=== 1810.04805v2 page 3: 参与比较=11 逆序对=4 （逆序率 7.3%）
```
BERT p3 实测错乱：栏感知输出前 10 块是 `NSP Mask LM Mask LM / C / T1 / TN / T1’… / TM’ / T[SEP]… / BERT / E1 EN E1’ EM’ …`，而 PyMuPDF 原文顺序是 `BERT BERT / E[CLS] E1 E[SEP]… / C T1 T[SEP]… / [CLS] Tok 1 [SEP]… / Question Paragraph / Start/End Span / BERT / …` —— **Figure 1 的图注文字被打散重排**。

### 4. `ja_probe4_falsepos.py` — 全量误判率 + 合成真双栏 PDF 验证正向路径
```
1706.03762v7.pdf                 总页数=15   判定双栏=7    误判率=46.7%
    乱序最严重 5 页(页码,块数,逆序对,逆序率): [(14,110,2522,0.4207),(15,110,2522,0.4207),(13,69,877,0.3738),(7,17,40,0.2941),(1,20,48,0.2526)]
1810.04805v2.pdf                 总页数=16   判定双栏=16   误判率=100.0%
    [(1,12,24,0.3636),(15,32,127,0.2560),(3,24,60,0.2174),(13,39,138,0.1862),(16,26,24,0.0738)]
LongBook_540p_fixture.pdf        总页数=540  判定双栏=0    误判率=0.0%
UnderstandingDeepLearning_...pdf 总页数=541  判定双栏=205  误判率=37.9%
    [(137,81,1248,0.3852),(414,58,614,0.3714),(475,67,820,0.3709),(62,61,661,0.3612),(262,16,43,0.3583)]

B) 合成真·双栏 PDF（左栏 6 段 / 右栏 6 段）
   _detect_two_columns -> is_two_column=True split_x=314.5
   column 序列: [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1]
   LEFT paragraph number 1..6 → RIGHT paragraph number 1..6   （正向路径正确）
```

### 5. `ja_probe10_rootcause.py` — 误判根因（统计"横跨 split_x 的块数"）
```
1810.04805v2.pdf  误判 16 页；横跨块数最多的页: (5,19,9,268.6) (1,12,6,256.5) (16,26,5,279.9) …
                  横跨块数==0 的页（真正像双栏）: 9/16
1706.03762v7.pdf  误判 7 页；  (5,24,17,275.8) (7,17,10,253.3) (1,20,9,238.1) (4,17,9,303.5) (14,110,5,311.8)
                  横跨块数==0 的页: 0/7        ← 7 页全部不满足双栏必要条件
UnderstandingDeepLearning 误判 205 页；(296,131,20,306.2) (367,56,20,268.5) (57,35,19,311.1) …
                  横跨块数==0 的页: 0/205      ← 205 页全部不满足
```

### 6. `ja_probe5_doc.py` — T2 图注 / T4 标题 / T5 参考文献 / T3 长文档
```
T2) 1706.03762v7: 提取到 3 张图
     page=3 index=1 caption='Figure 1: The Transformer - model architecture.' bbox=(196.6,72.0,415.4,394.4)
     page=4 index=2 caption='Figure 2: (left) Scaled Dot-Product Attention. (right) Multi…' bbox=(175.0,94.0,239.0,221.3)
     page=4 index=3 caption=''  bbox=None                      ← 多子图未覆盖
     无图注的图片数: 1/3；全篇 _caption_blocks 识别到 5 个图注块
    1810.04805v2: 提取到 8 张图, 带 caption 的 1 张            ← 7/8 无图注

T4) 1706.03762v7: 内置书签 22 条 -> 直接返回书签 (Introduction/Background/Model Architecture/…)
    1810.04805v2: 内置书签 0 条 -> 走推断, 返回 140 条（含作者名/邮箱/arXiv 时间戳/正文残句）

T5) 输入尾部 6000 字 -> 切出 17 条
     - [25] Mitchell P Marcus, Mary Ann Marcinkiewicz, and Beatrice Santorini. Building a large annotated corpus of e…
     - [30] Ofir Press and Lior Wolf. Using the output embedding to improve language models. arXiv preprint arXiv:160…

T3) LongBook_540p_fixture.pdf (540 页) prepare_pdf 耗时 1.5s（3ms/页）
    chunks=810  failed_pages=0  toc=0
    进度回调 11 次: [(50,540,…),(100,540,…),…,(540,540,'已处理 540/540 页')]
    每页独立 open_doc 平均 2.2 ms
```

### 7. `ja_probe6_big541.py` — 真实 541 页大书（`UnderstandingDeepLearning_02_09_26_C.pdf`，22MB）
```
真实 541 页 PDF：每页独立 open_doc 平均 24 ms；打开一次整本文档 16 ms
prepare_pdf(实时): 541 页, 耗时 15.9s, 平均每页 29 ms
   chunks=1301  failed=0  toc=181
   进度回调 11 次: [(50,541,'已处理 50/541 页'), …, (541,541,'已处理 541/541 页')]
TOC 推断质量（前 10 条，质量好）:
   (l1,p5) 'Preface' / (l1,p11) 'Acknowledgements' / (l1,p15) 'Introduction'
   (l2,p15) 'Supervised learning' / (l2,p21) 'Unsupervised learning' / (l2,p25) 'Reinforcement learning'
   (l2,p26) 'Ethics' / (l2,p29) 'Structure of book' / (l2,p29) 'Other books' / (l2,p30) 'How to read this book'
```

### 8. `ja_probe8_edge.py` — 异常路径 / 兼容 / 超时 / TOC
```
【G】损坏 PDF -> 返回 keys 无 'error'；prepared.get('error')=None
    ==> UI 实际走向: ❌ _commit(ctx, prepared) —— 空论文被静默入库
    commit 后 ctx.papers: {'broken.pdf': (0, 0)}
【H】旧两参回调收到: [(1.0, 15)]      新三参回调收到: [(15, 15, '已处理 15/15 页')]   （两参兼容生效）
【I】15 页全部 2s 慢页 + timeout=0.05s -> 耗时 1.6s
    failed_pages 数量=15 示例=[{'page': 1, 'error': '单页处理超过 0.1 秒，已跳过'}, …]
    page_count=15  非空页文本数=0
【J】1810.04805v2（16 页、无书签）-> 推断出 140 条（平均 8.8 条/页），样例见扣分清单
```

### 9. `ja_probe7_rag.py` — 抗幻觉 RAG 全链路
```
【A】validate_citations 内容一致性（source_texts=[含 P100/0.9/0.98/28.4 BLEU 的英文原文, 含 110 million 的 BERT 原文]）
  有依据(英文)  -> []（通过）
  编造(英文)    -> ['可疑引用【1】：…重叠仅 10%（阈值 15%）…', '可疑引用【2】：…重叠仅 13%…']
  有依据(中文)  -> []（通过）
  编造(中文)    -> ['可疑引用【1】：…重叠仅 6%…', '可疑引用【2】：…重叠仅 0%…']
  纯中文无锚点  -> []（通过）   ← 盲区，"该模型使用了八块英伟达显卡进行训练【1】"被判通过
  角标越界      -> ['引用角标 [7] 超出有效来源范围(1~2)']
  两参兼容: validate_citations('结论【3】',2)=['引用角标 [3] 超出有效来源范围(1~2)']
  两参兼容: validate_citations('结论【1】',2)=[]                      （向后兼容成立）

【B】_overlap
  'Training used eight NVIDIA P100 GPUs.'    vs src[0] = 0.845
  'Training used 512 Google TPU v4 chips.'   vs src[0] = 0.188
  'The model achieves 28.4 BLEU on WMT 2014.'vs src[0] = 0.500

【C】expand_queries（ctx.llm.available=False，走离线兜底）
  '训练用了什么 GPU？' -> list len=2 ['训练用了什么 GPU？', 'gpu gpu graphics train training trained']
  'Adam 的 beta_1、beta_2 是多少？BLEU 分数分别是多少？' -> list len=3 [原查询, 'score bleu how many number', 'BLEU score machine translation']
  'BERT 参数量' -> list len=3 [原查询, 'parameter param parameters', 'bidirectional encoder representations from transformers BERT']
  '今天天气怎么样' -> list len=1 [原查询]                              （无命中，不硬凑）
  _offline_expand('GPU 是什么') = ' gpu gpu graphics'  (返回 str，旧签名兼容成立)
  retrieve('训练用了什么 GPU？'): sources=6
    debug['variants']=['gpu gpu graphics train training trained']
    debug['dense_variant_ranks'] 长度=2      ← dense 路确实消费了 2 个变体
    debug['top_score']=0.64
    sources[0] keys=['id','page','page_end','para_idx','para_span','score','source','text']
    para_idx/para_span: [(1,1),(2,1),(2,1),(2,1),(1,1),(1,1)]

【D】拒答闸门
  '量子纠缠在拓扑绝缘体中的应用与自旋霍尔效应的关系' -> top_score=0.1820 < 0.35 -> 拒答
      refused=True  LLM 调用次数=0  answer='未在文献库中找到可靠依据，为避免幻觉我不作答。（最高相关度 0.18 低于阈值 0.35，共 6 个候选片段）'
  'Transformer 训练用了什么 GPU'                 -> top_score=0.7422 -> 放行（LLM 调用 2 次）

【E】修复环上限（LLM 恒返回编造答案 '…【9】。'）
  repairs=1  LLM 调用次数=2  issues=['引用角标 [9] 超出有效来源范围(1~6)']   ← 不死循环

【F】范围锁定与分组对比
  source_filter='bert.pdf' -> 来源集合={'bert.pdf'}
  默认 None               -> 来源集合={'bert.pdf','att.pdf'}
  retrieve_per_paper      -> per_paper={'att.pdf': 3, 'bert.pdf': 3}
  build_grouped_context index keys=['P1-1','P1-2','P1-3','P2-1','P2-2','P2-3']
  validate_grouped_citations('结论【P1-1】【P2-1】')=[]
  validate_grouped_citations('结论【P9-9】')=['引用角标【P9-9】不存在于给定来源']
  validate_grouped_citations('无角标')=['答案未包含任何【Pn-m】引用角标，存在无依据生成风险']
```

### 10. `ja_probe9_chunk.py` — chunk 契约（跨页 / 重叠 / para_idx）
```
config: CHUNK_SIZE=1200 CHUNK_OVERLAP=150
1706.03762v7: 15 页 -> 36 chunks
  跨页 chunk（page != page_end）: 11/36 = 30.6%
    page=1 page_end=2 len=1163 / page=2 page_end=3 len=928 / page=3 page_end=4 len=1338 …
  相邻 chunk 头部 == 上一 chunk 尾部(150字) 的对数: 35/35
  chunk0: page=1-1 para_idx=1  para_span=16 实际段落数=16
  chunk2: page=1-2 para_idx=21 para_span=3  实际段落数=3   ← 跨页 chunk 被算作第 1 页的段
  标注出的'页内最大段号': {1:23, 2:25, 3:11, 4:12, 5:21, 6:19, 7:27, 8:21, 9:8, 10:22}
```

### 11. 静态核对
- `grep "ctx\.llm\.chat\|json_chat\|llm\.vision"` → 受审 6 个文件**零命中**（残留只在 `core/agent.py`、`core/planner.py`、`core/router.py`、`ui/page_library.py`、`ui/page_search.py`，均不在本轮范围）。T6 成立。
- `READING_PAGE_IDX=3`（`ui/page_qa.py:24`）↔ `ui/sidebar.py:17 MENU[3]="📖 3. 版式阅读与智能翻译"` → 一致。
- 旧版对照源：`dist/AI4SResearchAgent/_internal/app_bundle/services/{parsing,library}.py`（构建于本轮改动之前），用于确认"跨中线否决"被删除、`_empty_prepared` 为新引入、150 字重叠为既有逻辑。

---

## 六、给两位考生的修复优先级（赛前必做）

**P0（不做必翻车）**
1. `_detect_two_columns` 加回"任何块横跨 split_x 即判单栏"否决（`parsing.py:132` 之前）—— 一行代码，能把 BERT 的 100% 误判、UDL 的 37.9% 误判压到接近 0。
2. `_empty_prepared` 补 `"error"` 字段（`library.py:105` 附近）—— 一行代码，恢复损坏 PDF 的友好报错。
3. `guardrails._anchor_tokens` 加中文 2-gram 锚点，或把"无法判定"从"通过"改成"未覆盖"。

**P1（影响观感）**
4. `infer_headings_from_text` 排除 `19xx/20xx)` 开头的行 + 合并被拆行的标题（压掉 BERT 的 140 条噪声）。
5. `page_library` 消费 `failed_pages`。
6. `annotate_paragraphs` 对跨页 chunk 不输出 `para_idx`。

**P2**
7. 修 `_GLOSSARY` 前导空格、超时文案、短 PDF 进度间隔；`qa_runs` 加上限。

**报告层面**：两位考生都需订正"环境缺依赖、无法真实运行"的表述，并改用 `anaconda3/envs/ai_research/python.exe` 复跑 `tests/test_core.py`（18/18）作为回归基线；考生2 需删除"阅读页未消费 `qa_jump_page`"这条已过时的未完成项。

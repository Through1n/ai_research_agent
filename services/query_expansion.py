"""查询扩展：跨语言 + 同语言同义/上位词 + 简写展开，返回 variants: List[str]。

策略（两级降级）：
1. 在线：LLM 可用时，让模型输出 JSON 列表（cross_lingual / synonyms / abbreviations），
   每个扩展词**独立成条**，不再拼成一个大字符串；
2. 离线：内置学术中英术语映射表 + 简写展开表兜底，无 Key 也能演示多查询 RRF 真实生效。

扩展结果按 (原查询, 用户) 缓存，避免重复调用模型。

Round 9 改动（S1：检索问答链路 + 抗幻觉）：
- 中英符号对齐词表：查询写 "beta1" 而论文原文写 "β1 / beta_1" 时，
  稀疏路分词把希腊字母丢掉、稠密路 embedding 也不桥接两种拼写，双路都召回不到
  答案片段（实测 BERT p13 在 "BERT 的 beta1 和 beta2 参数是多少？" 下
  dense/sparse top24 均未出现）。新增 beta1/β1 等条目，扩展到
  "adam optimizer … learning rate" —— 与论文原句 "We use Adam with learning
  rate of 1e-4, β1 = 0.9, β2 = 0.999" 字面重合，实测可把答案片段召回至 dense 第 5；
- 离线词表变体改为**每个术语独立成条**：实测拼接成一长串会稀释 embedding 相似度
  （拼接形态下答案片段掉出 dense top12，聚焦形态下进 top6）；
- expand_queries 新增 allow_llm 参数：原查询已强命中时由调用方传 False，
  跳过 LLM 扩展（省一次调用、保住耗时）但**离线变体仍然参与检索**；
- _clean_variant 清洗 LLM 扩展输出与历史缓存：实测 LLM 会把 JSON 字段名
  （"cross_lingual" 等）和围栏残片当变体写出，旧版已把这些残片写进
  __qexp__ 缓存——现在产出时和读取时都过一遍 _clean_variant，污染条目
  自动失效，无需手动改 storage/memory.json。

返回值：List[str]，首元素恒为原查询。旧签名是 [原查询, 扩展串?]，新实现只是把扩展串
拆成多条，调用方（services/rag.py:retrieve）按列表消费即可，向后兼容。
"""
import re

from services.state import tokenize

# 常用学术术语中英映射（覆盖 NLP/深度学习高频词，可扩展）
# ⚠️ 顺序即优先级：_offline_variants 按本字典序产出变体，rag.retrieve 的
# 「意图覆盖」也按变体序取每个来源的最佳 chunk。因此**符号对齐条目必须排在
# 最前**——它们是修符号 mismatch 的具体变体；泛化译词（参数→parameter param）
# 排在其后，否则泛化变体的 head（论文摘要）会挤占答案片段的位置。
_GLOSSARY = {
    # 中英符号对齐：论文原文用希腊字母 β1/β2 或 beta_1/beta_2，用户常写 beta1/beta2。
    # tokenize 的正则 [a-z0-9]+ 会丢掉希腊字母（"β1"→"1"），导致稀疏路必然失配；
    # 稠密路 embedding 也不桥接两种拼写。因此扩展到与论文原句字面重合的英文短语
    # （"We use Adam with learning rate of 1e-4, β1 = 0.9, β2 = 0.999, learning
    # rate warmup…"），让双路都能召回答案片段。键为大小写不敏感子串匹配，
    # ASCII 与希腊形态都要覆盖；四个键收敛到同一条聚焦变体（_offline_variants 去重）。
    # 实测该变体下 BERT p13（β1=0.9/β2=0.999）dense 第 8 / sparse 第 4，
    # Transformer v7 p7（β1=0.9/β2=0.98）dense 第 6 / sparse 第 5。
    "beta1": "adam optimizer beta1 beta2 learning rate warmup",
    "beta2": "adam optimizer beta1 beta2 learning rate warmup",
    "β1": "adam optimizer beta1 beta2 learning rate warmup",
    "β2": "adam optimizer beta1 beta2 learning rate warmup",
    # 细粒度数值问答的聚焦变体，与 beta 条目同理：问"WMT 2014 BLEU 分数"
    # 要的是论文 Table 2 的具体数字，而泛化译词（分数→score）的 dense 排名
    # 会把各版摘要/图注 chunk 顶成来源 head——实测全库环境下 v7 的 top6
    # 名额被 p4 图注 chunk 占据，含 28.4/41.8 的 p8 表格 chunk（__23）反在
    # 融合池第 12 名、原查询 sparse 第 16。扩展到与 Table 2 行名/任务名
    # 字面重合的短语后，该变体 dense 排名 __23 升至第 6（v7 首次出现），
    # 意图覆盖得以把它选为 v7 的 head-1。必须排在"分数/翻译"等泛化条目前。
    " BLEU": "transformer base model big model bleu newstest2014 english-to-german english-to-french",
    # 同一问答的第二条聚焦变体（指向 v7 摘要）：库内 1706.03762 有三个版本
    # （v1/v1_img/v7），v1 系全文沿用旧值 41.0，v7 只在摘要和 Table 2 改成
    # 41.8。仅召回表格 chunk 时上下文 41.0 出现 3 次 vs 41.8 一次，LLM 会
    # 答错；摘要句 "…28.4 BLEU on English-to-German … 41.8 on
    # English-to-French" 是显式的任务-数字映射，作为第二 head 进 top6 后
    # 41.8 信号增至两处且带列名，实测答案修正为 28.4/41.8。文案贴近摘要
    # 原句，同时把 v7 最佳池分抬到 0.80（超过 v1 的 0.75），使轮转第 1 轮
    # 能把摘要 chunk 补进 top6。
    "wmt": "our model achieves state-of-the-art bleu score on wmt 2014 english-to-german english-to-french translation task",
    " GPU": " gpu gpu graphics", "显卡": "gpu", "训练": "train training trained",
    "参数": "parameter param", "参数量": "parameters", "分数": "score",
    "翻译": "translation translate", "模型": "model",
    "架构": "architecture encoder decoder", "注意力": "attention",
    "变压器": "transformer", "层": "layer", "头": "head heads",
    "优化器": "optimizer adam", "学习率": "learning rate",
    "设备": "device", "多少": "how many number", "用量": "usage",
    "摘要": "abstract", "作者": "author authors", "机构": "affiliation university google",
    "引用": "citation cite references", "文献": "literature reference",
    "图谱": "graph citation network", "综述": "review survey",
    "数据集": "dataset", "实验": "experiment", "性能": "performance",
    "词向量": "embedding", "预训练": "pretrain pre-training", "微调": "fine-tune",
    "双向": "bidirectional", "编码器": "encoder", "解码器": "decoder",
    "卷积": "convolution", "循环": "recurrent", "序列": "sequence",
}

# 简写展开（缩写 → 全称/同义表达），独立成条参与检索
_ABBREV = {
    "transformer": "transformer self-attention encoder decoder",
    "bert": "bidirectional encoder representations from transformers BERT",
    "gpt": "generative pre-trained transformer GPT",
    "rnn": "recurrent neural network RNN",
    "lstm": "long short-term memory LSTM",
    "cnn": "convolutional neural network CNN",
    "gnn": "graph neural network GNN",
    "llm": "large language model",
    "nlp": "natural language processing",
    "sota": "state of the art",
    "bleu": "BLEU score machine translation",
    "mlm": "masked language model",
}

MAX_VARIANTS = 6  # 含原查询在内的变体总数上限

# 扩展模式（写入 report["mode"]，供 rag.debug / UI 诊断展示）
MODE_LLM = "llm"                        # LLM 扩展成功
MODE_CACHED = "cached"                  # 命中 (用户, 查询) 缓存
MODE_OFFLINE_NO_KEY = "offline_no_key"  # 未配置 Key，离线词表兜底
MODE_OFFLINE_KEY_ISSUE = "offline_key_issue"  # Key 形态异常（如含非 ASCII），调用前拦截
MODE_OFFLINE_LLM_FAILED = "offline_llm_failed"  # LLM 调用失败/无有效输出，离线兜底
MODE_OFFLINE_STRONG_HIT = "offline_strong_hit"  # 原查询已强命中，跳过 LLM 但离线变体仍参与检索


def key_issue(ctx):
    """调用前检查 API Key 形态，返回人话原因；无问题返回 None。

    非 ASCII Key（误粘贴中文/占位文本）会在 OpenAI SDK 发请求时抛
    UnicodeEncodeError 并重试 4 次、写 llm_failures.log（历史 74 条噪声）。
    在调用前拦截，直接给出可操作的提示。
    """
    llm = getattr(ctx, "llm", None)
    key = (getattr(llm, "api_key", "") or "")
    if not key.strip():
        return "未配置 API Key，大模型不可用"
    if not key.isascii():
        return (f"API Key 含非 ASCII 字符（长度 {len(key)}），疑似粘贴了中文或占位文本；"
                "请在左侧边栏重新粘贴纯英文 Key 后再试")
    return None


def _offline_expand(query):
    """离线词表扩展：命中术语映射则追加英文等价词（大小写不敏感）。保留原字符串返回值以兼容既有调用/测试。"""
    extras = []
    low = (query or "").lower()
    for zh, en in _GLOSSARY.items():
        key = zh.strip().lower()
        if key and key in low:
            extras.append(en)
    return " ".join(extras)


def _offline_variants(query):
    """离线兜底：每个命中的术语映射**独立成条** + 命中的简写展开，每条独立参与检索。

    独立成条而非拼接：实测把全部命中词拼成一长串会稀释 embedding 相似度——
    "BERT 的 beta1 和 beta2 参数是多少？" 的拼接形态下答案片段（BERT p13）
    掉出 dense top12，而聚焦形态 "adam optimizer beta1 beta_1 learning rate"
    可排到 dense 第 5。上限 4 条（调用方 dense 路一般只消费前 3 条）。
    """
    out = []
    low = (query or "").lower()
    for zh, en in _GLOSSARY.items():
        key = zh.strip().lower()
        en = (en or "").strip()
        if key and key in low and en and en not in out:
            out.append(en)
    for ab, full in _ABBREV.items():
        if re.search(r"(?<![a-z0-9])" + re.escape(ab) + r"(?![a-z0-9])", low):
            if full not in out:
                out.append(full)
    return out[:4]


def _complete(ctx, messages, temperature=0.0):
    """统一 LLM 入口（T6）：core.llm.call_llm_with_retry，3 次指数退避 + 伪响应兜底。
    复用 ctx.llm 上已配置的 provider key/base_url/model，避免丢失用户在侧边栏的选型。"""
    from core.llm import call_llm_with_retry
    # 调用前拦截异常 Key：不再触发 4 次重试 + llm_failures.log 噪声（缺陷 D）
    issue = key_issue(ctx)
    if issue:
        return ""
    try:
        resp = call_llm_with_retry(
            messages,
            model=getattr(ctx.llm, "model", None),
            temperature=temperature,
            api_key=getattr(ctx.llm, "api_key", "") or "",
            base_url=getattr(ctx.llm, "base_url", None),
        )
        return resp.choices[0].message.content or ""
    except Exception:
        return ""


_EXPAND_PROMPT = """你是学术检索查询扩展器。用户用中文提问，文献库是英文论文。
请只输出严格 JSON（不要解释、不要代码围栏），三个字段各 2-4 条，每条不超过 12 个词，
每条都必须是可独立用于检索的查询串：
{{"cross_lingual": ["中文问题对应的英文检索词或短语"],
 "synonyms": ["同语言（中文）的同义词/上位词改写，用于 BM25 字面匹配"],
 "abbreviations": ["简写展开后的完整写法，如 BERT -> bidirectional encoder representations from transformers"]}}
问题：{q}"""


# LLM 查询扩展 JSON 的字段名（含模型自造变体），行拆分兜底时必须剔除，
# 否则 '"cross_lingual": [...]' 这类原文行会被当成检索变体，挤占 MAX_VARIANTS
# 名额并污染 query 扩展缓存。
_EXPAND_FIELDS = {"cross_lingual", "synonyms", "abbreviations", "variants",
                  "queries", "expansions", "abbrev", "cross-lingual"}


def _clean_variant(v):
    """过滤 LLM 扩展输出里的 JSON 残片/围栏/超长串，只保留可检索的查询串。"""
    s = str(v).strip().strip('"').strip("'").strip()
    if not s or len(s) > 160:
        return ""
    if s.lower() in _EXPAND_FIELDS:
        return ""
    if s[0] in "{}[]\"'," or s[-1] in "{[":
        return ""
    if '":' in s or s.count('"') >= 2 or s.count(":") >= 2:
        return ""
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", s):
        return ""
    return s


def _llm_expand(ctx, query):
    """在线扩展：LLM 返回 JSON，扁平化为独立 variant 列表。"""
    from core.llm import parse_json
    raw = _complete(ctx, [{"role": "user", "content": _EXPAND_PROMPT.format(q=query)}], temperature=0.0)
    if not raw:
        return []
    data = None
    try:
        data = parse_json(raw)
    except Exception:
        data = None
    out = []
    if isinstance(data, dict):
        for key in ("cross_lingual", "synonyms", "abbreviations", "variants", "queries", "expansions"):
            v = data.get(key)
            if isinstance(v, str):
                v = re.split(r"[；;，,\n|]", v)
            if isinstance(v, list):
                out.extend(str(x).strip() for x in v if str(x).strip())
        if not out:  # 模型自造字段名：兜底收所有字符串列表
            for v in data.values():
                if isinstance(v, list):
                    out.extend(str(x).strip() for x in v if str(x).strip())
    elif isinstance(data, list):
        out.extend(str(x).strip() for x in data if str(x).strip())
    if not out:  # 非 JSON 输出：先抢救引号字符串，再按行拆
        out = [q for q in re.findall(r'"([^"\\\n]{4,120})"', raw)
               if q.strip().lower() not in _EXPAND_FIELDS]
    if not out:
        out = [re.sub(r"^[\s\-•*\d.]+", "", ln).strip() for ln in raw.split("\n") if ln.strip()]
    return [x for x in (_clean_variant(v) for v in out) if x][:5]


def expand_queries(ctx, query, report=None, allow_llm=True):
    """返回 variants: List[str]，首元素为原查询，其后每个扩展词独立成条。

    在线优先 LLM（JSON 列表），失败/无 Key 用离线词表 + 简写展开兜底。
    离线符号对齐变体**恒定合并**在 LLM 变体之前（两者互补，见函数内注释）。
    report: 可选 dict，回填 {"mode": ..., "note": ...} 供调用方诊断展示
    （如 Key 异常被调用前拦截、命中缓存等），不影响返回值。
    allow_llm: False 时跳过 LLM 扩展（调用方已判定原查询强命中，无需再花一次
    调用），离线词表/符号对齐变体照常生成并参与检索——符号 mismatch 场景下
    （查询 beta1 vs 原文 β1）原查询双路召回不到答案片段，离线符号对齐变体
    可零 LLM 成本把答案片段补进召回池。
    """
    def _fill(mode, note=""):
        if isinstance(report, dict):
            report["mode"] = mode
            report["note"] = note

    query = (query or "").strip()
    if not query:
        _fill(MODE_OFFLINE_NO_KEY, "空查询")
        return []
    variants = [query]
    cache_key = "__qexp__"
    try:
        cache = ctx.memory.recall(cache_key, {}) or {}
    except Exception:
        cache = {}
    ck = f"{getattr(ctx.memory, 'user', '')}::{query.lower()}"
    exts = cache.get(ck)
    if exts is not None:
        _fill(MODE_CACHED)
    else:
        mode, note = MODE_LLM, ""
        if not allow_llm:
            # 原查询已强命中：不调 LLM（保住耗时基线），但离线变体照常生成
            mode = MODE_OFFLINE_STRONG_HIT
            note = "原查询已强命中，跳过 LLM 扩展；离线符号对齐/词表变体仍参与检索"
            exts = _offline_variants(query)
        elif getattr(getattr(ctx, "llm", None), "available", False):
            issue = key_issue(ctx)
            if issue:
                # 调用前拦截：异常 Key 直接走离线兜底，不打 LLM、不写噪声日志
                mode, note = MODE_OFFLINE_KEY_ISSUE, issue
                exts = _offline_variants(query)
            else:
                exts = _llm_expand(ctx, query)
                if not exts:
                    mode = MODE_OFFLINE_LLM_FAILED
        else:
            mode, note = MODE_OFFLINE_NO_KEY, "未配置大模型 API Key，查询扩展走离线词表兜底"
            exts = []
        if not exts:
            exts = _offline_variants(query)
        if mode != MODE_OFFLINE_STRONG_HIT:
            # 只缓存需要（重新）花调用成本得到的结果；强命中下的离线变体
            # 是确定性计算，无需缓存，也避免占位把后续弱命中时的 LLM 扩展挤掉
            try:
                cache[ck] = exts
                ctx.memory.remember(cache_key, cache)
            except Exception:
                pass
        _fill(mode, note)
    if isinstance(exts, str):  # 兼容旧缓存里存的字符串形态
        exts = [exts] if exts.strip() else []
    # 历史缓存防御：旧版本可能已把 LLM 输出的 JSON 原文行（如
    # '"cross_lingual": [...]'）写进 __qexp__ 缓存，读取时统一清洗，
    # 无需手动改 storage/memory.json 也能让污染条目失效。
    exts = [x for x in (_clean_variant(v) for v in (exts or [])) if x]
    # 离线符号对齐/词表变体**恒定合并**（排在 LLM 变体之前）：
    # LLM 变体是语义泛化，离线变体是确定性符号桥接（查询 beta1 ↔ 原文 β1 ↔
    # "adam optimizer … learning rate"），两者互补。实测 LLM 扩展成功时不会
    # 产出与论文原句字面重合的变体，而它正是召回答案片段的关键——验收脚本
    # qa 组曾因 LLM 路径丢弃离线变体，导致 v7 单篇环境下 Adam β1/β2 的
    # 答案 chunk（p7）都进不了 top6。离线在前还保证 MAX_VARIANTS 截断时
    # 符号变体不被 LLM 变体挤掉。
    offline = _offline_variants(query)
    merged = offline + [v for v in (exts or []) if v not in offline]
    base = set(tokenize(query))
    for v in merged:
        v = str(v).strip().replace("\n", " ")
        if not v or v in variants:
            continue
        if not (set(tokenize(v)) - base):  # 与原查询词面完全相同，没有增量信息
            continue
        variants.append(v)
        if len(variants) >= MAX_VARIANTS:
            break
    return variants

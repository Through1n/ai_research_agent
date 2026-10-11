"""查询扩展：跨语言 + 同语言同义/上位词 + 简写展开，返回 variants: List[str]。

策略（两级降级）：
1. 在线：LLM 可用时，让模型输出 JSON 列表（cross_lingual / synonyms / abbreviations），
   每个扩展词**独立成条**，不再拼成一个大字符串；
2. 离线：内置学术中英术语映射表 + 简写展开表兜底，无 Key 也能演示多查询 RRF 真实生效。

扩展结果按 (原查询, 用户) 缓存，避免重复调用模型。

返回值：List[str]，首元素恒为原查询。旧签名是 [原查询, 扩展串?]，新实现只是把扩展串
拆成多条，调用方（services/rag.py:retrieve）按列表消费即可，向后兼容。
"""
import re

from services.state import tokenize

# 常用学术术语中英映射（覆盖 NLP/深度学习高频词，可扩展）
_GLOSSARY = {
    " GPU": " gpu gpu graphics", "显卡": "gpu", "训练": "train training trained",
    "参数": "parameter param", "参数量": "parameters", "分数": "score",
    " BLEU": "bleu", "翻译": "translation translate", "模型": "model",
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
    """离线兜底：跨语言词表 1 条 + 命中的简写展开若干条，每条独立参与检索。"""
    out = []
    cross = _offline_expand(query)
    if cross.strip():
        out.append(cross.strip())
    low = (query or "").lower()
    for ab, full in _ABBREV.items():
        if re.search(r"(?<![a-z0-9])" + re.escape(ab) + r"(?![a-z0-9])", low):
            out.append(full)
    return out[:4]


def _complete(ctx, messages, temperature=0.0):
    """统一 LLM 入口（T6）：core.llm.call_llm_with_retry，3 次指数退避 + 伪响应兜底。
    复用 ctx.llm 上已配置的 provider key/base_url/model，避免丢失用户在侧边栏的选型。"""
    from core.llm import call_llm_with_retry
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
    if not out:  # 非 JSON 输出：按行拆
        out = [re.sub(r"^[\s\-•*\d.]+", "", ln).strip() for ln in raw.split("\n") if ln.strip()]
    return [x for x in out if 0 < len(x) < 160][:5]


def expand_queries(ctx, query):
    """返回 variants: List[str]，首元素为原查询，其后每个扩展词独立成条。

    在线优先 LLM（JSON 列表），失败/无 Key 用离线词表 + 简写展开兜底。
    """
    query = (query or "").strip()
    if not query:
        return []
    variants = [query]
    cache_key = "__qexp__"
    try:
        cache = ctx.memory.recall(cache_key, {}) or {}
    except Exception:
        cache = {}
    ck = f"{getattr(ctx.memory, 'user', '')}::{query.lower()}"
    exts = cache.get(ck)
    if exts is None:
        if getattr(getattr(ctx, "llm", None), "available", False):
            exts = _llm_expand(ctx, query)
        else:
            exts = []
        if not exts:
            exts = _offline_variants(query)
        try:
            cache[ck] = exts
            ctx.memory.remember(cache_key, cache)
        except Exception:
            pass
    if isinstance(exts, str):  # 兼容旧缓存里存的字符串形态
        exts = [exts] if exts.strip() else []
    base = set(tokenize(query))
    for v in exts or []:
        v = str(v).strip().replace("\n", " ")
        if not v or v in variants:
            continue
        if not (set(tokenize(v)) - base):  # 与原查询词面完全相同，没有增量信息
            continue
        variants.append(v)
        if len(variants) >= MAX_VARIANTS:
            break
    return variants

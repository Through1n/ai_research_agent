"""查询扩展：把中文/混合查询转成等价英文检索词，弥合跨语言词表缺口。

策略（两级降级）：
1. 在线：LLM 可用时，让模型输出 4-8 个英文扩展查询词（一次性、低成本）；
2. 离线：内置学术中英术语映射表兜底，无 Key 也能演示 BM25 真实生效。

扩展结果按 (原查询, 用户) 缓存，避免重复调用模型。
"""
import json

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


def _offline_expand(query):
    """离线词表扩展：命中术语映射则追加英文等价词（大小写不敏感）。"""
    extras = []
    low = query.lower()
    for zh, en in _GLOSSARY.items():
        key = zh.strip().lower()
        if key and key in low:
            extras.append(en)
    return " ".join(extras)


def expand_queries(ctx, query):
    """返回 [原查询, 扩展查询?]。在线优先 LLM，失败/无 Key 用词表兜底。"""
    variants = [query]
    cache_key = "__qexp__"
    cache = ctx.memory.recall(cache_key, {}) or {}
    ck = f"{ctx.memory.user}::{query.strip().lower()}"
    if ck in cache:
        ext = cache[ck]
    elif ctx.llm.available:
        prompt = (f"用户用中文提问学术文献问题，文献是英文论文。请给出 4-8 个最可能出现在"
                  f"英文原文中的等价检索词（单词或短语，空格分隔，不要中文、不要解释）。\n"
                  f"问题：{query}")
        try:
            ext = (ctx.llm.chat([{"role": "user", "content": prompt}],
                                temperature=0.0).content or "").strip()
            ext = ext.replace("\n", " ")
        except Exception:
            ext = ""
        cache[ck] = ext
        ctx.memory.remember(cache_key, cache)
    else:
        ext = _offline_expand(query)
    if ext and set(tokenize(ext)) - set(tokenize(query)):
        variants.append(ext)
    return variants

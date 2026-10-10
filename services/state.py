"""服务容器：在 Streamlit 会话中集中持有 LLM、记忆、向量库、文献库、BM25 等组件与持久化状态。"""
import json
import os
import re
import numpy as np
import streamlit as st
import chromadb
from chromadb.utils import embedding_functions as chroma_ef

from config import CHROMA_DIR, LIBRARY_JSON, MEMORY_JSON, PROFILE_JSON, LLM_TIMEOUT
from core.llm import LLMClient
from core.memory import Memory
from core.providers import get_provider, resolve_key, custom_provider, CUSTOM_ID


def _stem(word):
    """轻量英文词干归一（训练语 training/trained/train 归一到同一词根）。"""
    w = word
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


def tokenize(text):
    """BM25 分词：英文词做词干归一；中文取单字 + 二元组（bigram），提升短语匹配能力。"""
    low = text.lower()
    toks = [_stem(w) for w in re.findall(r"[a-z0-9]+", low)]
    for seg in re.findall(r"[\u4e00-\u9fff]+", low):
        if len(seg) == 1:
            toks.append(seg)
        else:
            toks.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return toks


class Container:
    def __init__(self):
        # 多模态/向量
        self._chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.collection = self._chroma.get_or_create_collection("papers")
        self.embed_fn = chroma_ef.DefaultEmbeddingFunction()

        # 文献库元数据 + 分块（chunks 同时持久化到 library.json）
        lib = self._load_library()
        self.papers = lib.get("papers", {})
        self.paper_order = lib.get("paper_order", [])
        self.chunks = lib.get("chunks", [])
        self.likes = lib.get("likes", {})

        # LLM 与记忆（Provider/Key 在侧边栏设置后生效；默认取 LLM_PROVIDER 环境变量）
        self.provider_id = os.getenv("LLM_PROVIDER", "zhipu")
        self.llm = self._build_llm(self.provider_id, "")
        self.memory = Memory(MEMORY_JSON, PROFILE_JSON)
        self.bm25 = self._build_bm25()

    # ---------- 持久化 ----------
    @staticmethod
    def _load_library():
        try:
            return json.loads(LIBRARY_JSON.read_text(encoding="utf-8"))
        except Exception:
            return {"papers": {}, "paper_order": [], "chunks": [], "likes": {}}

    def save_library(self):
        LIBRARY_JSON.write_text(json.dumps({
            "papers": self.papers, "paper_order": self.paper_order,
            "chunks": self.chunks, "likes": self.likes,
        }, ensure_ascii=False), encoding="utf-8")

    # ---------- 检索基础 ----------
    def embed(self, texts):
        vecs = self.embed_fn(texts)
        return [np.asarray(v).tolist() for v in vecs]

    def rebuild_bm25(self):
        self.bm25 = self._build_bm25()

    def _build_bm25(self):
        # BM25Plus：idf 恒为正，避免小语料下词项分布于半数文档时得分归零
        from rank_bm25 import BM25Plus
        if not self.chunks:
            return None
        corpus = [tokenize(c["text"]) for c in self.chunks]
        return BM25Plus(corpus)

    def _cur_provider(self):
        if self.provider_id == CUSTOM_ID and getattr(self, "_custom_spec", None):
            return custom_provider(self._custom_spec)
        return get_provider(self.provider_id)

    def _build_llm(self, pid, manual_key=""):
        p = custom_provider(self._custom_spec) if (pid == CUSTOM_ID and getattr(self, "_custom_spec", None)) else get_provider(pid)
        key = resolve_key(p, manual_key)
        return LLMClient(key, p.base_url, p.model, p.vl_model, LLM_TIMEOUT)

    def set_provider(self, pid, manual_key="", custom_spec=None):
        """切换大模型 Provider（预设：智谱/DeepSeek/通义/OpenAI/Ollama；或 custom + custom_spec 自定义端点）。"""
        changed = False
        if custom_spec is not None:
            old = getattr(self, "_custom_spec", None)
            if custom_spec != old or pid != self.provider_id:
                self._custom_spec = custom_spec
                changed = True
        if pid != self.provider_id or manual_key != self.llm.api_key or changed:
            self.provider_id = pid
            self.llm = self._build_llm(pid, manual_key)
        return self.llm.available

    def set_api_key(self, key):
        if key and key != self.llm.api_key:
            self.llm = self._build_llm(self.provider_id, key)
        return self.llm.available

    # ---------- 便捷访问 ----------
    @property
    def files(self):
        return list(self.paper_order)

    def paper(self, name):
        return self.papers.get(name)


def get_container():
    """获取当前会话唯一容器（缓存于 session_state）。"""
    if "container" not in st.session_state:
        st.session_state.container = Container()
    return st.session_state.container

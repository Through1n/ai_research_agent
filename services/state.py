"""服务容器：在 Streamlit 会话中集中持有 LLM、记忆、向量库、文献库、BM25 等组件与持久化状态。"""
import json
import os
import re
import numpy as np
import streamlit as st
import chromadb
from chromadb.utils import embedding_functions as chroma_ef

from config import (CHROMA_DIR, LIBRARY_JSON, MEMORY_JSON, PROFILE_JSON,
                    LLM_TIMEOUT, _env_str)
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


def _build_embed_fn():
    """构造**持久**的本地 embedding 函数（话术 7：问答提速的最大单点）。

    chroma 的 DefaultEmbeddingFunction.__call__ 每次都临时新建一个
    ONNXMiniLM_L6_V2 实例：
        return ONNXMiniLM_L6_V2()(input)
    而 ONNX 推理会话挂在该类的 cached_property 上，实例一换缓存即失效——
    实测每次 embed 都重新 initialize_session（约 128ms），占单次调用固定
    开销的 80%+（1 条文本 155ms 里有 128ms 是建会话）。一次检索调 5 次
    embed，等于白付 640ms。持有单例后会话只建一次。
    """
    try:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
            ONNXMiniLM_L6_V2,
        )
        fn = ONNXMiniLM_L6_V2()
        fn(["warmup"])          # 触发模型与会话加载，把首问的等待挪到启动期
        return fn
    except Exception:
        return chroma_ef.DefaultEmbeddingFunction()


class Container:
    def __init__(self):
        # 多模态/向量
        self._chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.collection = self._chroma.get_or_create_collection("papers")
        self.embed_fn = _build_embed_fn()

        # 文献库元数据 + 分块（chunks 同时持久化到 library.json）
        lib = self._load_library()
        self.papers = lib.get("papers", {})
        self.paper_order = lib.get("paper_order", [])
        self.chunks = lib.get("chunks", [])
        self.likes = lib.get("likes", {})

        # LLM 与记忆（Provider/Key 在侧边栏设置后生效；默认取 LLM_PROVIDER 环境变量）
        # 走 _env_str：云平台上 .env 不存在，需回退到 Streamlit Secrets 才读得到
        self.provider_id = _env_str("LLM_PROVIDER", "zhipu")
        self.llm = self._build_llm(self.provider_id, "")
        self.memory = Memory(MEMORY_JSON, PROFILE_JSON)
        self.bm25 = self._build_bm25()

    # ---------- 持久化 ----------
    @staticmethod
    def _load_library():
        try:
            return _ensure_lib_dict(json.loads(LIBRARY_JSON.read_text(encoding="utf-8")))
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

    # ---------- LLM 降级状态透出（抗幻觉：不可用时必须给出人话原因） ----------
    @property
    def llm_status(self):
        """返回 {"available": bool, "reason": str, "kind": str}。

        UI 层可用它把「LLM 为什么不可用」如实展示给用户（如 Key 含非 ASCII、
        401 过期、限流等），而不是静默失败或把伪响应当答案。
        机制细节见 core.llm.is_degraded / DEGRADED_TEXT_MARK。
        """
        llm = getattr(self, "llm", None)
        try:
            if llm is not None and getattr(llm, "available", False):
                return {"available": True, "reason": "", "kind": None}
            reason = (getattr(llm, "key_error", None)
                      or getattr(llm, "last_error", None)
                      or "未配置 API Key，大模型不可用")
            return {"available": False, "reason": str(reason)[:300],
                    "kind": getattr(llm, "last_error_kind", None)}
        except Exception:
            return {"available": False, "reason": "LLM 状态读取失败", "kind": None}

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


def _ensure_lib_dict(data):
    """把 library.json 解析结果收成 dict。

    容器的 try/except 只能兜住「读取失败 / JSON 非法」，兜不住「JSON 合法但类型不对」——
    例如把 library.json 预置成 `[]`，read_text 与 json.loads 都会成功，接着 Container.__init__
    的 lib.get("papers", {}) 抛 AttributeError，容器起不来。
    """
    if isinstance(data, dict):
        return data
    return {"papers": {}, "paper_order": [], "chunks": [], "likes": {}}

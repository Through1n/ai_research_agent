"""服务容器：在 Streamlit 会话中集中持有 LLM、记忆、向量库、文献库、BM25 等组件与持久化状态。"""
import json
import os
import re
import threading
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

    ⚠️ 调用前先看 `_EMBED_*` 一节：本函数在冷缓存下会**同步阻塞**在 83MB
    的 S3 下载上（chroma 的 tenacity 重试 3 次且无超时上限），绝不能在
    Streamlit 首帧路径上直接调。统一走 `warm_embed_async()` / `get_embed_fn()`。
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


# ---------- embedding 模型：后台预热 + 按需取用（话术 19 白屏根因修复） ----------
# 线上验收实测（2026-10-14）：页面全白、body 仅 4 字、9/9 页点击不到、
# JS 错误 0、等待 183s 导航框仍未出现。根因不是 WebSocket（那条已修），而是：
#   app.py:86 模块级 get_container() → Container.__init__ → _build_embed_fn()
#   → ONNXMiniLM_L6_V2() + fn(["warmup"]) → chroma onnx_mini_lm_l6_v2.py:269
#   __call__ 内 _download_model_if_not_exists() → 从 S3 拉 83,178,821B 的
#   onnx.tar.gz（sha256 钉版）。境内网络该域名"连接建立但 body 传输挂死"
#   （实测 5s 0 字节），下载永不完成。
# 而 Streamlit 的渲染模型是「整段脚本跑完才发首帧」——脚本卡在这一行，
# 一帧都发不出来，于是全白 + 零交互 + 零报错（唯一线索是右上角 Stop 按钮，
# 它表示"脚本仍在运行"）。
#
# 讽刺点：warmup 是话术 7 为本地提速故意加在启动期的。本地热缓存下它是优化，
# 线上冷缓存下它堵死首帧——**同一个优化在不同缓存状态下语义相反**。
#
# 修法（两层，缺一不可）：
#   ① 代码层：加载改后台线程，首帧不再等模型（本文件）；
#   ② 部署层：把模型随发布包预置进镜像 ~/.cache/chroma/onnx_models，
#      连下载都不发生（见 start_app.sh / 部署说明）。
_EMBED_LOCK = threading.Lock()
_EMBED_FN = None            # 已就绪的 embedding 函数（None = 尚未就绪）
_EMBED_ERR = None           # 加载失败原因（人话，供 UI 展示）
_EMBED_WARMING = False      # 后台预热线程是否在跑
_EMBED_READY = threading.Event()   # 就绪/失败信号，供有界等待


def _embed_worker():
    """后台预热线程体：真正加载模型并回填结果（异常必须落 err，不许吞）。"""
    global _EMBED_FN, _EMBED_ERR, _EMBED_WARMING
    try:
        fn = _build_embed_fn()
        with _EMBED_LOCK:
            if _EMBED_FN is None:
                _EMBED_FN = fn
    except Exception as e:                               # noqa: BLE001
        with _EMBED_LOCK:
            if _EMBED_ERR is None:
                _EMBED_ERR = "%s: %s" % (type(e).__name__, str(e)[:200])
    finally:
        with _EMBED_LOCK:
            _EMBED_WARMING = False
        _EMBED_READY.set()


def warm_embed_async():
    """启动后台预热（幂等，永不阻塞调用方）。

    容器构造时调用一次即可；模型已就绪或已在预热中则直接返回。
    """
    global _EMBED_WARMING
    with _EMBED_LOCK:
        if _EMBED_FN is not None or _EMBED_WARMING:
            return
        _EMBED_WARMING = True
    threading.Thread(target=_embed_worker, name="ai4s-embed-warm",
                     daemon=True).start()


def embed_status():
    """返回 {"ready": bool, "warming": bool, "error": str|None}，供 UI 展示加载态。"""
    with _EMBED_LOCK:
        return {"ready": _EMBED_FN is not None,
                "warming": _EMBED_WARMING,
                "error": _EMBED_ERR}


def get_embed_fn(wait_sec=None):
    """取用 embedding 函数；未就绪时**有界**等待，超时抛人话 RuntimeError。

    设计要点：等待必须有上界。原实现在 Container.__init__ 里同步等一个
    可能永不完成的下载，等于把"慢"放大成"死"；这里超时后明确告诉用户
    「模型仍在后台加载，可先用关键词检索」，把控制权交还给用户。
    """
    if wait_sec is None:
        try:
            wait_sec = float(os.environ.get("AI4S_EMBED_WAIT_SEC", "90") or 90)
        except Exception:
            wait_sec = 90.0
    if _EMBED_FN is not None:
        return _EMBED_FN
    if not _EMBED_READY.wait(timeout=max(0.0, wait_sec)):
        st = embed_status()
        raise RuntimeError(
            "本地 embedding 模型仍在后台加载中（已等待 %.0f 秒）。"
            "云端冷启动首次需加载约 90MB 模型，请稍后重试；"
            "急用可先走关键词（BM25）检索。%s"
            % (wait_sec, ("加载失败：" + st["error"]) if st.get("error") else "")
        )
    if _EMBED_FN is not None:
        return _EMBED_FN
    raise RuntimeError(
        "本地 embedding 模型加载失败：%s。可先使用关键词（BM25）检索。"
        % (embed_status().get("error") or "未知错误")
    )


class Container:
    def __init__(self):
        # 多模态/向量
        self._chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.collection = self._chroma.get_or_create_collection("papers")
        # ⚠️ 不要在这里同步调 _build_embed_fn()：见 _EMBED_* 一节的故障复盘。
        # 改为后台预热——首帧不再等 90MB 模型下载（话术 19 白屏根因修复）。
        warm_embed_async()

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
    @property
    def embed_fn(self):
        """按需取用的 embedding 函数。

        保留属性形态是为了兼容既有调用方；语义已从「构造期就绪」变为
        「首次取用时就绪（后台预热中，可能有界等待）」。新代码建议直接用
        模块级 `get_embed_fn()`，语义更直白。
        """
        return get_embed_fn()

    def embed(self, texts):
        vecs = _embed_cached(tuple(texts))  # 重复 embed 命中记忆缓存（见文件末尾，语义不变）
        return [np.asarray(v).tolist() for v in vecs]

    def rebuild_bm25(self):
        self.bm25 = self._build_bm25()

    def _build_bm25(self):
        # BM25Plus：idf 恒为正，避免小语料下词项分布于半数文档时得分归零
        from rank_bm25 import BM25Plus
        if not self.chunks:
            return None
        corpus = [_tokenize_cached_list(c["text"]) for c in self.chunks]  # LRU 分词缓存（见文件末尾）
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


# ---------- 热点函数缓存（分词 LRU / embed 记忆） ----------
# 追加于文件末尾：上方各符号（含 资料/technical_doc.md 引用的 _build_bm25:221、
# embed:214、get_container:287 等）行号全部不变，文档引用零漂移。
# 两条缓存都遵守「入站存不可变快照、出站复制」纪律：调用方就地改写永不污染缓存。
from functools import lru_cache  # noqa: E402  置于末尾避免打乱上方行号


@lru_cache(maxsize=8192)
def _tokenize_cached(text):
    """分词 LRU（_build_bm25 热点：每个 chunk 调一次，重建即全量重算）。

    存不可变 tuple；出站经 _tokenize_cached_list 复制成 list 后才交给调用方，
    规避「lru_cache 返回共享可变对象」的污染坑。语义与 tokenize 逐行等价。
    """
    return tuple(tokenize(text))


def _tokenize_cached_list(text):
    """_build_bm25 调用点：命中 LRU 返回 list 副本；未命中原算法实算后回填。"""
    return list(_tokenize_cached(text))


_EMBED_MEMO = {}          # 文本 tuple -> 向量列表（embed 记忆缓存，深拷贝进出防污染）
_EMBED_MEMO_LOCK = threading.Lock()
_EMBED_MEMO_MAX = 512


def _embed_cached(key):
    """embed 记忆缓存：命中返回深拷贝；未命中才调 get_embed_fn()（阻塞/抛错语义不变）。

    同一批文本被重复 embed（重复提问、Streamlit 重跑、多会话共享）时省掉整次
    ONNX 推理；embedding 模型是进程内单例，同文本结果确定，缓存不影响正确性。
    """
    with _EMBED_MEMO_LOCK:
        hit = _EMBED_MEMO.get(key)
    if hit is not None:
        return [list(v) for v in hit]
    vecs = get_embed_fn()(list(key))
    out = [list(np.asarray(v).tolist()) for v in vecs]
    with _EMBED_MEMO_LOCK:
        if len(_EMBED_MEMO) >= _EMBED_MEMO_MAX and key not in _EMBED_MEMO:
            _EMBED_MEMO.pop(next(iter(_EMBED_MEMO)), None)   # FIFO 淘汰最旧，防无界增长
        _EMBED_MEMO[key] = [list(v) for v in out]
    return out

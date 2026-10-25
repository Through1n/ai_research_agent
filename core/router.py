"""Multi-Agent 路由：按意图把任务分发给领域专家 Agent（限定各自可用工具子集）。

本轮（Round4 Worker8）升级为**有状态机语义的可观测多智能体路由**：

1. 路由决策可见：`route_detail()` 返回 `(专家键, 决策理由, 命中的关键词/规则)`，
   UI 可据此展示"为什么选它"以及"该专家可用哪些工具"（`describe_expert()`）。
2. 状态机转交：`next_expert()` 根据**当前专家 + 刚执行完的工具 + 观察结果 + 用户目标**
   决定是否切换到下一个专家（如 search 专家检索到语料后转 qa 专家做证据抽取与综合），
   agent 会把每次切换记入 `handoff_chain`，在 UI 上呈现完整的转交链。

向后兼容（外部既有调用不动）：
    EXPERTS / keyword_route(goal) -> str / route(ctx, goal) -> (expert, reason)
    allowed_tools(expert) -> list[str]

Round 10（性能）：route_detail 对 LLM 意图分类结果做线程安全缓存（key = prompt+model），
相同 query 命中即跳过 LLM 调用；LLM 分类本身非确定性，但相同输入返回首结果不改变
本次路由命中（与既有 per-run 调用语义等价），仅省去重复调用成本。
"""
import threading


EXPERTS = {
    "qa": {"label": "抗幻觉问答与多文推理专家",
           "tools": ["list_papers", "search_literature", "multi_paper", "run_python",
                     "recall_memory", "save_note", "arxiv_search", "web_search",
                     "track_arxiv", "track_topic", "github_search", "citation_quality"]},
    "search": {"label": "论文检索与追踪专家",
               "tools": ["arxiv_search", "pubmed_search", "web_search", "download_paper",
                         "batch_download", "search_literature",
                         "track_arxiv", "track_topic", "github_search", "citation_quality"]},
    "graph": {"label": "引用图谱与综述专家",
              "tools": ["build_citation_graph", "generate_review", "explore_future_work",
                        "list_papers", "search_literature", "citation_insights"]},
    "writing": {"label": "学术写作与可视化专家",
                "tools": ["write_outline", "draw_diagram", "translate_text", "run_python",
                          "list_papers", "search_literature"]},
    "reading": {"label": "版式阅读与翻译专家",
                "tools": ["translate_text", "search_literature", "multi_paper", "list_papers"]},
    "parsing": {"label": "文档解析专家",
                "tools": ["list_papers", "search_literature", "run_python"]},
}

# 关键词兜底规则（离线/LLM 不可用时）
_KEYWORDS = [
    ("graph", ["图谱", "引用关系", "基石", "综述", "citation", "graph", "pagerank", "脉络"]),
    ("search", ["检索", "搜索", "下载", "追踪", "最新论文", "arxiv", "pubmed", "find", "download", "fetch"]),
    ("writing", ["框架", "写作", "outline", "拓扑图", "tikz", "mermaid", "graphviz", "图注", "caption"]),
    ("reading", ["翻译", "对照", "同步滚动", "translate", "reading", "双栏", "划词"]),
    ("parsing", ["解析", "ocr", "扫描", "标题树", "公式", "表格", "parse"]),
    ("qa", ["对比", "为什么", "是什么", "参数", "gpu", "bleu", "compare", "区别", "差异", "?？"]),
]

# 工具职能分组（状态机转移的触发条件）
SEARCH_TOOLS = {"arxiv_search", "pubmed_search", "web_search", "search_literature",
                "batch_download", "download_paper", "github_search", "track_arxiv", "track_topic"}
GRAPH_TOOLS = {"build_citation_graph", "generate_review", "explore_future_work"}
WRITING_TOOLS = {"write_outline", "draw_diagram", "translate_text"}
READ_TOOLS = {"search_literature", "multi_paper", "list_papers", "citation_quality"}
PARSE_TOOLS = {"run_python", "list_papers"}

# 状态机转移表：(源专家, 触发工具集合, 目标专家, 转交理由, 目标关键词门槛)
# 门槛为 None 表示无条件；否则要求用户目标命中其中一个关键词才允许转交。
TRANSITIONS = [
    ("search", SEARCH_TOOLS, "qa",
     "已检索到语料，转交问答专家做证据抽取与抗幻觉综合", None),
    ("parsing", PARSE_TOOLS, "qa",
     "解析结果已入库，转交问答专家基于原文作答", None),
    ("reading", READ_TOOLS, "qa",
     "已定位原文语境，转交问答专家做跨文献推理", None),
    ("qa", GRAPH_TOOLS, "graph",
     "证据充足且目标涉及引用关系/综述，转交图谱综述专家",
     ("图谱", "引用", "综述", "基石", "citation", "graph", "脉络", "承袭")),
    ("qa", WRITING_TOOLS, "writing",
     "证据充足且目标涉及写作产出，转交写作专家",
     ("综述", "写作", "大纲", "框架", "outline", "图", "tikz", "mermaid", "报告")),
    ("graph", WRITING_TOOLS, "writing",
     "图谱/综述骨架已成，转交写作专家产出大纲与图示",
     ("综述", "写作", "大纲", "框架", "outline", "图", "mermaid", "报告")),
    ("search", GRAPH_TOOLS, "graph",
     "检索到多篇文献且目标涉及引用关系，转交图谱综述专家",
     ("图谱", "引用", "综述", "基石", "citation", "graph", "脉络", "承袭")),
]


def keyword_route(goal):
    """关键词兜底路由：返回专家键（向后兼容，签名与返回类型不变）。"""
    low = (goal or "").lower()
    for expert, kws in _KEYWORDS:
        if any(k in low for k in kws):
            return expert
    return "qa"


def keyword_route_reason(goal):
    """关键词路由 + 可解释性：返回 (专家键, 命中的关键词列表, 决策理由)。"""
    low = (goal or "").lower()
    for expert, kws in _KEYWORDS:
        hit = [k for k in kws if k in low]
        if hit:
            return expert, hit, f"关键词规则路由（命中：{'、'.join(hit[:4])}）"
    return "qa", [], "关键词规则路由（未命中特征词，回落到默认问答专家）"


def route(ctx, goal):
    """返回 (expert_key, reason)。优先 LLM 分类，失败用关键词（签名/返回不变）。"""
    key, reason, _matched = route_detail(ctx, goal)
    return key, reason


# ---------- 路由结果线程安全缓存（话术 10：相同 query 跳过重复 LLM 分类） ----------
_route_cache = {}
_route_cache_lock = threading.Lock()
_ROUTE_CACHE_MAX = 256


def _route_cache_evict():
    if len(_route_cache) > _ROUTE_CACHE_MAX:
        _route_cache.pop(next(iter(_route_cache)), None)


def route_detail(ctx, goal):
    """返回 (expert_key, reason, matched_keywords)。

    与 route() 的差别：把"为什么选它"解释出来——LLM 给的理由，或关键词规则命中的词表，
    供 UI 展示路由决策依据。

    话术 10：LLM 分类结果按 (prompt, model) 做线程安全缓存，相同输入命中即返回首结果、
    跳过 LLM 调用；返回的是不可变元组，安全共享，不改变本次路由命中。
    """
    goal = goal or ""
    try:
        if getattr(ctx, "llm", None) is not None and ctx.llm.available:
            listing = "\n".join(f"- {k}: {v['label']}" for k, v in EXPERTS.items())
            prompt = f"""判断以下用户目标应交给哪个专家 Agent 处理，只输出 JSON：{{"expert":"键","reason":"简述"}}
可选专家：
{listing}
用户目标：{goal}"""
            cache_key = (prompt, getattr(ctx.llm, "model", None))
            with _route_cache_lock:
                cached = _route_cache.get(cache_key)
            if cached is not None:
                return cached
            try:
                data = ctx.llm.json_chat([{"role": "user", "content": prompt}], temperature=0.0)
                key = (data or {}).get("expert")
                if key in EXPERTS:
                    result = (key, str((data or {}).get("reason", "") or "LLM 意图分类"), [])
                    with _route_cache_lock:
                        _route_cache[cache_key] = result
                        _route_cache_evict()
                    return result
            except Exception:
                pass
    except Exception:
        pass
    return keyword_route_reason(goal)


def allowed_tools(expert):
    return EXPERTS[expert]["tools"]


def describe_expert(expert):
    """UI 展示用：专家键 + 名称 + 可用工具清单（键不存在时安全回落 qa）。"""
    e = EXPERTS.get(expert, EXPERTS["qa"])
    return {"key": expert if expert in EXPERTS else "qa",
            "label": e["label"],
            "tools": list(e["tools"])}


def next_expert(current, tool_name="", observation="", goal="", visited=None, used_tools=None):
    """状态机转交：根据执行结果决定下一个专家。

    参数：
        current      当前专家键
        tool_name    刚执行成功的工具名
        observation  工具返回摘要（失败/空结果不触发转交）
        goal         用户原始目标（用于关键词门槛）
        visited      已走过的专家集合（防止 A→B→A 回环）
        used_tools   本次运行已用过的工具（预留扩展）

    返回 (next_expert_key, reason)；不需要转交时返回 None。
    """
    try:
        if not current or current not in EXPERTS:
            return None
        if not tool_name:
            return None
        obs = str(observation or "")
        if (not obs.strip()) or ("error" in obs.lower()[:200]) or ("失败" in obs[:200]):
            return None
        visited = set(visited or [])
        low = (goal or "").lower()
        for src, tools, dst, reason, gate in TRANSITIONS:
            if src != current or dst == current or dst in visited:
                continue
            if tool_name not in tools:
                continue
            if gate and not any(k in low for k in gate):
                continue
            return dst, f"{reason}（触发工具：{tool_name}）"
    except Exception:
        return None
    return None

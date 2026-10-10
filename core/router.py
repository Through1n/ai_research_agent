"""Multi-Agent 路由：按意图把任务分发给领域专家 Agent（限定各自可用工具子集）。"""


EXPERTS = {
    "qa": {"label": "抗幻觉问答与多文推理专家",
           "tools": ["list_papers", "search_literature", "multi_paper", "run_python",
                     "recall_memory", "save_note", "arxiv_search", "web_search",
                     "track_arxiv", "github_search", "citation_quality"]},
    "search": {"label": "论文检索与追踪专家",
               "tools": ["arxiv_search", "pubmed_search", "web_search", "download_paper",
                         "batch_download", "search_literature",
                         "track_arxiv", "github_search", "citation_quality"]},
    "graph": {"label": "引用图谱与综述专家",
              "tools": ["build_citation_graph", "generate_review", "explore_future_work",
                        "list_papers", "search_literature"]},
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


def keyword_route(goal):
    low = goal.lower()
    for expert, kws in _KEYWORDS:
        if any(k in low for k in kws):
            return expert
    return "qa"


def route(ctx, goal):
    """返回 (expert_key, reason)。优先 LLM 分类，失败用关键词。"""
    if ctx.llm.available:
        listing = "\n".join(f"- {k}: {v['label']}" for k, v in EXPERTS.items())
        prompt = f"""判断以下用户目标应交给哪个专家 Agent 处理，只输出 JSON：{{"expert":"键","reason":"简述"}}
可选专家：
{listing}
用户目标：{goal}"""
        try:
            data = ctx.llm.json_chat([{"role": "user", "content": prompt}], temperature=0.0)
            key = data.get("expert")
            if key in EXPERTS:
                return key, data.get("reason", "")
        except Exception:
            pass
    return keyword_route(goal), "关键词规则路由（LLM 不可用或解析失败）"


def allowed_tools(expert):
    return EXPERTS[expert]["tools"]

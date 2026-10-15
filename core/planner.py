"""Task Planning：把高层目标动态拆解为**有序、结构化、可执行**的子任务，并真正驱动 Agent 逐步下钻。

本轮（Round4 Worker8）把 planner 从"只产出展示用字符串"升级为**真正参与执行编排**的规划器：

* `plan()` 返回结构化子任务列表，每个子任务含
  `id / title / tool（建议工具）/ expert（建议专家）/ depends（依赖）/ status / elapsed_ms / note`，
  `core/agent.py` 按 `depends` 顺序逐个执行，并把每步状态（⏳进行中 / ✅完成 / ⚠️跳过 / ❌失败）
  实时写回任务对象供 UI 渲染。
* `replan()` 支持**动态规划**：某步失败或结果不满足时，基于已完成的步骤、失败原因与
  剩余步骤重新规划"剩下的路"，而不是一条路走到黑。

向后兼容：
    fallback_plan(goal, expert) -> list[str]（既有单测直接使用，长度 ≥3，签名与返回类型不变）
    plan(ctx, goal, expert)     依然可调用（返回结构化任务；展示用字符串走 plan_strings()）
"""
import copy
import re
import threading

from core.router import EXPERTS, allowed_tools

# 子任务状态 → 展示图标（UI 与 Trace 文案共用）
STATUS_ICON = {
    "pending": "⏳",
    "running": "⏳",
    "done": "✅",
    "skipped": "⚠️",
    "failed": "❌",
}
STATUS_TEXT = {
    "pending": "待执行",
    "running": "进行中",
    "done": "完成",
    "skipped": "跳过",
    "failed": "失败",
}

MAX_TASKS = 12           # 单次运行最多执行的子任务数（含重规划新增）
MAX_REPLANS = 2          # 最多触发 N 次动态规划，避免死循环

# 工具职能分组：用于离线兜底拆解时挑选"建议工具"
_GATHER_TOOLS = ["arxiv_search", "pubmed_search", "web_search", "download_paper",
                 "batch_download", "search_literature", "list_papers"]
_ANALYZE_TOOLS = ["search_literature", "multi_paper", "citation_quality", "run_python"]
_GRAPH_TOOLS = ["build_citation_graph", "explore_future_work", "generate_review"]
_WRITE_TOOLS = ["write_outline", "draw_diagram", "translate_text"]

# 工具失败后的备选（动态规划的"换条路"策略）
_ALTERNATIVES = {
    "arxiv_search": "web_search",
    "pubmed_search": "web_search",
    "web_search": "arxiv_search",
    "download_paper": "search_literature",
    "batch_download": "arxiv_search",
    "search_literature": "list_papers",
    "multi_paper": "search_literature",
    "citation_quality": "search_literature",
    "build_citation_graph": "list_papers",
    "generate_review": "search_literature",
    "explore_future_work": "search_literature",
    "write_outline": "search_literature",
    "draw_diagram": "run_python",
    "translate_text": "search_literature",
    "run_python": "search_literature",
    "track_arxiv": "arxiv_search",
    "track_topic": "arxiv_search",
    "github_search": "web_search",
    "recall_memory": "search_literature",
    "save_note": "search_literature",
}

ALL_TOOLS = sorted({t for v in EXPERTS.values() for t in v["tools"]})


# ---------------------------------------------------------------- 任务构造/工具函数
def make_task(tid, title, tool="", expert="", depends=None, status="pending"):
    """构造一个结构化子任务（dict，便于 JSON 序列化与 UI 渲染）。"""
    return {
        "id": str(tid),
        "title": str(title or "").strip(),
        "tool": str(tool or "").strip(),
        "expert": str(expert or "").strip(),
        "depends": [str(d) for d in (depends or []) if d],
        "status": status,          # pending / running / done / skipped / failed
        "elapsed_ms": 0,
        "note": "",                # 失败原因 / 跳过原因 / 结果摘要
        "result": "",              # 本步产出的文本结论（截断保存）
    }


def task_line(t):
    """把子任务渲染成带状态图标的一行（供 Trace / UI 直接展示）。"""
    icon = STATUS_ICON.get(t.get("status", "pending"), "⏳")
    txt = STATUS_TEXT.get(t.get("status", "pending"), "待执行")
    ms = int(t.get("elapsed_ms") or 0)
    tail = f" · {ms}ms" if ms else ""
    tool = f" · 建议工具 `{t.get('tool')}`" if t.get("tool") else ""
    note = f" · {t['note']}" if t.get("note") else ""
    return f"{icon} **{t.get('id')}** {t.get('title','')}（{txt}{tool}{tail}）{note}"


def plan_strings(tasks):
    """把结构化任务转成展示用字符串列表（render_trace 等旧消费方需要 list[str]）。"""
    return [task_line(t) for t in (tasks or [])]


def _pick(pool, allowed, default=""):
    """在候选工具里挑第一个当前专家可用的。"""
    for name in pool:
        if name in allowed:
            return name
    return default


# ---------------------------------------------------------------- 兜底（离线/LLM 不可用）
def fallback_plan(goal, expert):
    """向后兼容：返回展示用的步骤字符串列表（长度 ≥3，签名与返回类型不变）。"""
    label = EXPERTS[expert]["label"]
    return [
        f"由「{label}」接管，明确目标与所需资料：{goal[:40]}",
        "检索/调用相应工具收集带出处的证据（文献片段、外部接口或代码执行）",
        "基于证据进行推理、综合，必要时跨多篇文献关联",
        "生成带内联引用的结果，并做抗幻觉与一致性校验",
    ]


def fallback_tasks(goal, expert):
    """离线兜底的结构化拆解：按目标关键词挑选建议工具，保证 ≥3 步、可执行。"""
    allowed = set(allowed_tools(expert))
    low = (goal or "").lower()
    tasks = []

    tasks.append(make_task("T1", f"盘点现有资料，明确本任务所需证据：{(goal or '')[:36]}",
                           _pick(["list_papers", "search_literature"], allowed), expert))

    if any(k in low for k in ("图谱", "引用", "基石", "综述", "citation", "graph", "脉络", "承袭")):
        tasks.append(make_task("T2", "构建/查询引用关系，定位相关文献与承袭脉络",
                               _pick(_GRAPH_TOOLS, allowed), expert, ["T1"]))
    elif any(k in low for k in ("框架", "写作", "大纲", "outline", "图", "mermaid", "tikz", "报告")):
        tasks.append(make_task("T2", "检索支撑材料，整理为可写作的证据集",
                               _pick(_GATHER_TOOLS, allowed), expert, ["T1"]))
    else:
        tasks.append(make_task("T2", "检索/抓取目标文献与片段，收集带出处的证据",
                               _pick(_GATHER_TOOLS, allowed), expert, ["T1"]))

    tasks.append(make_task("T3", "对证据做跨源比对与推理（必要时调用分析工具复核）",
                           _pick(_ANALYZE_TOOLS, allowed), expert,
                           [t["id"] for t in tasks[-1:]]))
    tasks.append(make_task("T4", "综合以上证据生成带【n】来源标注的中文答案",
                           "", expert, [t["id"] for t in tasks[-1:]]))
    return tasks


# ---------------------------------------------------------------- LLM 输出归一化
def normalize_tasks(raw, expert, goal=""):
    """把 LLM 的任意合理输出归一化成结构化任务列表；无法解析返回 None。

    兼容四种形态：
      {"tasks":[{...}]} / {"plan":["...", ...]} / {"steps":[...]} / 直接是 list
    """
    try:
        if isinstance(raw, dict):
            for key in ("tasks", "plan", "steps", "subtasks"):
                if key in raw:
                    raw = raw[key]
                    break
            else:
                return None
        if not isinstance(raw, list) or not raw:
            return None

        allowed = set(allowed_tools(expert))
        out = []
        prev_id = None
        for i, item in enumerate(raw[:MAX_TASKS], 1):
            if isinstance(item, dict):
                tid = str(item.get("id") or item.get("task_id") or f"T{i}")
                title = (item.get("title") or item.get("desc") or item.get("description")
                         or item.get("task") or item.get("step") or "")
                tool = str(item.get("tool") or item.get("tools") or item.get("action") or "")
                if isinstance(tool, (list, tuple)):
                    tool = next((str(x) for x in tool if str(x) in ALL_TOOLS), "")
                tool = str(tool).strip()
                # 建议工具必须在所有专家工具里存在，否则视为无效建议
                if tool and tool not in ALL_TOOLS:
                    tool = ""
                ex = str(item.get("expert") or "")
                if ex not in EXPERTS:
                    ex = ""
                dep = item.get("depends") or item.get("depends_on") or item.get("deps") or []
                if isinstance(dep, str):
                    dep = [d for d in re.split(r"[,\s]+", dep) if d]
                dep = [str(d) for d in dep]
            else:
                tid = f"T{i}"
                title = str(item)
                tool, ex, dep = "", "", []
            if not str(title).strip():
                continue
            if not dep and prev_id:      # 没写依赖就默认串行依赖上一步，保证"有序"
                dep = [prev_id]
            t = make_task(tid, title, tool, ex, dep)
            if t["tool"] and t["tool"] not in allowed:
                # 当前专家用不了该工具：保留建议但允许路由切换后执行
                t["note"] = f"建议工具 {t['tool']} 不在当前专家工具集，可能触发路由转交"
            out.append(t)
            prev_id = tid
        return out or None
    except Exception:
        return None


# ---------------------------------------------------------------- 主规划入口
_PLAN_PROMPT = """你是 Agent 的任务规划器。请把用户目标拆解为 3-6 个**有序、可执行**的子任务。
只输出 JSON：{{"tasks":[{{"id":"T1","title":"做什么","tool":"建议工具(可选)","expert":"建议专家键(可选)","depends":["T0"]}}, ...]}}
规则：
1) title 必须是一个具体动作，不要写"思考""总结"这类空话；
2) tool 只能从下面的可用工具里选，或留空字符串表示纯推理步；
3) expert 只能从下面专家键里选，或留空表示沿用当前专家；
4) depends 填该步依赖的前序任务 id，没有就留空数组；步骤必须能被顺序执行。
当前负责专家：{label}（键：{expert}）
可用工具：{tools}
可选专家键：{experts}
用户目标：{goal}"""


# ---------- 规划结果线程安全缓存（话术 10：相同 (goal, expert) 跳过重复 LLM 规划） ----------
_plan_cache = {}
_plan_cache_lock = threading.Lock()
_PLAN_CACHE_MAX = 256


def _plan_cache_evict():
    if len(_plan_cache) > _PLAN_CACHE_MAX:
        _plan_cache.pop(next(iter(_plan_cache)), None)


def plan(ctx, goal, expert):
    """返回结构化子任务列表（list[dict]）。LLM 不可用/解析失败时回落到离线拆解。

    话术 10：LLM 规划结果按 (prompt, model) 做线程安全缓存，相同输入命中即跳过 LLM
    调用、返回缓存深拷贝（避免调用方就地改 status/note 污染缓存）；不改变拆解结果。
    """
    goal = goal or ""
    try:
        if getattr(ctx, "llm", None) is not None and getattr(ctx.llm, "available", False):
            prompt = _PLAN_PROMPT.format(
                label=EXPERTS[expert]["label"], expert=expert,
                tools=", ".join(EXPERTS[expert]["tools"]),
                experts=", ".join(EXPERTS.keys()), goal=goal[:1200])
            cache_key = (prompt, getattr(ctx.llm, "model", None))
            with _plan_cache_lock:
                cached = _plan_cache.get(cache_key)
            if cached is not None:
                return copy.deepcopy(cached)
            try:
                raw = ctx.llm.json_chat([{"role": "user", "content": prompt}], temperature=0.2)
                tasks = validate_plan(normalize_tasks(raw, expert, goal))
                if tasks:
                    # 缓存存快照、返回也給调用方独立副本：调用方会在执行期就地改写
                    # status / note（⏳→✅/❌）。若缓存与返回值共用同一对象，运行态会
                    # 被写进缓存，导致下次相同 goal 一上来就显示上一次的终态。
                    snapshot = copy.deepcopy(tasks)
                    with _plan_cache_lock:
                        _plan_cache[cache_key] = snapshot
                        _plan_cache_evict()
                    return copy.deepcopy(tasks)
            except Exception:
                pass
    except Exception:
        pass
    return validate_plan(fallback_tasks(goal, expert))


# ---------------------------------------------------------------- 动态规划（Re-Planning）
_REPLAN_PROMPT = """你是 Agent 的任务规划器，现在需要**重新规划剩余步骤**。
原始目标：{goal}
已完成步骤：{done}
刚刚失败/结果不充分的步骤：{failed}（原因：{reason}）
原计划剩余步骤：{remain}
请只输出 JSON：{{"tasks":[{{"id":"T3","title":"换一种做法完成什么","tool":"备选工具","expert":"专家键","depends":[]}}, ...]}}
要求：1~4 步；必须绕开失败原因（换工具/缩小范围/改走离线库）；不要重复已完成的步骤。
可用工具：{tools}"""


def heuristic_replan(failed_task, remaining, expert, reason):
    """离线动态规划：换备选工具重试 → 缩小范围 → 退化为纯推理步。"""
    allowed = set(allowed_tools(expert))
    tool = (failed_task or {}).get("tool", "")
    alt = _ALTERNATIVES.get(tool, "")
    new = []
    base_id = (failed_task or {}).get("id", "T0")
    n = 0
    if alt and alt in allowed:
        n += 1
        new.append(make_task(f"{base_id}R{n}",
                             f"改用 `{alt}` 重试：{(failed_task or {}).get('title','')[:40]}",
                             alt, expert))
    else:
        n += 1
        new.append(make_task(f"{base_id}R{n}",
                             f"缩小范围重试（不依赖外部检索）：{(failed_task or {}).get('title','')[:40]}",
                             _pick(["list_papers", "search_literature"], allowed), expert))
    for j, t in enumerate(remaining or [], 1):
        n += 1
        nt = make_task(f"{base_id}R{n}", t.get("title", ""), t.get("tool", ""),
                       t.get("expert", ""), [new[0]["id"]] if j == 1 else [f"{base_id}R{n - 1}"])
        new.append(nt)
    if not remaining:
        new.append(make_task(f"{base_id}R{n + 1}", "基于已有证据直接给出带来源标注的结论",
                             "", expert, [new[0]["id"]]))
    return new[:6]


def replan(ctx, goal, tasks, index, failed_task, reason, expert):
    """动态规划：返回替换"剩余步骤"的新任务列表（不含已完成部分）。

    优先 LLM 重规划，失败时用启发式策略（换备选工具 / 缩小范围 / 退化纯推理）。
    """
    goal = goal or ""
    done = [t for t in (tasks or [])[:max(index, 0)] if t.get("status") in ("done", "skipped")]
    remaining = [t for t in (tasks or [])[max(index, 0) + 1:]]
    try:
        if getattr(ctx, "llm", None) is not None and getattr(ctx.llm, "available", False):
            prompt = _REPLAN_PROMPT.format(
                goal=goal[:800],
                done="; ".join(f"{t['id']}:{t['title']}" for t in done) or "（无）",
                failed=f"{(failed_task or {}).get('id','')}:{(failed_task or {}).get('title','')}",
                reason=str(reason or "")[:200],
                remain="; ".join(f"{t['id']}:{t['title']}" for t in remaining) or "（无）",
                tools=", ".join(allowed_tools(expert)))
            try:
                raw = ctx.llm.json_chat([{"role": "user", "content": prompt}], temperature=0.2)
                new = validate_plan(normalize_tasks(raw, expert, goal))
                if new:
                    return new[:6]
            except Exception:
                pass
    except Exception:
        pass
    return validate_plan(heuristic_replan(failed_task, remaining, expert, reason))


# ---------------------------------------------------------------- 计划校验与自评（确定性，无 LLM）
def validate_plan(tasks, expert=""):
    """校验并修复结构化计划的**依赖图**，返回清洗后的任务列表（为空/不可修复返回 None）。

    `run()` 按**列表顺序**逐个执行子任务，`depends` 只用于"前序失败则跳过本步"这一处判定
    （见 core.agent._deps_failed）。因此只有"指向排在自己之前的真实任务"的依赖才是可执行、
    可判定的；LLM 规划或启发式拆解可能产出以下几类**病态依赖**，这里统一收敛为诚实 DAG：

      1) 重复 id     → 追加 `#n` 后缀去重（保留首个），否则 audit / 展示会张冠李戴；
      2) 悬挂依赖    → 删除指向不存在任务的 depends（by_id 取不到 → 永远"看起来满足"，语义失真）；
      3) 前向/自依赖 → 删除指向"排在自己之后或自己"的依赖（本循环按序执行，前向依赖本就无法执行）；
      4) 依赖成环    → 由规则 3 自动破除（任何环在线性序里至少含一条前向边，删掉即成 DAG）。

    纯函数、确定性、不调用 LLM；任何异常都安全回落到"原样返回"，绝不阻断执行。
    """
    try:
        seq = [t for t in (tasks or []) if isinstance(t, dict)]
        if not seq:
            return None
        # 1) 去重 id（保留首个，后续追加 #n）
        seen = {}
        for t in seq:
            tid = str(t.get("id") or "?")
            if tid in seen:
                seen[tid] += 1
                t["id"] = f"{tid}#{seen[tid]}"
            else:
                seen[tid] = 0
        order = {t["id"]: i for i, t in enumerate(seq)}
        # 2/3/4) 逐任务清理依赖：去悬挂、去前向/自引用、去重 → 天然破除成环
        for i, t in enumerate(seq):
            kept = []
            for d in [str(x) for x in (t.get("depends") or []) if x]:
                j = order.get(d)
                if j is None or j >= i:       # 悬挂 / 前向 / 自引用
                    continue
                if d not in kept:             # 依赖去重
                    kept.append(d)
            t["depends"] = kept
        return seq or None
    except Exception:
        return list(tasks) if tasks else None


# 阶段识别：工具职能分组 + 标题关键词（与 fallback_tasks 的工具分组保持一致）
_GATHER_KW = ("检索", "抓取", "下载", "收集", "盘点", "search", "download", "fetch", "gather", "追踪")
_ANALYZE_KW = ("比对", "推理", "分析", "核查", "复核", "综合", "定位", "analy", "compar", "reason")
_SYNTH_KW = ("生成", "给出", "输出", "作答", "答案", "结论", "撰写", "成文", "synthesi", "answer", "produce")


def _dep_depth(tasks):
    """依赖链最大深度（以"边"计：全并行=0，纯串行 N 步=N-1）。"""
    by_id = {t["id"]: t for t in tasks}
    memo = {}

    def depth(tid, stack):
        if tid in memo:
            return memo[tid]
        if tid in stack:            # 防御性：理论上 validate_plan 已破环
            return 0
        deps = (by_id.get(tid) or {}).get("depends") or []
        d = 0
        for p in deps:
            if p in by_id:
                d = max(d, 1 + depth(p, stack | {tid}))
        memo[tid] = d
        return d

    return max((depth(t["id"], frozenset()) for t in tasks), default=0)


def plan_health(tasks, goal=""):
    """确定性"计划自评"：不调用 LLM，仅从结构与目标关键词评估计划质量。

    返回 dict：
      n_steps        计划步数
      dep_depth      依赖链最大深度（边计）
      phases         命中的阶段标签（检索 gather / 分析 analyze / 综合 synth）
      tool_coverage  带建议工具的步数占比（0~1）
      covers_goal    目标与步骤标题是否共享 ≥2 字/词片段（粗糙覆盖度）
      verdict        ok / thin（步数过少或缺检索·分析阶段）
      notes          人读自评要点（list[str]）

    用途：core.agent.run() 在规划后把自评写入 Trace，让"Agent 是否理解并覆盖了自己的计划"
    变得可观测，而非只丢一串步骤字符串。
    """
    res = {"n_steps": 0, "dep_depth": 0, "phases": [], "tool_coverage": 0.0,
           "covers_goal": False, "verdict": "ok", "notes": []}
    try:
        seq = [t for t in (tasks or []) if isinstance(t, dict)]
        res["n_steps"] = len(seq)
        if not seq:
            res["verdict"] = "thin"
            res["notes"].append("空计划：无任何子任务")
            return res
        phases, has_tool = set(), 0
        for t in seq:
            title = str(t.get("title") or "").lower()
            tool = str(t.get("tool") or "")
            if tool:
                has_tool += 1
            if tool in _GATHER_TOOLS or any(k in title for k in _GATHER_KW):
                phases.add("检索")
            if tool in _ANALYZE_TOOLS or any(k in title for k in _ANALYZE_KW):
                phases.add("分析")
            if any(k in title for k in _SYNTH_KW):
                phases.add("综合")
        res["phases"] = sorted(phases)
        res["tool_coverage"] = round(has_tool / len(seq), 2)
        res["dep_depth"] = _dep_depth(seq)
        # 目标覆盖：目标切 2-gram / 拉丁词，与步骤标题比对
        try:
            g = (goal or "").lower()
            grams = set(re.findall(r"[a-z0-9]{2,}", g))
            grams |= {g[i:i + 2] for i in range(len(g) - 1)
                      if any('一' <= c <= '鿿' for c in g[i:i + 2])}
            titles = " ".join(str(t.get("title") or "").lower() for t in seq)
            res["covers_goal"] = bool(grams) and any(x in titles for x in grams)
        except Exception:
            res["covers_goal"] = False
        thin = (len(seq) < 3) or ("检索" not in phases and "分析" not in phases)
        res["verdict"] = "thin" if thin else "ok"
        res["notes"] = [
            f"共 {len(seq)} 步，依赖深度 {res['dep_depth']}",
            f"覆盖阶段：{'、'.join(res['phases']) or '（未识别）'}",
            f"建议工具覆盖率 {int(res['tool_coverage'] * 100)}%",
            ("目标关键词已进入步骤标题" if res["covers_goal"] else "步骤标题未显式覆盖目标关键词"),
        ]
        if thin:
            res["notes"].append("步骤偏少或缺少检索/分析阶段，计划覆盖可能不足")
    except Exception:
        pass
    return res

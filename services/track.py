"""arXiv 动态追踪：话题订阅 + 增量新论文检测（last-seen 检查点）。

与 UI/守护脚本配合：
- fetch_track(ctx)：按订阅话题抓取最新论文，diff 出「上次以来新增」的论文并持久化检查点；
- diff_new(ctx, topic, items)：纯增量比对（供自测与守护脚本复用）；
- 检查点存于长期记忆（storage/memory.json 的 kv），无需数据库即可跨会话追踪。
"""
import json

from services import extern

_SUB_KEY = "track_subscriptions"      # 话题订阅列表
_SEEN_KEY = "track_seen"              # {topic: [arxiv_id,...]} 检查点


def get_subscriptions(ctx):
    return ctx.memory.recall(_SUB_KEY, ["cs.AI", "cs.CL"])


def set_subscriptions(ctx, topics):
    ctx.memory.remember(_SUB_KEY, [t.strip() for t in topics if t.strip()])


def diff_new(ctx, topic, items):
    """返回该话题「上次检查点之后」的新论文，并更新检查点。"""
    seen = ctx.memory.recall(_SEEN_KEY, {})
    known = set(seen.get(topic, []))
    fresh = [it for it in items if it["arxiv_id"] not in known]
    seen[topic] = list(known | {it["arxiv_id"] for it in items})
    ctx.memory.remember(_SEEN_KEY, seen)
    return fresh


def fetch_track(ctx, max_results=8, only_new=True):
    """抓取所有订阅话题的最新论文；only_new 时只返回增量新论文。"""
    report = []
    for topic in get_subscriptions(ctx):
        try:
            items = extern.latest_arxiv(topic, max_results)
        except Exception as e:
            report.append({"topic": topic, "error": str(e)[:80], "new": []})
            continue
        new = diff_new(ctx, topic, items) if only_new else items
        report.append({"topic": topic, "count": len(items), "new": new})
    return report


def reset_checkpoints(ctx):
    """清空检查点（演示时让首次追踪立即产出「新论文」）。"""
    ctx.memory.remember(_SEEN_KEY, {})

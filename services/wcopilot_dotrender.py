"""Graphviz DOT 零依赖降级渲染器（学术 Copilot T3 第三级兜底）。

用途：当本机既没有 ``dot`` 可执行文件、也没有 Python ``graphviz`` / ``matplotlib``
时，仍然把 LLM 生成的 ``digraph { A -> B; B -> C }`` 渲染成**真实可见的 SVG 拓扑图**，
而不是只给用户一段 DOT 源码文本。

设计取舍：
- 只用标准库（re / html），不 pip install、不写临时文件、不发起网络请求；
- 解析是**尽力而为**的宽松解析（兼容有/无分号、引号节点名、链式的 ``a -> b -> c``、
  ``label=`` 属性），解析不出节点时返回空字符串，由上层回退到"仅展示源码"；
- 布局是**分层最长路径布局**（rank = max(rank(前驱)) + 1），同层按出现顺序排列并
  整体居中，视觉上与 Graphviz 的 ``dot`` 默认布局（自上而下）等价但不保证像素一致，
  因此 UI 上必须标注"降级渲染（等价拓扑图）"，不可声称是 Graphviz 官方渲染。
"""
import html
import re

_NODE_W = 148          # 默认节点框宽度（按标签长度自适应，见 _node_size）
_NODE_H = 38           # 节点框高度
_RANK_GAP = 96         # 层间垂直间距
_COL_GAP = 34          # 同层节点水平间距
_MARGIN = 28           # SVG 四周留白
_MAX_NODES = 150       # 超过则只渲染前 N 个节点，避免超大图卡死前端
_FONT_SIZE = 12

_ARROW_RE = re.compile(r"\s*(?:-->|->|--)\s*")
_ATTR_RE = re.compile(r"([A-Za-z_][\w]*)\s*=\s*(\"[^\"]*\"|[^,\]]+)")


# ---------------------------------------------------------------- 解析

def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", " ", str(src or ""), flags=re.DOTALL)
    src = re.sub(r"(?m)^\s*//.*$", " ", src)
    return re.sub(r"(?m)^\s*#.*$", " ", src)


def _split_statements(body):
    """按 ``;`` 与"语义完整"的换行切分语句；括号/引号内的分隔符不切。"""
    stmts, buf = [], []
    depth, in_quote, i, n = 0, False, 0, len(body)
    while i < n:
        ch = body[i]
        if ch == '"':
            if not in_quote:
                in_quote = True
            elif i == 0 or body[i - 1] != "\\":
                in_quote = False
        elif not in_quote:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth = max(0, depth - 1)
            elif depth == 0 and ch == ";":
                stmts.append("".join(buf))
                buf = []
                i += 1
                continue
            elif depth == 0 and ch == "\n":
                tail = "".join(buf).rstrip()
                if tail and (tail[-1].isalnum() or tail[-1] in "\"'_]"):
                    stmts.append(tail)
                    buf = []
                    i += 1
                    continue
                buf.append(" ")
                i += 1
                continue
        buf.append(ch)
        i += 1
    stmts.append("".join(buf))
    return [s.strip() for s in stmts if s.strip()]


def _clean_id(text):
    text = str(text or "").strip()
    text = re.sub(r"\[.*$", "", text, flags=re.DOTALL).strip()
    text = text.strip().strip('"').strip()
    return text


def _parse_attrs(text):
    attrs = {}
    for key, val in _ATTR_RE.findall(text or ""):
        val = str(val).strip().strip('"')
        attrs[key.lower()] = val
    return attrs


def parse_dot(src):
    """把 DOT 源码解析为 ``{"directed": bool, "nodes": [...], "edges": [...]}``。

    nodes: ``[{"id","label","shape"}]``；edges: ``[{"src","dst","label"}]``。
    任何异常都返回空图（不抛给上层）。
    """
    empty = {"directed": True, "nodes": [], "edges": []}
    try:
        text = _strip_comments(src)
        if not text.strip():
            return empty
        # 不像 DOT（既无 graph 关键字也无花括号/箭头）就直接判为不可解析，
        # 避免把任意文本误画成一个孤立节点
        looks_like_dot = bool(re.search(r"\b(?:di|sub)?graph\b", text, re.IGNORECASE)) \
            or (("{" in text and "}" in text) or _ARROW_RE.search(text))
        if not looks_like_dot:
            return empty
        directed = bool(re.search(r"\bdi\s*graph\b|\bdigraph\b", text, re.IGNORECASE))
        first = text.find("{")
        last = text.rfind("}")
        body = text[first + 1:last] if 0 < first < last else text
        nodes, order, edges = {}, [], []

        def ensure(node_id, attrs=None):
            node_id = _clean_id(node_id)
            if not node_id or node_id in ("graph", "node", "edge", "subgraph"):
                return None
            if node_id not in nodes:
                nodes[node_id] = {"id": node_id, "label": node_id, "shape": "box"}
                order.append(node_id)
            if attrs:
                if attrs.get("label"):
                    nodes[node_id]["label"] = attrs["label"]
                if attrs.get("shape"):
                    nodes[node_id]["shape"] = attrs["shape"].lower()
            return node_id

        for stmt in _split_statements(body):
            attr_text = ""
            m_attr = re.search(r"\[(.*)\]", stmt, re.DOTALL)
            if m_attr:
                attr_text = m_attr.group(1)
                stmt = (stmt[:m_attr.start()] + " " + stmt[m_attr.end():]).strip()
            attrs = _parse_attrs(attr_text)
            if _ARROW_RE.search(stmt):
                parts = [p for p in _ARROW_RE.split(stmt) if p.strip()]
                # 链式中 `[...]` 属性属于"边"，不写到节点上（避免边 label 顶掉节点名）
                cleaned = [ensure(p) for p in parts]
                cleaned = [c for c in cleaned if c]
                for a, b in zip(cleaned, cleaned[1:]):
                    edges.append({"src": a, "dst": b, "label": attrs.get("label", "")})
                for c in cleaned:  # 链式中首个/末个节点即使未成对也要入图
                    ensure(c)
            else:
                lead = stmt.strip()
                if lead.lower().startswith(("rank", "label=", "labelloc", "rankdir", "nodesep",
                                            "ranksep", "size", "style", "bgcolor", "fontname")):
                    continue
                ensure(lead, attrs)
        if len(order) > _MAX_NODES:
            keep = set(order[:_MAX_NODES])
            order = [n for n in order if n in keep]
            edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
        return {
            "directed": directed,
            "nodes": [nodes[n] for n in order],
            "edges": edges,
        }
    except Exception:
        return empty


# ---------------------------------------------------------------- 布局

def _node_size(label):
    text = str(label or "")
    width = max(84, min(236, int(len(text) * 7.4) + 26))
    return width, _NODE_H


def _compute_ranks(nodes, edges, directed):
    preds = {n["id"]: [] for n in nodes}
    for e in edges:
        a, b = e["src"], e["dst"]
        if b in preds:
            preds[b].append(a)
        if not directed and a in preds:
            preds[a].append(b)
    rank, state = {}, {}

    def dfs(node_id):
        if state.get(node_id) == 2:
            return rank[node_id]
        if state.get(node_id) == 1:      # 环：此分支贡献 0，避免无限递归
            return 0
        state[node_id] = 1
        best = 0
        for p in preds.get(node_id, ()):
            if p in preds:
                best = max(best, dfs(p) + 1)
        rank[node_id] = best
        state[node_id] = 2
        return best

    for n in nodes:
        dfs(n["id"])
    return rank


def _layout(graph):
    """分层布局，返回 ``{node_id: {"x","y","w","h"}}`` 与画布尺寸。"""
    nodes, edges = graph["nodes"], graph["edges"]
    rank = _compute_ranks(nodes, edges, graph.get("directed", True))
    sizes = {n["id"]: _node_size(n["label"]) for n in nodes}
    layers = {}
    for n in nodes:
        layers.setdefault(rank.get(n["id"], 0), []).append(n["id"])

    widths = {}
    for r, ids in layers.items():
        total = sum(sizes[i][0] for i in ids) + _COL_GAP * max(0, len(ids) - 1)
        widths[r] = total
    canvas_w = max(widths.values()) if widths else 400

    pos = {}
    for r, ids in layers.items():
        x = _MARGIN + (canvas_w - widths[r]) / 2.0
        y = _MARGIN + r * _RANK_GAP
        for node_id in ids:
            w, h = sizes[node_id]
            pos[node_id] = {"x": x + w / 2.0, "y": y + h / 2.0, "w": w, "h": h}
            x += w + _COL_GAP
    max_y = max((r for r in layers), default=0)
    canvas_h = _MARGIN * 2 + max_y * _RANK_GAP + _NODE_H
    return pos, int(canvas_w + _MARGIN * 2), int(canvas_h)


# ---------------------------------------------------------------- SVG 输出

def _boundary(node, tx, ty):
    dx, dy = tx - node["x"], ty - node["y"]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return node["x"], node["y"]
    scale = min(0.5 * node["w"] / max(abs(dx), 1e-6), 0.5 * node["h"] / max(abs(dy), 1e-6))
    return node["x"] + dx * scale, node["y"] + dy * scale


def _shape_svg(node):
    x, y, w, h = node["x"] - node["w"] / 2.0, node["y"] - node["h"] / 2.0, node["w"], node["h"]
    shape = (node.get("shape") or "box").lower()
    if shape in ("ellipse", "oval", "circle", "doublecircle"):
        return (f'<ellipse cx="{node["x"]:.1f}" cy="{node["y"]:.1f}" rx="{w/2:.1f}" ry="{h/2:.1f}" '
                f'fill="#e3f0fd" stroke="#3b6fb6" stroke-width="1.4"/>')
    if shape == "diamond":
        pts = f'{node["x"]:.1f},{y:.1f} {x+w:.1f},{node["y"]:.1f} {node["x"]:.1f},{y+h:.1f} {x:.1f},{node["y"]:.1f}'
        return f'<polygon points="{pts}" fill="#fff3e0" stroke="#e08b2d" stroke-width="1.4"/>'
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="7" '
            f'fill="#eef4fb" stroke="#3b6fb6" stroke-width="1.4"/>')


def dot_to_svg(src, title=""):
    """把 DOT 源码渲染为 SVG 字符串；解析失败/空图时返回空字符串（上层据此降级）。"""
    try:
        graph = parse_dot(src)
        if not graph["nodes"]:
            return ""
        pos, canvas_w, canvas_h = _layout(graph)
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_w} {canvas_h}" '
            f'width="{canvas_w}" height="{canvas_h}" font-family="Segoe UI,Microsoft YaHei,PingFang SC,Noto Sans CJK SC,WenQuanYi Micro Hei,sans-serif">',
            '<defs><marker id="wdot-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#5b6b7a"/></marker></defs>',
            f'<rect width="100%" height="100%" fill="#ffffff"/>',
        ]
        for e in graph["edges"]:
            a, b = pos.get(e["src"]), pos.get(e["dst"])
            if not a or not b:
                continue
            sx, sy = _boundary(a, b["x"], b["y"])
            ex, ey = _boundary(b, a["x"], a["y"])
            mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0
            dx, dy = ex - sx, ey - sy
            dist = max((dx * dx + dy * dy) ** 0.5, 1e-6)
            off = 0.10 * dist if abs(b["y"] - a["y"]) > 1 else 0.18 * dist
            cx, cy = mx - dy / dist * off, my + dx / dist * off
            marker = ' marker-end="url(#wdot-arrow)"' if graph.get("directed", True) else ""
            parts.append(f'<path d="M {sx:.1f} {sy:.1f} Q {cx:.1f} {cy:.1f} {ex:.1f} {ey:.1f}" '
                         f'fill="none" stroke="#5b6b7a" stroke-width="1.4"{marker}/>')
            if e.get("label"):
                parts.append(f'<text x="{mx:.1f}" y="{my - 4:.1f}" font-size="{_FONT_SIZE - 2}" '
                             f'fill="#7a8794" text-anchor="middle">{html.escape(str(e["label"])[:32])}</text>')
        for n in graph["nodes"]:
            p = pos.get(n["id"])
            if not p:
                continue
            parts.append(_shape_svg({"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"],
                                     "shape": n.get("shape", "box")}))
            label = str(n.get("label") or n["id"])
            if len(label) > 28:
                label = label[:27] + "…"
            parts.append(f'<text x="{p["x"]:.1f}" y="{p["y"] + 4:.1f}" font-size="{_FONT_SIZE}" '
                         f'fill="#1f3a5f" text-anchor="middle">{html.escape(label)}</text>')
        if title:
            parts.append(f'<text x="{_MARGIN}" y="{canvas_h - 8}" font-size="11" fill="#9aa7bd">'
                         f'{html.escape(str(title)[:80])}</text>')
        parts.append("</svg>")
        return "".join(parts)
    except Exception:
        return ""

# -*- coding: utf-8 -*-
"""docs/technical_doc.md 行号引用校验器（可重复运行）。

用法：
    python scripts/_check_doc_refs.py                 # 校验全文
    python scripts/_check_doc_refs.py --end-line 576  # 只校验第 1-4 章范围

功能：
1. 从 technical_doc.md 抽出所有 `路径:行号` / `:行号` 形式的引用；
2. 打开目标文件读到该行，判断该行是否真的包含引用处上下文提到的符号/关键字；
3. 输出三列清单：OK（正确）/ SUSPECT（疑似错误，附该行实际内容）/ UNKNOWN（无法判定）。

说明：
- 符号候选从引用所在的 Markdown 行的内联代码跨度（backtick）与 `name(` 形式中提取；
- 裸文件名（如 `parsing.py`）按 basename 在仓库内解析，重名则记 UNKNOWN；
- `:NNN` 裸行号引用继承上一处完整路径。
"""
import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "technical_doc.md"

_EXT = r"(?:py|md|json|txt|yml|yaml|toml|csv|html)"
# `路径:行号` / `路径:行号-行号` / `路径:行号 符号名` —— 反引号内允许跟随说明文字
# 同时兼容防漂移写法 `符号名@路径:行号` / `a.b.c@路径:行号`（前缀用非捕获组，组号不变）
# 第二条分支用于点文件（`.gitignore:20` 这类无扩展名的路径）：
# 这类引用此前恒不匹配，导致「SUSPECT 0」被误当成「全文引用无串位」。
# 注意：常规分支的 `\.%s` 必须在捕获组**内部**，否则路径会丢掉扩展名。
REF_RE = re.compile(r"`(?:[A-Za-z_][A-Za-z0-9_.]*@)?([A-Za-z0-9_./\\-]+\.%s|\.[A-Za-z0-9_.-]+):(\d+)(?:-(\d+))?[^`]*`" % _EXT)
# 裸行号 `（`:196-201`）` / `:80 keyword_route`
BARE_RE = re.compile(r"`(?<![\w./\\-]):(\d+)(?:-(\d+))?[^`]*`")
# 只给文件路径、不带行号（`services/writing.py`）：用于给后续裸行号定锚
PATHONLY_RE = re.compile(r"`([A-Za-z0-9_./\\-]+\.%s)`" % _EXT)
IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\s*\(")

# 不参与"是否为候选符号"判断的噪音词
NOISE = {
    "py", "md", "json", "txt", "yml", "yaml", "toml", "the", "and", "for", "not",
    "import", "from", "def", "class", "return", "self", "None", "True", "False",
    "env", "git", "http", "https", "www", "com", "shots", "png", "pdf", "csv",
    "streamlit", "python", "pip", "conda", "docker", "api", "url", "json",
    # 模块名/目录名：出现频次高但不足以定位到某一行
    "core", "tools", "ui", "app", "config", "docs", "storage", "scripts", "tests",
    "services", "state", "parsing", "library", "rag", "graph", "writing",
    "translate", "extern", "track", "citation_quality", "query_expansion",
    "guardrails", "sandbox", "router", "agent", "planner", "memory", "trace",
    "definitions", "registry", "providers", "llm", "common", "sidebar",
    "page_agent", "page_library", "page_qa", "page_reading", "page_search",
    "page_graph", "page_writing", "round", "source", "filter", "chunk", "chunks",
}


def build_index():
    """basename -> [相对路径]，用于解析裸文件名。"""
    idx = {}
    skip = {"dist", "build", "_internal", "__pycache__", ".git", "node_modules",
            "ai_research_agent", "version_history"}
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in skip for part in rel.parts):
            continue
        if p.suffix not in {".py", ".md", ".json", ".txt", ".yml", ".yaml", ".toml"}:
            continue
        idx.setdefault(p.name, []).append(str(rel).replace("\\", "/"))
    return idx


def resolve_path(spec, index):
    """把文档里的路径写法解析成仓库内的真实文件。返回 (相对路径|None, 说明)。"""
    cand = spec.replace("\\", "/").lstrip("./")
    if (ROOT / cand).is_file():
        return cand, ""
    hits = index.get(Path(cand).name, [])
    if not hits:
        return None, "文件不存在"
    if len(hits) == 1:
        return hits[0], "按 basename 唯一解析"
    # 多个同名：优先取与 spec 后缀匹配的
    tail = [h for h in hits if h.endswith(cand)]
    if len(tail) == 1:
        return tail[0], "按路径后缀解析"
    return hits[0], "basename 重名 %d 处，取其一：%s" % (len(hits), ", ".join(hits))


def extract_candidates(mdline, ref_span):
    """从 Markdown 行中抽出候选符号。

    ref_span 只覆盖 `路径:行号` 本体（不含其后紧跟的符号名），
    因此形如 `rag.py:48 MAX_VARIANT_DENSE` 的写法里，MAX_VARIANT_DENSE
    仍会被当作候选符号——这正是我们想校验的目标。
    """
    span_start, span_end = ref_span
    # 抹掉引用本体，避免把路径里的词当候选
    masked = mdline[:span_start] + " " * (span_end - span_start) + mdline[span_end:]
    cands = []
    # 1) 内联代码跨度里的标识符
    for code in re.findall(r"`([^`]+)`", masked):
        for tok in IDENT_RE.findall(code):
            if tok not in NOISE:
                cands.append(tok)
    # 2) 正文里 `name(` 形式
    for tok in CALL_RE.findall(masked):
        if tok not in NOISE:
            cands.append(tok)
    # 去重保序
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-line", type=int, default=1)
    ap.add_argument("--end-line", type=int, default=10 ** 9)
    ap.add_argument("--show-ok", action="store_true", help="同时打印判定为正确的条目")
    ap.add_argument("--doc", default=None, help="待校验的 Markdown（默认 docs/technical_doc.md）")
    args = ap.parse_args()

    doc = Path(args.doc).resolve() if args.doc else DOC
    lines = doc.read_text(encoding="utf-8").splitlines()
    index = build_index()
    cache = {}

    def read(rel):
        if rel not in cache:
            cache[rel] = (ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        return cache[rel]

    ok, suspect, unknown = [], [], []
    last_path = None

    for i, mdline in enumerate(lines, 1):
        if not (args.start_line <= i <= args.end_line):
            continue
        # 纯路径引用（不带行号）也参与"定位锚"的推进
        for m in PATHONLY_RE.finditer(mdline):
            rel, _ = resolve_path(m.group(1), index)
            if rel:
                last_path = rel
        # 先收集本行的完整路径引用（带位置），供裸行号按"位置最近的前驱"继承
        full = []
        for m in REF_RE.finditer(mdline):
            path, a, b = m.group(1), int(m.group(2)), m.group(3)
            rel, note = resolve_path(path, index)
            if rel:
                last_path = rel
            # span 只覆盖 `路径:行号`，不覆盖其后紧跟的符号名
            span = (m.start(1), m.end(3) if m.group(3) else m.end(2))
            full.append((m.start(), span, rel, note, a, int(b) if b else a))
        items = list(full)
        for m in BARE_RE.finditer(mdline):
            a = int(m.group(1))
            b = m.group(2)
            pre = [f for f in full if f[0] < m.start()]
            if pre:
                _, span, rel, note, _, _ = pre[-1]
                note = "裸行号（继承最近前驱 %s）" % rel
            else:
                span, rel, note = m.span(), last_path, "裸行号（继承上文 %s）" % last_path
            items.append((m.start(), span, rel, note, a, int(b) if b else a))

        for _, span, rel, note, a, b in items:
            rec = dict(doc_line=i, spec=(rel or "?"), a=a, b=b, note=note,
                       raw=mdline.strip()[:150])
            if rel is None:
                unknown.append(dict(rec, kind="文件不存在"))
                continue
            try:
                src = read(rel)
            except Exception as e:
                unknown.append(dict(rec, kind="读取失败 %s" % e))
                continue
            if a > len(src):
                suspect.append(dict(rec, kind="行号越界（文件共 %d 行）" % len(src),
                                    actual="<超出文件末尾>", cands=[]))
                continue
            seg = "\n".join(src[a - 1:b])
            cands = extract_candidates(mdline, span)
            hit = [c for c in cands if c in seg]
            rec["cands"] = cands
            rec["actual"] = src[a - 1].strip()[:160]
            if hit:
                ok.append(dict(rec, kind="OK", hit=hit))
                continue
            if not cands:
                unknown.append(dict(rec, kind="无符号上下文，无法判定"))
                continue
            # 疑似错误：尝试在文件内定位候选符号的真实行号
            found = []
            for c in cands:
                pat = re.compile(r"\b%s\b" % re.escape(c))
                # 优先"定义/赋值"行：def X / class X / X = ... / X: ... =
                defn = [j for j, sl in enumerate(src, 1)
                        if pat.search(sl) and re.match(
                            r"\s*(?:def\s+%s\b|class\s+%s\b|%s\s*(?::[^=]+)?=)" % (re.escape(c), re.escape(c), re.escape(c)), sl)]
                if defn:
                    found.append((c, defn[0], "def"))
                else:
                    any_hit = [j for j, sl in enumerate(src, 1) if pat.search(sl)]
                    if any_hit:
                        found.append((c, any_hit[0], "ref"))
            suspect.append(dict(rec, kind="未命中", suggest=found))

    print("=" * 78)
    print("行号引用校验：%s（第 %d-%s 行）" % (doc.name, args.start_line, args.end_line))
    print("=" * 78)
    print("总计 %d 条：OK %d / SUSPECT %d / UNKNOWN %d\n"
          % (len(ok) + len(suspect) + len(unknown), len(ok), len(suspect), len(unknown)))

    if args.show_ok:
        print("-" * 78)
        print("【OK】%d 条" % len(ok))
        for r in ok:
            print("  L%-5d %s:%d-%d  ✓ %s" % (r["doc_line"], r["spec"], r["a"], r["b"], ",".join(r["hit"])))

    print("-" * 78)
    print("【SUSPECT】%d 条 —— 该行未包含上下文提到的符号" % len(suspect))
    for r in suspect:
        print("\n  文档 L%d  `%s:%s`" % (r["doc_line"], r["spec"],
                                        str(r["a"]) if r["a"] == r["b"] else "%d-%d" % (r["a"], r["b"])))
        print("    判定    : %s %s" % (r["kind"], r.get("note", "")))
        print("    候选符号: %s" % (", ".join(r.get("cands", [])) or "—"))
        print("    该行实际: %s" % r["actual"])
        if r.get("suggest"):
            print("    建议行号: %s" % ", ".join("%s@%d(%s)" % (c, j, k) for c, j, k in r["suggest"]))

    print("\n" + "-" * 78)
    print("【UNKNOWN】%d 条 —— 无符号上下文，人工判定" % len(unknown))
    for r in unknown:
        print("  文档 L%-5d %-34s %s | %s" % (r["doc_line"], r["spec"] + ":" + str(r["a"]),
                                             r["kind"], r["actual"][:90]))
    print("\n" + "=" * 78)
    return 0 if not suspect else 1


if __name__ == "__main__":
    sys.exit(main())

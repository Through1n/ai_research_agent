"""评委实证：真实调用 RAG 问答，验证「是不是真的有用」。

仅作一次性验证，不进入四基线、不提交。用已加载的演示库（Transformer/BERT）问一个
有明确事实答案的问题，检查是否返回带引用的真实答案。
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.state import get_container
from services import library as _lib
from services import rag


def main():
    ctx = get_container()
    n_files = len(getattr(ctx, "files", []) or [])
    n_chunks = len(getattr(ctx, "chunks", []) or [])
    print("[库] 文献 %d 篇 · %d 个片段" % (n_files, n_chunks))
    if n_files == 0:
        print("[结论] 文献库为空，无法验证有用性（请先加载演示数据）。")
        return 1

    q = "Transformer 训练用了什么 GPU？一共用了多少块？"
    print("[问] %s" % q)
    try:
        res = rag.answer(ctx, q, use_rerank=False)
    except Exception as e:
        print("[结论] 调用抛异常（已优雅处理）：%s" % str(e)[:200])
        return 2

    answer = res.get("answer") if isinstance(res, dict) else str(res)
    sources = res.get("sources") if isinstance(res, dict) else []
    print("-" * 60)
    print("[答] %s" % (str(answer)[:800]))
    print("-" * 60)
    print("[来源数] %d" % len(sources or []))
    for s in (sources or [])[:5]:
        if isinstance(s, dict):
            print("  · %s  p%s  score=%.2f" % (s.get("source"), s.get("page"), float(s.get("score", 0) or 0)))
    refuses = isinstance(res, dict) and res.get("refused")
    has_cite = ("【" in str(answer)) or bool(sources)
    useful = bool(answer) and not refuses and has_cite
    print("[结论] 真的有用 = %s（有答案=%s 引用角标/来源=%s 拒答=%s）" %
          (useful, bool(answer), has_cite, refuses))
    return 0 if useful else 3


if __name__ == "__main__":
    sys.exit(main())

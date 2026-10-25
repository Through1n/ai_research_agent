"""全量 PDF 标题抽取对照表：修复前 vs 修复后（技术文档 4.x「8/8 符合预期」的可复跑来源）。

复跑命令（必须用 ai_research 环境）：
    python scripts/verify_title_extract.py

机制：
- 「修复前」基线钉死在 `BEFORE_REF`（= `5dbf937`，即标题修复提交 `0cee432` 的父提交），
  用 `git show <ref>:services/library.py` 动态载入为独立模块（lib_before），**不改动工作区**。
  注意：不能图省事用 `HEAD`——修复早已合入主干，`HEAD` 版已含 `_title_case_ratio`，
  会导致「修复前 == 修复后」，历史对照失效。
- 「修复后」用工作区的 `services/library.py`；
- 两者对同一批 `pdf/*.pdf` + `demo_data/*.pdf` 的首页文本跑 `guess_title_from_text`，
  逐份与 EXPECT 期望值核对，末尾打印 `核对通过 n/m`。
"""
import glob
import os
import subprocess
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from services import library as lib_after            # noqa: E402
from services import parsing                         # noqa: E402


# 标题修复提交 0cee432 的父提交（修复前基线）。请勿改成 HEAD：修复已合入主干。
BEFORE_REF = "5dbf937"


def load_before_lib():
    """从 BEFORE_REF 取修复前版本，动态载入为独立模块。"""
    src = subprocess.run(
        ["git", "show", f"{BEFORE_REF}:services/library.py"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    if "_title_case_ratio" in src:
        raise RuntimeError(
            f"{BEFORE_REF}:services/library.py 已含修复，不是真正的「修复前」基线，请重新钉定 BEFORE_REF"
        )
    mod = types.ModuleType("lib_before")
    mod.__file__ = ROOT + "/services/library.py"
    sys.modules["lib_before"] = mod
    exec(compile(src, "lib_before(HEAD)", "exec"), mod.__dict__)
    return mod


EXPECT = {
    "1706.03762v1.pdf": "Attention Is All You Need",
    "1706.03762v1_img.pdf": "(扫描件，无文本层)",
    "1706.03762v7.pdf": "Attention Is All You Need",
    "1810.04805v2.pdf": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
    "LongBook_540p_fixture.pdf": "Chapter 1 Deep Learning Foundations and Agent Systems",
    "UnderstandingDeepLearning_02_09_26_C.pdf": "Understanding Deep Learning",
    # 2026-10 演示语料 3→6 篇时新增的三篇（原先本脚本对 pdf/ 全量断言，缺期望值会直接 AssertionError）
    "1312.6114v10.pdf": "Auto-Encoding Variational Bayes",
    "1412.6980v9.pdf": "Adam: A Method for Stochastic Optimization",
    "1502.03167v3.pdf": "Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift",
}


def main():
    paths = sorted(glob.glob(ROOT + "/pdf/*.pdf")) + sorted(glob.glob(ROOT + "/demo_data/*.pdf"))
    lib_before = load_before_lib()

    rows = []
    for p in paths:
        name = p.replace("\\", "/").split("/")[-1]
        rel = p.replace(ROOT + "/", "")
        try:
            raw = open(p, "rb").read()
            doc = parsing.open_doc(raw)
            scanned = bool(parsing.detect_scanned(doc))
            text, _ = parsing.extract_page_text_column_aware(doc[0], return_blocks=True)
        except Exception as exc:
            rows.append((rel, "打开失败", str(exc), "?", "?"))
            continue
        before = lib_before.guess_title_from_text(text, "")
        after = lib_after.guess_title_from_text(text, "")
        rows.append((rel, before or "(空)", after or "(空)", "是" if scanned else "否", EXPECT.get(name, "?")))
        assert name in EXPECT, name

    print("=" * 130)
    print(f"{'文件':<48} | {'扫描件':<5} | {'修复前 title':<30} | {'修复后 title':<62}")
    print("=" * 130)
    for rel, before, after, scanned, exp in rows:
        print(f"{rel:<48} | {scanned:<5} | {before:<30.30} | {after:<62.62}")
    print("=" * 130)
    print()
    print("逐份核对（期望 vs 修复后）:")
    ok = 0
    for rel, before, after, scanned, exp in rows:
        name = rel.replace("\\", "/").split("/")[-1]
        if name == "1706.03762v1_img.pdf":
            verdict = "OK(扫描件如实为空)" if after == "(空)" else "不一致"
        else:
            core = exp.split(":")[0].split(" —")[0]
            verdict = "OK" if (after == exp or (len(after) > 8 and core.lower() in after.lower())) else "不一致"
        ok += verdict.startswith("OK")
        print(f"  {rel:<48} 期望={exp[:52]!r}")
        print(f"  {'':<48} 修复前={before[:60]!r}")
        print(f"  {'':<48} 修复后={after[:60]!r}  -> {verdict}")
    print()
    print(f"核对通过 {ok}/{len(rows)}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())

"""探针：术语表块注入位置 → 译文是否被回显污染。

复现用户 2026-10-26 截图缺陷：中英对照右栏每段都以「【术语表】翻译时必须采用
以下首选译法…」开头，且同一段里再接上英文原文，看起来「每段译文都一样」。

现场条件（截图右侧 14 段全都如此）：
  · 术语表 = DEFAULT_GLOSSARY_TEXT（31 条，块长 765 字符）；
  · 第 1 页段落多为**极短行**（标题 24 字符、邮箱 30 字符）；
  · 术语表块被 _glossary_inject 拼到**待译文本前面**，于是「内容：」之后的
    文字里 96% 是术语表指令、4% 才是待译正文。

三种 prompt 摆法 × 长/短正文，逐条比对模型输出：

  A 现状：块拼在**待译文本前面**（_glossary_inject）
  B 修正：块放在**指令区**（「内容：」之前），正文只放待译文本
  C 对照：不传术语表

缺陷标签：出现「【术语表】」/ 出现「→」条目行 / 译文尾部回显英文原文 / 空输出。

跑法：
  python scripts/_probe_glossary_echo.py [--provider zhipu|deepseek]
退出码 0 = 与预期一致（B、C 全部干净）；1 = 修正方案也脏。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.translate import (          # noqa: E402
    DEFAULT_GLOSSARY_TEXT, parse_glossary, glossary_prompt_block,
    _glossary_inject, apply_glossary,
)
from core.llm import call_llm_with_retry    # noqa: E402

PARA_LONG = ("The dominant sequence transduction models are based on complex recurrent or "
             "convolutional neural networks in an encoder-decoder configuration. The best "
             "performing models also connect the encoder and decoder through an attention "
             "mechanism. We propose a new simple network architecture, the Transformer, based "
             "solely on attention mechanisms.")

# 真实页第 1 页的段落是**极短行**（标题 / 单位 / 邮箱），而术语表块有 765 字符：
# 块比正文大一个数量级时，模型更容易把块当成"内容"一起输出。
PARA_SHORT = ["Attention Is All You Need",
              "Google Brain avaswani@google.com",
              "Niki Parmar* Google Research nikip@google.com"]


def make_ctx(provider="zhipu"):
    import tempfile
    import services.state as state_mod
    from core.providers import PROVIDERS
    tmp = tempfile.mkdtemp(prefix="ai4s_probe_gl_")
    tp = Path(tmp)
    state_mod.CHROMA_DIR = tp / "chroma"
    state_mod.LIBRARY_JSON = tp / "library.json"
    state_mod.MEMORY_JSON = tp / "memory.json"
    state_mod.PROFILE_JSON = tp / "profile.json"
    ctx = state_mod.Container()
    if provider != "zhipu":
        prov = {p.id: p for p in PROVIDERS}.get(provider)
        if prov is None:
            raise SystemExit("未知 provider：%s" % provider)
        ctx.llm.api_key = os.getenv(prov.key_env, "")
        ctx.llm.base_url = prov.base_url
        ctx.llm.model = prov.model
    return ctx


def raw_call(ctx, prompt):
    """绕开 writing._llm_text，直接看模型原始返回（保留降级文案）。"""
    llm = getattr(ctx, "llm", None)
    resp = call_llm_with_retry([{"role": "user", "content": prompt}],
                               temperature=0.2,
                               api_key=getattr(llm, "api_key", None),
                               base_url=getattr(llm, "base_url", None),
                               model=getattr(llm, "model", None))
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def verdict(out, src):
    """该输出的缺陷标签（空 = 干净）。src = 本次送译的英文原文。"""
    bad = []
    if "【术语表】" in out:
        bad.append("回显术语表块")
    if "→" in out:
        bad.append("残留条目行(→)")
    flat_out, flat_src = "".join(out.split()), "".join(str(src).split())
    if flat_src and len(flat_src) >= 8 and flat_src in flat_out:
        bad.append("回显英文原文")
    if not out:
        bad.append("空输出")
    return bad


INST = ("请将以下英文学术内容精准翻译为中文：保留专业术语（首次出现时可在括号内标注原文）、"
        "公式与引用标记，保持段落结构；只输出中文译文，不要解释。")


def main():
    provider = "zhipu"
    if "--provider" in sys.argv:
        provider = sys.argv[sys.argv.index("--provider") + 1]
    ctx = make_ctx(provider)
    gl = parse_glossary(DEFAULT_GLOSSARY_TEXT)
    block = glossary_prompt_block(gl)
    print("provider=%s model=%r available=%s" % (
        provider, getattr(getattr(ctx, "llm", None), "model", None),
        getattr(getattr(ctx, "llm", None), "available", None)))
    print("术语表条目 %d 条 / 块长 %d 字符\n" % (len(gl), len(block)))

    samples = [("长段落(%d 字符)" % len(PARA_LONG), PARA_LONG)]
    for s in PARA_SHORT:
        samples.append(("短行(%d 字符)%s" % (len(s), s[:24]), s))

    fix_dirty = 0
    for label, src in samples:
        results = {}

        pa = INST + "\n内容：\n" + _glossary_inject(src, gl)
        results["A 现状"] = apply_glossary(raw_call(ctx, pa), gl)

        pb = INST + ("\n\n" + block if block else "") + "\n内容：\n" + src
        results["B 修正"] = apply_glossary(raw_call(ctx, pb), gl)

        results["C 对照"] = raw_call(ctx, INST + "\n内容：\n" + src)

        # D 生产路径：ui.page_reading._safe_translate（指令区块 + 译后清洗 + 术语统一）
        try:
            from ui.page_reading import _safe_translate
            d_text, d_err = _safe_translate(ctx, src, glossary=gl)
            results["D 生产"] = d_text or ("<失败：%s>" % d_err)
        except Exception as e:
            results["D 生产"] = "<异常：%s: %s>" % (type(e).__name__, str(e)[:80])

        print("=== %s ===" % label)
        for k in ("A 现状", "B 修正", "C 对照", "D 生产"):
            out = results[k]
            bad = verdict(out, src)
            mark = "❌" if bad else "✅"
            print("  %s %-6s len=%-4d %s" % (mark, k, len(out), " / ".join(bad) or "干净"))
            print("      %r" % out[:90])
            if k in ("B 修正", "D 生产") and bad:
                fix_dirty += 1
        print()

    total = len(samples) * 2
    print("结论：修正后路径（B/C/D）脏样本 %d / %d" % (fix_dirty, total))
    return 1 if fix_dirty else 0


if __name__ == "__main__":
    sys.exit(main())

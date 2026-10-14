"""针对性验证：embedding 失败路径防御（判断「真改」还是「假改」的判决性测试）。

背景：services/state.py 把 embedding 改为「后台预热 + 按需取用」后，模型没加载上时
get_embed_fn() 会抛 RuntimeError。本测试用三个桩复现三种状态，走一遍「加载演示数据」
的真实入口（library.add_text_corpus，即侧边栏「🚀 一键加载演示数据」按钮逐篇调用的
那个函数），断言失败不再把整条链路打死：

  桩 1（已失败分支）：_build_embed_fn 抛异常，后台预热线程已把 _EMBED_ERR 落盘
      → 不抛未捕获异常；论文确实入库（ctx.papers 非空）；
        ctx.embed_degraded_reason 非空（降级原因被记录，而不是静默）
  桩 2（等待超时分支）：没有预热线程、事件永不就绪，AI4S_EMBED_WAIT_SEC=1
      → 有界等待 1s 后同样降级入库 + 记录原因
  桩 3（就绪对照）：模型正常就绪
      → 不降级：embedding 正常算出、embed_degraded_reason 保持 None
        （证明本测试能区分「降级」与「正常」，不是无论如何都绿的假测试）

用法：
    python scripts/_test_embed_degrade.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
os.environ["AI4S_EMBED_WAIT_SEC"] = "1"      # 有界等待压到 1s，测试不等 90s

import services.state as st_mod               # noqa: E402
from services import library                  # noqa: E402
from config import DEMO_PAPERS                # noqa: E402


class _FakeCollection:
    """只记录 upsert，不碰真实 chroma（测试绝不写 storage/）。"""

    def __init__(self):
        self.upserts = []

    def upsert(self, ids=None, documents=None, metadatas=None):
        self.upserts.append({"ids": list(ids or []), "documents": list(documents or [])})


class _FakeMemory:
    def update_profile(self, name):
        return None


class _FakeFn:
    """桩 3：正常就绪的 embedding 函数（确定性返回，不加载真实模型）。"""

    def __call__(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def _make_ctx():
    """不跑 Container.__init__（避免碰真实 storage/chroma/library.json），
    但保留**真实的** Container.embed / get_embed_fn 失败路径——被测对象正是它。"""
    ctx = st_mod.Container.__new__(st_mod.Container)
    ctx.papers = {}
    ctx.paper_order = []
    ctx.chunks = []
    ctx.likes = {}
    ctx.collection = _FakeCollection()
    ctx.memory = _FakeMemory()
    ctx.rebuild_bm25 = lambda: None
    ctx.save_library = lambda: None
    return ctx


def _reset_embed(build_fn=None, warm=False):
    """复位 embedding 全局状态（与 scripts/_test_coldstart_firstframe.py 同手法）。"""
    st_mod._EMBED_FN = None
    st_mod._EMBED_ERR = None
    st_mod._EMBED_WARMING = False
    st_mod._EMBED_READY.clear()
    st_mod._build_embed_fn = build_fn
    if warm:
        st_mod.warm_embed_async()


def _load_demo(ctx):
    """「加载演示数据」入口：与 ui/sidebar.py:433 逐篇调用 add_text_corpus 完全一致。"""
    for fn, info in DEMO_PAPERS.items():
        if fn not in ctx.files:
            library.add_text_corpus(ctx, fn, info.get("text", ""), info)
    return ctx


def run_case(name, build_fn, warm):
    _reset_embed(build_fn, warm)
    ctx = _make_ctx()
    err = None
    try:
        _load_demo(ctx)
    except Exception as e:                        # noqa: BLE001
        err = e
    reason = getattr(ctx, "embed_degraded_reason", None)
    print(f"  [{name}] papers={len(ctx.papers)} upsert={len(ctx.collection.upserts)} "
          f"未捕获异常={'有: ' + str(err)[:80] if err else '无'} "
          f"degraded_reason={'有' if reason else '无'}")
    return ctx, err, reason


def main():
    print("=" * 72)
    print("embedding 失败路径防御验证（AI4S_EMBED_WAIT_SEC=1）")
    print("=" * 72)
    n_demo = len(DEMO_PAPERS)

    # ---------- 桩 1：模型加载已失败（后台线程已落 _EMBED_ERR） ----------
    print("\n[1] 桩 1：_build_embed_fn 抛异常（模型加载失败分支）")
    ctx1, err1, reason1 = run_case(
        "load-failed", lambda: (_ for _ in ()).throw(RuntimeError("S3 下载失败：连接被重置")), warm=True)
    assert err1 is None, f"桩 1 抛了未捕获异常：{err1!r}"
    assert len(ctx1.papers) == n_demo, f"桩 1 论文未全部入库：{list(ctx1.papers)}"
    assert reason1, "桩 1 embed_degraded_reason 为空——降级原因被静默吞了！"
    for fn, p in ctx1.papers.items():
        assert p.get("embedding") is None, f"{fn} 降级值应为 None，实得 {p.get('embedding')!r}"
        assert p.get("fingerprints") == [], f"{fn} 降级值应为 []，实得 {p.get('fingerprints')!r}"
    print(f"      ✓ 未抛异常；{n_demo} 篇全部入库；降级值 None/[] 正确")
    print(f"      ✓ 降级原因已记录：{str(reason1)[:100]}")

    # ---------- 桩 2：模型仍在后台加载，有界等待超时 ----------
    print("\n[2] 桩 2：无预热线程 + 事件永不就绪（等待超时分支，等 1s）")
    ctx2, err2, reason2 = run_case("wait-timeout", lambda: None, warm=False)
    assert err2 is None, f"桩 2 抛了未捕获异常：{err2!r}"
    assert len(ctx2.papers) == n_demo, f"桩 2 论文未全部入库：{list(ctx2.papers)}"
    assert reason2, "桩 2 embed_degraded_reason 为空——降级原因被静默吞了！"
    print(f"      ✓ 未抛异常；{n_demo} 篇全部入库；降级原因已记录：{str(reason2)[:100]}")

    # ---------- 桩 3：模型正常就绪（对照组：不允许任何降级） ----------
    print("\n[3] 桩 3：模型正常就绪（对照组）")
    _reset_embed(build_fn=None, warm=False)
    st_mod._EMBED_FN = _FakeFn()
    st_mod._EMBED_READY.set()
    ctx3 = _make_ctx()
    try:
        _load_demo(ctx3)
    except Exception as e:                        # noqa: BLE001
        raise AssertionError(f"桩 3（模型就绪）不应抛异常：{e!r}")
    reason3 = getattr(ctx3, "embed_degraded_reason", None)
    assert reason3 is None, f"桩 3 模型就绪却记录了降级原因：{reason3!r}"
    assert len(ctx3.papers) == n_demo, f"桩 3 论文未全部入库：{list(ctx3.papers)}"
    for fn, p in ctx3.papers.items():
        assert p.get("embedding") == [0.1, 0.2, 0.3], f"{fn} 就绪路径 embedding 不对：{p.get('embedding')!r}"
        # 指纹规则（library.py:73 既有约定）：全文不足 seg=1200 字时本就返回 []
        text = DEMO_PAPERS[fn].get("text", "")
        if len(text) >= 1200:
            assert p.get("fingerprints"), f"{fn} 文本 {len(text)} 字足够长，就绪路径 fingerprints 不应为空"
        else:
            assert p.get("fingerprints") == [], f"{fn} 文本不足 1200 字，fingerprints 应为 []"
    print(f"      ✓ 未抛异常；{n_demo} 篇全部入库；embedding/fingerprints 正常算出；无降级记录")
    _reset_embed(build_fn=None, warm=False)

    print("\n" + "=" * 72)
    print("全部通过：embedding 失败不再打死「加载演示数据」，且降级原因对人可见。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

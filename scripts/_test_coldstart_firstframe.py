"""证明「首帧不再被 embedding 模型下载阻塞」——话术 19 白屏根因的判决性测试。

线上故障复盘：页面全白、body 仅 4 字、0 JS 错误、等 183s 导航框不出现。
根因是 Container.__init__ 同步等 chroma 从 S3 下 90MB 模型，而 Streamlit
「整段脚本跑完才发首帧」，脚本卡住 → 一帧发不出来。

本测试用两个桩复现两种冷缓存状态，断言**导航框照样出现**：
  桩 A：_build_embed_fn 直接抛异常（下载失败）
  桩 B：_build_embed_fn 永久睡眠（S3 连接挂死——线上真实形态）
两种情况下 AppTest 都必须拿到导航 radio，且 9 个页面项齐全。

另测桩 C：模型正常就绪时，embed() 仍走原路径（不许把优化改成退化）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
os.environ.setdefault("AI4S_EMBED_WAIT_SEC", "2")   # 有界等待压到 2s，测试不等 90s

from streamlit.testing.v1 import AppTest          # noqa: E402

import services.state as st_mod                   # noqa: E402

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.py")
APP = os.path.abspath(APP)

# 与 ui/sidebar.py 的 MENU / MENU_MAIN / MENU_ABOUT 逐字对应。
# ⚠️ 这里必须用**完整 label** 比对，不能用 d[:1] 比对首字符：🕸️/🏗️ 这类 emoji
# 带 U+FE0F 变体选择符，是 2 个字符，切片后与字面量对不上（实测因此误判 5≠7）。
MENU_MAIN = ["🤖 0. 智能总助手", "📁 1. 文献库管理", "💬 2. 跨文献问答与溯源",
             "📖 3. 版式阅读与智能翻译", "🔍 4. 论文检索与追踪",
             "🕸️ 5. 引用图谱与综述", "✍️ 6. 写作辅助与可视化"]
MENU_ABOUT = ["🏗️ A. 系统架构设计", "💡 B. 技术创新亮点"]


def _nav_destinations(at):
    """导航可达目的地 = 主区 radio 的 7 个选项 + 折叠区「关于本系统」的 2 个按钮。

    结构见 ui/sidebar.py:25 `MENU_MAIN = MENU[:7]` 与 :385 的 expander 按钮组
    （🏗️/💡 是按钮不是 radio——两组 radio 共用一个导航状态会互相打架，故分组用按钮）。
    """
    radio_opts, btns = [], []
    for r in at.radio:
        if r.label == "🧭 功能导航":
            radio_opts.extend(str(o) for o in r.options)
    btns.extend(str(b.label) for b in at.button)
    return radio_opts, btns


def run_case(name, patch_fn):
    orig = st_mod._build_embed_fn
    st_mod._EMBED_FN = None
    st_mod._EMBED_ERR = None
    st_mod._EMBED_WARMING = False
    st_mod._EMBED_READY.clear()
    st_mod._build_embed_fn = patch_fn
    try:
        t0 = time.time()
        at = AppTest.from_file(APP, default_timeout=60).run()
        dt = time.time() - t0
        radio_opts, btns = _nav_destinations(at)
        print(f"  [{name}] 首帧到达 {dt:.1f}s / 主区 radio {len(radio_opts)} 项 / "
              f"按钮 {len(btns)} 个")
        assert radio_opts == MENU_MAIN, (
            f"[{name}] 主区导航不是预期的 7 项：{radio_opts}——首帧仍被阻塞！")
        for lab in MENU_ABOUT:
            assert lab in btns, f"[{name}] 折叠区缺少「{lab}」"
        assert not at.exception, f"[{name}] 页面抛异常: {at.exception}"
        print(f"  [{name}] ✓ 首帧未被模型加载阻塞（9 个导航目的地全部就位）")
        return dt
    finally:
        st_mod._build_embed_fn = orig
        st_mod._EMBED_FN = None
        st_mod._EMBED_ERR = None
        st_mod._EMBED_WARMING = False
        st_mod._EMBED_READY.clear()


print("=== 桩 A：模型加载失败（抛异常）===")
run_case("A-load-error", lambda: (_ for _ in ()).throw(RuntimeError("S3 下载失败")))

print("=== 桩 B：模型加载永久挂死（线上真实形态）===")
run_case("B-hang-forever", lambda: time.sleep(10 ** 7))

print("=== 桩 C：模型正常就绪，embed() 语义不变 ===")


class _FakeFn:
    def __call__(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


st_mod._EMBED_FN = _FakeFn()
st_mod._EMBED_READY.set()
t0 = time.time()
fn = st_mod.get_embed_fn()
assert fn(["x"]) == [[0.1, 0.2, 0.3]], "就绪路径返回值不对"
# 再验证 Container.embed 走的是同一条就绪路径
c = st_mod.Container.__new__(st_mod.Container)
assert c.embed(["a", "b"]) == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]], "Container.embed 语义变了"
assert c.embed_fn(["z"]) == [[0.1, 0.2, 0.3]], "embed_fn 属性语义变了"
print(f"  [C] ✓ 就绪路径直通，耗时 {(time.time()-t0)*1000:.1f}ms")

print()
print("全部通过：首帧与 embedding 模型加载已解耦。")

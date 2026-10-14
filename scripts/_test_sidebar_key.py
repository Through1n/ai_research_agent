# -*- coding: utf-8 -*-
"""验证「侧边栏现场手填 API Key」这条演示路径的**完整状态迁移**。

背景（Round 16 决策）：线上不注入 Key，演示时在侧边栏「接入方式」当场粘贴。
这条路径此前只有 _degrade_drill_api.py 的附带覆盖（set_value 后看 last_error），
从没有测试断言过「无 Key → 填入 → 通道就绪 → 清空 → 回落」四态迁移本身。
选定为演示路径后，它必须是实测可用的，不能只凭代码阅读下结论。

实测内容（全程不发起任何真实网络请求：set_provider 只构造 LLMClient，
OpenAI(api_key=...) 构造器不发请求，已核对 services/state.py:239-250）：

    A 态  无 Key（模拟线上现状）
          · 侧边栏出现「📌 尚未配置 API Key」警告框
          · ctx.llm.available == False
          · llm_guard(ctx, ...) == False（问答/Agent 等页的守卫会关掉功能）
    B 态  填入 Key（演示时当场粘贴的动作）
          · ctx.llm.available == True
          · 出现「✅ Key 已填入，模型通道就绪」
          · 「📌 尚未配置 API Key」警告消失
          · llm_guard(ctx, ...) == True（守卫放行）
    C 态  清空 Key（演示完/误操作后的回落）
          · ctx.llm.available == False
          · 「📌 尚未配置 API Key」警告回来
          · 不残留任何「通道就绪」字样（避免假成功）

用法（必须用 ai_research 环境）：
    C:/Users/Administrator/anaconda3/envs/ai_research/python.exe scripts/_test_sidebar_key.py

纪律：用覆盖把三个 Key 环境变量全部置空串，保证无论本机 .env 里有没有真 Key，
A 态都是干净的离线态；置空串而非 del，是为了压过 load_dotenv 从 .env 注入的值
（load_dotenv 不覆盖已存在的环境变量，config.py:55-58）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# 三个入口全部压成空串：config.py:120 是 ZHIPU_API_KEY or LLM_API_KEY，
# core/llm.py:501 还有 OPENAI_API_KEY 兜底，漏一个就会让 A 态不干净。
for _k in ("ZHIPU_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"):
    os.environ[_k] = ""

from streamlit.testing.v1 import AppTest                # noqa: E402
from ui.sidebar import MENU                             # noqa: E402

PAGE_QA = [m for m in MENU if m.startswith("💬")][0]

NO_KEY_WARN = "尚未配置 API Key"
READY_OK = "Key 已填入，模型通道就绪"
QA_GUARD_MSG = "请先在左侧填入 API Key"

FAILS = []


def _check(tag, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", tag,
                           ("  → " + detail) if detail else ""))
    if not cond:
        FAILS.append(tag + ("  → " + detail if detail else ""))


def _all_text(at):
    """把页面所有元素的文案抓成一个字符串，便于做子串断言。"""
    parts = []
    for attr in ("warning", "success", "error", "info", "caption", "markdown", "text"):
        for el in getattr(at, attr, []):
            try:
                v = el.value
            except Exception:
                v = ""
            if isinstance(v, list):
                v = " ".join(str(x) for x in v)
            parts.append(str(v))
    return "\n".join(parts)


def _ss(at, key, default=None):
    """AppTest 的 session_state 是 SafeSessionState，没有 .get()，
    未初始化的 key 直接抛 AttributeError —— 这里包一层。"""
    try:
        return at.session_state[key]
    except Exception:
        return default


def _key_widget(at):
    """侧边栏 Key 输入框。注意：填入 Key 后「模型接入与账号」折叠区默认收起，
    每轮都要重新查 widget 列表，不能缓存（收起后 widget 是否还在树里取决于
    Streamlit 版本，缓存会写出假 FAIL）。"""
    return [w for w in at.text_input if getattr(w, "key", None) == "key_input"]


def _guard_open(ctx):
    """llm_guard 是问答/Agent 等页共用守卫；它对 ctx.llm.available 求值。
    这里直接 import 真实函数来断言"守卫放行"，比只断 available 更接近演示效果。"""
    from ui.common import llm_guard
    return bool(llm_guard(ctx, "测试"))


def main():
    print("=" * 78)
    print("侧边栏现场手填 Key：四态迁移实测（无真实网络请求）")
    print("=" * 78)

    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30)
    at.session_state["menu"] = PAGE_QA
    at.run()
    _check("A0 首轮渲染无异常", len(at.exception) == 0,
           str(at.exception[0].value)[:120] if at.exception else "")

    # ---------------- A 态：无 Key（线上现状） ----------------
    print("\n--- A 态：无 Key（模拟线上现状）---")
    ctx = _ss(at, "container")
    _check("A1 ctx.llm.available 为 False", not getattr(ctx.llm, "available", True))
    _check("A2 侧边栏有「尚未配置 API Key」警告", NO_KEY_WARN in _all_text(at))
    _check("A3 llm_guard 关闭（守卫会拦掉问答/Agent）", not _guard_open(ctx))
    kw = _key_widget(at)
    _check("A4 Key 输入框存在（无 Key 时折叠区自动展开）", len(kw) == 1,
           "找到 %d 个" % len(kw))
    if not kw:
        _report()
        return

    # ---------------- B 态：填入 Key（演示时当场粘贴） ----------------
    print("\n--- B 态：填入 Key（演示时当场粘贴的动作）---")
    kw[0].set_value("sidebar-demo-key-not-real").run()
    ctx = _ss(at, "container")
    txt_b = _all_text(at)
    _check("B1 ctx.llm.available 变 True", bool(getattr(ctx.llm, "available", False)))
    _check("B2 出现「Key 已填入，模型通道就绪」", READY_OK in txt_b)
    _check("B3 「尚未配置 API Key」警告消失", NO_KEY_WARN not in txt_b)
    _check("B3b 不出现自相矛盾（警告与成功提示不同时在场）",
           not (NO_KEY_WARN in txt_b and READY_OK in txt_b))
    _check("B4 llm_guard 放行（问答/Agent 不再被拦）", _guard_open(ctx))
    _check("B5 session_state.saved_api_key 已记录",
           _ss(at, "saved_api_key") == "sidebar-demo-key-not-real")

    # B 态补一项：守卫放行后，问答页点「提问」不应再出现"请先填 Key"的提示。
    # （这里不断言真实答案——那需要真 Key + 真网络，超出本测试范围；
    #   只断言"拦截文案不出现"，即守卫这一层确实让开了。）
    qa_btns = [b for b in at.button if b.label.startswith("🔎")]
    if qa_btns:
        ta = [t for t in at.text_area if t.label.find("问题") >= 0 or t.label.find("提问") >= 0]
        if ta:
            ta[0].set_value("测试问题").run()
        qa_btns2 = [b for b in at.button if b.label.startswith("🔎")]
        if qa_btns2:
            qa_btns2[0].click().run()
            _check("B6 问答页点提问后不再出现「请先填 Key」拦截",
                   QA_GUARD_MSG not in _all_text(at))
        else:
            print("  [SKIP] B6 未找到提问按钮（不应出现，记为 FAIL）")
            FAILS.append("B6 提问按钮消失")
    else:
        print("  [SKIP] B6 未找到提问按钮（不应出现，记为 FAIL）")
        FAILS.append("B6 提问按钮消失")

    # ---------------- C 态：清空 Key（回落） ----------------
    print("\n--- C 态：清空 Key（演示完 / 误操作后的回落）---")
    kw2 = _key_widget(at)
    if len(kw2) == 1:
        kw2[0].set_value("").run()
        ctx = _ss(at, "container")
        txt_c = _all_text(at)
        _check("C1 ctx.llm.available 当轮即回落 False",
               not getattr(ctx.llm, "available", True))
        _check("C2 当轮有即时反馈（「未填 Key」caption 或警告框）",
               ("未填 Key" in txt_c) or (NO_KEY_WARN in txt_c))
        # 已知边界（见 _pending_key_ready 文档）：清空那一回合 ctx.llm 还是旧客户端，
        # 警告框晚一回合出现；但不出现"两条对立提示同时在场"。
        at.run()
        txt_c2 = _all_text(at)
        _check("C3 下一回合「尚未配置 API Key」警告回来", NO_KEY_WARN in txt_c2)
        _check("C4 下一回合 llm_guard 重新关闭", not _guard_open(_ss(at, "container")))
        _check("C5 全程无「通道就绪」残留（无假成功）",
               READY_OK not in txt_c and READY_OK not in txt_c2)
    else:
        # 折叠区收起导致 widget 不在树里：用 session_state 直接清，同样覆盖回落语义
        print("  [INFO] 折叠区收起，key_input 不在 widget 树中（%d 个）；"
              "改走 session_state 清空" % len(kw2))
        at.session_state["key_input"] = ""
        at.session_state["saved_api_key"] = ""
        at.run()
        ctx = _ss(at, "container")
        txt_c = _all_text(at)
        _check("C1 ctx.llm.available 回落 False", not getattr(ctx.llm, "available", True))
        _check("C2 「尚未配置 API Key」警告回来", NO_KEY_WARN in txt_c)
        _check("C4 llm_guard 重新关闭", not _guard_open(ctx))

    # ---------------- D 态：Key 来自 .env/环境变量（线上注入 Key 时的路径） ----------------
    # 注意必须用**全新的 AppTest 实例**并在首次 run 之前就装好 mock：
    # set_provider 的重建条件是 `manual_key != self.llm.api_key`
    # （services/state.py:247）。在 A→B→C 流程之后 ctx.llm.api_key 已是空串，
    # 此时再给空 manual_key 两边相等 → 不重建 → resolve_key 压根不会被调用。
    # 生产环境的 .env 路径靠的是**启动时首次解析**（Container.__init__ →
    # resolve_key(prov, "") → 环境变量），所以要用"全新启动 + mock 预装"来复现。
    print("\n--- D 态：Key 来自环境变量、侧边栏输入框为空（Round 12 F2 场景）---")
    import core.providers as _prov
    import services.state as _state_mod
    _orig_resolve = _state_mod.resolve_key   # state.py:15 是 from-import，必须补它自己的命名空间

    def _fake_resolve(p, manual=""):
        return manual.strip() or "env-provided-key-simulated"

    _state_mod.resolve_key = _fake_resolve
    _prov.resolve_key = _fake_resolve
    try:
        at2 = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30)
        at2.session_state["menu"] = PAGE_QA
        at2.run()
        ctx_d = _ss(at2, "container")
        txt_d = _all_text(at2)
        _check("D0 首轮渲染无异常", len(at2.exception) == 0,
               str(at2.exception[0].value)[:120] if at2.exception else "")
        _check("D1 环境变量供 Key 时 ctx.llm.available 为 True",
               bool(getattr(ctx_d.llm, "available", False)))
        _check("D2 不误报「尚未配置 API Key」（新逻辑未误伤有 Key 站点）",
               NO_KEY_WARN not in txt_d)
        _check("D3 llm_guard 放行", _guard_open(ctx_d))
        # D4 不能写成恒真断言（`... or True` 那种），那等于没测。
        # 有 Key 时侧边栏不该出现「未填 Key」的误导性 caption。
        _check("D4 不出现「未填 Key：可使用内置演示数据」误导 caption",
               "未填 Key" not in txt_d)
    finally:
        _state_mod.resolve_key = _orig_resolve
        _prov.resolve_key = _orig_resolve

    _report()


def _report():
    print("\n" + "=" * 78)
    if FAILS:
        print("结果：FAIL %d 项" % len(FAILS))
        for f in FAILS:
            print("  ✗ " + f)
        sys.exit(1)
    print("结果：PASS —— 侧边栏现场手填 Key 四态迁移全部符合预期，可作演示路径")
    print("=" * 78)
    sys.exit(0)


if __name__ == "__main__":
    main()

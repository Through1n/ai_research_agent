# -*- coding: utf-8 -*-
"""故障演练补验脚本：degrade-fix 第二轮新增修复的**取证**（②残留成功态 + F2 横幅）。

为什么不写进 `_degrade_drill_api.py` / `_degrade_drill_file.py`：那两份分别是
degrade-api / degrade-file 的独占取证脚本，约定后续新增验证另起文件，避免并发
覆盖与取证口径混淆。本脚本只补他们两份**没有覆盖**的三处：

  V1  🌳 图谱页「📝 一键生成领域综述」  —— LLM 降级时不得出现「✅ 领域综述已生成」
  V2  🚀 图谱页「Future Work 前沿扫描」  —— 同上，不得出现「✅ 前沿方向扫描完成」
  V3  💬 问答页答案区                    —— 离线占位答案必须带可见降级标识，且不得给「✅ 引用校验通过」
  V4  🔔 F2 框架横幅                    —— Key 来自 .env 时重绘一轮后 last_error 应存活

用法（必须用 ai_research 环境）：
    C:/Users/Administrator/anaconda3/envs/ai_research/python.exe scripts/_degrade_drill_fix.py

纪律：全程 unittest.mock 注入，**不发起任何真实外网请求**；不修改任何业务代码。
"""
import os
import sys
import time
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

os.environ["ZHIPU_API_KEY"] = "drill-placeholder-key-not-a-real-key"

import openai                                           # noqa: E402
import httpx2                                           # noqa: E402
from streamlit.testing.v1 import AppTest                # noqa: E402

from core.llm import degraded_content                   # noqa: E402
from ui.sidebar import MENU                             # noqa: E402

PAGE_GRAPH = next(m for m in MENU if "图谱" in m)
PAGE_QA = next(m for m in MENU if "问答" in m)
PAGE_LIB = next(m for m in MENU if "文献库" in m)
PAGE_SEARCH = next(m for m in MENU if "检索" in m)
PAGE_AGENT = next(m for m in MENU if "总助手" in m)

LINE = "=" * 78
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def _hr(t):
    print("\n" + LINE)
    print(t)
    print(LINE)


def _texts(at_part):
    out = []
    for attr in ("error", "warning", "info", "success", "caption", "markdown"):
        try:
            for el in getattr(at_part, attr):
                v = getattr(el, "value", "")
                if v:
                    out.append("[%s] %s" % (attr, v))
        except Exception:
            pass
    try:                                   # st.status 的标题（综述/前沿的进度态）
        for el in at_part.status:
            v = getattr(el, "label", "") or getattr(el, "value", "")
            if v:
                out.append("[status] %s" % v)
    except Exception:
        pass
    return out


def _ss(at, key, default=None):
    """安全读 session_state。

    AppTest 的 session_state 是 `__getattr__` 代理：**没有 .get 方法**，
    `at.session_state.get(k, d)` 会抛 AttributeError（本脚本第一版就踩了）。
    未初始化的 key 抛 KeyError → 这里统一兜成 default。
    """
    try:
        return at.session_state[key]
    except Exception:
        return default


def _click(at, label):
    for b in at.button:
        if label in str(b.label):
            b.click().run()
            return True
    return False


def _llm_patch(exc_factory, counter):
    def _boom(self, *a, **kw):
        counter.append(1)
        raise exc_factory()

    return mock.patch.object(openai.resources.chat.completions.Completions,
                             "create", _boom)


def _exc_401():
    return openai.AuthenticationError(
        "Error code: 401 - {'error': {'code': '1001', 'message': 'invalid api key'}}",
        response=httpx2.Response(401, request=httpx2.Request("POST", ZHIPU_URL),
                                 headers={"content-type": "application/json"}),
        body={"error": {"code": "1001", "message": "invalid api key"}},
    )


def _goto(at, label):
    at.session_state["menu"] = label
    at.run()


# ============================ V1 / V2：图谱页两个成功态 ============================
def v1_v2_graph(at):
    _hr("V1 / V2：图谱页「领域综述」「前沿扫描」在 LLM 降级时不得报成功")
    _goto(at, PAGE_GRAPH)
    for label, ok_word, state_key in (
            ("📝 一键生成领域综述", "领域综述已生成", "review"),
            ("🚀 Future Work 前沿扫描", "前沿方向扫描完成", "future")):
        counter = []
        t0 = time.time()
        with _llm_patch(_exc_401, counter):
            hit = _click(at, label)
        dt = time.time() - t0
        body = _texts(at)
        fake_ok = [s for s in body if ok_word in s]
        # 两档降级标识任一命中都算"明示"：占位响应 / 离线确定性生成
        degraded_banner = [s for s in body
                           if ("以下内容不是模型生成" in s) or ("大模型输出" in s)]
        stored = str(_ss(at, state_key, "") or "")
        print("\n  ▶ 点击「%s」命中=%s  SDK调用=%d次  实测=%.1fs" % (label, hit, len(counter), dt))
        print("     未捕获异常: %s" % (list(at.exception) or "无"))
        print("     【判据】仍出现「✅ %s」= %s（修复后应为 False）" % (ok_word, bool(fake_ok)))
        if fake_ok:
            print("       → %s" % fake_ok[:2])
        print("     【判据】出现可见降级标识（非真实模型输出）= %s（应为 True）"
              % bool(degraded_banner))
        print("     session_state[%r] 前 60 字 = %r" % (state_key, stored[:60]))
        if degraded_banner:
            print("       → %s" % degraded_banner[0][:150])


# ============================ V2b：正常成功不得被误判成降级 ============================
def v2b_no_false_positive(at):
    """反向核验：LLM **真的**返回内容时，成功态必须照常出现（别把真答案判成降级）。"""
    _hr("V2b：反向核验 —— 真实模型输出不得被误判为降级（成功态必须照常）")
    _goto(at, PAGE_GRAPH)
    # 先清空上一组探针留下的降级态，否则页面会继续为旧内容重放降级标识（那是对的行为）
    at.session_state["review"] = ""
    at.session_state["future"] = ""
    at.run()
    from types import SimpleNamespace

    def _ok(*a, **kw):
        return SimpleNamespace(
            id="chatcmpl-drill", object="chat.completion", model="drill-model",
            choices=[SimpleNamespace(index=0, finish_reason="stop", message=SimpleNamespace(
                role="assistant",
                content="## 一、研究脉络\n这是一次真实的大模型输出（V2b 探针）。"))])

    for label, ok_word in (("📝 一键生成领域综述", "领域综述已生成"),
                           ("🚀 Future Work 前沿扫描", "前沿方向扫描完成")):
        with mock.patch.object(openai.resources.chat.completions.Completions, "create", _ok):
            hit = _click(at, label)
        body = _texts(at)
        ok_hit = [s for s in body if ok_word in s]
        mis = [s for s in body if ("以下内容不是模型生成" in s)
               or ("由本地确定性算法生成" in s)]
        print("\n  ▶ 点击「%s」命中=%s" % (label, hit))
        print("     【判据】正常成功仍报「✅ %s」= %s（应为 True）" % (ok_word, bool(ok_hit)))
        print("     【判据】误弹降级标识 = %s（应为 False）" % bool(mis))
        if mis:
            print("       → %s" % mis[:2])


# ============================ V5：F7 横幅只挂在「产生它的那一页」 ============================
def v5_f7_banner_scope(at):
    """F7 唯一的证明方式：真的走一遍「失败 → 切页 → 切回 → 换一页再失败」。

    两次失败注入的都是同一个 401、错误字符串**完全相同**——所以只要 ④/⑤ 成立，
    就证明靠的是 last_error_seq 而不是字符串比对。
    """
    _hr("V5：F7 —— LLM 失败横幅只挂在产生它的那一页（AppTest 真实走一遍）")

    def _seq():
        try:
            return at.session_state["container"].llm.last_error_seq
        except Exception:
            return None

    _goto(at, PAGE_GRAPH)
    with _llm_patch(_exc_401, []):
        hit1 = _click(at, "📝 一键生成领域综述")
    s1 = _seq()
    b1 = _banner(_texts(at))
    print("  ① 图谱页触发失败（命中=%s，seq=%s）→ 本页横幅 = %s（应为 True）"
          % (hit1, s1, bool(b1)))

    _goto(at, PAGE_LIB)
    b2 = _banner(_texts(at))
    print("  ② 切到【文献库】（未触发任何调用）→ 横幅 = %s（应为 False）" % bool(b2))

    _goto(at, PAGE_GRAPH)
    b3 = _banner(_texts(at))
    print("  ③ 切回【图谱页】→ 横幅 = %s（应为 True，失败页回来仍可见）" % bool(b3))

    _goto(at, PAGE_SEARCH)
    # 该按钮要求 text_area("qrefs") 非空才走 LLM，先塞两条参考文献
    at.session_state["qrefs"] = ("Vaswani et al. Attention Is All You Need.\n"
                                 "Devlin et al. BERT: Pre-training of Deep Bidirectional Transformers.")
    at.run()
    with _llm_patch(_exc_401, []):
        hit4 = _click(at, "🧹 分析并剔除应付式引用")
    # 该操作内部会 st.rerun()，再跑一轮拿最终渲染态
    at.run()
    s4 = _seq()
    b4 = _banner(_texts(at))
    print("  ④ 在【检索页】触发失败（命中=%s，seq=%s）→ 本页横幅 = %s（应为 True）"
          % (hit4, s4, bool(b4)))

    _goto(at, PAGE_GRAPH)
    b5 = _banner(_texts(at))
    print("  ⑤ 再切回【图谱页】→ 横幅 = %s（应为 False，横幅已挪到检索页）" % bool(b5))
    print("     last_error（两页完全相同，故只能靠 seq 区分）= %r"
          % str(_last_err(at))[:70])
    ok = (bool(b1) and not bool(b2) and bool(b3) and bool(b4) and not bool(b5))
    print("  【总判据】F7 五步全对 = %s" % ok)
    return ok


# ==================== V5b：F7 双路径之「侧边栏路径」 ====================
def v5b_sidebar_path(at):
    """路径 1：侧边栏「🔌 测试连通性」失败 —— 当轮可见 / 切走消失 / 切回重现。

    这条是 degrade-api 00:52 抓到的回归点（那版 `_SEQ_BEFORE` 取在页面循环之前，
    侧边栏更早渲染完 → seq 在取值前就 +1 → 判据恒 False → 横幅永不显示）。
    """
    _hr("V5b：F7 路径 1 —— 侧边栏「🔌 测试连通性」失败（演示现场第一反应会点的入口）")
    _goto(at, PAGE_SEARCH)
    with _llm_patch(_exc_401, []):
        hit = _click(at, "🔌 测试连通性")
    b1 = _banner(_texts(at))
    print("  点击命中=%s → 失败当轮横幅 = %s（应为 True）" % (hit, bool(b1)))
    if b1:
        print("    → %s" % b1[0][:150])
    _goto(at, PAGE_LIB)
    b2 = _banner(_texts(at))
    print("  切到【文献库】→ 横幅 = %s（应为 False）" % bool(b2))
    _goto(at, PAGE_SEARCH)
    b3 = _banner(_texts(at))
    print("  切回【检索页】→ 横幅 = %s（应为 True，出问题的页面回来仍可见）" % bool(b3))
    print("  【路径 1 判据】当轮可见 + 切走消失 + 切回重现 = %s"
          % (bool(b1) and not bool(b2) and bool(b3)))


# ==================== V6：Agent 页「✅ 已完成」假成功 ====================
def v6_agent(at):
    """Agent 页是 _PAGES 第一个、默认落地页，曝光度最高，必须实测（不能只读代码）。

    走真实路径：点示例任务「用此任务 ▶」→ 后台线程跑 agent → 401 注入 →
    等结果回填 → 断言「✅ 已完成」不再出现、降级标识出现。
    """
    _hr("V6：Agent 页「✅ 已完成」在 LLM 降级时不得报成功（真实点按钮 + 后台任务）")
    _goto(at, PAGE_AGENT)
    at.session_state["agent_runs"] = []
    at.run()
    counter = []
    hit = False
    with _llm_patch(_exc_401, counter):
        hit = _click(at, "用此任务 ▶")
        # 后台任务在线程里跑，patch 必须一直开着（否则线程会打真实外网）
        for _ in range(40):
            time.sleep(1.0)
            at.run()
            if _ss(at, "agent_runs"):
                break
    body = _texts(at)
    fake_ok = [s for s in body if "已完成" in s and "未输出任何内容" not in s]
    banner = [s for s in body if "以下内容不是模型生成" in s]
    runs = _ss(at, "agent_runs", [])
    print("  点击「用此任务 ▶」命中=%s  SDK调用=%d次  agent_runs=%d条"
          % (hit, len(counter), len(runs or [])))
    print("  未捕获异常: %s" % (list(at.exception) or "无"))
    print("  【判据】仍出现「✅ 已完成」= %s（修复后应为 False）" % bool(fake_ok))
    if fake_ok:
        print("    → %s" % fake_ok[:2])
    print("  【判据】出现可见降级标识「以下内容不是模型生成」= %s（应为 True）"
          % bool(banner))
    for s in banner[:2]:
        print("    → %s" % s[:160])


# ============ V7：侧边栏「模型通道就绪」在已失败时必须改成失败措辞 ============
def v7_sidebar_ready_wording(at):
    """degrade-api 观察的"假就绪"：调用已失败时侧边栏仍报 `✅ Key 已填入，模型通道就绪`。

    第一版修复被实测打回：`:234` 读的是 `set_provider` **重建之后**的 last_error（恒为 None），
    而回补块在判断之后执行 → 分支永不命中。改读 `_prev[2]`（重建前的值）后必须实测确认。
    """
    _hr("V7：侧边栏「模型通道就绪」在调用已失败时不得报成功（degrade-api 观察）")

    def _side():
        out = []
        for attr in ("success", "caption", "warning", "error"):
            try:
                for el in getattr(at.sidebar, attr):
                    v = getattr(el, "value", "")
                    if v:
                        out.append("[%s] %s" % (attr, v))
            except Exception:
                pass
        return out

    # 先清空 last_error（前面的 V1/V2/V6 都注入过失败），拿到真正的"干净态"基线
    try:
        at.session_state["container"].llm.last_error = None
        at.session_state["container"].llm.last_error_kind = None
    except Exception:
        pass
    _goto(at, PAGE_QA)
    s0_ok = [x for x in _side() if "通道就绪" in x]
    print("  【干净态基线】last_error 已清空 → 侧边栏报「✅ 模型通道就绪」= %s（应为 True）"
          % bool(s0_ok))
    for s in s0_ok[:1]:
        print("    → %s" % s[:140])

    with _llm_patch(_exc_401, []):
        _click(at, "🔌 测试连通性")
    e1 = _last_err(at)
    s1 = _side()
    ok1 = [x for x in s1 if "通道就绪" in x]
    bad1 = [x for x in s1 if "最近一次大模型调用失败" in x]
    print("  【失败当轮】last_error=%s" % ("非空" if e1 else "空"))
    print("    仍报「✅ 模型通道就绪」= %s（**已知滞后一轮**：这一行在 set_provider 时渲染，"
          "早于「🔌 测试连通性」那次调用；同轮侧边栏下方已有「连通性测试未通过」、"
          "主体区已有横幅）" % bool(ok1))
    print("    出现「⚠️ 最近一次大模型调用失败」= %s（当轮应为 False，下一轮才翻）" % bool(bad1))
    for s in (ok1 or bad1)[:2]:
        print("    → %s" % s[:160])

    at.run()                      # 重绘一轮
    s2 = _side()
    ok2 = [x for x in s2 if "通道就绪" in x]
    bad2 = [x for x in s2 if "最近一次大模型调用失败" in x]
    print("  【重绘一轮后】仍报「✅ 模型通道就绪」= %s（应为 False）；"
          "失败措辞 = %s（应为 True）" % (bool(ok2), bool(bad2)))
    for s in (bad2 or ok2)[:2]:
        print("    → %s" % s[:160])
    print("  【V7 总判据】干净态报 ✅（未失败不该吓唬人）＋ 失败后下一轮翻成 ⚠️ = %s"
          % (bool(s0_ok) and not ok2 and bool(bad2)))

    # 顺带：F3 那条「离线演示仍可用」是否真出现在横幅里
    body = _texts(at)
    off = [s for s in body if "离线演示仍可用" in s]
    print("  【附带】主体区横幅含「离线演示仍可用」= %s（team-lead 要求加）" % bool(off))
    for s in off[:1]:
        print("    → %s" % s[:200])


# ============================ V3：问答页答案区 ============================
def v3_qa(at):
    _hr("V3：问答页答案区——离线占位答案必须带可见降级标识，且不得给「✅ 引用校验通过」")
    _goto(at, PAGE_QA)
    # 直接往 qa_runs 里塞一条"降级答案"，走页面真实的历史问答渲染循环
    at.session_state["qa_runs"] = [{
        "id": 1, "q": "Transformer 训练用了什么 GPU？",
        "a": degraded_content("API Key 无效或已过期（HTTP 401/403），请在侧边栏检查 Key 后重试"),
        "refused": False, "top_score": 0.83, "scope": "全部文献",
        "issues": [], "unverified": [], "sources": [], "debug": {},
    }]
    at.run()
    body = _texts(at)
    banner = [s for s in body if "以下内容不是模型生成" in s]
    fake_pass = [s for s in body if "引用校验通过" in s]
    print("  未捕获异常: %s" % (list(at.exception) or "无"))
    print("  【判据】答案区出现可见降级标识 = %s（应为 True）" % bool(banner))
    print("  【判据】仍给「✅ 引用校验通过」= %s（修复后应为 False）" % bool(fake_pass))
    if fake_pass:
        print("    → %s" % fake_pass[:2])
    for s in (banner + [x for x in body if "未做引用校验" in x])[:3]:
        print("    → %s" % s[:170])
    at.session_state["qa_runs"] = []


# ============================ V4：F2 框架横幅 ============================
def v4_banner(at):
    _hr("V4：F2 —— LLM 失败横幅（app.py 框架层，消费 ctx.llm.last_error）")
    _goto(at, PAGE_QA)
    key_widget = [w for w in at.text_input if getattr(w, "key", None) == "key_input"]
    if key_widget:
        key_widget[0].set_value("").run()      # 模拟"Key 来自 .env，界面输入框为空"
    with _llm_patch(_exc_401, []):
        _click(at, "🔌 测试连通性")
    e1 = _last_err(at)
    b1 = _banner(_texts(at))
    at.run()                                    # 再跑一轮 = 用户切页 / 任意重绘
    e2 = _last_err(at)
    b2 = _banner(_texts(at))
    print("  【判据】失败当轮 last_error = %s；同轮即出现横幅 = %s（应为 True/True）"
          % ("有" if e1 else "无", bool(b1)))
    print("  【判据】重绘一轮后 last_error 仍存活 = %s（修复前为「无」，横幅消失）；"
          "该轮仍有横幅 = %s" % ("有" if e2 else "无", bool(b2)))
    for s in (b1 or b2)[:3]:
        print("    → %s" % s[:170])
    # ---- 边界核对：演示中途 Key 真的换了，last_error 不许带过来 ----
    if key_widget:
        key_widget[0].set_value("brand-new-key-xyz").run()
        e3 = _last_err(at)
        print("  【边界】Key 被换成新值后 last_error = %s（应为「无」，"
              "不把上一次的失败原因带新 Key 上）" % ("有" if e3 else "无"))
        key_widget[0].set_value("").run()
    return bool(e2)


def _banner(body):
    """只认框架层横幅（`llm_failure_banner` 的 warning），不要按"大模型调用失败"匹配——

    侧边栏那条新的失败措辞 `⚠️ Key 已填入，但最近一次大模型调用失败了…` 也含这五个字，
    会把"切页后横幅消失"误判成"仍在显示"（V5b 就是这样被污染过一次）。
    """
    return [s for s in body if "点击页面内其他功能仍可继续使用" in s]


def _last_err(at):
    try:
        return at.session_state["container"].llm.last_error
    except Exception:
        return None


def main():
    t0 = time.time()
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=600)
    at.run()
    print("AI4S 故障演练补验（②残留成功态 + F2 横幅）　进程内 AppTest，全程 mock，无真实外网请求")
    print("首渲染 %.1fs · 未捕获异常=%s" % (time.time() - t0, list(at.exception) or "无"))
    v1_v2_graph(at)
    v2b_no_false_positive(at)
    v5_f7_banner_scope(at)
    v5b_sidebar_path(at)
    v6_agent(at)
    v7_sidebar_ready_wording(at)
    v3_qa(at)
    v4_banner(at)
    print("\n" + LINE)
    print("补验结束，总耗时 %.1fs（以上全部为脚本真实输出，无编造）" % (time.time() - t0))
    print(LINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

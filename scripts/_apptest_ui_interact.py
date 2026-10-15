"""P7 UI/UX 交互点击验证（AppTest 模拟真实点击）。

与 scripts/_smoke_ui.py 的分工：冒烟只验证「渲染不抛异常」，本脚本验证
「交互真的点下去行为正确」——本轮新增/改造的交互控件必须过这一关：
  1) 问答页「🎯 待跳转操作条」：写入 qa_jump_page → 渲染操作条 →
     点「📖 立即打开阅读页」→ session_state["menu"] 切到阅读页且 0 异常；
  2) 同一条上点「✅ 已查看，清除」→ payload 清空、0 异常；
  3) 三个教学式空状态卡片真实渲染（问答无历史 / Agent 无记录 / 图谱未构建）；
  4) 图谱页「🎭 演示模式示意图谱」点击后 KPI 指标条渲染真实数值；
  5) 文献库页「文献库」页签 KPI 概览指标条渲染。

用法：python scripts/_apptest_ui_interact.py
退出码：0 = 全部通过；1 = 有失败项（明细打印到 stdout）。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

from ui.sidebar import MENU  # noqa: E402

READING_LABEL = MENU[3]          # 📖 版式阅读与智能翻译
QA_LABEL = MENU[2] if "问答" in MENU[2] else next(m for m in MENU if "问答" in m)
GRAPH_LABEL = next(m for m in MENU if "图谱" in m)
AGENT_LABEL = MENU[0]
LIB_LABEL = next(m for m in MENU if "文献库" in m)

problems = []


def _fail(tag, msg):
    print("  ✗ %s：%s" % (tag, msg))
    problems.append((tag, msg))


def _ok(tag, msg=""):
    print("  ✅ %s%s" % (tag, ("：" + msg) if msg else ""))


def _ss_get(at, key, default=None):
    """AppTest 的 session_state 代理没有 .get()，用 try/except 封装。"""
    try:
        return at.session_state[key]
    except KeyError:
        return default


def _md_text(at):
    return "\n".join(str(m.value) for m in at.markdown)


def _exceptions(at):
    return [str(e.value)[:200] for e in at.exception]


def _new():
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=240)
    at.run()
    return at


def _menu_radio(at, label):
    for r in at.radio:
        try:
            if label in list(r.options):
                return r
        except Exception:
            continue
    return None


def _goto(at, label):
    r = _menu_radio(at, label)
    if r is None:
        return False
    r.set_value(label).run()
    at.run()      # 结算回合（与 _smoke_ui 一致）
    return True


def _click(at, label):
    for b in at.button:
        if b.label.strip() == label.strip():
            b.click().run()
            return True
    return False


def _load_demo(at):
    t0 = time.time()
    if not _click(at, "🚀 一键加载演示数据（v1/v7/BERT）"):
        _fail("演示数据", "未找到「一键加载演示数据」按钮")
        return False
    print("  （演示数据加载耗时 %.1fs）" % (time.time() - t0))
    return True


# ----------------------------------------------------------------  case 1/2
def case_jump_bar():
    print("\n[1] 问答页待跳转操作条：一键前往阅读页")
    at = _new()
    _load_demo(at)
    if not _goto(at, QA_LABEL):
        _fail("导航", "找不到问答页菜单项")
        return
    at.session_state["qa_jump_page"] = {
        "source": "1706.03762v7.pdf", "page": 3, "para_idx": 2, "from": "qa"}
    at.run()
    text = _md_text(at)
    if "问答溯源待跳转" not in text or "1706.03762v7.pdf" not in text:
        _fail("操作条渲染", "未渲染待跳转操作条（markdown 中找不到标题/文件名）")
    else:
        _ok("操作条渲染", "标题与目标文献均在")
    btns = [b.label for b in at.button]
    if "📖 立即打开阅读页" not in btns:
        _fail("操作条按钮", "找不到「📖 立即打开阅读页」按钮：%s" % btns[:8])
        return
    _click(at, "📖 立即打开阅读页")
    errs = _exceptions(at)
    if errs:
        _fail("跳转点击", "点击后出现未捕获异常：%s" % errs[:2])
        return
    menu = _ss_get(at, "menu")
    if menu != READING_LABEL:
        _fail("跳转落点", "点击后 menu=%r，应为 %r" % (menu, READING_LABEL))
    else:
        _ok("跳转落点", "menu 已切到「%s」" % READING_LABEL)
    # 端到端证据：阅读页真的消费了跳转载荷（page_reading._consume_qa_jump
    # 会渲染「🎯 已从问答页跳转」并清空 qa_jump_page + 删除 jump_page/paper
    # 两个 URL 参数——所以最终 URL 只剩 p 是**设计预期**，不是写入失败）。
    successes = "\n".join(str(s.value) for s in at.success)
    if "已从问答页跳转" not in successes:
        _fail("端到端消费", "阅读页未渲染「已从问答页跳转」，深链可能没生效：%r"
                          % successes[:120])
    else:
        _ok("端到端消费", "阅读页已消费跳转载荷并定位（success 提示可见）")
    if _ss_get(at, "qa_jump_page") is not None:
        _fail("一次性语义", "qa_jump_page 未被消费清空：%r" % (_ss_get(at, "qa_jump_page"),))
    else:
        _ok("一次性语义", "qa_jump_page 已消费清空，不会重复跳转")
    qp = dict(at.query_params)
    print("  （最终 URL 参数：%s —— jump_page/paper 被阅读页按设计消费后删除，属预期）" % qp)


def case_jump_clear():
    print("\n[2] 问答页待跳转操作条：已查看清除")
    at = _new()
    _load_demo(at)
    if not _goto(at, QA_LABEL):
        _fail("导航", "找不到问答页菜单项")
        return
    at.session_state["qa_jump_page"] = {"source": "a.pdf", "page": 1, "para_idx": None}
    at.run()
    if not _click(at, "✅ 已查看，清除"):
        _fail("清除按钮", "找不到「✅ 已查看，清除」按钮")
        return
    errs = _exceptions(at)
    if errs:
        _fail("清除点击", "点击后出现未捕获异常：%s" % errs[:2])
        return
    if _ss_get(at, "qa_jump_page") is not None:
        _fail("清除语义", "点击后 qa_jump_page=%r，应为 None"
                          % (_ss_get(at, "qa_jump_page"),))
    else:
        _ok("清除语义", "payload 已清空，操作条不再渲染")


# ----------------------------------------------------------------  case 3
def case_empty_cards():
    print("\n[3] 教学式空状态卡片（演示数据已加载）")
    at = _new()
    _load_demo(at)

    if not _goto(at, QA_LABEL):
        _fail("导航", "找不到问答页菜单项")
    else:
        text = _md_text(at)
        if "还没有问答记录" not in text:
            _fail("问答空状态", "未渲染「还没有问答记录」教学卡片")
        else:
            _ok("问答空状态", "「还没有问答记录」已渲染")

    if not _goto(at, AGENT_LABEL):
        _fail("导航", "找不到 Agent 页菜单项")
    else:
        text = _md_text(at)
        if "还没有执行记录" not in text:
            _fail("Agent 空状态", "未渲染「还没有执行记录」教学卡片")
        else:
            _ok("Agent 空状态", "「还没有执行记录」已渲染")

    if not _goto(at, GRAPH_LABEL):
        _fail("导航", "找不到图谱页菜单项")
    else:
        text = _md_text(at)
        if "尚未构建图谱" not in text:
            _fail("图谱空状态", "未渲染「尚未构建图谱」教学卡片")
        else:
            _ok("图谱空状态", "「尚未构建图谱」已渲染")
    errs = _exceptions(at)
    if errs:
        _fail("空状态渲染", "出现未捕获异常：%s" % errs[:2])


# ----------------------------------------------------------------  case 4
def case_graph_metrics():
    print("\n[4] 图谱页演示模式 + KPI 指标条（本地重操作）")
    at = _new()
    _load_demo(at)
    if not _goto(at, GRAPH_LABEL):
        _fail("导航", "找不到图谱页菜单项")
        return
    t0 = time.time()
    if not _click(at, "🎭 演示模式示意图谱"):
        _fail("演示图谱按钮", "找不到「🎭 演示模式示意图谱」按钮")
        return
    errs = _exceptions(at)
    if errs:
        _fail("演示图谱点击", "点击后出现未捕获异常：%s" % errs[:2])
        return
    text = _md_text(at)
    missing = [k for k in ("逻辑节点", "引用边", "基石论文", "边缘衍生") if k not in text]
    if missing:
        _fail("KPI 指标条", "指标条缺少：%s" % missing)
    else:
        _ok("KPI 指标条", "4 个 hero 指标均已渲染（耗时 %.1fs）" % (time.time() - t0))
    if "演示模式示意图谱" not in text and "演示模式" not in text:
        _fail("演示标识", "未看到演示模式非真实引用标识")
    else:
        _ok("演示标识", "「演示模式·非真实引用」标识可见")


# ----------------------------------------------------------------  case 5
def case_library_metrics():
    print("\n[5] 文献库页 KPI 概览指标条")
    at = _new()
    _load_demo(at)
    if not _goto(at, LIB_LABEL):
        _fail("导航", "找不到文献库页菜单项")
        return
    text = _md_text(at)
    missing = [k for k in ("文献篇数", "片段总数", "多版本组", "可定位原文") if k not in text]
    if not missing:
        _ok("KPI 概览", "4 个指标均已渲染")
        return
    # 页签内容可能未激活：尝试点击「文献库」页签后再查一次
    clicked = False
    for b in at.button:
        if b.label.strip() == "文献库":
            try:
                b.click().run()
                clicked = True
            except Exception as e:
                print("  （点击页签失败：%s）" % str(e)[:120])
            break
    text2 = _md_text(at)
    missing2 = [k for k in ("文献篇数", "片段总数", "多版本组", "可定位原文") if k not in text2]
    if missing2:
        _fail("KPI 概览", "指标条缺少：%s（clicked_tab=%s）" % (missing2, clicked))
    else:
        _ok("KPI 概览", "点击「文献库」页签后 4 个指标均已渲染")


def main():
    print("=" * 72)
    print("P7 UI/UX 交互点击验证（AppTest）")
    print("=" * 72)
    for fn in (case_jump_bar, case_jump_clear, case_empty_cards,
               case_graph_metrics, case_library_metrics):
        try:
            fn()
        except Exception as e:            # 单场景崩溃不带走其余场景的证据
            import traceback
            _fail(fn.__name__, "场景自身异常：%s" % str(e)[:200])
            traceback.print_exc()
    print("\n" + "=" * 72)
    if problems:
        print("❌ 交互验证未全绿：%d 处失败" % len(problems))
        for tag, msg in problems:
            print("   - %s: %s" % (tag, msg[:160]))
        return 1
    print("🎉 交互验证全部通过（跳转落点 / 清除语义 / 空状态卡片 / KPI 指标条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

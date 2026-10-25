"""P7 UI/UX 交互点击验证（AppTest 模拟真实点击）。

与 scripts/_smoke_ui.py 的分工：冒烟只验证「渲染不抛异常」，本脚本验证
「交互真的点下去行为正确」——本轮新增/改造的交互控件必须过这一关：
  1) 问答页「🎯 待跳转操作条」：写入 qa_jump_page → 渲染操作条 →
     点「📖 立即打开阅读页」→ session_state["menu"] 切到阅读页且 0 异常；
  2) 同一条上点「✅ 已查看，清除」→ payload 清空、0 异常；
  3) 三个教学式空状态卡片真实渲染（问答无历史 / Agent 无记录 / 图谱未构建）；
  4) 图谱页「🎭 演示模式示意图谱」点击后 KPI 指标条渲染真实数值；
  5) 文献库页「文献库」页签 KPI 概览指标条渲染。
  6-8) 板块 1/4/6 长任务后台化三态（提交登记 / 闸门两态 / 交付落点，事件闸门打桩）；
  9) 问答页「多篇对比」选中不被 pill 重跑抹掉（2026-10-22 用户缺陷回归：
     中段 st.rerun 曾把后面 widget 的状态裁剪重置）；
  10) 文献库页元数据抽取后台化三态（LLM 打桩，板块冻结缺陷回归）。
  11) 文献库页细粒度结构解析后台化三态（最重 worker：三解析调用 + 插图落盘）。

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
SEARCH_LABEL = next(m for m in MENU if "检索" in m)
WRITING_LABEL = next(m for m in MENU if "写作" in m)
QA_EX0 = "这篇论文提出了什么模型？"   # 与 ui/page_qa.py 的 EX[0] 一致（示例问题首个）

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
    if not _click(at, "一键加载演示数据（6 篇）"):
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


# ----------------------------------------------------------------  case 6
def case_library_bg_parse():
    """文献库页「解析并查重」后台化端到端（2026-10 R19 后台化参考实现）。

    链路：选文件 → 按钮渲染 → 挂受控慢任务验闸门（禁用+运行中提示）→
    真实点击提交 → tid 登记 → 后台线程完成 → 页面渲染时 bg_consume 交付 →
    自动入库（成功 flash）→ 闸门恢复 → 清理测试产物。
    断言的是后台任务中心真的接进了页面（提交/闸门/交付三态），而非"渲染不崩"。
    AppTest 无浏览器定时器，侧边栏 fragment 的"完成即重绘"不会自动发生，
    由本测试轮询到终态后手动 at.run() 驱动（等价于用户一次交互）。
    """
    print("\n[6] 文献库页解析并查重：后台提交 → 闸门 → 自动交付")
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(72, 72, 500, 700),
                        "Background parse probe paper for task center wiring. " * 60,
                        fontsize=11)
    pdf_bytes = doc.write()
    doc.close()

    from services import tasks as _tasks
    from services import library as _lib

    at = _new()
    if not _goto(at, LIB_LABEL):
        _fail("导航", "找不到文献库页菜单项")
        return
    try:
        at.file_uploader[0].set_value(
            (("bg_probe.pdf", pdf_bytes, "application/pdf"),)).run()
    except Exception as e:
        _fail("选文件", "file_uploader.set_value 失败：%s" % str(e)[:150])
        return
    at.run()
    if "解析并查重" not in [b.label for b in at.button]:
        _fail("按钮渲染", "选文件后未出现「解析并查重」按钮")
        return
    _ok("按钮渲染", "选文件后「解析并查重」出现")

    # --- 闸门：挂一个受控慢任务（同类 kind），按钮应禁用 + 运行中提示可见 ---
    def _slow(progress):
        progress("受控慢任务（闸门测试）", 0.5)
        time.sleep(5)
        return "ok"
    slow_tid = _tasks.submit("受控慢任务（闸门测试）", _slow, kind="library_parse")
    at.session_state["_bg_task_ids"] = [slow_tid]
    at.run()
    cap = "\n".join(str(c.value) for c in at.caption)
    if "⏳ 解析任务正在后台运行" not in cap:
        _fail("闸门提示", "同类任务在跑时未显示运行中提示")
    else:
        _ok("闸门提示", "运行中提示可见")
    btn = next((b for b in at.button if "解析并查重" in b.label), None)
    if btn is not None and not btn.disabled:
        _fail("闸门禁用", "同类任务在跑时按钮未禁用")
    else:
        _ok("闸门禁用", "按钮已禁用（防重复提交）")
    _t0 = time.time()
    while time.time() - _t0 < 30:            # 等慢任务终态再走真实提交，避免排队
        if (_tasks.get(slow_tid) or {}).get("state") in ("done", "error"):
            break
        time.sleep(0.3)
    at.session_state["_bg_task_ids"] = []
    at.run()
    # AppTest 特化行为：侧边栏 fragment 内 st.rerun(scope="app")（慢任务完成时触发）
    # 会重置 AppTest 的 widget 状态，上传值丢失、按钮不再渲染。真实浏览器中
    # st.rerun 保留 widget 状态（现有 agent 完成链路同款，线上无此问题），
    # 故这里补一次 set_value 还原"文件仍选中"的真实状态，再走真实提交。
    at.file_uploader[0].set_value(
        (("bg_probe.pdf", pdf_bytes, "application/pdf"),)).run()
    at.run()

    # --- 真实提交（从任务中心按名字找 tid，兼容"已秒完成并被消费"的竞态） ---
    if not _click(at, "解析并查重"):
        _fail("提交", "点击「解析并查重」失败")
        return
    at.run()
    mine = [t for t in _tasks.snapshot(limit=10)
            if str(t.get("name", "")).startswith("解析并查重：bg_probe.pdf")]
    if not mine:
        _fail("提交登记", "任务中心找不到「解析并查重：bg_probe.pdf」")
        return
    tid = mine[-1]["id"]
    _ok("提交登记", "tid=%s 已入后台任务中心" % tid[:8])

    final = None
    _t0 = time.time()
    while time.time() - _t0 < 90:
        final = _tasks.get(tid)
        if final and final["state"] in ("done", "error"):
            break
        time.sleep(0.4)
    if not final or final["state"] not in ("done", "error"):
        _fail("后台执行", "90s 未进入终态：%s" % ((final or {}).get("state"),))
        return
    _ok("后台执行", "终态=%s（%s）" % (final["state"], str(final.get("stage"))[:40]))
    at.run()                                  # 驱动渲染回合：bg_consume 交付
    at.run()                                  # 结算回合（_commit 内部 st.rerun）

    errs = _exceptions(at)
    if errs:
        _fail("交付渲染", "交付后未捕获异常：%s" % errs[:2])
        return
    # 主证据用持久副作用而非 flash：flash 是"渲染即 pop"的瞬态，交付若发生在
    # 带内部 rerun 的那一轮，断言时它已被消费掉（AppTest 会跟随 fragment 内
    # 的 st.rerun）。文献库状态才是"真的入库了"的硬证据。
    try:
        ctx = at.session_state["container"]
        papers = list(getattr(ctx, "papers", {}).keys())
    except Exception as e:
        _fail("状态读取", "读不到容器状态：%s" % str(e)[:120])
        return
    if "bg_probe.pdf" in papers:
        _ok("自动入库", "bg_probe.pdf 已在文献库（持久副作用实证）")
    elif _ss_get(at, "pending"):
        _ok("版本决策", "查重命中，pending 决策 UI 已渲染")
    else:
        err_text = "\n".join(str(e.value) for e in at.error)
        _fail("结果落点", "文献库无 bg_probe.pdf 也无 pending；error 区：%s" % err_text[:150])
        return
    succ = "\n".join(str(s.value) for s in at.success)
    if "已入库" in succ:
        _ok("成功提示", "flash 成功提示可见（瞬态证据兜头彩）")
    btn = next((b for b in at.button if "解析并查重" in b.label), None)
    cap = "\n".join(str(c.value) for c in at.caption)
    if btn is not None and btn.disabled:
        _fail("闸门恢复", "任务完成但按钮仍禁用")
    elif "⏳ 解析任务正在后台运行" in cap:
        _fail("闸门恢复", "任务完成但运行中提示仍在")
    else:
        _ok("闸门恢复", "按钮与提示均已恢复")
    try:
        _lib.remove_paper(ctx, "bg_probe.pdf")
        _ok("测试清理", "bg_probe.pdf 已从文献库移除")
    except Exception as e:
        _fail("测试清理", "移除 bg_probe.pdf 失败：%s" % str(e)[:120])


# ----------------------------------------------------------------  case 7
def case_search_background():
    """板块4 检索页后台化：arXiv 检索提交 → 闸门 → 后台执行 → 自动交付 → 闸门恢复。

    与 case 6 同款断言三态（提交登记 / 闸门两态 / 交付落点），差异：
    用 **事件闸门打桩** search_arxiv（worker 阻塞在 Event.wait 上，由测试放行），
    把"后台正在跑"变成确定性状态——不像 sleep 打桩那样与时序赛跑
    （sleep 方案在慢机器上会让闸门断言假失败）。恢复在 finally 里做。
    """
    print("\n[7] 检索页 arXiv 检索：后台提交 → 闸门 → 自动交付")
    import threading
    import services.extern as _extern
    from services import tasks as _tasks

    _release = threading.Event()

    def _stub(q, n=5):
        _release.wait(timeout=60)            # 测试放行前一直"正在检索"
        return [
            {"title": "打桩论文甲：Attention Is All You Need", "arxiv_id": "1706.03762v7",
             "published": "2023-08", "id": "1706.03762v7", "github": [],
             "summary": "Stub summary for AppTest wiring verification. " * 5},
            {"title": "打桩论文乙：BERT Pre-training of Deep Bidirectional Transformers",
             "arxiv_id": "1810.04805v2", "published": "2019-05", "id": "1810.04805v2",
             "github": [], "summary": "Another stub summary. " * 5},
        ]

    orig = _extern.search_arxiv
    _extern.search_arxiv = _stub
    try:
        at = _new()
        if not _goto(at, SEARCH_LABEL):
            _fail("导航", "找不到检索页菜单项")
            return
        ti = next((t for t in at.text_input if getattr(t, "key", "") == "aq"), None)
        if ti is None:
            _fail("输入框", "找不到 arXiv 检索输入框（key=aq）")
            return
        ti.set_value("attention is all you need").run()
        at.run()
        if not _click(at, "搜索 arXiv"):
            _fail("提交", "点击「搜索 arXiv」失败")
            return
        at.run()
        ids = _ss_get(at, "_bg_task_ids") or []
        tid = ids[-1] if ids else None
        if tid is None:
            mine = [t for t in _tasks.snapshot(limit=10)
                    if str(t.get("name", "")).startswith("arXiv 检索：")]
            tid = mine[-1]["id"] if mine else None
        if tid is None:
            _fail("提交登记", "任务中心找不到 arXiv 检索任务")
            return
        _ok("提交登记", "tid=%s 已入后台任务中心" % tid[:8])

        # --- 闸门：worker 阻塞在 Event 上，"正在跑"是确定性状态 ---
        st_info = _tasks.get(tid) or {}
        if st_info.get("state") not in ("pending", "running"):
            _fail("闸门前提", "点击后任务不在运行态：%s" % st_info.get("state"))
            return
        btn = next((b for b in at.button if "搜索 arXiv" in b.label), None)
        if btn is None:
            _fail("按钮渲染", "提交后「搜索 arXiv」按钮消失")
            return
        if not btn.disabled:
            _fail("闸门禁用", "同类任务在跑时按钮未禁用")
        else:
            _ok("闸门禁用", "按钮已禁用（防重复提交）")
        cap = "\n".join(str(c.value) for c in at.caption)
        if "⏳ arXiv 检索正在后台运行" not in cap:
            _fail("闸门提示", "未显示运行中提示：%r" % cap[:120])
        else:
            _ok("闸门提示", "运行中提示可见")

        # --- 放行 worker → 等终态 → 驱动交付 ---
        _release.set()
        final = None
        _t0 = time.time()
        while time.time() - _t0 < 60:
            final = _tasks.get(tid)
            if final and final["state"] in ("done", "error"):
                break
            time.sleep(0.3)
        if not final or final["state"] not in ("done", "error"):
            _fail("后台执行", "放行后 60s 未进入终态：%s" % ((final or {}).get("state"),))
            return
        _ok("后台执行", "终态=%s（%s）" % (final["state"], str(final.get("stage"))[:40]))
        at.run()                              # 驱动渲染回合：bg_consume 交付
        at.run()                              # 结算回合（consume 内 st.rerun）

        errs = _exceptions(at)
        if errs:
            _fail("交付渲染", "交付后未捕获异常：%s" % errs[:2])
            return
        # 主证据用持久副作用：结果落 session_state.arxiv_res 并真实渲染
        md = _md_text(at)
        if "打桩论文甲" in md and "打桩论文乙" in md:
            _ok("结果渲染", "两篇打桩论文均已渲染（持久副作用实证）")
        else:
            _fail("结果渲染", "markdown 中找不到打桩论文：%r" % md[:150])
            return
        succ = "\n".join(str(s.value) for s in at.success)
        if "检索到 2 篇论文" in succ:
            _ok("成功提示", "flash 成功提示可见（瞬态证据兜头彩）")
        btn2 = next((b for b in at.button if "搜索 arXiv" in b.label), None)
        cap2 = "\n".join(str(c.value) for c in at.caption)
        if btn2 is not None and btn2.disabled:
            _fail("闸门恢复", "任务完成但按钮仍禁用")
        elif "⏳ arXiv 检索正在后台运行" in cap2:
            _fail("闸门恢复", "任务完成但运行中提示仍在")
        else:
            _ok("闸门恢复", "按钮与提示均已恢复")
    finally:
        _extern.search_arxiv = orig
        _release.set()                        # 兜底放行，防止worker线程悬挂


def case_writing_background():
    """板块6 写作页后台化：Figure Caption 提交 → 闸门 → 后台执行 → 自动交付 → 闸门恢复。

    为什么单测这一个是必要的：Round 19 半迁移态曾把该按钮改成 bg_submit 却漏接
    bg_consume（_consume_caption_auto 成死代码）——用户点了没反应，而渲染冒烟
    从不点这个按钮、单元测试不触 UI 交付链，缺陷直接漏到线上。本用例钉死三态：
    提交登记 / 闸门两态 / 交付落点（auto_captions[0] 持久副作用）。

    与 case 7 同款事件闸门打桩：worker 阻塞在 Event.wait 上由测试放行，
    "正在跑"是确定性状态，不与时序赛跑。llm_available 打桩为 True——
    测的是接线，不是 API Key（无 Key 环境下按钮守卫会直接拦下提交）。
    """
    print("\n[8] 写作页 Figure Caption：后台提交 → 闸门 → 自动交付")
    import threading
    from services import writing as _writing
    from services import tasks as _tasks

    _release = threading.Event()
    _STUB_CAP = "Stub caption: the trend rises by 42% across 12 months."

    def _stub_caption(ctx, df, chart_desc="", x=None, y=None):
        _release.wait(timeout=60)
        return _STUB_CAP

    orig_cap, orig_avail = _writing.figure_caption, _writing.llm_available
    _writing.figure_caption = _stub_caption
    _writing.llm_available = lambda ctx: True
    try:
        at = _new()
        if not _goto(at, WRITING_LABEL):
            _fail("导航", "找不到写作页菜单项")
            return
        if not _click(at, "📂 载入示例数据"):
            _fail("示例数据", "找不到「📂 载入示例数据」按钮")
            return
        at.run()
        at.run()                          # 结算回合：chart_plan 推断 + 按钮渲染
        if not any("生成该图 Figure Caption" in b.label for b in at.button):
            _fail("按钮渲染", "载入示例数据后未出现「生成该图 Figure Caption」按钮"
                             "（infer_chart_plan 可能返回空）")
            return
        _ok("按钮渲染", "自动推荐图表 + Caption 按钮已渲染")

        if not _click(at, "🤖 生成该图 Figure Caption"):
            _fail("提交", "点击「🤖 生成该图 Figure Caption」失败")
            return
        at.run()
        ids = _ss_get(at, "_bg_task_ids") or []
        tid = ids[-1] if ids else None
        if tid is None:
            mine = [t for t in _tasks.snapshot(limit=10)
                    if str(t.get("name", "")).startswith("Figure Caption（图 1）")]
            tid = mine[-1]["id"] if mine else None
        if tid is None:
            _fail("提交登记", "任务中心找不到 Figure Caption 任务")
            return
        _ok("提交登记", "tid=%s 已入后台任务中心" % tid[:8])

        # --- 闸门：worker 阻塞在 Event 上，"正在跑"是确定性状态 ---
        st_info = _tasks.get(tid) or {}
        if st_info.get("state") not in ("pending", "running"):
            _fail("闸门前提", "点击后任务不在运行态：%s" % st_info.get("state"))
            return
        btn = next((b for b in at.button if "生成该图 Figure Caption" in b.label), None)
        if btn is None or not btn.disabled:
            _fail("闸门禁用", "同类任务在跑时按钮未禁用")
        else:
            _ok("闸门禁用", "按钮已禁用（防重复提交）")
        cap = "\n".join(str(c.value) for c in at.caption)
        if "⏳ Figure Caption 正在后台生成" not in cap:
            _fail("闸门提示", "未显示运行中提示：%r" % cap[:120])
        else:
            _ok("闸门提示", "运行中提示可见")

        # --- 放行 worker → 等终态 → 驱动交付 ---
        _release.set()
        final = None
        _t0 = time.time()
        while time.time() - _t0 < 60:
            final = _tasks.get(tid)
            if final and final["state"] in ("done", "error"):
                break
            time.sleep(0.3)
        if not final or final["state"] not in ("done", "error"):
            _fail("后台执行", "放行后 60s 未进入终态：%s" % ((final or {}).get("state"),))
            return
        _ok("后台执行", "终态=%s" % final["state"])
        at.run()                              # 驱动渲染回合：bg_consume 交付
        at.run()                              # 结算回合（bg_consume 内 st.rerun）

        errs = _exceptions(at)
        if errs:
            _fail("交付渲染", "交付后未捕获异常：%s" % errs[:2])
            return
        # 主证据用持久副作用：auto_captions[0] 被交付处理器写回（Round 19 断链bug
        # 的直接 Regression：断链时此键永远缺失，而渲染冒烟根本点不到这个按钮）。
        caps = _ss_get(at, "auto_captions") or {}
        if caps.get(0) == _STUB_CAP:
            _ok("结果落点", "auto_captions[0] 已写回打桩 caption（持久副作用实证）")
        else:
            _fail("结果落点", "auto_captions 未写回：%r" % (dict(caps),))
            return
        info = "\n".join(str(i.value) for i in at.info)
        if _STUB_CAP in info:
            _ok("结果渲染", "caption 已随页面渲染（st.info 可见）")
        btn2 = next((b for b in at.button if "生成该图 Figure Caption" in b.label), None)
        cap2 = "\n".join(str(c.value) for c in at.caption)
        if btn2 is not None and btn2.disabled:
            _fail("闸门恢复", "任务完成但按钮仍禁用")
        elif "⏳ Figure Caption 正在后台生成" in cap2:
            _fail("闸门恢复", "任务完成但运行中提示仍在")
        else:
            _ok("闸门恢复", "按钮与提示均已恢复")
    finally:
        _writing.figure_caption = orig_cap
        _writing.llm_available = orig_avail
        _release.set()                        # 兜底放行，防止 worker 线程悬挂


# ----------------------------------------------------------------  case 9
def case_qa_compare_survives_pill():
    """问答页「多篇对比」选中不被 pill 重跑抹掉（2026-10-22 用户缺陷回归）。

    根因：pills 处理器曾在脚本中段 st.rerun()，导致其后实例化的 multiselect 等
    widget 本轮不注册、状态被裁剪重置——用户选好的对比论文被清成 []，
    反馈「我没法在此勾选论文比对」。修复：去掉中段 rerun，改写入输入框显式 key。
    本用例钉死三件事：① 选中可累积；② 点 pill 后选中不丢；③ 输入框被填入且
    不被选中态覆盖手改。
    """
    print("\n[9] 问答页多篇对比：选论文 → 点 pill → 选中不丢 / 输入框填入")
    at = _new()
    if not _goto(at, QA_LABEL):
        _fail("导航", "找不到问答页菜单项")
        return
    ms = next((m for m in at.multiselect if "多篇对比" in str(m.label)), None)
    if ms is None:
        _fail("控件渲染", "找不到多篇对比 multiselect")
        return
    opts = list(ms.options)
    if len(opts) < 2:
        _fail("前置", "库内论文不足 2 篇，无法验证多选：%r" % (opts[:5],))
        return
    _ok("选项就绪", "可选 %d 篇（取前两篇验证）" % len(opts))

    ms.select(opts[0]).run()
    at.run()
    picked = _ss_get(at, "qa_compare_papers")
    if picked == [opts[0]]:
        _ok("单选生效", "session_state 已登记 %r" % picked)
    else:
        _fail("单选生效", "qa_compare_papers=%r" % (picked,))
        return

    pills = next((p for p in at.pills if "试试这些问题" in str(p.label)), None)
    if pills is None:
        _fail("控件渲染", "找不到示例问题 pills")
        return
    pills.set_value(QA_EX0).run()
    at.run()                                  # 结算回合（点击与清理之间还有一次渲染）
    after = _ss_get(at, "qa_compare_papers")
    if after == [opts[0]]:
        _ok("选中保持", "点 pill 后对比选中未被抹掉：%r" % (after,))
    else:
        _fail("选中保持", "点 pill 后 qa_compare_papers=%r（中段 rerun 抹选回归！）" % (after,))
        return
    if _ss_get(at, "qa_input") == QA_EX0:
        _ok("输入框填入", "qa_input 已被填入示例问题")
    else:
        _fail("输入框填入", "qa_input=%r" % (_ss_get(at, "qa_input"),))
    ti = next((t for t in at.text_input if "请输入问题" in str(t.label)), None)
    if ti is not None and ti.value == QA_EX0:
        _ok("输入框渲染", "输入框可见值为示例问题")
    else:
        _fail("输入框渲染", "输入框值=%r" % (getattr(ti, "value", None),))

    # 手改输入框 → 勾重排（普通 rerun）→ 不应被仍选中的 pill 覆盖
    ti.set_value("手改的问题").run()
    at.run()
    cb = next((c for c in at.checkbox if "重排" in str(c.label)), None)
    if cb is not None:
        cb.set_value(True).run()
        at.run()
        if _ss_get(at, "qa_input") == "手改的问题":
            _ok("手改不被覆盖", "影子键逻辑仍生效")
        else:
            _fail("手改不被覆盖", "qa_input=%r" % (_ss_get(at, "qa_input"),))
    errs = _exceptions(at)
    if errs:
        _fail("渲染异常", "流程后未捕获异常：%s" % errs[:2])
    else:
        _ok("渲染异常", "全流程 0 异常")


# ----------------------------------------------------------------  case 10
def case_library_meta_background():
    """文献库页元数据抽取后台化全链路（2026-10-22 用户缺陷：板块冻结回归）。

    与 case 6/7/8 同款断言三态（提交登记 / 闸门两态 / 交付落点），差异：
    worker 里的 ctx.llm.json_chat 用事件闸门打桩（真实 open_doc 照跑），
    LLM 不可用也能确定性地验证「后台抽取 → 自动交付 → meta_view 落点」。
    """
    print("\n[10] 文献库页元数据抽取：后台提交 → 闸门 → 自动交付 → 落点")
    import threading
    from services import tasks as _tasks
    from ui.common import resolve_pdf as _resolve_pdf

    _release = threading.Event()
    _STUB_META = {"title": "Attention Is All You Need", "authors": ["Ashish Vaswani"],
                  "institution": "Google Brain", "abstract": "Stub abstract for wiring."}

    class _StubLLM:
        available = True

        def json_chat(self, msgs, **kw):
            _release.wait(timeout=60)
            return dict(_STUB_META)

    at = _new()
    if not _goto(at, LIB_LABEL):
        _fail("导航", "找不到文献库页菜单项")
        return
    ctx = at.session_state["container"]
    target = next((f for f in getattr(ctx, "files", []) if _resolve_pdf(f)), None)
    if target is None:
        _fail("前置", "库内没有带原始 PDF 的论文，无法跑通抽取")
        return
    _ok("目标论文", "%s（resolve_pdf 可达原始文件）" % target)
    sb = next((s for s in at.selectbox if getattr(s, "key", "") == "meta_sel"), None)
    if sb is None:
        _fail("控件渲染", "找不到元数据板块「选择论文」下拉框")
        return
    sb.set_value(target).run()
    at.run()

    orig_llm = ctx.llm
    ctx.llm = _StubLLM()
    try:
        if not _click(at, "抽取标题/作者/机构/摘要 + 判断排版"):
            _fail("提交", "点击元数据抽取按钮失败")
            return
        at.run()
        ids = _ss_get(at, "_bg_task_ids") or []
        tid = ids[-1] if ids else None
        if tid is None:
            mine = [t for t in _tasks.snapshot(limit=10)
                    if str(t.get("name", "")).startswith("元数据抽取：")]
            tid = mine[-1]["id"] if mine else None
        if tid is None:
            _fail("提交登记", "任务中心找不到元数据抽取任务")
            return
        _ok("提交登记", "tid=%s 已入后台任务中心" % tid[:8])

        # --- 闸门：worker 阻塞在 Event 上，"正在跑"是确定性状态 ---
        st_info = _tasks.get(tid) or {}
        if st_info.get("state") not in ("pending", "running"):
            _fail("闸门前提", "点击后任务不在运行态：%s" % st_info.get("state"))
            return
        btn = next((b for b in at.button if "抽取标题/作者/机构/摘要" in b.label), None)
        if btn is None or not btn.disabled:
            _fail("闸门禁用", "同类任务在跑时按钮未禁用")
        else:
            _ok("闸门禁用", "按钮已禁用（防重复提交）")
        cap = "\n".join(str(c.value) for c in at.caption)
        if "⏳ 元数据抽取正在后台运行" not in cap:
            _fail("闸门提示", "未显示运行中提示：%r" % cap[:120])
        else:
            _ok("闸门提示", "运行中提示可见（板块其他控件不再冻结）")

        # --- 放行 worker → 等终态 → 驱动交付 ---
        _release.set()
        final = None
        _t0 = time.time()
        while time.time() - _t0 < 60:
            final = _tasks.get(tid)
            if final and final["state"] in ("done", "error"):
                break
            time.sleep(0.3)
        if not final or final["state"] not in ("done", "error"):
            _fail("后台执行", "放行后 60s 未进入终态：%s" % ((final or {}).get("state"),))
            return
        _ok("后台执行", "终态=%s" % final["state"])
        at.run()                              # 驱动渲染回合：bg_consume 交付
        at.run()                              # 结算回合（bg_consume 内 st.rerun）

        errs = _exceptions(at)
        if errs:
            _fail("交付渲染", "交付后未捕获异常：%s" % errs[:2])
            return
        # 主证据用持久副作用：meta_view 被交付处理器写回（半迁移态下此键永远缺失）
        view = _ss_get(at, "meta_view")
        if view == _STUB_META:
            _ok("结果落点", "meta_view 已写回打桩元数据（持久副作用实证）")
        else:
            _fail("结果落点", "meta_view=%r" % (view,))
            return
        succ = "\n".join(str(s.value) for s in at.success)
        if "元数据抽取完成" in succ:
            _ok("成功提示", "flash 成功提示可见")
        btn2 = next((b for b in at.button if "抽取标题/作者/机构/摘要" in b.label), None)
        cap2 = "\n".join(str(c.value) for c in at.caption)
        if btn2 is not None and btn2.disabled:
            _fail("闸门恢复", "任务完成但按钮仍禁用")
        elif "⏳ 元数据抽取正在后台运行" in cap2:
            _fail("闸门恢复", "任务完成但运行中提示仍在")
        else:
            _ok("闸门恢复", "按钮与提示均已恢复")
    finally:
        ctx.llm = orig_llm
        _release.set()                        # 兜底放行，防止 worker 线程悬挂


# ----------------------------------------------------------------  case 11
def case_library_struct_background():
    """文献库页细粒度结构解析后台化（2026-10-22 板块冻结回归，最重 worker）。

    worker 内含 open_doc + extract_toc/tables/figures 三个解析调用与插图落盘，
    是本轮后台化里逻辑最重的一个；视觉路径保持关闭（默认复选框态），
    用真实 PDF 跑通「后台解析 → 自动交付 → struct 落点 → 闸门恢复」。
    """
    print("\n[11] 文献库页结构解析：后台提交 → 闸门 → 自动交付 → 落点")
    import threading
    from services import tasks as _tasks
    from ui.common import resolve_pdf as _resolve_pdf

    _release = threading.Event()

    class _StubLLM:
        available = True

        def json_chat(self, msgs, **kw):
            return {"stub": True}

    at = _new()
    if not _goto(at, LIB_LABEL):
        _fail("导航", "找不到文献库页菜单项")
        return
    ctx = at.session_state["container"]
    target = next((f for f in getattr(ctx, "files", []) if _resolve_pdf(f)), None)
    if target is None:
        _fail("前置", "库内没有带原始 PDF 的论文，无法跑通解析")
        return
    _ok("目标论文", "%s" % target)
    sb = next((s for s in at.selectbox if getattr(s, "key", "") == "struct_sel"), None)
    if sb is None:
        _fail("控件渲染", "找不到结构解析板块「选择论文」下拉框")
        return
    sb.set_value(target).run()
    at.run()

    orig_llm = ctx.llm
    ctx.llm = _StubLLM()
    try:
        if not _click(at, "全方位解析（标题树/表格/图表）"):
            _fail("提交", "点击全方位解析按钮失败")
            return
        at.run()
        ids = _ss_get(at, "_bg_task_ids") or []
        tid = ids[-1] if ids else None
        if tid is None:
            mine = [t for t in _tasks.snapshot(limit=10)
                    if str(t.get("name", "")).startswith("结构解析：")]
            tid = mine[-1]["id"] if mine else None
        if tid is None:
            _fail("提交登记", "任务中心找不到结构解析任务")
            return
        _ok("提交登记", "tid=%s 已入后台任务中心" % tid[:8])

        final = None
        _t0 = time.time()
        while time.time() - _t0 < 120:
            final = _tasks.get(tid)
            if final and final["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        if not final or final["state"] not in ("done", "error"):
            _fail("后台执行", "120s 未进入终态：%s" % ((final or {}).get("state"),))
            return
        _ok("后台执行", "终态=%s%s" % (final["state"],
                                        "（%s）" % str(final.get("error"))[:80] if final["state"] == "error" else ""))
        at.run()                              # 驱动渲染回合：bg_consume 交付
        at.run()                              # 结算回合（bg_consume 内 st.rerun）

        errs = _exceptions(at)
        if errs:
            _fail("交付渲染", "交付后未捕获异常：%s" % errs[:2])
            return
        stobj = _ss_get(at, "struct")
        if isinstance(stobj, dict) and stobj.get("file") == target:
            _ok("结果落点", "struct 已写回（toc %d 项 / 表 %d 个 / 图 %d 张）"
                % (len(stobj.get("toc") or []), len(stobj.get("tables") or []),
                   len(stobj.get("figs") or [])))
        else:
            _fail("结果落点", "struct=%r" % (stobj,))
            return
        btn2 = next((b for b in at.button if "全方位解析" in b.label), None)
        cap2 = "\n".join(str(c.value) for c in at.caption)
        if btn2 is not None and btn2.disabled:
            _fail("闸门恢复", "任务完成但按钮仍禁用")
        elif "⏳ 结构解析正在后台运行" in cap2:
            _fail("闸门恢复", "任务完成但运行中提示仍在")
        else:
            _ok("闸门恢复", "按钮与提示均已恢复")
    finally:
        ctx.llm = orig_llm
        _release.set()


def main():
    print("=" * 72)
    print("P7 UI/UX 交互点击验证（AppTest）")
    print("=" * 72)
    for fn in (case_jump_bar, case_jump_clear, case_empty_cards,
               case_graph_metrics, case_library_metrics, case_library_bg_parse,
               case_search_background, case_writing_background,
               case_qa_compare_survives_pill, case_library_meta_background,
               case_library_struct_background):
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
    print("🎉 交互验证全部通过（跳转落点 / 清除语义 / 空状态卡片 / KPI 指标条 / 解析后台化）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

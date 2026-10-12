"""UI 渲染冒烟测试（进程内 AppTest，不启动 streamlit server）。

用法：
    python scripts/_smoke_ui.py            # 标准：渲染全部页面
    python scripts/_smoke_ui.py --heavy    # 额外触发本地重操作（建图/演示数据）

说明：
- 只验证「渲染不抛异常」（演示路径零崩溃），不校验视觉样式；
- 网络相关按钮（arXiv/PubMed/DuckDuckGo）不在冒烟范围内，避免演示机断网时长时间阻塞；
- 用 streamlit.testing.v1.AppTest 捕获异常，异常会被 ui/common.py safe_render 兜底，
  因此 at.exception 为空 = 页面级错误边界生效。
"""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

# 菜单常量以 ui/sidebar.py 为唯一真源，**不在本文件复制一份**。
# 背景：此前这里硬编码了 MENU 副本，Round 9「术语中文化」重命名菜单项后，
# 脚本的副本与线上长期不一致 → 导航定位失败 → 9 条按钮断言全部被"跳过" →
# 冒烟输出 ✅ 却是假绿。复制常量等于埋一颗必然引爆的雷。
from ui.sidebar import MENU  # noqa: E402

HEAVY = "--heavy" in sys.argv


def _app_route_keys():
    """静态解析 app.py 的 _PAGES 路由键（不执行 app.py，避免副作用）。"""
    import ast
    try:
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "_PAGES":
                        return [k.value for k in node.value.keys
                                if isinstance(k, ast.Constant)]
    except Exception:
        pass
    return []


ROUTES = _app_route_keys()

# 路由键 → 菜单文案。测试只依赖 app.py 里的路由键（emoji），不复制菜单文案，
# 菜单重命名 / 分组调整都不会再把测试变成假绿。
MENU_BY_ROUTE = {r: m for m in MENU for r in ROUTES if m.startswith(r)}


def _new():
    t0 = time.time()
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=180)
    at.run()
    return at, time.time() - t0


def _report(tag, at, dt, problems):
    errs = [str(e.value)[:200] for e in at.exception]
    print(f"  [{tag}] {dt:6.1f}s  exception={len(errs)}")
    for e in errs[:5]:
        print(f"      ✗ {e}")
        problems.append((tag, e))
    return errs


def _menu_radio(at, label):
    """定位「含有该菜单项」的 radio。

    不再假设导航只有唯一一个 radio：重构后导航拆成了主区 radio +
    「ℹ️ 关于本系统」折叠区里的第二个 radio，旧写法 `options == MENU` 恒不成立。
    按「选项包含目标项」匹配，后续再拆分组也不至于静默失效。
    """
    for r in at.radio:
        try:
            if label in list(r.options):
                return r
        except Exception:
            continue
    return None


def _goto(at, label, problems=None):
    """切换到指定菜单项。定位失败必须记为问题——宁可报错，不可静默跳过。"""
    r = _menu_radio(at, label)
    if r is not None:
        r.set_value(label).run()
        # 结算回合：真实浏览器在"点击 → 重跑"之后还会完成一次渲染，中间态才对用户可见。
        # 少这一步，导航清理逻辑与下一次点击会被压进同一次 run，测出浏览器上不存在的假故障。
        at.run()
        return True
    # 折叠区「关于本系统」是按钮导航（radio 有选中态，与共享导航状态冲突）
    for b in at.button:
        if b.label.strip() == label.strip():
            try:
                b.click().run()
                at.run()      # 同上：补一次结算渲染回合
                return True
            except Exception as e:      # 例如正好停在当页被 disabled
                if problems is not None:
                    problems.append((label, "导航按钮不可点击：%s" % e))
                return False
    msg = "菜单项不可达（导航定位失败）：%s" % label
    print("  ✗ " + msg)
    if problems is not None:
        problems.append((label, msg))
    return False


def _click(at, label):
    """按 label 点击按钮，返回是否点中。"""
    for b in at.button:
        if b.label.strip() == label.strip():
            b.click().run()
            return True
    return False


def _miss(problems, tag, msg):
    """统一的「断言未执行」记账入口。"""
    print("  ✗ " + msg)
    problems.append((tag, msg))


def _inject():
    """故障注入：把外部依赖替换成必崩函数，验证页面是否给出友好提示而非 Traceback。"""
    def boom(msg):
        def _f(*a, **k):
            raise RuntimeError(msg)
        return _f

    import core.agent as _agent
    import services.extern as _extern
    import services.graph as _graph
    import services.library as _library
    import services.track as _track
    import tools.definitions as _defs

    _extern.search_arxiv = boom("模拟断网：arXiv 不可达")
    _extern.latest_arxiv = boom("模拟断网：arXiv 不可达")
    _extern.search_pubmed = boom("模拟断网：PubMed 不可达")
    _extern.web_search = boom("模拟断网：DuckDuckGo 不可达")
    _track.fetch_track = boom("模拟断网：追踪抓取失败")
    _defs._ingest_arxiv_paper = boom("模拟断网：PDF 下载失败")
    _defs.t_batch_download = boom("模拟断网：批量下载失败")
    _library.version_diff = boom("模拟大模型不可用：额度耗尽")
    _graph.build_graph = boom("模拟 embedding 服务异常")
    _agent.run = boom("模拟大模型调用超时")


def _seed_inputs(at, seeds):
    """给文本框/文本域填值（不填会因为空值守卫而绕开待验证的失败路径）。"""
    filled = set()
    for w in list(at.text_input) + list(at.text_area):
        try:
            k = getattr(w, "key", None)
            if k in seeds and k not in filled:
                w.set_value(seeds[k]).run()
                filled.add(k)
        except Exception:
            continue


def phase_fault(problems):
    print("\n[4] 故障注入：验证降级提示而非 Traceback")
    import time
    _inject()
    _search, _lib, _graph = "🔍", "📁", "🕸️"
    cases = [
        (_search, "🔍 搜索 arXiv"),
        (_search, "🔍 PubMed"),
        (_search, "🔍 DuckDuckGo"),
        (_search, "✨ 刷新并推荐"),
        (_search, "🔄 运行一次追踪"),
        (_search, "🚀 批量下载入库"),
        (_lib, "🔍 对比 1706.03762 版本差异"),
        (_graph, "🕸️ 构建引用图谱"),
        (_graph, "📝 一键生成领域综述"),
    ]
    input_seed = {
        "aq": "transformer",            # arXiv 检索关键词
        "pq": "protein",                # PubMed 关键词
        "wq": "large language model",   # 网页搜索关键词
        "bt": "Attention Is All You Need\nBERT",   # 批量下载标题
        "qrefs": "[1] Vaswani et al. Attention is all you need.",  # 引用含金量
        "trk_topics": "cs.AI, cs.CL",   # 订阅话题
    }
    for i, (route, btn) in enumerate(cases):
        page = MENU_BY_ROUTE.get(route)
        if page is None:
            _miss(problems, route, "路由键 %s 无对应菜单项" % route)
            continue
        try:
            at, _ = _new()
            _click(at, "🚀 一键加载演示数据（v1/v7/BERT）")
            if not _goto(at, page, problems):
                continue
            _seed_inputs(at, input_seed)
            t0 = time.time()
            hit = _click(at, btn)
            if not hit:
                _miss(problems, btn, "按钮未找到（断言未执行）：%s @ %s" % (btn, page))
                continue
            dt = time.time() - t0
            errs = [str(e.value)[:200] for e in at.exception]
            friend = [w.value[:70] for w in at.error] + [w.value[:70] for w in at.warning]
            ok = (not errs) and bool(friend)
            print(f"  [{i+1}/{len(cases)}] {btn[:24]:<26} {dt:5.1f}s  exception={len(errs)} "
                  f"友好提示={'有' if friend else '无'}  {'OK' if ok else '✗ 未降级'}")
            for e in errs[:3]:
                print(f"      ✗ {e}")
                problems.append((btn, e))
            if not ok and friend:
                problems.append((btn, "未给出友好提示"))
        except Exception as e:
            print(f"  [{btn}] 注入阶段异常：{str(e)[:200]}")
            problems.append((btn, str(e)))


def main():
    problems = []
    print("=" * 72)
    print("AI4S UI 冒烟测试 (AppTest in-process)　heavy=%s" % HEAVY)
    print("=" * 72)

    # ---------- 0. 覆盖检查：每个菜单项都必须有对应页面路由 ----------
    print("\n[0] 覆盖检查")
    orphan = [m for m in MENU if not any(m.startswith(r) for r in ROUTES)]
    if orphan:
        _miss(problems, "覆盖检查", "菜单项无页面路由（点了会落到空路由页）：%s" % orphan)
    else:
        print(f"  ✅ {len(MENU)} 个菜单项全部有路由；解析到 {len(ROUTES)} 条 _PAGES 键")

    # ---------- 0b. 导航回归：落地页正确 + 每项都能真正切换到位 ----------
    # 曾经的教训：「逐个 radio 能不能找到」和「页面有没有真的切过去」是两回事。
    # 渲染异常为 0 不代表路由正确 —— 首渲染跳错页、点了导航不切换，at.exception
    # 都是空的。这里直接断言 session_state["menu"] 落到预期值。
    print("\n[0b] 导航回归")
    try:
        at, _ = _new()

        def _cur_menu(at_):
            try:
                return at_.session_state["menu"]
            except Exception:
                return None

        landed = _cur_menu(at)
        if landed != MENU[0]:
            _miss(problems, "落地页", "首次渲染落在 %r，应为 %r" % (landed, MENU[0]))
        else:
            print("  ✅ 首次渲染落地页 = %s" % MENU[0])
        for label in MENU:
            if not _goto(at, label, problems):
                continue
            got = _cur_menu(at)
            if got != label:
                _miss(problems, "导航切换", "选中 %r 但 menu=%r" % (label, got))
        if not [p for p in problems if p[0] == "导航切换"]:
            print("  ✅ %d 项导航均真正切换到位" % len(MENU))
    except Exception as e:
        _miss(problems, "导航回归", str(e)[:200])

    # ---------- 1. 各页面独立渲染 ----------
    print("\n[1] 逐页渲染（空库状态）")
    for i, label in enumerate(MENU):
        try:
            at, dt = _new()
            if _goto(at, label, problems):
                _report(label, at, dt, problems)
        except Exception as e:
            print(f"  [{label}] 冒烟脚本自身异常：{str(e)[:200]}")
            problems.append((label, str(e)))

    # ---------- 2. 加载演示数据后全量渲染 ----------
    print("\n[2] 加载演示数据后逐页渲染")
    try:
        at, dt = _new()
        if not _click(at, "🚀 一键加载演示数据（v1/v7/BERT）"):
            _miss(problems, "sidebar", "未找到「一键加载演示数据」按钮")
        else:
            print(f"  加载耗时 {dt:.1f}s")
        for label in MENU:
            at.run()
            t0 = time.time()
            if _goto(at, label, problems):
                _report(label, at, time.time() - t0, problems)
    except Exception as e:
        print("  演示数据阶段异常：" + str(e)[:200])
        traceback.print_exc()
        problems.append(("demo-load", str(e)))

    # ---------- 3. 本地重操作（可选） ----------
    if HEAVY:
        print("\n[3] 本地重操作（无外网依赖，先加载演示数据）")
        heavy_ops = [
            ("🕸️", "🕸️ 构建引用图谱"),
            ("🕸️", "🎭 演示模式示意图谱"),
            ("🕸️", "📝 一键生成领域综述"),
            ("📁", "🧩 全方位解析（标题树/表格/图表）"),
        ]
        for route, btn in heavy_ops:
            page = MENU_BY_ROUTE.get(route)
            if page is None:
                _miss(problems, route, "路由键 %s 无对应菜单项" % route)
                continue
            try:
                at, _ = _new()
                if not _click(at, "🚀 一键加载演示数据（v1/v7/BERT）"):
                    _miss(problems, "sidebar", "演示数据按钮未找到")
                    continue
                if not _goto(at, page, problems):
                    continue
                t0 = time.time()
                if not _click(at, btn):
                    _miss(problems, btn, "按钮未找到（断言未执行）：%s @ %s" % (btn, page))
                    continue
                dt = time.time() - t0
                at.run()                      # 再跑一轮，检查结果渲染是否稳定
                _report(f"{page} / {btn}", at, dt, problems)
                print(f"      success={len(at.success)} warning={len(at.warning)} "
                      f"error={len(at.error)} info={len(at.info)}")
            except Exception as e:
                print(f"  [{page}/{btn}] 异常：{str(e)[:200]}")
                problems.append((btn, str(e)))
    else:
        print("\n[3] 本地重操作：已跳过（加 --heavy 启用）")

    phase_fault(problems)

    # ---------- 汇总 ----------
    print("\n" + "=" * 72)
    if problems:
        print(f"❌ 冒烟未全绿：{len(problems)} 处问题")
        for tag, msg in problems[:20]:
            print(f"   - {tag}: {msg[:160]}")
        return 1
    print("✅ 冒烟通过：全部页面 0 未捕获异常")
    return 0


if __name__ == "__main__":
    sys.exit(main())

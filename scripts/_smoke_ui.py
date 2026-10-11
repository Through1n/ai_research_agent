"""UI 渲染冒烟测试（进程内 AppTest，不启动 streamlit server）。

用法：
    C:/Users/Administrator/anaconda3/python.exe scripts/_smoke_ui.py            # 标准：渲染全部页面
    C:/Users/Administrator/anaconda3/python.exe scripts/_smoke_ui.py --heavy    # 额外触发本地重操作（建图/演示数据）

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

HEAVY = "--heavy" in sys.argv

MENU = [
    "🤖 0. Agent 总助手",
    "📁 1. 文献库管理",
    "💬 2. 跨文献问答与溯源",
    "📖 3. 版式阅读与智能翻译",
    "🔍 4. 论文自动检索与追踪",
    "🕸️ 5. 引用图谱与综述",
    "✍️ 6. 写作辅助与可视化",
    "🏗️ 系统架构设计",
    "💡 技术创新亮点",
]


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


def _menu_radio(at):
    """定位导航菜单 radio（第一个 radio 是「接入方式」，需按 options 精确匹配）。"""
    for r in at.radio:
        try:
            if list(r.options) == MENU:
                return r
        except Exception:
            continue
    return None


def _goto(at, label):
    r = _menu_radio(at)
    if r is None:
        print("  ⚠ 未找到导航菜单 radio")
        return False
    r.set_value(label).run()
    return True


def _click(at, label):
    """按 label 点击按钮，返回是否点中。"""
    for b in at.button:
        if b.label.strip() == label.strip():
            b.click().run()
            return True
    return False


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
    cases = [
        ("🔍 4. 论文自动检索与追踪", "🔍 搜索 arXiv"),
        ("🔍 4. 论文自动检索与追踪", "🔍 PubMed"),
        ("🔍 4. 论文自动检索与追踪", "🔍 DuckDuckGo"),
        ("🔍 4. 论文自动检索与追踪", "✨ 刷新并推荐"),
        ("🔍 4. 论文自动检索与追踪", "🔄 运行一次追踪"),
        ("🔍 4. 论文自动检索与追踪", "🚀 批量下载入库"),
        ("📁 1. 文献库管理", "🔍 对比 1706.03762 版本差异"),
        ("🕸️ 5. 引用图谱与综述", "🕸️ 构建引用图谱"),
        ("🕸️ 5. 引用图谱与综述", "📝 一键生成领域综述"),
    ]
    input_seed = {
        "aq": "transformer",            # arXiv 检索关键词
        "pq": "protein",                # PubMed 关键词
        "wq": "large language model",   # 网页搜索关键词
        "bt": "Attention Is All You Need\nBERT",   # 批量下载标题
        "qrefs": "[1] Vaswani et al. Attention is all you need.",  # 引用含金量
        "trk_topics": "cs.AI, cs.CL",   # 订阅话题
    }
    for i, (page, btn) in enumerate(cases):
        try:
            at, _ = _new()
            _click(at, "🚀 一键加载演示数据（v1/v7/BERT）")
            _goto(at, page)
            _seed_inputs(at, input_seed)
            t0 = time.time()
            hit = _click(at, btn)
            if not hit:
                print(f"  ⚠ 未找到按钮（跳过）：{btn}")
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

    # ---------- 1. 各页面独立渲染 ----------
    print("\n[1] 逐页渲染（空库状态）")
    for i, label in enumerate(MENU):
        try:
            at, dt = _new()
            _goto(at, label)
            _report(label, at, dt, problems)
        except Exception as e:
            print(f"  [{label}] 冒烟脚本自身异常：{str(e)[:200]}")
            problems.append((label, str(e)))

    # ---------- 2. 加载演示数据后全量渲染 ----------
    print("\n[2] 加载演示数据后逐页渲染")
    try:
        at, dt = _new()
        if not _click(at, "🚀 一键加载演示数据（v1/v7/BERT）"):
            print("  ⚠ 未找到「一键加载演示数据」按钮")
            problems.append(("sidebar", "未找到演示数据按钮"))
        else:
            print(f"  加载耗时 {dt:.1f}s")
        for label in MENU:
            at.run()
            t0 = time.time()
            _goto(at, label)
            _report(label, at, time.time() - t0, problems)
    except Exception as e:
        print("  演示数据阶段异常：" + str(e)[:200])
        traceback.print_exc()
        problems.append(("demo-load", str(e)))

    # ---------- 3. 本地重操作（可选） ----------
    if HEAVY:
        print("\n[3] 本地重操作（无外网依赖，先加载演示数据）")
        heavy_ops = [
            ("🕸️ 5. 引用图谱与综述", "🕸️ 构建引用图谱"),
            ("🕸️ 5. 引用图谱与综述", "🎭 演示模式示意图谱"),
            ("🕸️ 5. 引用图谱与综述", "📝 一键生成领域综述"),
            ("📁 1. 文献库管理", "🧩 全方位解析（标题树/表格/图表）"),
        ]
        for page, btn in heavy_ops:
            try:
                at, _ = _new()
                if not _click(at, "🚀 一键加载演示数据（v1/v7/BERT）"):
                    print("  ⚠ 演示数据按钮未找到")
                    continue
                _goto(at, page)
                t0 = time.time()
                if not _click(at, btn):
                    print(f"  ⚠ 未找到按钮：{btn}")
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

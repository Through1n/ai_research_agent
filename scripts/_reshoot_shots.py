# -*- coding: utf-8 -*-
"""Round 12 界面截图采集脚本（v2 · 证明点版）。

范围：`docs/shots/p0.png` … `p6.png` 共 7 张（一图一模块；p7/p8 经 M6 裁定不再使用，旧文件保留）。
每张图必须加载对应模块的**证明点**：

  p0 文献库管理 → 1b 细粒度解析（元数据 / 标题树 / 表格 / 插图 / LaTeX 公式 / 参考文献）
  p1 Agent 总助手 → Function Calling 闭环 + Trace 可视化
  p2 跨文献问答与溯源 → 2b 拒答 + 调试区 + 页码级溯源锚点
  p3 版式阅读与翻译 → 3a 双栏 PDF 原生渲染不串栏（另产出中英同步滚动候选）
  p6 论文检索与追踪 → 4b 多源检索结果 + 引用含金量质量标注
  p4 引用图谱与综述 → 5c 图谱驱动综述（含「图谱结构明细 networkx 计算所得」）
  p5 写作辅助与可视化 → 6a References 溯源表 + CSV 自动出图

前提：
  1. 启动应用（ai_research 环境）：
     streamlit run app.py --server.headless true --server.port 8599 --server.address 127.0.0.1
  2. .env 已配置真实 Key（脚本只读 Key 用于「明文泄漏比对」，绝不打印/外传），
     保证拍到的是功能完整的干净态，而不是「未配置 Key」降级态。

用法：
  python scripts/_reshoot_shots.py                    # 全量 7 张
  python scripts/_reshoot_shots.py --only p0,p3       # 只拍指定页
  python scripts/_reshoot_shots.py --mtime-check      # 只打印 UI 文件 mtime

安全：每张图截图前扫描主文档 + 全部 iframe 的可见文本与全部 input 的 value，
      与 .env 中的 Key 逐串比对，发现明文立即终止；不为截图修改任何业务代码。
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CAND_DIR = Path("C:/Users/Administrator/reshoot_backup/candidates")


# ------------------------------------------------------------------ Key 自查
_KEYS = set()


def load_keys():
    env = ROOT / ".env"
    if not env.exists():
        return
    for ln in env.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        name, v = ln.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if len(v) >= 12 and ("KEY" in name.upper() or "TOKEN" in name.upper()
                             or "SECRET" in name.upper()):
            _KEYS.add(v)


def mask(s):
    return s[:3] + "***" + s[-3:]


def leak_check(pg, tag):
    texts, inputs = [], []
    for fr in pg.frames:
        try:
            texts.append(fr.evaluate("() => document.body ? document.body.innerText : ''"))
            inputs += fr.evaluate(
                "() => Array.from(document.querySelectorAll('input')).map("
                "i => i.type + '|' + (i.getAttribute('value')||'') + '|' + (i.value||''))")
        except Exception:
            pass
    for k in _KEYS:
        if not k:
            continue
        for t in texts:
            if k in t:
                raise SystemExit(f"❌ 密钥明文泄漏（{tag}）：正文文本出现 {mask(k)}，终止")
        for rec in inputs:
            if k in rec:
                raise SystemExit(f"❌ 密钥明文泄漏（{tag}）：input value 出现 {mask(k)}，终止")
    print(f"  [leak-check {tag}] OK（{len(_KEYS)} 个 Key 无明文）")


# ------------------------------------------------------------------ 等待
def wait_idle(pg, timeout=120, stable=2.5):
    t0 = time.time()
    last, last_change = None, time.time()
    while time.time() - t0 < timeout:
        busy = pg.evaluate("""() => {
            const sels = ['[data-testid="stStatusWidget"]',
                          '[data-testid="stSpinner"]',
                          '[data-testid="stStatus"]'];
            for (const s of sels) {
                const el = document.querySelector(s);
                if (el && el.offsetParent !== null) return s;
            }
            return '';
        }""")
        txt = pg.evaluate("() => document.body.innerText")
        if busy:
            last, last_change = None, time.time()
        else:
            if txt == last:
                if time.time() - last_change >= stable:
                    return True
            else:
                last, last_change = txt, time.time()
        time.sleep(0.8)
    return False


def wait_text(pg, needle, timeout=240, must=True):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if needle in pg.evaluate("() => document.body.innerText"):
                return True
        except Exception:
            pass
        time.sleep(1.0)
    print(f"  ⚠️ 等待文案超时：{needle!r}（{timeout}s）")
    if must:
        raise SystemExit(f"❌ 等待文案超时：{needle!r}")
    return False


# ------------------------------------------------------------------ 控件
def btn(pg, text):
    """按文本找主区按钮（避开 role selector 对 / 的转义问题）。"""
    return pg.locator("button").filter(has_text=text)


def fill_text_by_label(pg, label, value):
    loc = pg.locator("[data-testid='stTextInput']").filter(
        has=pg.locator("[data-testid='stWidgetLabel']", has_text=label))
    loc.first.locator("input").fill(value)
    time.sleep(0.3)


def fill_area_by_label(pg, label, value):
    loc = pg.locator("[data-testid='stTextArea']").filter(
        has=pg.locator("[data-testid='stWidgetLabel']", has_text=label))
    loc.first.locator("textarea").fill(value)
    time.sleep(0.3)


def set_number(pg, label, value):
    loc = pg.locator("[data-testid='stNumberInput']:visible").filter(
        has=pg.locator("[data-testid='stWidgetLabel']", has_text=label))
    inp = loc.first.locator("input")
    inp.click()
    inp.fill(str(value))
    inp.press("Enter")
    wait_idle(pg, timeout=60)
    time.sleep(0.4)


def nav(pg, item):
    pg.locator("[data-testid='stSidebar'] label").filter(has_text=item).first.click()
    wait_idle(pg)
    time.sleep(0.5)


def click_tab(pg, name):
    pg.locator("[role='tab']").filter(has_text=name).first.click()
    wait_idle(pg, timeout=30)
    time.sleep(0.4)


def expand(pg, label):
    pg.get_by_text(label).first.click()
    time.sleep(0.8)


def wait_iframes(pg, min_n=1, timeout=90):
    """等嵌入 iframe 出现（版式渲染 / 图谱 / 双语面板都是 iframe）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = len(pg.frames) - 1
        if n >= min_n:
            time.sleep(2.0)
            return n
        time.sleep(1.0)
    return len(pg.frames) - 1


# ------------------------------------------------------------------ 截图
def shoot(pg, path, tag, max_h=14000, top_pad=60):
    """截图：先把内部滚动容器滚回顶部，再临时拉高视口把整页装进一屏。

    不能用 playwright 的 full_page=True：Streamlit 的滚动发生在内部容器
    （[data-testid='stMain']）里，body 本身不滚动，full_page 会把当前滚动
    位置当成起点，导致页头丢失、内容从中间开始。
    """
    wait_idle(pg)
    for _ in range(2):
        pg.evaluate("""() => {
            window.scrollTo(0, 0);
            document.querySelectorAll('*').forEach(e => {
                if (e.scrollHeight > e.clientHeight + 10 && e.scrollTop > 0) e.scrollTop = 0;
            });
        }""")
        time.sleep(0.8)
    h = pg.evaluate(
        "() => { const m = document.querySelector('[data-testid=\"stMain\"]');"
        " return m ? m.scrollHeight : document.body.scrollHeight; }")
    vh = int(min(max(h, 1000) + top_pad, max_h))
    pg.set_viewport_size({"width": 1440, "height": vh})
    time.sleep(2.5)
    pg.evaluate("""() => {
        document.querySelectorAll('*').forEach(e => {
            if (e.scrollHeight > e.clientHeight + 10 && e.scrollTop > 0) e.scrollTop = 0;
        });
    }""")
    time.sleep(0.8)
    leak_check(pg, tag)
    pg.screenshot(path=str(path), full_page=False)
    print(f"  📸 {tag} -> {path}（{Path(path).stat().st_size // 1024} KB，视口 {vh}px）")
    pg.set_viewport_size({"width": 1440, "height": 900})
    time.sleep(1.2)


def crop_by_text_bottom(pg, path, pattern, occurrence=1, pad=40):
    """按 DOM 文本定位裁剪边界（用于截掉 Agent 轨迹里大量重复的阶段块）。"""
    try:
        from PIL import Image
        y = pg.evaluate(
            """(args) => {
                const els = Array.from(document.querySelectorAll('*')).filter(e =>
                    e.children.length === 0 &&
                    new RegExp(args.pat).test(e.textContent || ''));
                if (els.length > args.occ) {
                    const r = els[args.occ].getBoundingClientRect();
                    return Math.max(0, Math.round(r.top + window.scrollY) - args.pad);
                }
                return 0;
            }""", {"pat": pattern, "occ": occurrence, "pad": pad})
        if y and y > 1000:
            im = Image.open(path)
            im.crop((0, 0, im.width, min(im.height, y * 2))).save(path)
            print(f"  ✂️ {Path(path).name} 裁剪到 {min(im.height, y * 2)}px")
    except Exception as e:
        print("  ⚠️ 裁剪跳过：", e)


def ui_mtimes():
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format:%m-%d_%H:%M", "--", "ui/"],
            cwd=str(ROOT), capture_output=True, text=True)
        git_last = (out.stdout or "").strip()
    except Exception:
        git_last = ""
    rows = []
    for f in ["ui/page_agent.py", "ui/page_library.py", "ui/page_qa.py", "ui/page_reading.py",
              "ui/page_search.py", "ui/page_graph.py", "ui/page_writing.py", "ui/page_misc.py",
              "ui/common.py", "ui/sidebar.py"]:
        p = ROOT / f
        if p.exists():
            rows.append(f"{f} {time.strftime('%m-%d_%H:%M', time.localtime(p.stat().st_mtime))}")
    return rows, git_last


# ------------------------------------------------------------------ 各页采集
def do_p1(pg, out):
    """0. Agent 总助手：真实执行 + Trace 可视化。"""
    print("→ p1 Agent 总助手（Function Calling 闭环 + Trace）")
    before = pg.evaluate("() => document.body.innerText").count("🙋")
    btn(pg, "用此任务").first.click()
    wait_idle(pg, timeout=30)
    print("  等待后台 Agent 任务完成（真实 LLM，实测 1–5 分钟）…")
    t0, ok = time.time(), False
    while time.time() - t0 < 600:
        txt = pg.evaluate("() => document.body.innerText")
        if txt.count("🙋") > before and "已完成 · 📚" in txt:
            ok = True
            break
        time.sleep(2.0)
    if not ok:
        raise SystemExit("❌ Agent 任务 600s 未返回最终答案卡片")
    print(f"  Agent 任务完成（{time.time() - t0:.0f}s）")
    wait_idle(pg, timeout=60)
    for lbl in ("计划 / 路由 / 思考过程", "查看 Agent 执行轨迹"):
        try:
            expand(pg, lbl)
        except Exception as e:
            print(f"  ⚠️ 展开失败 {lbl}：{e}")
    wait_idle(pg, timeout=60)
    shoot(pg, out / "p1.png", "p1", max_h=14000)
    crop_by_text_bottom(pg, out / "p1.png", "任务规划 Task Planning（更新）", occurrence=1)


def do_p0(pg, out):
    """1. 文献库管理：1b 细粒度解析（元数据/标题树/表格/插图/公式/参考文献）。"""
    print("→ p0 文献库管理（1b 细粒度解析）")
    nav(pg, "📁 1. 文献库管理")
    btn(pg, "抽取标题/作者/机构/摘要").first.click()
    print("  元数据抽取（真实 LLM）…")
    wait_text(pg, "元数据 JSON", timeout=180, must=False)
    wait_idle(pg)
    btn(pg, "全方位解析").first.click()
    print("  细粒度解析（标题树/表格/插图）…")
    wait_text(pg, "解析完成", timeout=300, must=False)
    wait_idle(pg)
    btn(pg, "提取 LaTeX 公式").first.click()
    print("  LaTeX 公式抽取（真实 LLM）…")
    wait_text(pg, "🧮 公式（", timeout=240, must=False)
    wait_idle(pg)
    btn(pg, "结构化参考文献").first.click()
    print("  参考文献结构化（真实 LLM）…")
    wait_text(pg, "参考文献", timeout=240, must=False)
    wait_idle(pg)
    for lbl in ("标题树（", "公式（"):
        try:
            expand(pg, lbl)
        except Exception as e:
            print(f"  ⚠️ 展开 {lbl} 失败：{e}")
    wait_idle(pg, timeout=30)
    shoot(pg, out / "p0.png", "p0", max_h=16000)
    # 备选：版本差异页签（语义去重 + 版本管理）
    click_tab(pg, "版本差异")
    btn(pg, "版本差异").first.click()
    print("  版本差异对比（真实 LLM）…")
    wait_text(pg, "版本差异报告", timeout=90, must=False)
    wait_idle(pg)
    shoot(pg, CAND_DIR / "p0_ver.png", "p0候选-版本差异")


def do_p2(pg, out):
    """2. 跨文献问答与溯源：2b 拒答 + 调试区 + 页码级溯源锚点。"""
    print("→ p2 跨文献问答与溯源（2b 拒答 + 调试区 + 溯源）")
    nav(pg, "💬 2. 跨文献问答与溯源")
    # ① 正常问答：答案 + 【n】角标 + 溯源卡片（页码徽章 + 定位按钮）
    fill_text_by_label(pg, "请输入问题", "对比 Transformer 与 BERT 的架构（表格）")
    btn(pg, "提问").first.click()
    print("  第 1 问（正常问答）…")
    wait_text(pg, "📚 答案来源", timeout=480)
    wait_idle(pg, timeout=60)
    # ② 拒答：库外问题触发拒答闸门 + 调试区（top 分数 / LLM 调用次数）
    fill_text_by_label(pg, "请输入问题", "2031 年诺贝尔物理学奖得主是谁？")
    btn(pg, "提问").first.click()
    print("  第 2 问（触发拒答闸门 + 检索调试区）…")
    wait_text(pg, "🛑", timeout=300, must=False)
    wait_idle(pg, timeout=60)
    # 展开来源与调试区
    for lbl in ("条来源 · 点击展开可定位原文", "检索调试（高级）"):
        for _ in range(2):
            try:
                expand(pg, lbl)
            except Exception:
                pass
    wait_idle(pg, timeout=60)
    shoot(pg, out / "p2.png", "p2", max_h=16000)


def do_p3(pg, out):
    """3. 版式阅读与翻译：3a 双栏原生渲染不串栏。"""
    print("→ p3 版式阅读与智能翻译（3a 不串栏）")
    nav(pg, "📖 3. 版式阅读与智能翻译")
    wait_idle(pg, timeout=60)
    for pg_no in (4, 3, 5):
        try:
            set_number(pg, "页码", pg_no)
        except Exception as e:
            print(f"  ⚠️ 设页码失败：{e}")
        btn(pg, "🎨 渲染该页").first.click()
        wait_idle(pg, timeout=120)
        n = wait_iframes(pg, min_n=1, timeout=60)
        print(f"  第 {pg_no} 页渲染 iframe 数={n}")
        if n >= 1:
            break
    btn(pg, "🔍 检测本页栏数").first.click()
    wait_idle(pg, timeout=60)
    time.sleep(1.0)
    shoot(pg, out / "p3.png", "p3", max_h=12000)
    # 备选候选：中英同步滚动（同步滚动证据）
    click_tab(pg, "中英同步滚动")
    btn(pg, "加载并逐段翻译").first.click()
    print("  逐段翻译（真实 LLM）…")
    wait_text(pg, "中英对照（逐段对齐", timeout=600, must=False)
    wait_idle(pg, timeout=90)
    shoot(pg, CAND_DIR / "p3_bi.png", "p3候选-中英同步滚动")


def do_p6(pg, out):
    """4. 论文检索与追踪：多源真实检索 + 引用含金量质量标注。"""
    print("→ p6 论文检索与追踪（多源结果 + 质量标注）")
    nav(pg, "🔍 4. 论文检索与追踪")
    click_tab(pg, "检索与下载")
    fill_text_by_label(pg, "标题 / 关键词 / ID", "graph neural network")
    got = False
    for i in range(6):
        btn(pg, "搜索 arXiv").first.click()
        print(f"  arXiv 检索第 {i + 1} 次…")
        t0 = time.time()
        while time.time() - t0 < 120:
            txt = pg.evaluate("() => document.body.innerText")
            if "检索到" in txt:
                got = True
                break
            if "操作失败" in txt or "过于频繁" in txt:
                break
            time.sleep(2.0)
        if got:
            break
        print("  ⚠️ arXiv 限流，60s 后重试")
        time.sleep(60)
    # PubMed 真实检索（同一页签，多源并行入镜）
    try:
        fill_text_by_label(pg, "关键词", "graph neural network")
        btn(pg, "🔍 PubMed").first.click()
        wait_text(pg, "PubMed 结果", timeout=120, must=False)
    except Exception as e:
        print("  ⚠️ PubMed 检索失败：", e)
    wait_idle(pg, timeout=60)
    shoot(pg, out / "p6.png", "p6", max_h=14000)
    # 质量/置信度标注：引用含金量页签（真实 LLM 逐条判定）
    click_tab(pg, "引用含金量")
    fill_area_by_label(pg, "粘贴参考文献", "\n".join([
        "Attention Is All You Need (Vaswani et al., 2017)",
        "BERT: Pre-training of Deep Bidirectional Transformers (Devlin et al., 2018)",
        "Understanding Deep Learning (Simon Prince, 2023)"]))
    btn(pg, "分析并剔除应付式引用").first.click()
    print("  引用含金量分析（真实 LLM）…")
    wait_text(pg, "引用含金量分析报告", timeout=300, must=False)
    wait_idle(pg, timeout=90)
    shoot(pg, CAND_DIR / "p6_quality.png", "p6候选-引用含金量质量标注")


def do_p4(pg, out):
    """5. 引用图谱与综述：5c 图谱驱动综述（含图谱结构明细）。"""
    print("→ p4 引用图谱与综述（5c 图谱驱动综述）")
    nav(pg, "🕸️ 5. 引用图谱与综述")
    btn(pg, "构建引用图谱").first.click()
    print("  构建真实引用图谱…")
    wait_text(pg, "图谱构建完成", timeout=300, must=False)
    wait_idle(pg, timeout=60)
    wait_iframes(pg, min_n=1, timeout=90)
    time.sleep(3.0)
    btn(pg, "一键生成领域综述").first.click()
    print("  生成领域综述（真实 LLM）…")
    wait_text(pg, "领域综述已生成", timeout=600, must=False)
    wait_idle(pg, timeout=120)
    for lbl in ("综述全文", "🔗 引用关系（"):
        try:
            expand(pg, lbl)
        except Exception as e:
            print(f"  ⚠️ 展开 {lbl} 失败：{e}")
    wait_idle(pg, timeout=90)
    time.sleep(1.5)
    shoot(pg, out / "p4.png", "p4", max_h=16000)


def do_p5(pg, out):
    """6. 写作辅助与可视化：6a References 溯源表 + CSV 自动出图。"""
    print("→ p5 写作辅助与可视化（6a References 溯源表）")
    nav(pg, "✍️ 6. 写作辅助与可视化")
    btn(pg, "📂 载入示例数据").first.click()
    wait_idle(pg, timeout=120)
    btn(pg, "生成该图 Figure Caption").first.click()
    print("  Figure Caption（真实 LLM）…")
    wait_text(pg, "Figure Caption:", timeout=180, must=False)
    wait_idle(pg, timeout=60)
    fill_area_by_label(
        pg, "输入 Idea",
        "面向多模态文献的跨语言检索增强问答：方法论与实证评估")
    btn(pg, "生成框架（引用真实文献）").first.click()
    print("  生成论文框架（真实 LLM 流式 + References 白名单校验）…")
    wait_text(pg, "References 溯源", timeout=600, must=False)
    wait_idle(pg, timeout=120)
    time.sleep(2.0)
    shoot(pg, out / "p5.png", "p5", max_h=16000)


FLOWS = [("p1", do_p1), ("p0", do_p0), ("p2", do_p2), ("p3", do_p3),
         ("p6", do_p6), ("p4", do_p4), ("p5", do_p5)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8599")
    ap.add_argument("--out", default=str(ROOT / "docs" / "shots"))
    ap.add_argument("--only", default="")
    ap.add_argument("--mtime-check", action="store_true")
    args = ap.parse_args()

    rows, git_last = ui_mtimes()
    print("=== 拍摄前 ui/ 文件 mtime ===")
    for r in rows:
        print("  ", r)
    if git_last:
        print("   ui/ 最近一次 git 提交：", git_last)
    if args.mtime_check:
        return

    only = [x.strip() for x in args.only.split(",") if x.strip()]
    out = Path(args.out)
    CAND_DIR.mkdir(parents=True, exist_ok=True)
    load_keys()
    if not _KEYS:
        print("⚠️ .env 中未读到 Key（自查仍执行，只是无比对样本）")

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        pg.goto(args.base + "/", timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_selector("text=AI4S 科研助手", timeout=60000)
        wait_idle(pg, timeout=180)
        # 干净态确认：不得出现「尚未配置 API Key」等降级提示
        txt = pg.evaluate("() => document.body.innerText")
        for bad in ("尚未配置 API Key", "请先在左侧填入 API Key", "未检测到可用 API Key"):
            if bad in txt:
                raise SystemExit(f"❌ 当前为降级/未配置 Key 态（{bad}），不合规，终止拍摄")
        print("  ✅ 干净态校验通过（无未配置 Key 提示）")

        for tag, fn in FLOWS:
            if only and tag not in only:
                continue
            if tag == "p1":
                try:
                    btn(pg, "一键加载演示数据").first.click()
                    wait_text(pg, "演示数据已就绪", timeout=180, must=False)
                    wait_idle(pg)
                except Exception as e:
                    print("  ⚠️ 加载演示数据失败：", e)
            fn(pg, out)
        b.close()
    print("✅ 采集完成。候选图目录：", CAND_DIR)
    rows2, _ = ui_mtimes()
    print("=== 拍摄后 ui/ 文件 mtime（若与开拍前有差异需补拍）===")
    for r in rows2:
        print("  ", r)


if __name__ == "__main__":
    sys.exit(main())

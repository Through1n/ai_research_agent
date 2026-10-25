# -*- coding: utf-8 -*-
"""Round 12 截图定点修复脚本（v2 主脚本的补拍流程，复用其全部工具函数）。

修复项：
  p0  重试 LaTeX 公式 / 结构化参考文献（上一轮 LLM 超时），等插图加载完再测高，杜绝黑底
  p2  硬拒答问题（库外问题触发拒答闸门）+ 只展开不回折（修 double-toggle bug）
  p3  先用「检测本页栏数」选双栏页，最后一步才点「渲染该页」（修按钮输出被后续 rerun 冲掉）
  p5  全页照拍，另按 DOM 定位裁出 References 溯源区 + 导出 CSV 区候选
  p6  修 PubMed 输入定位（:visible + 精确标签），arXiv+PubMed 多源同屏
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _reshoot_shots import (ROOT, CAND_DIR, btn, expand, fill_area_by_label,  # noqa: E402
                            fill_text_by_label, leak_check, load_keys, nav,
                            set_number, shoot, wait_idle, wait_iframes,
                            wait_text, click_tab)

from playwright.sync_api import sync_playwright  # noqa: E402


def expand_all(pg, label):
    """展开页面上所有匹配文案的折叠区（各点一次，绝不重复点同一元素）。"""
    locs = pg.get_by_text(label)
    n = min(locs.count(), 6)
    for i in range(n):
        try:
            locs.nth(i).click()
            time.sleep(0.7)
        except Exception:
            pass


def wait_images(pg, timeout=45):
    try:
        pg.wait_for_function(
            "() => Array.from(document.images).every(i => i.complete)", timeout=timeout * 1000)
    except Exception:
        pass
    time.sleep(1.5)


def rect_of(pg, pattern, which="top", occ=0):
    return pg.evaluate(
        """(args) => {
            const els = Array.from(document.querySelectorAll('*')).filter(e =>
                e.children.length === 0 && new RegExp(args.pat).test(e.textContent || ''));
            if (els.length <= args.occ) return 0;
            const r = els[args.occ].getBoundingClientRect();
            return Math.max(0, Math.round((args.which === 'bottom' ? r.bottom : r.top)
                                          + window.scrollY));
        }""", {"pat": pattern, "which": which, "occ": occ})


def crop_between(pg, path, top_pat, bot_pat, pad_top=70, pad_bot=50):
    try:
        from PIL import Image
        y1 = rect_of(pg, top_pat, "top")
        y2 = rect_of(pg, bot_pat, "bottom")
        if y1 and y2 and y2 > y1:
            im = Image.open(path)
            im.crop((0, max(0, (y1 - pad_top) * 2), im.width,
                    min(im.height, (y2 + pad_bot) * 2))).save(path)
            print(f"  ✂️ {Path(path).name} 裁剪 [{(y1 - pad_top) * 2}, {(y2 + pad_bot) * 2}]")
            return True
    except Exception as e:
        print("  ⚠️ 裁剪失败：", e)
    return False


# ------------------------------------------------------------------ p0
def fix_p0(pg, out):
    print("→ [修复] p0 文献库管理（补公式/参考文献，等图加载）")
    nav(pg, "📁 1. 文献库管理")
    btn(pg, "抽取标题/作者/机构/摘要").first.click()
    wait_text(pg, "元数据 JSON", timeout=180, must=False)
    wait_idle(pg)
    btn(pg, "全方位解析").first.click()
    wait_text(pg, "解析完成", timeout=300, must=False)
    wait_idle(pg)
    # 公式：失败自动重试（上一轮 LLM 超时是瞬时故障）
    for i in range(3):
        btn(pg, "提取 LaTeX 公式").first.click()
        print(f"  公式抽取第 {i + 1} 次…")
        if wait_text(pg, "🧮 公式（", timeout=150, must=False):
            break
        wait_idle(pg, timeout=30)
    # 参考文献：同样重试
    for i in range(3):
        btn(pg, "结构化参考文献").first.click()
        print(f"  参考文献结构化第 {i + 1} 次…")
        if wait_text(pg, "📚 参考文献", timeout=150, must=False):
            break
        wait_idle(pg, timeout=30)
    for lbl in ("标题树（", "公式（"):
        try:
            expand(pg, lbl)
        except Exception as e:
            print(f"  ⚠️ 展开 {lbl}：{e}")
    wait_images(pg, 60)
    wait_idle(pg)
    shoot(pg, out / "p0.png", "p0", max_h=16000)
    # 版本差异候选（语义去重 + 版本管理）
    click_tab(pg, "版本差异")
    btn(pg, "版本差异").first.click()
    wait_text(pg, "版本差异报告", timeout=300, must=False)
    wait_idle(pg)
    shoot(pg, CAND_DIR / "p0_ver.png", "p0候选-版本差异")


# ------------------------------------------------------------------ p2
def fix_p2(pg, out):
    print("→ [修复] p2 问答与溯源（硬拒答 + 调试区展开）")
    nav(pg, "💬 2. 跨文献问答与溯源")
    fill_text_by_label(pg, "请输入问题", "对比 Transformer 与 BERT 的架构（表格）")
    btn(pg, "提问").first.click()
    print("  第 1 问（正常问答）…")
    wait_text(pg, "📚 答案来源", timeout=480)
    wait_idle(pg, timeout=60)
    fill_text_by_label(pg, "请输入问题", "红烧肉怎么做才好吃？")
    btn(pg, "提问").first.click()
    print("  第 2 问（库外问题 → 拒答闸门）…")
    wait_text(pg, "拒答", timeout=300, must=False)
    wait_idle(pg, timeout=60)
    # 只点一次：修上一轮「点两次=开了又关」的 bug
    expand_all(pg, "条来源 · 点击展开可定位原文")
    expand_all(pg, "检索调试（高级）")
    wait_idle(pg, timeout=60)
    shoot(pg, out / "p2.png", "p2", max_h=16000)


# ------------------------------------------------------------------ p3
def fix_p3(pg, out):
    print("→ [修复] p3 版式阅读（先选双栏页，最后渲染）")
    nav(pg, "📖 3. 版式阅读与智能翻译")
    wait_idle(pg, timeout=60)
    best = None
    for pg_no in (2, 3, 5, 6):
        try:
            set_number(pg, "页码", pg_no)
        except Exception as e:
            print(f"  ⚠️ 设页码失败：{e}")
            continue
        btn(pg, "🔍 检测本页栏数").first.click()
        wait_idle(pg, timeout=60)
        txt = pg.evaluate("() => document.body.innerText")
        tag = "双栏" if "📑 双栏" in txt else "单栏"
        print(f"  第 {pg_no} 页检测结果：{tag}")
        if "📑 双栏" in txt and best is None:
            best = pg_no
    if best is None:
        best = 2
        print("  ⚠️ 未找到双栏页，退回第 2 页")
    print(f"  选定第 {best} 页做原生渲染")
    set_number(pg, "页码", best)
    # 最后一步才是渲染：渲染产物只活在自己那轮 rerun，之后不能再有任何点击
    btn(pg, "🎨 渲染该页").first.click()
    wait_idle(pg, timeout=120)
    wait_iframes(pg, min_n=1, timeout=90)
    wait_images(pg, 45)
    wait_idle(pg, timeout=60)
    time.sleep(1.5)
    shoot(pg, out / "p3.png", "p3", max_h=12000)
    # 中英同步滚动候选
    click_tab(pg, "中英同步滚动")
    btn(pg, "加载并逐段翻译").first.click()
    print("  逐段翻译（真实 LLM）…")
    wait_text(pg, "中英对照（逐段对齐", timeout=600, must=False)
    wait_idle(pg, timeout=90)
    shoot(pg, CAND_DIR / "p3_bi.png", "p3候选-中英同步滚动")


# ------------------------------------------------------------------ p5
def fix_p5(pg, out):
    print("→ [修复] p5 写作辅助（References 溯源表 + 分区裁剪）")
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
    print("  生成论文框架（真实 LLM 流式 + 白名单校验）…")
    wait_text(pg, "References 溯源", timeout=600, must=False)
    wait_text(pg, "引用白名单校验", timeout=300, must=False)
    wait_idle(pg, timeout=120)
    wait_images(pg, 45)
    time.sleep(2.0)
    shoot(pg, out / "p5.png", "p5", max_h=16000)
    # ① References 溯源区（6a 证明点）
    try:
        from PIL import Image
        im = Image.open(out / "p5.png")
        im.save(CAND_DIR / "p5_full.png")
        if crop_between(pg, out / "p5.png", r"References 溯源", r"引用白名单校验通过"):
            print("  p5.png 已裁为 References 溯源区")
        # ② CSV 出图区候选
        y_top = rect_of(pg, r"CSV 数据可视化", "top")
        y_bot = rect_of(pg, r"无幻觉论文框架", "top")
        if y_top and y_bot and y_bot > y_top:
            im2 = Image.open(CAND_DIR / "p5_full.png")
            im2.crop((0, max(0, (y_top - 60) * 2), im2.width,
                      min(im2.height, (y_bot - 40) * 2))).save(CAND_DIR / "p5_csv.png")
            print(f"  ✂️ p5_csv 候选 [{(y_top - 60) * 2}, {(y_bot - 40) * 2}]")
    except Exception as e:
        print("  ⚠️ p5 分区失败：", e)


# ------------------------------------------------------------------ p6
def fix_p6(pg, out):
    import re
    print("→ [修复] p6 检索与追踪（arXiv + PubMed 多源同屏）")
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
    # PubMed：:visible + 精确标签「关键词」（上一轮误中隐藏的「arXiv 分类 / 关键词」）
    try:
        loc = pg.locator("[data-testid='stTextInput']:visible").filter(
            has=pg.locator("[data-testid='stWidgetLabel']").filter(
                has_text=__import__("re").compile("^关键词$")))
        loc.first.locator("input").fill("graph neural network")
        time.sleep(0.3)
        btn(pg, "🔍 PubMed").first.click()
        wait_text(pg, "PubMed 结果", timeout=120, must=False)
    except Exception as e:
        print("  ⚠️ PubMed 检索失败：", e)
    wait_idle(pg, timeout=60)
    shoot(pg, out / "p6.png", "p6", max_h=14000)


def main():
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else ["p0", "p2", "p3", "p5", "p6"]
    flows = {"p0": fix_p0, "p2": fix_p2, "p3": fix_p3, "p5": fix_p5, "p6": fix_p6}
    load_keys()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        pg.goto("http://127.0.0.1:8599/", timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_selector("text=AI4S 科研助手", timeout=60000)
        wait_idle(pg, timeout=180)
        for tag in only:
            flows[tag](pg, ROOT / "docs" / "shots")
        b.close()
    print("✅ 修复轮完成")


if __name__ == "__main__":
    main()

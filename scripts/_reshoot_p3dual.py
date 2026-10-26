# -*- coding: utf-8 -*-
"""补拍 BERT(1706.03762v1.pdf) 双栏正文页的「原生渲染」截图。

输出 资料/截图/_p3_dual.png（临时文件，不覆盖任何正式图）。
方法：真双栏正文页（先用「检测本页栏数」确认），最后一步点「渲染该页」，
等 iframe 出现后按视口截图（1440x1250，dsf=2 → 2880x2500）。
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _reshoot_shots import (btn, load_keys, leak_check, nav,  # noqa: E402
                            set_number, wait_idle, wait_iframes)

from playwright.sync_api import sync_playwright  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "资料" / "截图" / "_p3_dual.png"


def click_in_expander(pg, expander_label, btn_label):
    """展开折叠区后再点里面的按钮。

    背景：首屏减法改造把「解析与版式诊断」收进了默认收起的 st.expander，
    里面的按钮在 DOM 里存在但 not visible ⇒ 直接 click 会一直等到超时。
    （实测 2026-10-26：p3 重拍卡在「检测本页栏数」element is not visible。）
    """
    try:
        expander = pg.get_by_text(expander_label).first
        expander.click()
        wait_idle(pg, timeout=60)
        time.sleep(0.6)
    except Exception as e:
        print("  ⚠️ 展开 %s 失败：%s" % (expander_label, e))
    btn(pg, btn_label).first.click()

def select_doc(pg, name):
    loc = pg.locator("[data-testid='stSelectbox']").filter(
        has=pg.locator("[data-testid='stWidgetLabel']", has_text="从文献库选择"))
    loc.first.locator("input").click()
    time.sleep(0.5)
    pg.keyboard.type(name)
    time.sleep(1.0)
    pg.keyboard.press("Enter")
    wait_idle(pg, timeout=90)
    time.sleep(1.0)


def main():
    load_keys()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=2)
        pg.goto("http://127.0.0.1:8599/", timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_selector("text=MoreHand", timeout=60000)
        wait_idle(pg, timeout=180)
        nav(pg, "版式阅读与智能翻译")
        # 1706.03762 各版本本地文件实测均为单栏排版（detect_columns n_cols=1）；
        # 库内唯一真双栏论文是 BERT：1810.04805v2.pdf（第 1-12 页 n_cols=2）
        select_doc(pg, "1810.04805v2.pdf")

        # 逐页检测分栏，优先第 3 页
        order = [3, 4, 2, 5, 6]
        best = None
        for no in order:
            set_number(pg, "页码", no)
            click_in_expander(pg, "解析与版式诊断", "检测本页栏数")
            wait_idle(pg, timeout=90)
            txt = pg.evaluate("() => document.body.innerText")
            tag = "双栏" if "双栏" in txt else "单栏"
            m = re.search(r"文本块\s*(\d+)", txt)
            print(f"  第 {no} 页：{tag} 文本块={m.group(1) if m else '?'}")
            if tag == "双栏":
                best = no
                break
        if best is None:
            print("❌ v1 未检测到双栏页，如实报告，不拍")
            b.close()
            return
        set_number(pg, "页码", best)
        # 最后一步才是渲染（渲染产物只活在自己那轮 rerun）
        btn(pg, "渲染该页").first.click()
        wait_idle(pg, timeout=120)
        n = wait_iframes(pg, min_n=1, timeout=90)
        print("  iframes:", n)
        time.sleep(3.0)
        # 把「原生渲染」页签滚到视口顶部附近
        pg.evaluate("""() => {
            const t = Array.from(document.querySelectorAll("[role='tab']"))
                .find(e => /原生渲染/.test(e.textContent || ''));
            if (t) { t.scrollIntoView({block: 'start'}); window.scrollBy(0, -70); }
        }""")
        time.sleep(1.5)
        leak_check(pg, "p3_dual")
        pg.screenshot(path=str(OUT), full_page=False)
        print("✅ 已存", OUT, OUT.stat().st_size, "bytes")
        b.close()


if __name__ == "__main__":
    main()

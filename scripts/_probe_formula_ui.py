# -*- coding: utf-8 -*-
"""探针：文献库页选中指定论文 → 点「🧮 提取 LaTeX 公式」→ 读回渲染出来的公式。

比 scripts/_probe_formula.py 多两件事：
  ① 显式指定论文（走「选择论文」下拉），否则拍的是列表里第一篇；
  ② 把展开器里的公式逐条打印出来，用来判断「抽到的是不是真公式」而不只是
     「有没有出现展开器」——2026-10-25 之前正是后者让碎片公式混过了验收。

用法：python scripts/_probe_formula_ui.py [论文文件名] [超时秒数]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._reshoot_shots import btn, nav, wait_idle, load_keys  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:8599"


def pick_paper(pg, name):
    """在「选择论文」下拉里选中 name。"""
    # 页面上可能有多个 stSelectbox（含隐藏的旧节点），按标签文本定位到「选择论文」
    box = pg.locator('div[data-testid="stSelectbox"]:has-text("选择论文")').first
    if box.count() == 0:
        box = pg.locator('div[data-testid="stSelectbox"]').filter(visible=True).first
    box.scroll_into_view_if_needed()
    box.click()
    time.sleep(1.0)
    # Streamlit 1.63 的下拉选项是 div[role="option"]（不是 li），实测 DOM 确认
    opt = pg.locator(f'div[role="option"]:has-text("{name}")').first
    opt.click()
    wait_idle(pg, 120)
    time.sleep(1.0)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "1412.6980v9.pdf"
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    load_keys()
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        pg = br.new_page(viewport={"width": 1440, "height": 1000})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        wait_idle(pg, 120)
        nav(pg, "📁 1. 文献库管理")
        wait_idle(pg, 120)
        pick_paper(pg, name)
        print(f"已选中：{name}")

        t0 = time.time()
        btn(pg, "提取 LaTeX 公式").first.click()
        print("已点击「提取 LaTeX 公式」，轮询中…")
        hit = False
        while time.time() - t0 < budget:
            txt = pg.evaluate("() => document.body.innerText")
            if "公式（" in txt:
                hit = True
                break
            time.sleep(1.5)
        elapsed = time.time() - t0
        print(f"\n结果：出现公式展开器={hit} 用时={elapsed:.1f}s")
        if not hit:
            txt = pg.evaluate("() => document.body.innerText")
            for ln in txt.split("\n"):
                if any(k in ln for k in ("失败", "⚠️", "超时", "公式")):
                    print("   !", ln.strip()[:120])
            br.close()
            return 1

        # 读展开器区块：把「公式（N）」之后的 katex 文本逐条打印
        blocks = pg.locator('div[data-testid="stExpander"]')
        n = blocks.count()
        for i in range(n):
            blk = blocks.nth(i)
            head = (blk.inner_text() or "")[:40].replace("\n", " ")
            if "公式" in head:
                kates = blk.locator(".katex")
                cnt = kates.count()
                print(f"  展开器「{head.strip()}」内 katex 渲染数={cnt}")
                for j in range(min(cnt, 12)):
                    t = (kates.nth(j).inner_text() or "").strip().replace("\n", " ")
                    print(f"    [{j+1}] {t[:90]}")
                break
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""快速重拍 p3（版式阅读 · 3a 双栏原生渲染不串栏）。

两个必要前提（都有真机依据，别再退回旧写法）：

1. **用 BERT 那篇拍双栏**（`1810.04805v2.pdf`）。实测 `detect_columns` 对六篇演示
   PDF 的判定：`1706.03762v7/v1` 各页均为单栏（该 PDF 是单栏重排版），
   `1810.04805v2` 各页均为双栏 —— 拿 Transformer 拍「双栏不串栏」等于自己打自己。
2. **渲染结果要跨重跑保留**。`ui/page_reading.py` 原先把 `embed_html` 写在按钮
   分支里，点「检测本页栏数」触发重跑后整块消失（旧 p3 图就是那个空态）。

流程：选 BERT → 页码 3 → 渲染该页 → 检测本页栏数（顺带验证 iframe 仍在）→ 截图。

用法：python scripts/_reshoot_p3_quick.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._reshoot_shots import (  # noqa: E402
    btn, load_keys, nav, set_number, shoot, wait_idle, wait_iframes,
)
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:8599"
OUT = Path(__file__).resolve().parents[1] / "资料" / "截图" / "p3.png"
DOC = "1810.04805v2.pdf"
PAGE = 3


def frames_text(pg):
    out = []
    for fr in pg.frames:
        if fr == pg.main_frame:
            continue
        try:
            t = fr.evaluate("() => document.body ? document.body.innerText : ''") or ""
            h = fr.evaluate("() => document.documentElement.scrollHeight")
        except Exception:
            t, h = "", -1
        out.append((h, len(t)))
    return out


def select_doc(pg, label, option):
    sb = pg.locator("[data-testid='stSelectbox']:visible").filter(
        has=pg.locator("[data-testid='stWidgetLabel']", has_text=label)).first
    sb.click()
    time.sleep(0.6)
    opt = pg.locator("[role='option']").filter(has_text=option).first
    opt.click()
    wait_idle(pg, 90)
    time.sleep(0.8)


def main():
    load_keys()
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        pg = br.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        wait_idle(pg, 60)

        print("→ p3 版式阅读与智能翻译（3a 双栏不串栏 · 快速版）")
        nav(pg, "📖 3. 版式阅读与智能翻译")
        wait_idle(pg, 60)

        try:
            select_doc(pg, "从文献库选择", DOC)
            print(f"  已选文献：{DOC}")
        except Exception as e:
            print(f"  ⚠️ 选择文献失败（沿用默认）：{e}")

        pg.locator("[role='tab']").filter(has_text="原生渲染").first.click()
        wait_idle(pg, 30)
        time.sleep(0.5)

        set_number(pg, "页码", PAGE)
        btn(pg, "🎨 渲染该页").first.click()
        n = wait_iframes(pg, min_n=1, timeout=180)
        wait_idle(pg, 120)
        time.sleep(1.5)
        f1 = frames_text(pg)
        print(f"  渲染后 iframe 数={n} {f1}")

        btn(pg, "🔍 检测本页栏数").first.click()
        wait_idle(pg, 90)
        time.sleep(1.5)
        f2 = frames_text(pg)
        print(f"  点「检测本页栏数」后 iframe 数={len(f2)} {f2}")

        txt = pg.evaluate("() => document.body.innerText")
        is2col = "双栏" in txt
        print(f"  栏数检测结果：{'双栏 ✅' if is2col else '单栏 ❌（换一页或换文献）'}")
        if not f2:
            print("  ❌ 渲染结果跨重跑消失")
            br.close()
            return 1

        time.sleep(6)          # 等 toast 淡出，别把「检测已完成」提示条拍进图里
        shoot(pg, OUT, "p3", max_h=12000)
        br.close()
    print("✅ 完成")
    return 0 if (f2 and is2col) else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""把文献库重置成「6 篇演示语料」的拍摄前置态。

为什么单独一个脚本：`_reshoot_shots.py` 只会「点一次加载演示数据」，它假定库已经
是干净态。库里若残留上次的官方重建结果（5 篇 / 1480 chunks），点加载只会追加，
拍摄出来的侧栏统计就不是演示态的 6 篇。

顺序：🗑️ 清空文献库 → ✅ 确认清空 → 🚀 一键加载演示数据 → 等「演示数据已就绪」。
"""
import sys
import re
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._reshoot_shots import btn, nav, wait_idle, load_keys  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:8599"


def main():
    load_keys()
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        pg = br.new_page(viewport={"width": 1440, "height": 1000})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        wait_idle(pg, 120)

        # 1) 清空（幂等：库已空时按钮可能不存在，不算错）
        try:
            btn(pg, "清空文献库").first.click()
            time.sleep(1.5)
            btn(pg, "确认清空").first.click()
            wait_idle(pg, 120)
            print("✅ 已清空文献库")
        except Exception as e:
            print("⚠️ 清空跳过：", str(e)[:100])

        # 2) 加载 6 篇
        t0 = time.time()
        btn(pg, "一键加载演示数据").first.click()
        print("已点击「一键加载演示数据」，等待就绪…")
        for _ in range(240):
            txt = pg.evaluate("() => document.body.innerText")
            if "演示数据已就绪" in txt or "演示数据加载完成" in txt:
                break
            time.sleep(1.5)
        wait_idle(pg, 180)
        txt = pg.evaluate("() => document.body.innerText")
        m = re.search(r"文献库\s*(\d+)\s*篇", txt)
        print(f"用时 {time.time()-t0:.1f}s | 侧栏统计：文献库 {m.group(1) if m else '?'} 篇")
        for line in txt.split("\n"):
            if "演示数据" in line or "文献库" in line:
                print("   ", line.strip()[:80])
        br.close()


if __name__ == "__main__":
    sys.exit(main())

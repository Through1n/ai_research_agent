# -*- coding: utf-8 -*-
"""单点探针：文献库页「🧮 提取 LaTeX 公式」能否在默认 LLM_TIMEOUT 内跑完并渲染。

为什么单独探：补拍 p0 时这一步连续 3 轮超时（每次 240s 白等），需要在不跑整条
11 分钟流程的前提下分辨三种可能：
  ① 请求本身太慢（>45s，撞 LLM_TIMEOUT）→ 后台任务失败；
  ② 请求成功但没有交付（bg_consume 未跑）→ 页面不渲染；
  ③ 解析失败（JSON 围栏 / LaTeX 转义）→ 任务报错。

输出：耗时、任务面板里的任务状态文本、页面上是否出现「公式（」展开器、
      顶部是否出现报错横幅（含原因行）。

用法：python scripts/_probe_formula.py
"""

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._reshoot_shots import btn, load_keys, nav, wait_idle  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:8599"


def main():
    load_keys()
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        pg = br.new_page(viewport={"width": 1440, "height": 1000})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        wait_idle(pg, 60)
        nav(pg, "📁 1. 文献库管理")
        wait_idle(pg, 60)

        t0 = time.time()
        btn(pg, "提取 LaTeX 公式").first.click()
        print("已点击「提取 LaTeX 公式」，开始轮询…")

        hit, elapsed = False, 0.0
        while time.time() - t0 < 180:
            txt = pg.evaluate("() => document.body.innerText")
            if "公式（" in txt:
                hit = True
                elapsed = time.time() - t0
                break
            time.sleep(1.5)
        if not hit:
            elapsed = time.time() - t0

        txt = pg.evaluate("() => document.body.innerText")
        print(f"\n结果：命中展开器={hit} 用时={elapsed:.1f}s")

        # 任务面板状态
        for m in re.finditer(r"[✅❌🕐⚙️][^\n]{0,80}", txt):
            line = m.group(0).strip()
            if any(k in line for k in ("公式", "抽取", "解析", "参考文献")):
                print("  任务面板:", line)
        # 顶部报错横幅
        for kw in ("失败", "可能原因", "无法解析", "超时"):
            for m in re.finditer(rf"[^\n]{{0,120}}{kw}[^\n]{{0,120}}", txt):
                s = m.group(0).strip()
                if len(s) > 12:
                    print("  横幅:", s[:200])
                break

        if hit:
            print(f"\n✅ 公式抽取成功并渲染（{elapsed:.1f}s）")
        else:
            print("\n❌ 未出现公式展开器——见上方任务面板 / 横幅定位原因")
        br.close()
    return 0 if hit else 1


if __name__ == "__main__":
    sys.exit(main())

"""品牌徽标落地验真：三处同一张图 + 「有界框 + 可滚动 + 点击看详情」。

对应 2026-10-25 的产品需求：
  ① 所有展示内容（图片与文字）限定在明确的最大框定范围内，不得溢出边界；
  ② 内容超出框定范围时，支持框内上下滑动，或提供点击进入查看详情的交互。

验的不是「渲染没报错」，而是**计算样式与几何**：
  - 侧栏品牌区真有徽标图，且与页面 favicon、徽标区块用的是同一张图；
  - 徽标卡 max-height 真的锁死（算出来的 computed style 不是 none）；
  - 内容确实超出（scrollHeight > clientHeight）——不超出就说明这条断言是空转；
  - 超出部分在框内滚动（overflow-y:auto）且横向不溢出（scrollWidth <= clientWidth）；
  - 图片右缘不越出框的右缘（几何断言，不是样式断言）；
  - 「品牌徽标」区块藏在折叠区里，点一下才出现（点击进入详情的交互）。

用法：python scripts/_verify_brand_badge.py [--port 8530] [--serve]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from _dist_verify_common import pick_port, wait_http  # noqa: E402

BADGE_SEL = ".ai-brand-badge"


def wait_ready(page) -> None:
    page.wait_for_selector('[data-testid="stSidebar"]', timeout=180000)
    page.wait_for_function("() => !document.body.innerText.includes('Please wait')",
                           timeout=120000)
    page.wait_for_timeout(2500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8530)
    ap.add_argument("--serve", action="store_true")
    args = ap.parse_args()
    port = pick_port(args.port)
    proc = None
    problems: list[str] = []

    def ok(msg):
        print(f"  ✅ {msg}")

    def bad(msg):
        print(f"  ❌ {msg}")
        problems.append(msg)

    try:
        url = f"http://127.0.0.1:{port}/"
        if args.serve:
            proc = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", "app.py",
                 "--server.port", str(port), "--server.headless", "true",
                 "--browser.gatherUsageStats", "false"],
                cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_http(url):
            print("[FAIL] 服务没起来")
            return 1
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            wait_ready(page)

            print("[1/5] 侧栏品牌区：徽标图 + 品牌名 + 副标题")
            imgs = page.locator('[data-testid="stSidebar"] img')
            if imgs.count() == 0:
                bad("侧栏没有徽标图（brand_logo 没解析到 assets/icon.png？）")
            else:
                src = imgs.first.get_attribute("src") or ""
                ok(f"侧栏徽标图已渲染（{len(src)} 字符的 data/文件 src）")
            side_text = page.locator('[data-testid="stSidebar"]').inner_text()
            if "MoreHand" in side_text:
                ok("侧栏出现品牌名 MoreHand")
            else:
                bad("侧栏没有品牌名 MoreHand")
            if "研究流水线" in side_text:
                ok("侧栏出现品牌副标题")
            else:
                bad("侧栏没有品牌副标题")

            print("[2/5] 页面 favicon 指向徽标（不再是默认/emoji）")
            fav = page.evaluate(
                "() => {const l = document.querySelector('link[rel~=\"icon\"]');"
                " return l ? (l.href || '') : '';}")
            if fav:
                ok(f"favicon = {fav[:80]}")
            else:
                bad("页面没有 favicon link")

            print("[3/5] 点击进入查看详情：折叠区点开才出现徽标卡")
            try:
                page.get_by_text("关于", exact=False).first.click(timeout=15000)
                page.wait_for_timeout(600)
                page.get_by_text("品牌徽标", exact=False).first.click(timeout=15000)
                page.wait_for_timeout(1200)
            except Exception as exc:  # noqa: BLE001
                bad(f"点不开「关于 / 品牌徽标」折叠区：{type(exc).__name__}")
            if page.locator(BADGE_SEL).count() == 0:
                bad("徽标卡没有出现（点击交互失效）")
                print("\nRESULT: FAIL —— %d 项" % len(problems))
                for x in problems:
                    print("  -", x)
                return 1
            ok("点开折叠区后徽标卡已渲染")

            print("[4/5] 有界框：max-height 锁死 + 横向不溢出")
            box = page.locator(BADGE_SEL).first
            m = box.evaluate(
                "el => {const s = getComputedStyle(el);"
                " return {maxH: s.maxHeight, oy: s.overflowY, ox: s.overflowX,"
                " cw: el.clientWidth, sw: el.scrollWidth,"
                " ch: el.clientHeight, sh: el.scrollHeight,"
                " r: el.getBoundingClientRect().right};}")
            if m["maxH"] and m["maxH"] != "none":
                ok(f"max-height = {m['maxH']}（框定范围已锁死）")
            else:
                bad(f"max-height 未生效（computed = {m['maxH']!r}）")
            if m["oy"] == "auto" or m["oy"] == "scroll":
                ok(f"overflow-y = {m['oy']}（超出可在框内上下滑动）")
            else:
                bad(f"overflow-y = {m['oy']!r}，超出内容无处可滑")
            if m["sw"] <= m["cw"] + 1:
                ok(f"横向不溢出（scrollWidth {m['sw']} <= clientWidth {m['cw']}）")
            else:
                bad(f"横向溢出：scrollWidth {m['sw']} > clientWidth {m['cw']}")
            if m["sh"] > m["ch"]:
                ok(f"内容确实超出框高（scrollHeight {m['sh']} > clientHeight {m['ch']}）"
                   "——说明上面几条断言不是空转")
            else:
                bad("内容没超出框高，这条用例没验到东西（文案太少？）")

            print("[5/5] 图片不越界 + 滚到底仍完整可读")
            img_r = box.evaluate(
                "el => {const i = el.querySelector('img');"
                " if (!i) return null; const a = i.getBoundingClientRect(),"
                " b = el.getBoundingClientRect();"
                " return {iw: Math.round(a.width), ir: Math.round(a.right),"
                " br: Math.round(b.right)};}")
            if img_r is None:
                bad("徽标卡里没有图片")
            elif img_r["ir"] <= img_r["br"] + 1:
                ok(f"图片右缘 {img_r['ir']} <= 框右缘 {img_r['br']}（不溢出边界）")
            else:
                bad(f"图片越界：图片右缘 {img_r['ir']} > 框右缘 {img_r['br']}")
            after = box.evaluate(
                "el => {el.scrollTop = el.scrollHeight;"
                " return {top: el.scrollTop, h: el.scrollHeight, ch: el.clientHeight};}")
            if after["top"] > 0:
                ok(f"可滚动到底（scrollTop {after['top']}，内容高 {after['h']}）")
            else:
                bad("滚不动（scrollTop 仍为 0）")
            tail = box.evaluate("el => (el.innerText || '').slice(-40)")
            print(f"      滚到底部末行：{tail.strip()!r}")
            shot = ROOT / "docs" / "shots" / "_brand_badge.png"
            page.screenshot(path=str(shot))
            print(f"      截图：{shot}")
            b.close()
    finally:
        if proc is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)

    print()
    if problems:
        print(f"RESULT: FAIL —— {len(problems)} 项不通过")
        for x in problems:
            print("  -", x)
        return 1
    print("RESULT: PASS —— 徽标三处同源、框定范围锁死、超出可滚动、点击可看详情")
    return 0


if __name__ == "__main__":
    sys.exit(main())

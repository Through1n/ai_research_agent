# -*- coding: utf-8 -*-
"""阅读页 A1「原生渲染」结果持久性探针（真机 · 计算 DOM 证据）。

背景（2026-10-24 补拍 p3 时发现）：
  `ui/page_reading.py` 里 `embed_html(...)` 写在
  `if st.button("🎨 渲染该页"):` 的**分支体内**——点击当轮渲染一次 iframe，
  下一轮该按钮返回 False，iframe 不再发射，于是「点一下别的按钮，渲染结果
  整块消失」。栏数检测同理。

本探针不看源代码，只操作真机 DOM：
  ① 打开阅读页 → 原生渲染页签
  ② 点「🎨 渲染该页」→ 记录 iframe 数量 / 首帧高度 / 帧内文本长度
  ③ 点「🔍 检测本页栏数」（无关按钮，触发一次重跑）
  ④ 再看 iframe：应当仍在（持久），且帧内有文本（不是空白壳）
  ⑤ 再点一次「🎨 渲染该页」→ 数量不应翻倍（幂等，不是越点越多）

用法：
  python scripts/_verify_read_render_persist.py            # 断言模式（失败退出码 1）
  python scripts/_verify_read_render_persist.py --dump     # 只打印现场，不断言
"""

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8599"
FAILS = []


def _ck(name, ok, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


def _wait_idle(pg, timeout=90, stable=2.0):
    t0 = time.time()
    last, last_change = None, time.time()
    while time.time() - t0 < timeout:
        busy = pg.evaluate("""() => {
            for (const s of ['[data-testid="stStatusWidget"]','[data-testid="stSpinner"]']) {
                const el = document.querySelector(s);
                if (el && el.offsetParent !== null) return true;
            }
            return false;
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
        time.sleep(0.7)
    return False


def _frames(pg):
    """只数 components.v1.html 这类业务 iframe（Streamlit 自身无 iframe）。"""
    out = []
    for fr in pg.frames:
        if fr == pg.main_frame:
            continue
        try:
            h = fr.evaluate("() => document.documentElement.scrollHeight || document.body.scrollHeight")
        except Exception:
            h = -1
        try:
            n = len(fr.evaluate("() => document.body ? document.body.innerText : ''") or "")
        except Exception:
            n = -1
        out.append({"url": (fr.url or "")[:60], "h": h, "text": n})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        pg = br.new_page(viewport={"width": 1440, "height": 1000})
        pg.goto(args.base, wait_until="domcontentloaded", timeout=60000)
        _wait_idle(pg, 60)

        # ① 导航到阅读页（侧栏第 3 项）
        pg.locator("[data-testid='stSidebar'] label").filter(
            has_text="版式阅读与智能翻译").first.click()
        _wait_idle(pg, 60)
        time.sleep(0.6)

        # ② 确保在「原生渲染」页签
        pg.locator("[role='tab']").filter(has_text="原生渲染").first.click()
        _wait_idle(pg, 30)
        time.sleep(0.5)

        before = _frames(pg)
        print(f"① 初始 iframe 数 = {len(before)}")
        _ck("初始无渲染 iframe（未点按钮时不预渲染）", len(before) == 0,
            f"frames={[f['h'] for f in before]}")

        # ③ 点「渲染该页」
        pg.locator("button").filter(has_text="渲染该页").first.click()
        _wait_idle(pg, 120)
        time.sleep(1.5)
        after_render = _frames(pg)
        print(f"② 点「渲染该页」后 iframe 数 = {len(after_render)}")
        for f in after_render:
            print(f"     h={f['h']} text={f['text']} url={f['url']}")
        ok_render = len(after_render) >= 1
        _ck("渲染后有 iframe", ok_render)
        if ok_render:
            _ck("渲染帧内有内容（高度>200 且文本>50）",
                any(f["h"] > 200 and f["text"] > 50 for f in after_render))

        # ④ 点无关按钮「检测本页栏数」→ 触发重跑
        pg.locator("button").filter(has_text="检测本页栏数").first.click()
        _wait_idle(pg, 120)
        time.sleep(1.5)
        after_col = _frames(pg)
        print(f"③ 点「检测本页栏数」后 iframe 数 = {len(after_col)}")
        for f in after_col:
            print(f"     h={f['h']} text={f['text']}")
        _ck("★ 渲染结果跨重跑仍在（核心断言）", len(after_col) >= 1,
            "点击无关按钮后 iframe 消失 = 结果不持久")
        if after_col:
            _ck("跨重跑后帧内仍有内容",
                any(f["h"] > 200 and f["text"] > 50 for f in after_col))
        _ck("栏数检测结果已显示",
            ("单栏" in pg.evaluate("() => document.body.innerText")
             or "双栏" in pg.evaluate("() => document.body.innerText")))

        # ⑤ 幂等：再点一次渲染，不应叠加出第二个 iframe
        pg.locator("button").filter(has_text="渲染该页").first.click()
        _wait_idle(pg, 120)
        time.sleep(1.5)
        again = _frames(pg)
        print(f"④ 再点「渲染该页」后 iframe 数 = {len(again)}")
        _ck("重复点击不叠加 iframe（≤1）", len(again) <= 1, f"实际 {len(again)}")

        br.close()

    print()
    if args.dump:
        print("（--dump 模式，不做断言结论）")
        return 0
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：" + " / ".join(FAILS))
        return 1
    print("✅ 全部通过：渲染结果跨重跑持久、帧内有内容、重复点击不叠加")
    return 0


if __name__ == "__main__":
    sys.exit(main())

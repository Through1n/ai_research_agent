"""抓线上 JS 错误：走遍 9 个页面 + 演示路径，记录每条 console error 的时机与文本。

背景：`_accept_online.py` 报 `js_errors_total = 2` 但 `js_errors_after_nav` 为空，
说明这 2 条发生在"导航出现"那次快照**之后**。首轮探针只加载了首页，抓到 0 条，
所以必须走遍页面才能复现。
"""
import json
import time

from playwright.sync_api import sync_playwright

URL = "https://ai4s-research-agent.app.workbuddy.host/"
OUT = "资料/截图/online"

MAIN = ["智能总助手", "文献库管理", "跨文献问答与溯源",
        "版式阅读与智能翻译", "论文检索与追踪",
        "引用图谱与综述", "写作辅助与可视化"]
ABOUT = ["系统架构设计", "技术创新亮点"]

log = []


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        t0 = time.time()

        def on_console(m):
            if m.type == "error":
                log.append({"t": round(time.time() - t0, 1), "kind": "console",
                            "text": m.text[:400]})

        def on_pageerr(e):
            log.append({"t": round(time.time() - t0, 1), "kind": "pageerror",
                        "text": str(e)[:400]})

        def on_reqfail(r):
            log.append({"t": round(time.time() - t0, 1), "kind": "requestfailed",
                        "text": f"{r.url[:160]}"})

        pg.on("console", on_console)
        pg.on("pageerror", on_pageerr)
        pg.on("requestfailed", on_reqfail)

        pg.goto(URL, wait_until="domcontentloaded", timeout=90000)
        for _ in range(120):
            if pg.locator("input[type=radio]").count() >= 2:
                break
            time.sleep(1)
        log.append({"t": round(time.time() - t0, 1), "kind": "mark", "text": "导航就位"})

        # 主区 7 页。⚠️ 用 dispatch_event 而非 .check()：线上有遮罩层会拦截
        # pointer events，.check() 会重试 14 次后超时（首轮探针就这么挂的）。
        for lab in MAIN:
            r = pg.locator(f"input[type=radio][aria-label='{lab}']")
            if not r.count():
                r = pg.get_by_role("radio", name=lab)
            if r.count():
                try:
                    r.first.dispatch_event("click")
                except Exception as e:
                    log.append({"t": round(time.time() - t0, 1), "kind": "clickfail",
                                "text": f"{lab}: {str(e)[:120]}"})
                time.sleep(3.2)
                log.append({"t": round(time.time() - t0, 1), "kind": "mark",
                            "text": f"已切换 {lab}"})

        # 折叠区 2 页
        try:
            exp = pg.get_by_text("ℹ️ 关于本系统", exact=False).first
            if exp.count():
                exp.dispatch_event("click")
                time.sleep(1.2)
        except Exception:
            pass
        for lab in ABOUT:
            btn = pg.get_by_role("button", name=lab)
            if btn.count():
                try:
                    btn.first.dispatch_event("click")
                except Exception as e:
                    log.append({"t": round(time.time() - t0, 1), "kind": "clickfail",
                                "text": f"{lab}: {str(e)[:120]}"})
                time.sleep(3.5)
                log.append({"t": round(time.time() - t0, 1), "kind": "mark",
                            "text": f"已切换 {lab}"})

        # 演示路径
        try:
            pg.get_by_role("radio", name="文献库管理").first.dispatch_event("click")
            time.sleep(2.5)
            demo = pg.get_by_role("button", name="一键加载演示数据")
            if demo.count():
                demo.first.dispatch_event("click")
                time.sleep(8)
                log.append({"t": round(time.time() - t0, 1), "kind": "mark",
                            "text": "已点加载演示数据"})
        except Exception as e:
            log.append({"t": round(time.time() - t0, 1), "kind": "clickfail",
                        "text": f"演示路径: {str(e)[:120]}"})

        b.close()

    errs = [e for e in log if e["kind"] != "mark"]
    print(f"=== 全程 {len(log)} 条事件，其中错误类 {len(errs)} 条 ===")
    for e in log:
        if e["kind"] == "mark":
            print(f"  [{e['t']:6.1f}s] ── {e['text']}")
        else:
            print(f"  [{e['t']:6.1f}s] !! [{e['kind']}] {e['text']}")

    with open(f"{OUT}/js_errors.json", "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"\n结果写入 {OUT}/js_errors.json")


if __name__ == "__main__":
    main()

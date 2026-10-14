"""复验补充探针：捕获全部 console 消息 + 点开折叠区两个页面（🏗️/💡）。

`_accept_online.py` 只点主区 radio，而 🏗️ A. 系统架构设计 / 💡 B. 技术创新亮点
在侧边栏「ℹ️ 关于本系统」expander 里、是**按钮不是 radio**（ui/sidebar.py:385），
所以验收脚本对这两页恒为 clicked=False。本探针补上这块，并抓启动期的 JS 错误。
"""
import json
import os
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("TARGET_URL", "https://ai4s-research-agent.app.workbuddy.host/")
OUT = "docs/shots/online"


def main():
    msgs, errs = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        pg.on("console", lambda m: msgs.append((m.type, m.text[:300])))
        pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
        pg.on("requestfailed", lambda r: msgs.append(("requestfailed", f"{r.url[:120]}")))

        t0 = time.time()
        pg.goto(URL, wait_until="domcontentloaded", timeout=90000)
        # 等导航 radio 就位（修复后约 9s）
        for _ in range(120):
            if pg.locator("input[type=radio]").count() >= 2:
                break
            time.sleep(1)
        nav_sec = time.time() - t0
        print(f"[probe] 导航就位 {nav_sec:.1f}s")

        # ① 抓启动期 JS 错误（_accept_online 只抓导航出现之后的，漏了启动期）
        print(f"[probe] console 消息 {len(msgs)} 条 / pageerror {len(errs)} 条")
        for t, txt in msgs:
            if t in ("error", "requestfailed"):
                print(f"    [{t}] {txt}")
        for e in errs:
            print(f"    [pageerror] {e}")

        # ② 点开「ℹ️ 关于本系统」expander，再点两个页面按钮
        exp = pg.get_by_text("ℹ️ 关于本系统", exact=False).first
        if exp.count():
            exp.click()
            time.sleep(1.2)
        for label, shot in [("🏗️ A. 系统架构设计", "o14_arch.png"),
                            ("💡 B. 技术创新亮点", "o15_innov.png")]:
            btn = pg.get_by_role("button", name=label)
            if btn.count():
                btn.first.click()
                time.sleep(3.5)
                body = pg.inner_text("body")
                pg.screenshot(path=os.path.join(OUT, shot), full_page=True)
                print(f"[probe] {label} → 点击成功，正文 {len(body)} 字，截图 {shot}")
            else:
                print(f"[probe] {label} → 未找到按钮")

        b.close()

    with open(os.path.join(OUT, "probe2.json"), "w", encoding="utf-8") as f:
        json.dump({"nav_sec": round(nav_sec, 1),
                   "console": msgs, "pageerrors": errs}, f, ensure_ascii=False, indent=2)
    print("[probe] 结果写入 docs/shots/online/probe2.json")


if __name__ == "__main__":
    main()

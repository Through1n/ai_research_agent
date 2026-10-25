"""深度验证 dist 产物：真浏览器点「一键加载演示数据（6 篇）」，验到业务闭环。

前面 _verify_dist_desktop.py 证明了「能启动、能渲染」；本脚本再往前一步：
点真实按钮 → 走 文本入库 + 向量化(chromadb/onnx) + 落盘 → 断言侧栏变成「文献库 6 篇」。
这一步才真正把「打包时漏了 chromadb 的原生依赖」这类问题暴露出来。
"""
import sys

from _dist_verify_common import ROOT, FrozenApp, pick_port, require_exe, wait_http

PORT = 8766
BTN = "一键加载演示数据（6 篇）"
EXPECT = "文献库 6 篇"


def main():
    err = require_exe()
    if err:
        print(f"[FAIL] {err}")
        return 1
    port = pick_port(PORT)

    print(f"[1/3] 起服务（临时 APPDATA，端口 {port}）")
    with FrozenApp(port) as app:
        ok, why = wait_http(app.url)
        if not ok:
            print(f"[FAIL] 服务没起来：{why}")
            app.dump_logs()
            return 1

        from playwright.sync_api import sync_playwright
        errs = []
        shot = None
        exc_blocks = []
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: errs.append(str(e)[:300]))
            page.goto(app.url, wait_until="domcontentloaded", timeout=60000)
            print(f"[2/3] 点「{BTN}」")
            # text= 是子串匹配，会命中包含该文案的容器（侧栏底部也有「一键加载演示数据」字样），
            # 必须按 role=button 精确点；并且在点之前等会话真正连上，否则点了不生效。
            page.wait_for_selector('[data-testid="stSidebar"]', timeout=240000)
            page.wait_for_function(
                "() => !document.body.innerText.includes('Please wait')", timeout=120000)
            page.wait_for_timeout(2000)
            btn = page.get_by_role("button", name=BTN)
            btn.first.wait_for(state="visible", timeout=120000)
            btn.first.click()

            print("[3/3] 等待入库完成（文本语料 + 向量化，首次较慢）")
            try:
                page.wait_for_selector(f"text={EXPECT}", timeout=300000)
                print(f"      侧栏出现「{EXPECT}」")
            except Exception as exc:  # noqa: BLE001
                print(f"[FAIL] 没等到「{EXPECT}」：{type(exc).__name__}")
                print("      --- 侧栏文本 ---")
                try:
                    print(page.inner_text("section[data-testid='stSidebar']")[-1200:])
                except Exception:  # noqa: BLE001
                    pass
                b.close()
                app.dump_logs()
                return 1

            shot = ROOT / "docs" / "shots" / "_dist_deep.png"
            page.screenshot(path=str(shot))
            exc_blocks = page.query_selector_all('[data-testid="stException"]')
            b.close()

    if exc_blocks:
        print(f"[FAIL] 出现 {len(exc_blocks)} 个异常块")
        return 1
    if errs:
        print(f"[FAIL] 未捕获 pageerror：{errs[:3]}")
        return 1
    print(f"\nRESULT: PASS —— 演示数据 6 篇入库成功，业务闭环可用（截图 {shot}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

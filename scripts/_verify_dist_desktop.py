"""验证 dist 里的桌面版产物真的能用（冻结态、无 GUI）。

分四段，逐段都要过：
1. **临时 APPDATA 启动** exe 的 `--st-server` 模式，模拟「全新安装首次运行」。
   必须换 APPDATA：真实 %APPDATA% 里已有 app/ 旧业务目录时，ensure_app_copy() 会跳过
   复制，跑的是旧代码，验证就失真了。
2. **HTTP 探活** —— 只证明 streamlit 起来了。注意：纯 HTTP GET **不会执行 app.py**
   （Streamlit 的脚本跑在 WebSocket 会话里），所以这一关过了不代表业务代码能跑。
3. **真浏览器加载**（headless Chromium）—— 建立会话、真正执行 app.py，这才验到
   业务依赖（chromadb/onnxruntime/pymupdf…）在冻结态能不能 import。
4. **断言**：侧栏出现本次新文案「一键加载演示数据（6 篇）」、页面无 Streamlit 异常块、
   无未捕获 pageerror。

只读 + 临时目录，不碰仓库、不碰真实 %APPDATA%。
"""
import sys

from _dist_verify_common import ROOT, FrozenApp, pick_port, require_exe, wait_http

PORT = 8765
# 本次业务代码的特征串：只有新代码里才有（旧文案是「（v1/v7/BERT）」）
FINGERPRINT = "一键加载演示数据（6 篇）"


def main():
    err = require_exe()
    if err:
        print(f"[FAIL] {err}")
        return 1
    port = pick_port(PORT)

    print(f"[1/4] 启动产物（临时 APPDATA，端口 {port}）")
    with FrozenApp(port) as app:
        print("[2/4] HTTP 探活（只证明 streamlit 起来了）")
        ok, why = wait_http(app.url)
        if not ok:
            print(f"[FAIL] 服务没起来：{why}")
            app.dump_logs()
            return 1
        print("      HTTP 200")

        print("[3/4] headless 浏览器加载（真正执行 app.py）")
        rc = _browser_check(app.url)
        if rc != 0:
            app.dump_logs()
            return rc

        print("[4/4] 校验复制出来的业务代码是本次新代码")
        sidebar = app.app_dir / "ui" / "sidebar.py"
        if not sidebar.exists():
            print(f"[FAIL] 业务代码没被复制：{sidebar}")
            return 1
        if "（6 篇）" not in sidebar.read_text(encoding="utf-8", errors="replace"):
            print("[FAIL] 复制出的 sidebar.py 是旧代码")
            return 1
        print("      app_bundle 已复制且为新代码")

    print("\nRESULT: PASS —— 产物可启动、可渲染、业务代码为本次版本")
    return 0


def _browser_check(url):
    from playwright.sync_api import sync_playwright

    page_errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)[:300]))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # 首次运行要 import chromadb/onnxruntime，冷启动慢，给足时间等侧栏出现
            page.wait_for_selector(f"text={FINGERPRINT}", timeout=240000)
            print(f"      侧栏命中「{FINGERPRINT}」")
        except Exception as exc:  # noqa: BLE001 - 失败要把现场打出来
            print(f"[FAIL] 页面没渲染出预期内容：{type(exc).__name__}: {exc}")
            try:
                print("      --- 页面文本片段 ---")
                print(page.inner_text("body")[:800])
            except Exception:  # noqa: BLE001
                pass
            if page_errors:
                print("      --- pageerror ---")
                for e in page_errors[:5]:
                    print(f"      {e}")
            browser.close()
            return 1

        # Streamlit 的异常块：冻结态缺模块时通常以 stException 呈现
        exc_blocks = page.query_selector_all('[data-testid="stException"]')
        try:
            shot = ROOT / "docs" / "shots" / "_dist_verify.png"
            page.screenshot(path=str(shot))
            print(f"      截图：{shot}")
        except Exception:  # noqa: BLE001
            pass
        browser.close()

    if page_errors:
        print(f"[FAIL] 有未捕获 pageerror：{page_errors[:3]}")
        return 1
    if exc_blocks:
        print(f"[FAIL] 页面出现 {len(exc_blocks)} 个 Streamlit 异常块")
        return 1
    print("      无未捕获异常、无 stException 块")
    return 0


if __name__ == "__main__":
    sys.exit(main())

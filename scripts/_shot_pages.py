"""界面快照：把 9 个页面按 1440×900 视口各拍一张（不全页），用于改版前后对比。

为什么不全页：文献库页全页高 17882px（@2x），一张全页图既看不清细节，
也掩盖了「一页要滚十几屏」这个真正的杂乱信号。视口快照才反映用户首屏看到什么。

用法：
    python scripts/_shot_pages.py --port 8502 --out docs/shots/_pre
"""
import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    ("p0_agent", "智能总助手"),
    ("p1_library", "文献库管理"),
    ("p2_qa", "跨文献问答与溯源"),
    ("p3_reading", "版式阅读与智能翻译"),
    ("p4_search", "论文检索与追踪"),
    ("p5_graph", "引用图谱与综述"),
    ("p6_writing", "写作辅助与可视化"),
]


def wait_http(url, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8502)
    ap.add_argument("--out", default="docs/shots/_pre")
    ap.add_argument("--serve", action="store_true", help="自己起一个 streamlit 再拍")
    args = ap.parse_args()

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    proc = None
    if args.serve:
        proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app.py",
             "--server.port", str(args.port), "--server.headless", "true",
             "--browser.gatherUsageStats", "false"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{args.port}/"
        if not wait_http(url):
            print("[FAIL] 服务没起来")
            return 1
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 1440, "height": 900},
                              device_scale_factor=2)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('[data-testid="stSidebar"]', timeout=180000)
            page.wait_for_function("() => !document.body.innerText.includes('Please wait')",
                                   timeout=120000)
            page.wait_for_timeout(2500)
            for slug, label in PAGES:
                try:
                    page.locator('[data-testid="stSidebar"]').get_by_text(
                        label, exact=False).first.click(timeout=20000)
                    page.wait_for_timeout(2200)
                    page.wait_for_function(
                        "() => !document.body.innerText.includes('Please wait')", timeout=90000)
                    page.wait_for_timeout(800)
                except Exception as exc:
                    print(f"  [WARN] 切页 {label} 失败：{type(exc).__name__}")
                shot = out / f"{slug}.png"
                page.screenshot(path=str(shot))
                # 记录首屏高度信息，用于量化「一页几屏」
                h = page.evaluate(
                    "() => {const m = document.querySelector('[data-testid=\"stMain\"]')"
                    " || document.querySelector('[data-testid=\"stAppViewContainer\"]');"
                    " return (m || document.body).scrollHeight;}")
                print(f"  {slug:12} 截图={shot.name}  内容高={h}px  屏数≈{round((h or 0)/900, 1)}")
            b.close()
    finally:
        if proc is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

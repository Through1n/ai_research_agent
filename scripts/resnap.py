"""刷新 资料/截图 界面截图：启动临时 Streamlit → playwright 逐页截图。
用法：python scripts/resnap.py
需要本机 conda 环境已安装 playwright 与 chromium（一次性）。
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "资料", "截图")
PORT = 8599
URL = f"http://127.0.0.1:{PORT}"

PAGES = [  # (query参数p, 输出文件名, 加载后额外等待秒, 进入页面后点击的按钮文本)
    ("0", "p1", 2.5, None),
    ("1", "p0", 2.0, None),
    ("2", "p2", 2.0, None),
    ("3", "p3", 2.0, None),
    ("5", "p4", 2.0, "构建引用图谱"),
    ("6", "p5", 2.0, None),
    ("4", "p6", 2.0, None),
    ("7", "p7", 1.5, None),
    ("8", "p8", 1.5, None),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.port", str(PORT), "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 等待服务就绪
        import urllib.request
        for _ in range(60):
            try:
                urllib.request.urlopen(URL + "/_stcore/health", timeout=2)
                break
            except Exception:
                time.sleep(1)
        else:
            print("Streamlit 未就绪"); return 1

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1560, "height": 950})
            # 先加载演示数据：打开文献库页，点侧边栏按钮
            page.goto(f"{URL}/?p=1", wait_until="networkidle")
            page.wait_for_timeout(3000)
            try:
                page.locator("button", has_text="一键加载演示数据").first.click(timeout=8000)
                page.wait_for_timeout(12000)  # 内置语料入库+重建索引
            except Exception as e:
                print("演示数据加载失败（继续截空库状态）:", str(e)[:80])
            for q, name, wait, click_text in PAGES:
                try:
                    page.goto(f"{URL}/?p={q}", wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(wait * 1000)
                    if click_text:
                        try:
                            page.locator("button", has_text=click_text).first.click(timeout=6000)
                            page.wait_for_timeout(40000)  # 建图+渲染
                        except Exception as e:
                            print(f"  点击「{click_text}」失败: {str(e)[:60]}")
                    page.screenshot(path=os.path.join(OUT, f"{name}.png"))
                    print(f"✅ {name}.png")
                except Exception as e:
                    print(f"⚠️ {name} 截图失败: {str(e)[:100]}")
            browser.close()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())

"""静默启动器：无任何黑窗口。
- 已在运行则直接打开浏览器；
- 未运行则隐藏窗口拉起 Streamlit 服务，等待健康检查通过后自动开浏览器。
配合「启动科研助手.vbs」被 pythonw 调用，全程无 CMD 弹窗。
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8501
URL = f"http://127.0.0.1:{PORT}"


def port_open():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def healthy():
    try:
        with urllib.request.urlopen(URL + "/_stcore/health", timeout=1.5) as r:
            return r.read().decode().strip() == "ok"
    except Exception:
        return False


def main():
    if port_open() and healthy():
        webbrowser.open(URL)
        return
    py = sys.executable  # pythonw.exe（无控制台）
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows：子进程也不弹窗
    subprocess.Popen(
        [py, "-m", "streamlit", "run", "app.py",
         "--server.port", str(PORT), "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT, creationflags=flags,
        stdout=open(os.path.join(ROOT, "storage", "streamlit_run.log"), "a", encoding="utf-8"),
        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    for _ in range(90):          # 最长等待 90 秒（首次加载模型可能较慢）
        time.sleep(1)
        if healthy():
            break
    webbrowser.open(URL)


if __name__ == "__main__":
    main()

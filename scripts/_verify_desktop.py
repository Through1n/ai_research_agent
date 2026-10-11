"""开发态桌面壳轻量验证：
1. 后台启动 python -m desktop（AI4S_DESKTOP=1）
2. 轮询 app.lock 拿端口
3. 轮询 /_stcore/health 直到 ok 或超时 25s
4. 读 pid，taskkill /PID <pid> /T /F 杀整棵树
5. 报告结果
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PY = r"C:\Users\Administrator\anaconda3\envs\ai_research\python.exe"
ROOT = r"C:\Users\Administrator\ai_research_agent"
DATA = Path(os.environ["APPDATA"]) / "AI4SResearchAgent"
LOCK = DATA / "app.lock"

print(f"[verify] data dir: {DATA}")

# 清旧锁/旧日志
for p in [LOCK]:
    if p.exists():
        p.unlink()

env = os.environ.copy()
env["AI4S_DESKTOP"] = "1"

print("[verify] starting desktop shell subprocess ...")
proc = subprocess.Popen(
    [PY, "-m", "desktop"],
    cwd=ROOT,
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
print(f"[verify] shell pid = {proc.pid}")

port = None
deadline = time.time() + 25.0
while time.time() < deadline:
    if LOCK.exists():
        try:
            data = json.loads(LOCK.read_text(encoding="utf-8"))
            port = int(data["port"])
            pid_in_lock = int(data["pid"])
            print(f"[verify] lock: port={port} pid={pid_in_lock}")
            break
        except Exception as e:
            print(f"[verify] lock read error: {e}")
    time.sleep(0.5)

if port is None:
    print("[FAIL] never got app.lock within 25s")
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(1)

# 健康检查
healthy = False
deadline = time.time() + 25.0
while time.time() < deadline:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1.0) as r:
            body = r.read().decode().strip()
        if body == "ok":
            healthy = True
            print(f"[verify] HEALTH OK on port {port}")
            break
    except Exception:
        pass
    time.sleep(0.5)

# 杀进程树
try:
    lock_data = json.loads(LOCK.read_text(encoding="utf-8"))
    shell_pid = int(lock_data["pid"])
except Exception:
    shell_pid = proc.pid

print(f"[verify] killing tree pid={shell_pid}")
k = subprocess.run(
    ["taskkill", "/PID", str(shell_pid), "/T", "/F"],
    capture_output=True, text=True,
)
print(f"[verify] taskkill rc={k.returncode} out={k.stdout.strip()} err={k.stderr.strip()}")

# 兜底杀 proc.pid 本身
if proc.pid != shell_pid:
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 检查日志
log_dir = DATA / "logs"
launcher_log = log_dir / "launcher.log"
streamlit_log = log_dir / "streamlit.log"
print(f"[verify] launcher.log exists={launcher_log.exists()} size={launcher_log.stat().st_size if launcher_log.exists() else 0}")
print(f"[verify] streamlit.log exists={streamlit_log.exists()} size={streamlit_log.stat().st_size if streamlit_log.exists() else 0}")

if healthy:
    print("[RESULT] DEV_MODE_HEALTH_OK")
    sys.exit(0)
else:
    print("[RESULT] DEV_MODE_HEALTH_FAIL")
    if streamlit_log.exists():
        tail = streamlit_log.read_text(encoding="utf-8", errors="replace")[-1500:]
        print("--- streamlit.log tail ---")
        print(tail)
    sys.exit(2)

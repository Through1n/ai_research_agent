"""两个 dist 验真脚本的公共部分：找 exe、挑端口、等 HTTP、用临时 APPDATA 拉起进程。

抽出来的原因是这四条知识每个脚本都得用一遍，抄两遍必然会漂：
1. **必须换临时 APPDATA**——真实 `%APPDATA%\\AI4SResearchAgent\\app\\` 存在时，
   冻结态启动会走热更新直接跑旧业务代码，验证结论假绿。
2. **端口要先探活再启动**——上一轮留下的僵尸 exe 还占着端口时，
   现象是「服务没起来」，很难看出真实原因。
3. **HTTP 200 不等于业务能跑**——Streamlit 的脚本跑在 WebSocket 会话里，
   纯 GET 不会执行 app.py，必须真浏览器加载。
4. 冻结态冷启动要 import chromadb/onnxruntime，探活超时给足。

只被 scripts/_verify_dist_*.py 使用；不做通用框架，够用即止。
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "AI4SResearchAgent" / "AI4SResearchAgent.exe"


def require_exe() -> str | None:
    """产物不存在时返回错误说明（由调用方打印并退出）。"""
    if EXE.exists():
        return None
    return f"产物不存在：{EXE}（先跑 build_desktop.bat）"


def _port_in_use(port: int) -> bool:
    """端口是否已被占用。

    注意：这里**不能**给探针 socket 设 SO_REUSEADDR —— Windows 的语义是
    「允许抢绑一个正在被监听的端口」，探测会一路 succeed，等于什么都没查
    （实测：8765 已 listen 时仍返回可用）。所以先真连一次，再裸 bind 一次。
    """
    with socket.socket() as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return True
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
    except OSError:
        return True
    return False


def pick_port(preferred: int, span: int = 8) -> int:
    """从 preferred 开始找一个真正能用的端口，避免僵尸进程占端口导致的假失败。"""
    for port in range(preferred, preferred + span):
        if not _port_in_use(port):
            return port
    raise RuntimeError(f"{preferred}–{preferred + span - 1} 全部被占用，先清理残留进程")


def wait_http(url: str, timeout: int = 180, interval: float = 2.0) -> tuple[bool, str]:
    """等 Streamlit 起来。只证明服务在监听，不代表 app.py 执行过。"""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True, ""
                last = f"HTTP {r.status}"
        except Exception as exc:  # noqa: BLE001 - 探活阶段什么异常都可能
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(interval)
    return False, last


class FrozenApp:
    """上下文管理器：临时 APPDATA 起冻结态 exe，退出时收进程与临时目录。"""

    def __init__(self, port: int):
        self.port = port
        self.tmp_appdata = ""
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "FrozenApp":
        self.tmp_appdata = tempfile.mkdtemp(prefix="ai4s_dist_verify_")
        env = dict(os.environ)
        env["APPDATA"] = self.tmp_appdata          # 模拟全新安装
        env.pop("AI4S_DESKTOP", None)              # 别让宿主环境串进来
        self.proc = subprocess.Popen(
            [str(EXE), "--st-server", str(self.port)],
            env=env, cwd=str(EXE.parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def app_dir(self) -> Path:
        """复制出来的业务代码目录（验证「跑的是新代码」时用）。"""
        return Path(self.tmp_appdata) / "AI4SResearchAgent" / "app"

    def dump_logs(self) -> None:
        logs = Path(self.tmp_appdata) / "AI4SResearchAgent" / "logs"
        if not logs.exists():
            print("      （没有日志目录）")
            return
        for f in sorted(logs.glob("*")):
            print(f"      --- {f.name} ---")
            try:
                print(f.read_text(encoding="utf-8", errors="replace")[-2000:])
            except Exception as exc:  # noqa: BLE001
                print(f"      （读不动：{exc}）")

    def __exit__(self, *exc_info) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.tmp_appdata:
            import shutil
            shutil.rmtree(self.tmp_appdata, ignore_errors=True)

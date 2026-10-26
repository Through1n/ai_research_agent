"""Streamlit 子进程启动器：动态端口、单实例锁、健康等待、进程树清理。

铁律（契约 §4）：
- 一律 subprocess.Popen 启动 Streamlit（禁止 multiprocessing）；
- 锁端口固定 127.0.0.1:47851，业务端口动态空闲端口；
- CREATE_NO_WINDOW 隐藏子进程窗口，stdout/stderr 落 logs/streamlit.log；
- 窗口关闭后用 psutil 杀整棵进程树，taskkill 兜底。
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

from desktop import paths

# ---------------------------------------------------------------- 常量

LOCK_PORT = 47851  # 单实例锁端口（契约 §2 固定值）

MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40

_CREATE_NO_WINDOW = 0x08000000

# Streamlit 命令行参数（显式传递，不依赖 config.toml 兜底）
# 注意：streamlit 1.63 已移除 --browser.openInExternalBrowser；headless=true 已阻止开浏览器。
#
# Round 16 追加两项（与 Dockerfile CMD / start_app.sh 的口径对齐）：
#   enableStaticServing=true —— 写作页的 mermaid.min.js 从 /static 本地加载，
#     该项默认 false。热更新业务包理论上可能不含 .streamlit/config.toml
#     （该文件里有这条设置），命令行显式传 = 不依赖包内配置，离线不退化到 CDN。
#   maxUploadSize=100 —— 与 config.py 的应用层上限 MAX_UPLOAD_MB=100 对齐：
#     应用层在解析前才拦超限文件，而文件此时已整份读进内存；在 streamlit
#     上传层就拦掉更省内存（桌面态同理，理由同 Dockerfile 第 207-221 行注释）。
STREAMLIT_CLI_ARGS = [
    "--server.address=127.0.0.1",
    "--server.headless=true",
    "--server.runOnSave=false",
    "--global.developmentMode=false",
    "--browser.gatherUsageStats=false",
    "--client.toolbarMode=minimal",
    "--server.enableStaticServing=true",
    "--server.maxUploadSize=100",
]

_STREAMLIT_LOG = None  # 子进程 stdout 句柄，保活至清理阶段


# ---------------------------------------------------------------- 小工具

def _log(msg: str) -> None:
    """launcher 自身日志（pythonw 无控制台，异常/进展都落盘）。"""
    try:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with open(paths.logs_dir() / "launcher.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _message_box(text: str, title: str, flags: int) -> None:
    """原生弹窗（windowed 模式下唯一的错误/提示通道）。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        pass


def find_free_port() -> int:
    """bind 127.0.0.1:0 取系统分配的空闲端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _health_ok(port: int, timeout: float = 1.5) -> bool:
    """GET /_stcore/health，返回体 strip() == 'ok' 即健康。"""
    try:
        url = f"http://127.0.0.1:{port}/_stcore/health"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace").strip()
        return body == "ok"
    except Exception:
        return False


# ---------------------------------------------------------------- 单实例

def acquire_single_instance():
    """尝试占用锁端口。

    返回 (lock_socket, existing_port)：
    - 成功：(socket对象, None)，socket 必须保活到进程退出；
    - 已有健康实例：(None, port) 且已弹“程序已在运行”，调用方应退出码 0；
    - 锁被占但不健康（僵尸锁）：(None, None)，允许继续并由写锁阶段重建。
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Windows 下即便 TIME_WAIT 也不影响 listen bind；SO_REUSEADDR 不打开，避免双开
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
        return sock, None
    except OSError:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    # bind 失败 → 认为已有实例，读 app.lock 取业务端口做健康检查
    port = None
    try:
        data = json.loads(paths.lock_file().read_text(encoding="utf-8"))
        port = int(data.get("port"))
    except Exception:
        port = None

    if port and _health_ok(port):
        _message_box(
            "MoreHand 已经在运行。\n\n如需打开已有窗口，请查看任务栏；"
            "若窗口被关闭且无法恢复，请结束任务后重试。",
            "MoreHand",
            MB_ICONINFORMATION,
        )
        _log(f"another healthy instance on port {port}, exiting")
        return None, port

    _log("stale lock detected (lock port busy but no healthy server), taking over")
    return None, None


def write_lock(port: int) -> None:
    """把动态端口与本进程 pid 写入 app.lock（JSON）。"""
    paths.ensure_dirs()
    payload = {"port": port, "pid": os.getpid()}
    paths.lock_file().write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    _log(f"lock written: {payload}")


# ---------------------------------------------------------------- 启动 Streamlit

def launch_streamlit(port: int) -> subprocess.Popen:
    """以 subprocess.Popen 启动 Streamlit 服务子进程，返回 Popen 对象。

    - 冻结态：exe 重新执行自己 --st-server <port>，cwd = data_root/app（热更新业务代码目录）；
    - 开发态：当前解释器跑 -m streamlit run <project_root>/app.py，cwd = project_root。
    """
    global _STREAMLIT_LOG

    cli_args = [f"--server.port={port}"] + STREAMLIT_CLI_ARGS

    if paths.FROZEN:
        # 冻结态：exe 重新执行自己，由 __main__.py 识别 --st-server 进入服务模式；
        # cwd 指向热更新业务目录 app_dir()，使 .streamlit/config.toml、lib/、演示语料/ 相对解析。
        work_dir = paths.app_dir()
        argv = [sys.executable, "--st-server", str(port)]
    else:
        # 开发态：直接跑项目根 app.py（app_bundle_dir 返回 project_root）
        work_dir = paths.project_root()
        argv = [sys.executable, "-m", "streamlit", "run", str(paths.project_root() / "app.py")] + cli_args

    env = os.environ.copy()
    env["AI4S_DESKTOP"] = "1"

    paths.ensure_dirs()
    log_path = paths.logs_dir() / "streamlit.log"
    _STREAMLIT_LOG = open(log_path, "a", encoding="utf-8", errors="replace")

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE

    proc = subprocess.Popen(
        argv,
        cwd=str(work_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=_STREAMLIT_LOG,
        stderr=subprocess.STDOUT,
        creationflags=_CREATE_NO_WINDOW,
        startupinfo=startupinfo,
    )
    _log(f"streamlit child started: pid={proc.pid} port={port} frozen={paths.FROZEN} cwd={work_dir}")
    return proc


def wait_for_server(port: int, timeout: float = 60.0) -> int:
    """每 0.5s 轮询健康检查；超时读日志尾部弹窗报错，返回非 0。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _health_ok(port):
            _log(f"server healthy on port {port}")
            return 0
        time.sleep(0.5)

    tail = ""
    try:
        log_path = paths.logs_dir() / "streamlit.log"
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 2048))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        pass

    _message_box(
        f"Streamlit 服务在 {int(timeout)} 秒内未就绪。\n\n日志尾部：\n{tail[-1500:]}",
        "MoreHand 启动失败",
        MB_ICONERROR,
    )
    _log(f"server wait timeout on port {port}")
    return 1


# ---------------------------------------------------------------- 清理

def shutdown_tree(pid) -> None:
    """杀掉 Streamlit 整棵进程树：psutil terminate→5s 宽限→kill，taskkill 兜底。"""
    if pid is None:
        return

    try:
        import psutil

        root = psutil.Process(pid)
        procs = root.children(recursive=True)
        for child in procs:
            try:
                child.terminate()
            except Exception:
                pass
        try:
            root.terminate()
        except Exception:
            pass

        gone, alive = psutil.wait_procs(procs, timeout=5)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        try:
            if root.is_running():
                root.kill()
        except Exception:
            pass
    except Exception as e:
        _log(f"psutil shutdown error: {e}")

    # 兜底（幂等：进程已死时仅返回错误码，忽略）
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as e:
        _log(f"taskkill fallback error: {e}")

    global _STREAMLIT_LOG
    if _STREAMLIT_LOG is not None:
        try:
            _STREAMLIT_LOG.close()
        except Exception:
            pass
        _STREAMLIT_LOG = None
    _log(f"shutdown done for pid {pid}")


def cleanup_lock(lock_sock) -> None:
    """删除本实例 app.lock（pid 匹配才删）并关闭锁 socket，try/finally 语义由调用方保证。"""
    try:
        lock = paths.lock_file()
        if lock.exists():
            try:
                data = json.loads(lock.read_text(encoding="utf-8"))
                if int(data.get("pid", -1)) == os.getpid():
                    lock.unlink()
                    _log("app.lock removed")
            except Exception:
                pass
    finally:
        if lock_sock is not None:
            try:
                lock_sock.close()
            except Exception:
                pass

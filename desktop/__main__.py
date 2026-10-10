"""桌面壳入口。

两种模式：
1. 桌面模式（默认）：ensure_dirs → 单实例锁 → 起 Streamlit → 健康等待 → 原生窗口；
2. 冻结态服务模式：argv 含 --st-server <port>，exe 子进程内直接跑 Streamlit CLI。
"""

import os
import sys

# 桌面壳自身始终视为桌面模式（开发态运行 -m desktop 时也走 %APPDATA% 数据根），
# 同时保证后续 import 的 paths.DESKTOP 为真、子进程环境自动继承。
os.environ["AI4S_DESKTOP"] = "1"


def _fatal(text: str) -> int:
    """windowed（pythonw）模式下唯一的异常出口：原生弹窗，返回非 0。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "AI4S 科研助手启动失败", 0x10)
    except Exception:
        pass
    return 1


def _service_mode(port: int) -> int:
    """冻结态子进程：直接在本进程内运行 Streamlit CLI。"""
    from desktop import paths
    from desktop.launcher import STREAMLIT_CLI_ARGS

    os.environ["AI4S_DESKTOP"] = "1"
    paths.ensure_dirs()

    from streamlit.web import cli as stcli

    sys.argv = (
        ["streamlit", "run", str(paths.resource_root() / "app.py")]
        + [f"--server.port={port}"]
        + STREAMLIT_CLI_ARGS
    )
    stcli.main()
    return 0


def main() -> int:
    argv = sys.argv[1:]

    if "--st-server" in argv:
        try:
            idx = argv.index("--st-server")
            port = int(argv[idx + 1])
        except (ValueError, IndexError):
            return _fatal("--st-server 参数缺少端口号")
        try:
            return _service_mode(port)
        except Exception as e:
            import traceback
            return _fatal(f"{e}\n\n{traceback.format_exc()}")

    from desktop import launcher, paths, window

    paths.ensure_dirs()
    lock_sock = None
    server_proc = None

    try:
        lock_sock, existing_port = launcher.acquire_single_instance()
        if existing_port is not None:
            return 0  # 已有健康实例，提示框已弹出

        port = launcher.find_free_port()
        launcher.write_lock(port)

        server_proc = launcher.launch_streamlit(port)
        if launcher.wait_for_server(port) != 0:
            return 1

        window.open_window(port)  # 阻塞至窗口关闭
        return 0
    except Exception as e:
        import traceback
        return _fatal(f"{e}\n\n{traceback.format_exc()}")
    finally:
        if server_proc is not None:
            launcher.shutdown_tree(server_proc.pid)
        launcher.cleanup_lock(lock_sock)


if __name__ == "__main__":
    sys.exit(main())

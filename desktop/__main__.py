"""桌面壳入口。

两种模式：
1. 桌面模式（默认）：ensure_dirs → ensure_app_copy → 单实例锁 → 起 Streamlit → 健康等待 → 原生窗口；
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
    """冻结态子进程：直接在本进程内运行 Streamlit CLI，跑热更新业务目录下的 app.py。"""
    from desktop import paths
    from desktop.launcher import STREAMLIT_CLI_ARGS

    os.environ["AI4S_DESKTOP"] = "1"
    paths.ensure_dirs()

    script = paths.app_script()
    if not script.exists():
        # 极端情况：服务子进程启动时业务目录被清空/删除，兜底再 copy 一次
        try:
            paths.ensure_app_copy()
        except Exception:
            pass

    from streamlit.web import cli as stcli

    sys.argv = (
        ["streamlit", "run", str(script)]
        + [f"--server.port={port}"]
        + STREAMLIT_CLI_ARGS
    )
    stcli.main()
    return 0


def _background_update_check() -> None:
    """启动时同步但短时（3s）检查业务更新：有新版且运行时兼容则直接下载应用，下次启动生效。

    网络失败 / 非冻结态 / 无新版本一律静默跳过，绝不阻塞首次启动。
    """
    from desktop import paths
    if not paths.FROZEN:
        return
    try:
        from desktop import updater
        updater.update_on_startup()
    except Exception as e:
        try:
            paths.ensure_dirs()
            import time
            with open(paths.logs_dir() / "updater.log", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] startup update check error: {e}\n")
        except Exception:
            pass


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

    # 热更新层：冻结态首次启动把 app_bundle 复制到 data_root/app
    try:
        paths.ensure_app_copy()
    except Exception as e:
        return _fatal(f"初始化业务代码失败：{e}")

    # 启动时短时检查更新（不阻塞用户界面太久，失败静默）
    _background_update_check()

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

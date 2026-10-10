"""原生窗口（pywebview + WebView2 edgechromium）与顶部菜单。"""

import os
import webbrowser

import webview

from desktop import paths
from desktop.about import ABOUT_TEXT, APP_NAME, HOMEPAGE_URL


def _open_data_dir() -> None:
    os.startfile(str(paths.data_root()))


def _open_logs() -> None:
    log_file = paths.logs_dir() / "streamlit.log"
    if log_file.exists():
        os.startfile(str(log_file))
    else:
        os.startfile(str(paths.logs_dir()))


def _about() -> None:
    """关于对话框。

    说明：pywebview 5.4 的 Window 仅暴露 create_confirmation_dialog /
    create_file_dialog，没有契约中提到的 create_message_dialog；
    为保证纯展示、无 Cancel 歧义，这里用原生 MessageBoxW（MB_ICONINFORMATION）。
    """
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, ABOUT_TEXT, "关于 AI4S 科研助手", 0x40)


def open_window(port: int) -> None:
    """创建 1440x900（最小 1200x720）原生窗口并进入 WebView2 消息循环。"""
    url = f"http://127.0.0.1:{port}"

    window = webview.create_window(
        title=APP_NAME,
        url=url,
        width=1440,
        height=900,
        min_size=(1200, 720),
        icon=paths.resolve_icon(),
    )

    file_menu = webview.Menu(
        "文件",
        [
            webview.MenuAction("打开数据目录", _open_data_dir),
            webview.MenuAction("查看日志", _open_logs),
            webview.MenuAction("退出", window.destroy),
        ],
    )
    help_menu = webview.Menu(
        "帮助",
        [
            webview.MenuAction("项目主页", lambda: webbrowser.open(HOMEPAGE_URL)),
            webview.MenuAction("关于", _about),
        ],
    )

    # winforms 后端遍历 window.menu 列表逐项建顶层菜单
    window.menu = [file_menu, help_menu]

    webview.start(gui="edgechromium")

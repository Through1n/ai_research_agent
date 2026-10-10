"""原生窗口（pywebview + WebView2 edgechromium）与顶部菜单。"""

import os
import webbrowser

import webview
from webview.menu import MenuAction

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


def _check_update() -> None:
    """帮助菜单“检查更新”：短超时查询 GitHub Release，弹窗反馈结果。"""
    import ctypes
    from desktop import updater

    MB_ICONINFORMATION = 0x40
    MB_ICONERROR = 0x10
    MB_ICONWARNING = 0x30

    try:
        meta, reason = updater.needs_update()
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(
            None, f"检查更新时出错：{e}", "AI4S 科研助手", MB_ICONERROR
        )
        return

    if reason == "network_error":
        ctypes.windll.user32.MessageBoxW(
            None,
            "无法连接到更新服务器。\n\n请检查网络后重试，或访问：\n"
            f"{updater.GITHUB_RELEASE_PAGE}",
            "AI4S 科研助手", MB_ICONWARNING,
        )
        return

    if reason == "up_to_date":
        cur = updater.current_business_version()
        ctypes.windll.user32.MessageBoxW(
            None, f"当前已是最新版本（业务版本 {cur}）。",
            "AI4S 科研助手", MB_ICONINFORMATION,
        )
        return

    if reason == "runtime_too_old":
        ctypes.windll.user32.MessageBoxW(
            None,
            f"发现新业务版本 {meta.get('version')}，但需要运行时 "
            f">= {meta.get('min_runtime_version')}（当前壳层 {updater.RUNTIME_VERSION}）。\n\n"
            f"请下载完整安装包升级：\n{updater.GITHUB_RELEASE_PAGE}",
            "AI4S 科研助手", MB_ICONWARNING,
        )
        return

    # update_available：直接下载+应用（下次启动生效）
    url = meta.get("zip_download_url")
    if not url:
        ctypes.windll.user32.MessageBoxW(
            None, f"发现新版本 {meta.get('version')}，但下载地址缺失。\n"
                  f"请手动前往 {updater.GITHUB_RELEASE_PAGE} 下载。",
            "AI4S 科研助手", MB_ICONWARNING,
        )
        return

    updates_dir = paths.data_root() / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    zip_name = meta.get("zip_asset_name") or f"app-{meta.get('version')}.zip"
    dst = updates_dir / zip_name

    ctypes.windll.user32.MessageBoxW(
        None,
        f"发现新版本 {meta.get('version')}，正在下载…\n"
        f"下载与校验完成后将弹出结果，下次启动生效。",
        "AI4S 科研助手", MB_ICONINFORMATION,
    )

    if not updater.download_package(url, dst, timeout=120.0):
        ctypes.windll.user32.MessageBoxW(
            None, "下载失败，请稍后重试。", "AI4S 科研助手", MB_ICONERROR
        )
        return

    ok, msg = updater.apply_update(
        dst,
        expected_sha256=meta.get("zip_sha256", ""),
        min_runtime_version=meta.get("min_runtime_version", "0.0.0"),
    )
    flags = MB_ICONINFORMATION if ok else MB_ICONERROR
    ctypes.windll.user32.MessageBoxW(None, msg, "AI4S 科研助手", flags)


def open_window(port: int) -> None:
    """创建 1440x900（最小 1200x720）原生窗口并进入 WebView2 消息循环。"""
    url = f"http://127.0.0.1:{port}"

    # pywebview 5.4 create_window 不支持 icon 参数；窗口/任务栏图标由 exe 自身决定
    # （PyInstaller spec 已设置 icon=assets/icon.ico），开发模式使用默认图标。
    window = webview.create_window(
        title=APP_NAME,
        url=url,
        width=1440,
        height=900,
        min_size=(1200, 720),
    )

    file_menu = webview.Menu(
        "文件",
        [
            MenuAction("打开数据目录", _open_data_dir),
            MenuAction("查看日志", _open_logs),
            MenuAction("退出", window.destroy),
        ],
    )
    help_menu = webview.Menu(
        "帮助",
        [
            MenuAction("项目主页", lambda: webbrowser.open(HOMEPAGE_URL)),
            MenuAction("检查更新", _check_update),
            MenuAction("关于", _about),
        ],
    )

    # winforms 后端遍历 window.menu 列表逐项建顶层菜单
    window.menu = [file_menu, help_menu]

    # 强制前台显示：某些启动方式（如 SW_HIDE 的 vbs、后台进程）会让宿主窗口
    # 创建后保持 invisible；页面加载后主动 restore + show 把它拉到前台。
    def _bring_to_front() -> None:
        try:
            window.restore()
            window.show()
        except Exception:
            pass

    window.events.loaded += _bring_to_front

    webview.start(gui="edgechromium")

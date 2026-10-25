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
    ctypes.windll.user32.MessageBoxW(None, ABOUT_TEXT, "关于 MoreHand", 0x40)


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
            None, f"检查更新时出错：{e}", "MoreHand", MB_ICONERROR
        )
        return

    if reason == "network_error":
        ctypes.windll.user32.MessageBoxW(
            None,
            "无法连接到更新服务器。\n\n请检查网络后重试，或访问：\n"
            f"{updater.GITHUB_RELEASE_PAGE}",
            "MoreHand", MB_ICONWARNING,
        )
        return

    if reason == "up_to_date":
        cur = updater.current_business_version()
        ctypes.windll.user32.MessageBoxW(
            None, f"当前已是最新版本（业务版本 {cur}）。",
            "MoreHand", MB_ICONINFORMATION,
        )
        return

    if reason == "runtime_too_old":
        ctypes.windll.user32.MessageBoxW(
            None,
            f"发现新业务版本 {meta.get('version')}，但需要运行时 "
            f">= {meta.get('min_runtime_version')}（当前壳层 {updater.RUNTIME_VERSION}）。\n\n"
            f"请下载完整安装包升级：\n{updater.GITHUB_RELEASE_PAGE}",
            "MoreHand", MB_ICONWARNING,
        )
        return

    # update_available：直接下载+应用（下次启动生效）
    url = meta.get("zip_download_url")
    if not url:
        ctypes.windll.user32.MessageBoxW(
            None, f"发现新版本 {meta.get('version')}，但下载地址缺失。\n"
                  f"请手动前往 {updater.GITHUB_RELEASE_PAGE} 下载。",
            "MoreHand", MB_ICONWARNING,
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
        "MoreHand", MB_ICONINFORMATION,
    )

    if not updater.download_package(url, dst, timeout=120.0):
        ctypes.windll.user32.MessageBoxW(
            None, "下载失败，请稍后重试。", "MoreHand", MB_ICONERROR
        )
        return

    ok, msg = updater.apply_update(
        dst,
        expected_sha256=meta.get("zip_sha256", ""),
        min_runtime_version=meta.get("min_runtime_version", "0.0.0"),
    )
    flags = MB_ICONINFORMATION if ok else MB_ICONERROR
    ctypes.windll.user32.MessageBoxW(None, msg, "MoreHand", flags)


def _ensure_webview2_gpu_fallback() -> None:
    """给 WebView2 browser 追加 --disable-gpu（保留外部已设的参数）。

    Round 13 桌面版白屏事故修复。现象与根因（崩溃转储 + Watson 元数据实锤）：
    启动桌面版后窗口出现但页面全白，约 18 秒后进程静默退出；streamlit 侧
    零页面访问，launcher 无任何错误日志。崩溃转储显示 WebView2 browser 进程
    启动 GPU 子进程失败后直接 FATAL 自杀（msedge.dll int 3，退出码
    0x80000003，日志 "GPU process isn't usable. Goodbye."）。已实证诱因：
    游戏全屏独占显卡（Edge 浏览器自身有软件渲染回退，WebView2 嵌入式没有）。
    Streamlit UI 不依赖 GPU，显式禁用 GPU 走 SwiftShader 软件渲染，
    用可忽略的性能代价换"GPU 不可用时不整个应用暴毙"的稳定性。
    """
    existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    flags = existing.split() if existing.strip() else []
    if "--disable-gpu" not in flags:
        flags.append("--disable-gpu")
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(flags)


def open_window(port: int) -> None:
    """创建 1440x900（最小 1200x720）原生窗口并进入 WebView2 消息循环。"""
    url = f"http://127.0.0.1:{port}"

    _ensure_webview2_gpu_fallback()

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

    # 页面加载成功标记：browser 进程崩溃时 webview.start() 会静默返回，
    # loaded 不触发 → 下方据此弹出诊断提示，避免用户只看到窗口一闪即消失。
    page_loaded = {"ok": False}

    def _mark_loaded() -> None:
        page_loaded["ok"] = True

    window.events.loaded += _mark_loaded
    window.events.loaded += _bring_to_front

    # storage_path 持久化 + 关闭无痕模式：
    # 1) pywebview 5.4 在 private_mode=True 且无 storage_path 时用
    #    tempfile.TemporaryDirectory().name 作用户数据目录，该临时对象随即被
    #    GC、目录当场删除，WebView2 行为依赖运行时宽容度（隐患）；
    # 2) 持久目录保留缓存/登录态，且位于 data_root 下可随应用数据一起清理。
    webview.start(
        gui="edgechromium",
        private_mode=False,
        storage_path=str(paths.data_root() / "webview"),
    )

    if not page_loaded["ok"]:
        _report_page_load_failure()


def _report_page_load_failure() -> None:
    """页面从未加载成功（browser 崩溃静默退出）时的原生提示。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            "应用页面加载失败（WebView2 未能渲染页面）。\n\n"
            "常见原因：显卡驱动异常，或显卡被其他程序（如全屏游戏）独占。\n"
            "请尝试：\n"
            "1. 关闭全屏游戏/占显卡的程序后重试；\n"
            "2. 更新显卡驱动；\n"
            "3. 查看日志目录：%APPDATA%\\AI4SResearchAgent\\logs\\",
            "MoreHand",
            0x10,
        )
    except Exception:
        pass

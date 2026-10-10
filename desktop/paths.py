"""路径常量：按冻结态 / 桌面态切换资源根（只读）与可写数据根。

契约（INTERFACE_CONTRACT.md §2/§3）：
- 桌面模式环境变量 AI4S_DESKTOP=1；
- 用户数据根 %APPDATA%\\AI4SResearchAgent\\，含 storage\\(uploads/chroma/pdf)、logs\\、config\\、app\\(热更新业务代码)；
- 图标 assets\\icon.ico（打包后随 bundle 走 resource_root）。

热更新模型（v1.1）：
- 冻结态把业务代码打在 resource_root/app_bundle/（只读，随 exe 分发）；
- 首次启动时 ensure_app_copy() 把 app_bundle 整个复制到 data_root/app/（可写）；
- 之后 Streamlit 一律运行 data_root/app/app.py，业务包更新直接替换该目录，
  无需重新安装 exe（壳层 desktop/* 不参与热更新）。
"""

import os
import shutil
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
DESKTOP = FROZEN or os.environ.get("AI4S_DESKTOP") == "1"


def project_root() -> Path:
    """开发态项目根（desktop/ 的上一级目录）。"""
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """只读资源根：冻结态为 PyInstaller 解包目录，否则项目根。"""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", project_root()))
    return project_root()


def data_root() -> Path:
    """可写数据根：桌面态为 %APPDATA%\\AI4SResearchAgent，否则项目根。"""
    if DESKTOP:
        return Path(os.environ["APPDATA"]) / "AI4SResearchAgent"
    return project_root()


def logs_dir() -> Path:
    """日志目录：桌面态 data_root/logs，开发态兼容旧位置 project_root/storage。"""
    if DESKTOP:
        return data_root() / "logs"
    return project_root() / "storage"


def ensure_dirs() -> None:
    """创建 data_root 下全部可写子目录（幂等）。"""
    root = data_root()
    for sub in ("storage", "storage/uploads", "storage/chroma", "storage/pdf",
                "logs", "config", "app", "updates"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def lock_file() -> Path:
    """单实例锁文件（JSON：{"port": n, "pid": n}）。"""
    return data_root() / "app.lock"


def launcher_state_file() -> Path:
    """热更新状态文件（JSON：{"current_version": "...", "updated_at": "..."}）。"""
    return data_root() / "launcher_state.json"


def resolve_icon():
    """窗口图标路径；不存在时返回 None（不得抛异常）。"""
    icon = resource_root() / "assets" / "icon.ico"
    return str(icon) if icon.exists() else None


# ---------------------------------------------------------------- 热更新业务代码路径

def app_bundle_dir() -> Path:
    """业务代码只读源目录。

    - 冻结态：resource_root/app_bundle/（随 exe 分发的初始业务包）；
    - 开发态：project_root()（直接跑源码，不复制、不热更新）。
    """
    if FROZEN:
        return resource_root() / "app_bundle"
    return project_root()


def app_dir() -> Path:
    """当前运行的业务代码目录（可写，热更新替换目标）。"""
    return data_root() / "app"


def app_script() -> Path:
    """当前运行的 app.py 路径（冻结态指向 app_dir/app.py）。"""
    return app_dir() / "app.py"


def _native_error_box(text: str) -> None:
    """复制失败等致命错误时弹原生框（windowed 模式唯一错误通道）。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "AI4S 科研助手初始化失败", 0x10)
    except Exception:
        pass


def ensure_app_copy() -> None:
    """冻结态首次启动时把 app_bundle 复制到 app_dir。

    - 开发态：直接 return（跑 project_root 源码）；
    - 冻结态且 app_dir/app.py 已存在：视为已初始化，跳过；
    - 否则递归复制（忽略 __pycache__/*.pyc），失败弹原生错误框并抛异常。
    """
    if not FROZEN:
        return
    target = app_dir()
    if (target / "app.py").exists():
        return
    src = app_bundle_dir()
    if not (src / "app.py").exists():
        # 初始 bundle 缺失：打包异常，不静默继续
        _native_error_box(
            f"初始业务包缺失：{src}\\app.py 不存在。\n\n请重新安装本软件。"
        )
        raise FileNotFoundError(f"app_bundle missing: {src / 'app.py'}")

    ensure_dirs()
    try:
        shutil.copytree(
            src,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
        )
    except Exception as e:
        _native_error_box(
            f"初始化业务代码失败：{e}\n\n目标：{target}\n源：{src}"
        )
        raise

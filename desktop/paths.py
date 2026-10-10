"""路径常量：按冻结态 / 桌面态切换资源根（只读）与可写数据根。

契约（INTERFACE_CONTRACT.md §2/§3）：
- 桌面模式环境变量 AI4S_DESKTOP=1；
- 用户数据根 %APPDATA%\\AI4SResearchAgent\\，含 storage\\(uploads/chroma/pdf)、logs\\、config\\；
- 图标 assets\\icon.ico（打包后随 bundle 走 resource_root）。
"""

import os
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
    for sub in ("storage", "storage/uploads", "storage/chroma", "storage/pdf", "logs", "config"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def lock_file() -> Path:
    """单实例锁文件（JSON：{"port": n, "pid": n}）。"""
    return data_root() / "app.lock"


def resolve_icon():
    """窗口图标路径；不存在时返回 None（不得抛异常）。"""
    icon = resource_root() / "assets" / "icon.ico"
    return str(icon) if icon.exists() else None

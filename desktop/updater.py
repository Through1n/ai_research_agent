"""热更新核心：业务代码（app/）的版本检查、下载、校验、应用与回滚。

设计要点：
- 本模块属于壳层（desktop/），打进 exe，**不参与业务热更新**；
- 业务版本由 GitHub Release 上的 version.json 描述，业务包为 app-<version>.zip；
- 启动时 update_on_startup() 短超时（3s）检查一次，有新版且运行时兼容则静默下载应用，
  不重启、不打扰用户，下次启动自然生效；
- 手动“检查更新”由 window.py 帮助菜单触发，弹窗反馈结果；
- 所有操作写 logs/updater.log，网络失败绝不抛异常影响主流程。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from desktop import paths
from desktop.version import RUNTIME_VERSION

GITHUB_RELEASES_API = (
    "https://api.github.com/repos/Through1n/ai_research_agent/releases/latest"
)
GITHUB_RELEASE_PAGE = (
    "https://github.com/Through1n/ai_research_agent/releases/latest"
)

# 业务包版本号的“初始未知值”——首次启动还没写 launcher_state.json 时用它
_UNKNOWN_VERSION = "0.0.0"


# ---------------------------------------------------------------- 日志

def _log_dir() -> Path:
    try:
        paths.ensure_dirs()
    except Exception:
        pass
    return paths.logs_dir()


def log(msg: str) -> None:
    try:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with open(_log_dir() / "updater.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------- 版本号

def compare_version(local: str, remote: str) -> int:
    """语义化版本对比（忽略 v 前缀、忽略预发布后缀的纯数字段）。返回 -1/0/1。"""
    def norm(v: str):
        v = (v or "").strip().lstrip("vV")
        # 去掉预发布/构建后缀
        for sep in ("-", "+", "_"):
            if sep in v:
                v = v.split(sep, 1)[0]
        parts = []
        for seg in v.split("."):
            try:
                parts.append(int(seg))
            except ValueError:
                # 非数字段截断到数字
                num = ""
                for ch in seg:
                    if ch.isdigit():
                        num += ch
                    else:
                        break
                parts.append(int(num) if num else 0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    a, b = norm(local), norm(remote)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def current_business_version() -> str:
    """读 launcher_state.json 的 current_version；没有则返回 0.0.0。"""
    try:
        state = json.loads(paths.launcher_state_file().read_text(encoding="utf-8"))
        v = state.get("current_version")
        if v:
            return str(v)
    except Exception:
        pass
    return _UNKNOWN_VERSION


def _write_state(version: str) -> None:
    try:
        paths.ensure_dirs()
        payload = {
            "current_version": version,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        paths.launcher_state_file().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log(f"write launcher_state.json failed: {e}")


# ---------------------------------------------------------------- GitHub 检查

def _http_get(url: str, timeout: float) -> bytes | None:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AI4SResearchAgent-Updater",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        log(f"HTTP GET failed ({url}): {e}")
        return None


def check_latest(timeout: float = 3.0) -> dict | None:
    """查询 GitHub 最新 Release，解析 version.json 并返回 dict；网络失败返回 None。

    返回字段：version、min_runtime_version、release_notes、zip_sha256、
    zip_asset_name、zip_download_url、published_at。
    """
    body = _http_get(GITHUB_RELEASES_API, timeout=timeout)
    if body is None:
        return None

    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:
        log(f"parse releases api json failed: {e}")
        return None

    assets = data.get("assets", []) or []
    version_json_url = None
    zip_asset_url = None
    zip_asset_name = None

    for a in assets:
        name = a.get("name", "")
        if name == "version.json":
            version_json_url = a.get("browser_download_url")
        elif name.startswith("app-") and name.endswith(".zip"):
            zip_asset_url = a.get("browser_download_url")
            zip_asset_name = name

    if not version_json_url:
        log("release has no version.json asset")
        return None

    vbody = _http_get(version_json_url, timeout=timeout)
    if vbody is None:
        return None

    try:
        meta = json.loads(vbody.decode("utf-8", "replace"))
    except Exception as e:
        log(f"parse version.json failed: {e}")
        return None

    # 校验必备字段
    for k in ("version", "min_runtime_version", "zip_sha256", "zip_asset_name"):
        if k not in meta:
            log(f"version.json missing field: {k}")
            return None

    # 若 version.json 里没给下载 URL，回退到 assets 里的 zip
    if not zip_asset_url:
        zip_asset_url = next(
            (a.get("browser_download_url") for a in assets
             if a.get("name") == meta.get("zip_asset_name")),
            None,
        )

    meta["zip_download_url"] = zip_asset_url
    meta.setdefault("published_at", data.get("published_at", ""))
    log(f"latest remote version: {meta.get('version')} "
        f"(min_runtime={meta.get('min_runtime_version')})")
    return meta


# ---------------------------------------------------------------- 下载 / 校验

def download_package(asset_url: str, dst_path: Path, timeout: float = 60.0) -> bool:
    """urllib 下载 zip 到 dst_path；带进度写日志。失败返回 False。"""
    if not asset_url:
        log("download_package: empty asset_url")
        return False
    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        log(f"downloading {asset_url} -> {dst_path}")
        req = urllib.request.Request(
            asset_url, headers={"User-Agent": "AI4SResearchAgent-Updater"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dst_path, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            last_log = 0.0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if total and now - last_log > 3.0:
                    pct = downloaded * 100 // total
                    log(f"  progress {downloaded}/{total} bytes ({pct}%)")
                    last_log = now
        log(f"download done: {dst_path} ({dst_path.stat().st_size} bytes)")
        return True
    except Exception as e:
        log(f"download_package failed: {e}")
        try:
            if dst_path.exists():
                dst_path.unlink()
        except Exception:
            pass
        return False


def verify_sha256(path: Path, expected: str) -> bool:
    if not expected:
        log("verify_sha256: empty expected digest, skip")
        return False
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        actual = h.hexdigest().lower()
        ok = actual == expected.strip().lower()
        log(f"sha256 verify {path.name}: {'OK' if ok else 'MISMATCH'} "
            f"expected={expected[:16]}... actual={actual[:16]}...")
        return ok
    except Exception as e:
        log(f"verify_sha256 error: {e}")
        return False


# ---------------------------------------------------------------- 应用 / 回滚

def _backup_current_app(old_version: str) -> Path | None:
    """把当前 app_dir 重命名为 app.backup-<old_version>；旧 backup 先删掉。"""
    app = paths.app_dir()
    if not app.exists():
        return None
    backup_name = f"app.backup-{old_version or 'preupdate'}"
    backup = app.parent / backup_name
    try:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        # 同一父目录下 rename，跨盘风险低（都在 %APPDATA% 内）
        os.rename(app, backup)
        log(f"backup current app -> {backup}")
        return backup
    except Exception as e:
        log(f"rename app -> backup failed: {e}")
        return None


def apply_update(zip_path: Path, expected_sha256: str,
                 min_runtime_version: str) -> tuple[bool, str]:
    """应用业务包：校验 → 检查运行时兼容 → 备份旧版 → 解压 → 写 state。

    返回 (success, message)。任何一步失败都尽量把旧版还原回去。
    """
    if not zip_path.exists():
        return False, f"升级包不存在：{zip_path}"

    if not verify_sha256(zip_path, expected_sha256):
        return False, "升级包 SHA256 校验失败，已取消（可能下载不完整）。"

    if compare_version(min_runtime_version, RUNTIME_VERSION) > 0:
        return False, (
            f"该业务包要求运行时 >= {min_runtime_version}，当前运行时为 {RUNTIME_VERSION}。\n"
            f"请下载完整安装包升级壳层。"
        )

    old_version = current_business_version()
    backup = _backup_current_app(old_version)
    if backup is None and paths.app_dir().exists():
        # 备份失败但旧目录还在：直接中止，避免解压污染
        return False, "无法备份当前版本，已取消升级。"

    app = paths.app_dir()
    try:
        app.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(app)
        log(f"extracted {zip_path.name} -> {app}")
    except Exception as e:
        log(f"extract failed: {e}，尝试回滚")
        # 回滚：删掉刚解压的半成品，把 backup 改回来
        try:
            if app.exists():
                shutil.rmtree(app, ignore_errors=True)
            if backup is not None:
                os.rename(backup, app)
        except Exception as e2:
            log(f"rollback after extract failure also failed: {e2}")
        return False, f"解压升级包失败：{e}"

    # 从解压出来的 version.json 读新版本号（若有）；否则用文件名推断
    new_version = None
    try:
        state_file = app / "version.json"
        if state_file.exists():
            meta = json.loads(state_file.read_text(encoding="utf-8"))
            new_version = meta.get("version")
    except Exception:
        pass
    if not new_version:
        # 从 zip 文件名 app-<ver>.zip 推断
        stem = zip_path.stem  # app-1.0.1
        if stem.startswith("app-"):
            new_version = stem[4:]
    if not new_version:
        new_version = "unknown"

    _write_state(new_version)
    log(f"update applied: {old_version} -> {new_version}")
    return True, f"已更新到业务版本 {new_version}，下次启动生效。"


def rollback() -> tuple[bool, str]:
    """如果存在 app.backup-*，删掉当前 app_dir 并把 backup 改回 app_dir。"""
    app = paths.app_dir()
    parent = app.parent
    backups = sorted(parent.glob("app.backup-*"))
    if not backups:
        return False, "没有可回滚的备份版本。"
    backup = backups[-1]  # 取最新一个
    try:
        if app.exists():
            shutil.rmtree(app, ignore_errors=True)
        os.rename(backup, app)
        # 推断版本号并写回 state
        ver = backup.name.replace("app.backup-", "")
        _write_state(ver if ver != "preupdate" else _UNKNOWN_VERSION)
        log(f"rollback to {backup.name}")
        return True, f"已回滚到 {ver}，下次启动生效。"
    except Exception as e:
        log(f"rollback failed: {e}")
        return False, f"回滚失败：{e}"


def needs_update() -> tuple[dict | None, str]:
    """对比本地业务版本与远端最新版。返回 (remote_meta, reason)。

    - 有更新且兼容：(meta, "update_available")
    - 已是最新：(meta, "up_to_date")
    - 网络失败：(None, "network_error")
    - 运行时不兼容：(meta, "runtime_too_old")
    """
    meta = check_latest(timeout=3.0)
    if meta is None:
        return None, "network_error"
    local = current_business_version()
    remote = meta.get("version", "0.0.0")
    if compare_version(local, remote) >= 0:
        return meta, "up_to_date"
    if compare_version(meta.get("min_runtime_version", "0.0.0"), RUNTIME_VERSION) > 0:
        return meta, "runtime_too_old"
    return meta, "update_available"


# ---------------------------------------------------------------- 启动时自动更新

def update_on_startup() -> None:
    """启动时静默检查并应用更新；任何失败都不影响主流程。"""
    try:
        meta, reason = needs_update()
        if reason != "update_available":
            log(f"startup check: reason={reason}")
            return
        url = meta.get("zip_download_url")
        if not url:
            log("startup check: no zip_download_url, skip")
            return
        updates_dir = paths.data_root() / "updates"
        updates_dir.mkdir(parents=True, exist_ok=True)
        zip_name = meta.get("zip_asset_name") or f"app-{meta.get('version')}.zip"
        dst = updates_dir / zip_name
        if not download_package(url, dst, timeout=60.0):
            return
        ok, msg = apply_update(
            dst,
            expected_sha256=meta.get("zip_sha256", ""),
            min_runtime_version=meta.get("min_runtime_version", "0.0.0"),
        )
        log(f"startup apply_update: ok={ok} msg={msg}")
        # 应用成功后保留 zip 便于排障（下次启动若失败可手动回滚）
    except Exception as e:
        log(f"update_on_startup unexpected error: {e}")

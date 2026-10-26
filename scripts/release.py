"""业务代码发版工具：打包 app-<version>.zip、生成 version.json、打 git tag、可选 gh release。

用法：
    python scripts/release.py --version 1.0.1 --notes "修复双栏PDF串读"

产出（dist-release/）：
    - app-<version>.zip   业务代码包（不含壳层 desktop/、tests/、scripts/、storage/ 等）
    - version.json          下发元数据（version / min_runtime_version / zip_sha256 / ...）

行为：
    - 自动 `git tag v<version>`（若不存在），不 push；
    - 检测到 gh CLI 已登录则自动 `gh release create v<version> ...`，否则打印手动上传步骤。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 读取壳层运行时版本（作为业务包 min_runtime_version）
from desktop.version import RUNTIME_VERSION  # noqa: E402

# ---------------------------------------------------------------- 打包清单

# 顶层包含项（文件或目录）
INCLUDE_TOP_ITEMS = [
    "app.py",
    "config.py",
    "requirements.txt",
    ".env.example",
    "ui",
    "services",
    "core",
    "tools",
    "lib",
    "演示语料",
    ".streamlit",
]

# 排除的目录名 / 文件名（递归匹配）
EXCLUDE_NAMES = {
    ".git", "build", "dist", "dist-release", "storage", "version_history",
    "__pycache__", ".idea", ".vscode", ".venv", "node_modules",
    "desktop", "tests", "scripts", "论文样本",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd"}

# 顶层排除文件（不在 INCLUDE_TOP_ITEMS 里的本来就不会进）
EXCLUDE_TOP_FILES = {
    "AI4SResearchAgent.spec", "build_desktop.bat", "installer.iss",
    "README.md", "LICENSE", "docker-compose.yml", "Dockerfile",
    ".gitignore", ".env", "启动桌面版.vbs", "启动科研助手.bat", "启动科研助手.vbs",
}


def _is_excluded(rel_path: Path) -> bool:
    parts = rel_path.parts
    for part in parts[:-1]:  # 目录段
        if part in EXCLUDE_NAMES:
            return True
    name = parts[-1]
    if name in EXCLUDE_NAMES:
        return True
    if any(name.endswith(suf) for suf in EXCLUDE_SUFFIXES):
        return True
    return False


def iter_bundle_files():
    """产出 (absolute_path, arcname) 列表。"""
    files = []
    for item in INCLUDE_TOP_ITEMS:
        src = PROJECT_ROOT / item
        if not src.exists():
            print(f"[warn] include item not found, skip: {item}")
            continue
        if src.is_file():
            if src.name in EXCLUDE_TOP_FILES:
                continue
            files.append((src, src.name))
            continue
        # 目录：递归
        for p in src.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(PROJECT_ROOT)
            if _is_excluded(rel):
                continue
            files.append((p, str(rel).replace(os.sep, "/")))
    return files


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- git

def git(*args) -> subprocess.CompletedProcess:
    git_exe = os.environ.get(
        "AI4S_GIT",
        r"C:\Users\Administrator\AppData\Local\GitHubDesktop\app-3.5.8\resources\app\git\cmd\git.exe",
    )
    return subprocess.run(
        [git_exe, *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def ensure_tag(version: str) -> bool:
    """若 v<version> tag 不存在则创建。返回是否新创建。"""
    tag = f"v{version}"
    r = git("rev-parse", tag)
    if r.returncode == 0:
        print(f"[info] git tag {tag} already exists, skip")
        return False
    r = git("tag", "-a", tag, "-m", f"release {tag}")
    if r.returncode != 0:
        print(f"[warn] git tag failed: {r.stderr.strip()}")
        return False
    print(f"[ok] git tag {tag} created (not pushed)")
    return True


def gh_available() -> bool:
    r = subprocess.run(["where.exe", "gh"], capture_output=True, text=True)
    return r.returncode == 0


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="业务版本号，如 1.0.1")
    ap.add_argument("--notes", default="", help="release notes")
    ap.add_argument("--dry-run", action="store_true", help="只列文件，不打 zip")
    args = ap.parse_args()

    version = args.version.strip().lstrip("vV")
    if not re.match(r"^\d+\.\d+\.\d+", version):
        print(f"[error] version 看起来不像语义化版本：{version}")
        return 2

    out_dir = PROJECT_ROOT / "dist-release"
    out_dir.mkdir(exist_ok=True)
    zip_name = f"app-{version}.zip"
    zip_path = out_dir / zip_name

    files = iter_bundle_files()
    print(f"[info] bundle {len(files)} files")
    for _, arc in files[:20]:
        print(f"       + {arc}")
    if len(files) > 20:
        print(f"       ... ({len(files) - 20} more)")

    # 校验：zip 内不得包含 desktop/、tests/、scripts/、storage/
    bad = [arc for _, arc in files
           if arc.split("/")[0] in {"desktop", "tests", "scripts", "storage"}
           or arc.startswith("storage/")]
    if bad:
        print(f"[error] bundle contains forbidden paths: {bad[:5]}")
        return 3

    if args.dry_run:
        print("[info] dry-run, exit")
        return 0

    # 打 zip
    import zipfile
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for abs_path, arc in files:
            zf.write(abs_path, arc)
    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"[ok] wrote {zip_path} ({size_mb:.2f} MB)")

    digest = sha256_of(zip_path)
    print(f"[ok] sha256={digest}")

    meta = {
        "version": version,
        "min_runtime_version": RUNTIME_VERSION,
        "release_notes": args.notes or f"Release v{version}",
        "zip_sha256": digest,
        "zip_asset_name": zip_name,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    meta_path = out_dir / "version.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] wrote {meta_path}")

    # git tag
    ensure_tag(version)

    # 可选 gh release
    if gh_available():
        print("[info] gh CLI detected, creating release ...")
        r = subprocess.run(
            [
                "gh", "release", f"v{version}",
                str(zip_path), str(meta_path),
                "--notes", args.notes or f"Release v{version}",
                "--title", f"v{version}",
            ],
            cwd=str(PROJECT_ROOT),
        )
        if r.returncode != 0:
            print("[warn] gh release create failed, please upload manually")
    else:
        print("[info] gh CLI not found. Upload manually:")
        print(f"       1. gh release create v{version} \"{zip_path}\" \"{meta_path}\" "
              f"--notes \"{args.notes}\" --title \"v{version}\"")
        print(f"       2. or visit https://github.com/Through1n/ai_research_agent/releases/new")
        print(f"          attach: {zip_path.name}, version.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

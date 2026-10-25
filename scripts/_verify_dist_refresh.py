"""验证「重新打包后，老安装会真的换上新业务代码」——用户说「没什么变化」的真根因。

背景（2026-10-25 实测）：`%APPDATA%\\AI4SResearchAgent\\app\\` 里存着几个月前的
业务代码，老版 `ensure_app_copy()` 只看「app.py 存在吗」，存在就 return——于是重装了
新 exe，跑的还是旧代码，界面看起来一点没变。

修法：打包时往 app_bundle 写 `.build_id`（ISO 时间戳），启动时比较 bundle 与
已安装副本的戳：两边都有戳且 **bundle 更新** 才替换（旧的留 `app.backup-<时间>`）。
任一方没戳（老版本安装 / 热更新包）就保持原样——宁可跑旧代码，也不能把用户
的业务包覆盖成更旧的。

本脚本按 4 种情形验证决策表，另外验「打出来的 bundle 真的带戳且戳是新的」。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop import paths  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIST_BUNDLE = ROOT / "dist" / "AI4SResearchAgent" / "_internal" / "app_bundle"

OLD_ID = "2020-01-01T00:00:00+08:00"
NEW_ID = "2999-01-01T00:00:00+08:00"
# 「刚打出来的新包」：比 OLD_ID 新、比 NEW_ID 旧
BUNDLE_ID = "2026-10-25T20:00:00+08:00"

_failures: list[str] = []


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _bad(msg: str) -> None:
    print(f"  ❌ {msg}")
    _failures.append(msg)


def _seed_bundle(dst: Path, build_id: str | None, marker: str) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "app.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (dst / "ui").mkdir(exist_ok=True)
    (dst / "ui" / "sidebar.py").write_text(f"# {marker}\n", encoding="utf-8")
    if build_id is not None:
        (dst / ".build_id").write_text(build_id, encoding="utf-8")


def _seed_install(dst: Path, build_id: str | None, marker: str) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "app.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (dst / "ui").mkdir(exist_ok=True)
    (dst / "ui" / "sidebar.py").write_text(f"# {marker}\n", encoding="utf-8")
    if build_id is not None:
        (dst / ".build_id").write_text(build_id, encoding="utf-8")


def _run_case(name: str, bundle_id, install_id, expect: str) -> None:
    """expect: 'replace' | 'keep' """
    tmp = Path(tempfile.mkdtemp(prefix="ai4s_refresh_"))
    try:
        bundle = tmp / "app_bundle"
        target = tmp / "app"
        _seed_bundle(bundle, bundle_id, "NEW")
        _seed_install(target, install_id, "OLD")

        # 冻结态仿真：直接替换模块级路径解析函数
        orig_frozen = paths.FROZEN
        orig_bundle = paths.app_bundle_dir
        orig_app = paths.app_dir
        paths.FROZEN = True
        paths.app_bundle_dir = lambda: bundle          # type: ignore[assignment]
        paths.app_dir = lambda: target                # type: ignore[assignment]
        try:
            paths.ensure_app_copy()
        finally:
            paths.FROZEN = orig_frozen
            paths.app_bundle_dir = orig_bundle        # type: ignore[assignment]
            paths.app_dir = orig_app                  # type: ignore[assignment]

        content = (target / "app.py").read_text(encoding="utf-8")
        replaced = "NEW" in content
        backups = [p for p in tmp.iterdir() if p.name.startswith("app.backup-")]
        if expect == "replace":
            if replaced:
                _ok(f"{name}：已换成新业务代码（旧目录备份 {backups[0].name if backups else '无'}）")
            else:
                _bad(f"{name}：应该替换，实际仍是旧代码（{content.strip()}）")
            if not backups:
                _bad(f"{name}：替换后没有留下 app.backup-* 备份")
        else:
            if not replaced:
                _ok(f"{name}：按预期保持原样（没有覆盖用户的业务包）")
            else:
                _bad(f"{name}：不该替换却替换了（{content.strip()}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("[1/6] 决策表：bundle 新 / 已装旧  → 应替换")
    _run_case("老安装 + 新 exe", BUNDLE_ID, OLD_ID, "replace")

    print("[2/6] 决策表：bundle 旧 / 已装新  → 不该替换（不能把用户代码覆盖成更旧）")
    _run_case("热更新包比 exe 新", BUNDLE_ID, NEW_ID, "keep")
    _run_case("已装戳更新", OLD_ID, NEW_ID, "keep")

    print("[3/6] 决策表：已装无戳 = 打戳机制之前的旧安装 → 必须替换")
    _run_case("已装无戳（老版本安装）", BUNDLE_ID, None, "replace")
    print("[4/6] 决策表：bundle 无戳（打包脚本没跑第 4 步）→ 保持原样")
    _run_case("bundle 无戳", None, OLD_ID, "keep")
    _run_case("两边都无戳", None, None, "keep")

    print("[5/6] 全新安装：目标目录不存在 → 应正常复制")
    tmp = Path(tempfile.mkdtemp(prefix="ai4s_fresh_"))
    try:
        bundle = tmp / "app_bundle"
        target = tmp / "app"
        _seed_bundle(bundle, OLD_ID, "NEW")
        orig_frozen, orig_bundle, orig_app = paths.FROZEN, paths.app_bundle_dir, paths.app_dir
        paths.FROZEN = True
        paths.app_bundle_dir = lambda: bundle          # type: ignore[assignment]
        paths.app_dir = lambda: target                # type: ignore[assignment]
        try:
            paths.ensure_app_copy()
        finally:
            paths.FROZEN = orig_frozen
            paths.app_bundle_dir = orig_bundle        # type: ignore[assignment]
            paths.app_dir = orig_app                  # type: ignore[assignment]
        if (target / "app.py").exists() and "NEW" in (target / "app.py").read_text(encoding="utf-8"):
            _ok("全新安装：业务代码已复制到位")
        else:
            _bad("全新安装：业务代码没有复制")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("[6/6] 产物自检：dist 里的 bundle 必须带 .build_id 且戳是新的")
    if not DIST_BUNDLE.exists():
        _bad(f"产物缺失：{DIST_BUNDLE}（先跑 build_desktop.bat）")
    else:
        bid = DIST_BUNDLE / ".build_id"
        if not bid.exists():
            _bad("dist bundle 没有 .build_id —— 打包脚本第 4 步没跑成功")
        else:
            text = bid.read_text(encoding="utf-8").strip()
            print(f"      bundle .build_id = {text}")
            try:
                ts = time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
                age_days = (time.time() - ts) / 86400
                if age_days <= 2:
                    _ok(f"戳是新的（{age_days:.1f} 天内）")
                else:
                    _bad(f"戳太旧了（{age_days:.1f} 天）—— 打包脚本可能没重写")
            except Exception as exc:  # noqa: BLE001
                _bad(f"戳格式无法解析：{exc}")

    print()
    if _failures:
        print(f"RESULT: FAIL —— {len(_failures)} 项不通过")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS —— 老安装会自动换上新业务代码，且不会把更新的用户代码覆盖成更旧的")
    return 0


if __name__ == "__main__":
    sys.exit(main())

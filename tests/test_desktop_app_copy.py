"""`desktop.ensure_app_copy()` 的替换决策表——「重装了却什么都没变」的守门用例。

背景（2026-10-25 实测）：老实现只判断「app_dir/app.py 存在吗」，存在就 return。
于是 exe 里是新业务代码、跑的却是 `%APPDATA%` 里几个月前那份旧代码，
用户看到的界面永远不变。现在改用 `.build_id`（ISO 时间戳）比较新旧：

| bundle 戳 | 已装戳 | 动作 | 理由 |
|---|---|---|---|
| 无 | 任意 | 不动 | 打包脚本没跑第 4 步 = 打包异常，不做没依据的覆盖 |
| 有 | 无 | 替换 | 「已装无戳」只可能是打戳机制之前装的旧版，本次 exe 一定更新 |
| 有 | 有 | 仅当 bundle > 已装才替换 | 保护比 exe 更新的热更新版本（updater 会自己写戳） |

写不成「永远真」的断言：每次都直接跑真函数 + 真目录，断言落在**磁盘状态**上
（业务代码标记 + 备份目录），不是落在 mock 调用次数上。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from desktop import paths

OLD_ID = "2020-01-01T00:00:00+08:00"
NEW_ID = "2999-01-01T00:00:00+08:00"
BUNDLE_ID = "2026-10-25T20:00:00+08:00"   # 比 OLD_ID 新、比 NEW_ID 旧


def _seed(folder: Path, build_id: str | None, marker: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "app.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (folder / "ui").mkdir(exist_ok=True)
    (folder / "ui" / "sidebar.py").write_text(f"# {marker}\n", encoding="utf-8")
    if build_id is not None:
        (folder / paths.BUILD_ID_FILE).write_text(build_id, encoding="utf-8")


class EnsureAppCopyTest(unittest.TestCase):
    """每个用例都是一套真实的临时目录 + 真函数调用。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ai4s_appcopy_"))
        self.bundle = self.tmp / "app_bundle"
        self.target = self.tmp / "app"
        self._orig = (paths.FROZEN, paths.app_bundle_dir, paths.app_dir)

    def tearDown(self):
        paths.FROZEN, paths.app_bundle_dir, paths.app_dir = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, bundle_id, install_id, install_exists=True):
        _seed(self.bundle, bundle_id, "NEW")
        if install_exists:
            _seed(self.target, install_id, "OLD")
        paths.FROZEN = True
        paths.app_bundle_dir = lambda: self.bundle
        paths.app_dir = lambda: self.target
        paths.ensure_app_copy()

    def _marker(self) -> str:
        return (self.target / "app.py").read_text(encoding="utf-8").strip()

    def _backups(self) -> list[Path]:
        return [p for p in self.tmp.iterdir() if p.name.startswith("app.backup-")]

    # ---------------- 应替换 ----------------

    def test_bundle_newer_replaces_old_install(self):
        """bundle 新 / 已装旧 → 换成新代码，并留备份。"""
        self._run(BUNDLE_ID, OLD_ID)
        self.assertIn("NEW", self._marker())
        self.assertTrue(self._backups(), "替换后应留下 app.backup-* 备份")

    def test_legacy_install_without_stamp_is_replaced(self):
        """已装无戳（打戳机制之前的旧安装）→ 必须替换。

        这条是用户「重装了但界面一点没变」那批安装的真正出路：
        它们的 app/ 目录里没有 .build_id。
        """
        self._run(BUNDLE_ID, None)
        self.assertIn("NEW", self._marker())
        self.assertTrue(self._backups())

    # ---------------- 不应替换 ----------------

    def test_bundle_without_stamp_keeps_install(self):
        """bundle 无戳（打包脚本没跑第 4 步）→ 不动用户的业务包。"""
        self._run(None, OLD_ID)
        self.assertIn("OLD", self._marker())
        self.assertFalse(self._backups())

    def test_newer_install_kept(self):
        """已装戳比 bundle 新（热更新过）→ 不动。"""
        self._run(BUNDLE_ID, NEW_ID)
        self.assertIn("OLD", self._marker())
        self.assertFalse(self._backups())

    def test_same_stamp_kept(self):
        """两边戳相同 → 不动（没有更新可换）。"""
        self._run(BUNDLE_ID, BUNDLE_ID)
        self.assertIn("OLD", self._marker())
        self.assertFalse(self._backups())

    def test_install_without_stamp_but_bundle_without_stamp_kept(self):
        """两边都无戳 → 不动（bundle 无戳就不做没依据的覆盖）。"""
        self._run(None, None)
        self.assertIn("OLD", self._marker())
        self.assertFalse(self._backups())

    # ---------------- 全新安装 ----------------

    def test_fresh_install_copies_bundle(self):
        """目标目录不存在 → 正常复制（首次安装路径不能被上面的规则挡住）。"""
        self._run(BUNDLE_ID, None, install_exists=False)
        self.assertIn("NEW", self._marker())
        self.assertTrue((self.target / "ui" / "sidebar.py").exists())
        # 全新安装不产生备份（没有旧目录可备）
        self.assertFalse(self._backups())

    # ---------------- 开发态 ----------------

    def test_dev_mode_never_copies(self):
        """开发态直接 return，永远跑仓库源码。"""
        _seed(self.bundle, BUNDLE_ID, "NEW")
        _seed(self.target, OLD_ID, "OLD")
        paths.FROZEN = False
        paths.app_bundle_dir = lambda: self.bundle
        paths.app_dir = lambda: self.target
        paths.ensure_app_copy()
        self.assertIn("OLD", self._marker())


if __name__ == "__main__":
    unittest.main()

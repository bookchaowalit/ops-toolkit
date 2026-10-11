#!/usr/bin/env python3
"""Tests for the read-only unused-file scanner."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_UTILS = Path(__file__).resolve().parents[2] / "scripts" / "utils"
sys.path.insert(0, str(SCRIPTS_UTILS))

from unused_files import CONTRACT, scan  # noqa: E402


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class UnusedFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _write(self.root, "README.md", "Run `python3 tools/used_tool.py`.\n")
        _write(self.root, "tools/used_tool.py", "import helper_mod\n")
        _write(self.root, "tools/helper_mod.py", "VALUE = 1\n")
        _write(self.root, "tools/orphan_tool.py", "print('orphan_tool')\n")
        _write(self.root, "docs/lonely.md", "nobody links here\n")
        _write(self.root, "data/raw/orphan.csv", "a,b\n")
        _write(self.root, "tools/used_tool_test.py", "pass\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "commit", "-qm", "init"],
            cwd=self.root, check=True,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_reports_only_unreferenced_files(self):
        report = scan(self.root, [])
        paths = {row["path"] for row in report["candidates"]}
        self.assertEqual(report["contract"], CONTRACT)
        self.assertEqual(report["mode"], "report-only")
        self.assertEqual(paths, {"tools/orphan_tool.py", "docs/lonely.md"})

    def test_excludes_data_boundary_tests_and_entrypoints(self):
        paths = {row["path"] for row in scan(self.root, [])["candidates"]}
        self.assertNotIn("data/raw/orphan.csv", paths)
        self.assertNotIn("tools/used_tool_test.py", paths)
        self.assertNotIn("README.md", paths)

    def test_prefix_and_code_filters(self):
        paths = {row["path"] for row in scan(self.root, ["tools/"], kind="code")["candidates"]}
        self.assertEqual(paths, {"tools/orphan_tool.py"})

    def test_scan_never_modifies_the_worktree(self):
        scan(self.root, [])
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout
        self.assertEqual(status, "")


if __name__ == "__main__":
    unittest.main()

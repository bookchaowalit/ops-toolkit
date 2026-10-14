#!/usr/bin/env python3
"""A scheduled writer requires a verified pilot preview, not just exit zero."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("timer_installer", ROOT / "infra/scripts/sync/install_tracker_sync_timer.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class TimerActivationTest(unittest.TestCase):
    def attempt(self, preview):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if "--preflight" in args:
                data = {"status": "ready", "auth_verified": False}
            elif args[0] == "systemd-analyze":
                data = {}
            elif "--json" in args:
                data = preview
            else:
                data = {}
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            output = io.StringIO()
            with patch.object(installer.subprocess, "run", side_effect=run), \
                 patch.object(installer.Path, "home", return_value=home), \
                 patch.object(sys, "argv", ["installer", "--activate"]), \
                 contextlib.redirect_stdout(output):
                code = installer.main()
            enabled = any(args[:4] == ["systemctl", "--user", "enable", "--now"] for args in calls)
            units_written = (home / ".config/systemd/user/tracker-sync-pilot.timer").exists()
            return code, enabled, units_written, json.loads(output.getvalue())

    def test_locked_zero_exit_preview_cannot_enable_timer(self):
        code, enabled, written, report = self.attempt({"schema": "tracker-sync.report.v1", "task_number": 177, "status": "locked"})
        self.assertEqual(code, 2)
        self.assertFalse(enabled)
        self.assertFalse(written)
        self.assertFalse(report["activated"])

    def test_wrong_pilot_zero_exit_preview_cannot_enable_timer(self):
        code, enabled, written, report = self.attempt({"schema": "tracker-sync.report.v1", "task_number": 999, "status": "dry_run"})
        self.assertEqual(code, 2)
        self.assertFalse(enabled)
        self.assertFalse(written)

    def test_verified_preview_allows_mocked_activation(self):
        code, enabled, written, report = self.attempt({"schema": "tracker-sync.report.v1", "task_number": 177, "status": "dry_run"})
        self.assertEqual(code, 0)
        self.assertTrue(enabled)
        self.assertTrue(written)
        self.assertTrue(report["activated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

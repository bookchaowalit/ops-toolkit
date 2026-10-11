#!/usr/bin/env python3
"""Role task declarations remain portable; live checks stay explicit."""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "docs/registries", "solo-architecture checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
from _env import skip_without_knowledge  # noqa: E402

skip_without_knowledge("role_task_guard_test.py")  # needs solo-knowledge; skipped in CI when absent
import role_task_guard as guard  # noqa: E402


class RoleTaskGuardTests(unittest.TestCase):
    def run_guard(self, arguments=(), ci="1"):
        with patch.dict(os.environ, {"SOLO_EMPIRE_CI": ci}), patch.object(
            sys, "argv", ["role_task_guard.py", *arguments]
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            status = guard.main()
        return status, output.getvalue()

    def test_ci_validates_tracked_registries_without_database(self):
        with patch.object(guard, "open_cell_store", side_effect=AssertionError("live cell read")), patch.object(
            guard, "validate_registries", wraps=guard.validate_registries
        ) as validate:
            status, output = self.run_guard()
        self.assertEqual(status, 0, output)
        validate.assert_called_once()
        self.assertIn("SKIP: live", output)

    def test_invalid_registries_fail_in_ci(self):
        with patch.object(guard, "validate_registries", side_effect=ValueError("invalid tracked registry")):
            status, output = self.run_guard()
        self.assertEqual(status, 1)
        self.assertIn("invalid tracked registry", output)

    def test_sync_is_not_replaced_by_static_check(self):
        with patch.object(guard, "sync_role_integrations", return_value=7) as sync:
            status, _ = self.run_guard(("--sync", "--dry-run"))
        self.assertEqual(status, 7)
        self.assertTrue(sync.call_args.args[0].dry_run)

    def test_task_wrappers_forward_explicit_flags(self):
        import yaml
        tasks = yaml.safe_load((ROOT / "infra" / "tasks" / "portfolio.yml").read_text())["tasks"]
        for name in ("role-tasks:check",):
            self.assertIn("{{.CLI_ARGS}}", tasks[name]["cmds"][0])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Offline tests for the central Playwright portfolio orchestrator."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


INFRA_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = INFRA_ROOT / "scripts" / "quality" / "run_playwright_portfolio.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_playwright_portfolio", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PlaywrightPortfolioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def test_discovers_config_and_script_projects_without_node_modules_walk(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "apps" / "first"
            second = root / "apps" / "second"
            dependency = first / "node_modules" / "ignored"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            dependency.mkdir(parents=True)
            (first / "package.json").write_text(
                json.dumps({"name": "first-app", "scripts": {"e2e": "playwright test"}}),
                encoding="utf-8",
            )
            (first / "playwright.config.ts").write_text("export default {};", encoding="utf-8")
            (second / "package.json").write_text(
                json.dumps({"name": "second-app", "scripts": {"test": "vitest"}}),
                encoding="utf-8",
            )
            (dependency / "package.json").write_text(
                json.dumps({"name": "dependency", "scripts": {"e2e": "playwright test"}}),
                encoding="utf-8",
            )
            manifest = {
                "roots": ["apps"],
                "config_names": ["playwright.config.ts"],
                "script_priority": ["e2e"],
            }
            projects = self.mod.discover_projects(manifest, root)
            self.assertEqual([project.id for project in projects], ["first-app"])
            self.assertEqual(projects[0].config, "playwright.config.ts")
            self.assertEqual(projects[0].script, "e2e")

    def test_builds_package_and_direct_cli_commands(self) -> None:
        package_project = self.mod.Project(
            id="web", path="apps/web", package_name="web", script="e2e", config="playwright.config.ts",
            package_manager="npm", local_cli="apps/web/node_modules/.bin/playwright",
        )
        direct_project = self.mod.Project(
            id="config-only", path="apps/config-only", package_name="config-only", script=None,
            config="playwright.config.ts", package_manager="npm",
            local_cli="apps/config-only/node_modules/.bin/playwright",
        )
        self.assertEqual(
            self.mod.build_run_command(package_project),
            ["npm", "run", "e2e", "--", "--reporter=blob,html"],
        )
        self.assertEqual(
            self.mod.build_run_command(direct_project),
            [str(self.mod.REPO_ROOT / "apps/config-only/node_modules/.bin/playwright"), "test", "--reporter=blob,html"],
        )

    def test_builds_interactive_ui_commands(self) -> None:
        package_project = self.mod.Project(
            id="web", path="apps/web", package_name="web", script="e2e", config="playwright.config.ts",
            package_manager="npm", local_cli="apps/web/node_modules/.bin/playwright",
        )
        direct_project = self.mod.Project(
            id="config-only", path="apps/config-only", package_name="config-only", script=None,
            config="playwright.config.ts", package_manager="npm",
            local_cli="apps/config-only/node_modules/.bin/playwright",
        )
        ui_args = ["--ui", "--ui-host=127.0.0.1", "--ui-port=9324"]
        self.assertEqual(
            self.mod.build_ui_command(package_project, "127.0.0.1", 9324),
            ["npm", "run", "e2e", "--", *ui_args],
        )
        self.assertEqual(
            self.mod.build_ui_command(direct_project, "127.0.0.1", 9324),
            [str(self.mod.REPO_ROOT / "apps/config-only/node_modules/.bin/playwright"), "test", *ui_args],
        )

    def test_default_exit_requires_at_least_one_pass_and_strict_fails_skips(self) -> None:
        results = [self.mod.ProjectResult("missing", "apps/missing", "skip", 0, "node_modules missing")]
        self.assertEqual(self.mod.result_exit_code(results, strict=False), 1)
        self.assertEqual(self.mod.result_exit_code(results, strict=True), 1)
        results.append(self.mod.ProjectResult("ready", "apps/ready", "pass", 1.2))
        self.assertEqual(self.mod.result_exit_code(results, strict=False), 0)
        self.assertEqual(self.mod.result_exit_code(results, strict=True), 1)


if __name__ == "__main__":
    unittest.main()

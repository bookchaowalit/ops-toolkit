#!/usr/bin/env python3
"""Tests for the engagement review command."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "utils" / "engagement_review.py"
sys.path.insert(0, str(ROOT / "scripts" / "utils"))  # task_store
sys.path.insert(0, str(ROOT / "tests" / "support"))
from sqlite_task_store import sqlite_opener  # noqa: E402  (also isolates the test from the live Tasks API)


ARCHITECTURE_ROOT = Path(os.environ.get("SOLO_ARCHITECTURE_ROOT") or ROOT.parent.parent / "solo-architecture")
REGISTRY = ARCHITECTURE_ROOT / "docs" / "registries" / "ENGAGEMENT-REGISTRY.yaml"
needs_registry = unittest.skipUnless(REGISTRY.is_file(), "solo-architecture checkout not present (SOLO_ARCHITECTURE_ROOT)")


@needs_registry
class EngagementReviewTest(unittest.TestCase):
    def run_review(
        self,
        *args: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        # The CLI subprocess runs in the guard's portable CI mode: registry integrity is checked and Tasks API
        # task-link validation is skipped, so the test never reaches the live API. Task-link rules are covered
        # in-process below with the SQLite store double.
        env["SOLO_EMPIRE_CI"] = "1"
        env["EMPIRE_CLIENT_CONFIG"] = "/nonexistent"
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_current_registry_has_no_integrity_errors(self) -> None:
        result = self.run_review("--strict", "--as-of", "2026-08-06")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Findings:", result.stdout)
        self.assertIn("ai-doc-assistant", result.stdout)

    def test_json_report_is_machine_readable(self) -> None:
        result = self.run_review("--json", "--as-of", "2026-08-06")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        registry = yaml.safe_load(
            REGISTRY.read_text(encoding="utf-8")
        )
        self.assertEqual(report["engagement_count"], len(registry["engagements"]))
        self.assertEqual(report["error_count"], 0)
        self.assertGreater(report["warning_count"], 0)

    def test_portable_ci_does_not_require_local_task_database(self) -> None:
        result = self.run_review(
            "--strict",
            "--as-of",
            "2026-08-06",
            extra_env={
                "SOLO_EMPIRE_CI": "1",
                "SOLO_EMPIRE_DB_PATH": "/tmp/solo-empire-ci-missing.db",
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CI portability mode", result.stdout)


class EngagementTaskIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        environment = types.ModuleType("_env")
        environment.PROJECT_ROOT = ROOT.parent
        environment.ARCHITECTURE_ROOT = ARCHITECTURE_ROOT
        environment.skip_without_architecture = lambda *_a, **_k: None
        environment.DB_PATH = Path("fixture-only.db")
        spec = importlib.util.spec_from_file_location("engagement_review_fixture", SCRIPT)
        self.review = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"_env": environment}):
            spec.loader.exec_module(self.review)
        self.review.PORTABLE_CI = False

    def report_for_identities(self, external_ids: list[str | None]) -> dict:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, status TEXT, "
            "priority INTEGER, project TEXT, due_date TEXT, external_source TEXT, "
            "external_id TEXT, updated_at TEXT)"
        )
        rows = [
            (
                15 + index, "Fixture task", "todo", 3, "engagements/demo", None,
                self.review.EXTERNAL_SOURCE, external_id, "2026-10-04",
            )
            for index, external_id in enumerate(external_ids)
        ]
        connection.executemany("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        record = {
            "slug": "demo", "status": "active", "task_ids": [row[0] for row in rows],
            "next_action": "Fixture next action", "next_review_on": "2026-10-05",
            "delivery_workspace": "clients/fixture/demo", "risks": [],
        }
        with mock.patch.object(self.review, "open_task_store", lambda _c, automation, **_kw: sqlite_opener(connection, automation)):
            return self.review.review({"engagements": [record]}, dt.date(2026, 10, 4), 7)

    def test_foreign_task_identity_is_rejected_despite_matching_source_and_project(self) -> None:
        report = self.report_for_identities(["engagement:demo-other:next-action"])
        errors = [item for item in report["findings"] if item["severity"] == "error"]
        self.assertTrue(any(item["category"] == "task_identity" and item["task_id"] == 15 for item in errors), errors)

    def test_task_identity_requires_nonempty_engagement_scoped_key(self) -> None:
        for external_id in (None, "engagement:demo:", "engagement:demo: "):
            with self.subTest(external_id=external_id):
                report = self.report_for_identities([external_id])
                errors = [item for item in report["findings"] if item["severity"] == "error"]
                self.assertTrue(any(item["category"] == "task_identity" for item in errors), errors)

    def test_next_action_and_supplemental_engagement_tasks_remain_valid(self) -> None:
        for external_ids in (
            ["engagement:demo:next-action", "engagement:demo:delivery-review"],
            ["engagement:demo:unblock"],
        ):
            with self.subTest(external_ids=external_ids):
                self.assertEqual(self.report_for_identities(external_ids)["error_count"], 0)


if __name__ == "__main__":
    unittest.main()

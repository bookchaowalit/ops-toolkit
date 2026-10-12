#!/usr/bin/env python3
"""Tests for the deduplicated engagement notification bridge."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "utils" / "engagement_notifications.py"
UTILS = ROOT / "scripts" / "utils"
if str(UTILS) not in sys.path:
    sys.path.insert(0, str(UTILS))

SUPPORT = ROOT / "tests" / "support"
sys.path.insert(0, str(SUPPORT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists, requires  # noqa: E402
ENGAGEMENT_REGISTRY = ARCHITECTURE_ROOT / "docs" / "registries" / "ENGAGEMENT-REGISTRY.yaml"

import engagement_notifications as bridge  # noqa: E402
from sqlite_notifications_store import SqliteNotificationsStore  # noqa: E402
from sqlite_task_store import apply_legacy_task_tables  # noqa: E402


class EngagementNotificationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "test.db"
        connection = sqlite3.connect(self.db_path)
        apply_legacy_task_tables(connection)
        connection.commit()
        self.store = SqliteNotificationsStore(connection)
        self.addCleanup(connection.close)

    def run_bridge(self, *args: str) -> subprocess.CompletedProcess[str]:
        # The SQLite doubles stand in for the Tasks and Notifications APIs.
        env = {**os.environ, "SOLO_EMPIRE_DB_PATH": str(self.db_path), "EMPIRE_CLIENT_CONFIG": "/nonexistent"}
        return subprocess.run(
            [sys.executable, str(SUPPORT / "run_with_sqlite_store.py"), str(SCRIPT), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_apply_queues_one_digest_then_deduplicates(self) -> None:
        findings = [{"category": "blocked", "slug": "demo", "message": "blocked", "severity": "warning"}]
        with patch.object(bridge, "open_notifications_store", lambda *_a, **_k: self.store):
            first = bridge.queue_digest(findings, apply=True)
            second = bridge.queue_digest(findings, apply=True)
        self.assertTrue(first["queued"])
        self.assertEqual((first["new_count"], first["deduplicated_count"]), (1, 0))
        self.assertFalse(second["queued"])
        self.assertEqual((second["new_count"], second["deduplicated_count"]), (0, 1))
        entries = self.store.notifications(notification_type="engagement_review")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["metadata"]["fingerprints"], first["fingerprints"])
        self.assertEqual((entries[0]["severity"], entries[0]["channel"]), ("critical", "console"))
        self.assertIsNone(entries[0]["rule_id"])

    def test_unavailable_service_fails_safely(self) -> None:
        report = {"findings": [], "as_of": "2026-10-10", "due_soon_days": 3}
        unavailable = bridge.NotificationsStoreError(503, "unavailable")
        with patch.object(bridge, "review", return_value=report), \
                patch.object(bridge, "load_registry", return_value={}), \
                patch.object(bridge, "open_notifications_store", side_effect=unavailable), \
                patch.object(sys, "argv", ["engagement_notifications.py", "--dry-run"]):
            self.assertEqual(bridge.main(), 1)

    def test_empty_findings_are_not_queued_on_dry_run(self) -> None:
        with patch.object(bridge, "open_notifications_store", lambda *_a, **_k: self.store):
            result = bridge.queue_digest([], apply=False)
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["deduplicated_count"], 0)
        self.assertFalse(result["queued"])
        self.assertEqual(result["fingerprints"], [])

    def test_notifiable_filter_keeps_only_configured_categories(self) -> None:
        report = {
            "findings": [
                {"category": "blocked", "slug": "a", "message": "blocked"},
                {"category": "info_note", "slug": "b", "message": "ignore"},
                {"category": "overdue_task", "slug": "c", "message": "overdue"},
            ]
        }
        findings = bridge.notifiable_findings(report)
        categories = {item["category"] for item in findings}
        self.assertEqual(categories, {"blocked", "overdue_task"})

    @requires(ENGAGEMENT_REGISTRY, "architecture-docs engagement registry")
    def test_cli_dry_run_never_queues(self) -> None:
        result = self.run_bridge("--dry-run", "--json", "--as-of", "2026-08-11")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("new_count", payload["notification"])
        self.assertGreaterEqual(payload["notification"]["new_count"], 0)
        self.assertFalse(payload["notification"]["queued"])


if __name__ == "__main__":
    unittest.main()

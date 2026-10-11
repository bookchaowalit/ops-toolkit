#!/usr/bin/env python3
"""Light readers take habit counts from the habits store and degrade when the API is unavailable."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for sub in ("utils", "workflows"):
    sys.path.insert(0, str(ROOT / "infra" / "scripts" / sub))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))

import system_health  # noqa: E402
import system_status  # noqa: E402
from habits_store import HabitsStoreError  # noqa: E402
from sqlite_habits_store import SqliteHabitsStore  # noqa: E402

DAY = date.today().isoformat()
WEEKDAY = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[date.today().weekday()]


def store() -> SqliteHabitsStore:
    conn = sqlite3.connect(":memory:")
    habits = SqliteHabitsStore(conn)
    conn.executemany("INSERT INTO habits (id, name, slug, status, schedule_days) VALUES (?,?,?,?,?)", [
        (1, "A", "a", "active", None), (2, "B", "b", "active", f'["{WEEKDAY}"]'),
        (3, "C", "c", "paused", None), (4, "D", "d", "active", '["zzz"]'),
        (5, "E", "e", "active", None)])
    conn.executemany("INSERT INTO habit_logs (habit_id, log_date, completed) VALUES (?,?,?)",
                     [(1, DAY, 1), (2, DAY, 0)])
    conn.commit()
    return habits


def failing(*_a, **_k):
    raise HabitsStoreError(503, "unavailable:test")


class HabitsReadersTests(unittest.TestCase):
    def test_summaries_count_active_and_completed_today(self):
        for module, function in ((system_status, system_status.habit_summary),
                                 (system_health, system_health.collect_habits)):
            with self.subTest(module=module.__name__), patch.object(module, "open_habits_store", return_value=store()) as opener:
                self.assertEqual(function(), {"active": 4, "logged_today": 1})
                opener.assert_called_with("reporting-reader")

    def test_summaries_degrade_when_api_unavailable(self):
        for module, function in ((system_status, system_status.habit_summary),
                                 (system_health, system_health.collect_habits)):
            with self.subTest(module=module.__name__), patch.object(module, "open_habits_store", side_effect=failing):
                self.assertTrue(function()["unavailable"])


if __name__ == "__main__":
    unittest.main()

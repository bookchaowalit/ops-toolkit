#!/usr/bin/env python3
"""The weekly life check-in writes through the habits client and never guesses missing answers."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "support"))
sys.path.insert(0, str(ROOT / "scripts" / "utils"))
os.environ["EMPIRE_CLIENT_CONFIG"] = "/nonexistent/solo-empire-test/client.env"

from sqlite_habits_store import SqliteHabitsStore  # noqa: E402


def load():
    spec = importlib.util.spec_from_file_location("life_checkin", ROOT / "scripts" / "cli" / "life-checkin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOG_TEXT = "# Log\n\n## Rule effectiveness\n\ntext\n\n## ลำดับถัดไป\n\n1. next\n"


class LifeCheckinTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load()
        self.store = SqliteHabitsStore(sqlite3.connect(":memory:"))
        self.mod.open_habits_store = lambda *_a, **_k: self.store
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "log.md"
        self.log.write_text(LOG_TEXT, encoding="utf-8")
        self.mod.VALIDATION_LOG = self.log

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.mod.main(argv)
        return code, out.getvalue()

    def test_stores_counts_and_mood_in_the_habits_cell(self):
        code, _ = self.run_main(["--date", "2026-10-18", "--no-input", "--mood", "4", "--sleep", "8",
                                 "--exercise", "3", "--rest-days", "0", "--contacts", "5", "--offers", "1"])
        self.assertEqual(code, 0)
        exercise = self.store.habit_by_slug("life-exercise")
        self.assertEqual(exercise["frequency"], "weekly")
        logs = self.store.habit_logs(habit_id=exercise["id"])
        self.assertEqual([(log["log_date"], log["count"], log["completed"]) for log in logs],
                         [("2026-10-18", 3, True)])
        rest = self.store.habit_logs(habit_id=self.store.habit_by_slug("life-rest-day")["id"])
        self.assertEqual((rest[0]["count"], rest[0]["completed"]), (0, False))
        routine = self.store.routine_by_slug("life-weekly-checkin")
        mood = self.store.routine_logs(routine_id=routine["id"])
        self.assertEqual(mood[0]["mood_after"], 4)
        self.assertIn("sleep_hours_avg=8", mood[0]["notes"])

    def test_blank_answers_stay_unknown_and_store_nothing(self):
        code, out = self.run_main(["--date", "2026-10-18", "--no-input", "--exercise", "2"])
        self.assertEqual(code, 0)
        self.assertIn("life-rest-day: unknown (not stored)", out)
        self.assertEqual(self.store.habit_logs(habit_id=self.store.habit_by_slug("life-rest-day")["id"]), [])
        self.assertIn("| 2026-10-18 | unknown | unknown | 2 | unknown | unknown | unknown |  |",
                      self.log.read_text(encoding="utf-8"))

    def test_rerun_is_idempotent_and_replaces_the_row(self):
        self.run_main(["--date", "2026-10-18", "--no-input", "--mood", "3", "--exercise", "1"])
        self.run_main(["--date", "2026-10-18", "--no-input", "--mood", "4", "--exercise", "2"])
        self.assertEqual(len([h for h in self.store.habits() if h["slug"] == "life-exercise"]), 1)
        text = self.log.read_text(encoding="utf-8")
        self.assertEqual(text.count("| 2026-10-18 |"), 1)
        self.assertIn("| 2026-10-18 | 4 |", text)
        self.assertLess(text.index("## Weekly check-ins"), text.index("## ลำดับถัดไป"))

    def test_invalid_mood_is_refused_before_any_write(self):
        code, _ = self.run_main(["--date", "2026-10-18", "--no-input", "--mood", "9"])
        self.assertEqual(code, 2)
        self.assertEqual(self.store.habits(), [])
        self.assertEqual(self.log.read_text(encoding="utf-8"), LOG_TEXT)

    def test_dry_run_writes_nothing(self):
        code, out = self.run_main(["--date", "2026-10-18", "--no-input", "--mood", "3", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("nothing stored", out)
        self.assertEqual(self.store.habits(), [])
        self.assertEqual(self.log.read_text(encoding="utf-8"), LOG_TEXT)


if __name__ == "__main__":
    unittest.main()

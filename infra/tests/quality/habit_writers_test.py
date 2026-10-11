#!/usr/bin/env python3
"""Habit writers talk to the habits client only (no SQL on the retired tables)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Google OAuth settings are read at import time by some scripts; never depend on a private infra/.env.
for _name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
    os.environ.setdefault(_name, "offline-test-placeholder")
sys.path.insert(0, str(ROOT / "tests" / "support"))
sys.path.insert(0, str(ROOT / "scripts" / "utils"))
os.environ["EMPIRE_CLIENT_CONFIG"] = "/nonexistent/solo-empire-test/client.env"

from sqlite_habits_store import SqliteHabitsStore  # noqa: E402


def load(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def iso(offset: int = 0) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


class HabitWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SqliteHabitsStore(sqlite3.connect(":memory:"))
        every_day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        self.habit = self.store.create_habit({"name": "Morning Exercise", "slug": "morning-exercise",
                                              "category": "health", "frequency": "daily",
                                              "schedule_days": every_day, "status": "active"})
        self.opener = lambda *_a, **_k: self.store

    def run_main(self, module, argv):
        module.open_habits_store = self.opener
        old = sys.argv
        sys.argv = argv
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                module.main()
        finally:
            sys.argv = old
        return out.getvalue()

    def test_tracker_done_add_and_idempotent(self) -> None:
        tracker = load("cli/habit-tracker.py", "habit_tracker")
        out = self.run_main(tracker, ["x", "done", str(self.habit["id"]), "-d", "30", "-n", "run"])
        self.assertIn("🔥1", out)
        logs = self.store.habit_logs(habit_id=self.habit["id"])
        self.assertEqual([(l["log_date"], l["duration_minutes"], l["notes"]) for l in logs], [(iso(), 30, "run")])
        self.assertEqual(self.store.habit(self.habit["id"])["current_streak"], 1)
        again = self.run_main(tracker, ["x", "done", str(self.habit["id"])])
        self.assertIn("already logged today", again)
        self.assertEqual(len(self.store.habit_logs()), 1)
        self.assertIn("Habit #999 not found", self.run_main(tracker, ["x", "done", "999"]))
        self.run_main(tracker, ["x", "add", "Meditate", "-c", "mindfulness", "-t", "10"])
        added = self.store.habit_by_slug("meditate")
        self.assertEqual((added["target_duration_minutes"], len(added["schedule_days"])), (10, 7))
        for cmd in (None, "list", "streaks", "week"):
            self.run_main(tracker, ["x"] + ([cmd] if cmd else []))

    def test_weekly_autogrant_dry_run_and_idempotent(self) -> None:
        weekly = load("utils/weekly_habit_autogranted.py", "weekly_autogrant")
        self.run_main(weekly, ["x", "--dry-run", "--days", "3"])
        self.assertEqual(self.store.habit_logs(), [])
        self.run_main(weekly, ["x", "--days", "3"])
        self.assertEqual([l["log_date"] for l in self.store.habit_logs()], [iso(-3), iso(-2), iso(-1)])
        self.run_main(weekly, ["x", "--days", "3"])
        self.assertEqual(len(self.store.habit_logs()), 3)
        self.assertEqual(self.store.habit(self.habit["id"])["current_streak"], 3)

    def test_auto_detect_logs_once(self) -> None:
        detect = load("utils/habit_auto_detect.py", "habit_auto_detect")
        self.assertEqual(detect.log_habit("morning-exercise", store=self.store), (True, "logged"))
        self.assertEqual(detect.log_habit("morning-exercise", store=self.store), (False, "already_logged"))
        self.assertEqual(detect.log_habit("nope", store=self.store), (False, "habit_not_found"))

    def test_strava_sync_skips_existing_and_counts_prefix(self) -> None:
        strava = load("sync/sync_strava.py", "sync_strava")
        acts = [{"start_date_local": f"{iso()}T06:00:00Z", "moving_time": 1800, "distance": 5000, "type": "Run"},
                {"start_date_local": f"{iso(-1)}T06:00:00Z", "moving_time": 600, "distance": 1000, "type": "Walk"}]
        self.store.log_habit(self.habit["id"], iso(-1), {"completed": True, "notes": "manual"})
        self.assertEqual(strava.sync_habit_logs(self.store, acts), 1)
        self.assertEqual(strava.sync_habit_logs(self.store, acts), 0)
        logs = self.store.habit_logs(notes_prefix="Strava:")
        self.assertEqual([(l["log_date"], l["duration_minutes"]) for l in logs], [(iso(), 30)])
        self.assertEqual(self.store.habit(self.habit["id"])["current_streak"], 2)

    def test_routines_done_once(self) -> None:
        routines = load("cli/routines-cli.py", "routines_cli")
        routine = self.store.create_routine({"name": "AM", "slug": "am", "routine_type": "daily_morning",
                                             "status": "active", "total_duration_minutes": 20,
                                             "steps": [{"order": 1, "title": "Water"}]})
        self.assertIn("completed for today", self.run_main(routines, ["x", "done", str(routine["id"])]))
        self.assertIn("already logged", self.run_main(routines, ["x", "start", str(routine["id"])]))
        logs = self.store.routine_logs(routine_id=routine["id"])
        self.assertEqual((len(logs), logs[0]["completed_steps"], logs[0]["duration_minutes"]), (1, [1], 20))
        for args in ([], ["view", str(routine["id"])], ["history"], ["streaks"]):
            self.assertNotIn("❌", self.run_main(routines, ["x", *args]))
        self.assertIn("1 day streak", self.run_main(routines, ["x", "streaks"]))


if __name__ == "__main__":
    unittest.main()

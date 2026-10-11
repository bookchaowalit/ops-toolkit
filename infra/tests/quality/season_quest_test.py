#!/usr/bin/env python3
"""Regression tests for the Guild OS Quest Marker."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "utils"))

import season_quest  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
from sqlite_task_store import use_connection, uses_sqlite_store  # noqa: E402

TASKS_SCHEMA = (REPO_ROOT / 'tests' / 'support' / 'sqlite_tasks_schema.sql')

BOARD = """# Season Quest Board: 2026-Q4

| Field | Value |
|---|---|
| Season / window | 2026-Q4 · 2026-10-07 → 2026-12-31 |
| Goal brief / goal ID | [Brief](../brief.md) · `goals.id` 73 (created) |

## Main Quest and Boss Fight

- **Main Quest:** Receive the first payment
  from a payer outside Thailand.
- **Boss Fight:** received USD ≥ 1 by
  **2026-11-06**.
- **Hypothesis:** ignored.
"""


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        TASKS_SCHEMA.read_text(encoding="utf-8")
        + """INSERT INTO tasks (id, title, status, due_date, archived_at) VALUES
               (86, 'Log 5 cases', 'todo', '2026-10-08', NULL),
               (87, 'Shortlist', 'todo', '2026-10-11', NULL),
               (85, 'Done one', 'done', '2026-10-01', NULL),
               (90, 'Other goal', 'todo', '2026-10-02', NULL);
           INSERT INTO task_goal_links (task_id, goal_id) VALUES (86, 73), (87, 73), (85, 73), (90, 1);"""
    )
    return conn


class SeasonQuestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "2026-Q4-quest-board.md").write_text(BOARD, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_parse_board(self) -> None:
        board = season_quest.parse_board(self.dir / "2026-Q4-quest-board.md")
        self.assertEqual(board.season, "2026-Q4")
        self.assertEqual(board.goal_id, 73)
        self.assertEqual(board.main_quest, "Receive the first payment from a payer outside Thailand.")
        self.assertEqual(board.boss_fight, "received USD ≥ 1 by 2026-11-06.")

    @uses_sqlite_store(season_quest)
    def test_marker_lists_open_linked_tasks_and_flags_overdue(self) -> None:
        use_connection(make_db())
        self.addCleanup(use_connection, None)
        text = season_quest.render_marker(date(2026, 10, 9), self.dir)
        self.assertIn("**Season:** 2026-Q4 (83 days left)", text)
        self.assertIn("- [ ] #86 Log 5 cases (due 2026-10-08 ⚠ overdue)", text)
        self.assertIn("- [ ] #87 Shortlist (due 2026-10-11)", text)
        self.assertNotIn("#85", text)
        self.assertNotIn("#90", text)
        self.assertLess(text.index("#86"), text.index("#87"))

    def test_no_active_board_outside_window(self) -> None:
        text = season_quest.render_marker(date(2027, 1, 5), self.dir)
        self.assertIn("No active Season Quest Board", text)


if __name__ == "__main__":
    unittest.main()

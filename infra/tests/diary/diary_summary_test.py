#!/usr/bin/env python3
"""Regression tests for the privacy-safe weekly/monthly diary summary."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / "scripts" / "workflows"
sys.path.insert(0, str(WORKFLOWS_DIR))
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))

from sqlite_task_store import sqlite_opener  # noqa: E402  (the Tasks API stand-in; also isolates the live API)

import daily_diary  # noqa: E402
import diary_summary  # noqa: E402
from daily_diary import record_conversation_summary  # noqa: E402
from diary_summary import (  # noqa: E402
    build_diary_summary,
    compute_window,
    render_diary_summary,
    write_diary_summary,
)


class DiarySummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY, title TEXT, project TEXT, category TEXT,
                priority INTEGER, status TEXT, completed_at TEXT, created_at TEXT,
                due_date TEXT, archived_at TEXT
            );
            """
        )
        # Monday 2026-08-17 .. Sunday 2026-08-23 is the target ISO week.
        self.conn.execute(
            """
            INSERT INTO tasks VALUES
                (1, 'ปิดงานที่หนึ่ง', 'Solo Empire', 'infra', 1, 'done',
                 '2026-08-17 10:00:00', '2026-08-16 10:00:00', NULL, NULL),
                (2, 'ปิดงานที่สอง', 'Solo Empire', 'infra', 2, 'done',
                 '2026-08-19 09:00:00', '2026-08-16 10:00:00', NULL, NULL),
                (3, 'งานใหม่', 'Solo Empire', 'infra', 2, 'todo',
                 NULL, '2026-08-18 11:00:00', NULL, NULL),
                (4, 'งานนอกสัปดาห์', 'Solo Empire', 'infra', 2, 'done',
                 '2026-08-25 09:00:00', '2026-08-16 10:00:00', NULL, NULL)
            """
        )
        self.conn.commit()

        for module in (daily_diary, diary_summary):
            patcher = patch.object(module, "open_task_store", lambda _c, automation, **_kw: sqlite_opener(self.conn, automation))
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.conn.close()

    def test_compute_window_weekly_is_monday_to_sunday(self) -> None:
        start, end = compute_window("weekly", "2026-08-19")
        self.assertEqual(start, date(2026, 8, 17))
        self.assertEqual(end, date(2026, 8, 23))

    def test_compute_window_monthly_is_calendar_month(self) -> None:
        start, end = compute_window("monthly", "2026-08-19")
        self.assertEqual(start, date(2026, 8, 1))
        self.assertEqual(end, date(2026, 8, 31))

    def test_compute_window_rejects_unknown_cadence(self) -> None:
        with self.assertRaises(ValueError):
            compute_window("daily", "2026-08-19")

    def test_build_is_bounded_to_window_and_has_no_ai_conversation_source(self) -> None:
        payload = build_diary_summary(cadence="weekly", as_of="2026-08-19", repo_root=tempfile.gettempdir()
        )
        rendered = render_diary_summary(payload)

        self.assertEqual(payload["label"], "2026-W34")
        self.assertEqual(payload["overview"]["completed_tasks"], 2)
        self.assertEqual(payload["overview"]["created_tasks"], 1)
        self.assertNotIn("ai_conversation", str(payload))
        # The task completed on 2026-08-25 falls outside this ISO week.
        self.assertNotIn("งานนอกสัปดาห์", rendered)

    def test_conversation_summaries_are_merged_across_the_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_conversation_summary(
                day="2026-08-17",
                topic="weekly summary contract",
                question_summary="ผู้ใช้อยากได้สรุปรายสัปดาห์",
                outcome_summary="เพิ่ม diary_summary.py ที่รวมข้อมูลทั้งสัปดาห์",
                entries_root=temp_dir,
            )
            record_conversation_summary(
                day="2026-08-20",
                topic="monthly summary contract",
                question_summary="ผู้ใช้อยากได้สรุปรายเดือนด้วย",
                outcome_summary="รองรับ cadence monthly ในสคริปต์เดียวกัน",
                entries_root=temp_dir,
            )
            payload = build_diary_summary(
                cadence="weekly",
                as_of="2026-08-19",
                repo_root=tempfile.gettempdir(),
                entries_root=temp_dir,
            )
            self.assertEqual(payload["overview"]["conversation_summaries"], 2)

    def test_write_is_idempotent_and_partitioned_by_cadence_and_label(self) -> None:
        payload = build_diary_summary(cadence="monthly", as_of="2026-08-19", repo_root=tempfile.gettempdir()
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            first = write_diary_summary(payload, temp_dir)
            second = write_diary_summary(payload, temp_dir)
            self.assertEqual(first, second)
            markdown_path = Path(first["markdown"])
            self.assertEqual(markdown_path.relative_to(temp_dir).as_posix(), "monthly/2026-08.md")
            self.assertTrue(Path(first["json"]).is_file())


if __name__ == "__main__":
    unittest.main()

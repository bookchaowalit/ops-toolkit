#!/usr/bin/env python3
"""Regression tests for the privacy-safe daily activity diary."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / "scripts" / "workflows"
sys.path.insert(0, str(WORKFLOWS_DIR))
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists, requires  # noqa: E402
ROLE_CLASSIFICATIONS = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-CLASSIFICATIONS.yaml"

from sqlite_task_store import sqlite_opener  # noqa: E402  (the Tasks API stand-in; also isolates the live API)

import daily_diary  # noqa: E402
from daily_diary import (  # noqa: E402
    build_daily_diary,
    record_conversation_summary,
    render_daily_diary,
    write_daily_diary,
)


class DailyDiaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY, title TEXT, project TEXT, category TEXT,
                priority INTEGER, status TEXT, completed_at TEXT, created_at TEXT,
                due_date TEXT, archived_at TEXT
            );
            """
        )
        self.conn.execute("INSERT INTO projects VALUES (1, 'Solo Empire')")
        self.conn.execute(
            """
            INSERT INTO tasks VALUES
                (1, 'ปิดงานรายวัน', 'Solo Empire', 'infra', 1, 'done',
                 '2026-08-11 10:00:00', '2026-08-10 10:00:00', NULL, NULL),
                (2, 'งานใหม่', 'Solo Empire', 'infra', 2, 'todo',
                 NULL, '2026-08-11 11:00:00', NULL, NULL)
            """
        )
        self.conn.commit()

        for module in (daily_diary,):
            patcher = patch.object(module, "open_task_store", lambda _c, automation, **_kw: sqlite_opener(self.conn, automation))
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.conn.close()

    def test_build_is_bounded_and_has_no_ai_conversation_source(self) -> None:
        payload = build_daily_diary(as_of="2026-08-11", repo_root=tempfile.gettempdir())
        rendered = render_daily_diary(payload)

        self.assertEqual(payload["overview"]["completed_tasks"], 1)
        self.assertEqual(payload["overview"]["created_tasks"], 1)
        self.assertNotIn("ai_conversation", str(payload))

    def test_write_is_date_partitioned_and_idempotent(self) -> None:
        payload = build_daily_diary(as_of="2026-08-11", repo_root=tempfile.gettempdir())
        with tempfile.TemporaryDirectory() as temp_dir:
            first = write_daily_diary(payload, temp_dir)
            first_bytes = first.read_bytes()
            second = write_daily_diary(payload, temp_dir)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, second.read_bytes())
            self.assertEqual(first.parent.relative_to(temp_dir).as_posix(), "2026/08/11")
            self.assertEqual(first.name, "auto-daily.md")

    def test_conversation_summary_updates_daily_note_without_raw_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = record_conversation_summary(
                day="2026-08-11",
                topic="daily diary contract",
                question_summary="กำหนดให้ทุกบทสนทนาอัปเดตไฟล์ประจำวัน",
                outcome_summary="เพิ่มคำสั่งบันทึก summary และกติกาในเอกสาร agent",
                next_step="ใช้คำสั่งนี้ก่อนจบทุก turn",
                entries_root=temp_dir,
            )
            duplicate = record_conversation_summary(
                day="2026-08-11",
                topic="daily diary contract",
                question_summary="กำหนดให้ทุกบทสนทนาอัปเดตไฟล์ประจำวัน",
                outcome_summary="เพิ่มคำสั่งบันทึก summary และกติกาในเอกสาร agent",
                next_step="ใช้คำสั่งนี้ก่อนจบทุก turn",
                entries_root=temp_dir,
            )
            payload = build_daily_diary(
                as_of="2026-08-11",
                repo_root=tempfile.gettempdir(),
                entries_root=temp_dir,
            )
            rendered = render_daily_diary(payload)

            self.assertEqual(first["status"], "recorded")
            self.assertEqual(duplicate["status"], "already_recorded")
            self.assertEqual(payload["overview"]["conversation_summaries"], 1)
            self.assertIn("กำหนดให้ทุกบทสนทนาอัปเดตไฟล์ประจำวัน", rendered)
            self.assertIn("เพิ่มคำสั่งบันทึก summary", rendered)


    @requires(ROLE_CLASSIFICATIONS, "solo-architecture role classifications")
    def test_conversation_summary_records_universal_functions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = dict(
                day="2026-08-11",
                topic="function tagging",
                question_summary="ติด function ให้รอบคุย",
                outcome_summary="บันทึก function ของงานรอบนี้",
                entries_root=temp_dir,
            )
            untagged = record_conversation_summary(**base)
            tagged = record_conversation_summary(**base, functions="build-creation, function:governance-assurance")
            with self.assertRaises(ValueError):
                record_conversation_summary(**base, functions="management-coordination")
            payload = build_daily_diary(as_of="2026-08-11", repo_root=tempfile.gettempdir(), entries_root=temp_dir,
            )
            rendered = render_daily_diary(payload)

            self.assertEqual(untagged["id"], tagged["id"])
            self.assertEqual(tagged["status"], "recorded")
            self.assertEqual(tagged["summary"]["functions"], ["build-creation", "governance-assurance"])
            self.assertEqual(payload["overview"]["conversation_summaries"], 1)
            self.assertIn("[function: build-creation, governance-assurance]", rendered)


if __name__ == "__main__":
    unittest.main()

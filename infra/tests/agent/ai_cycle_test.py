#!/usr/bin/env python3
"""Focused tests for the weekly planning cycle projection and KPI math."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_UTILS = Path(__file__).resolve().parents[2] / "scripts" / "utils"
sys.path.insert(0, str(SCRIPTS_UTILS))

import ai_cycle  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from sqlite_task_store import sqlite_opener  # noqa: E402
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402  (also isolates the cell API client switches)
from ai_cycle import command_check, report_payload, snapshot_cycle, task_summary  # noqa: E402


class AiCycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY, title TEXT, status TEXT, priority INTEGER,
                project TEXT, progress_percent INTEGER, estimated_minutes INTEGER,
                actual_minutes INTEGER, due_date TEXT
            );
            CREATE TABLE operating_cycles (
                id INTEGER PRIMARY KEY, slug TEXT, name TEXT, cycle_type TEXT,
                status TEXT, start_date TEXT, end_date TEXT, focus TEXT,
                capacity_minutes INTEGER, owner TEXT, decision TEXT
            );
            CREATE TABLE operating_cycle_goal_links (
                id INTEGER PRIMARY KEY, cycle_id INTEGER, goal_id INTEGER, link_type TEXT
            );
            CREATE TABLE operating_cycle_tasks (
                id INTEGER PRIMARY KEY, cycle_id INTEGER, task_id INTEGER, commitment_type TEXT,
                estimate_minutes INTEGER, evidence_required INTEGER
            );
            CREATE TABLE operating_kpi_definitions (
                id INTEGER PRIMARY KEY, slug TEXT, name TEXT, description TEXT,
                unit TEXT, direction TEXT, target_value REAL, source_kind TEXT,
                status TEXT
            );
            CREATE TABLE operating_kpi_measurements (
                id INTEGER PRIMARY KEY, kpi_id INTEGER, cycle_id INTEGER, value REAL, target_value REAL,
                status TEXT, measured_at TEXT, source_ref TEXT, evidence_ref TEXT,
                metadata_json TEXT, UNIQUE(kpi_id, cycle_id)
            );
            CREATE TABLE operating_cycle_reviews (
                id INTEGER PRIMARY KEY, cycle_id INTEGER UNIQUE, reviewer TEXT, summary TEXT,
                decision TEXT
            );
            INSERT INTO operating_cycles VALUES
                (1, 'test-cycle', 'Test Cycle', 'sprint', 'planned', '2026-08-17', '2026-08-23', 'Test focus', 600, 'Owner', NULL);
            INSERT INTO operating_cycle_goal_links VALUES (1, 1, 1, 'objective');
            INSERT INTO tasks VALUES (1, 'First task', 'todo', 2, 'test', 0, 60, 0, NULL);
            INSERT INTO tasks VALUES (2, 'Second task', 'done', 2, 'test', 100, 30, 30, NULL);
            INSERT INTO operating_cycle_tasks VALUES (1, 1, 1, 'committed', 60, 1);
            INSERT INTO operating_cycle_tasks VALUES (2, 1, 2, 'committed', 30, 1);
            INSERT INTO operating_kpi_definitions VALUES
                (1, 'cycle-delivery-rate', 'Cycle delivery rate', 'delivery', '%', 'increase', 80, 'cycle_tasks', 'active'),
                (2, 'cycle-commitment-accuracy', 'Commitment accuracy', 'accuracy', '%', 'increase', 80, 'cycle_tasks', 'active'),
                (3, 'ai-telemetry-success-rate', 'Telemetry success', 'success', '%', 'increase', 95, 'ai_telemetry', 'active'),
                (4, 'ai-budget-compliance', 'Budget compliance', 'budget', '%', 'increase', 100, 'ai_telemetry', 'active'),
                (5, 'ai-cost-usd', 'AI cost', 'cost', 'USD', 'decrease', 5, 'ai_telemetry', 'active'),
                (6, 'cycle-review-complete', 'Review complete', 'review', '%', 'increase', 100, 'review', 'active'),
                (7, 'evidence-gate-rate', 'Evidence gate', 'evidence', '%', 'increase', 100, 'manual', 'active');
            """
        )

        opener = sqlite_cell_opener(self.conn)  # the goals cell, over the legacy tables of the test database
        self.goals = opener("goals")
        self.goals.create("goals", {"title": "AI objective", "status": "active", "priority": 1,
                                    "progress_percent": 0, "due_date": "2026-09-30"})
        self.goals.create("goal-metrics", {"goal_id": 1, "metric_key": "cycle-start", "name": "Cycle starts",
                                           "metric_type": "key_result", "direction": "increase", "unit": "count",
                                           "baseline_value": 0, "target_value": 1, "current_value": 0,
                                           "status": "no_data", "source_type": "manual"})
        for name, replacement in (("open_cell_store", opener), ("open_task_store", lambda _c, automation, **_kw: sqlite_opener(self.conn, automation))):
            patcher = patch.object(ai_cycle, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.conn.close()

    def test_task_summary_is_deterministic(self):
        rows = [dict(row) for row in self.conn.execute(
            """
            SELECT ct.commitment_type, ct.estimate_minutes, t.id, t.title, t.status,
                   t.actual_minutes, t.estimated_minutes AS task_estimated_minutes,
                   t.priority, t.project, t.progress_percent, t.due_date,
                   ct.evidence_required
            FROM operating_cycle_tasks ct JOIN tasks t ON t.id = ct.task_id
            WHERE ct.cycle_id = 1 ORDER BY t.id
            """
        )]
        self.assertEqual(task_summary(rows)["completed"], 1)
        self.assertEqual(task_summary(rows)["estimated_minutes"], 90)
        self.assertAlmostEqual(task_summary(rows)["delivery_rate"], 50.0)

    def test_report_exposes_planning_gate_and_runtime_kpis(self):
        cycle = dict(self.conn.execute("SELECT * FROM operating_cycles WHERE id = 1").fetchone())
        payload = report_payload(cycle)
        self.assertIn("cycle is planned", payload["warnings"][0])
        self.assertEqual(payload["task_summary"]["completed"], 1)
        self.assertNotIn("telemetry", payload)
        ai_cost = next(item for item in payload["kpis"] if item["slug"] == "ai-cost-usd")
        self.assertIsNone(ai_cost["value"])
        delivery = next(item for item in payload["kpis"] if item["slug"] == "cycle-delivery-rate")
        self.assertEqual(delivery["value"], 50.0)
        self.assertEqual(delivery["status"], "at_risk")

    def test_snapshot_is_idempotent(self):
        cycle = dict(self.conn.execute("SELECT * FROM operating_cycles WHERE id = 1").fetchone())
        snapshot_cycle(cycle)
        snapshot_cycle(cycle)
        count = self.conn.execute("SELECT COUNT(*) FROM operating_kpi_measurements").fetchone()[0]
        self.assertEqual(count, 7)

    def test_planned_cycle_with_started_task_is_blocked(self):
        self.conn.execute("UPDATE tasks SET status = 'in_progress' WHERE id = 1")
        self.assertIn("planned cycle has started work (2 task(s)): test-cycle", command_check())


if __name__ == "__main__":
    unittest.main()

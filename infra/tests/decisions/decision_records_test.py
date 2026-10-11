#!/usr/bin/env python3
"""Regression tests for the cross-domain decision proposal boundary."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
# The goals, projects and roles-skills tables left schema.sql (migration 084); fixtures still seed them.
LEGACY_PORTFOLIO = Path(__file__).resolve().parents[1] / "support" / "legacy_portfolio_tables.sql"
LEGACY_TASKS = ROOT / "infra" / "tests" / "support" / "legacy_task_tables.sql"
# cron_jobs and the operating tables left schema.sql (migration 085); their legacy DDL serves the automation cell double.
LEGACY_OIA = ROOT / "infra" / "tests" / "support" / "legacy_operating_intake_automation_tables.sql"
RUNNER = ROOT / "infra" / "tests" / "support" / "run_with_sqlite_store.py"
ENGINE = ROOT / "infra" / "scripts" / "workflows" / "decision_engine.py"
REVIEW_WORKFLOW = ROOT / "infra" / "scripts" / "workflows" / "decision_review.py"
CLI = ROOT / "infra" / "scripts" / "cli" / "decision-cli.py"


def run_engine(db_path: Path) -> dict:
    env = os.environ.copy()
    env["SOLO_EMPIRE_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(ENGINE), "--sync", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def run_cli(db_path: Path, args: list[str]) -> dict:
    env = os.environ.copy()
    env["SOLO_EMPIRE_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(CLI), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def run_review_workflow(db_path: Path, args: list[str]) -> dict:
    env = os.environ.copy()
    env["SOLO_EMPIRE_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(REVIEW_WORKFLOW), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="solo-empire-decisions-") as temp_dir:
        db_path = Path(temp_dir) / "decision-test.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(LEGACY_PORTFOLIO.read_text(encoding="utf-8"))
        conn.executescript(LEGACY_TASKS.read_text(encoding="utf-8"))
        conn.executescript(LEGACY_OIA.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO cron_jobs (name, schedule, job_type, last_status)
            VALUES ('Gmail sync', '* * * * *', 'sync_gmail', 'failed')
            """
        )
        conn.execute(
            """
            INSERT INTO tasks (title, status, due_date)
            VALUES ('Overdue task fixture', 'todo', date('now', '-1 day'))
            """
        )
        conn.execute(
            """
            INSERT INTO goals (title, status, due_date)
            VALUES ('Overdue goal fixture', 'active', date('now', '-1 day'))
            """
        )
        conn.commit()
        conn.close()

        first = run_engine(db_path)
        second = run_engine(db_path)

        assert first["signals"] == 3
        assert first["created"] == 3
        assert second["created"] == 0
        assert second["updated"] == 3

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """
            SELECT dedupe_key, approval_required, approval_status,
                   action_status, privacy_class
            FROM decision_records
            ORDER BY dedupe_key
            """
        ).fetchall()
        event_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'decision_events'"
        ).fetchone()
        conn.close()

        assert event_tables == ("decision_events",)
        assert len(rows) == 3
        assert len({row[0] for row in rows}) == 3
        assert rows == [
            ("cron_failed:sync_gmail", 1, "pending", "pending_approval", "internal"),
            ("overdue_goals", 0, "not_required", "none", "private"),
            ("overdue_tasks", 0, "not_required", "none", "internal"),
        ]

        safe_list = run_cli(db_path, ["list", "--json"])
        assert safe_list["count"] == 3
        assert sum(1 for row in safe_list["decisions"] if row.get("redacted")) == 1
        assert all(
            row["privacy_class"] == "internal" or row.get("redacted")
            for row in safe_list["decisions"]
        )

        approved = run_cli(
            db_path,
            [
                "approve",
                "1",
                "--confirm",
                "--actor",
                "test-user",
                "--reason",
                "Approved for the regression fixture",
                "--json",
            ],
        )
        assert approved["decision"]["status"] == "approved"
        assert approved["decision"]["action_status"] == "queued"

        completed = run_cli(
            db_path,
            [
                "complete",
                "1",
                "--confirm",
                "--outcome",
                "Fallback documented",
                "--actor",
                "test-user",
                "--json",
            ],
        )
        assert completed["decision"]["status"] == "completed"
        assert completed["decision"]["action_status"] == "executed"

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE decision_records SET review_date = date('now') WHERE id = 1"
        )
        conn.commit()
        conn.close()

        first_review = run_review_workflow(db_path, ["--sync", "--json"])
        second_review = run_review_workflow(db_path, ["--sync", "--json"])
        assert first_review["eligible"] == 1
        assert first_review["created_reviews"] == 1
        assert first_review["created_tasks"] == 1
        assert second_review["created_reviews"] == 0
        assert second_review["created_tasks"] == 0
        assert second_review["existing_reviews"] == 1

        conn = sqlite3.connect(db_path)
        review_row = conn.execute(
            "SELECT id, task_id, status FROM decision_reviews WHERE decision_id = 1"
        ).fetchone()
        task_row = conn.execute(
            "SELECT status, external_source, external_id FROM tasks WHERE external_source = 'solo_empire_decision_review'"
        ).fetchone()
        conn.close()
        assert review_row == (1, 2, "pending")
        assert task_row == ("todo", "solo_empire_decision_review", "decision-review:1")

        reviewed = run_cli(
            db_path,
            [
                "review",
                "1",
                "--confirm",
                "--outcome",
                "The approved fallback solved the issue",
                "--actor",
                "test-user",
                "--json",
            ],
        )
        assert reviewed["review"]["status"] == "completed"
        assert reviewed["review"]["task_id"] == 2

        events = run_cli(db_path, ["events", "1", "--json"])
        assert [event["event_type"] for event in events["events"]] == [
            "created",
            "approved",
            "completed",
            "review_task_created",
            "reviewed",
        ]
        assert events["events"][1]["actor"] == "test-user"

        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT status FROM tasks WHERE id = 2").fetchone() == ("done",)
        assert conn.execute("SELECT status FROM decision_reviews WHERE id = 1").fetchone() == ("completed",)
        conn.close()

    print("✅ decisions: approval, audit, transitions, review-task idempotency, privacy, and outcome loop passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

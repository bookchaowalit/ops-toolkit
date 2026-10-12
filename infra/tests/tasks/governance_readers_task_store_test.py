#!/usr/bin/env python3
"""Governance and engagement readers read tasks only through the task store.

Run: bash scripts/setup/run-python3.sh tests/tasks/governance_readers_task_store_test.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import sys
import tempfile
import types
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "docs/registries", "architecture-docs checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

# The goals, projects and roles-skills tables left schema.sql (migration 084); fixtures still seed them.
LEGACY_PORTFOLIO = Path(__file__).resolve().parents[1] / "support" / "legacy_portfolio_tables.sql"

import engagement_notifications  # noqa: E402
import engagement_review  # noqa: E402
import portfolio_context_guard  # noqa: E402
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402
from sqlite_task_store import SqliteTaskStore, apply_legacy_task_tables  # noqa: E402
from sqlite_notifications_store import SqliteNotificationsStore  # noqa: E402

SOURCE = engagement_review.EXTERNAL_SOURCE


class Spy:
    """Replacement for open_task_store: records the automation id, returns the SQLite store."""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self.automations: list[str] = []
        self.conn = conn

    def __call__(self, conn, automation, **_kw):
        self.automations.append(automation)
        return SqliteTaskStore(conn if conn is not None else self.conn)


def make_db(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or ":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(LEGACY_PORTFOLIO.read_text(encoding="utf-8"))
    apply_legacy_task_tables(conn)
    conn.execute("PRAGMA foreign_keys = OFF")  # fixtures include orphan links on purpose
    return conn


def seed(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        INSERT INTO tasks (id, title, status, priority, project, tags, due_date, external_source, external_id, archived_at)
        VALUES
            (1, 'Linked task', 'todo', 1, 'engagements/demo', '["function:design-architecture"]', '2026-10-01',
             '{SOURCE}', 'engagement:demo:next-action', NULL),
            (2, 'Unlinked task', 'done', 2, NULL, '["role:x"]', NULL, NULL, NULL, NULL),
            (3, 'Archived task', 'todo', 2, NULL, NULL, NULL, NULL, NULL, '2026-01-01 00:00:00'),
            (4, '', 'todo', NULL, NULL, NULL, NULL, NULL, NULL, NULL);
        INSERT INTO portfolio_contexts (id, slug, name, context_type) VALUES (1, 'ctx', 'Context', 'internal');
        INSERT INTO task_context_links (task_id, context_id) VALUES (1, 1), (99, 1), (1, 77);
    """)
    conn.commit()


# ── portfolio_context_guard ─────────────────────────────────
def run_portfolio_guard(spy: Spy) -> str:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "guard.db"
        seed_conn = make_db(str(path))
        seed(seed_conn)
        out = io.StringIO()
        with patch.object(portfolio_context_guard, "open_cell_store", sqlite_cell_opener(seed_conn)), \
                patch.object(portfolio_context_guard, "load_yaml", return_value={}), \
                patch.object(portfolio_context_guard, "seed", return_value={}), \
                patch.object(portfolio_context_guard, "open_task_store", lambda _c, automation, **kw: spy(seed_conn, automation, **kw)), \
                patch.object(sys, "argv", ["portfolio_context_guard.py"]), \
                contextlib.redirect_stdout(out):
            assert portfolio_context_guard.main() == 1  # other fixture tables are empty on purpose
        seed_conn.close()
    return out.getvalue()


def test_portfolio_guard_counts_match_the_old_sql_and_use_store():
    spy = Spy()
    output = run_portfolio_guard(spy)
    # Old SQL: tasks LEFT JOIN task_context_links WHERE l.id IS NULL -> tasks 2, 3 (archived counts), 4.
    assert "FAIL: tasks without context: 3" in output, output
    # Old SQL: links whose task or context is missing -> (99, 1) and (1, 77).
    assert "FAIL: invalid goal/project/task context links: 2" in output, output
    assert spy.automations and set(spy.automations) == {"governance-reader"}


# ── engagement_review / engagement_notifications ────────────
def test_engagement_review_fetch_tasks_uses_store_and_includes_archived():
    conn = make_db()
    seed(conn)
    tasks = engagement_review.fetch_tasks(SqliteTaskStore(conn), [1, 3, 99])
    assert sorted(tasks) == [1, 3]
    assert tasks[1]["external_source"] == SOURCE and tasks[1]["due_date"] == "2026-10-01"
    assert tasks[1]["project"] == "engagements/demo" and tasks[3]["archived_at"]
    assert engagement_review.fetch_tasks(SqliteTaskStore(conn), []) == {}


def test_engagement_review_report_goes_through_store():
    conn = make_db()
    seed(conn)
    spy = Spy(conn)
    data = {"engagements": [{"slug": "demo", "status": "active", "next_action": "go", "task_ids": [1, 99],
                             "next_review_on": "2026-12-01", "delivery_workspace": "x"}]}

    with patch.object(engagement_review, "PORTABLE_CI", False), \
            patch.object(engagement_review, "open_task_store", spy):
        report = engagement_review.review(data, date(2026, 10, 9), 7)
    messages = [f["message"] for f in report["findings"]]
    assert "linked task #99 does not exist" in messages, messages
    assert any("task #1 is overdue since 2026-10-01: Linked task" in m for m in messages), messages
    assert spy.automations == ["engagement-reader"]


def test_engagement_notifications_snapshot_uses_store_and_digest_queue():
    conn = make_db()
    seed(conn)
    snapshot = engagement_notifications.task_snapshot(SqliteTaskStore(conn), [1, 2, 99])
    assert sorted(snapshot) == [1, 2] and snapshot[1]["priority"] == 1 and snapshot[1]["due_date"] == "2026-10-01"
    assert engagement_notifications.task_snapshot(SqliteTaskStore(conn), []) == {}

    spy = Spy(conn)

    findings = [{"severity": "warning", "category": "overdue_task", "slug": "demo", "message": "m", "task_id": 1}]
    notifications = SqliteNotificationsStore(conn)
    with patch.object(engagement_notifications, "open_task_store", spy), \
            patch.object(engagement_notifications, "open_notifications_store", lambda *_a, **_k: notifications):
        result = engagement_notifications.queue_digest(findings, apply=True)
    assert result["queued"] is True and spy.automations == ["engagement-reader"]
    entry = notifications.notifications()[0]
    assert (entry["task_id"], entry["task_priority"], entry["task_due_date"]) == (1, 1, "2026-10-01"), entry


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("Governance readers task store tests passed")

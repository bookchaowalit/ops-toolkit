#!/usr/bin/env python3
"""Reporting readers read tasks only through the task store adapter.

For each migrated file: (i) the SQLite path returns what the legacy SQL returned
on a small fixture, (ii) the file opens the store as automation "reporting-reader".

Run: bash scripts/setup/run-python3.sh tests/tasks/reporting_readers_task_store_test.py
"""

from __future__ import annotations

import contextlib
import os
import io
import json
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Google OAuth settings are read at import time by some scripts; never depend on a private infra/.env.
for _name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
    os.environ.setdefault(_name, "offline-test-placeholder")

ROOT = Path(__file__).resolve().parents[3]
for sub in ("utils", "workflows", "sync"):
    sys.path.insert(0, str(ROOT / "infra" / "scripts" / sub))


import alerts  # noqa: E402
import daily_briefing  # noqa: E402
import daily_diary  # noqa: E402
import decision_engine  # noqa: E402
import diary_summary  # noqa: E402
import system_health  # noqa: E402
import system_status  # noqa: E402
import task_calendar_health  # noqa: E402
import task_email_digest  # noqa: E402
import tasks_view  # noqa: E402
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_task_store import SqliteTaskStore, apply_legacy_task_tables  # noqa: E402
from sqlite_decision_store import SqliteDecisionStore, apply_legacy_decision_tables  # noqa: E402  (isolates the decisions API)
import decision_store  # noqa: E402
from sqlite_habits_store import SqliteHabitsStore  # noqa: E402  (isolates the habits API)
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402  (isolates the goals, projects and roles-skills APIs)

AUTOMATION = "reporting-reader"
TODAY = date.today()
DAY = TODAY.isoformat()
PAST = (TODAY - timedelta(days=3)).isoformat()
STAMP = f"{DAY} 06:00:00"
OLD_STAMP = f"{(TODAY - timedelta(days=9)).isoformat()} 05:00:00"

COLUMNS = ("id", "title", "status", "priority", "project", "due_date", "external_source", "completed_at",
           "created_at", "archived_at", "parent_task_id", "category", "tags")
ROWS = [
    (1, "Overdue p1", "todo", 1, "A", PAST, None, None, STAMP, None, None, "ops", '["path:dev"]'),
    (2, "Today wip", "in_progress", 2, "A", DAY, "todoist", None, OLD_STAMP, None, None, "ops", None),
    (3, "Finished", "done", 2, "B", None, None, STAMP, OLD_STAMP, None, None, "income", None),
    (4, "Blocked one", "blocked", 3, "B", PAST, "gmail", None, OLD_STAMP, None, None, None, None),
    (5, "No priority", "todo", None, "C", DAY, None, None, OLD_STAMP, None, None, "ops", None),
    (6, "Archived gmail", "todo", 2, "A", DAY, "gmail", None, OLD_STAMP, STAMP, None, None, None),
    (7, "Employer", "todo", 2, "A", DAY, "leantime", None, OLD_STAMP, None, None, None, None),
    (8, "Cancelled", "cancelled", 2, "B", PAST, None, None, OLD_STAMP, None, None, None, None),
    (9, "Child of blocked", "todo", 1, "A", PAST, "gmail", None, OLD_STAMP, None, 4, None, None),
    (10, "Archived done", "done", 2, "B", DAY, None, STAMP, OLD_STAMP, STAMP, None, None, None),
    (11, "Loose", "todo", 2, None, None, None, None, STAMP, None, None, "ops", None),
    (12, "Second today", "todo", 2, "A", DAY, None, None, OLD_STAMP, None, None, None, None),
]


def fixture(directory: str) -> Path:
    path = Path(directory) / "fixture.db"
    conn = sqlite3.connect(path)
    apply_legacy_task_tables(conn)
    placeholders = ", ".join("?" * len(COLUMNS))
    conn.executemany(f"INSERT INTO tasks ({', '.join(COLUMNS)}) VALUES ({placeholders})", ROWS)
    conn.execute("INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (5, 2)")
    conn.commit()
    conn.close()
    return path


ACTIVE_DB: list = [None]  # the fixture database of the running test; production opens the store without a connection


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextlib.contextmanager
def spy(module, calls: list):
    def opener(conn, automation, **kwargs):
        calls.append(automation)
        return SqliteTaskStore(conn if conn is not None else connect(ACTIVE_DB[0]))

    with patch.object(module, "open_task_store", side_effect=opener):
        yield


def with_db(fn):
    def wrapper():
        with tempfile.TemporaryDirectory() as directory:
            path = fixture(directory)
            ACTIVE_DB[0] = path
            opener = sqlite_cell_opener(sqlite3.connect(path))  # goals / projects / roles-skills cells over the fixture
            with contextlib.ExitStack() as stack:
                # the habits, decisions and notifications doubles open the test database by path
                stack.enter_context(patch.dict(os.environ, {"SOLO_EMPIRE_DB_PATH": str(path)}))
                for module in (alerts, daily_briefing, decision_engine,
                               system_health, system_status):
                    stack.enter_context(patch.object(module, "open_cell_store", opener))
                fn(path, Path(directory))
    wrapper.__name__ = fn.__name__
    return wrapper


def legacy(path: Path, sql: str, params: tuple = ()) -> list:
    conn = connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ── daily_diary ────────────────────────────────────────────
@with_db
def test_daily_diary_matches_legacy_sql_and_uses_store(path, tmp):
    calls: list = []
    conn = connect(path)
    with spy(daily_diary, calls):
        payload = daily_diary.build_daily_diary(as_of=DAY, repo_root=tmp, entries_root=tmp / "entries")
    cols = "t.title, t.project, t.category, t.priority"
    done = legacy(path, f"""SELECT {cols}, t.completed_at FROM tasks t WHERE t.status = 'done'
        AND t.completed_at IS NOT NULL AND date(t.completed_at, 'localtime') = ? ORDER BY t.completed_at, t.id LIMIT 30""", (DAY,))
    created = legacy(path, f"""SELECT {cols}, t.created_at FROM tasks t WHERE date(t.created_at, 'localtime') = ?
        ORDER BY t.created_at, t.id LIMIT 30""", (DAY,))
    assert payload["activities"]["completed_tasks"] == [dict(r) for r in done] and len(done) == 2
    assert payload["activities"]["created_tasks"] == [dict(r) for r in created] and len(created) == 2
    state = legacy(path, """SELECT SUM(CASE WHEN status = 'blocked' AND archived_at IS NULL THEN 1 ELSE 0 END),
        SUM(CASE WHEN due_date < ? AND status NOT IN ('done', 'cancelled') AND archived_at IS NULL THEN 1 ELSE 0 END)
        FROM tasks""", (DAY,))[0]
    assert (payload["overview"]["blocked_tasks_current"], payload["overview"]["overdue_tasks_current"]) == tuple(state)
    assert calls == [AUTOMATION]


# ── diary_summary ──────────────────────────────────────────
@with_db
def test_diary_summary_matches_legacy_sql_and_uses_store(path, tmp):
    calls: list = []
    conn = connect(path)
    with spy(diary_summary, calls):
        payload = diary_summary.build_diary_summary(cadence="weekly", as_of=DAY, repo_root=tmp,
                                                    entries_root=tmp / "entries")
        start, end = diary_summary.compute_window("weekly", DAY)
        days = diary_summary._active_days_from_db(start.isoformat(), end.isoformat())
    s, e = start.isoformat(), end.isoformat()
    overview = payload["overview"]
    assert overview["completed_tasks"] == legacy(path, """SELECT COUNT(*) FROM tasks WHERE status = 'done'
        AND completed_at IS NOT NULL AND date(completed_at, 'localtime') BETWEEN ? AND ?""", (s, e))[0][0] == 2
    assert overview["created_tasks"] == legacy(
        path, "SELECT COUNT(*) FROM tasks WHERE date(created_at, 'localtime') BETWEEN ? AND ?", (s, e))[0][0] == 2
    expected_days = {r[0] for r in legacy(path, """SELECT DISTINCT d FROM (
        SELECT date(completed_at, 'localtime') AS d FROM tasks WHERE status = 'done' AND completed_at IS NOT NULL
          AND date(completed_at, 'localtime') BETWEEN ? AND ?)""", (s, e))}
    assert days == expected_days and DAY in days
    state = legacy(path, """SELECT SUM(CASE WHEN status = 'blocked' AND archived_at IS NULL THEN 1 ELSE 0 END),
        SUM(CASE WHEN due_date < ? AND status NOT IN ('done', 'cancelled') AND archived_at IS NULL THEN 1 ELSE 0 END)
        FROM tasks""", (e,))[0]
    assert (overview["blocked_tasks_current"], overview["overdue_tasks_current"]) == tuple(state)
    assert calls and set(calls) == {AUTOMATION}


# ── daily_briefing ─────────────────────────────────────────
@with_db
def test_daily_briefing_orders_like_sqlite_and_uses_store(path, _tmp):
    calls: list = []
    habits = SqliteHabitsStore(sqlite3.connect(":memory:"))  # no habits: this test is about tasks
    with patch.object(daily_briefing, "open_habits_store", lambda *a, **k: habits), \
            spy(daily_briefing, calls):
        data = daily_briefing.get_db_briefing()
    today = datetime.now().strftime("%Y-%m-%d")
    expected_today = legacy(path, """SELECT title, external_source, priority, status FROM tasks
        WHERE due_date = ? AND status NOT IN ('done', 'cancelled') ORDER BY priority DESC, external_source""", (today,))
    expected_overdue = legacy(path, """SELECT title, external_source, priority, due_date FROM tasks
        WHERE due_date < ? AND status NOT IN ('done', 'cancelled') ORDER BY due_date LIMIT 5""", (today,))
    assert [tuple(r) for r in data["tasks_today"]] == [tuple(r) for r in expected_today] and len(expected_today) == 5
    assert [tuple(r) for r in data["tasks_overdue"]] == [tuple(r) for r in expected_overdue] and len(expected_overdue) == 3
    assert calls == [AUTOMATION]


# ── system_status / system_health ──────────────────────────
@with_db
def test_system_status_and_health_task_counts(path, _tmp):
    expected = {"total": 12, "done": 2, "in_progress": 1}
    legacy_counts = legacy(path, "SELECT COUNT(*), SUM(status='done'), SUM(status='in_progress') FROM tasks")[0]
    assert tuple(expected.values()) == tuple(legacy_counts)
    for module, function in ((system_status, system_status.task_summary), (system_health, system_health.collect_tasks)):
        calls: list = []
        with spy(module, calls):
            assert function() == expected
        assert calls == [AUTOMATION]


# ── task_email_digest ──────────────────────────────────────
@with_db
def test_task_email_digest_fetch_matches_legacy_and_uses_store(path, _tmp):
    calls: list = []
    with spy(task_email_digest, calls):
        everything = task_email_digest.fetch_open_tasks(connect(path))
        project = task_email_digest.fetch_open_tasks(connect(path), project="A")
    sql = """SELECT id, title, status, priority, project, category, tags, due_date FROM tasks
        WHERE archived_at IS NULL AND status NOT IN ('done', 'cancelled'){} ORDER BY priority ASC, due_date ASC, id ASC"""
    expected = [dict(r) for r in legacy(path, sql.format(""))]
    expected_a = [dict(r) for r in legacy(path, sql.format(" AND project = ?"), ("A",))]
    normalize = lambda rows: [{**r, "tags": task_email_digest._parse_tags(r["tags"])} for r in rows]  # noqa: E731
    assert normalize(everything) == normalize(expected) and normalize(project) == normalize(expected_a)
    assert [t["id"] for t in everything][0] == 5 and len(expected_a) == 5
    assert calls == [AUTOMATION, AUTOMATION]


# ── alerts ─────────────────────────────────────────────────
@with_db
def test_alerts_gmail_task_count_matches_legacy_and_uses_store(path, _tmp):
    calls: list = []
    output = io.StringIO()
    with spy(alerts, calls), contextlib.redirect_stdout(output):
        alerts.check_alerts()
    expected = legacy(path, "SELECT COUNT(*) FROM tasks WHERE external_source = 'gmail' "
                      "AND status NOT IN ('done','cancelled','archived')")[0][0]
    assert expected == 3
    assert f"{expected} open task(s) from Gmail pipeline" in output.getvalue()
    assert calls == [AUTOMATION]


# ── tasks_view ─────────────────────────────────────────────
@with_db
def test_tasks_view_matches_legacy_order_and_uses_store(path, _tmp):
    calls: list = []
    with spy(tasks_view, calls):
        tasks = tasks_view.load_tasks()
    expected = legacy(path, """SELECT id, title, status, priority, project, due_date, external_source FROM tasks
        WHERE status NOT IN ('done', 'cancelled') AND archived_at IS NULL
          AND (external_source IS NULL OR external_source != 'leantime')
        ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END, priority ASC,
          COALESCE(due_date, '9999-12-31') ASC, id ASC""")
    assert tasks == [dict(r) for r in expected] and len(tasks) == 7
    assert "sync:" not in tasks_view.render("T", "d", tasks)
    assert calls == [AUTOMATION]


# ── decision_engine ────────────────────────────────────────
@with_db
def test_decision_engine_overdue_signal_matches_legacy_and_uses_store(path, _tmp):
    calls: list = []
    with spy(decision_engine, calls):
        signals = decision_engine.collect_signals()
    expected = legacy(path, """SELECT COUNT(*) FROM tasks WHERE status NOT IN ('done', 'cancelled')
        AND due_date IS NOT NULL AND due_date < date('now')""")[0][0]
    overdue = [s for s in signals if s["dedupe_key"] == "overdue_tasks"]
    assert expected == 3 and overdue[0]["rationale"] == f"There are {expected} open task(s) past their due date."
    assert calls == [AUTOMATION]


# ── task_calendar_health ───────────────────────────────────
@with_db
def test_task_calendar_health_matches_legacy_and_uses_store(path, _tmp):
    calls: list = []
    with spy(task_calendar_health, calls):
        health = task_calendar_health.collect_health()
    conn = connect(path)
    conn.execute("UPDATE tasks SET reminder_at = '2026-01-01 00:00:00', calendar_event_id = 'evt' WHERE id = 2")
    conn.commit()
    conn.close()
    with spy(task_calendar_health, calls):
        health_linked = task_calendar_health.collect_health()
    tasks = dict(legacy(path, """
        SELECT count(*) active, COALESCE(sum(status='in_progress'),0) in_progress,
          COALESCE(sum(date(due_date)<date('now','+7 hours')),0) overdue,
          COALESCE(sum(NULLIF(due_date,'') IS NOT NULL),0) dated,
          COALESCE(sum(reminder_at IS NOT NULL),0) manual_reminders,
          COALESCE(sum(calendar_event_id IS NOT NULL),0) calendar_linked
        FROM tasks WHERE archived_at IS NULL AND status NOT IN ('done','cancelled')
          AND COALESCE(external_source,'') != 'leantime'""")[0])
    focus = [r[0] for r in legacy(path, """
        SELECT t.id FROM tasks t WHERE t.archived_at IS NULL AND t.status IN ('todo','in_progress')
          AND COALESCE(t.external_source,'')!='leantime'
          AND NOT EXISTS (SELECT 1 FROM task_dependencies d JOIN tasks p ON p.id=d.depends_on_task_id
            WHERE d.task_id=t.id AND p.status NOT IN ('done','cancelled'))
          AND NOT EXISTS (SELECT 1 FROM tasks p WHERE p.id=t.parent_task_id AND p.status='blocked')
        ORDER BY t.priority, CASE t.status WHEN 'in_progress' THEN 0 ELSE 1 END,
          COALESCE(NULLIF(t.due_date,''),'9999-12-31'), t.id LIMIT 5""")]
    assert health_linked["tasks"] == tasks and health_linked["suggested_focus_task_ids"] == focus
    assert health["tasks"]["manual_reminders"] == 0 and tasks["manual_reminders"] == 1
    assert focus == [1, 2, 12, 11] and 5 not in focus and 9 not in focus
    assert calls == [AUTOMATION, AUTOMATION]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"✅ {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"❌ {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)

#!/usr/bin/env python3
"""Workflow task writers go through the task_store adapter (SQLite result + store calls)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "infra" / "scripts"
for sub in ("utils", "workflows"):
    sys.path.insert(0, str(SCRIPTS / sub))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_task_store import SqliteTaskStore, sqlite_opener, use_connection  # noqa: E402  (also isolates the test from the live Tasks API)
from sqlite_decision_store import SqliteDecisionStore, apply_legacy_decision_tables  # noqa: E402
from sqlite_cell_store import SqliteCellStore  # noqa: E402  (skills and role-skill links: the roles-skills cell)

import decision_review  # noqa: E402
import role_practice_evidence  # noqa: E402

# Production is API-only; these tests run the writers on the SQLite double over their temp database.
for _module in (decision_review, role_practice_evidence):
    _module.open_task_store = sqlite_opener

TASKS_SCHEMA = (ROOT / 'infra' / 'tests' / 'support' / 'sqlite_tasks_schema.sql')
EXTRA_SCHEMA = """
CREATE TABLE progress_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id INTEGER, activity_type TEXT, title TEXT,
    description TEXT, what_learned TEXT, challenges TEXT, next_steps TEXT, evidence_url TEXT,
    progress_before INTEGER, progress_after INTEGER, logged_at TEXT, created_at TEXT);
CREATE TABLE skills (id INTEGER PRIMARY KEY, name TEXT, progress_percent INTEGER,
    last_practiced TEXT, updated_at TEXT);
CREATE TABLE role_skill_links (id INTEGER PRIMARY KEY, role_id TEXT, skill_id INTEGER, source_registry TEXT);
"""


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(TASKS_SCHEMA.read_text(encoding="utf-8"))
    conn.executescript(EXTRA_SCHEMA)
    apply_legacy_decision_tables(conn)  # the retired decision tables, for the decisions store double
    use_connection(conn)  # production opens the Tasks store without a connection
    return conn


def task_row(conn: sqlite3.Connection, number: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (number,)).fetchone()


class FakeTaskStore:
    def __init__(self, tasks: dict[int, dict] | None = None, external: dict | None = None,
                 assignments: list[dict] | None = None) -> None:
        self.tasks = tasks or {}
        self.assigned = assignments or []
        self.external = external or {}
        self.calls: list[tuple] = []

    def get(self, number):
        self.calls.append(("get", number))
        return self.tasks.get(number)

    def assignments(self, *, task=None, assignee_type=None, assignee_id=None, assignment_type=None):
        self.calls.append(("assignments", task, assignee_type, assignee_id, assignment_type))
        wanted = {"task": task, "assignee_type": assignee_type, "assignee_id": assignee_id,
                  "assignment_type": assignment_type}
        return [a for a in self.assigned if all(v is None or a[k] == v for k, v in wanted.items())]

    def find_external(self, source, external_id):
        self.calls.append(("find_external", source, external_id))
        return self.external.get((source, external_id))

    def upsert_external(self, source, external_id, fields, *, legacy=None):
        self.calls.append(("upsert_external", source, external_id, fields, legacy))
        return "created", 901

    def update(self, number, fields, *, legacy=None):
        self.calls.append(("update", number, fields, legacy))

    def complete(self, number, *, legacy=None):
        self.calls.append(("complete", number, legacy))


# ---- decision-review ------------------------------------------------------

def seed_decision(conn: sqlite3.Connection):
    conn.execute(
        "INSERT INTO decision_records (id, title, decision, status, approval_status, action_status, privacy_class,"
        " approval_required, action_type) VALUES (7, 'Pick stack', 'Use the stack', 'approved', 'approved', 'none',"
        " 'internal', 1, 'none')")
    conn.execute("INSERT INTO decision_reviews (id, decision_id, due_date) VALUES (3, 7, '2026-10-09')")
    decision = conn.execute("SELECT * FROM decision_records WHERE id = 7").fetchone()
    review = conn.execute(
        "SELECT id AS review_id, decision_id, task_id, due_date FROM decision_reviews WHERE id = 3").fetchone()
    return decision, review


def test_decision_review_sqlite_creates_and_links_task():
    conn = make_db()
    decision, review = seed_decision(conn)
    store = SqliteTaskStore(conn)
    task_id, created = decision_review.ensure_review_task(decision, review, "NOW", store)
    assert created is True
    row = task_row(conn, task_id)
    assert row["title"] == "Review decision #7: Pick stack" and row["priority"] == 2
    assert row["project"] == "decisions" and row["category"] == "ops" and row["sync_status"] == "local"
    assert row["tags"] == '["decision-review","decision:7"]'
    assert row["external_id"] == "decision-review:3"
    assert row["external_source"] == decision_review.REVIEW_TASK_SOURCE
    assert row["due_date"] == "2026-10-09" and row["created_at"] == row["updated_at"] == "NOW"
    # Linking the task to the review and the history event happen in sync_reviews (decisions cell);
    # see infra/tests/decisions/decision_records_test.py.
    assert decision_review.ensure_review_task(decision, review, "LATER", store) == (task_id, False)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_decision_review_store_calls():
    conn = make_db()
    decision, review = seed_decision(conn)
    fake = FakeTaskStore()
    assert decision_review.ensure_review_task(decision, review, "NOW", fake) == (901, True)
    assert fake.calls[0] == ("find_external", decision_review.REVIEW_TASK_SOURCE, "decision-review:3")
    call = fake.calls[1]
    assert call[0] == "upsert_external" and call[2] == "decision-review:3"
    assert call[3]["tags"] == '["decision-review","decision:7"]' and call[3]["priority"] == 2
    assert call[4] == {"sync_status": "local", "created_at": "NOW", "updated_at": "NOW"}

    fake = FakeTaskStore(external={(decision_review.REVIEW_TASK_SOURCE, "decision-review:3"): {"id": 44}})
    assert decision_review.ensure_review_task(decision, review, "NOW", fake) == (44, False)
    assert [c[0] for c in fake.calls] == ["find_external"]


# ---- role-evidence --------------------------------------------------------

def seed_role(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        INSERT INTO tasks (id, title, status, priority, notes) VALUES
            (10, 'low prio', 'todo', 3, NULL), (11, 'top prio', 'todo', 1, 'old'),
            (12, 'closed', 'done', 1, NULL);
        INSERT INTO task_assignments (task_id, assignee_type, assignee_id) VALUES
            (10, 'role', 'r1'), (11, 'role', 'r1'), (12, 'role', 'r1');
        INSERT INTO skills (id, name, progress_percent) VALUES (1, 's', 10);
        INSERT INTO role_skill_links (role_id, skill_id, source_registry) VALUES
            ('r1', 1, 'docs/ROLE-LEARNING.yaml');
    """)


def record(conn, task_id, mark, store=None):
    return role_practice_evidence.record_for_role("r1", task_id, "docs/x.md", {"r1": {"name": "Role One"}},
        {"r1": {"practice_action": "do", "success_signal": "ok"}}, mark, store=store,
        roles_skills=SqliteCellStore(conn, "roles-skills"))


def test_role_evidence_sqlite_updates_notes_status_and_validates_assignment():
    conn = make_db()
    seed_role(conn)
    result = record(conn, None, True)
    assert result["task_id"] == 11 and result["logs_inserted"] == 1
    row = task_row(conn, 11)
    assert row["status"] == "in_progress" and row["notes"].startswith("old; practice evidence: docs/x.md")
    assert task_row(conn, 10)["notes"] is None
    # same evidence again: no duplicate note
    notes = task_row(conn, 11)["notes"]
    assert record(conn, 11, True)["logs_inserted"] == 0 and task_row(conn, 11)["notes"] == notes
    # explicit task, no status change without flag
    record(conn, 10, False)
    assert task_row(conn, 10)["status"] == "todo" and "practice evidence" in task_row(conn, 10)["notes"]
    conn.execute("INSERT INTO tasks (id, title) VALUES (99, 'unassigned')")
    try:
        record(conn, 99, False)
    except ValueError as exc:
        assert "not assigned" in str(exc)
    else:
        raise AssertionError("unassigned task must be rejected")
    conn.execute("UPDATE tasks SET status='done' WHERE id IN (10, 11)")
    try:
        record(conn, None, False)
    except ValueError as exc:
        assert "no active task" in str(exc)
    else:
        raise AssertionError("no active task expected")


def test_role_evidence_store_calls():
    conn = make_db()
    seed_role(conn)
    fake = FakeTaskStore(tasks={
        10: {"id": 10, "status": "todo", "priority": 3, "notes": None},
        11: {"id": 11, "status": "todo", "priority": 1, "notes": "old"},
        12: {"id": 12, "status": "done", "priority": 1, "notes": None},
    }, assignments=[{"task": n, "assignee_type": "role", "assignee_id": "r1", "assignee_name": None,
                     "assignment_type": "assigned"} for n in (10, 11, 12)])
    assert record(conn, None, True, store=fake)["task_id"] == 11
    update = [c for c in fake.calls if c[0] == "update"]
    assert len(update) == 1 and update[0][1] == 11
    assert update[0][2]["status"] == "in_progress" and update[0][2]["notes"].startswith("old; ")
    assert update[0][3].keys() == {"updated_at"}


# ---- decision-cli ---------------------------------------------------------

def load_cli():
    spec = importlib.util.spec_from_file_location("decision_cli", SCRIPTS / "cli" / "decision-cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _KeepOpen:
    """Delegate to the connection but ignore close() so tests can assert afterwards."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def run_review_complete(cli, conn, fake=None):
    conn.commit()
    if fake is not None:
        cli.open_task_store = lambda c, automation, channel="workflow": (
            fake.calls.append(("open", automation, channel)) or fake)
    else:  # the SQLite double over the same connection
        cli.open_task_store = lambda c, automation, channel="workflow": SqliteTaskStore(conn)
    cli.open_decision_store = lambda automation, channel="workflow": SqliteDecisionStore(_KeepOpen(conn))
    args = argparse.Namespace(review_id=3, outcome="done well", notes=None, actor="tester", include_private=True)
    return cli.complete_review(args)


def seed_cli(conn, task_status):
    decision, _review = seed_decision(conn)
    conn.execute("INSERT INTO tasks (id, title, status) VALUES (50, 't', ?)", (task_status,))
    conn.execute("UPDATE decision_reviews SET task_id = 50 WHERE id = 3")


def test_decision_cli_sqlite_completes_open_task_and_skips_closed():
    cli = load_cli()
    conn = make_db()
    seed_cli(conn, "in_progress")
    run_review_complete(cli, conn)
    row = task_row(conn, 50)
    assert row["status"] == "done" and row["completed_at"] and row["completed_at"] == row["updated_at"]
    assert conn.execute("SELECT status FROM decision_reviews WHERE id = 3").fetchone()[0] == "completed"

    for closed in ("done", "cancelled"):
        conn = make_db()
        seed_cli(conn, closed)
        conn.execute("UPDATE tasks SET completed_at = 'orig', updated_at = 'orig' WHERE id = 50")
        run_review_complete(cli, conn)
        row = task_row(conn, 50)
        assert (row["status"], row["completed_at"], row["updated_at"]) == (closed, "orig", "orig")


def test_decision_cli_store_calls():
    cli = load_cli()
    conn = make_db()
    seed_cli(conn, "todo")
    fake = FakeTaskStore(tasks={50: {"id": 50, "status": "todo"}})
    run_review_complete(cli, conn, fake)
    assert fake.calls[0] == ("open", "owner-cli", "cli")
    assert fake.calls[1] == ("get", 50)
    assert fake.calls[2][0] == "complete" and fake.calls[2][1] == 50
    assert fake.calls[2][2].keys() == {"completed_at", "updated_at"}

    conn = make_db()
    seed_cli(conn, "cancelled")
    fake = FakeTaskStore(tasks={50: {"id": 50, "status": "cancelled"}})
    run_review_complete(cli, conn, fake)
    assert [c[0] for c in fake.calls] == ["open", "get"]


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
    print("Workflow writers task store tests passed")

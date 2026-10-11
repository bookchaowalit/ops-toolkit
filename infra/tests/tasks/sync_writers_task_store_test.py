#!/usr/bin/env python3
"""Sync writers create tasks only through the task store adapter.

Run: bash scripts/setup/run-python3.sh tests/tasks/sync_writers_task_store_test.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Google OAuth settings are read at import time by some scripts; never depend on a private infra/.env.
for _name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
    os.environ.setdefault(_name, "offline-test-placeholder")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402  (the roles-skills cell over the legacy tables)
from sqlite_task_store import SqliteTaskStore, use_connection, uses_sqlite_store  # noqa: E402  (also isolates the test from the live Tasks API)

SCHEMA = (ROOT / 'infra' / 'tests' / 'support' / 'sqlite_tasks_schema.sql')

import engagement_tasks_sync  # noqa: E402
import gmail_categorizer  # noqa: E402


def tasks_db(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or ":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()


class FakeTaskStore:
    def __init__(self, existing: dict | None = None) -> None:
        self.calls: list[tuple] = []
        self.existing = existing or {}

    def find_external(self, source, external_id):
        self.calls.append(("find_external", source, external_id))
        return self.existing.get((source, external_id))

    def upsert_external(self, source, external_id, fields, *, legacy=None):
        self.calls.append(("upsert_external", source, external_id, dict(fields), legacy))
        return "created", 41


# ── engagement-sync ─────────────────────────────────────────
ENGAGEMENTS = {"engagements": [
    {"slug": "acme", "name": "Acme", "work_type": "audit", "client": "Acme Co", "source_path": "x",
     "next_action": "Review scope", "priority": 2, "next_review_on": "2026-11-01"},
    {"slug": "idle", "name": "Idle", "work_type": "audit", "client": "c", "source_path": "x"},
]}


@uses_sqlite_store(engagement_tasks_sync)
def test_engagement_sync_is_idempotent():
    conn = tasks_db()
    use_connection(conn)
    first = engagement_tasks_sync.sync_tasks(ENGAGEMENTS)
    second = engagement_tasks_sync.sync_tasks(ENGAGEMENTS)
    found = rows(conn)
    assert first == second == {"acme": found[0]["id"]} and len(found) == 1
    task = found[0]
    assert (task["external_source"], task["external_id"]) == ("solo_empire_engagements", "engagement:acme:next-action")
    assert task["title"] == "[Engagement] Acme: Review scope" and task["priority"] == 2
    assert task["project"] == "engagements/acme" and task["category"] == "operations"
    assert task["due_date"] == "2026-11-01" and task["status"] == "todo" and task["sync_status"] == "local"
    assert task["tags"] == '["engagement", "audit", "acme", "review"]'


def test_engagement_sync_uses_store():
    fake = FakeTaskStore()
    with patch.object(engagement_tasks_sync, "open_task_store", return_value=fake) as opened:
        assert engagement_tasks_sync.sync_tasks(ENGAGEMENTS) == {"acme": 41}
    assert opened.call_args.args[1] == "engagement-sync"
    assert [c[:3] for c in fake.calls] == [("upsert_external", "solo_empire_engagements", "engagement:acme:next-action")]
    assert fake.calls[0][3]["project"] == "engagements/acme"


# ── email-pipeline: gmail_categorizer ───────────────────────
def run_categorizer(db: Path, fake=None):
    rule = gmail_categorizer.CATEGORY_RULES[0]
    email = {"id": "g-9", "from": "hr@example.com", "to": "me", "subject": "Interview invitation",
             "date": "Mon", "snippet": gmail_categorizer.JOB_RESPONSE_KEYWORDS[0], "labels": []}
    store_conn = _open(db)
    patches = [
        patch.object(sys, "argv", ["gmail_categorizer.py"]),
        patch.object(gmail_categorizer, "get_access_token", return_value="t"),
        patch.object(gmail_categorizer, "fetch_recent_emails", return_value=[email]),
        patch.object(gmail_categorizer, "get_existing_labels", return_value={rule["label_name"]: "L1"}),
        patch.object(gmail_categorizer, "categorize_email", return_value=rule["category"]),
        patch.object(gmail_categorizer, "apply_label"),
        patch.object(gmail_categorizer, "modify_labels"),
        patch.object(gmail_categorizer.time, "sleep"),
    ]
    if fake is not None:
        patches.append(patch.object(gmail_categorizer, "open_task_store", return_value=fake))
    else:  # the SQLite double over the same temporary database
        patches.append(patch.object(gmail_categorizer, "open_task_store", lambda c, aid, **_: SqliteTaskStore(store_conn)))
    for p in patches:
        p.start()
    try:
        gmail_categorizer.main()
    finally:
        for p in patches:
            p.stop()
        store_conn.commit()
        store_conn.close()


def _open(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def categorizer_db(tmp: str) -> Path:
    db = Path(tmp) / "t.db"
    conn = tasks_db(db)
    conn.execute("CREATE TABLE automation_log (id INTEGER PRIMARY KEY, action_type TEXT, description TEXT,"
                 " affected_count INTEGER, success INTEGER, executed_at TEXT)")
    conn.commit()
    conn.close()
    return db


def test_categorizer_job_response_retry_does_not_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        db = categorizer_db(tmp)
        run_categorizer(db)
        run_categorizer(db)
        conn = _open(db)
        found = rows(conn)
        assert len(found) == 1, len(found)
        task = found[0]
        assert (task["external_source"], task["external_id"]) == ("gmail-job-response", "g-9")
        assert task["title"] == "Job response: Interview invitation" and task["priority"] == 1
        assert task["project"] == "Job Applications" and task["category"] == "income"
        assert task["status"] == "todo" and task["created_at"] and task["updated_at"]
        conn.close()


def test_categorizer_uses_store():
    fake = FakeTaskStore()
    with tempfile.TemporaryDirectory() as tmp:
        run_categorizer(categorizer_db(tmp), fake)
    assert [c[:3] for c in fake.calls] == [("find_external", "gmail-job-response", "g-9"),
                                           ("upsert_external", "gmail-job-response", "g-9")]
    assert "status" not in fake.calls[1][3]
    assert set(fake.calls[1][4]) == {"created_at", "updated_at"}


def test_categorizer_keeps_an_existing_job_response_task():
    fake = FakeTaskStore({("gmail-job-response", "g-9"): {"id": 7, "title": "Edited by owner"}})
    with tempfile.TemporaryDirectory() as tmp:
        run_categorizer(categorizer_db(tmp), fake)
    assert [c[0] for c in fake.calls] == ["find_external"]


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

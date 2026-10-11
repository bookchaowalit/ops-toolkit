#!/usr/bin/env python3
"""Verify that activity enrichment does not claim progress or time."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "infra" / "scripts" / "cli" / "skills-cli.py"
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402  (the SQLite double of the roles-skills / goals cells)

SPEC = importlib.util.spec_from_file_location("skills_cli", MODULE_PATH)
assert SPEC and SPEC.loader
skills_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(skills_cli)


SCHEMA = """
CREATE TABLE progress_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER,
    activity_type TEXT,
    title TEXT,
    what_learned TEXT,
    hours_spent REAL,
    progress_before INTEGER,
    progress_after INTEGER,
    logged_at TEXT,
    created_at TEXT
);
"""


def test_enrichment_records_activity_only() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    skills_cli.open_cell_store = sqlite_cell_opener(conn)
    skills_cli.open_cell_store("roles-skills", "owner-cli")  # creates the legacy cell tables
    conn.execute(
        "INSERT INTO skills (id, name, slug, progress_percent, hours_invested, last_practiced, status)"
        " VALUES (1, 'AI Agents', 'ai-agents', 72, 12.5, '2026-08-01', 'practicing')"
    )
    goals = skills_cli.open_cell_store("goals", "owner-cli")
    goal = goals.create("goals", {"title": "Ship agents", "status": "active", "domain": "book-ai"})
    goals.create("goal-tasks", {
        "goal_id": goal["id"], "title": "Tool use loop", "status": "done", "completed_at": "2026-08-25 10:00:00",
    })

    skills_cli.enrich_skills()
    skills_cli.enrich_skills()

    skill = conn.execute("SELECT * FROM skills WHERE id = 1").fetchone()
    logs = conn.execute("SELECT * FROM progress_log WHERE skill_id = 1").fetchall()
    log = logs[0]
    assert skill["progress_percent"] == 72
    assert skill["hours_invested"] == 12.5
    assert skill["last_practiced"] == "2026-08-01"
    assert log["activity_type"] == "review"
    assert log["hours_spent"] is None
    assert log["progress_before"] is None
    assert log["progress_after"] is None
    assert len(logs) == 1
    conn.close()


if __name__ == "__main__":
    test_enrichment_records_activity_only()
    print("OK: skills enrichment activity-only checks passed")

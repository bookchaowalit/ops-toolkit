#!/usr/bin/env python3
"""Offline checks for explicit learning evidence recording."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "learning"))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import SqliteCellStore  # noqa: E402  (the SQLite double of the roles-skills cell)

from learning_evidence import record_evidence, resolve_skill, safe_evidence_path  # noqa: E402


SCHEMA = """
CREATE TABLE progress_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER,
    activity_type TEXT,
    title TEXT,
    description TEXT,
    what_learned TEXT,
    next_steps TEXT,
    evidence_url TEXT,
    progress_before INTEGER,
    progress_after INTEGER,
    logged_at TEXT,
    created_at TEXT
);
"""


def test_record_is_explicit_idempotent_and_does_not_change_progress() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    store = SqliteCellStore(conn, "roles-skills")
    store.create("skills", {"id": 1, "legacy_id": 1, "name": "AI Agents", "slug": "ai-agents", "progress_percent": 100})
    skill = resolve_skill(store, "ai-agents")
    assert resolve_skill(store, "1")["id"] == 1
    evidence = "learning/ai-agents/labs/first-solo-empire-agent.md"

    first = record_evidence(
        skill,
        evidence,
        "Completed tool-use lab",
        "Completed a read-only agent loop with source-of-truth checks.",
        "practice",
        store,
    )
    second = record_evidence(
        skill,
        evidence,
        "Completed tool-use lab",
        "Completed a read-only agent loop with source-of-truth checks.",
        "practice",
        store,
    )
    assert first["inserted"] is True
    assert second["inserted"] is False
    assert conn.execute("SELECT COUNT(*) FROM progress_log").fetchone()[0] == 1
    row = conn.execute("SELECT * FROM progress_log").fetchone()
    assert row["evidence_url"] == evidence
    assert row["progress_before"] == 100
    assert row["progress_after"] == 100
    assert store.get("skills", 1)["last_practiced"] is not None
    conn.close()


def test_evidence_path_blocks_secret_boundaries() -> None:
    try:
        safe_evidence_path("infra/.env")
    except ValueError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("secret boundary was not blocked")


if __name__ == "__main__":
    test_record_is_explicit_idempotent_and_does_not_change_progress()
    test_evidence_path_blocks_secret_boundaries()
    print("OK: learning evidence checks passed")

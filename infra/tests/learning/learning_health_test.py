#!/usr/bin/env python3
"""Offline checks for the learning health report."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "learning"))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))

import learning_health  # noqa: E402
from learning_health import build_report  # noqa: E402
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402  (the roles-skills cell over the legacy tables)


SCHEMA = """
CREATE TABLE progress_log (
    id INTEGER PRIMARY KEY,
    skill_id INTEGER NOT NULL,
    activity_type TEXT,
    title TEXT NOT NULL,
    evidence_url TEXT,
    logged_at TEXT
);
"""


def test_report_separates_activity_from_evidence() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    opener = sqlite_cell_opener(conn)
    for legacy_id, name, slug, progress, hours in (
        (1, "High Activity", "high-activity", 100, 10),
        (2, "Evidence Skill", "evidence-skill", 60, 4),
    ):
        opener("roles-skills").create("skills", {
            "legacy_id": legacy_id, "name": name, "slug": slug, "progress_percent": progress,
            "status": "practicing", "hours_invested": hours,
        })
    conn.executemany(
        "INSERT INTO progress_log VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, "review", "Activity enrichment (tasks)", None, "2026-08-25"),
            (2, 2, "practice", "Lab", "learning/evidence/lab.md", "2026-08-25"),
        ],
    )

    with patch.object(learning_health, "open_cell_store", opener):
        report = build_report()
    assert report["summary"]["skills"] == 2
    assert report["summary"]["skills_with_explicit_evidence"] == 1
    assert report["summary"]["high_activity_without_demonstrated_evidence"] == 1
    assert report["summary"]["auto_enrichment_logs"] == 1
    assert report["skills"][0]["proficiency_evidence_ready"] is False
    assert report["skills"][1]["evidence_state"] == "demonstrated"
    conn.close()


if __name__ == "__main__":
    test_report_separates_activity_from_evidence()
    print("OK: learning health report checks passed")

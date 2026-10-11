#!/usr/bin/env python3
"""Regression tests for the privacy-safe Daily/Weekly decision brief."""

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
BRIEF = ROOT / "infra" / "scripts" / "workflows" / "decision_brief.py"


RUNNER = ROOT / "infra" / "tests" / "support" / "run_with_sqlite_store.py"
LEGACY_DECISIONS = ROOT / "infra" / "tests" / "support" / "legacy_decision_tables.sql"


def run_brief(db_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SOLO_EMPIRE_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(BRIEF), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip())
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="solo-empire-decision-brief-") as temp_dir:
        temp = Path(temp_dir)
        db_path = temp / "brief-test.db"
        diary_root = temp / "entries"
        day_root = diary_root / "2026" / "08" / "08"
        day_root.mkdir(parents=True)
        private_diary = "PRIVATE DIARY CONTENT MUST NOT ENTER THE BRIEF"
        (day_root / "daily.md").write_text(private_diary, encoding="utf-8")

        conn = sqlite3.connect(db_path)
        conn.executescript(LEGACY_PORTFOLIO.read_text(encoding="utf-8"))
        conn.executescript(LEGACY_DECISIONS.read_text(encoding="utf-8"))
        # Intake sources/queue live in the Intake cell; the double reads these legacy tables.
        sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
        import sqlite_cell_store  # noqa: PLC0415

        sqlite_cell_store.apply_legacy_portfolio_tables(conn)
        conn.execute(
            "INSERT INTO goals (title, category, type, status, priority, due_date, progress_percent) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Private overdue goal", "business", "quarterly", "active", 1, "2026-08-01", 20),
        )
        conn.execute(
            "INSERT INTO goals (title, category, type, status, priority, due_date, progress_percent) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Private upcoming goal", "life", "weekly", "active", 2, "2026-08-10", 60),
        )
        conn.execute(
            """
            INSERT INTO decision_records
                (title, domain, decision, rationale, approval_required, approval_status,
                 action_type, action_status, privacy_class)
            VALUES (?, ?, ?, ?, 1, 'pending', 'financial', 'pending_approval', 'private')
            """,
            ("Private decision title", "finance", "Private decision text", "Private rationale"),
        )
        conn.commit()
        before_decisions = conn.execute("SELECT COUNT(*) FROM decision_records").fetchone()[0]
        conn.close()

        daily = run_brief(
            db_path,
            [
                "--cadence",
                "daily",
                "--as-of",
                "2026-08-08",
                "--diary-root",
                str(diary_root),
                "--json",
            ],
        )
        daily_payload = json.loads(daily.stdout)
        daily_brief = daily_payload["brief"]
        assert daily_payload["status"] == "preview"
        assert daily_brief["contract"] == "solo-empire.decision-brief.v1"
        assert daily_brief["window"]["days"] == 1
        assert "finance" not in daily_brief["context"]
        assert daily_brief["context"]["diary"]["markdown_entries"] == 1
        assert daily_brief["context"]["goals"]["overdue_goals"] == 1
        assert daily_brief["context"]["decisions"]["pending_approval"] == 1
        assert any(item["ref"] == "decisions:pending_approval" for item in daily_brief["priorities"])
        assert daily_brief["policy"]["automatic_execution"] is False
        assert daily_brief["context"]["goals"]["goal_titles_included"] is False
        assert private_diary not in daily.stdout
        assert "Private journal title" not in daily.stdout
        assert "Private overdue goal" not in daily.stdout
        assert "Private decision title" not in daily.stdout
        assert "Private rationale" not in daily.stdout

        output_dir = temp / "generated" / "briefings"
        weekly = run_brief(
            db_path,
            [
                "--cadence",
                "weekly",
                "--as-of",
                "2026-08-08",
                "--diary-root",
                str(diary_root),
                "--output-dir",
                str(output_dir),
                "--write",
                "--json",
            ],
        )
        weekly_payload = json.loads(weekly.stdout)
        weekly_brief = weekly_payload["brief"]
        assert weekly_payload["status"] == "written"
        assert weekly_brief["window"]["days"] == 7
        assert Path(weekly_payload["output_paths"]["markdown"]).exists()
        assert Path(weekly_payload["output_paths"]["json"]).exists()
        written_markdown = Path(weekly_payload["output_paths"]["markdown"]).read_text(encoding="utf-8")
        written_json = Path(weekly_payload["output_paths"]["json"]).read_text(encoding="utf-8")
        assert "Human review queue" in written_markdown
        assert private_diary not in written_markdown
        assert "Private account label" not in written_json
        assert "Private decision title" not in written_json

        conn = sqlite3.connect(db_path)
        after_decisions = conn.execute("SELECT COUNT(*) FROM decision_records").fetchone()[0]
        conn.close()
        assert after_decisions == before_decisions

    print("✅ decision-brief: daily/weekly cadence, privacy boundary, preview/write gate, and no-mutation contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

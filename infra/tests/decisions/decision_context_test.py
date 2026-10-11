#!/usr/bin/env python3
"""Regression tests for the privacy-safe cross-domain decision context pack."""

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
RUNNER = ROOT / "infra" / "tests" / "support" / "run_with_sqlite_store.py"
CONTEXT = ROOT / "infra" / "scripts" / "agent" / "decision_context.py"
ENGINE = ROOT / "infra" / "scripts" / "workflows" / "decision_engine.py"


def run_script(script: Path, db_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SOLO_EMPIRE_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(script), *args],
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
    with tempfile.TemporaryDirectory(prefix="solo-empire-decision-context-") as temp_dir:
        temp = Path(temp_dir)
        db_path = temp / "context-test.db"
        diary_root = temp / "entries"
        day_root = diary_root / "2026" / "08" / "08"
        day_root.mkdir(parents=True)
        private_diary = "PRIVATE DIARY CONTENT MUST NOT ENTER THE PACK"
        (day_root / "daily.md").write_text(private_diary, encoding="utf-8")

        conn = sqlite3.connect(db_path)
        conn.executescript(LEGACY_PORTFOLIO.read_text(encoding="utf-8"))
        conn.executescript(LEGACY_TASKS.read_text(encoding="utf-8"))
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
        conn.commit()
        conn.close()

        result = run_script(CONTEXT, db_path, [
            "--as-of", "2026-08-08", "--window-days", "7",
            "--diary-root", str(diary_root), "--json",
        ])
        payload = json.loads(result.stdout)
        assert payload["contract"] == "solo-empire.decision-context.v1"
        assert "finance" not in payload
        assert payload["diary"]["markdown_entries"] == 1
        assert payload["goals"]["active_goals"] == 2
        assert payload["goals"]["overdue_goals"] == 1
        assert payload["goals"]["due_in_window"] == 1
        assert payload["goals"]["goal_titles_included"] is False
        assert all("title" not in ref for ref in payload["goals"]["references"])
        assert private_diary not in result.stdout
        assert "Private overdue goal" not in result.stdout

        engine = run_script(ENGINE, db_path, [
            "--dry-run", "--with-context", "--as-of", "2026-08-08",
            "--window-days", "7", "--diary-root", str(diary_root), "--json",
        ])
        engine_payload = json.loads(engine.stdout)
        assert engine_payload["context"]["contract"] == "solo-empire.decision-context.v1"
        assert engine_payload["context"]["policy"]["raw_diary_text_included"] is False
        assert private_diary not in engine.stdout

    print("✅ decision-context: aggregate Diary/Goals/Decisions pack, bounded window, privacy policy, and decision-engine integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

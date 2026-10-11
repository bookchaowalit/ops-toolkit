#!/usr/bin/env python3
"""
R-batch Analytics E2E Tests.

Tests the 4 R-batch analytics scripts:
  1. revenue_analytics.py  — valid JSON, expected keys (overview, by_source, monthly_trends, growth)
  2. habit_analytics.py    — valid JSON, expected keys (overall_score, habits, weekly_pattern, category_breakdown)
  3. goal_velocity.py      — valid JSON, expected keys (portfolio, goals)
  4. monthly_report.py     — valid JSON, expected keys (revenue, goals, habits, tasks, at_risk_goals)

Usage:
    python3 infra/tests/quality/analytics_test.py
    python3 infra/tests/quality/analytics_test.py --verbose

Exit code 0 = all pass, 1 = some failures.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

# Add utils directory to path for _env import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'utils'))
from _env import DB_PATH, PROJECT_ROOT, REPORTS_DIR

SUPPORT_DIR = Path(__file__).resolve().parents[1] / "support"
sys.path.insert(0, str(SUPPORT_DIR))
from sqlite_habits_store import SqliteHabitsStore  # noqa: E402  (also isolates the habits API client switch)

SCRIPTS_DIR = PROJECT_ROOT / "infra" / "scripts" / "utils"
DASHBOARD_DIR = REPORTS_DIR

VERBOSE = False
passed = 0
failed = 0
errors = []


def log_pass(msg: str):
    global passed
    passed += 1
    if VERBOSE:
        print(f"  PASS: {msg}")


def log_fail(msg: str):
    global failed
    failed += 1
    errors.append(msg)
    print(f"  FAIL: {msg}")


_HABIT_DB_DIR = tempfile.TemporaryDirectory(prefix="analytics-habits-")
HABIT_DB = Path(_HABIT_DB_DIR.name) / "operations.db"
HABIT_SCRIPTS = {"habit_analytics.py", "monthly_report.py"}
HABIT_SCRIPTS |= {"goal_velocity.py"}  # reads goals through the cell store double
HABIT_SCRIPTS |= {"revenue_analytics.py"}  # reads revenue through the CRM ledger double


def seed_habit_db() -> None:
    """A temporary copy of the database plus habits seeded through the SQLite habits double."""
    if HABIT_DB.exists():
        return
    if DB_PATH.exists():
        shutil.copy(DB_PATH, HABIT_DB)
    conn = sqlite3.connect(HABIT_DB)
    store = SqliteHabitsStore(conn)
    habit = store.create_habit({"name": "Fixture Habit", "slug": "fixture-habit", "category": "health",
                                "frequency": "daily", "status": "active", "priority": 1,
                                "schedule_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]})
    today = datetime.now().date()
    for offset in range(3):
        store.log_habit(habit["id"], (today - timedelta(days=offset)).isoformat())
    conn.close()


def habit_command(script: Path, extra: list) -> tuple[list, dict]:
    """Habit-reading scripts run under the SQLite habits double over the temporary database."""
    env = {**os.environ, "SOLO_EMPIRE_DB_PATH": str(HABIT_DB), "EMPIRE_CLIENT_CONFIG": "/nonexistent"}
    return [sys.executable, str(SUPPORT_DIR / "run_with_sqlite_store.py"), str(script), *extra], env


def run_script(script_name: str, args: list = None) -> dict:
    """Run a script with --json and return parsed output."""
    script = SCRIPTS_DIR / script_name
    if not script.exists():
        raise FileNotFoundError(f"{script_name} not found at {script}")

    cmd = [sys.executable, str(script), "--json"] + (args or [])
    env = None
    if script_name in HABIT_SCRIPTS:
        seed_habit_db()
        cmd, env = habit_command(script, ["--json"] + (args or []))
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=str(PROJECT_ROOT), timeout=30, env=env
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{script_name} exited with code {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return json.loads(result.stdout)


def assert_key(data: dict, key: str, script: str):
    """Assert a key exists in the JSON output."""
    if key in data:
        log_pass(f"{script}: has key '{key}'")
    else:
        log_fail(f"{script}: missing key '{key}' (got: {list(data.keys())})")


def assert_type(data: dict, key: str, expected_type, script: str):
    """Assert a key has the expected type."""
    if key not in data:
        log_fail(f"{script}: key '{key}' missing — can't check type")
        return
    type_name = expected_type.__name__ if not isinstance(expected_type, tuple) else '/'.join(t.__name__ for t in expected_type)
    if isinstance(data[key], expected_type):
        log_pass(f"{script}: '{key}' is {type_name}")
    else:
        log_fail(
            f"{script}: '{key}' expected {type_name}, "
            f"got {type(data[key]).__name__}"
        )


# ─── Test 1: revenue_analytics.py ───────────────────────────────────────

def test_revenue_analytics():
    print("\n[1/4] revenue_analytics.py")
    try:
        data = run_script("revenue_analytics.py")

        # Top-level keys
        assert_key(data, "timestamp", "revenue_analytics")
        assert_key(data, "overview", "revenue_analytics")
        assert_key(data, "by_source", "revenue_analytics")
        assert_key(data, "monthly_trends", "revenue_analytics")
        assert_key(data, "growth", "revenue_analytics")

        # Timestamp is valid ISO
        if "timestamp" in data:
            try:
                datetime.fromisoformat(data["timestamp"])
                log_pass("revenue_analytics: timestamp is valid ISO")
            except ValueError:
                log_fail(f"revenue_analytics: invalid timestamp '{data['timestamp']}'")

        # Overview has MRR
        if "overview" in data and isinstance(data["overview"], dict):
            assert_key(data["overview"], "mrr", "revenue_analytics.overview")
            assert_type(data["overview"], "mrr", (int, float), "revenue_analytics.overview")

        # by_source is a list
        assert_type(data, "by_source", list, "revenue_analytics")

        # monthly_trends is a list
        assert_type(data, "monthly_trends", list, "revenue_analytics")

    except FileNotFoundError as e:
        log_fail(str(e))
    except RuntimeError as e:
        log_fail(f"revenue_analytics: {e}")
    except json.JSONDecodeError as e:
        log_fail(f"revenue_analytics: invalid JSON — {e}")


# ─── Test 2: habit_analytics.py ─────────────────────────────────────────

def test_habit_analytics():
    print("\n[2/4] habit_analytics.py")
    try:
        data = run_script("habit_analytics.py")

        assert_key(data, "timestamp", "habit_analytics")
        assert_key(data, "overall_score", "habit_analytics")
        assert_key(data, "habits", "habit_analytics")
        assert_key(data, "weekly_pattern", "habit_analytics")
        assert_key(data, "category_breakdown", "habit_analytics")

        # overall_score is numeric
        assert_type(data, "overall_score", (int, float), "habit_analytics")

        # habits is a list
        assert_type(data, "habits", list, "habit_analytics")

        # Each habit has expected fields
        if "habits" in data and isinstance(data["habits"], list) and len(data["habits"]) > 0:
            h = data["habits"][0]
            for key in ("name", "current_streak", "completion_rate_30"):
                assert_key(h, key, "habit_analytics.habits[0]")

    except FileNotFoundError as e:
        log_fail(str(e))
    except RuntimeError as e:
        log_fail(f"habit_analytics: {e}")
    except json.JSONDecodeError as e:
        log_fail(f"habit_analytics: invalid JSON — {e}")


# ─── Test 3: goal_velocity.py ───────────────────────────────────────────

def test_goal_velocity():
    print("\n[3/4] goal_velocity.py")
    try:
        data = run_script("goal_velocity.py")

        assert_key(data, "timestamp", "goal_velocity")
        assert_key(data, "portfolio", "goal_velocity")
        assert_key(data, "goals", "goal_velocity")

        # goals is a list
        assert_type(data, "goals", list, "goal_velocity")

        # Each goal has expected fields
        if "goals" in data and isinstance(data["goals"], list) and len(data["goals"]) > 0:
            g = data["goals"][0]
            for key in ("title", "velocity_per_day", "risk"):
                assert_key(g, key, "goal_velocity.goals[0]")

        # Portfolio summary has expected keys
        if "portfolio" in data and isinstance(data["portfolio"], dict):
            for key in ("total", "active", "avg_progress"):
                assert_key(data["portfolio"], key, "goal_velocity.portfolio")

    except FileNotFoundError as e:
        log_fail(str(e))
    except RuntimeError as e:
        log_fail(f"goal_velocity: {e}")
    except json.JSONDecodeError as e:
        log_fail(f"goal_velocity: invalid JSON — {e}")


# ─── Test 4: monthly_report.py ──────────────────────────────────────────

def test_monthly_report():
    print("\n[4/4] monthly_report.py")
    try:
        data = run_script("monthly_report.py")

        assert_key(data, "month", "monthly_report")
        assert_key(data, "revenue", "monthly_report")
        assert_key(data, "goals", "monthly_report")
        assert_key(data, "habits", "monthly_report")
        assert_key(data, "tasks", "monthly_report")
        assert_key(data, "at_risk_goals", "monthly_report")

        # month is a string like 2026-06
        if "month" in data:
            if isinstance(data["month"], str) and len(data["month"]) == 7:
                log_pass("monthly_report: month format is YYYY-MM")
            else:
                log_fail(f"monthly_report: unexpected month format: {data['month']}")

        # at_risk_goals is a list
        assert_type(data, "at_risk_goals", list, "monthly_report")

    except FileNotFoundError as e:
        log_fail(str(e))
    except RuntimeError as e:
        log_fail(f"monthly_report: {e}")
    except json.JSONDecodeError as e:
        log_fail(f"monthly_report: invalid JSON — {e}")


def main():
    global VERBOSE
    VERBOSE = "--verbose" in sys.argv

    print("=" * 60)
    print("R-batch Analytics E2E Tests")
    print("=" * 60)

    test_revenue_analytics()
    test_habit_analytics()
    test_goal_velocity()
    test_monthly_report()

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    if errors:
        print("\nFailures:")
        for e in errors:
            print(f"  - {e}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

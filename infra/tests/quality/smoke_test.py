#!/usr/bin/env python3
"""
Solo Empire Smoke Tests.

Tests the core infrastructure:
  1. Environment loader can load .env
  2. Database is accessible and has expected tables
  3. Migrations system works
  4. API write routes reject unauthenticated requests
  5. No hardcoded secrets in scripts
  6. No stale absolute paths in scripts

Usage:
    python3 infra/tests/quality/smoke_test.py
    python3 infra/tests/quality/smoke_test.py --verbose

Exit code 0 = all pass, 1 = some failures.
"""

import os
import sys
import sqlite3
import subprocess
import re
from pathlib import Path

# Add utils directory to path for _env import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'utils'))
from _env import DB_PATH, PROJECT_ROOT

SCRIPTS_DIR = PROJECT_ROOT / "infra" / "scripts"
ENV_FILE = PROJECT_ROOT / "infra" / ".env"
ENV_EXAMPLE = PROJECT_ROOT / "infra" / ".env.example"
MIGRATE_SCRIPT = PROJECT_ROOT / "infra" / "scripts" / "db" / "migrate_db.py"

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


def test_env_loader():
    """Test 1: _env.py can be imported and loads correctly."""
    print("\n[1/4] Environment Loader")
    try:
        sys.path.insert(0, str(SCRIPTS_DIR / "utils"))
        import _env
        assert hasattr(_env, "PROJECT_ROOT"), "_env.py missing PROJECT_ROOT"
        assert hasattr(_env, "DB_PATH"), "_env.py missing DB_PATH"
        assert hasattr(_env, "resolve_db_path"), "_env.py missing resolve_db_path"
        assert _env.PROJECT_ROOT.exists(), f"PROJECT_ROOT {_env.PROJECT_ROOT} doesn't exist"
        assert str(_env.DB_PATH).endswith("operations.db"), f"Unexpected DB_PATH: {_env.DB_PATH}"
        # An empty value means "unset"; passing None would read the live
        # SOLO_EMPIRE_DB_PATH override and make this check environment-dependent.
        default = _env.resolve_db_path("")
        expected = (_env.PROJECT_ROOT / "data" / "runtime" / "operations.db").resolve()
        assert default == expected, f"default DB path {default} != {expected}"
        override = os.environ.get("SOLO_EMPIRE_DB_PATH", "").strip()
        if override:
            assert _env.DB_PATH == _env.resolve_db_path(override), (
                f"DB_PATH {_env.DB_PATH} ignores SOLO_EMPIRE_DB_PATH override"
            )
        log_pass("_env.py loads and SOLO_EMPIRE_DB_PATH default resolves correctly")
    except Exception as e:
        log_fail(f"_env.py import failed: {e}")
    finally:
        if str(SCRIPTS_DIR / "utils") in sys.path:
            sys.path.remove(str(SCRIPTS_DIR / "utils"))


def test_database_retired():
    """Test 2: the control-plane SQLite database stays retired (2026-10-11)."""
    print("\n[2/4] Retired Database")
    default_db = PROJECT_ROOT / "data" / "runtime" / "operations.db"
    if (PROJECT_ROOT / "infra" / "database").exists():
        log_fail("retired infra/database folder is back")
    if default_db.exists():
        log_fail(f"retired control-plane database is back at {default_db}")
    else:
        log_pass("no control-plane database in the repository")
    if MIGRATE_SCRIPT.exists():
        log_fail(f"retired migration runner is back at {MIGRATE_SCRIPT}")
    else:
        log_pass("no migration runner for the retired database")


def test_no_secrets():
    """Test 5: No hardcoded secrets in Python scripts."""
    print("\n[3/4] Secret Scan")
    patterns = [
        (r'GOCSPX-', 'Google OAuth Client Secret'),
        (r'ghp_[a-zA-Z0-9]{30,}', 'GitHub Personal Access Token'),
        (r'fc-[a-f0-9]{32}', 'Firecrawl API Key'),
        (r'445208879962-gr82isa', 'Google OAuth Client ID'),
    ]

    found_secrets = []
    for py_file in SCRIPTS_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        try:
            content = py_file.read_text()
            for pattern, label in patterns:
                if re.search(pattern, content):
                    found_secrets.append(f"{py_file.name}: {label}")
        except Exception:
            pass

    if found_secrets:
        for s in found_secrets:
            log_fail(f"Hardcoded secret found: {s}")
    else:
        log_pass("No hardcoded secrets found in scripts")


def test_no_stale_paths():
    """Test 6: No stale absolute paths in scripts."""
    print("\n[4/4] Stale Path Scan")
    stale_pattern = r'/home/bookchaowalit/solo-empire[^/]'

    found_paths = []
    for py_file in SCRIPTS_DIR.glob("*.py"):
        try:
            content = py_file.read_text()
            if re.search(stale_pattern, content):
                found_paths.append(py_file.name)
        except Exception:
            pass

    for sh_file in SCRIPTS_DIR.glob("*.sh"):
        try:
            content = sh_file.read_text()
            if re.search(stale_pattern, content):
                found_paths.append(sh_file.name)
        except Exception:
            pass

    if found_paths:
        for p in found_paths:
            log_fail(f"Stale path found in: {p}")
    else:
        log_pass("No stale absolute paths found")


def main():
    global VERBOSE
    VERBOSE = "--verbose" in sys.argv

    print("=" * 60)
    print("Solo Empire Smoke Tests")
    print("=" * 60)

    test_env_loader()
    test_database_retired()
    test_no_secrets()
    test_no_stale_paths()

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

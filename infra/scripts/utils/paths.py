#!/usr/bin/env python3
"""
Canonical path definitions for Solo Empire.

This module is the single source of truth for all project paths.
All scripts should import from here instead of calculating paths manually.

Usage:
    from paths import PROJECT_ROOT, DATABASE_PATH, MCP_SERVER_DIR
"""

from pathlib import Path

try:
    # Package import used by CI and callers such as
    # ``from infra.scripts.utils.paths import validate_paths``.
    from ._env import DB_PATH, PROJECT_ROOT
except ImportError:
    # Direct script execution keeps compatibility with the repository's
    # run-python3.sh wrapper, which places this directory on sys.path.
    from _env import DB_PATH, PROJECT_ROOT

# Canonical paths - these are the ONLY correct locations
DATABASE_PATH = DB_PATH
DATABASE_DIR = DATABASE_PATH.parent

MCP_SERVER_DIR = PROJECT_ROOT / "infra" / "mcp-server"
SCRIPTS_DIR = PROJECT_ROOT / "infra" / "scripts"
TESTS_DIR = PROJECT_ROOT / "infra" / "tests"
DATA_DIR = PROJECT_ROOT / "data"
GENERATED_DIR = DATA_DIR / "generated"
BRIEFINGS_DIR = GENERATED_DIR / "briefings"
PAGES_DIR = GENERATED_DIR / "pages"
REPORTS_DIR = GENERATED_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"
RAW_DATA_DIR = DATA_DIR / "raw"
RUNTIME_DIR = DATA_DIR / "runtime"

# Validation function
def validate_paths():
    """Validate that all canonical paths exist and are accessible."""
    errors = []

    if not PROJECT_ROOT.exists():
        errors.append(f"PROJECT_ROOT does not exist: {PROJECT_ROOT}")

    if not MCP_SERVER_DIR.exists():
        errors.append(f"MCP_SERVER_DIR does not exist: {MCP_SERVER_DIR}")

    if not SCRIPTS_DIR.exists():
        errors.append(f"SCRIPTS_DIR does not exist: {SCRIPTS_DIR}")

    if not TESTS_DIR.exists():
        errors.append(f"TESTS_DIR does not exist: {TESTS_DIR}")

    return errors


def get_relative_path(path: Path) -> str:
    """Get path relative to project root for display."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    # Quick validation when run directly
    errors = validate_paths()
    if errors:
        print("❌ Path validation errors:")
        for error in errors:
            print(f"  - {error}")
        exit(1)
    else:
        print("✅ All canonical paths validated")
        print(f"  PROJECT_ROOT: {PROJECT_ROOT}")
        print(f"  DATABASE_PATH: {DATABASE_PATH}")
        print(f"  MCP_SERVER_DIR: {MCP_SERVER_DIR}")

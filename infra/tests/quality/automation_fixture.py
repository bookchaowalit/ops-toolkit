"""Run automation E2E checks in a disposable, credential-free workspace."""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def run_isolated(test_path: Path, args: list[str]) -> int:
    source = test_path.resolve().parents[3]
    with tempfile.TemporaryDirectory(prefix="solo-automation-") as directory:
        root = Path(directory)
        for relative in ("infra/scripts",):
            shutil.copytree(
                source / relative, root / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "node_modules", ".git"),
            )
        for relative in (
            "infra/package.json",
            "infra/tests/quality/automation_test.py",
            "infra/tests/fixtures/automation/sitecustomize.py",
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
        # Every domain lives in a cell API; the SQLite store doubles (and their legacy DDL) stand in for them.
        shutil.copytree(
            source / "infra/tests/support", root / "infra/tests/support",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for relative in ("data/backups", "data/generated/briefings", "home"):
            (root / relative).mkdir(parents=True, exist_ok=True)
        db_path = root / "data/runtime/operations.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            # Tasks, goals, habits, ledger, ... live in cell APIs and the cron runner is retired: the store
            # doubles read these legacy tables instead of the live APIs.
            for ddl in sorted((root / "infra/tests/support").glob("legacy_*_tables.sql")):
                conn.executescript(ddl.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO revenue(source_type, source_name, amount, currency, revenue_date, received) "
                "VALUES ('service', 'Automation fixture', 1000, 'THB', '2026-10-01', 1)"
            )
            conn.execute("INSERT INTO tasks(title, status, priority) VALUES ('Automation fixture', 'todo', 3)")
            conn.commit()
        finally:
            conn.close()
        (root / ".automation-fixture").write_text("isolated-offline-test\n", encoding="utf-8")
        allowed_names = {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "LANG", "LC_ALL", "VIRTUAL_ENV", "LD_LIBRARY_PATH",
        }
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed_names}
        env.update({
            "HOME": str(root / "home"),
            "USERPROFILE": str(root / "home"),
            "APPDATA": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "home"),
            "SOLO_EMPIRE_AUTOMATION_FIXTURE": str(root),
            "SOLO_EMPIRE_DB_PATH": str(db_path),
            "SOLO_EMPIRE_TEST_DOUBLES": "1",
            "PYTHONPATH": str(root / "infra/tests/fixtures/automation"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "SECRETS_PROVIDER": "env",
            "SECRETS_FALLBACK_ENV": "false",
        })
        return subprocess.run(
            [sys.executable, str(root / "infra/tests/quality/automation_test.py"), *args],
            cwd=root, env=env, check=False,
        ).returncode

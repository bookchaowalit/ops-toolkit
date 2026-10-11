#!/usr/bin/env python3
"""The task writer guard passes on the repository and catches new direct writes."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
import task_writer_guard as guard  # noqa: E402


def test_repository_passes():
    report = guard.scan(guard.tracked_files())
    assert report["ok"], report


def test_new_direct_write_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "infra/scripts").mkdir(parents=True)
        (root / "infra/scripts/new_writer.py").write_text('x = 1\nconn.execute("""UPDATE\n    tasks SET status = ?""")\n')
        (root / "infra/scripts/links.py").write_text('conn.execute("INSERT INTO tasks_goal_links VALUES (1)")\n')
        (root / "infra/tests").mkdir()
        (root / "infra/tests/x_test.py").write_text('conn.execute("DELETE FROM tasks")\n')
        files = ["infra/scripts/new_writer.py", "infra/scripts/links.py", "infra/tests/x_test.py"]
        with patch.object(guard, "ROOT", root), patch.object(guard, "PENDING", {}):
            report = guard.scan(files)
    assert report["violations"] == {"infra/scripts/new_writer.py": [2]}
    assert not report["ok"]


def test_stale_allowlist_entry_fails():
    with patch.object(guard, "PENDING", {"infra/gone.py": "retired"}):
        report = guard.scan([])
    assert report["stale_allowlist"] == ["infra/gone.py"] and not report["ok"]


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

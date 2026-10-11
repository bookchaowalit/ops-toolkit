#!/usr/bin/env python3
"""Offline tests for the active orchestration guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

from active_orchestration_guard import (  # noqa: E402
    is_active_path,
    is_excluded_path,
    scan_text,
)


def main() -> int:
    assert not scan_text("task docs:check\ntask test\n")
    assert len(scan_text("npm --prefix infra run docs:check\n")) == 1
    assert len(scan_text("npm --prefix infra test\n")) == 1

    assert is_active_path("docs/references/QUICK-START.md")
    assert is_active_path("infra/scripts/utils/example.py")
    assert is_active_path("infra/tasks/finance.yml")
    assert is_excluded_path("repository-catalog/audits/old-run.md")
    assert is_excluded_path("docs/CHANGELOG.md")
    assert not is_active_path("projects/product/engineering/book-dev/app/package.json")

    script = ROOT / "infra" / "scripts" / "utils" / "active_orchestration_guard.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        print(output)
        raise AssertionError(f"active orchestration guard failed: {result.returncode}")

    print("Active orchestration guard tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

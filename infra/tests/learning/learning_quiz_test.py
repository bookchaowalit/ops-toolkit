#!/usr/bin/env python3
"""Offline checks for the learning-backed quiz workspace."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import KNOWLEDGE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(KNOWLEDGE_ROOT / "quiz", "solo-knowledge checkout (SOLO_KNOWLEDGE_ROOT)")
RUNNER = ROOT / "infra" / "scripts" / "learning" / "quiz.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    checks = [
        ("--check", re.compile(r"validated (\d+) quiz files")),
        ("--list", "ai-agents"),
        ("--list", "business-systems"),
        ("--list", "platform-engineering"),
        ("--list", "systems-networking"),
    ]
    for argument, expected in checks:
        result = run(argument)
        if result.returncode != 0:
            print(f"FAIL: quiz runner {argument} exited {result.returncode}")
            print(result.stdout)
            print(result.stderr)
            return 1
        if isinstance(expected, re.Pattern):
            match = expected.search(result.stdout)
            # The quiz set grows over time; require at least the original six.
            if not match or int(match.group(1)) < 6:
                print(f"FAIL: quiz runner {argument} did not match {expected.pattern!r} with >= 6 files")
                print(result.stdout)
                return 1
        elif expected not in result.stdout:
            print(f"FAIL: quiz runner {argument} did not include {expected!r}")
            print(result.stdout)
            return 1

    print("OK: learning-backed quiz runner checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

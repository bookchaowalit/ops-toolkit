#!/usr/bin/env python3
"""Fail when active repository files reintroduce root npm orchestration.

The root Taskfile composes owner adapters under infra/tasks. npm remains valid
inside Node package boundaries, but active source and operating documentation
must not route cross-runtime work through the old infra-prefixed npm forms.

Historical evidence, changelogs, dated snapshots, private diary entries, and
nested Book Dev repositories are intentionally outside this guard's scope.

Usage:
    task orchestration:check
    python3 infra/scripts/utils/active_orchestration_guard.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from _env import PROJECT_ROOT  # noqa: E402


ACTIVE_PREFIXES = (
    ".github",
    "dashboard",
    "diary",
    "docs",
    "finance",
    "goals",
    "habits",
    "infra/platform",
    "infra/scripts",
    "infra/tasks",
    "integrations",
    "learning",
    "media",
    "operations",
    "opportunities",
    "projects",
    "repository-catalog",
    "roles",
    "routines",
    "skills",
    "systems",
    "tasks",
    "templates",
    "ventures",
)
ACTIVE_FILES = {
    "AGENTS.md",
    "Makefile",
    "README.md",
    "Taskfile.yml",
    "infra/.env.example",
    "infra/README.md",
    "infra/cli.ts",
}

# These boundaries preserve original commands as evidence or belong to a
# separate repository/runtime. They are not active command guidance for the
# Solo Empire root.
EXCLUDED_PREFIXES = (
    "diary/entries/",
    "projects/product/engineering/book-dev/",
    "repository-catalog/audits/",
    "repository-catalog/snapshots/",
    "roles/pilots/",
    "data/",
)
EXCLUDED_FILES = {
    "ENGINEERING_AUDIT.md",
    "docs/CHANGELOG.md",
    "opportunities/research/MARKET-SIGNAL-RESEARCH-V1.md",
}

# Build the legacy forms from pieces so this guard cannot match its own
# source while scanning infra/scripts.
LEGACY_PATTERNS = (
    (
        "npm-prefix-infra-run",
        re.compile(r"npm\s+--prefix\s+infra\s+" + r"run\b"),
    ),
    (
        "npm-prefix-infra-test",
        re.compile(r"npm\s+--prefix\s+infra\s+" + r"test\b"),
    ),
)


def relative_path(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def is_excluded_path(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix()
    return normalized in EXCLUDED_FILES or any(
        normalized.startswith(prefix) for prefix in EXCLUDED_PREFIXES
    )


def is_active_path(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix()
    if is_excluded_path(normalized):
        return False
    if normalized in ACTIVE_FILES:
        return True
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in ACTIVE_PREFIXES
    )


def repository_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("git ls-files failed; run this command inside the repository")

    paths = [item for item in result.stdout.decode("utf-8").split("\0") if item]
    return [
        PROJECT_ROOT / path
        for path in paths
        if is_active_path(path) and (PROJECT_ROOT / path).is_file()
    ]


def scan_text(text: str) -> list[dict[str, int | str]]:
    findings: list[dict[str, int | str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern_name, pattern in LEGACY_PATTERNS:
            if pattern.search(line):
                findings.append({"pattern": pattern_name, "line": line_number})
    return findings


def audit() -> dict[str, object]:
    violations: list[dict[str, int | str]] = []
    scanned_files = 0
    for path in repository_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        scanned_files += 1
        for finding in scan_text(text):
            violations.append({"path": relative_path(path), **finding})
    return {
        "scanned_files": scanned_files,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable output")
    args = parser.parse_args()

    report = audit()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Active orchestration guard")
        print(f"Scanned {report['scanned_files']} active text files")
        violations = report["violations"]
        if violations:
            print("FAIL: legacy root npm orchestration found:")
            for item in violations:
                print(f"  - {item['path']}:{item['line']} ({item['pattern']})")
            print("Use the matching root Taskfile command instead.")
        else:
            print("PASS: active source and docs use Task for root orchestration")

    return 1 if report["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

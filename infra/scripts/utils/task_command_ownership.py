#!/usr/bin/env python3
"""Report root package-script ownership and Task parity.

The root ``infra/package.json`` remains a compatibility/package boundary. The
root Taskfile is the canonical interface for commands that cross runtime
boundaries. This report makes that relationship explicit and fails when a
package script has no corresponding root Task task.

Usage:
    task task:ownership
    task task:ownership -- --json
    task task:ownership -- --write
    task task:parity
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from _env import PROJECT_ROOT  # noqa: E402


PACKAGE_PATH = PROJECT_ROOT / "infra" / "package.json"
REPORT_PATH = PROJECT_ROOT / "data" / "indexes" / "task-command-ownership.json"
SCHEMA_VERSION = "solo-empire.task-command-ownership.v1"


def load_package_scripts() -> dict[str, str]:
    try:
        package = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {PACKAGE_PATH.relative_to(PROJECT_ROOT)}: {exc}") from exc
    scripts = package.get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        raise RuntimeError("infra/package.json has no scripts object")
    invalid = sorted(name for name, command in scripts.items() if not isinstance(command, str))
    if invalid:
        raise RuntimeError(f"package scripts must be strings: {', '.join(invalid)}")
    return {str(name): command for name, command in scripts.items()}


def task_binary() -> str:
    candidates = [
        os.environ.get("SOLO_EMPIRE_TASK", ""),
        shutil.which("task") or "",
        str(Path.home() / ".local" / "bin" / "task"),
        str(Path.home() / "go" / "bin" / "task"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("task binary not found; run task doctor and install the pinned version")


def load_task_names() -> set[str]:
    result = subprocess.run(
        [task_binary(), "--list", "--json"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"task --list --json failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"task --list --json returned invalid JSON: {exc}") from exc
    rows = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("task --list --json did not return a tasks list")
    names = {
        row.get("name")
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    if not names:
        raise RuntimeError("root Taskfile returned no tasks")
    return names


def runtime_labels(command: str) -> list[str]:
    labels: set[str] = set()
    if re.search(r"\b(?:npm|npx|node|tsx|tsc)\b", command):
        labels.add("node")
    if re.search(r"(?:run-python3\.sh|\bpython(?:3)?\b|\.py\b)", command):
        labels.add("python")
    if re.search(r"\bgo(?:\s|/)|go\.mod", command):
        labels.add("go")
    if re.search(r"\bbash\b|\.sh\b", command) and "python" not in labels:
        labels.add("shell")
    if re.search(r"\b(?:docker|podman|kubectl|helm)\b", command):
        labels.add("container")
    if not labels:
        labels.add("other")
    return sorted(labels)


def runtime_label(command: str) -> str:
    labels = runtime_labels(command)
    return labels[0] if len(labels) == 1 else "polyglot"


def build_report(scripts: dict[str, str], task_names: set[str]) -> dict[str, Any]:
    missing = sorted(set(scripts) - task_names)
    task_only = sorted(task_names - set(scripts))
    entries = [
        {
            "name": name,
            "canonical_owner": "task" if name in task_names else "legacy",
            "runtime": runtime_label(command),
            "runtime_labels": runtime_labels(command),
            "command": command,
        }
        for name, command in sorted(scripts.items())
    ]
    runtime_counts = Counter(entry["runtime"] for entry in entries)
    return {
        "schema_version": SCHEMA_VERSION,
        "package_path": str(PACKAGE_PATH.relative_to(PROJECT_ROOT)),
        "taskfile_path": "Taskfile.yml",
        "package_script_count": len(scripts),
        "task_count": len(task_names),
        "mapped_script_count": len(scripts) - len(missing),
        "missing_task_names": missing,
        "task_only_names": task_only,
        "runtime_counts": dict(sorted(runtime_counts.items())),
        "scripts": entries,
    }


def write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def print_human(report: dict[str, Any]) -> None:
    missing = report["missing_task_names"]
    print("Task command ownership report")
    print(f"  package scripts: {report['package_script_count']}")
    print(f"  root Task tasks: {report['task_count']}")
    print(f"  mapped to Task: {report['mapped_script_count']}")
    print(f"  missing Task mappings: {len(missing)}")
    print(f"  Task-only commands: {len(report['task_only_names'])}")
    runtime_summary = ", ".join(
        f"{name}={count}" for name, count in report["runtime_counts"].items()
    )
    print(f"  implementation runtimes: {runtime_summary}")
    if missing:
        print("  legacy/unmapped scripts:")
        for name in missing:
            print(f"    - {name}")
    else:
        print("  status: READY — every infra package script has a root Task mapping")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when any package script lacks a Task mapping")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output")
    parser.add_argument("--write", action="store_true", help="Write the generated JSON report")
    args = parser.parse_args()

    try:
        report = build_report(load_package_scripts(), load_task_names())
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.write:
        write_report(report)
        if not args.json:
            print(f"Wrote {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(report)

    return 1 if args.check and report["missing_task_names"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

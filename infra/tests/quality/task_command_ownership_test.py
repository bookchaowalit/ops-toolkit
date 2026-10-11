#!/usr/bin/env python3
"""Offline tests for package-script ownership and Task parity."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

from task_command_ownership import (  # noqa: E402
    build_report,
    load_package_scripts,
    load_task_names,
    runtime_label,
)


def check_task_output_encoding() -> None:
    completed = subprocess.CompletedProcess(
        args=["task", "--list", "--json"],
        returncode=0,
        stdout=json.dumps(
            {"tasks": [{"name": "docs:check", "description": "ตรวจเอกสาร 🏰"}]},
            ensure_ascii=False,
        ),
        stderr="",
    )
    with patch("task_command_ownership.task_binary", return_value="task"), patch(
        "task_command_ownership.subprocess.run", return_value=completed
    ) as run:
        assert load_task_names() == {"docs:check"}
        assert run.call_args is not None
        assert run.call_args.kwargs["encoding"] == "utf-8"


def main() -> int:
    check_task_output_encoding()
    assert runtime_label("npm run build") == "node"
    assert runtime_label("bash scripts/setup/run-python3.sh scripts/check.py") == "python"
    assert runtime_label("go run ./cmd") == "go"
    assert runtime_label("npm run build && bash scripts/check.sh") == "polyglot"

    scripts = load_package_scripts()
    tasks = load_task_names()
    report = build_report(scripts, tasks)
    assert report["package_script_count"] >= 100  # floor only; retirements shrink the set, parity checks below are the real guard
    assert report["mapped_script_count"] == report["package_script_count"]
    assert report["missing_task_names"] == []
    assert report["task_count"] >= report["package_script_count"]

    script = ROOT / "infra" / "scripts" / "utils" / "task_command_ownership.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print((result.stdout or "") + (result.stderr or ""))
        raise AssertionError(f"ownership check failed: {result.returncode}")
    payload = json.loads(result.stdout)
    assert payload["missing_task_names"] == []

    print("Task command ownership tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

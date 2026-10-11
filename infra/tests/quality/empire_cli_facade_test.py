#!/usr/bin/env python3
"""Offline tests for the thin root Empire-to-Task facade."""

from __future__ import annotations

import subprocess
import sys
import json
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "empire"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def main() -> int:
    spec = importlib.util.spec_from_loader("empire_fixture", SourceFileLoader("empire_fixture", str(CLI)))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    payload = {"tasks": [{"name": route.task, "desc": "ตรวจคำสั่ง"} for route in module.ROUTES]}
    completed = subprocess.CompletedProcess([], 0, json.dumps(payload, ensure_ascii=False), "")
    with patch.object(module.subprocess, "run", return_value=completed) as run:
        assert module.check_routes() == 0
        assert run.call_args.kwargs["encoding"] == "utf-8"

    help_result = run_cli("--help")
    assert help_result.returncode == 0
    assert "Task-backed" in help_result.stdout
    assert "empire diary daily" in help_result.stdout

    route_check = run_cli("routes", "--check")
    assert route_check.returncode == 0, route_check.stderr
    assert "Empire route check passed" in route_check.stdout

    install_preview = run_cli("--dry-run", "install")
    assert install_preview.returncode == 0
    assert " -> " in install_preview.stdout

    assert run_cli("--dry-run", "status").stdout.strip() == "task status"
    assert run_cli("--dry-run", "diary", "daily").stdout.strip() == "task diary:daily"
    assert (
        run_cli("--dry-run", "task", "ownership", "--", "--json").stdout.strip()
        == "task task:ownership -- --json"
    )

    unknown = run_cli("--dry-run", "does-not-exist")
    assert unknown.returncode == 2
    assert "unknown command" in unknown.stderr
    assert "npm" not in help_result.stdout.lower()

    print("Empire CLI facade tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

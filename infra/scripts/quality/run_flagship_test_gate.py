#!/usr/bin/env python3
"""Run nested Tier A flagship unit/contract suites from a tracked target list.

This gate does **not** try to test all ~255 Book Dev repos. It only runs the
frozen flagship targets declared in ``flagship_test_targets.yaml``.

Usage:
    task test:flagships
    task test:flagships -- --list
    task test:flagships -- --only bookreading-frontend,booktrading-frontend
    task test:flagships -- --json
    task test:flagships -- --strict-inventory
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    sys.exit(1)

INFRA_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import book_dev_tree  # noqa: E402

# Workspace root = the Book Dev tree that holds `github/<owner>/<visibility>/<repo>` clones (siblings of solo-ops).
REPO_ROOT = book_dev_tree() or INFRA_ROOT.parent
DEFAULT_TARGETS = Path(__file__).resolve().parent / "flagship_test_targets.yaml"


@dataclass
class TargetResult:
    id: str
    path: str
    status: str  # pass | fail | skip | missing | inventory | timeout | error
    seconds: float
    reason: str = ""
    tail: str = ""


def load_targets(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "targets" not in data:
        raise ValueError(f"invalid targets file: {path}")
    return data


def resolve_target_dir(book_dev_root: str, rel: str) -> Path:
    return (REPO_ROOT / book_dev_root / rel).resolve()


def npm_ready(cwd: Path) -> str | None:
    if not (cwd / "package.json").is_file():
        return "package.json missing"
    if not (cwd / "node_modules").is_dir():
        return "node_modules missing (run npm ci in package)"
    return None


def go_ready(cwd: Path) -> str | None:
    if not (cwd / "go.mod").is_file():
        return "go.mod missing"
    if shutil.which("go") is None:
        return "go binary not on PATH"
    # Nested trees sometimes ship go.mod without go.sum; refuse to mutate nested
    # modules from the empire gate (no go mod download / tidy).
    if not (cwd / "go.sum").is_file():
        return "go.sum missing (run go mod tidy inside nested package first)"
    return None


def python_ready(cwd: Path) -> str | None:
    if not (cwd / "tests").is_dir() and not list(cwd.glob("test_*.py")):
        return "no tests/ directory or test_*.py found"
    if package_python(cwd) is None and shutil.which("python3") is None:
        return "python3 not on PATH"
    return None


def package_python(cwd: Path) -> str | None:
    for candidate in (
        cwd / ".venv" / "bin" / "python",
        cwd / ".venv" / "bin" / "python3",
        cwd / "venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def run_command(
    command: list[str],
    cwd: Path,
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> TargetResult:
    # filled by caller with id/path
    started = time.monotonic()
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("npm_config_fund", "false")
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        seconds = round(time.monotonic() - started, 3)
        combined = (proc.stdout or "") + (proc.stderr or "")
        tail = combined[-1000:].strip()
        if proc.returncode == 0:
            return TargetResult("", "", "pass", seconds, tail=tail)
        return TargetResult(
            "",
            "",
            "fail",
            seconds,
            reason=f"exit {proc.returncode}",
            tail=tail,
        )
    except subprocess.TimeoutExpired as exc:
        seconds = round(time.monotonic() - started, 3)
        out = ""
        if exc.stdout:
            out += exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode()
        if exc.stderr:
            out += exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode()
        return TargetResult("", "", "timeout", seconds, reason=f"exceeded {timeout}s", tail=out[-1000:].strip())
    except Exception as exc:  # noqa: BLE001
        seconds = round(time.monotonic() - started, 3)
        return TargetResult("", "", "error", seconds, reason=str(exc))


def execute_target(target: dict[str, Any], book_dev_root: str) -> TargetResult:
    tid = str(target["id"])
    rel = str(target["path"])
    kind = str(target.get("kind", "inventory"))
    cwd = resolve_target_dir(book_dev_root, rel)
    display = f"{book_dev_root}/{rel}"

    if not cwd.is_dir():
        return TargetResult(tid, display, "missing", 0.0, reason="path not found")

    if kind == "inventory":
        note = str(target.get("note") or "inventory-only (no suite declared)")
        return TargetResult(tid, display, "inventory", 0.0, reason=note)

    if kind == "files":
        missing = []
        for name in target.get("expect_files") or []:
            if not (cwd / str(name)).exists():
                missing.append(str(name))
        if missing:
            return TargetResult(
                tid,
                display,
                "fail",
                0.0,
                reason=f"missing files: {', '.join(missing)}",
            )
        return TargetResult(tid, display, "pass", 0.0, reason="expected files present")

    required = bool(target.get("required", False))
    if kind == "npm":
        ready = npm_ready(cwd)
        if ready:
            # Missing node_modules is environmental, not a product assertion failure.
            status = "skip" if ("node_modules" in ready or not required) else "fail"
            return TargetResult(tid, display, status, 0.0, reason=ready)
    elif kind == "go":
        ready = go_ready(cwd)
        if ready:
            status = "skip" if ("go.sum" in ready or not required) else "fail"
            return TargetResult(tid, display, status, 0.0, reason=ready)
    elif kind == "python":
        ready = python_ready(cwd)
        if ready:
            status = "skip" if not required else "fail"
            return TargetResult(tid, display, status, 0.0, reason=ready)
    else:
        return TargetResult(tid, display, "error", 0.0, reason=f"unknown kind: {kind}")

    command = list(target.get("command") or [])
    if not command:
        return TargetResult(tid, display, "error", 0.0, reason="empty command")

    # Prefer package-local venv, then empire python launcher.
    if kind == "python" and command[0] in {"python3", "python"}:
        local_py = package_python(cwd)
        if local_py:
            command = [local_py, *command[1:]]
        else:
            launcher = INFRA_ROOT / "scripts" / "setup" / "run-python3.sh"
            if launcher.is_file():
                command = ["bash", str(launcher), *command[1:]]
    timeout = int(target.get("timeout_sec") or 180)
    extra_env = target.get("env") if isinstance(target.get("env"), dict) else None
    result = run_command(command, cwd, timeout, extra_env=extra_env)
    result.id = tid
    result.path = display
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tier A flagship nested test gate")
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated target ids",
    )
    parser.add_argument(
        "--strict-inventory",
        action="store_true",
        help="fail when inventory-only Tier A packages have no suite",
    )
    parser.add_argument(
        "--include-optional",
        action="store_true",
        help="also run required=false suite targets",
    )
    args = parser.parse_args(argv)

    data = load_targets(args.targets)
    book_dev_root = str(data.get("book_dev_root") or ".")
    targets = list(data["targets"])
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if only:
        targets = [t for t in targets if t.get("id") in only]

    if args.list:
        rows = []
        for t in targets:
            rows.append(
                {
                    "id": t.get("id"),
                    "path": t.get("path"),
                    "kind": t.get("kind"),
                    "required": bool(t.get("required")),
                    "command": t.get("command") or t.get("note") or t.get("expect_files"),
                }
            )
        if args.json:
            print(json.dumps({"count": len(rows), "targets": rows}, indent=2))
        else:
            print(f"Flagship targets: {len(rows)}")
            for row in rows:
                req = "req" if row["required"] else "opt"
                print(f"  [{req}] {row['id']:32} {row['kind']:10} {row['path']}")
        return 0

    print("=" * 72)
    print("Solo Empire flagship nested test gate (Tier A)")
    print(f"Targets file: {args.targets}")
    print("=" * 72)

    results: list[TargetResult] = []
    for target in targets:
        required = bool(target.get("required"))
        kind = str(target.get("kind"))
        if not required and kind not in {"inventory", "files"} and not args.include_optional:
            tid = str(target["id"])
            display = f"{book_dev_root}/{target['path']}"
            results.append(
                TargetResult(tid, display, "skip", 0.0, reason="optional (pass --include-optional)")
            )
            print(f"SKIP  {tid}  — optional")
            continue

        print(f"RUN   {target['id']} ...", flush=True)
        result = execute_target(target, book_dev_root)
        results.append(result)
        line = f"{result.status.upper():9} {result.id}  ({result.seconds}s)"
        if result.reason:
            line += f"  — {result.reason}"
        print(line)
        if result.status in {"fail", "timeout", "error"} and result.tail:
            for row in result.tail.splitlines()[-10:]:
                print(f"      | {row}")

    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1

    hard_fail_statuses = {"fail", "timeout", "error", "missing"}
    if args.strict_inventory:
        hard_fail_statuses.add("inventory")

    failed = [r for r in results if r.status in hard_fail_statuses]
    summary = {
        "total": len(results),
        "counts": counts,
        "failed": [asdict(r) for r in failed],
        "results": [asdict(r) for r in results],
    }

    print("=" * 72)
    print(
        "Results: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        + f" (of {len(results)})"
    )
    print("=" * 72)
    if args.json:
        print(json.dumps(summary, indent=2))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

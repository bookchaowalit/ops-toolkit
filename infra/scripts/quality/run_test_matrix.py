#!/usr/bin/env python3
"""Discover and run every Solo Empire infra Python test module.

Default mode runs all modules under ``infra/tests/`` except benchmarks and
optional product-boundary suites that need external checkouts.

Usage:
    task test:matrix
    task test:matrix -- --json
    task test:matrix -- --list
    task test:matrix -- --only database,security
    task test:matrix -- --include-optional
    task test:matrix -- --include-benchmarks
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

INFRA_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = INFRA_ROOT.parent
TESTS_ROOT = INFRA_ROOT / "tests"
DEFAULT_TIMEOUT_SEC = 180

# Not unit/contract suites — run only when explicitly requested.
BENCHMARK_FILES = {
    "api/api_performance_benchmark.py",
}

# Need external product checkouts / credentials beyond local fixtures.
OPTIONAL_FILES = {
    "data_lake/job_data_e2e_test.py",  # needs BOOK_JOB_DATA_ROOT product tree
}

# Extra modules that do not match *_test.py but are part of the suite.
EXTRA_MODULES: set[str] = set()


@dataclass
class TestResult:
    path: str
    status: str  # pass | fail | skip | timeout | error
    seconds: float
    exit_code: int | None
    reason: str = ""
    tail: str = ""


def rel_test_path(path: Path) -> str:
    return path.relative_to(TESTS_ROOT).as_posix()


def discover_modules() -> list[Path]:
    found: list[Path] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or "fixtures" in path.parts:
            continue
        name = path.name
        rel = rel_test_path(path)
        if rel in EXTRA_MODULES:
            found.append(path)
            continue
        if name.endswith("_test.py") or name.endswith("test.py"):
            # Avoid pulling in pure helpers if any appear later
            if name.startswith("_"):
                continue
            found.append(path)
            continue
        if rel in BENCHMARK_FILES:
            found.append(path)
    # Stable unique order
    uniq: dict[str, Path] = {rel_test_path(p): p for p in found}
    return [uniq[k] for k in sorted(uniq)]


def should_skip(
    rel: str,
    *,
    include_optional: bool,
    include_benchmarks: bool,
) -> str | None:
    if rel in BENCHMARK_FILES and not include_benchmarks:
        return "benchmark (pass --include-benchmarks)"
    if rel in OPTIONAL_FILES and not include_optional:
        if rel == "data_lake/job_data_e2e_test.py":
            if os.environ.get("BOOK_JOB_DATA_ROOT"):
                return None
            return "optional product boundary (set BOOK_JOB_DATA_ROOT or --include-optional)"
        return "optional (pass --include-optional)"
    return None


def run_module(path: Path, timeout: int) -> TestResult:
    rel = rel_test_path(path)
    started = time.monotonic()
    env = os.environ.copy()
    env.setdefault("SOLO_EMPIRE_CI", env.get("CI", ""))
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(INFRA_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        seconds = round(time.monotonic() - started, 3)
        combined = (proc.stdout or "") + (proc.stderr or "")
        tail = combined[-800:].strip()
        if proc.returncode == 0:
            return TestResult(rel, "pass", seconds, proc.returncode, tail=tail)
        return TestResult(
            rel,
            "fail",
            seconds,
            proc.returncode,
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
        return TestResult(
            rel,
            "timeout",
            seconds,
            None,
            reason=f"exceeded {timeout}s",
            tail=out[-800:].strip(),
        )
    except Exception as exc:  # noqa: BLE001 - matrix must never crash the runner
        seconds = round(time.monotonic() - started, 3)
        return TestResult(rel, "error", seconds, None, reason=str(exc))


def filter_modules(
    modules: list[Path],
    only: list[str] | None,
) -> list[Path]:
    if not only:
        return modules
    tokens = {t.strip().lower() for t in only if t.strip()}
    selected: list[Path] = []
    for path in modules:
        rel = rel_test_path(path)
        group = rel.split("/", 1)[0].lower()
        if group in tokens or rel.lower() in tokens or path.stem.lower() in tokens:
            selected.append(path)
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Solo Empire infra test matrix")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable summary")
    parser.add_argument("--list", action="store_true", help="List discovered modules and exit")
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated group or path filters (e.g. database,security,agent)",
    )
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--include-benchmarks", action="store_true")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SEC,
        help=f"Per-module timeout seconds (default {DEFAULT_TIMEOUT_SEC})",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failure",
    )
    args = parser.parse_args(argv)

    modules = discover_modules()
    only_tokens = [t for t in args.only.split(",") if t.strip()] if args.only else None
    modules = filter_modules(modules, only_tokens)

    if args.list:
        payload = []
        for path in modules:
            rel = rel_test_path(path)
            skip = should_skip(
                rel,
                include_optional=args.include_optional,
                include_benchmarks=args.include_benchmarks,
            )
            payload.append({"path": rel, "default": "skip" if skip else "run", "reason": skip or ""})
        if args.json:
            print(json.dumps({"count": len(payload), "modules": payload}, indent=2))
        else:
            print(f"Discovered {len(payload)} modules under {TESTS_ROOT.relative_to(REPO_ROOT)}")
            for item in payload:
                mark = "SKIP" if item["default"] == "skip" else "RUN "
                extra = f"  ({item['reason']})" if item["reason"] else ""
                print(f"  {mark}  {item['path']}{extra}")
        return 0

    results: list[TestResult] = []
    print("=" * 72)
    print("Solo Empire infra test matrix")
    print(f"Root: {TESTS_ROOT}")
    print("=" * 72)

    for path in modules:
        rel = rel_test_path(path)
        skip = should_skip(
            rel,
            include_optional=args.include_optional,
            include_benchmarks=args.include_benchmarks,
        )
        if skip:
            results.append(TestResult(rel, "skip", 0.0, None, reason=skip))
            print(f"SKIP  {rel}  — {skip}")
            continue

        print(f"RUN   {rel} ...", flush=True)
        result = run_module(path, timeout=args.timeout)
        results.append(result)
        status = result.status.upper()
        print(f"{status:5} {rel}  ({result.seconds}s)" + (f"  {result.reason}" if result.reason else ""))
        if result.status in {"fail", "timeout", "error"} and result.tail:
            for line in result.tail.splitlines()[-12:]:
                print(f"      | {line}")
        if args.fail_fast and result.status in {"fail", "timeout", "error"}:
            break

    counts = {k: 0 for k in ("pass", "fail", "skip", "timeout", "error")}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1

    summary = {
        "root": str(TESTS_ROOT.relative_to(REPO_ROOT)),
        "total": len(results),
        "counts": counts,
        "failed": [
            {"path": r.path, "status": r.status, "reason": r.reason, "seconds": r.seconds}
            for r in results
            if r.status in {"fail", "timeout", "error"}
        ],
        "skipped": [
            {"path": r.path, "reason": r.reason} for r in results if r.status == "skip"
        ],
        "results": [asdict(r) for r in results],
    }

    print("=" * 72)
    print(
        "Results: "
        f"{counts['pass']} passed, "
        f"{counts['fail']} failed, "
        f"{counts['timeout']} timeout, "
        f"{counts['error']} error, "
        f"{counts['skip']} skipped "
        f"(of {len(results)})"
    )
    print("=" * 72)

    if args.json:
        # Print JSON after human summary for local use; CI can parse last object if needed.
        print(json.dumps(summary, indent=2))

    hard_fails = counts["fail"] + counts["timeout"] + counts["error"]
    return 1 if hard_fails else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Compare cell wake-up time before and after a cutoff, from Railway deployment logs.

Railway Serverless cells sleep when idle. Each wake logs ``Starting Container``
and then uvicorn's ``Application startup complete``; the gap is the wake time
the first request waits for (container start + Python startup). This reads the
logs of each online cell's ``<cell>-api`` service through the Railway CLI (run
from the cell's linked checkout), splits deployments at ``--cutoff``, and
reports the median and quartiles on each side. Read-only.

    python3 infra/scripts/platform/cell_coldstart.py --cutoff 2026-10-11T06:30 [--json]

Railway keeps logs for a limited time and returns at most 5000 lines per
deployment, so old deployments may give few samples.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import legacy_env_name  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
# The systems registry belongs to the registry repository: point SOLO_REGISTRY_DIR at its
# `repository-catalog` folder (there is no default).
_REGISTRY_DIR = os.environ.get("SOLO_REGISTRY_DIR")
REGISTRY = Path(_REGISTRY_DIR or "/nonexistent-solo-registry") / "registries" / "online-systems.yaml"


def checkout_for_repo(repo_path: str) -> Path:
    """Sibling clone of the repository named by a registry `repo.path` (SOLO_<NAME>_ROOT or ../<repo>)."""
    name = Path(repo_path).name
    override = os.environ.get(f"SOLO_{legacy_env_name(name)}_ROOT")
    return Path(override) if override else ROOT.parent / name


def load_systems() -> list[dict]:
    if not REGISTRY.is_file():
        raise SystemExit("online-systems.yaml not found: set SOLO_REGISTRY_DIR to the repository-catalog folder")
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["systems"]


def _time(stamp: str) -> datetime:
    stamp = stamp.rstrip("Z")
    if "." in stamp:
        head, frac = stamp.split(".")
        stamp = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(stamp)


def railway(cwd: Path, *args: str) -> str:
    return subprocess.run(["railway", *args], cwd=cwd, capture_output=True, text=True, timeout=180).stdout


def wake_times(cwd: Path, service: str, deployment: str) -> list[float]:
    out = railway(cwd, "logs", deployment, "--service", service, "--deployment", "--json",
                  "--since", "30d", "--lines", "5000")
    events = []
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message", "")
        if "Starting Container" in message:
            events.append((_time(entry["timestamp"]), "start"))
        elif "Application startup complete" in message:
            events.append((_time(entry["timestamp"]), "ready"))
    gaps, started = [], None
    for at, kind in sorted(events):
        if kind == "start":
            started = at
        elif started is not None:
            gaps.append((at - started).total_seconds())
            started = None
    return gaps


def summary(values: list[float]) -> dict:
    if len(values) < 4:
        return {"n": len(values), "values": [round(v, 2) for v in values]}
    q1, median, q3 = statistics.quantiles(values, n=4)
    return {"n": len(values), "median": round(median, 2), "p25": round(q1, 2), "p75": round(q3, 2)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cutoff", required=True, help="ISO time; deployments created before it count as 'before'")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    systems = load_systems()
    cells = [s for s in systems if s.get("kind") == "cell" and s.get("status") == "online"
             and s.get("host", {}).get("provider") == "railway" and s.get("repo", {}).get("path")]
    before_all, after_all, per_cell = [], [], {}
    for cell in cells:
        cwd = checkout_for_repo(cell["repo"]["path"])
        api = next((svc for svc in cell["host"].get("services", []) if svc.endswith("-api")), None)
        if api is None or not cwd.is_dir():
            continue
        try:
            deployments = json.loads(railway(cwd, "deployment", "list", "--service", api, "--json") or "[]")
        except ValueError:
            deployments = []
        before = [g for d in deployments if d["createdAt"] < args.cutoff for g in wake_times(cwd, api, d["id"])]
        after = [g for d in deployments if d["createdAt"] >= args.cutoff for g in wake_times(cwd, api, d["id"])]
        per_cell[cell["id"]] = {"before": summary(before), "after": summary(after)}
        before_all += before
        after_all += after

    report = {"cutoff": args.cutoff, "before": summary(before_all), "after": summary(after_all), "cells": per_cell}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for name, side in per_cell.items():
            print(f"{name:14} before n={side['before']['n']:3}  after n={side['after']['n']:3}")
        print(f"before: {report['before']}")
        print(f"after:  {report['after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

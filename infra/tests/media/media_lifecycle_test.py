#!/usr/bin/env python3
"""Focused tests for the safe media lifecycle policy."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from media_lifecycle import build_plan  # noqa: E402


def stamp(days_ago: int) -> str:
    value = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return value.isoformat().replace("+00:00", "Z")


def test_policy_protects_tracked_and_referenced_files():
    records = [
        {
            "path": "data/generated/reports/old.png",
            "root": "generated",
            "kind": "image",
            "entry_type": "file",
            "size": 10,
            "mtime": stamp(60),
            "sha256": "a",
            "tracked": False,
            "referenced": False,
        },
        {
            "path": "data/generated/reports/tracked.png",
            "root": "generated",
            "kind": "image",
            "entry_type": "file",
            "size": 10,
            "mtime": stamp(60),
            "sha256": "b",
            "tracked": True,
            "referenced": False,
        },
        {
            "path": "data/generated/reports/referenced.png",
            "root": "generated",
            "kind": "image",
            "entry_type": "file",
            "size": 10,
            "mtime": stamp(60),
            "sha256": "c",
            "tracked": False,
            "referenced": True,
        },
    ]
    plan = build_plan(records)
    assert [item["path"] for item in plan["candidates"]] == [
        "data/generated/reports/old.png"
    ]


def test_policy_can_retire_old_runtime_snapshot_as_one_directory():
    record = {
        "path": "data/runtime/agent-loop/run-old",
        "root": "runtime",
        "kind": "runtime_snapshot",
        "entry_type": "directory",
        "file_count": 100,
        "size": 1024,
        "mtime": stamp(20),
        "newest_file_mtime": stamp(20),
        "sha256": None,
        "tracked": False,
        "referenced": False,
    }
    plan = build_plan([record])
    assert plan["summary"]["candidates"] == 1
    assert plan["candidates"][0]["reason"].startswith("runtime snapshot")


if __name__ == "__main__":
    test_policy_protects_tracked_and_referenced_files()
    test_policy_can_retire_old_runtime_snapshot_as_one_directory()
    print("✅ media lifecycle tests passed")

#!/usr/bin/env python3
"""Regressions found in the independent review of the reader migration."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
import task_store  # noqa: E402
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_task_store import SqliteTaskStore, apply_legacy_task_tables  # noqa: E402



def store() -> SqliteTaskStore:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    apply_legacy_task_tables(conn)
    conn.execute("PRAGMA foreign_keys = OFF")  # skills and goals are not part of these checks
    return SqliteTaskStore(conn)


def skill_rows(s, task):
    return sorted(tuple(r) for r in s.conn.execute(
        "SELECT source_registry, link_type, match_reason FROM task_skill_links WHERE task_id = ?", (task,)))


def test_skill_link_refresh_and_withdraw_stay_within_their_registry():
    s = store()
    task = s.create({"title": "Learn networking"})
    s.link(task, "skill", 9, {"link_type": "accepted", "match_reason": "owner", "source_registry": "docs/OTHER.yaml"})
    s.link(task, "skill", 9, {"link_type": "suggested", "match_reason": "term", "source_registry": "docs/ROLE-LEARNING.yaml"})
    # Refreshing one registry must not collide with or rewrite the other one.
    assert s.link(task, "skill", 9, {"match_reason": "term v2", "source_registry": "docs/ROLE-LEARNING.yaml"},
                  update=True) == "updated"
    assert skill_rows(s, task) == [("docs/OTHER.yaml", "accepted", "owner"),
                                   ("docs/ROLE-LEARNING.yaml", "suggested", "term v2")]
    # Withdrawing the generated suggestion keeps the other registry's accepted link.
    assert s.unlink(task, "skill", 9, source_registry="docs/ROLE-LEARNING.yaml")
    assert skill_rows(s, task) == [("docs/OTHER.yaml", "accepted", "owner")]


def test_task_types_return_only_active_rows():
    s = store()
    s.conn.execute("DELETE FROM task_types")
    s.conn.execute("INSERT INTO task_types (name, is_active, sort_order) VALUES ('on', 1, 0), ('off', 0, 0), ('unset', NULL, 0)")
    assert [t["name"] for t in s.task_types()] == ["on"]
    assert sorted(t["name"] for t in s.task_types(include_inactive=True)) == ["off", "on", "unset"]


def test_recurring_instances_keep_their_parent_reference():
    s = store()
    parent = s.create({"title": "Weekly review", "recurrence_rule": "weekly", "due_date": "2026-10-01"})
    child = s.generate_occurrences(parent, "2026-10-08")[0]
    assert s.get(child)["parent_recurring_id"] == parent


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"✅ {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"❌ {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)

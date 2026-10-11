#!/usr/bin/env python3
"""Accept unambiguous high-confidence task-to-role mappings.

This is deliberately separate from human ownership. Accepted mappings live in
task_role_suggestions with review_status=accepted; task_assignments remains the
source of truth for named/operator ownership.
"""

from __future__ import annotations

import argparse
from datetime import date

from _env import PROJECT_ROOT, REPORTS_DIR, display_path
from role_integration_sync import (
    TASK_ROLE_SOURCE,
    matching_roles,
    sync as sync_role_integrations,
    validate_registries,
)
from task_store import TaskStore, open_task_store


AUTOMATION = "role-router"
REPORT_PATH = REPORTS_DIR / "ROLE-TASK-MAPPING.md"


def candidate_mappings(store: TaskStore, routing_by_id: dict[str, dict]) -> list[tuple[int, str]]:
    assigned_task_ids = {row["task"] for row in store.assignments(assignee_type="role")}
    accepted_keys = {
        (row["task"], row["role_id"])
        for row in store.role_suggestions(source_registry=TASK_ROLE_SOURCE, review_status="accepted")
    }
    candidates: list[tuple[int, str]] = []
    for task in store.list(include_archived=True):
        matches = matching_roles(task, routing_by_id)
        high_matches = [
            role_id for role_id, (confidence, _reason) in matches.items()
            if confidence == "high"
        ]
        if (
            len(high_matches) == 1
            and task["id"] not in assigned_task_ids
            and (task["id"], high_matches[0]) not in accepted_keys
        ):
            candidates.append((task["id"], high_matches[0]))
    return candidates


def query_counts(store: TaskStore) -> dict[str, int]:
    tasks = store.list(include_archived=True)
    role_assigned = {row["task"] for row in store.assignments(assignee_type="role")}
    suggestions = store.role_suggestions(source_registry=TASK_ROLE_SOURCE)

    def tasks_with(review_status: str, confidence: str | None = None) -> set[int]:
        return {
            row["task"]
            for row in suggestions
            if row["review_status"] == review_status and (confidence is None or row["confidence"] == confidence)
        }

    accepted = tasks_with("accepted")
    return {
        "tasks": len(tasks),
        "active_tasks": sum(task["status"] in ("todo", "in_progress", "blocked") for task in tasks),
        "assigned_tasks": len(role_assigned),
        "accepted_tasks": len(accepted),
        "suggested_tasks": len(tasks_with("suggested")),
        "suggested_rows": sum(row["review_status"] == "suggested" for row in suggestions),
        "accepted_rows": sum(row["review_status"] == "accepted" for row in suggestions),
        "unmapped_tasks": sum(task["id"] not in role_assigned and task["id"] not in accepted for task in tasks),
        "high_review_tasks": len(tasks_with("suggested", "high")),
        "medium_review_tasks": len(tasks_with("suggested", "medium")),
        "low_review_tasks": len(tasks_with("suggested", "low")),
    }


def write_report(store: TaskStore, applied_count: int, candidate_count: int) -> None:
    counts = query_counts(store)
    suggestions = store.role_suggestions(source_registry=TASK_ROLE_SOURCE)
    tasks_by_role: dict[str, set[int]] = {}
    for suggestion in suggestions:
        if suggestion["review_status"] == "accepted":
            tasks_by_role.setdefault(suggestion["role_id"], set()).add(suggestion["task"])
    role_rows = [
        {"role_id": role_id, "task_count": len(task_ids)} for role_id, task_ids in sorted(tasks_by_role.items())
    ]
    suggested = [row for row in suggestions if row["review_status"] == "suggested"]
    tasks_by_id = {
        task["id"]: task
        for task in store.list(numbers=sorted({row["task"] for row in suggested}), include_archived=True)
    }
    confidence_rank = {"high": 1, "medium": 2}
    review_rows = [
        {
            "task_id": row["task"],
            "status": tasks_by_id[row["task"]]["status"],
            "title": tasks_by_id[row["task"]]["title"],
            "role_id": row["role_id"],
            "confidence": row["confidence"],
            "match_reason": row["match_reason"],
        }
        for row in sorted(
            (row for row in suggested if row["task"] in tasks_by_id),
            key=lambda row: (confidence_rank.get(row["confidence"], 3), row["task"], row["role_id"]),
        )[:100]
    ]
    lines = [
        "---",
        "title: Task to Atomic Role Mapping",
        "tags: [dashboard, tasks, roles, mapping, review-queue]",
        f"updated: {date.today().isoformat()}",
        "status: generated",
        "source: docs/registries/ROLE-TASK-ROUTING.yaml, task_role_suggestions, task_assignments",
        "---",
        "",
        "# Task → Atomic Role Mapping",
        "",
        "> Accepted mappings identify the most likely atomic role for a task. They do not replace named/operator ownership in task_assignments.",
        "",
        "## Current coverage",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Total tasks | {counts['tasks']} |",
        f"| Active tasks | {counts['active_tasks']} |",
        f"| Tasks with human/operator role assignment | {counts['assigned_tasks']} |",
        f"| Tasks with accepted role mapping | {counts['accepted_tasks']} |",
        f"| Tasks still unmapped | {counts['unmapped_tasks']} |",
        f"| Accepted mapping rows | {counts['accepted_rows']} |",
        f"| Review queue rows | {counts['suggested_rows']} |",
        f"| High-confidence review tasks | {counts['high_review_tasks']} |",
        f"| Medium-confidence review tasks | {counts['medium_review_tasks']} |",
        f"| Low-confidence review tasks | {counts['low_review_tasks']} |",
        "",
        f"Run result: accepted {applied_count} new mappings from {candidate_count} eligible high-confidence candidates.",
        "",
        "## Accepted mappings by role",
        "",
        "| Atomic role | Mapped tasks |",
        "|---|---:|",
    ]
    for row in role_rows:
        lines.append(f"| {row['role_id']} | {row['task_count']} |")
    lines.extend(
        [
            "",
            "## Review queue sample",
            "",
            "| Task | Status | Suggested role | Confidence | Reason |",
            "|---|---|---|---|---|",
        ]
    )
    for row in review_rows:
        title = str(row["title"] or "").replace("|", "\\|").replace("\n", " ")
        reason = str(row["match_reason"] or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| #{row['task_id']} {title} | {row['status']} | {row['role_id']} | "
            f"{row['confidence']} | {reason} |"
        )
    lines.extend(
        [
            "",
            "## Mapping rules",
            "",
            "1. Only one high-confidence role match is accepted automatically.",
            "2. Existing task role assignments are never changed.",
            "3. Ambiguous high-confidence, medium-confidence, and low-confidence matches remain suggested for review.",
            "4. Accepted mappings are routing metadata; create or change task_assignments only after human ownership review.",
            "",
        ]
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def run(dry_run: bool) -> int:
    _catalog, _learning, routing, _paths, _catalog_by_id, _learning_by_id = validate_registries()

    routing_by_id = {
        row["role_id"]: row
        for row in routing.get("rules", [])
        if isinstance(row, dict) and row.get("role_id")
    }

    if not dry_run:
        sync_role_integrations(
            argparse.Namespace(include_history=True, dry_run=False)
        )

    store = open_task_store(None, AUTOMATION)
    candidates = candidate_mappings(store, routing_by_id)
    if dry_run:
        counts = query_counts(store)
        print("Task-to-role mapping (dry-run)")
        print(f"  eligible high-confidence mappings: {len(candidates)}")
        print(f"  total tasks: {counts['tasks']}")
        print("  task changes: 0")
        return 0

    applied_count = 0
    for task_id, role_id in candidates:
        applied_count += store.review_role(
            task_id, role_id, TASK_ROLE_SOURCE, "accepted", expected_status="suggested"
        )
    write_report(store, applied_count, len(candidates))
    print("Task-to-role mapping")
    print(f"  eligible high-confidence mappings: {len(candidates)}")
    print(f"  accepted mappings: {applied_count}")
    print(f"  report: {display_path(REPORT_PATH)}")
    print("Result: OK — ownership assignments were not changed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        return run(args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"Result: FAIL — {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

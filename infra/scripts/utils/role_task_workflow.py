#!/usr/bin/env python3
"""Review role suggestions, approve explicit ownership, and build an execution queue.

The workflow keeps three states separate:

* ``task_role_suggestions`` is routing evidence and may be accepted/dismissed.
* ``task_assignments`` contains the canonical user owner plus optional working
  role assignments. ``--claim-all-owner`` applies the canonical Owner to every
  task; ``--assign TASK_ID:ROLE_ID`` sets a working role explicitly.
* ``EXECUTION-QUEUE.md`` (reports directory) is a generated view of active work across
  multiple goals; it never becomes a second task source of truth.

Running without a decision flag only refreshes the two generated reports.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime

from _env import PROJECT_ROOT, REPORTS_DIR, display_path  # noqa: E402
from cell_store import open_cell_store  # noqa: E402
from role_integration_sync import TASK_ROLE_SOURCE, validate_registries  # noqa: E402
from task_owner import canonical_owner  # noqa: E402,F401
from task_store import TaskStore, open_task_store  # noqa: E402


REVIEW_REPORT = REPORTS_DIR / "ROLE-TASK-REVIEW.md"
QUEUE_REPORT = REPORTS_DIR / "EXECUTION-QUEUE.md"
ACTIVE_STATUSES = ("todo", "in_progress", "blocked")
AUTOMATION = "role-router"


def role_catalog() -> dict[str, dict]:
    _catalog, _learning, _routing, _paths, catalog_by_id, _learning_by_id = validate_registries()
    return catalog_by_id


def parse_role_pairs(values: list[str], flag: str) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    for value in values:
        parts = value.split(":", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].strip():
            raise ValueError(f"{flag} expects TASK_ID:ROLE_ID, got {value!r}")
        pairs.append((int(parts[0]), parts[1].strip()))
    return pairs


def load_state(store: TaskStore | None = None) -> dict:
    store = store or open_task_store(None, AUTOMATION)
    active = store.list(status=list(ACTIVE_STATUSES), include_archived=True)
    active.sort(key=lambda t: (3 if t["priority"] is None else t["priority"], t["id"]))
    parent_numbers = sorted({t["parent_task_id"] for t in active if t["parent_task_id"] is not None})
    parents = {t["id"]: t for t in store.list(numbers=parent_numbers, include_archived=True)} if parent_numbers else {}
    tasks = {
        task["id"]: {
            **task,
            "parent_status": parents[task["parent_task_id"]]["status"] if task["parent_task_id"] in parents else None,
            "parent_title": parents[task["parent_task_id"]]["title"] if task["parent_task_id"] in parents else None,
        }
        for task in active
    }
    assignments: dict[int, list[str]] = defaultdict(list)
    assignment_names: dict[tuple[int, str], str] = {}
    for row in sorted(
        store.assignments(assignee_type="role", assignment_type="assigned"),
        key=lambda r: (r["task"], r["assignee_id"]),
    ):
        assignments[row["task"]].append(row["assignee_id"])
        assignment_names[(row["task"], row["assignee_id"])] = row["assignee_name"] or row["assignee_id"]

    user_owners: dict[int, list[str]] = defaultdict(list)
    for row in sorted(
        store.assignments(assignee_type="user", assignment_type="assigned"),
        key=lambda r: (r["task"], r["assignee_id"]),
    ):
        user_owners[row["task"]].append(row["assignee_name"] or row["assignee_id"])

    suggestions: dict[int, list[dict]] = defaultdict(list)
    confidence_rank = {"high": 1, "medium": 2}
    for row in sorted(
        store.role_suggestions(source_registry=TASK_ROLE_SOURCE),
        key=lambda r: (r["task"], confidence_rank.get(r["confidence"], 3), r["role_id"]),
    ):
        suggestions[row["task"]].append(
            {
                "task_id": row["task"],
                "role_id": row["role_id"],
                "confidence": row["confidence"],
                "match_reason": row["match_reason"],
                "review_status": row["review_status"],
            }
        )

    goal_titles = {row["id"]: row["title"] for row in open_cell_store("goals", "role-router").list("goals")}
    goals: dict[int, list[str]] = defaultdict(list)
    for link in sorted(
        (l for l in store.links(target_type="goal") if l["target_id"] in goal_titles),
        key=lambda l: (l["task"], l["attributes"].get("link_type") or "", l["target_id"]),
    ):
        goals[link["task"]].append(f"#{link['target_id']} {goal_titles[link['target_id']]}")

    project_labels = {
        row["id"]: row["slug"] if row["slug"] is not None else str(row["id"])
        for row in open_cell_store("projects", "role-router").list("projects")
    }
    projects: dict[int, list[str]] = defaultdict(list)
    for link in sorted(
        (l for l in store.links(target_type="project") if l["target_id"] in project_labels),
        key=lambda l: (l["task"], l["attributes"].get("relation_type") or "", l["target_id"]),
    ):
        projects[link["task"]].append(project_labels[link["target_id"]])

    return {
        "tasks": tasks,
        "assignments": assignments,
        "assignment_names": assignment_names,
        "user_owners": user_owners,
        "suggestions": suggestions,
        "goals": goals,
        "projects": projects,
    }


def accepted_roles(rows: list[dict]) -> list[str]:
    return [row["role_id"] for row in rows if row["review_status"] == "accepted"]


def suggested_roles(rows: list[dict], confidence: str | None = None) -> list[str]:
    return [
        row["role_id"]
        for row in rows
        if row["review_status"] == "suggested"
        and (confidence is None or row["confidence"] == confidence)
    ]


def review_candidates(state: dict) -> list[dict]:
    candidates: list[dict] = []
    for task_id, task in state["tasks"].items():
        rows = state["suggestions"].get(task_id, [])
        high = suggested_roles(rows, "high")
        medium = suggested_roles(rows, "medium")
        assigned = state["assignments"].get(task_id, [])
        accepted = accepted_roles(rows)
        # Active work is the first review target. A task with several high
        # matches needs a role decision even when the router is confident.
        needs_review = (
            len(high) > 1
            or (not assigned and not accepted)
            or (not assigned and len(accepted) > 1)
        )
        if not needs_review:
            continue
        candidates.append(
            {
                "task": task,
                "high": high,
                "medium": medium,
                "assigned": assigned,
                "accepted": accepted,
                "goals": state["goals"].get(task_id, []),
                "projects": state["projects"].get(task_id, []),
            }
        )
    return sorted(candidates, key=lambda row: (row["task"]["priority"] or 3, row["task"]["id"]))


def readiness(task: dict) -> str:
    if task["status"] == "blocked":
        return "blocked"
    if task["parent_task_id"] and task["parent_status"] != "done":
        return "waiting-parent"
    return "ready"


def queue_rows(state: dict) -> list[dict]:
    rows: list[dict] = []
    for task_id, task in state["tasks"].items():
        assigned = state["assignments"].get(task_id, [])
        accepted = accepted_roles(state["suggestions"].get(task_id, []))
        role_ids = assigned or accepted
        task_readiness = readiness(task)
        if assigned:
            owner_state = "assigned"
        elif accepted:
            owner_state = "role-confirmation-needed"
        else:
            owner_state = "role-routing-needed"
        priority = int(task["priority"] or 3)
        # Priority is the dominant signal. Metadata completeness and role
        # routing confidence must never let a low-priority baseline task hide
        # an urgent operational or security task that still needs routing.
        score = (6 - max(1, min(5, priority))) * 100
        score += {"in_progress": 30, "todo": 20, "blocked": -30}[task["status"]]
        score += 20 if assigned else 10 if accepted else 0
        score += 10 if state["goals"].get(task_id) else 0
        score += 5 if state["projects"].get(task_id) or task["project"] else 0
        score += 10 if task_readiness == "ready" else -10
        rows.append(
            {
                "rank": 0,
                "task": task,
                "role_ids": role_ids,
                "owner_state": owner_state,
                "readiness": task_readiness,
                "goals": state["goals"].get(task_id, []),
                "projects": state["projects"].get(task_id, []) or ([task["project"]] if task["project"] else []),
                "score": score,
            }
        )
    rows.sort(key=lambda row: (-row["score"], row["task"]["id"]))
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    return rows


def markdown_cell(value: object) -> str:
    return str(value or "—").replace("|", r"\|").replace("\n", " ")


def ownership_cell(state: dict, task_id: int, role_ids: list[str]) -> str:
    owners = ", ".join(state["user_owners"].get(task_id, [])) or "unclaimed"
    roles = ", ".join(f"`{role_id}`" for role_id in role_ids)
    return f"{owners}<br>working role: {roles}" if roles else owners


def write_review_report(state: dict, roles: dict[str, dict]) -> dict[str, int]:
    candidates = review_candidates(state)
    active_assigned = sum(bool(state["assignments"].get(task_id)) for task_id in state["tasks"])
    active_user_owned = sum(bool(state["user_owners"].get(task_id)) for task_id in state["tasks"])
    active_accepted = sum(bool(accepted_roles(state["suggestions"].get(task_id, []))) for task_id in state["tasks"])
    active_ambiguous_high = sum(len(row["high"]) > 1 for row in candidates)
    lines = [
        "---",
        "title: Role Task Review Queue",
        "tags: [dashboard, roles, tasks, review, ownership]",
        f"updated: {datetime.now().date().isoformat()}",
        "status: generated",
        "source: task_role_suggestions, task_assignments, task_goal_links, task_project_links",
        "---",
        "",
        "# Role → Task Review Queue",
        "",
        "> Every task is owned by the canonical user Owner. Role assignments below describe the working role and are separate from user ownership.",
        "",
        "## Active coverage",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Active tasks | {len(state['tasks'])} |",
        f"| Active tasks with user owner | {active_user_owned} |",
        f"| Active tasks with working role assignment | {active_assigned} |",
        f"| Active tasks with accepted role mapping | {active_accepted} |",
        f"| Active tasks needing role review | {len(candidates)} |",
        f"| Active tasks with multiple high-confidence matches | {active_ambiguous_high} |",
        "",
        "## Review decisions",
        "",
        "Use the exact role ID from the table or `docs/registries/ROLE-CATALOG.yaml`:",
        "",
        "```bash",
        "task role-task:review -- --accept TASK_ID:ROLE_ID",
        "task role-task:review -- --dismiss TASK_ID:ROLE_ID",
        "task role-task:review -- --assign TASK_ID:ROLE_ID",
        "task role-task:review -- --claim-all-owner",
        "```",
        "",
        "`--claim-all-owner` adds the canonical user Owner idempotently to every task. `--accept` changes routing review status only. `--assign` adds a working-role assignment and refuses to replace a different role assignment.",
        "",
        "## Active review items",
        "",
        "| Task | Status | Priority | Suggested high roles | Other suggestions | User owner / working role | Goal | Project |",
        "|---|---|---:|---|---|---|---|---|",
    ]
    for item in candidates:
        task = item["task"]
        high = ", ".join(f"`{role_id}`" for role_id in item["high"]) or "—"
        other = ", ".join(
            f"`{row['role_id']}` ({row['confidence']})"
            for row in state["suggestions"].get(task["id"], [])
            if row["review_status"] == "suggested" and row["confidence"] != "high"
        )
        owner = ownership_cell(state, task["id"], item["assigned"])
        lines.append(
            f"| #{task['id']} {markdown_cell(task['title'])} | {task['status']} | {task['priority'] or 3} | "
            f"{high} | {other or '—'} | {owner} | {markdown_cell('<br>'.join(item['goals']))} | "
            f"{markdown_cell('<br>'.join(item['projects']) or task['project'])} |"
        )
    if not candidates:
        lines.append("| — | No active task needs role review | — | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "1. Multiple high-confidence matches require a human choice; the router does not guess.",
            "2. An accepted mapping is not ownership.",
            "3. The canonical user Owner applies to every task; role assignments describe the working role, not another human owner.",
            "4. Review status, user ownership, role assignment, and portfolio progress remain separate states.",
            "",
        ]
    )
    REVIEW_REPORT.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_REPORT.write_text("\n".join(lines), encoding="utf-8")
    return {
        "active_tasks": len(state["tasks"]),
        "review_tasks": len(candidates),
        "ambiguous_high_tasks": active_ambiguous_high,
    }


def write_queue_report(state: dict, roles: dict[str, dict]) -> dict[str, int]:
    rows = queue_rows(state)
    assigned_count = sum(row["owner_state"] == "assigned" for row in rows)
    awaiting_count = len(rows) - assigned_count
    user_owned_count = sum(bool(state["user_owners"].get(row["task"]["id"])) for row in rows)
    ready_count = sum(row["readiness"] == "ready" and row["owner_state"] == "assigned" for row in rows)
    lines = [
        "---",
        "title: Multi-Goal Execution Queue",
        "tags: [dashboard, execution, queue, goals, roles, tasks]",
        f"updated: {datetime.now().date().isoformat()}",
        "status: generated",
        "source: tasks, task_assignments, task_role_suggestions, task_goal_links, task_project_links",
        "---",
        "",
        "# Multi-Goal Execution Queue",
        "",
        "> Generated from active SQLite tasks. Multiple Goals can progress at the same time; this queue ranks work but does not create a sprint or replace the task source of truth.",
        "",
        "## Queue coverage",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Active tasks in queue | {len(rows)} |",
        f"| Active tasks with user owner | {user_owned_count} |",
        f"| Ready with confirmed working role | {ready_count} |",
        f"| With working role assignment | {assigned_count} |",
        f"| Awaiting working-role confirmation | {awaiting_count} |",
        "",
        "## Ranked work",
        "",
        "| Rank | Task | Status | Priority | Role / candidate | User owner | Working-role state | Readiness | Goal | Project | Score |",
        "|---:|---|---|---:|---|---|---|---|---|---|---:|",
    ]
    for row in rows:
        task = row["task"]
        role_text = ", ".join(f"`{role_id}`" for role_id in row["role_ids"]) or "needs role review"
        owner_text = ", ".join(state["user_owners"].get(task["id"], [])) or "unclaimed"
        lines.append(
            f"| {row['rank']} | #{task['id']} {markdown_cell(task['title'])} | {task['status']} | {task['priority'] or 3} | "
            f"{role_text} | {owner_text} | {row['owner_state']} | {row['readiness']} | {markdown_cell('<br>'.join(row['goals']))} | "
            f"{markdown_cell('<br>'.join(row['projects']))} | {row['score']} |"
        )
    if not rows:
        lines.append("| — | No active task exists | — | — | — | — | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## Ranking rules",
            "",
            "- Priority is the dominant signal; role assignment and metadata bonuses cannot move a lower-priority task above a higher-priority task.",
            "- `in_progress` receives a small execution boost; `blocked` is kept visible but penalized.",
            "- Working-role assignment, Goal links, Project links, and dependency readiness increase execution confidence; user ownership is universal.",
            "- `role-confirmation-needed` and `role-routing-needed` remain visible so the queue can be reviewed without silently assigning ownership.",
            "- Progress is calculated separately by `task portfolio:progress`.",
            "",
        ]
    )
    QUEUE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_REPORT.write_text("\n".join(lines), encoding="utf-8")
    return {
        "queue_tasks": len(rows),
        "assigned_tasks": assigned_count,
        "awaiting_owner_tasks": awaiting_count,
        "ready_tasks": ready_count,
    }


def validate_task_role(store: TaskStore, roles: dict[str, dict], task_id: int, role_id: str) -> dict:
    if role_id not in roles:
        raise ValueError(f"unknown atomic role: {role_id}")
    task = store.get(task_id)
    if task is None:
        raise ValueError(f"unknown task: {task_id}")
    suggestion = store.role_suggestions(task=task_id, role_id=role_id, source_registry=TASK_ROLE_SOURCE)
    if not suggestion:
        raise ValueError(f"no routing suggestion exists for task {task_id} and role {role_id}")
    return task


def apply_decisions(store: TaskStore,
    roles: dict[str, dict],
    accepts: list[tuple[int, str]],
    dismisses: list[tuple[int, str]],
    assignments: list[tuple[int, str]],
    dry_run: bool,
) -> int:
    decisions = 0
    for task_id, role_id in accepts:
        validate_task_role(store, roles, task_id, role_id)
        if not dry_run:
            store.review_role(task_id, role_id, TASK_ROLE_SOURCE, "accepted")
        decisions += 1
    for task_id, role_id in dismisses:
        validate_task_role(store, roles, task_id, role_id)
        if not dry_run:
            store.review_role(task_id, role_id, TASK_ROLE_SOURCE, "dismissed")
        decisions += 1
    for task_id, role_id in assignments:
        task = validate_task_role(store, roles, task_id, role_id)
        existing = sorted(
            row["assignee_id"]
            for row in store.assignments(task=task_id, assignee_type="role", assignment_type="assigned")
        )
        other_roles = [assignee_id for assignee_id in existing if assignee_id != role_id]
        if other_roles:
            raise ValueError(
                f"task {task_id} already has role owner(s): {', '.join(other_roles)}; refusing replacement"
            )
        if not dry_run:
            store.review_role(task_id, role_id, TASK_ROLE_SOURCE, "accepted")
            store.assign(task_id, "role", role_id, roles[role_id].get("name", role_id), "assigned")
        decisions += 1
    return decisions


def claim_all_tasks_for_owner(
    store: TaskStore, owner: dict[str, str], dry_run: bool
) -> int:
    task_ids = [task["id"] for task in store.list(include_archived=True)]
    existing = {
        row["task"]
        for row in store.assignments(assignee_type="user", assignee_id=owner["id"], assignment_type="assigned")
    }
    missing = [task_id for task_id in task_ids if task_id not in existing]
    if not dry_run:
        for task_id in missing:
            store.assign(task_id, "user", owner["id"], owner["name"], "assigned")
    return len(missing)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept", action="append", default=[], metavar="TASK_ID:ROLE_ID")
    parser.add_argument("--dismiss", action="append", default=[], metavar="TASK_ID:ROLE_ID")
    parser.add_argument("--assign", action="append", default=[], metavar="TASK_ID:ROLE_ID")
    parser.add_argument("--claim-all-owner", action="store_true", help="assign the canonical user Owner to every task")
    parser.add_argument("--dry-run", action="store_true", help="validate decisions without writing")
    parser.add_argument("--queue-only", action="store_true", help="write only the execution queue report")
    args = parser.parse_args()

    roles = role_catalog()
    accepts = parse_role_pairs(args.accept, "--accept")
    dismisses = parse_role_pairs(args.dismiss, "--dismiss")
    assignments = parse_role_pairs(args.assign, "--assign")
    store = open_task_store(None, AUTOMATION)
    owner = canonical_owner()
    owner_claims = claim_all_tasks_for_owner(store, owner, args.dry_run) if args.claim_all_owner else 0
    decisions = apply_decisions(store, roles, accepts, dismisses, assignments, args.dry_run)
    state = load_state(store)
    review_counts = {"active_tasks": 0, "review_tasks": 0, "ambiguous_high_tasks": 0}
    if not args.queue_only:
        review_counts = write_review_report(state, roles)
    queue_counts = write_queue_report(state, roles)

    mode = "dry-run" if args.dry_run else "applied"
    print(f"Role task workflow ({mode})")
    print(f"  canonical user owner: {owner['name']} (id={owner['id']})")
    print(f"  owner assignments added: {owner_claims}")
    print(f"  decisions processed: {decisions}")
    if not args.queue_only:
        for key, value in review_counts.items():
            print(f"  {key}: {value}")
        print(f"  review report: {display_path(REVIEW_REPORT)}")
    for key, value in queue_counts.items():
        print(f"  {key}: {value}")
    print(f"  queue report: {display_path(QUEUE_REPORT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)

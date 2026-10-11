#!/usr/bin/env python3
"""Validate atomic-role task, learning, and routing integrations."""

from __future__ import annotations

import argparse
import os
from datetime import date

from _env import PROJECT_ROOT, ARCHITECTURE_ROOT, REPORTS_DIR, display_path, skip_without_architecture  # noqa: E402
from cell_store import CellStoreError, open_cell_store
from role_integration_sync import ROLE_PATH_SOURCE
from role_integration_sync import sync as sync_role_integrations
from role_integration_sync import validate_registries
from task_owner import canonical_owner
from task_store import ClientConfigError, open_task_store


ROLE_CATALOG = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-CATALOG.yaml"
# The SQL source_registry literals below are stable provenance identifiers;
# they intentionally retain their original logical names after the YAML move.
AUTOMATION = "governance-reader"
ACTIVE_STATUSES = ("todo", "in_progress", "blocked")
REPORT_PATH = REPORTS_DIR / "ROLE-INTEGRATION.md"
PATH_REPORT_PATH = REPORTS_DIR / "ROLE-PATHS.md"
PORTFOLIO_REPORT_PATH = REPORTS_DIR / "ROLE-PORTFOLIO.md"


def main() -> int:
    skip_without_architecture("role_task_guard.py")  # standalone: no solo-architecture checkout -> SKIP, not FAIL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync", action="store_true", help="sync role learning links and task suggestions")
    parser.add_argument("--include-history", action="store_true", help="include historical tasks when syncing")
    parser.add_argument("--dry-run", action="store_true", help="report sync changes without writing")
    parser.add_argument("--report", action="store_true", help="write the role integration dashboard report")
    parser.add_argument("--live", action="store_true", help="require live database integration evidence, including in CI")
    args = parser.parse_args()
    if args.sync:
        return sync_role_integrations(args)

    print("=" * 70)
    print("Solo Empire — Atomic Role Integration Guard")
    print("=" * 70)
    errors: list[str] = []

    if not ROLE_CATALOG.is_file():
        errors.append(f"missing role catalog: {display_path(ROLE_CATALOG)}")
    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1

    try:
        _catalog, learning, routing, paths, catalog_by_id, learning_by_id = validate_registries()
    except (OSError, ValueError) as exc:
        print(f"  FAIL: {exc}")
        return 1

    role_ids = set(catalog_by_id)
    if os.environ.get("SOLO_EMPIRE_CI") == "1" and not (args.live or args.report):
        print(f"  PASS: tracked learning, routing, and path declarations for {len(role_ids)} roles")
        print("  SKIP: live task, skill, goal, and path integration evidence not checked in portable CI; use --live")
        return 0
    try:
        roles_skills = open_cell_store("roles-skills", AUTOMATION)
        goals_cell = open_cell_store("goals", AUTOMATION)
        projects_cell = open_cell_store("projects", AUTOMATION)

        store = open_task_store(None, AUTOMATION)
        all_tasks = store.list(include_archived=True)
        task_status = {task["id"]: task["status"] for task in all_tasks}
        active_task_ids = {task_id for task_id, status in task_status.items() if status in ACTIVE_STATUSES}
        all_assignments = store.assignments()
        role_assignments = [row for row in all_assignments if row["assignee_type"] == "role"]
        user_assignments = [
            row
            for row in all_assignments
            if row["assignee_type"] == "user" and row["assignment_type"] == "assigned"
        ]
        all_suggestions = store.role_suggestions()
        routing_suggestions = [
            row for row in all_suggestions if row["source_registry"] == "docs/ROLE-TASK-ROUTING.yaml"
        ]
        all_links = store.links()
        goal_links = [link for link in all_links if link["target_type"] == "goal"]
        project_links = [link for link in all_links if link["target_type"] == "project"]
        skill_links = [link for link in all_links if link["target_type"] == "skill"]
        goal_linked_tasks = {link["task"] for link in goal_links}
        project_linked_tasks = {link["task"] for link in project_links}

        all_skills = roles_skills.list("skills")
        skill_ids = {row["slug"]: row["id"] for row in all_skills}
        role_skill_rows = [
            row for row in roles_skills.list("role-skill-links") if row["source_registry"] == "docs/ROLE-LEARNING.yaml"
        ]
        all_path_step_rows = roles_skills.list("role-path-steps")
        path_step_rows = [row for row in all_path_step_rows if row["source_registry"] == ROLE_PATH_SOURCE]
        role_goal_rows = roles_skills.list("role-goal-links")
        allocation_rows = roles_skills.list("role-project-allocations")
        goal_rows = goals_cell.list("goals")
        project_rows = projects_cell.list("projects")
        work_item_ids = {row["id"] for row in projects_cell.list("work-items")}
        work_item_task_link_rows = projects_cell.list("work-item-task-links")
        goal_project_link_rows = goals_cell.list("goal-project-links")
        expected_role_skill_links: set[tuple[str, int]] = set()
        for role_id, row in learning_by_id.items():
            for slug in row.get("skill_slugs", []):
                if slug not in skill_ids:
                    errors.append(f"{role_id}: unknown learning skill slug: {slug}")
                else:
                    expected_role_skill_links.add((role_id, skill_ids[slug]))

        actual_role_skill_links = {(row["role_id"], row["skill_id"]) for row in role_skill_rows}
        missing_role_skill_links = expected_role_skill_links - actual_role_skill_links
        extra_role_skill_links = actual_role_skill_links - expected_role_skill_links
        if missing_role_skill_links:
            errors.append(f"missing role-skill link rows: {len(missing_role_skill_links)}")
        if extra_role_skill_links:
            errors.append(f"extra role-skill link rows: {len(extra_role_skill_links)}")

        expected_path_steps = {
            (role_id, step_key)
            for role_id, row in learning_by_id.items()
            for step_key in (
                "orient",
                *(f"learn-{index}" for index in range(1, len(row.get("skill_slugs", [])) + 1)),
                "practice",
                "evidence",
                "operate",
            )
        }
        actual_path_steps = {(row["role_id"], row["step_key"]) for row in path_step_rows}
        missing_path_steps = expected_path_steps - actual_path_steps
        extra_path_steps = actual_path_steps - expected_path_steps
        if missing_path_steps:
            errors.append(f"missing role-path step rows: {len(missing_path_steps)}")
        if extra_path_steps:
            errors.append(f"extra role-path step rows: {len(extra_path_steps)}")

        invalid_assignments = sorted(
            {row["assignee_id"] for row in role_assignments if row["assignee_id"] not in role_ids}
        )
        if invalid_assignments:
            errors.append(
                "unknown atomic role assignment(s): "
                + ", ".join(str(value) for value in invalid_assignments)
            )

        invalid_suggestions = sorted(
            {row["role_id"] for row in all_suggestions if row["role_id"] not in role_ids}
        )
        if invalid_suggestions:
            errors.append(
                "unknown role suggestion(s): "
                + ", ".join(str(value) for value in invalid_suggestions)
            )

        known_skill_ids = {row["id"] for row in all_skills}
        invalid_task_skill_links = sorted(
            {link["target_id"] for link in skill_links if link["target_id"] not in known_skill_ids}
        )
        if invalid_task_skill_links:
            errors.append(
                "unknown task learning skill link(s): "
                + ", ".join(str(value) for value in invalid_task_skill_links)
            )

        invalid_path_roles = sorted({row["role_id"] for row in path_step_rows if row["role_id"] not in role_ids})
        if invalid_path_roles:
            errors.append(
                "unknown role path role(s): "
                + ", ".join(str(value) for value in invalid_path_roles)
            )

        invalid_path_skills = sorted(
            {
                row["skill_id"]
                for row in all_path_step_rows
                if row["skill_id"] is not None and row["skill_id"] not in known_skill_ids
            }
        )
        if invalid_path_skills:
            errors.append(
                "unknown role path skill(s): "
                + ", ".join(str(value) for value in invalid_path_skills)
            )

        known_goal_ids = {row["id"] for row in goal_rows}
        invalid_goal_links = sorted(
            {
                row["role_id"]
                for row in role_goal_rows
                if row["role_id"] not in role_ids or row["goal_id"] not in known_goal_ids
            }
        )
        if invalid_goal_links:
            errors.append(
                "unknown role goal link(s): "
                + ", ".join(str(value) for value in invalid_goal_links)
            )

        invalid_task_goal_links = [
            link
            for link in goal_links
            if link["task"] not in task_status or link["target_id"] not in known_goal_ids
        ]
        if invalid_task_goal_links:
            errors.append(f"invalid task-goal link rows: {len(invalid_task_goal_links)}")

        known_project_ids = {row["id"] for row in project_rows}
        invalid_allocations = sorted(
            {
                row["role_id"]
                for row in allocation_rows
                if row["role_id"] not in role_ids
                or row["project_id"] not in known_project_ids
                or (row["goal_id"] is not None and row["goal_id"] not in known_goal_ids)
            }
        )
        if invalid_allocations:
            errors.append(
                "invalid role project allocation(s): "
                + ", ".join(str(value) for value in invalid_allocations)
            )

        invalid_goal_project_count = sum(
            1
            for row in goal_project_link_rows
            if row["goal_id"] not in known_goal_ids or row["project_id"] not in known_project_ids
        )
        invalid_task_project_count = sum(
            1
            for link in project_links
            if link["task"] not in task_status or link["target_id"] not in known_project_ids
        )
        work_item_links = [
            (row["task_id"], row["work_item_id"] not in work_item_ids) for row in work_item_task_link_rows
        ]
        invalid_work_item_count = sum(
            1 for task_id, work_item_missing in work_item_links if work_item_missing or task_id not in task_status
        )
        invalid_hierarchy_count = (
            invalid_goal_project_count + invalid_task_project_count + invalid_work_item_count
        )
        if invalid_hierarchy_count:
            errors.append(f"invalid portfolio hierarchy link rows: {invalid_hierarchy_count}")

        active_role_task_ids = sorted(
            {row["task"] for row in role_assignments if row["task"] in active_task_ids}
        )
        active_role_tasks_without_project = [
            task_id for task_id in active_role_task_ids if task_id not in project_linked_tasks
        ]
        if active_role_tasks_without_project:
            errors.append(
                "active role task(s) without a project link: "
                + ", ".join(str(task_id) for task_id in active_role_tasks_without_project)
            )

        work_item_linked_tasks = {row[0] for row in work_item_links}
        active_role_tasks_without_story = [
            task_id
            for task_id in active_role_task_ids
            if task_id in goal_linked_tasks and task_id not in work_item_linked_tasks
        ]
        if active_role_tasks_without_story:
            errors.append(
                "active role task(s) without an Epic/Story link: "
                + ", ".join(str(task_id) for task_id in active_role_tasks_without_story)
            )

        active_goal_ids = {row["id"] for row in goal_rows if row["status"] in ("planning", "active")}
        roles_with_goal = {row["role_id"] for row in role_goal_rows if row["goal_id"] in active_goal_ids}
        missing_goal_roles = sorted(role_id for role_id in role_ids if role_id not in roles_with_goal)
        if missing_goal_roles:
            errors.append(
                "roles without an active/planning goal: "
                + ", ".join(str(value) for value in missing_goal_roles)
            )

        roles_with_active_task = {
            row["assignee_id"] for row in role_assignments if row["task"] in active_task_ids
        }
        missing_task_roles = sorted(role_id for role_id in role_ids if role_id not in roles_with_active_task)
        if missing_task_roles:
            errors.append(
                "roles without an active task: "
                + ", ".join(str(role_id) for role_id in missing_task_roles)
            )

        unlinked_role_tasks = [
            task_id for task_id in active_role_task_ids if task_id not in goal_linked_tasks
        ]
        if unlinked_role_tasks:
            errors.append(
                "active role task(s) without a goal link: "
                + ", ".join(str(task_id) for task_id in unlinked_role_tasks)
            )

        task_count = len(all_tasks)
        assignment_count = len(role_assignments)
        role_assigned_tasks = {row["task"] for row in role_assignments}
        assigned_count = len(role_assigned_tasks)
        owner = canonical_owner()
        canonical_owner_id = owner["id"]
        canonical_owner_name = owner["name"]
        user_owner_count = len({row["task"] for row in user_assignments})
        canonical_owner_task_count = (
            len({row["task"] for row in user_assignments if str(row["assignee_id"]) == canonical_owner_id})
            if canonical_owner_id is not None
            else 0
        )
        accepted_tasks = {
            row["task"] for row in routing_suggestions if row["review_status"] == "accepted"
        }
        accepted_mapping_count = len(accepted_tasks)
        mapped_task_count = sum(
            1 for task_id in task_status if task_id in role_assigned_tasks or task_id in accepted_tasks
        )
        role_skill_count = len(role_skill_rows)
        task_skill_count = sum(
            1 for link in skill_links if link["attributes"].get("source_registry") == "docs/ROLE-LEARNING.yaml"
        )
        task_suggestion_count = len(routing_suggestions)
        role_path_count = len(path_step_rows)
        role_goal_link_count = len(role_goal_rows)
        task_goal_link_count = len(goal_links)
        role_project_allocation_count = len(allocation_rows)
        goal_project_link_count = len(goal_project_link_rows)
        work_item_count = len(work_item_ids)
        work_item_task_link_count = len(work_item_task_link_rows)
        task_project_link_count = len(project_links)
        suggested_task_count = len({row["task"] for row in routing_suggestions})
        tasks_by_assignee: dict[str, set[int]] = {}
        for row in role_assignments:
            tasks_by_assignee.setdefault(row["assignee_id"], set()).add(row["task"])
        by_role = [(role_id, len(task_ids)) for role_id, task_ids in sorted(tasks_by_assignee.items())]
    except (CellStoreError, ClientConfigError) as exc:
        errors.append(f"cell read failed: {exc}")
        task_count = assignment_count = assigned_count = 0
        canonical_owner_name = "Owner"
        user_owner_count = canonical_owner_task_count = 0
        accepted_mapping_count = 0
        mapped_task_count = 0
        role_skill_count = task_skill_count = task_suggestion_count = suggested_task_count = 0
        role_path_count = role_goal_link_count = task_goal_link_count = 0
        role_project_allocation_count = 0
        goal_project_link_count = work_item_count = 0
        work_item_task_link_count = task_project_link_count = 0
        by_role = []

    if errors:
        return _finish(errors)

    print(f"  ✅ atomic role IDs: {len(role_ids)}")
    print(f"  ✅ learning registry roles: {len(learning_by_id)}")
    print(f"  ✅ task routing rules: {len(routing.get('rules', []))}")
    print(f"  ✅ role-skill links: {role_skill_count}")
    print(f"  ✅ task records: {task_count}")
    print(f"  ✅ task-skill links: {task_skill_count}")
    print(f"  ✅ task-role suggestions: {task_suggestion_count} across {suggested_task_count} tasks")
    print(f"  ✅ role path steps: {role_path_count}")
    print(f"  ✅ role-goal links: {role_goal_link_count}")
    print(f"  ✅ task-goal links: {task_goal_link_count}")
    print(f"  ✅ role-project allocations: {role_project_allocation_count}")
    print(f"  ✅ goal-project links: {goal_project_link_count}")
    print(f"  ✅ Epic/Story work items: {work_item_count}")
    print(f"  ✅ work-item task links: {work_item_task_link_count}")
    print(f"  ✅ task-project links: {task_project_link_count}")
    print(f"  ✅ role assignment rows: {assignment_count}")
    print(f"  ✅ tasks assigned to an atomic role: {assigned_count}")
    print(f"  ℹ️ tasks without an atomic-role assignment: {task_count - assigned_count}")
    print(f"  ✅ tasks with a user owner: {user_owner_count}")
    print(f"  ✅ tasks owned by canonical user {canonical_owner_name}: {canonical_owner_task_count}")
    print(f"  ℹ️ tasks without canonical user owner: {task_count - canonical_owner_task_count}")
    print(f"  ✅ accepted task-role mappings: {accepted_mapping_count}")
    print(f"  ℹ️ tasks without assignment or accepted mapping: {task_count - mapped_task_count}")
    for role_id, count in by_role:
        print(f"     - {role_id}: {count}")
    if args.report:
        _write_report(catalog_by_id, learning_by_id, paths)
        print(f"  ✅ dashboard report: {display_path(REPORT_PATH)}")
        print(f"  ✅ path report: {display_path(PATH_REPORT_PATH)}")
        print(f"  ✅ portfolio report: {display_path(PORTFOLIO_REPORT_PATH)}")
    print("  ✅ every assignment and suggestion uses an ID from ROLE-CATALOG.yaml")
    print("Result: OK")
    return 0


def _finish(errors: list[str]) -> int:
    for error in errors:
        print(f"  FAIL: {error}")
    print(f"Result: FAIL ({len(errors)} errors)")
    return 1


def _write_report(catalog_by_id: dict, learning_by_id: dict, paths: dict) -> None:
    store = open_task_store(None, AUTOMATION)
    roles_skills = open_cell_store("roles-skills", AUTOMATION)
    slug_by_skill = {row["id"]: row["slug"] for row in roles_skills.list("skills")}
    skill_rows: dict[str, list[str]] = {}
    path_rows: dict[str, str] = {}
    for row in sorted(
        (
            link
            for link in roles_skills.list("role-skill-links")
            if link["source_registry"] == "docs/ROLE-LEARNING.yaml" and link["skill_id"] in slug_by_skill
        ),
        key=lambda link: (link["role_id"], link["id"]),
    ):
        skill_rows.setdefault(row["role_id"], []).append(slug_by_skill[row["skill_id"]])
        path_rows.setdefault(row["role_id"], row["learning_path"])
    tasks_by_assignee: dict[str, set[int]] = {}
    for row in store.assignments(assignee_type="role"):
        tasks_by_assignee.setdefault(row["assignee_id"], set()).add(row["task"])
    assignment_rows = {role_id: len(task_ids) for role_id, task_ids in tasks_by_assignee.items()}
    active_task_ids = {task["id"] for task in store.list(status=list(ACTIVE_STATUSES), include_archived=True)}
    routing_suggestions = store.role_suggestions(source_registry="docs/ROLE-TASK-ROUTING.yaml")
    suggested_by_role: dict[str, set[int]] = {}
    accepted_by_role: dict[str, set[int]] = {}
    for row in routing_suggestions:
        if row["review_status"] == "suggested" and row["task"] in active_task_ids:
            suggested_by_role.setdefault(row["role_id"], set()).add(row["task"])
        if row["review_status"] == "accepted":
            accepted_by_role.setdefault(row["role_id"], set()).add(row["task"])
    suggestion_rows = {role_id: len(task_ids) for role_id, task_ids in suggested_by_role.items()}
    accepted_mapping_rows = {role_id: len(task_ids) for role_id, task_ids in accepted_by_role.items()}

    lines = [
        "---",
        "title: Atomic Role Integration Dashboard",
        "tags: [dashboard, roles, tasks, learning, routing]",
        f"updated: {date.today().isoformat()}",
        "status: generated",
        "source: docs/registries/ROLE-CATALOG.yaml, docs/registries/ROLE-LEARNING.yaml, docs/registries/ROLE-TASK-ROUTING.yaml, docs/registries/ROLE-PATHS.yaml, SQLite",
        "---",
        "",
        "# Atomic Role Integration Dashboard",
        "",
        "> Generated from the canonical role registries and SQLite projections.\n",
        "The `suggestions` column is routing evidence for review. It is not a human\n",
        "ownership assignment; approved ownership remains in `task_assignments`.",
        "",
        "| Atomic role | Role groups | Learning skills | Learning path | Active task suggestions | Accepted mappings | Assigned tasks |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for role_id, role in catalog_by_id.items():
        groups = ", ".join(role.get("role_groups", []))
        skills = ", ".join(skill_rows.get(role_id, [])) or "—"
        path = path_rows.get(role_id, "—")
        suggestions = suggestion_rows.get(role_id, 0)
        accepted_mappings = accepted_mapping_rows.get(role_id, 0)
        assignments = assignment_rows.get(role_id, 0)
        lines.append(
            f"| `{role_id}` | {groups} | {skills} | `{path}` | {suggestions} | {accepted_mappings} | {assignments} |"
        )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_paths_report(catalog_by_id, learning_by_id, paths)
    _write_portfolio_report(catalog_by_id)


def _write_portfolio_report(catalog_by_id: dict) -> None:
    """Write the many-to-many role → goal → project operating view."""
    store = open_task_store(None, AUTOMATION)
    roles_skills = open_cell_store("roles-skills", AUTOMATION)
    goal_by_id = {
        goal["id"]: goal
        for goal in open_cell_store("goals", AUTOMATION).list("goals")
        if goal["status"] in ("planning", "active")
    }
    goals_by_role: dict[str, list[str]] = {}
    role_goal_rows = sorted(
        (link for link in roles_skills.list("role-goal-links") if link["goal_id"] in goal_by_id),
        key=lambda link: (
            link["role_id"],
            link["priority"] is not None,
            link["priority"] if link["priority"] is not None else 0,
            link["goal_id"],
        ),
    )
    for link in role_goal_rows:
        goal = goal_by_id[link["goal_id"]]
        allocation = link["allocation_percent"] or 0
        goals_by_role.setdefault(link["role_id"], []).append(
            f"#{goal['id']} {goal['title']} ({goal['progress_percent']}%, {goal['status']}, {link['link_type']}, {allocation}%)"
        )
    tasks_by_role: dict[str, list[str]] = {}
    active_tasks = {task["id"]: task for task in store.list(status=list(ACTIVE_STATUSES), include_archived=True)}
    role_task_rows = [
        (row["assignee_id"], active_tasks[row["task"]])
        for row in store.assignments(assignee_type="role")
        if row["task"] in active_tasks
    ]
    # SQLite sorted NULL priority first.
    role_task_rows.sort(
        key=lambda item: (
            item[0],
            item[1]["priority"] is not None,
            item[1]["priority"] if item[1]["priority"] is not None else 0,
            item[1]["id"],
        )
    )
    for assignee_id, task in role_task_rows:
        tasks_by_role.setdefault(assignee_id, []).append(
            f"#{task['id']} {task['title']} ({task['status']}, {task['project'] or 'no-project'})"
        )
    projects_by_role: dict[str, list[str]] = {}
    project_by_id = {project["id"]: project for project in open_cell_store("projects", AUTOMATION).list("projects")}
    allocations = sorted(
        (row for row in roles_skills.list("role-project-allocations") if row["project_id"] in project_by_id),
        key=lambda row: (
            row["role_id"],
            project_by_id[row["project_id"]]["slug"],
            row["goal_id"] is not None,
            row["goal_id"] if row["goal_id"] is not None else 0,
        ),
    )
    for row in allocations:
        project = project_by_id[row["project_id"]]
        projects_by_role.setdefault(row["role_id"], []).append(
            f"{project['slug']} ({project['progress_percent']}%, {row['status']}, {row['allocation_percent']}%, goal {row['goal_id'] or '—'})"
        )

    lines = [
        "---",
        "title: Atomic Role Portfolio",
        "tags: [dashboard, roles, goals, projects, tasks, portfolio]",
        f"updated: {date.today().isoformat()}",
        "status: generated",
        "source: docs/registries/ROLE-CATALOG.yaml, SQLite",
        "---",
        "",
        "# Atomic Role Portfolio",
        "",
        "> Every canonical role has a goal and an actionable task. All goals can progress continuously and independently; `planning` means defined but not started, while `active` means work is underway.",
        "",
        "| Role | Active/planning goals | Active tasks | Projects / allocation |",
        "|---|---|---|---|",
    ]
    for role_id in catalog_by_id:
        goals = "<br>".join(goals_by_role.get(role_id, ["⚠️ missing goal"]))
        tasks = "<br>".join(tasks_by_role.get(role_id, ["⚠️ missing active task"]))
        projects = "<br>".join(projects_by_role.get(role_id, ["—"]))
        lines.append(f"| `{role_id}` | {goals} | {tasks} | {projects} |")
    lines.extend(
        [
            "",
            "## Operating rules",
            "",
            "1. A role may own or support multiple goals and projects.",
            "2. A role may advance multiple goals/projects at the same time; there is no round gate.",
            "3. Use priority and project allocation to describe attention/capacity, not to block independent progress.",
            "4. Every active role task must link to at least one goal through `task_goal_links`.",
        ]
    )
    PORTFOLIO_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_paths_report(catalog_by_id: dict, learning_by_id: dict, paths: dict) -> None:
    """Write the actionable per-role path and current next action."""
    store = open_task_store(None, AUTOMATION)
    roles_skills = open_cell_store("roles-skills", AUTOMATION)
    skill_by_id = {row["id"]: row for row in roles_skills.list("skills")}
    learning_links = sorted(
        (
            link
            for link in roles_skills.list("role-skill-links")
            if link["source_registry"] == "docs/ROLE-LEARNING.yaml" and link["skill_id"] in skill_by_id
        ),
        key=lambda link: (link["role_id"], link["id"]),
    )
    role_skills: dict[str, list[dict]] = {}
    for link in learning_links:
        skill = skill_by_id[link["skill_id"]]
        role_skills.setdefault(link["role_id"], []).append(
            {
                "role_id": link["role_id"],
                "link_type": link["link_type"],
                "skill_id": skill["id"],
                "slug": skill["slug"],
                "name": skill["name"],
                "progress": skill["progress_percent"],
            }
        )
    active_goal_ids = {
        goal["id"] for goal in open_cell_store("goals", AUTOMATION).list("goals") if goal["status"] in ("planning", "active")
    }
    active_goals: dict[str, int] = {}
    for link in roles_skills.list("role-goal-links"):
        if link["goal_id"] in active_goal_ids:
            active_goals[link["role_id"]] = active_goals.get(link["role_id"], 0) + 1
    done_task_ids = {task["id"] for task in store.list(status=["done"], include_archived=True)}
    done_by_role: dict[str, set[int]] = {}
    for row in store.assignments(assignee_type="role"):
        if row["task"] in done_task_ids:
            done_by_role.setdefault(row["assignee_id"], set()).add(row["task"])
    done_practice = {role_id: len(task_ids) for role_id, task_ids in done_by_role.items()}
    evidence_logs: dict[str, int] = {}
    evidence_paths: dict[str, set[str]] = {}
    progress_logs = roles_skills.list("progress-log")
    for role_id, role in catalog_by_id.items():
        title = f"Role practice evidence: {role['name']}"
        skill_id_set = {link["skill_id"] for link in learning_links if link["role_id"] == role_id}
        if not skill_id_set:
            continue
        for row in progress_logs:
            if row["skill_id"] in skill_id_set and row.get("title") == title and (row.get("evidence_url") or "") != "":
                evidence_logs[role_id] = evidence_logs.get(role_id, 0) + 1
                evidence_paths.setdefault(role_id, set()).add(row["evidence_url"])

    defaults = paths["defaults"]
    path_by_id = {row["role_id"]: row for row in paths["roles"]}
    lines = [
        "---",
        "title: Atomic Role Capability Paths",
        "tags: [dashboard, roles, goals, learning, practice, evidence]",
        f"updated: {date.today().isoformat()}",
        "status: generated",
        "source: docs/registries/ROLE-CATALOG.yaml, docs/registries/ROLE-LEARNING.yaml, docs/registries/ROLE-PATHS.yaml, SQLite",
        "---",
        "",
        "# Atomic Role Capability Paths",
        "",
        "> Generated path status. Every role has a capability baseline, and every linked goal/project may progress continuously; allocation describes attention and capacity, not a round gate.",
        "",
        "## วิธีอ่านสถานะ",
        "",
        "- orient: role ยังไม่มี active/planning goal (ถือเป็น coverage defect)",
        "- learn: goal ถูกเลือกแล้ว แต่ core skill ยังไม่ถึง target",
        "- practice: skill ถึง target แล้ว แต่ยังไม่มี task ที่ assign ให้ role และทำเสร็จ",
        "- evidence: มีงานฝึกแล้ว แต่ยังไม่มี progress-log evidence_url",
        "- operate: มี learning, practice และ evidence ขั้นต้นแล้ว; ควรทำซ้ำและ review metric",
        "",
        f"ค่าเริ่มต้นของ path คือ core skill >= {defaults['core_skill_target_percent']}% และ supporting skill >= {defaults['supporting_skill_target_percent']}%.",
        "Baseline goal ถูกสร้างจาก role catalog เพื่อให้ coverage ครบ; ทุก goal เดินต่อพร้อมกันได้ และ priority/project allocation ใช้บอก focus กับ capacity.",
        "",
        "| Role | Target outcome | Phase | Goal links | Learn now | Next action | Evidence / practice |",
        "|---|---|---|---:|---|---|---|",
    ]
    for role_id, role in catalog_by_id.items():
        row = path_by_id[role_id]
        skills = role_skills.get(role_id, [])
        skill_gaps = [
            skill for skill in skills
            if skill["progress"] < (
                int(defaults["core_skill_target_percent"])
                if skill["link_type"] == "core"
                else int(defaults["supporting_skill_target_percent"])
            )
        ]
        goal_count = active_goals.get(role_id, 0)
        if goal_count == 0:
            phase = "orient"
            next_action = "เลือกหรือสร้าง goal ที่จะใช้เป็น outcome แล้ว link เข้ากับ role นี้"
        elif skill_gaps:
            phase = "learn"
            target = (
                defaults["core_skill_target_percent"]
                if skill_gaps[0]["link_type"] == "core"
                else defaults["supporting_skill_target_percent"]
            )
            next_action = f"เรียน {skill_gaps[0]['name']} ต่อถึง {target}% แล้วบันทึก progress_log"
        elif not done_practice.get(role_id):
            phase = "practice"
            next_action = row["practice_action"]
            if evidence_logs.get(role_id):
                next_action = (
                    f"ทบทวน evidence {evidence_logs[role_id]} รายการ แก้ known gap "
                    "และทำ task ให้เสร็จเมื่อ acceptance ผ่าน"
                )
        elif not evidence_logs.get(role_id):
            phase = "evidence"
            next_action = "แนบ evidence_url ให้ progress log หรือ artifact ของงานฝึก"
        else:
            phase = "operate"
            next_action = "รัน workflow ซ้ำและทำ outcome/metric review"
        learn_now = ", ".join(
            f"{skill['name']} ({skill['progress']}%)"
            for skill in skills
            if skill["progress"] < (
                int(defaults["core_skill_target_percent"])
                if skill["link_type"] == "core"
                else int(defaults["supporting_skill_target_percent"])
            )
        ) or "ถึง target เบื้องต้นแล้ว"
        practice = row["evidence_artifacts"][0]
        role_cell = chr(96) + role_id + chr(96)
        phase_cell = chr(96) + phase + chr(96)
        if evidence_logs.get(role_id):
            evidence_cell = (
                f"{evidence_logs[role_id]} log(s): "
                + "<br>".join(
                    f"{chr(96)}{path}{chr(96)}"
                    for path in sorted(evidence_paths.get(role_id, set()))
                )
            )
        else:
            evidence_cell = f"{chr(96)}{practice}{chr(96)}"
        lines.append(
            f"| {role_cell} | {role['summary']} | {phase_cell} | {goal_count} | "
            f"{learn_now} | {next_action} | {evidence_cell} |"
        )
    lines.extend(
        [
            "",
            "## ลำดับที่ต้องทำต่อ",
            "",
            "1. เริ่มงานของ role/goal/project ใดก็ได้พร้อมกันตาม dependency และ capacity",
            "2. ตั้ง priority และ allocation เพื่อสะท้อน focus ไม่ใช่เพื่อบังคับให้รอเป็นรอบ",
            "3. เรียน core skill ตาม learning path และบันทึก progress_log พร้อม next_steps",
            "4. ทำ active task ที่ link กับ goal และ assign ให้ atomic role",
            "5. เก็บ artifact หรือ URL เป็น evidence ก่อนขยับไป phase ถัดไป",
        ]
    )
    PATH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("role_task_guard.py")
    raise SystemExit(main())

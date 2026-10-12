#!/usr/bin/env python3
"""Validate the personal-role and portfolio-context projection."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from _env import PROJECT_ROOT, ARCHITECTURE_ROOT, display_path, skip_without_architecture
from portfolio_context_seed import REPORT_PATH, seed
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError, open_task_store


AUTOMATION = "governance-reader"
CONTEXT_REGISTRY = ARCHITECTURE_ROOT / "docs" / "registries" / "PORTFOLIO-CONTEXTS.yaml"
PERSONAL_ROLE_REGISTRY = ARCHITECTURE_ROOT / "docs" / "registries" / "PERSONAL-ROLE-CATALOG.yaml"


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"registry must be a mapping: {display_path(path)}")
    return value


def main() -> int:
    skip_without_architecture("portfolio_context_guard.py")  # standalone: no architecture-docs checkout -> SKIP, not FAIL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="write the generated context dashboard")
    args = parser.parse_args()
    errors: list[str] = []
    if not CONTEXT_REGISTRY.is_file():
        errors.append(f"missing context registry: {display_path(CONTEXT_REGISTRY)}")
    if not PERSONAL_ROLE_REGISTRY.is_file():
        errors.append(f"missing personal role registry: {display_path(PERSONAL_ROLE_REGISTRY)}")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    try:
        contexts = load_yaml(CONTEXT_REGISTRY)
        personal_roles = load_yaml(PERSONAL_ROLE_REGISTRY)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}")
        return 1

    try:
        goals_cell = open_cell_store("goals", AUTOMATION)
        projects_cell = open_cell_store("projects", AUTOMATION)
        roles_cell = open_cell_store("roles-skills", AUTOMATION)
        task_store = open_task_store(None, AUTOMATION)

        context_rows = projects_cell.list("portfolio-contexts")
        role_rows = roles_cell.list("personal-roles")
        goal_rows = goals_cell.list("goals")
        project_rows = projects_cell.list("projects")
        goal_context_links = goals_cell.list("goal-context-links")
        project_context_links = projects_cell.list("project-context-links")
    except (CellStoreError, ClientConfigError) as exc:
        print(f"FAIL: {exc}")
        return 1

    try:
        expected_contexts = {row["slug"] for row in contexts.get("explicit_contexts", [])}
        actual_contexts = {row["slug"] for row in context_rows}
        missing_contexts = sorted(expected_contexts - actual_contexts)
        if missing_contexts:
            errors.append("missing explicit portfolio contexts: " + ", ".join(missing_contexts))

        referenced_role_contexts = {
            context_slug
            for role in personal_roles.get("roles", [])
            for context_slug in role.get("contexts", [])
        }
        missing_role_contexts = sorted(referenced_role_contexts - actual_contexts)
        if missing_role_contexts:
            errors.append(
                "personal role references missing portfolio contexts: "
                + ", ".join(missing_role_contexts)
            )

        expected_roles = {row["id"] for row in personal_roles.get("roles", [])}
        actual_roles = {row["slug"] for row in role_rows}
        missing_roles = sorted(expected_roles - actual_roles)
        if missing_roles:
            errors.append("missing personal roles: " + ", ".join(missing_roles))

        def without(rows: list[dict], links: list[dict], row_key: str, link_key: str) -> int:
            linked = {link[link_key] for link in links}
            return sum(1 for row in rows if row[row_key] not in linked)

        goal_tasks = goals_cell.list("goal-tasks")
        checks = (
            ("goals without context", lambda: without(goal_rows, goal_context_links, "id", "goal_id")),
            ("projects without context", lambda: without(project_rows, project_context_links, "id", "project_id")),
            (
                "tasks without context",
                lambda: len(
                    {task["id"] for task in task_store.list(include_archived=True)}
                    - {link["task"] for link in task_store.links(target_type="context")}
                ),
            ),
            (
                "personal roles without context",
                lambda: without(
                    role_rows, roles_cell.list("personal-role-context-links"), "id", "personal_role_id"
                ),
            ),
            (
                "personal roles without Goal",
                lambda: without(role_rows, roles_cell.list("personal-role-goal-links"), "id", "personal_role_id"),
            ),
            (
                "personal roles without Task",
                lambda: without(role_rows, roles_cell.list("personal-role-task-links"), "id", "personal_role_id"),
            ),
            (
                "goals without project",
                lambda: without(goal_rows, goals_cell.list("goal-project-links"), "id", "goal_id"),
            ),
            (
                "goals without Epic/Story hierarchy",
                lambda: without(goal_rows, projects_cell.list("work-items"), "id", "goal_id"),
            ),
            (
                "goal tasks without project bridge",
                lambda: without(goal_tasks, goals_cell.list("goal-task-project-links"), "id", "goal_task_id"),
            ),
            (
                "goal tasks without Story bridge",
                lambda: without(
                    goal_tasks, projects_cell.list("work-item-goal-task-links"), "id", "goal_task_id"
                ),
            ),
        )
        for label, query in checks:
            count = query()
            if count:
                errors.append(f"{label}: {count}")

        goal_ids = {row["id"] for row in goal_rows}
        project_ids = {row["id"] for row in project_rows}
        context_ids = {row["id"] for row in context_rows}
        invalid_links = sum(
            1 for link in goal_context_links if link["goal_id"] not in goal_ids or link["context_id"] not in context_ids
        ) + sum(
            1
            for link in project_context_links
            if link["project_id"] not in project_ids or link["context_id"] not in context_ids
        )
        task_numbers = {task["id"] for task in task_store.list(include_archived=True)}
        invalid_links += sum(
            1
            for link in task_store.links(target_type="context")
            if link["task"] not in task_numbers or link["target_id"] not in context_ids
        )
        if invalid_links:
            errors.append(f"invalid goal/project/task context links: {invalid_links}")
        task_count = task_store.stats(include_archived=True)["total"]
    except (CellStoreError, ClientConfigError) as exc:
        print(f"FAIL: {exc}")
        return 1

    if args.report and not errors:
        # The seed writes only the generated report in this mode; the database
        # projection is already current and the seed remains idempotent.
        seed(dry_run=False)
    elif not args.report and not errors:
        pending = seed(dry_run=True)
        pending = {
            key: value
            for key, value in pending.items()
            if key.endswith("_created") and value
        }
        if pending:
            errors.append("projection is stale: " + ", ".join(f"{key}={value}" for key, value in pending.items()))

    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1

    context_count = len(context_rows)
    role_count = len(role_rows)
    goal_count = len(goal_rows)
    project_count = len(project_rows)
    print("Portfolio Context Guard")
    print(f"  contexts: {context_count}")
    print(f"  personal roles: {role_count}")
    print(f"  goals covered: {goal_count}")
    print(f"  projects covered: {project_count}")
    print(f"  tasks covered: {task_count}")
    print("  Result: OK")
    return 0


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("portfolio_context_guard.py")
    raise SystemExit(main())

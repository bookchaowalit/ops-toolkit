#!/usr/bin/env python3
"""Seed an idempotent role portfolio and work hierarchy.

The role catalog is projected into a continuous Goal → Epic/Story → Project →
Task graph. Existing tasks remain the canonical execution records; this script
only adds explicit links and typed work containers so progress can roll up.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

import yaml

from _env import PROJECT_ROOT, ARCHITECTURE_ROOT
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError, open_task_store


ROLE_CATALOG = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-CATALOG.yaml"
ROLE_PATHS = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-PATHS.yaml"
# Stable SQLite provenance key; do not turn this into a filesystem lookup.
SOURCE = "docs/ROLE-CATALOG.yaml#role-coverage-baseline-v1"
AUTOMATION_ID = "portfolio-seeder"
PROJECT_SLUG = "solo-empire"
AI_PROJECT_SLUG = "book-ai"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def category_for_role(category: str) -> str:
    if category in {"finance", "accounting", "tax"}:
        return "financial"
    if category in {"design", "documentation", "growth", "language", "creative"}:
        return "creative"
    if category in {
        "architecture", "engineering", "quality", "data", "ai", "reliability",
        "security", "data-reliability", "platform", "delivery-reliability",
        "ai-platform", "data-ai", "data-governance", "security-operations",
        "privacy",
    }:
        return "tech"
    return "business"


def goal_title(role_name: str) -> str:
    return f"Build operating capability: {role_name}"


def task_title(role_name: str) -> str:
    return f"Role baseline: define outcome and first evidence for {role_name}"


def _json_tags(role_id: str, goal_id: int) -> str:
    return json.dumps(
        ["role-baseline", f"role:{role_id}", f"goal:{goal_id}", "phase:orient"],
        ensure_ascii=False,
    )


class _DryRunCell:
    """Reads go to the cell; writes are skipped and a create answers a row with a negative placeholder id."""

    _next_id = 0

    def __init__(self, cell) -> None:
        self._cell = cell

    def __getattr__(self, name):
        return getattr(self._cell, name)

    def create(self, plural, fields, **_kwargs):
        _DryRunCell._next_id -= 1
        return {**fields, "id": _DryRunCell._next_id}

    def update(self, plural, record_id, fields, **_kwargs):
        return {"id": record_id, **fields}

    def delete(self, plural, record_id):
        return True


_DRY_RUN = {"on": False}


class _DryRunTaskStore:
    """Reads go to the Tasks API; writes are skipped (a create answers a negative placeholder number)."""

    _next_id = 0

    def __init__(self, store) -> None:
        self._store = store

    def __getattr__(self, name):
        return getattr(self._store, name)

    def create(self, fields, **_kwargs):
        _DryRunTaskStore._next_id -= 1
        return _DryRunTaskStore._next_id

    def update(self, number, fields, **_kwargs):
        return None

    def assign(self, *_args, **_kwargs):
        return None

    def link(self, task, target_type, target_id, attributes=None, *, update=False):
        if task > 0 and target_id > 0 and self._store.links(task=task, target_type=target_type, target_id=target_id):
            return "unchanged"
        return "created"

    def unlink(self, *_args, **_kwargs):
        return False


def _task_store():
    store = open_task_store(None, "portfolio-seeder")
    return _DryRunTaskStore(store) if _DRY_RUN["on"] else store
_PROJECT_IDS: dict[str, int] = {}  # slug -> id, filled once per run (slug is not a filter field)


def _cell(name: str):
    store = open_cell_store(name, AUTOMATION_ID)
    return _DryRunCell(store) if _DRY_RUN["on"] else store


def _project_id(slug: str) -> int | None:
    if slug not in _PROJECT_IDS:
        for row in _cell("projects").list("projects"):
            _PROJECT_IDS[row["slug"]] = row["id"]
    return _PROJECT_IDS.get(slug)


def ensure_goal(role: dict) -> tuple[int, bool]:
    role_id = role["id"]
    goals, roles = _cell("goals"), _cell("roles-skills")
    linked = [
        link["goal_id"] for link in roles.list("role-goal-links", role_id=role_id) if link.get("source") == SOURCE
    ]
    existing = sorted(goal_id for goal_id in linked if goals.get("goals", goal_id) is not None)
    if existing:
        return existing[0], False
    # A goal created by a run whose link write failed: reuse it (the old SQL wrote both in one transaction).
    orphan = sorted(row["id"] for row in goals.list("goals", domain=f"role:{role_id}")
                    if row["title"] == goal_title(role["name"]))
    if orphan:
        _link_role_goal(roles, role_id, orphan[0])
        return orphan[0], False

    description = (
        f"Build a durable, evidence-backed capability path for the atomic role "
        f"{role['name']}. {role['summary']} This goal can progress continuously "
        "alongside other goals; priority and allocation describe attention, not a round gate."
    )
    goal = goals.create("goals", {
        "title": goal_title(role["name"]),
        "description": description,
        "type": "yearly",
        "category": category_for_role(role.get("category", "")),
        "status": "planning",
        "priority": 4,
        "start_date": date.today().isoformat(),
        "progress_percent": 0,
        "key_results": json.dumps(
            [
                "Complete the role learning path",
                "Produce one role-specific practice output",
                "Attach reviewable evidence",
            ],
        ),
        "notes": "Generated because this canonical role had no active/planning goal.",
        "domain": f"role:{role_id}",
    }, idempotency_key=f"role-portfolio-goal-{role_id}")
    goal_id = int(goal["id"])
    _link_role_goal(roles, role_id, goal_id)
    return goal_id, True


def _link_role_goal(roles, role_id: str, goal_id: int) -> None:
    roles.create("role-goal-links", {
        "role_id": role_id, "goal_id": goal_id, "link_type": "primary", "source": SOURCE,
        "priority": 4, "allocation_percent": 0,
    }, idempotency_key=f"role-portfolio-goal-link-{role_id}-{goal_id}")


def ensure_goal_task(goal_id: int, role: dict) -> tuple[int, bool]:
    title = task_title(role["name"])
    goals = _cell("goals")
    existing = next((row for row in goals.list("goal-tasks", goal_id=goal_id) if row["title"] == title), None)
    if existing:
        return existing["id"], False

    practice = next(
        (
            row.get("practice_action", "")
            for row in load_yaml(ROLE_PATHS).get("roles", [])
            if row.get("role_id") == role["id"]
        ),
        "Complete the first bounded practice output for this role.",
    )
    row = goals.create("goal-tasks", {
        "goal_id": goal_id,
        "title": title,
        "description": f"Start with the role boundary and produce a bounded practice output: {practice}",
        "status": "todo",
        "priority": 3,
        "sort_order": 1,
    })
    return int(row["id"]), True


def _active_role_tasks(role_id: str) -> list[dict]:
    """Active (todo/in_progress/blocked) tasks assigned to a role, ordered by priority then id."""
    store = _task_store()
    assigned = {
        int(assignment["task"])
        for assignment in store.assignments(assignee_type="role", assignee_id=role_id)
    }
    if not assigned:
        return []
    tasks = [
        task
        for task in store.list(status=["todo", "in_progress", "blocked"], include_archived=True)
        if int(task["id"]) in assigned
    ]
    return sorted(tasks, key=lambda task: (-1 if task["priority"] is None else task["priority"], task["id"]))


def ensure_role_task(role: dict,
    goal_id: int,
    learning_slugs: list[str],
    skill_ids: dict[str, int],
) -> tuple[int, bool]:
    role_id = role["id"]
    active = _active_role_tasks(role_id)
    if active:
        return active[0]["id"], False

    now = datetime.now().isoformat(timespec="seconds")
    practice = next(
        (
            row.get("practice_action", "")
            for row in load_yaml(ROLE_PATHS).get("roles", [])
            if row.get("role_id") == role_id
        ),
        "Complete the first bounded practice output for this role.",
    )
    description = (
        f"Baseline task for {role['name']}. Define the role boundary, select a "
        f"concrete project/goal, and produce the first evidence-backed output. "
        f"Practice direction: {practice}"
    )
    store = _task_store()
    task_id = int(store.create(
        {
            "title": task_title(role["name"]),
            "description": description,
            "status": "todo",
            "priority": 4,
            "project": PROJECT_SLUG,
            "tags": _json_tags(role_id, goal_id),
            "category": "learning",
            "notes": f"source={SOURCE}; role={role_id}; goal={goal_id}",
            "estimated_minutes": 90,
        },
        legacy={"sync_status": "local", "created_at": now, "updated_at": now},
    ))
    store.assign(task_id, "role", role_id, role["name"], "assigned")
    store.link(task_id, "goal", goal_id, {"link_type": "primary", "source": SOURCE}, update=False)
    for slug in learning_slugs:
        skill_id = skill_ids.get(slug)
        if skill_id is None:
            continue
        store.link(
            task_id, "skill", skill_id,
            {
                "link_type": "accepted",
                "match_reason": f"role baseline for {role_id}",
                "source_registry": "docs/ROLE-LEARNING.yaml",
            },
            update=True,
        )
    return task_id, True


def ensure_project_allocation(role_id: str, goal_id: int) -> bool:
    project_id = _project_id(PROJECT_SLUG)
    if project_id is None:
        raise RuntimeError(f"required portfolio project is missing: {PROJECT_SLUG}")
    roles = _cell("roles-skills")
    if roles.list("role-project-allocations", role_id=role_id, project_id=project_id, goal_id=goal_id):
        return False
    roles.create("role-project-allocations", {
        "role_id": role_id,
        "project_id": project_id,
        "goal_id": goal_id,
        "allocation_percent": 0,
        "priority": 4,
        "status": "planned",
        "source": SOURCE,
        "notes": "Continuous portfolio allocation baseline; adjust priority and allocation as work progresses.",
    })
    return True


def ensure_active_task_goal_links(role_id: str, goal_id: int) -> int:
    """Link existing active role tasks to the role's selected active goal."""
    link = next(iter(_cell("roles-skills").list("role-goal-links", role_id=role_id, goal_id=goal_id)), None)
    link_type = link["link_type"] if link else "primary"
    rows = [(task["id"],) for task in sorted(_active_role_tasks(role_id), key=lambda task: task["id"])]
    store = _task_store()
    created = 0
    for row in rows:
        created += int(store.link(
            row[0], "goal", goal_id, {"link_type": link_type, "source": SOURCE}, update=False,
        ) == "created")
    return created


def _task_progress(status: str, explicit_progress: int | None) -> int:
    if status == "done":
        return 100
    if explicit_progress and explicit_progress > 0:
        return max(0, min(100, int(explicit_progress)))
    return {"in_progress": 50, "blocked": 0, "cancelled": 0}.get(status, 0)


def ensure_project(slug: str, name: str, domain: str) -> tuple[int, bool]:
    existing = _project_id(slug)
    if existing is not None:
        return int(existing), False
    row = _cell("projects").create("projects", {
        "name": name,
        "slug": slug,
        "category": "product",
        "type": "portfolio",
        "status": "active",
        "priority": 1,
        "phase": "continuous-delivery",
        "domain": domain,
        "notes": "Workspace-backed project created to preserve explicit Goal → Project progress links.",
    })
    _PROJECT_IDS[slug] = int(row["id"])
    return int(row["id"]), True


def ensure_task_project_links(project_ids: dict[str, int]) -> int:
    created = 0
    store = _task_store()
    task_rows = [task for task in store.list(include_archived=True) if task["project"]]
    for row in task_rows:
        project_id = project_ids.get(row["project"])
        if project_id is None:
            continue
        created += int(store.link(
            row["id"], "project", project_id,
            {"relation_type": "delivery", "weight": 1, "source": SOURCE}, update=False,
        ) == "created")
    return created


def _goal_task_projects() -> list[dict]:
    """Distinct (goal, project, task) combinations from the task's goal and project links."""
    store = _task_store()
    projects_by_task: dict[int, list[int]] = {}
    for link in store.links(target_type="project"):
        projects_by_task.setdefault(link["task"], []).append(link["target_id"])
    combos = []
    for link in store.links(target_type="goal"):
        for project_id in projects_by_task.get(link["task"], []):
            combos.append({"goal_id": link["target_id"], "project_id": project_id, "task_id": link["task"]})
    return combos


def ensure_goal_project_links() -> int:
    goals = _cell("goals")
    known = {(row["goal_id"], row["project_id"]) for row in goals.list("goal-project-links")}
    created = 0
    for row in _goal_task_projects():
        pair = (row["goal_id"], row["project_id"])
        if pair in known:
            continue
        goals.create("goal-project-links", {
            "goal_id": row["goal_id"], "project_id": row["project_id"], "relation_type": "delivery",
            "weight": 1, "source": SOURCE,
        })
        known.add(pair)
        created += 1
    return created


def ensure_work_item(values: dict) -> tuple[int, bool]:
    source_key = values["source_key"]
    projects = _cell("projects")
    existing = next(iter(projects.list("work-items", source_key=source_key)), None)
    if existing:
        if existing["status"] != values["status"]:
            projects.update("work-items", existing["id"], {"status": values["status"]})
        return int(existing["id"]), False
    fields = {
        "item_type": values["item_type"], "title": values["title"], "description": values["description"],
        "goal_id": values["goal_id"], "project_id": values["project_id"],
        "parent_item_id": values["parent_item_id"], "status": values["status"],
        "progress_percent": values["progress_percent"], "weight": 1, "source_key": source_key, "source": SOURCE,
    }
    row = projects.create("work-items", {k: v for k, v in fields.items() if v is not None})
    return int(row["id"]), True


def ensure_work_hierarchy(project_ids: dict[str, int]) -> dict[str, int]:
    counts = {
        "task_project_links_created": ensure_task_project_links(project_ids),
        "goal_project_links_created": ensure_goal_project_links(),
        "projects_created": 0,
        "epics_created": 0,
        "stories_created": 0,
        "work_item_task_links_created": 0,
    }
    epic_ids: dict[tuple[int, int], int] = {}
    goals_by_id = {row["id"]: row for row in _cell("goals").list("goals")}
    projects_by_id = {row["id"]: row for row in _cell("projects").list("projects")}
    goal_projects = [
        {
            "goal_id": link["goal_id"], "project_id": link["project_id"],
            "goal_title": goals_by_id[link["goal_id"]]["title"],
            "project_name": projects_by_id[link["project_id"]]["name"],
        }
        for link in sorted(_cell("goals").list("goal-project-links"), key=lambda l: (l["goal_id"], l["project_id"]))
        if link["goal_id"] in goals_by_id and link["project_id"] in projects_by_id
    ]
    for row in goal_projects:
        epic_id, created = ensure_work_item({
                "item_type": "epic",
                "title": f"Epic: {row['goal_title']} — {row['project_name']}",
                "description": "Continuous delivery container for this goal and project.",
                "goal_id": row["goal_id"],
                "project_id": row["project_id"],
                "parent_item_id": None,
                "status": "todo",
                "progress_percent": 0,
                "source_key": f"{SOURCE}:epic:{row['goal_id']}:{row['project_id']}",
            },
        )
        epic_ids[(row["goal_id"], row["project_id"])] = epic_id
        counts["epics_created"] += int(created)

    epics_by_scope: dict[tuple[int, int], list[int]] = {}
    for epic in sorted(_cell("projects").list("work-items", item_type="epic"), key=lambda e: e["id"]):
        epics_by_scope.setdefault((epic["goal_id"], epic["project_id"]), []).append(epic["id"])
    tasks_by_number = {
        task["id"]: task
        for task in _task_store().list(include_archived=True)
    }
    task_rows = []
    for combo in _goal_task_projects():
        task = tasks_by_number.get(combo["task_id"])
        if task is None:
            continue
        for epic_id in epics_by_scope.get((combo["goal_id"], combo["project_id"]), []):
            task_rows.append({
                "goal_id": combo["goal_id"], "project_id": combo["project_id"], "task_id": task["id"],
                "title": task["title"], "description": task["description"], "status": task["status"],
                "progress_percent": task["progress_percent"], "epic_id": epic_id,
            })
    task_rows.sort(key=lambda r: (r["goal_id"], r["project_id"], r["task_id"], r["epic_id"]))
    projects = _cell("projects")
    task_links = {(row["work_item_id"], row["task_id"]) for row in projects.list("work-item-task-links")}
    for row in task_rows:
        story_id, created = ensure_work_item({
                "item_type": "story",
                "title": f"Story: {row['title']}",
                "description": row["description"] or "Story implementation tracked by the canonical task.",
                "goal_id": row["goal_id"],
                "project_id": row["project_id"],
                "parent_item_id": row["epic_id"],
                "status": row["status"],
                "progress_percent": _task_progress(row["status"], row["progress_percent"]),
                "source_key": f"{SOURCE}:story:{row['task_id']}:{row['goal_id']}:{row['project_id']}",
            },
        )
        counts["stories_created"] += int(created)
        link_pair = (story_id, row["task_id"])
        if link_pair not in task_links:
            projects.create("work-item-task-links", {
                "work_item_id": story_id, "task_id": row["task_id"], "relation_type": "implements", "weight": 1,
            })
            task_links.add(link_pair)
            counts["work_item_task_links_created"] += 1
    return counts


def seed(dry_run: bool = False) -> dict[str, int]:
    catalog = load_yaml(ROLE_CATALOG)
    learning = load_yaml(ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-LEARNING.yaml")
    roles = catalog.get("roles", [])
    learning_by_role = {
        row["role_id"]: row.get("skill_slugs", [])
        for row in learning.get("roles", [])
    }
    _DRY_RUN["on"] = dry_run
    _PROJECT_IDS.clear()
    roles_cell = _cell("roles-skills")
    skill_ids = {row["slug"]: row["id"] for row in roles_cell.list("skills")}
    counts = {
        "roles": len(roles),
        "projects_created": 0,
        "goals_created": 0,
        "goal_tasks_created": 0,
        "tasks_created": 0,
        "task_goal_links_created": 0,
        "allocations_created": 0,
        "task_project_links_created": 0,
        "goal_project_links_created": 0,
        "epics_created": 0,
        "stories_created": 0,
        "work_item_task_links_created": 0,
    }
    goals_by_id = {row["id"]: row for row in _cell("goals").list("goals")}
    role_links: dict[str, list[dict]] = {}
    for link in roles_cell.list("role-goal-links"):
        role_links.setdefault(link["role_id"], []).append(link)
    try:
        _, created = ensure_project(PROJECT_SLUG, "Solo Empire", "solo-empire")
        counts["projects_created"] += int(created)
        _, created = ensure_project(AI_PROJECT_SLUG, "AI/ML", "book-ai")
        counts["projects_created"] += int(created)
        project_ids = {
            slug: project_id
            for slug in (PROJECT_SLUG, AI_PROJECT_SLUG)
            if (project_id := _project_id(slug)) is not None
        }
        if PROJECT_SLUG not in project_ids:
            raise RuntimeError(f"required portfolio project is missing: {PROJECT_SLUG}")
        for role in roles:
            role_id = role["id"]
            candidates = []
            for link in role_links.get(role_id, []):
                goal = goals_by_id.get(link["goal_id"])
                if goal is not None and goal["status"] in ("planning", "active"):
                    candidates.append((0 if link["link_type"] == "primary" else 1, goal["priority"], goal["id"]))
            if candidates:
                goal_id = int(min(candidates)[2])
            else:
                goal_id, created = ensure_goal(role)
                counts["goals_created"] += int(created)

            _, created = ensure_goal_task(goal_id, role)
            counts["goal_tasks_created"] += int(created)
            _, created = ensure_role_task(role, goal_id, learning_by_role.get(role_id, []), skill_ids)
            counts["tasks_created"] += int(created)
            counts["task_goal_links_created"] += ensure_active_task_goal_links(role_id, goal_id)
            counts["allocations_created"] += int(ensure_project_allocation(role_id, goal_id))

        hierarchy_counts = ensure_work_hierarchy(project_ids)
        for key, value in hierarchy_counts.items():
            counts[key] += value

    finally:
        _DRY_RUN["on"] = False
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        counts = seed(dry_run=args.dry_run)
    except (CellStoreError, ClientConfigError) as exc:
        print(f"Role portfolio baseline unavailable: {exc}")
        return 1
    mode = " (dry-run)" if args.dry_run else ""
    print(f"Role portfolio baseline{mode}: {counts['roles']} roles")
    print(f"  projects created: {counts['projects_created']}")
    print(f"  goals created: {counts['goals_created']}")
    print(f"  goal tasks created: {counts['goal_tasks_created']}")
    print(f"  tasks created: {counts['tasks_created']}")
    print(f"  task-goal links created: {counts['task_goal_links_created']}")
    print(f"  project allocations created: {counts['allocations_created']}")
    print(f"  task-project links created: {counts['task_project_links_created']}")
    print(f"  goal-project links created: {counts['goal_project_links_created']}")
    print(f"  epics created: {counts['epics_created']}")
    print(f"  stories created: {counts['stories_created']}")
    print(f"  work-item task links created: {counts['work_item_task_links_created']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

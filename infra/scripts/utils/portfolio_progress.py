#!/usr/bin/env python3
"""Roll progress continuously from subtasks to goals.

Hierarchy:
    Goal → Epic/Story → Project → Task → Subtask

Tasks remain the execution source of truth. A task with children derives its
progress from those children; a leaf task derives progress from its explicit
percentage or status. Projects, work items, and goals then use weighted
averages of their linked children. No sprint or review cycle is required.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime

from _env import PROJECT_ROOT, REPORTS_DIR
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError, open_task_store

AUTOMATION_ID = "progress-rollup"  # writes progress_percent back, so not a pure reader


REPORT_PATH = REPORTS_DIR / "PORTFOLIO-PROGRESS.md"


def weighted_average(values: list[tuple[int, float]]) -> int | None:
    if not values:
        return None
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    return max(0, min(100, round(sum(progress * weight for progress, weight in values) / total_weight)))


def status_progress(status: str, explicit: int | None) -> int:
    if status == "done":
        return 100
    if explicit is not None and int(explicit or 0) > 0:
        return max(0, min(100, int(explicit)))
    return {"in_progress": 50, "blocked": 0, "cancelled": 0}.get(status, 0)


def update_value(cell, plural: str, row: dict, value: int, dry_run: bool = False) -> bool:
    """Write ``value`` as the row's progress when it differs; a dry run only reports the difference."""
    if int(row["progress_percent"] or 0) == value:
        return False
    if not dry_run:
        cell.update(plural, row["id"], {"progress_percent": value})
    return True


def rollup(dry_run: bool = False) -> dict[str, int]:
    now = datetime.now().isoformat(timespec="seconds")
    counts = {
        "tasks_updated": 0,
        "goal_tasks_updated": 0,
        "work_items_updated": 0,
        "projects_updated": 0,
        "goals_updated": 0,
    }

    store = open_task_store(None, AUTOMATION_ID)
    task_rows = {
        row["id"]: row
        for row in store.list(include_archived=True)
    }
    task_children: dict[int, list[int]] = defaultdict(list)
    for row in task_rows.values():
        if row["parent_task_id"] in task_rows:
            task_children[row["parent_task_id"]].append(row["id"])
    task_progress: dict[int, int] = {}
    visiting: set[int] = set()

    def compute_task(task_id: int) -> int:
        if task_id in task_progress:
            return task_progress[task_id]
        if task_id in visiting:
            raise ValueError(f"task parent cycle detected at task {task_id}")
        visiting.add(task_id)
        children = task_children.get(task_id, [])
        if children:
            value = weighted_average([(compute_task(child), 1) for child in children]) or 0
        else:
            row = task_rows[task_id]
            value = status_progress(row["status"], row["progress_percent"])
        visiting.remove(task_id)
        task_progress[task_id] = value
        return value

    for task_id in task_rows:
        compute_task(task_id)
    for task_id, value in task_progress.items():
        if int(task_rows[task_id]["progress_percent"] or 0) == value:
            continue
        counts["tasks_updated"] += 1
        if not dry_run:
            store.update(task_id, {"progress_percent": value}, legacy={"updated_at": now})

    goals_cell = open_cell_store("goals", AUTOMATION_ID)
    projects_cell = open_cell_store("projects", AUTOMATION_ID)

    goal_task_rows = goals_cell.list("goal-tasks")
    goal_task_progress: dict[int, int] = {}
    for row in goal_task_rows:
        value = status_progress(row["status"], row["progress_percent"])
        goal_task_progress[row["id"]] = value
        counts["goal_tasks_updated"] += int(update_value(goals_cell, "goal-tasks", row, value, dry_run))

    work_rows = {row["id"]: row for row in projects_cell.list("work-items")}
    work_children: dict[int, list[int]] = defaultdict(list)
    for row in work_rows.values():
        if row["parent_item_id"] in work_rows:
            work_children[row["parent_item_id"]].append(row["id"])
    work_task_link_rows = projects_cell.list("work-item-task-links")
    work_goal_task_link_rows = projects_cell.list("work-item-goal-task-links")
    work_tasks: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for row in work_task_link_rows:
        work_tasks[row["work_item_id"]].append((row["task_id"], row["weight"] or 1))
    work_goal_tasks: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for row in work_goal_task_link_rows:
        work_goal_tasks[row["work_item_id"]].append(
            (row["goal_task_id"], row["weight"] or 1)
        )
    work_progress: dict[int, int] = {}
    work_visiting: set[int] = set()

    def compute_work(item_id: int) -> int:
        if item_id in work_progress:
            return work_progress[item_id]
        if item_id in work_visiting:
            raise ValueError(f"work-item parent cycle detected at item {item_id}")
        work_visiting.add(item_id)
        children = work_children.get(item_id, [])
        if children:
            value = weighted_average(
                [(compute_work(child), work_rows[child]["weight"] or 1) for child in children]
            ) or 0
        elif work_tasks.get(item_id) or work_goal_tasks.get(item_id):
            entries = [
                (task_progress[task_id], weight)
                for task_id, weight in work_tasks[item_id]
            ]
            entries.extend(
                (goal_task_progress[goal_task_id], weight)
                for goal_task_id, weight in work_goal_tasks[item_id]
            )
            value = weighted_average(entries) or 0
        else:
            value = status_progress(work_rows[item_id]["status"], work_rows[item_id]["progress_percent"])
        work_visiting.remove(item_id)
        work_progress[item_id] = value
        return value

    for item_id in work_rows:
        compute_work(item_id)
    for item_id, value in work_progress.items():
        counts["work_items_updated"] += int(
            update_value(projects_cell, "work-items", work_rows[item_id], value, dry_run)
        )

    project_rows = {row["id"]: row for row in projects_cell.list("projects")}
    story_ids = {item_id for item_id, row in work_rows.items() if row["item_type"] == "story"}
    project_stories: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for item_id in sorted(story_ids):
        row = work_rows[item_id]
        project_stories[row["project_id"]].append((work_progress[item_id], row["weight"] or 1))
    project_tasks: dict[int, set[int]] = defaultdict(set)
    for link in store.links(target_type="project"):
        project_tasks[link["target_id"]].add(link["task"])
    story_task_ids: dict[int, set[int]] = defaultdict(set)
    for row in work_task_link_rows:
        if row["work_item_id"] in story_ids:
            story_task_ids[work_rows[row["work_item_id"]]["project_id"]].add(row["task_id"])
    for project_id, task_ids in story_task_ids.items():
        project_tasks[project_id].difference_update(task_ids)
    project_goal_tasks: dict[int, set[int]] = defaultdict(set)
    for row in goals_cell.list("goal-task-project-links"):
        project_goal_tasks[row["project_id"]].add(row["goal_task_id"])
    story_goal_task_ids: dict[int, set[int]] = defaultdict(set)
    for row in work_goal_task_link_rows:
        if row["work_item_id"] in story_ids:
            story_goal_task_ids[work_rows[row["work_item_id"]]["project_id"]].add(row["goal_task_id"])
    for project_id, goal_task_ids in story_goal_task_ids.items():
        project_goal_tasks[project_id].difference_update(goal_task_ids)

    project_progress: dict[int, int] = {}
    project_task_counts: dict[int, tuple[int, int]] = {}
    for project_id in project_rows:
        entries = list(project_stories.get(project_id, []))
        direct_ids = project_tasks.get(project_id, set())
        entries.extend((task_progress[task_id], 1) for task_id in direct_ids)
        direct_goal_task_ids = project_goal_tasks.get(project_id, set())
        entries.extend(
            (goal_task_progress[goal_task_id], 1)
            for goal_task_id in direct_goal_task_ids
        )
        if not entries:
            continue
        value = weighted_average(entries) or 0
        project_progress[project_id] = value
        all_execution_ids: set[str] = {f"task:{task_id}" for task_id in direct_ids}
        all_execution_ids.update(
            f"goal-task:{goal_task_id}" for goal_task_id in direct_goal_task_ids
        )
        all_execution_ids.update(f"task:{task_id}" for task_id in story_task_ids.get(project_id, ()))
        all_execution_ids.update(
            f"goal-task:{goal_task_id}" for goal_task_id in story_goal_task_ids.get(project_id, ())
        )
        project_task_counts[project_id] = (
            len(all_execution_ids),
            sum(
                (
                    task_progress[int(item.split(":", 1)[1])]
                    if item.startswith("task:")
                    else goal_task_progress[int(item.split(":", 1)[1])]
                ) == 100
                for item in all_execution_ids
            ),
        )

    for project_id, value in project_progress.items():
        current = project_rows[project_id]["progress_percent"] or 0
        task_count, completed_count = project_task_counts[project_id]
        if current != value or task_count != project_rows[project_id].get("task_count", 0):
            if not dry_run:
                projects_cell.update("projects", project_id, {
                    "progress_percent": value, "task_count": task_count, "completed_count": completed_count,
                })
            counts["projects_updated"] += 1

    goal_projects: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for row in goals_cell.list("goal-project-links"):
        if row["project_id"] in project_progress:
            goal_projects[row["goal_id"]].append(
                (project_progress[row["project_id"]], row["weight"] or 1)
            )
    goal_epics: dict[int, list[tuple[int, float]]] = defaultdict(list)
    goal_stories: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for item_id, row in work_rows.items():
        if row["goal_id"] is None:
            continue
        target = goal_epics if row["item_type"] == "epic" else goal_stories
        target[row["goal_id"]].append((work_progress[item_id], row["weight"] or 1))
    goal_task_children: dict[int, list[dict]] = defaultdict(list)
    for row in goal_task_rows:
        goal_task_children[row["goal_id"]].append(row)
    goal_rows = goals_cell.list("goals")
    goal_values: dict[int, int] = {}
    for goal in goal_rows:
        goal_id = goal["id"]
        if goal_epics.get(goal_id):
            goal_values[goal_id] = weighted_average(goal_epics[goal_id]) or 0
            continue
        if goal_stories.get(goal_id):
            goal_values[goal_id] = weighted_average(goal_stories[goal_id]) or 0
            continue
        if goal_projects.get(goal_id):
            goal_values[goal_id] = weighted_average(goal_projects[goal_id]) or 0
            continue
        fallback = [
            (status_progress(child["status"], goal_task_progress[child["id"]]), 1)
            for child in goal_task_children.get(goal_id, [])
        ]
        value = weighted_average(fallback)
        if value is not None:
            goal_values[goal_id] = value
    for goal in goal_rows:
        if goal["id"] in goal_values:
            counts["goals_updated"] += int(update_value(goals_cell, "goals", goal, goal_values[goal["id"]], dry_run))

    if not dry_run:
        write_report(store)
    return counts


def write_report(store=None) -> None:
    """Write the current continuous hierarchy as a generated dashboard."""
    store = store or open_task_store(None, AUTOMATION_ID)
    goals_cell = open_cell_store("goals", AUTOMATION_ID)
    projects_cell = open_cell_store("projects", AUTOMATION_ID)
    projects = {row["id"]: row for row in projects_cell.list("projects")}
    goal_links_by_goal: dict[int, list[dict]] = defaultdict(list)
    for link in goals_cell.list("goal-project-links"):
        if link["project_id"] in projects:  # the old inner join dropped dangling project ids
            goal_links_by_goal[link["goal_id"]].append(link)
    goals = []
    for goal in goals_cell.list("goals", status=["planning", "active", "paused"]):
        linked = goal_links_by_goal.get(goal["id"], [])
        if not linked:
            continue
        project_ids = {link["project_id"] for link in linked}
        goals.append({
            **goal,
            "project_count": len(project_ids),
            "active_project_count": sum(1 for pid in project_ids if projects[pid]["status"] in ("active", "planning")),
        })
    goals.sort(key=lambda g: (g["priority"] is not None, g["priority"] or 0, g["id"]))
    project_names = defaultdict(list)
    for goal_id in sorted(goal_links_by_goal):
        for slug, progress in sorted((projects[l["project_id"]]["slug"], projects[l["project_id"]]["progress_percent"])
                                     for l in goal_links_by_goal[goal_id]):
            project_names[goal_id].append(f"{slug} ({progress}%)")
    item_counts: dict[int, dict[str, int]] = {}
    for row in projects_cell.list("work-items"):
        counts_for = item_counts.setdefault(row["goal_id"], {"epic": 0, "story": 0})
        counts_for["epic"] += int(row["item_type"] == "epic")
        counts_for["story"] += int(row["item_type"] == "story")
    task_counts: dict[int, dict[str, int]] = defaultdict(lambda: {"tasks": 0, "done": 0})
    all_tasks = {task["id"]: task for task in store.list(include_archived=True)}
    children_of: dict[int, set[int]] = defaultdict(set)
    for task in all_tasks.values():
        if task["parent_task_id"] is not None:
            children_of[task["parent_task_id"]].add(task["id"])
    goal_links = store.links(target_type="goal")
    goal_roots: dict[int, set[int]] = defaultdict(set)
    for link in goal_links:
        if link["task"] in all_tasks:
            goal_roots[link["target_id"]].add(link["task"])
    for goal_id, roots in goal_roots.items():
        task_counts[goal_id]["tasks"] += len(roots)
        task_counts[goal_id]["done"] += sum(1 for task_id in roots if all_tasks[task_id]["progress_percent"] == 100)
    for row in goals_cell.list("goal-tasks"):
        task_counts[row["goal_id"]]["tasks"] += 1
        task_counts[row["goal_id"]]["done"] += int(row["status"] == "done")
    lines = [
        "---",
        "title: Continuous Portfolio Progress",
        "tags: [dashboard, goals, epics, stories, projects, tasks, subtasks, progress]",
        f"updated: {datetime.now().date().isoformat()}",
        "status: generated",
        "source: goals, goal_project_links, portfolio_work_items, task_project_links, task_goal_links, goal_task_project_links, work_item_goal_task_links, tasks",
        "---",
        "",
        "# Continuous Portfolio Progress",
        "",
        "> หลาย Goal เดินพร้อมกันได้ตลอดเวลา ไม่มีรอบบังคับ เปอร์เซ็นต์คำนวณจากงานลูกจริงแล้ว roll-up ขึ้นตามลำดับ:",
        "> `Goal → Epic/Story → Project → Task → Subtask`",
        "> การแยก Personal Role กับ Professional Role และบริบท Personal/Hobby/Freelance/Startup/Client อยู่ที่ [PORTFOLIO-CONTEXTS.md](PORTFOLIO-CONTEXTS.md)",
        "",
        "| Goal | Progress | Projects | Epics | Stories | Tasks done | Subtasks |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for goal in goals:
        items = item_counts.get(goal["id"], {"epic": 0, "story": 0})
        tasks = task_counts.get(goal["id"], {"tasks": 0, "done": 0})
        subtask_count = len(set().union(*(children_of.get(task_id, set()) for task_id in goal_roots.get(goal["id"], ()))))
        lines.append(
            f"| #{goal['id']} {goal['title']} ({goal['status']}) | {goal['progress_percent']}% | "
            f"{'<br>'.join(project_names.get(goal['id'], ['—']))} | {items['epic']} | "
            f"{items['story']} | {tasks['done']}/{tasks['tasks']} | {subtask_count} |"
        )
    goal_linked = {link["task"] for link in goal_links}
    operational_plans = []
    for parent in all_tasks.values():
        if parent["archived_at"] is not None or parent["status"] == "cancelled" or parent["id"] in goal_linked:
            continue
        kids = [all_tasks[child_id] for child_id in children_of.get(parent["id"], ())]
        if not kids:
            continue
        operational_plans.append({
            "id": parent["id"], "title": parent["title"], "project": parent["project"],
            "priority": parent["priority"], "progress_percent": parent["progress_percent"],
            "subtasks": len(kids), "done": sum(1 for kid in kids if kid["status"] == "done"),
            "blocked": sum(1 for kid in kids if kid["status"] == "blocked"),
        })
    operational_plans.sort(key=lambda plan: (
        plan["project"] is not None, plan["project"] or "",
        plan["priority"] is not None, plan["priority"] or 0, plan["id"]))
    if operational_plans:
        lines.extend([
            "", "## Operational task plans", "",
            "> Task/subtask plans without a goal link; no revenue goal or customer deadline is inferred.",
            "",
            "| Plan | Project | Progress | Subtasks done | Waiting on approval/evidence |",
            "|---|---|---:|---:|---:|",
        ])
        for plan in operational_plans:
            lines.append(
                f"| #{plan['id']} {plan['title']} | {plan['project'] or '—'} | "
                f"{plan['progress_percent']}% | {plan['done']}/{plan['subtasks']} | {plan['blocked']} |"
            )
    lines.extend(
        [
            "",
            "## Roll-up rules",
            "",
            "- A leaf task is 100% when `done`, 50% by default when `in_progress`, 0% when `todo`/`blocked`; set `tasks.progress_percent` for a more precise value.",
            "- A task with subtasks uses the weighted average of its subtasks.",
            "- A Story uses linked task progress; an Epic uses child Story progress.",
            "- A Project uses Story progress plus direct project tasks; a Goal uses its own linked Epics first, then linked project progress.",
            "- Legacy `goal_tasks` are bridged into Stories through `work_item_goal_task_links` without duplicating task records.",
        ]
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="commit calculated percentages")
    parser.add_argument("--dry-run", action="store_true", help="calculate without committing")
    args = parser.parse_args()
    dry_run = args.dry_run or not args.update
    try:
        counts = rollup(dry_run=dry_run)
    except (CellStoreError, ClientConfigError) as exc:
        print(f"Portfolio progress roll-up unavailable: {exc}")
        return 1
    mode = "dry-run" if dry_run else "updated"
    print(f"Portfolio progress roll-up ({mode})")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

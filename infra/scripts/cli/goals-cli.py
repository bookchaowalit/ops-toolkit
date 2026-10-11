#!/usr/bin/env python3
"""
Solo Empire Goals CLI — Manage goals and their tasks
Usage:
    python3 infra/scripts/cli/goals-cli.py                          # Show all active goals
    python3 infra/scripts/cli/goals-cli.py view <goal_id>           # View goal detail + tasks
    python3 infra/scripts/cli/goals-cli.py progress <goal_id> [0-100]  # Update progress
    python3 infra/scripts/cli/goals-cli.py task <goal_id> "Task"    # Add task to goal
    python3 infra/scripts/cli/goals-cli.py done <task_id>           # Mark task done
    python3 infra/scripts/cli/goals-cli.py add "Title" -t type -c category -p priority
    python3 infra/scripts/cli/goals-cli.py complete <goal_id>       # Mark goal complete
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from datetime import date, datetime

from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError

AUTOMATION_ID = "owner-cli"


TYPE_ICONS = {"yearly": "🎯", "quarterly": "📅", "monthly": "🗓️", "weekly": "⚡", "project": "🚀"}
STATUS_ICONS = {"active": "🟢", "paused": "⏸️", "completed": "✅", "abandoned": "❌"}
PRIORITY_ICONS = {
    1: "🔴", 2: "🟠", 3: "🟡", 4: "⚪",
    "high": "🔴", "medium": "🟠", "low": "🟡",
}

def get_conn():
    """The goals cell store (goals and goal tasks live in the goals cell, not local SQLite)."""
    return open_cell_store("goals", AUTOMATION_ID, channel="cli")


def _now():
    return datetime.now().astimezone().isoformat()


def show_goals(store):
    """Show all active goals with progress bars."""
    goals = store.list("goals", status=["active", "paused"])
    # SQLite sorted NULL first; a missing priority must not break the sort.
    goals.sort(key=lambda g: (g["priority"] is not None, g["priority"] or 0, g["due_date"] or ""))
    totals, dones = {}, {}
    for t in store.list("goal-tasks"):
        totals[t["goal_id"]] = totals.get(t["goal_id"], 0) + 1
        if t["status"] == "done":
            dones[t["goal_id"]] = dones.get(t["goal_id"], 0) + 1
    for g in goals:
        g["total_tasks"] = totals.get(g["id"], 0)
        g["done_tasks"] = dones.get(g["id"], 0)

    if not goals:
        print("🎯 No active goals. Add one with: goals-cli.py add \"Goal\" -t yearly")
        return

    print("🎯 SOLO EMPIRE GOALS")
    print("=" * 60)

    current_type = None
    for g in goals:
        if g["type"] != current_type:
            current_type = g["type"]
            icon = TYPE_ICONS.get(current_type, "🎯")
            print(f"\n  {icon} {(current_type or '').upper()}")
            print("  " + "-" * 40)

        p_icon = PRIORITY_ICONS.get(g["priority"], "⚪")
        s_icon = STATUS_ICONS.get(g["status"], "🟢")

        # Progress bar
        pct = g["progress_percent"] or 0
        bar_len = 20
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        # Task count
        task_str = f"{g['done_tasks']}/{g['total_tasks']} tasks" if g['total_tasks'] > 0 else "no tasks"

        # Due date warning
        due_str = ""
        if g["due_date"]:
            due = date.fromisoformat(g["due_date"][:10])
            days_left = (due - date.today()).days
            if days_left < 0:
                due_str = f" ⚠️ OVERDUE"
            elif days_left <= 7:
                due_str = f" ⚠️ {days_left}d left"
            else:
                due_str = f" ({days_left}d)"

        print(f"  {s_icon} [{g['id']}] {p_icon} {g['title']}")
        print(f"      {bar} {pct}% | {task_str}{due_str}")

    # Summary
    total = len(goals)
    avg_pct = sum(g["progress_percent"] or 0 for g in goals) / total if total else 0
    print(f"\n  📊 {total} active goals | avg progress: {avg_pct:.0f}%")
    print()

def view_goal(store, goal_id):
    """View goal detail with tasks."""
    g = store.get("goals", goal_id)
    if not g:
        print(f"❌ Goal #{goal_id} not found")
        return

    tasks = sorted(store.list("goal-tasks", goal_id=goal_id),
                   key=lambda t: (t["sort_order"] or 0, t["priority"] or 0, t["id"]))

    type_icon = TYPE_ICONS.get(g["type"], "🎯")
    s_icon = STATUS_ICONS.get(g["status"], "🟢")
    p_icon = PRIORITY_ICONS.get(g["priority"], "⚪")

    print(f"\n{type_icon} {s_icon} [{g['id']}] {p_icon} {g['title']}")
    print("=" * 50)
    if g["description"]:
        print(f"  {g['description']}")
    print(f"  Type: {g['type']} | Category: {g['category'] or '-'} | Priority: P{g['priority']}")
    print(f"  Progress: {g['progress_percent'] or 0}%")
    if g["start_date"]:
        print(f"  Start: {g['start_date']}")
    if g["due_date"]:
        print(f"  Due: {g['due_date']}")
    if g["key_results"]:
        print(f"  Key Results: {g['key_results']}")
    if g["notes"]:
        print(f"  Notes: {g['notes'][:100]}")

    if tasks:
        print(f"\n  📋 TASKS ({sum(1 for t in tasks if t['status']=='done')}/{len(tasks)} done)")
        print("  " + "-" * 40)
        for t in tasks:
            done = "✅" if t["status"] == "done" else "⬜"
            p = PRIORITY_ICONS.get(t["priority"], "⚪")
            due = f" (due {t['due_date']})" if t["due_date"] else ""
            print(f"    {done} [{t['id']}] {p} {t['title']}{due}")
    else:
        print(f"\n  📋 No tasks yet. Add: goals-cli.py task {goal_id} \"Task name\"")
    print()

def update_progress(store, goal_id, pct):
    """Update goal progress percentage."""
    pct = max(0, min(100, int(pct)))
    g = store.get("goals", goal_id)
    if not g:
        print(f"❌ Goal #{goal_id} not found")
        return
    store.update("goals", goal_id, {"progress_percent": pct})
    print(f"✅ [{goal_id}] {g['title']} → {pct}%")

def add_task(store, goal_id, title, priority=3, due_date=None):
    """Add a task to a goal."""
    g = store.get("goals", goal_id)
    if not g:
        print(f"❌ Goal #{goal_id} not found")
        return

    max_order = max((t["sort_order"] or 0 for t in store.list("goal-tasks", goal_id=goal_id)), default=0)

    fields = {"goal_id": goal_id, "title": title, "status": "todo", "priority": priority,
              "sort_order": max_order + 1}
    if due_date:
        fields["due_date"] = due_date
    store.create("goal-tasks", fields)
    print(f"✅ Task added to [{goal_id}] {g['title']}: \"{title}\"")

def mark_task_done(store, task_id):
    """Mark a goal task as done."""
    t = store.get("goal-tasks", task_id)
    if not t:
        print(f"❌ Task #{task_id} not found")
        return

    store.update("goal-tasks", task_id, {"status": "done", "completed_at": _now()})

    # Auto-update goal progress
    goal_id = t["goal_id"]
    siblings = store.list("goal-tasks", goal_id=goal_id)
    total = len(siblings)
    done = sum(1 for s in siblings if s["status"] == "done")
    pct = int(done / total * 100) if total else 0
    if total > 0:
        store.update("goals", goal_id, {"progress_percent": pct})

    print(f"✅ Task #{task_id} done! Goal progress → {pct}%")

def add_goal(store, title, type_="yearly", category=None, priority=3, due_date=None):
    """Add a new goal."""
    fields = {"title": title, "type": type_, "status": "active", "priority": priority,
              "start_date": date.today().isoformat(), "progress_percent": 0}
    if category:
        fields["category"] = category
    if due_date:
        fields["due_date"] = due_date
    store.create("goals", fields)
    print(f"✅ Goal added: \"{title}\" ({type_})")

def complete_goal(store, goal_id):
    """Mark goal as complete."""
    g = store.get("goals", goal_id)
    if not g:
        print(f"❌ Goal #{goal_id} not found")
        return
    store.update("goals", goal_id, {"status": "completed", "progress_percent": 100, "completed_at": _now()})
    print(f"🎉 Goal #{goal_id} \"{g['title']}\" completed!")

def main():
    conn = get_conn()  # the goals cell store
    args = sys.argv[1:]

    if not args:
        show_goals(conn)
    elif args[0] == "view" and len(args) >= 2:
        view_goal(conn, int(args[1]))
    elif args[0] == "progress" and len(args) >= 3:
        update_progress(conn, int(args[1]), int(args[2]))
    elif args[0] == "task" and len(args) >= 3:
        priority = 3
        due = None
        if "-p" in args:
            priority = int(args[args.index("-p") + 1])
        if "-d" in args:
            due = args[args.index("-d") + 1]
        add_task(conn, int(args[1]), args[2], priority, due)
    elif args[0] == "done" and len(args) >= 2:
        mark_task_done(conn, int(args[1]))
    elif args[0] == "add" and len(args) >= 2:
        type_ = "yearly"
        cat = None
        pri = 3
        due = None
        if "-t" in args:
            type_ = args[args.index("-t") + 1]
        if "-c" in args:
            cat = args[args.index("-c") + 1]
        if "-p" in args:
            pri = int(args[args.index("-p") + 1])
        if "-d" in args:
            due = args[args.index("-d") + 1]
        add_goal(conn, args[1], type_, cat, pri, due)
    elif args[0] == "complete" and len(args) >= 2:
        complete_goal(conn, int(args[1]))
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except (CellStoreError, ClientConfigError) as exc:
        print(f"❌ {exc}")
        sys.exit(1)

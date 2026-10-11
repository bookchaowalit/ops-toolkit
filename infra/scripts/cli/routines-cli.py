#!/usr/bin/env python3
"""
Routines CLI — Daily/weekly/monthly routine tracker
Usage:
    python3 infra/scripts/routines-cli.py                   # Show routines overview
    python3 infra/scripts/routines-cli.py view <id>         # View routine detail + steps
    python3 infra/scripts/routines-cli.py start <id>        # Log routine start today
    python3 infra/scripts/routines-cli.py done <id>         # Mark routine complete today
    python3 infra/scripts/routines-cli.py history [id]      # Show routine log history
    python3 infra/scripts/routines-cli.py streaks           # Show routine streaks
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from datetime import date, timedelta

from habits_store import HabitsStoreError, open_habits_store
from task_store import ClientConfigError


def get_conn():
    """The habits store (routines live in the habits cell, not local SQLite)."""
    return open_habits_store("owner-cli", channel="cli")


def _recent(logs, limit):
    """Newest first by log_date (ties: newest id first), like ORDER BY log_date DESC."""
    return sorted(logs, key=lambda l: (l["log_date"], l.get("id") or 0), reverse=True)[:limit]

def show_overview(conn):
    """Show all routines with today's status."""
    today = date.today().isoformat()
    routines = sorted(conn.routines(status="active"), key=lambda r: r["id"])
    done_ids = {l["routine_id"] for l in conn.routine_logs(date_from=today, date_to=today)}
    names = {r["id"]: r["name"] for r in conn.routines()}

    type_icons = {
        "daily_morning": "🌅", "daily_evening": "🌙",
        "weekly_review": "📅", "monthly_review": "📆"
    }

    print(f"\n🔄 ROUTINES ({len(routines)} active)")
    print(f"{'═' * 60}")

    for r in routines:
        icon = type_icons.get(r["routine_type"], "🔄")
        done = "✅" if r["id"] in done_ids else "⬜"
        steps = r["steps"] or []
        duration = r["total_duration_minutes"] or 0
        print(f"  [{r['id']}] {done} {icon} {r['name']}")
        print(f"       {len(steps)} steps | ~{duration}min | {r['routine_type']}")
        if r["description"]:
            print(f"       {r['description'][:60]}")

    # Show recent logs
    recent = _recent([l for l in conn.routine_logs(order="desc") if l["routine_id"] in names], 5)

    if recent:
        print(f"\n  📋 Recent Logs:")
        for log in recent:
            dur = f"{log['duration_minutes']}min" if log['duration_minutes'] else "-"
            mood = f"mood: {log['mood_before']}→{log['mood_after']}" if log['mood_before'] else ""
            print(f"    {log['log_date']} | {names[log['routine_id']]} | {dur} {mood}")

def view_routine(conn, routine_id):
    """View routine detail with steps."""
    r = conn.routine(routine_id)
    if not r:
        print(f"❌ Routine #{routine_id} not found.")
        return

    steps = r["steps"] or []

    print(f"\n🔄 ROUTINE #{r['id']}: {r['name']}")
    print(f"{'═' * 60}")
    print(f"  Type:     {r['routine_type']}")
    print(f"  Status:   {r['status']}")
    print(f"  Duration: ~{r['total_duration_minutes'] or 0}min")
    if r["description"]:
        print(f"  Desc:     {r['description']}")

    if steps:
        print(f"\n  Steps ({len(steps)}):")
        for s in steps:
            dur = f"({s.get('duration_minutes', 0)}min)" if s.get("duration_minutes") else ""
            stype = f"[{s.get('type', '')}]" if s.get("type") else ""
            print(f"    {s.get('order', '?')}. {s.get('title', '')} {dur} {stype}")
            if s.get("description"):
                print(f"       {s['description']}")

    # History
    logs = _recent(conn.routine_logs(routine_id=routine_id, order="desc"), 10)

    if logs:
        print(f"\n  History (last 10):")
        for log in logs:
            dur = f"{log['duration_minutes']}min" if log['duration_minutes'] else "-"
            print(f"    {log['log_date']} | {dur}")

def start_routine(conn, routine_id):
    """Log routine start (or completion) today."""
    today = date.today().isoformat()

    if conn.routine_logs(routine_id=routine_id, date_from=today, date_to=today):
        print(f"ℹ️  Routine #{routine_id} already logged for today.")
        return

    r = conn.routine(routine_id)
    if not r:
        print(f"❌ Routine #{routine_id} not found.")
        return

    steps = r["steps"] or []
    fields = {"completed_steps": [s.get("order") for s in steps]}
    if r["total_duration_minutes"] is not None:
        fields["duration_minutes"] = r["total_duration_minutes"]
    conn.log_routine(routine_id, today, fields)
    print(f"✅ Routine #{routine_id} ({r['name']}) completed for today!")

def done_routine(conn, routine_id):
    """Same as start — marks routine done for today."""
    start_routine(conn, routine_id)

def show_history(conn, routine_id=None):
    """Show routine log history."""
    names = {r["id"]: r["name"] for r in conn.routines()}
    logs = _recent([l for l in conn.routine_logs(routine_id=routine_id, order="desc")
                    if l["routine_id"] in names], 20)

    if not logs:
        print("  No routine logs yet. Start tracking!")
        return

    print(f"\n📋 ROUTINE HISTORY")
    print(f"{'─' * 60}")
    for log in logs:
        dur = f"{log['duration_minutes']}min" if log['duration_minutes'] else "-"
        mood = f"mood {log['mood_before']}→{log['mood_after']}" if log['mood_before'] else ""
        notes = f" — {log['notes'][:40]}" if log['notes'] else ""
        print(f"  {log['log_date']} | {names[log['routine_id']]} | {dur} {mood}{notes}")

def show_streaks(conn):
    """Show routine streaks."""
    routines = conn.routines(status="active")
    by_routine = {}
    for log in conn.routine_logs(order="desc"):
        by_routine.setdefault(log["routine_id"], []).append(log["log_date"])

    print(f"\n🔥 ROUTINE STREAKS")
    print(f"{'─' * 60}")

    for r in routines:
        dates = sorted((date.fromisoformat(d) for d in by_routine.get(r["id"], [])), reverse=True)
        streak = 0
        check_date = date.today()

        for d in dates:
            if d == check_date or d == check_date - timedelta(days=1):
                streak += 1
                check_date = d
            else:
                break

        total_logs = len(dates)
        bar = "█" * min(streak, 20) + "░" * max(0, 20 - min(streak, 20))
        print(f"  [{r['id']}] {r['name']}")
        print(f"      🔥 {streak} day streak | {total_logs} total logs | {bar}")

def main():
    try:
        _run()
    except (HabitsStoreError, ClientConfigError) as exc:
        print(f"❌ {exc}")
        sys.exit(1)


def _run():
    conn = get_conn()
    args = sys.argv[1:]

    if not args:
        show_overview(conn)
    elif args[0] == "view" and len(args) > 1:
        view_routine(conn, int(args[1]))
    elif args[0] == "start" and len(args) > 1:
        start_routine(conn, int(args[1]))
    elif args[0] == "done" and len(args) > 1:
        done_routine(conn, int(args[1]))
    elif args[0] == "history":
        rid = int(args[1]) if len(args) > 1 else None
        show_history(conn, rid)
    elif args[0] == "streaks":
        show_streaks(conn)
    else:
        print(__doc__)

if __name__ == "__main__":
    main()

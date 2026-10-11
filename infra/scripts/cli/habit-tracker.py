#!/usr/bin/env python3
"""
Daily Habit Tracker — Solo Empire
Usage:
  python3 habit-tracker.py              # Show today's checklist
  python3 habit-tracker.py done 1 3     # Mark habits 1,3 as done today
  python3 habit-tracker.py done 2 -d 60 # Mark habit 2 done, 60 min
  python3 habit-tracker.py add "Meditate" -c mindfulness -f daily -t 10
  python3 habit-tracker.py list         # List all active habits
  python3 habit-tracker.py streaks      # Show streak summary
  python3 habit-tracker.py week         # Show this week's completion
"""

import re
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from datetime import date, timedelta

from habits_store import HabitsStoreError, open_habits_store
from task_store import ClientConfigError


DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def get_conn():
    """The habits store (habits live in the habits cell, not local SQLite)."""
    return open_habits_store("owner-cli", channel="cli")


def _by_priority(habits):
    return sorted(habits, key=lambda h: (h.get("priority") if h.get("priority") is not None else -1, h["id"]))


def today_str():
    return date.today().isoformat()


def today_day_abbr():
    return DAY_NAMES[date.today().weekday()].lower()


def get_scheduled_habits(conn):
    """Get habits scheduled for today."""
    day = today_day_abbr()
    habits = _by_priority(conn.habits(status="active"))
    scheduled = []
    for h in habits:
        if h["schedule_days"]:
            if day not in h["schedule_days"]:
                continue
        scheduled.append(h)
    return scheduled


def get_today_logs(conn):
    """Get today's habit logs."""
    return conn.habit_logs(date_from=today_str(), date_to=today_str())


def show_checklist(conn):
    """Show today's habit checklist."""
    scheduled = get_scheduled_habits(conn)
    logs = {l["habit_id"]: l for l in get_today_logs(conn)}

    today = date.today()
    day_name = today.strftime("%A, %B %d, %Y")

    print(f"\n{'='*55}")
    print(f"  HABIT TRACKER — {day_name}")
    print(f"{'='*55}")

    if not scheduled:
        print("\n  No habits scheduled for today. Rest day! 🎉\n")
        return

    done_count = 0
    total = len(scheduled)

    # Group by time of day
    groups = {"morning": [], "afternoon": [], "evening": []}
    for h in scheduled:
        t = h["preferred_time"] or "morning"
        if t not in groups:
            groups[t] = []
        groups[t].append(h)

    for period in ["morning", "afternoon", "evening"]:
        items = groups.get(period, [])
        if not items:
            continue
        print(f"\n  ☀️  {period.upper()}" if period == "morning" else
              f"\n  🌤  {period.upper()}" if period == "afternoon" else
              f"\n  🌙  {period.upper()}")
        print(f"  {'-'*40}")

        for h in items:
            logged = h["id"] in logs
            if logged:
                done_count += 1
            mark = "✅" if logged else "☐"
            dur = f" ({h['target_duration_minutes']}min)" if h["target_duration_minutes"] else ""
            streak = f" 🔥{h['current_streak']}" if (h["current_streak"] or 0) > 0 else ""
            print(f"  {mark} [{h['id']:>2}] {h['name']}{dur}{streak}")

    print(f"\n{'='*55}")
    print(f"  Progress: {done_count}/{total} completed")
    bar_len = 20
    filled = int(bar_len * done_count / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"  [{bar}] {done_count*100//total}%")
    print(f"{'='*55}\n")

    if done_count == total:
        print("  🏆 ALL DONE! Perfect day!\n")
    elif done_count > 0:
        print(f"  💪 {total - done_count} left. Keep going!\n")
    else:
        print("  🌅 Fresh start — log your first habit!\n")
        print("  Usage: python3 habit-tracker.py done <id> [id2 ...]")
        print("  Example: python3 habit-tracker.py done 1 3 5\n")


def mark_done(conn, habit_ids, duration=None, notes=None):
    """Mark habits as done for today (the service recomputes streaks)."""
    today = today_str()
    for hid in habit_ids:
        h = conn.habit(hid)
        if not h:
            print(f"  ❌ Habit #{hid} not found")
            continue

        # Check if already logged
        if conn.habit_logs(habit_id=hid, date_from=today, date_to=today):
            print(f"  ⏭  [{hid}] {h['name']} — already logged today")
            continue

        fields = {"completed": True, "count": 1}
        if duration is not None:
            fields["duration_minutes"] = duration
        if notes is not None:
            fields["notes"] = notes
        conn.log_habit(hid, today, fields)

        # The service owns streaks: re-read the habit for the new value.
        fresh = conn.habit(hid) or h
        new_streak = max(fresh.get("current_streak") or 0, 1)
        print(f"  ✅ [{hid}] {h['name']} 🔥{new_streak}")

    print()


def list_habits(conn):
    """List all active habits."""
    habits = _by_priority(conn.habits(status="active"))

    print(f"\n{'='*60}")
    print(f"  ALL ACTIVE HABITS ({len(habits)})")
    print(f"{'='*60}\n")

    for h in habits:
        days = h["schedule_days"] if h["schedule_days"] else ["daily"]
        day_str = ",".join(d.capitalize() for d in days)
        dur = f"{h['target_duration_minutes']}min" if h["target_duration_minutes"] else "—"
        streak = f"🔥{h['current_streak']}" if (h["current_streak"] or 0) > 0 else "—"
        print(f"  [{h['id']:>2}] {h['name']:<25} {h['category']:<12} {dur:<8} {streak:<6} {day_str}")

    print()


def show_streaks(conn):
    """Show streak summary."""
    habits = sorted(conn.habits(status="active"), key=lambda h: -(h["current_streak"] or 0))

    print(f"\n{'='*50}")
    print(f"  🔥 STREAK BOARD")
    print(f"{'='*50}\n")

    for h in habits:
        current = h["current_streak"] or 0
        best = h["best_streak"] or 0
        bar = "█" * min(current, 30)
        last = h["last_completed_at"] or "never"
        print(f"  [{h['id']:>2}] {h['name']:<25} 🔥{current:<4} best:{best:<4} {bar}")
        print(f"       last: {last}")

    print()


def show_week(conn):
    """Show this week's completion grid."""
    today = date.today()
    start = today - timedelta(days=today.weekday())  # Monday

    habits = _by_priority(conn.habits(status="active"))
    end = start + timedelta(days=6)
    week_logs = {(l["habit_id"], l["log_date"]): l
                 for l in conn.habit_logs(date_from=start.isoformat(), date_to=end.isoformat())}

    print(f"\n{'='*60}")
    end = start + timedelta(days=6)
    print(f"  WEEKLY VIEW — {start.isoformat()} to {end.isoformat()}")
    print(f"{'='*60}\n")

    # Header
    header = f"  {'Habit':<22}"
    for i in range(7):
        d = start + timedelta(days=i)
        label = DAY_NAMES[i][:2]
        if d == today:
            label = f"*{label}"
        header += f"  {label}"
    print(header)
    print(f"  {'-'*55}")

    for h in habits:
        row = f"  {h['name']:<22}"
        for i in range(7):
            d = (start + timedelta(days=i)).isoformat()
            log = week_logs.get((h["id"], d))
            if log and log["completed"]:
                row += "  ✅"
            elif (start + timedelta(days=i)) > today:
                row += "  ·"
            else:
                row += "  ·"
        print(row)

    print(f"\n  *Mo = today | ✅ = done | · = not logged\n")


def add_habit(conn, name, category="productivity", frequency="daily", duration=None, days=None):
    """Add a new habit."""
    # The API accepts [a-z0-9-] slugs; a name without ASCII letters or digits gets a generated one.
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30].strip("-") or f"habit-{uuid.uuid4().hex[:8]}"
    schedule = list(days) if days else ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    fields = {"name": name, "slug": slug, "category": category, "frequency": frequency,
              "schedule_days": schedule, "status": "active"}
    if duration is not None:
        fields["target_duration_minutes"] = duration
    conn.create_habit(fields)
    print(f"  ✅ Added habit: {name} ({category}, {frequency})\n")


def main():
    try:
        _run()
    except (HabitsStoreError, ClientConfigError) as exc:
        print(f"  ❌ {exc}")
        sys.exit(1)


def _run():
    conn = get_conn()
    if len(sys.argv) < 2:
        show_checklist(conn)
        return

    cmd = sys.argv[1]

    if cmd == "done":
        if len(sys.argv) < 3:
            print("  Usage: habit-tracker.py done <id> [id2 ...] [-d minutes] [-n 'note']")
            return
        ids = []
        duration = None
        notes = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "-d" and i + 1 < len(sys.argv):
                duration = int(sys.argv[i + 1])
                i += 2
            elif sys.argv[i] == "-n" and i + 1 < len(sys.argv):
                notes = sys.argv[i + 1]
                i += 2
            else:
                ids.append(int(sys.argv[i]))
                i += 1
        mark_done(conn, ids, duration, notes)

    elif cmd == "list":
        list_habits(conn)

    elif cmd == "streaks":
        show_streaks(conn)

    elif cmd == "week":
        show_week(conn)

    elif cmd == "add":
        if len(sys.argv) < 3:
            print("  Usage: habit-tracker.py add \"Habit Name\" -c category -f daily -t 30")
            return
        name = sys.argv[2]
        cat = "productivity"
        freq = "daily"
        dur = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "-c" and i + 1 < len(sys.argv):
                cat = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "-f" and i + 1 < len(sys.argv):
                freq = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "-t" and i + 1 < len(sys.argv):
                dur = int(sys.argv[i + 1]); i += 2
            else:
                i += 1
        add_habit(conn, name, cat, freq, dur)

    else:
        print(f"  Unknown command: {cmd}")
        print("  Commands: done, list, streaks, week, add")


if __name__ == "__main__":
    main()

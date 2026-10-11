#!/usr/bin/env python3
"""
Solo Empire Dashboard — Unified daily view
Combines: Habits + Goals + Projects + Revenue (CRM ledger) + Skills + Routines + Work Tasks
Usage:
    python3 infra/scripts/empire.py              # Full daily dashboard
    python3 infra/scripts/empire.py --brief      # Quick summary
    python3 infra/scripts/empire.py --habits     # Habits only
    python3 infra/scripts/empire.py --goals      # Goals only
    python3 infra/scripts/empire.py --projects   # Projects only
    python3 infra/scripts/empire.py --skills     # Skills only
    python3 infra/scripts/empire.py --routines   # Routines only
    python3 infra/scripts/empire.py --work       # Work tasks (Leantime) only
    python3 infra/scripts/empire.py --stats      # Key metrics only
"""

# Add utils directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from cell_store import CellStoreError, open_cell_store
from habits_store import HabitsStoreError, completed_today, open_habits_store, scheduled_on
from task_store import ClientConfigError
import crm_ledger
from finance_totals import format_money, safe_revenue_overview  # noqa: E402
import sys
from pathlib import Path
from datetime import date, timedelta


def cell(name):
    """The store of one portfolio cell (goals, projects or roles-skills), read-only use."""
    return open_cell_store(name, "reporting-reader")


def prio_key(row):
    """ORDER BY priority (NULL first, as SQLite sorts it), then id."""
    return (row.get("priority") is not None, row.get("priority") or 0, row["id"])


def section_header(icon, title):
    print(f"\n{'─' * 60}")
    print(f"  {icon}  {title}")
    print(f"{'─' * 60}")

def show_header():
    today = date.today()
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print(f"║  👑 SOLO EMPIRE DASHBOARD                              ║")
    print(f"║  {today.strftime('%A, %B %d, %Y'):^54} ║")
    print("╚══════════════════════════════════════════════════════════╝")

def show_habits():
    """Today's habits."""
    section_header("⚡", "TODAY'S HABITS")

    day_abbr = ["mon","tue","wed","thu","fri","sat","sun"][date.today().weekday()]

    try:
        habits = open_habits_store("reporting-reader").habits(status="active")
    except (HabitsStoreError, ClientConfigError):
        print("  Habits unavailable (habits API).")
        return
    habits.sort(key=lambda h: (h.get("priority") is not None, h.get("priority") or 0))
    scheduled = [h for h in habits if scheduled_on(h, day_abbr)]

    if not scheduled:
        print("  No habits scheduled for today.")
        return

    done = sum(1 for h in scheduled if str(h["last_completed_at"] or "")[:10] == date.today().isoformat())
    total = len(scheduled)

    for h in scheduled:
        is_done = str(h["last_completed_at"] or "")[:10] == date.today().isoformat()
        icon = "✅" if is_done else "⬜"
        streak = f"🔥{h['current_streak']}" if h["current_streak"] > 0 else ""
        time_icon = {"morning": "🌅", "afternoon": "☀️", "evening": "🌙"}.get(h["preferred_time"], "⏰")
        print(f"  {icon} {h['name']} {time_icon} {streak}")

    pct = int(done / total * 100) if total else 0
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    print(f"\n  Progress: {bar} {done}/{total} ({pct}%)")

def show_goals():
    """Active goals with progress."""
    section_header("🎯", "GOALS")

    try:
        store = cell("goals")
        active = sorted(store.list("goals", status="active"), key=prio_key)[:5]
        task_counts = {}
        if active:
            for task in store.list("goal-tasks", goal_id=[g["id"] for g in active]):
                counts = task_counts.setdefault(task["goal_id"], [0, 0])
                counts[0] += 1
                counts[1] += 1 if task["status"] == "done" else 0
    except (CellStoreError, ClientConfigError) as exc:
        print(f"  Goals unavailable: {exc}")
        return
    goals = [
        {**g, "total_tasks": task_counts.get(g["id"], [0, 0])[0], "done_tasks": task_counts.get(g["id"], [0, 0])[1]}
        for g in active
    ]

    if not goals:
        print("  No active goals.")
        return

    for g in goals:
        pct = g["progress_percent"] or 0
        bar = "█" * (pct // 7) + "░" * (15 - pct // 7)
        task_str = f"{g['done_tasks']}/{g['total_tasks']}t" if g["total_tasks"] > 0 else ""

        due_str = ""
        if g["due_date"]:
            days_left = (date.fromisoformat(str(g["due_date"])[:10]) - date.today()).days
            if days_left < 0:
                due_str = " ⚠️OVERDUE"
            elif days_left <= 14:
                due_str = f" ⚠️{days_left}d"

        print(f"  [{g['id']}] {g['title']}")
        print(f"      {bar} {pct}% {task_str}{due_str}")

def _per_currency(totals):
    return ", ".join(format_money(amount, currency) for currency, amount in sorted(totals.items())) or format_money(0)


def ledger_revenue(since=None):
    """(rows, totals) from the CRM ledger (income-pipeline), or None when it is unreachable."""
    try:
        return crm_ledger.open_crm_ledger().revenue_all(since=since)
    except (crm_ledger.LedgerError, ClientConfigError):
        return None


def show_projects():
    """Active projects."""
    section_header("🚀", "PROJECTS")

    try:
        active = sorted(
            (p for p in cell("projects").list("projects") if p["status"] in ("active", "planning")), key=prio_key
        )[:6]
    except (CellStoreError, ClientConfigError) as exc:
        print(f"  Projects unavailable: {exc}")
        return
    ledger = ledger_revenue()
    revenue_by_source = {}  # the reporting currency only; other currencies are never added into it
    for row in (ledger[0] if ledger else []):
        if (row.get("currency") or "THB") == "THB":
            revenue_by_source[row["source_name"]] = revenue_by_source.get(row["source_name"], 0) + float(row["amount"] or 0)
    projects = [{**p, "rev": revenue_by_source.get(p["name"], 0) or 0} for p in active]

    if not projects:
        print("  No active projects.")
        return

    for p in projects:
        pct = p["progress_percent"] or 0
        bar = "█" * (pct // 7) + "░" * (15 - pct // 7)
        rev_str = f" 💰{format_money(p['rev'])}" if p["rev"] > 0 else ""
        phase = f" [{p['phase']}]" if p["phase"] else ""
        print(f"  [{p['id']}] {p['name']}{phase}")
        print(f"      {bar} {pct}%{rev_str}")

def show_revenue():
    """Revenue summary."""
    section_header("💰", "REVENUE")

    today = date.today()
    month_start = today.replace(day=1).isoformat()

    month = ledger_revenue(month_start)
    all_time = ledger_revenue()
    if month is None or all_time is None:
        print("  Revenue unavailable (CRM ledger)")
        return

    print(f"  This month: {_per_currency(month[1])}")  # per currency, never summed across currencies
    print(f"  All-time:   {_per_currency(all_time[1])}")

    recent = all_time[0][:3]
    if recent:
        print(f"\n  Recent:")
        for r in recent:
            print(f"    {r['revenue_date']} | {format_money(r['amount'], r.get('currency'))} |"
                  f" {r.get('source_name') or r['source_type']}")

def show_skills():
    """Active skills being practiced."""
    section_header("📚", "SKILLS IN PROGRESS")

    try:
        store = cell("roles-skills")
        practicing = [sk for sk in store.list("skills", status="practicing")]
        categories = {c["id"]: c["name"] for c in store.list("skill-categories")}
    except (CellStoreError, ClientConfigError) as exc:
        print(f"  Skills unavailable: {exc}")
        return
    practicing.sort(key=lambda sk: (sk.get("priority") is not None, sk.get("priority") or 0,
                                    -(sk.get("progress_percent") or 0), sk["id"]))
    skills = [{**sk, "category_name": categories.get(sk.get("category_id"))} for sk in practicing[:8]]

    if not skills:
        print("  No skills in progress.")
        return

    for s in skills:
        pct = s["progress_percent"] or 0
        bar = "█" * (pct // 7) + "░" * (15 - pct // 7)
        hours = s["hours_invested"] or 0
        level = s["level"] or "L1"
        print(f"  [{s['id']}] {s['name']} ({s['category_name']})")
        print(f"      {bar} {pct}% | {level} | {hours:.1f}h")

def show_routines():
    """Today's routines."""
    section_header("🔄", "ROUTINES")

    today = date.today().isoformat()
    try:
        store = open_habits_store("reporting-reader")
        routines = sorted(store.routines(status="active"), key=lambda r: r["id"])
        logged = {log["routine_id"] for log in store.routine_logs(date_from=today, date_to=today)}
    except (HabitsStoreError, ClientConfigError):
        print("  Routines unavailable (habits API).")
        return
    for r in routines:
        r["done_today"] = 1 if r["id"] in logged else 0

    if not routines:
        print("  No routines configured.")
        return

    type_icons = {"daily_morning": "🌅", "daily_evening": "🌙", "weekly_review": "📅", "monthly_review": "📆"}
    for r in routines:
        icon = type_icons.get(r["routine_type"], "🔄")
        done = "✅" if r["done_today"] > 0 else "⬜"
        dur = r["total_duration_minutes"] or 0
        print(f"  {done} {icon} {r['name']} (~{dur}min)")

def show_work_tasks():
    """Explain that employer work is managed outside Solo Empire."""
    section_header("🏢", "EXTERNAL EMPLOYER WORK")
    print("  Managed in nexatech-empire; not synced into Solo Empire.")

def show_stats():
    """Key metrics at a glance."""
    section_header("📊", "KEY METRICS")

    stats = {}

    # Habits
    today = date.today().isoformat()
    try:
        store = open_habits_store("reporting-reader")
        stats["habits_today"] = len(completed_today(store.habit_logs(date_from=today, date_to=today), today))
        stats["active_habits"] = len(store.habits(status="active"))
    except (HabitsStoreError, ClientConfigError):
        stats["habits_today"] = 0
        stats["active_habits"] = 0

    # Goals
    try:
        active_goals = cell("goals").list("goals", status="active")
        percents = [g["progress_percent"] for g in active_goals if g["progress_percent"] is not None]
        stats["active_goals"] = len(active_goals)
        stats["avg_goal_pct"] = sum(percents) / len(percents) if percents else 0
        # Projects
        stats["active_projects"] = len(cell("projects").list("projects", status=["active", "planning"]))
        # Skills
        stats["skills"] = len(cell("roles-skills").list("skills", status="practicing"))
    except (CellStoreError, ClientConfigError):
        stats["active_goals"] = stats["avg_goal_pct"] = stats["active_projects"] = stats["skills"] = 0

    # Revenue
    month_start = date.today().replace(day=1).isoformat()
    month = ledger_revenue(month_start)
    received = safe_revenue_overview(month_start)
    stats["monthly_rev"] = None if received.get("unavailable") else received["received_display"]

    # Employer work is managed in nexatech-empire.
    stats["work_tasks"] = 0

    print(f"  ⚡ Habits:     {stats['habits_today']}/{stats['active_habits']} done today")
    print(f"  🎯 Goals:      {stats['active_goals']} active (avg {stats['avg_goal_pct']:.0f}%)")
    print(f"  🚀 Projects:   {stats['active_projects']} active")
    print(f"  📚 Skills:     {stats['skills']} practicing")
    print("  🏢 Employer:   external (nexatech-empire)")
    rev = "unavailable (CRM ledger)" if stats["monthly_rev"] is None else f"{stats['monthly_rev']} received this month"
    print(f"  💰 Revenue:    {rev}")

def show_brief():
    """Quick summary - one line per section."""
    today = date.today()
    print(f"\n👑 SOLO EMPIRE — {today.strftime('%a %b %d')}")
    print("─" * 40)

    # Habits
    day_abbr = ["mon","tue","wed","thu","fri","sat","sun"][today.weekday()]
    try:
        habits = open_habits_store("reporting-reader").habits(status="active")
    except (HabitsStoreError, ClientConfigError):
        habits = None
    if habits is None:
        print("  ⚡ Habits:   unavailable (habits API)")
    else:
        scheduled = [h for h in habits if scheduled_on(h, day_abbr)]
        done = sum(1 for h in scheduled if str(h["last_completed_at"] or "")[:10] == today.isoformat())
        print(f"  ⚡ Habits:   {done}/{len(scheduled)} done")

    # Goals
    try:
        active_goals = cell("goals").list("goals", status="active")
        percents = [g["progress_percent"] for g in active_goals if g["progress_percent"] is not None]
        print(f"  🎯 Goals:    {len(active_goals)} active (avg {(sum(percents) / len(percents) if percents else 0):.0f}%)")

        # Projects
        print(f"  🚀 Projects: {len(cell('projects').list('projects', status=['active', 'planning']))} active")
    except (CellStoreError, ClientConfigError):
        print("  🎯 Goals:    unavailable (goals/projects API)")

    # Revenue
    month_start = today.replace(day=1).isoformat()
    month = ledger_revenue(month_start)
    if month is None:
        print("  💰 Revenue:  unavailable (CRM ledger)")
    else:
        print(f"  💰 Revenue:  {_per_currency(month[1])} this month")

    print("  🏢 Employer: external (nexatech-empire)")
    print()

def main():
    args = sys.argv[1:]

    if not args:
        show_header()
        show_habits()
        show_goals()
        show_projects()
        show_skills()
        show_routines()
        show_work_tasks()
        show_revenue()
        show_stats()
        print()
    elif args[0] == "--brief":
        show_brief()
    elif args[0] == "--habits":
        show_header(); show_habits()
    elif args[0] == "--goals":
        show_header(); show_goals()
    elif args[0] == "--projects":
        show_header(); show_projects()
    elif args[0] == "--skills":
        show_header(); show_skills()
    elif args[0] == "--routines":
        show_header(); show_routines()
    elif args[0] == "--work":
        show_header(); show_work_tasks()
    elif args[0] == "--stats":
        show_header(); show_stats()
    else:
        print(__doc__)

if __name__ == "__main__":
    main()

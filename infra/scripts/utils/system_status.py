#!/usr/bin/env python3
"""
System Status Aggregator
Runs all dashboard scripts and outputs a unified system snapshot.

Usage:
    python3 system_status.py              # Full status report
    python3 system_status.py --quick      # Quick 1-line-per-section summary
    python3 system_status.py --json       # JSON output
    python3 system_status.py --save       # Save to data/generated/briefings/system-status-DATE.md
"""

import json
from _env import BRIEFINGS_DIR, PROJECT_ROOT
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# Add utils directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from task_store import ClientConfigError, open_task_store
from cell_store import CellStoreError, open_cell_store
from habits_store import HabitsStoreError, completed_today, open_habits_store

SCRIPTS_DIR = PROJECT_ROOT / 'infra' / 'scripts' / 'utils'


def run_script(script_name, args=None):
    """Run a script and return stdout."""
    script = SCRIPTS_DIR / script_name
    if not script.exists():
        return f'❌ Script not found: {script_name}'
    cmd = [sys.executable, str(script)] + (args or [])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.returncode == 0 else f'❌ {script_name}: {result.stderr.strip()}'
    except subprocess.TimeoutExpired:
        return f'⏱️ {script_name}: timeout'
    except Exception as e:
        return f'❌ {script_name}: {e}'


def revenue_summary():
    """Revenue summary. ฿-labeled numeric fields are THB-only."""
    from finance_totals import safe_revenue_overview

    overview = safe_revenue_overview()  # CRM ledger; flagged unavailable when unreachable
    return {
        'unavailable': bool(overview.get('unavailable')),
        'received': overview['received'],
        'pending': overview['pending'],
        'total': overview['total'],
        'reporting_currency': overview['reporting_currency'],
        'received_display': overview['received_display'],
        'pending_display': overview['pending_display'],
        'by_currency': overview['by_currency'],
        'entries': overview['entries'],
    }


def goal_summary():
    """Goal summary; ``unavailable`` when the goals API cannot be reached."""
    try:
        store = open_cell_store("goals", "reporting-reader")
        total = len(store.list("goals", status="active"))
        task_counts = store.stats("goal-tasks", "status")
    except (CellStoreError, ClientConfigError):
        return {'goals': 0, 'tasks_total': 0, 'tasks_done': 0, 'unavailable': True}
    tasks_total = sum(task_counts.values())
    tasks_done = task_counts.get("done", 0)
    return {'goals': total, 'tasks_total': tasks_total, 'tasks_done': tasks_done}


def habit_summary():
    """Habit summary; ``unavailable`` when the habits API cannot be reached."""
    today = date.today().isoformat()
    try:
        store = open_habits_store("reporting-reader")
        active = len(store.habits(status="active"))
        logged_today = len(completed_today(store.habit_logs(date_from=today, date_to=today), today))
    except (HabitsStoreError, ClientConfigError):
        return {'active': None, 'logged_today': None, 'unavailable': True}
    return {'active': active, 'logged_today': logged_today}


def task_summary():
    """Task summary."""
    stats = open_task_store(None, "reporting-reader").stats(group_by='status', include_archived=True)
    total = stats['total']
    done = stats['groups'].get('done', 0)
    in_progress = stats['groups'].get('in_progress', 0)
    return {'total': total, 'done': done, 'in_progress': in_progress}


def print_full_status():
    """Print full system status."""
    today = date.today().isoformat()
    print('=' * 64)
    print('  🏰 SOLO EMPIRE — SYSTEM STATUS')
    print(f'  {today} {datetime.now().strftime("%H:%M:%S")}')
    print('=' * 64)

    # Revenue
    rev = revenue_summary()
    print(f'\n💰 Revenue')
    received_fallback = f"฿{rev['received']:,.0f}"
    pending_fallback = f"฿{rev['pending']:,.0f}"
    print(f'   Received: {rev.get("received_display", received_fallback)} | Pending: {rev.get("pending_display", pending_fallback)}')

    # Goals
    goals = goal_summary()
    print(f'\n🎯 Goals')
    print(f'   Active: {goals["goals"]} | Tasks: {goals["tasks_done"]}/{goals["tasks_total"]} done')

    # Habits
    habits = habit_summary()
    print(f'\n🔁 Habits')
    if habits.get('unavailable'):
        print('   unavailable (habits API)')
    else:
        print(f'   Active: {habits["active"]} | Logged today: {habits["logged_today"]}')

    # Tasks
    tasks = task_summary()
    print(f'\n📋 Tasks')
    print(f'   Total: {tasks["total"]} | Done: {tasks["done"]} | In Progress: {tasks["in_progress"]}')

    # Script outputs
    print(f'\n📊 Dashboard Scripts')
    print(f'   Revenue Analytics:')
    rev_output = run_script('revenue_analytics.py', ['--forecast'])
    for line in rev_output.split('\n')[:5]:
        print(f'      {line}')

    print(f'\n{"=" * 64}')


def quick_status():
    """Print quick 1-line summary."""
    rev = revenue_summary()
    goals = goal_summary()
    habits = habit_summary()
    tasks = task_summary()

    received_fallback = f"฿{rev['received']:,.0f}"
    # Built separately: nesting it in the f-string below reuses the outer quote (Python 3.12+ only).
    habits_display = "n/a" if habits.get("unavailable") else f"{habits['logged_today']}/{habits['active']}"
    print(f'🏰 Status: {rev.get("received_display", received_fallback)} received | {goals["tasks_done"]}/{goals["tasks_total"]} tasks | {habits_display} habits today | {tasks["done"]}/{tasks["total"]} tasks done')


def json_status():
    """Output as JSON."""
    return {
        'timestamp': datetime.now().isoformat(),
        'revenue': revenue_summary(),
        'goals': goal_summary(),
        'habits': habit_summary(),
        'tasks': task_summary(),
    }


def save_status():
    """Save status to file."""
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    filepath = BRIEFINGS_DIR / f'system-status-{today}.md'

    # Capture output
    import io
    from contextlib import redirect_stdout

    f = io.StringIO()
    with redirect_stdout(f):
        print_full_status()
    output = f.getvalue()

    with open(filepath, 'w') as fh:
        fh.write(output)
    print(f'✅ Saved to {filepath}')


def main():
    quick = '--quick' in sys.argv
    as_json = '--json' in sys.argv
    save = '--save' in sys.argv

    if as_json:
        print(json.dumps(json_status(), indent=2, default=str))
    elif quick:
        quick_status()
    elif save:
        save_status()
    else:
        print_full_status()


if __name__ == '__main__':
    main()

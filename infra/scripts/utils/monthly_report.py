#!/usr/bin/env python3
"""
Monthly Report Generator — Summarize monthly revenue, goals, habits, tasks.

Generates comprehensive monthly report with:
  - Revenue summary (received vs pending)
  - Goal progress and completions
  - Habit consistency
  - Task completion rate
  - System activity

Usage:
    python3 monthly_report.py              # Current month report
    python3 monthly_report.py --month 2026-06  # Specific month
    python3 monthly_report.py --save       # Save to data/generated/reports/
    python3 monthly_report.py --json       # JSON output
"""

import os
import sys
import json
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from habits_store import open_habits_store
from cell_store import open_cell_store
from finance_totals import safe_revenue_overview

REPORTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'generated', 'reports')


def get_month_range(year, month):
    """Get start and end dates for a month."""
    start = f'{year}-{month:02d}-01'
    if month == 12:
        end = f'{year + 1}-01-01'
    else:
        end = f'{year}-{month + 1:02d}-01'
    return start, end


def get_revenue_summary(year, month):
    """Get revenue stats for the month."""
    start, end = get_month_range(year, month)
    last_day = (datetime.fromisoformat(end) - timedelta(days=1)).strftime('%Y-%m-%d')

    overview = safe_revenue_overview(start, last_day)  # CRM ledger; flagged unavailable when unreachable
    return {
        'count': overview['entries'],
        'total': overview['total'],
        'received': overview['received'],
        'pending': overview['pending'],
        'received_display': overview['received_display'],
        'pending_display': overview['pending_display'],
        'by_currency': overview['by_currency'],
        'unavailable': bool(overview.get('unavailable')),
    }


def get_goal_summary(year, month):
    """Get goal progress for the month."""
    store = open_cell_store('goals', 'reporting-reader')
    goal_counts = store.stats('goals', 'status')
    active = goal_counts.get('active', 0)
    completed = goal_counts.get('completed', 0)
    progress = [g['progress_percent'] for g in store.list('goals', status='active') if g['progress_percent'] is not None]
    avg_progress = sum(progress) / len(progress) if progress else 0

    # Tasks completed this month
    start, end = get_month_range(year, month)
    tasks_done = sum(
        1 for t in store.list('goal-tasks', status='done') if start <= str(t['updated_at'] or '') < end
    )

    return {
        'active': active,
        'completed': completed,
        'avg_progress': round(avg_progress, 1),
        'tasks_done': tasks_done,
    }


def get_habit_summary(year, month):
    """Get habit consistency for the month."""
    store = open_habits_store('reporting-reader')
    start, end = get_month_range(year, month)

    total_habits = len(store.habits())

    last_day = (date.fromisoformat(str(end)[:10]) - timedelta(days=1)).isoformat()  # end is exclusive
    logs = [log for log in store.habit_logs(date_from=start, date_to=last_day, completed=True) if log['log_date'] < end]
    habits_logged = len({log['habit_id'] for log in logs})
    completions = len(logs)

    # Calculate days in month
    if month == 12:
        days = 31
    else:
        days = (datetime(year, month + 1, 1) - datetime(year, month, 1)).days

    consistency = round(completions / (total_habits * days) * 100, 1) if total_habits > 0 else 0

    return {
        'total': total_habits,
        'logged': habits_logged,
        'completions': completions,
        'consistency': consistency,
    }


def get_task_summary(year, month):
    """Get task completion stats."""
    start, end = get_month_range(year, month)
    store = open_cell_store('goals', 'reporting-reader')
    done_tasks = store.list('goal-tasks', status='done')
    done = sum(1 for t in done_tasks if start <= str(t['updated_at'] or '') < end)
    total_done = len(done_tasks)
    total = sum(store.stats('goal-tasks', 'status').values())

    return {
        'done_this_month': done,
        'total_done': total_done,
        'total': total,
        'completion_rate': round(total_done / total * 100, 1) if total > 0 else 0,
    }


def get_at_risk_goals():
    """Get goals that are at-risk (high/critical)."""
    today = datetime.now().strftime('%Y-%m-%d')

    active_goals = open_cell_store('goals', 'reporting-reader').list('goals', status='active')
    active_goals.sort(key=lambda g: (g['priority'] is None, -(g['priority'] or 0), g['id']))
    goals = [
        (g['id'], g['title'], g['progress_percent'], str(g['due_date'])[:10] if g.get('due_date') else None,
         g['priority'])
        for g in active_goals
    ]

    at_risk = []
    for g in goals:
        progress = g[2] or 0
        due = g[3]
        if due:
            try:
                due_date = datetime.strptime(due, '%Y-%m-%d').date()
                days_left = (due_date - datetime.now().date()).days
                if (days_left < 30 and progress < 50) or progress < 15:
                    at_risk.append({'title': g[1], 'progress': progress, 'due': due, 'days_left': days_left})
            except ValueError:
                pass
        elif progress < 15:
            at_risk.append({'title': g[1], 'progress': progress, 'due': None, 'days_left': None})

    return at_risk


def generate_report(year=None, month=None):
    """Generate full monthly report."""
    if not year or not month:
        now = datetime.now()
        year = now.year
        month = now.month

    month_name = datetime(year, month, 1).strftime('%B %Y')

    revenue = get_revenue_summary(year, month)
    goals = get_goal_summary(year, month)
    habits = get_habit_summary(year, month)
    tasks = get_task_summary(year, month)
    at_risk = get_at_risk_goals()

    # Build at-risk section
    at_risk_lines = ''
    if at_risk:
        at_risk_lines = '\n### ⚠️ At-Risk Goals\n'
        for g in at_risk:
            due_str = f', due {g["due"]}' if g['due'] else ''
            at_risk_lines += f'- **{g["title"]}** — {g["progress"]}%{due_str}\n'
    else:
        at_risk_lines = '\n✅ No at-risk goals detected.\n'

    if revenue.get('unavailable'):
        revenue_lines = "- **Unavailable:** the CRM ledger could not be read; no revenue figures this month."
    else:
        revenue_lines = (f"- **Received:** {revenue['received_display']}\n"
                         f"- **Pending:** {revenue['pending_display']}\n"
                         f"- **Entries:** {revenue['count']}")
    report = f"""# 📊 Monthly Report — {month_name}

*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*

## 💰 Revenue

{revenue_lines}

## 🎯 Goals

- **Active:** {goals['active']}
- **Completed:** {goals['completed']}
- **Avg Progress:** {goals['avg_progress']}%
- **Tasks Done:** {goals['tasks_done']}
{at_risk_lines}
## 🔥 Habits

- **Total Tracked:** {habits['total']}
- **Logged This Month:** {habits['logged']}
- **Completions:** {habits['completions']}
- **Consistency:** {habits['consistency']}%

## ✅ Tasks

- **Done This Month:** {tasks['done_this_month']}
- **Total Completed:** {tasks['total_done']}/{tasks['total']}
- **Completion Rate:** {tasks['completion_rate']}%

---

## Summary

{'⚪' if revenue.get('unavailable') else ('🟢' if revenue['received'] > 0 else '🔴')} Revenue: {'unavailable (CRM ledger)' if revenue.get('unavailable') else revenue.get('received_display')} received
{'🟢' if goals['avg_progress'] > 50 else '🟡'} Goals: {goals['avg_progress']}% avg progress
{'🟢' if habits['consistency'] > 70 else '🟡' if habits['consistency'] > 40 else '🔴'} Habits: {habits['consistency']}% consistency
{'🟢' if tasks['done_this_month'] > 10 else '🟡'} Tasks: {tasks['done_this_month']} completed
"""

    return report


def main():
    args = sys.argv[1:]
    json_output = '--json' in args
    save_output = '--save' in args

    year = None
    month = None

    if '--month' in args:
        idx = args.index('--month')
        if idx + 1 < len(args):
            month_str = args[idx + 1]
            try:
                dt = datetime.strptime(month_str, '%Y-%m')
                year = dt.year
                month = dt.month
            except ValueError:
                print(f'Invalid month format: {month_str}. Use YYYY-MM')
                return

    if json_output:
        now = datetime.now()
        y = year or now.year
        m = month or now.month
        data = {
            'month': f'{y}-{m:02d}',
            'revenue': get_revenue_summary(y, m),
            'goals': get_goal_summary(y, m),
            'habits': get_habit_summary(y, m),
            'tasks': get_task_summary(y, m),
            'at_risk_goals': get_at_risk_goals(),
        }
        print(json.dumps(data, indent=2))
        return

    report = generate_report(year, month)

    if save_output:
        now = datetime.now()
        y = year or now.year
        m = month or now.month
        filename = f'monthly-report-{y}-{m:02d}.md'
        path = os.path.join(REPORTS_DIR, filename)
        os.makedirs(REPORTS_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f'✅ Saved to {path}')
    else:
        print(report)


if __name__ == '__main__':
    main()

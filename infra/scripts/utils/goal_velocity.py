#!/usr/bin/env python3
"""
Goal Velocity — Progress rate, ETA to completion, at-risk detection.

Usage:
    python3 goal_velocity.py              # Full goal velocity report
    python3 goal_velocity.py --json       # JSON output
    python3 goal_velocity.py --at-risk    # Show only at-risk goals
"""

import json
# Add utils directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError
import sys
from datetime import datetime, timedelta
from pathlib import Path



AUTOMATION_ID = 'reporting-reader'


def get_conn():
    """The goals cell store (goals and goal tasks live in the goals cell, not local SQLite)."""
    return open_cell_store('goals', AUTOMATION_ID)


def get_goal_velocity(conn):  # conn: the goals cell store
    """Calculate velocity and ETA for each active goal."""
    goal_tasks = conn.list('goal-tasks')
    totals, dones = {}, {}
    for t in goal_tasks:
        totals[t['goal_id']] = totals.get(t['goal_id'], 0) + 1
        if t['status'] == 'done':
            dones[t['goal_id']] = dones.get(t['goal_id'], 0) + 1
    goals = sorted(conn.list('goals', status='active'),
                   key=lambda g: (-(g['priority'] or 0), -(g['progress_percent'] or 0)))

    today = datetime.now().date()
    results = []

    for g in goals:
        goal_id = g['id']
        progress = g['progress_percent'] or 0
        total_tasks = totals.get(goal_id, 0)
        done_tasks = dones.get(goal_id, 0)
        created = g['created_at'][:10] if g['created_at'] else None
        due = g['due_date'][:10] if g['due_date'] else None

        # Calculate age in days
        age_days = 0
        if created:
            try:
                created_date = datetime.strptime(created, '%Y-%m-%d').date()
                age_days = max(1, (today - created_date).days)
            except ValueError:
                age_days = 1

        # Velocity: progress per day
        velocity = round(progress / age_days, 2) if age_days > 0 else 0

        # ETA: days to reach 100% at current velocity
        eta_days = None
        if velocity > 0 and progress < 100:
            remaining = 100 - progress
            eta_days = round(remaining / velocity)

        # ETA date
        eta_date = None
        if eta_days is not None:
            eta_date = (today + timedelta(days=eta_days)).isoformat()

        # Days until due
        days_until_due = None
        if due:
            try:
                due_date = datetime.strptime(due, '%Y-%m-%d').date()
                days_until_due = (due_date - today).days
            except ValueError:
                pass

        # Task completion rate
        task_rate = round(done_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0

        # Risk assessment
        risk = assess_risk(progress, velocity, days_until_due, eta_days, task_rate, total_tasks)

        # Recent progress (last 7 days) — check goal_tasks updates
        week_ago = (today - timedelta(days=7)).isoformat()
        recent_updates = sum(1 for t in goal_tasks
                             if t['goal_id'] == goal_id and (t['updated_at'] or '') >= week_ago)

        results.append({
            'id': goal_id,
            'title': g['title'],
            'category': g['category'],
            'domain': g['domain'],
            'type': g['type'],
            'priority': g['priority'],
            'progress': progress,
            'velocity_per_day': velocity,
            'eta_days': eta_days,
            'eta_date': eta_date,
            'due_date': due,
            'days_until_due': days_until_due,
            'age_days': age_days,
            'total_tasks': total_tasks,
            'done_tasks': done_tasks,
            'task_rate': task_rate,
            'risk': risk,
            'recent_updates': recent_updates,
        })

    return results


def assess_risk(progress, velocity, days_until_due, eta_days, task_rate, total_tasks):
    """Determine risk level: low, medium, high, critical."""
    # No progress at all
    if progress == 0 and total_tasks > 0 and task_rate == 0:
        return 'critical'

    # Very slow velocity
    if velocity == 0 and progress < 50:
        return 'high'

    # Has due date
    if days_until_due is not None and eta_days is not None:
        if eta_days > days_until_due + 30:
            return 'critical'
        if eta_days > days_until_due:
            return 'high'
        if days_until_due < 14 and progress < 70:
            return 'high'

    # Slow but moving
    if velocity < 0.5 and progress < 50:
        return 'medium'

    # Good progress
    if progress >= 70 or velocity >= 2:
        return 'low'

    return 'medium'


def get_portfolio_summary(conn):
    """Overall goal portfolio stats."""
    goals = conn.list('goals')
    active = [g for g in goals if g['status'] == 'active']
    completed = [g for g in goals if g['status'] == 'completed']

    avg_progress = sum(g['progress_percent'] or 0 for g in active) / len(active) if active else 0

    # Category distribution
    cats = {}
    for g in active:
        cat = g['category'] or 'other'
        cats[cat] = cats.get(cat, 0) + 1

    # Type distribution
    types = {}
    for g in active:
        t = g['type'] or 'other'
        types[t] = types.get(t, 0) + 1

    return {
        'total': len(goals),
        'active': len(active),
        'completed': len(completed),
        'avg_progress': round(avg_progress, 1),
        'by_category': cats,
        'by_type': types,
    }


def generate_report(at_risk_only=False):
    """Generate text report."""
    conn = get_conn()
    goals = get_goal_velocity(conn)
    portfolio = get_portfolio_summary(conn)

    if at_risk_only:
        goals = [g for g in goals if g['risk'] in ('high', 'critical')]

    lines = []
    lines.append('# 🎯 Goal Velocity')
    lines.append(f'\n*Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}*\n')

    # Portfolio summary
    if not at_risk_only:
        lines.append('## Portfolio Overview\n')
        lines.append(f'| Metric | Value |')
        lines.append(f'|--------|-------|')
        lines.append(f'| Active Goals | {portfolio["active"]} |')
        lines.append(f'| Completed | {portfolio["completed"]} |')
        lines.append(f'| Avg Progress | {portfolio["avg_progress"]}% |')

        lines.append(f'\n**By Category:** ')
        lines.append(', '.join(f'{k}: {v}' for k, v in sorted(portfolio['by_category'].items())))

        lines.append(f'\n**By Type:** ')
        lines.append(', '.join(f'{k}: {v}' for k, v in sorted(portfolio['by_type'].items())))

    # Risk summary
    risk_counts = {}
    for g in goals:
        risk_counts[g['risk']] = risk_counts.get(g['risk'], 0) + 1

    lines.append('\n## Risk Summary\n')
    for level in ['critical', 'high', 'medium', 'low']:
        count = risk_counts.get(level, 0)
        icon = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🟢'}.get(level, '⚪')
        lines.append(f'- {icon} {level.capitalize()}: {count}')

    # Goal details
    lines.append('\n## Goal Details\n')
    lines.append('| Goal | Progress | Velocity | ETA | Due | Risk | Tasks |')
    lines.append('|------|----------|----------|-----|-----|------|-------|')
    for g in sorted(goals, key=lambda x: {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}.get(x['risk'], 4)):
        risk_icon = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🟢'}.get(g['risk'], '⚪')
        eta_str = f'{g["eta_days"]}d' if g['eta_days'] else '∞'
        due_str = g['due_date'] or '—'
        if g['days_until_due'] is not None and g['days_until_due'] < 0:
            due_str += ' (OVERDUE)'
        lines.append(f'| {g["title"][:35]} | {g["progress"]}% | {g["velocity_per_day"]:.1f}%/d | '
                      f'{eta_str} | {due_str} | {risk_icon} | {g["done_tasks"]}/{g["total_tasks"]} |')

    return '\n'.join(lines)


def main():
    args = sys.argv[1:]
    json_mode = '--json' in args
    at_risk_only = '--at-risk' in args

    if json_mode:
        conn = get_conn()
        goals = get_goal_velocity(conn)
        if at_risk_only:
            goals = [g for g in goals if g['risk'] in ('high', 'critical')]
        result = {
            'timestamp': datetime.now().isoformat(),
            'portfolio': get_portfolio_summary(conn),
            'goals': goals,
        }
        print(json.dumps(result, indent=2))
    else:
        print(generate_report(at_risk_only))


if __name__ == '__main__':
    try:
        main()
    except (CellStoreError, ClientConfigError) as exc:
        print(f'❌ {exc}')
        sys.exit(1)

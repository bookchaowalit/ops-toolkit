#!/usr/bin/env python3
"""
Goal Progress Calculator
Auto-updates progress_percent of goals (goals cell) based on:
  - Task completion (primary signal)
  - Activity momentum from completed goal tasks and skills (secondary signal)

Usage:
    python3 goal_progress.py              # Show current vs calculated
    python3 goal_progress.py --update     # Update DB with calculated values
    python3 goal_progress.py --json       # JSON output
    python3 goal_progress.py --momentum   # Show activity momentum only
"""

import json
# Add utils directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError
from portfolio_progress import rollup as rollup_portfolio_progress
from portfolio_context_seed import seed as seed_portfolio_contexts
import sys
from datetime import datetime, timedelta
from pathlib import Path



AUTOMATION_ID = 'progress-rollup'


def open_goals():
    return open_cell_store('goals', AUTOMATION_ID)


def open_roles():
    return open_cell_store('roles-skills', AUTOMATION_ID)


def get_activity_momentum(days=30, goals=None, roles=None):
    """Calculate activity momentum per domain from completed goal tasks and skills."""
    goals = goals or open_goals()
    roles = roles or open_roles()
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # Goal tasks COMPLETED recently (not just updated), grouped by goal domain
    tasks_by_domain = {}
    domain_of = {g['id']: g['domain'] for g in goals.list('goals')}
    for gt in goals.list('goal-tasks', status='done'):
        if gt['goal_id'] in domain_of and (gt['completed_at'] or '') >= cutoff:
            domain = domain_of[gt['goal_id']]
            tasks_by_domain[domain] = tasks_by_domain.get(domain, 0) + 1

    # Skills practiced recently
    skills_by_domain = {}
    cat_names = {c['id']: c['name'] for c in roles.list('skill-categories')}
    cat_counts = {}
    for sk in roles.list('skills', status='practicing'):
        if sk['category_id'] in cat_names and (sk['updated_at'] or '') >= cutoff:
            name = cat_names[sk['category_id']]
            cat_counts[name] = cat_counts.get(name, 0) + 1
    # Map broad categories to domains
    cat_to_domain = {
        'Core CS': 'book-dev', 'Infrastructure': 'book-dev',
        'Software Engineering': 'book-dev', 'Data': 'book-ai',
        'AI/ML': 'book-ai', 'Security': 'book-dev',
        'Business Strategy': 'book-work', 'Marketing': 'book-marketing',
        'Sales': 'book-marketing', 'Finance': 'book-work',
        'Health': 'book-health', 'Longevity': 'book-health',
    }
    for cat_name, cnt in cat_counts.items():
        d = cat_to_domain.get(cat_name, '')
        if d:
            skills_by_domain[d] = skills_by_domain.get(d, 0) + cnt

    return {
        'tasks': tasks_by_domain,
        'skills': skills_by_domain,
    }


def calculate_progress(goals=None, roles=None):
    """Calculate progress for all goals based on task completion + activity momentum."""
    goals = goals or open_goals()
    tasks = goals.list('goal-tasks')
    counts = {}
    for gt in tasks:
        c = counts.setdefault(gt['goal_id'], [0, 0, 0])
        c[0] += 1
        if gt['status'] == 'done':
            c[1] += 1
        elif gt['status'] == 'in_progress':
            c[2] += 1
    active = sorted(goals.list('goals', status='active'), key=lambda g: (g['domain'] or '', g['title']))
    goals_rows = [
        {**g, 'total_tasks': counts.get(g['id'], [0, 0, 0])[0],
         'done_tasks': counts.get(g['id'], [0, 0, 0])[1],
         'in_progress_tasks': counts.get(g['id'], [0, 0, 0])[2]}
        for g in active
    ]

    momentum = get_activity_momentum(goals=goals, roles=roles)

    results = []
    for g in goals_rows:
        total = g['total_tasks']
        done = g['done_tasks']
        in_progress = g['in_progress_tasks'] or 0
        current = g['progress_percent'] or 0
        domain = g['domain']

        # Task-based: base 10% + 80% proportional to task completion
        if total > 0:
            task_based = round(10 + (done / total) * 80)
        else:
            task_based = 10

        # Activity momentum: bonus 0-10% based on recent activity in domain
        task_score = min(3, momentum['tasks'].get(domain, 0) * 3)       # max 3% (completions)
        skill_score = min(2, momentum['skills'].get(domain, 0))          # max 2%
        momentum_bonus = min(10, task_score + skill_score)

        # Combined: task-based + momentum bonus
        calculated = min(100, task_based + momentum_bonus)

        results.append({
            'id': g['id'],
            'title': g['title'],
            'domain': domain,
            'current': current,
            'calculated': calculated,
            'task_based': task_based,
            'momentum_bonus': momentum_bonus,
            'total_tasks': total,
            'done_tasks': done,
            'in_progress_tasks': in_progress,
            'diff': calculated - current,
        })

    return results


def show_progress():
    """Show current vs calculated progress with momentum."""
    results = calculate_progress()

    print('=' * 80)
    print('  🎯 GOAL PROGRESS CALCULATOR (with Activity Momentum)')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 80)

    print(f'\n{"Goal":<35} {"Cur":>4} {"Calc":>5} {"Task":>5} {"Mom":>4} {"Tasks":>8}')
    print('-' * 80)

    needs_update = 0
    for r in results:
        task_str = f'{r["done_tasks"]}/{r["total_tasks"]}'
        marker = '⚠️' if r['diff'] != 0 else '✅'
        mom = f'+{r["momentum_bonus"]}' if r['momentum_bonus'] > 0 else '  '
        print(f'{marker} {r["title"][:33]:<33} {r["current"]:>3}% {r["calculated"]:>4}% {r["task_based"]:>4}% {mom:>4} {task_str:>8}')
        if r['diff'] != 0:
            needs_update += 1

    print('-' * 80)
    print(f'Total: {len(results)} goals | {needs_update} need update')

    if needs_update > 0:
        print(f'\n💡 Run with --update to sync progress_percent values')

    print('=' * 80)


def update_progress():
    """Update progress_percent in the goals cell."""
    goals = open_goals()
    results = calculate_progress(goals=goals)
    updated = 0

    for r in results:
        if r['diff'] != 0:
            goals.update('goals', r['id'], {'progress_percent': r['calculated']})
            updated += 1
            print(f'✅ {r["title"][:40]}: {r["current"]}% → {r["calculated"]}%')

    print(f'\n✅ Updated {updated} goals')


def json_output():
    """Output as JSON."""
    return {
        'timestamp': datetime.now().isoformat(),
        'momentum': get_activity_momentum(),
        'goals': calculate_progress(),
    }


def show_momentum():
    """Show activity momentum breakdown."""
    m = get_activity_momentum()

    print('=' * 60)
    print('  📈 ACTIVITY MOMENTUM (last 30 days)')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 60)

    print(f'\n✅ Tasks Updated: {sum(m["tasks"].values())}')
    for d, c in sorted(m['tasks'].items(), key=lambda x: x[1], reverse=True):
        print(f'   {d}: {c}')

    print(f'\n🎯 Skills Practicing: {sum(m["skills"].values())}')
    for d, c in sorted(m['skills'].items(), key=lambda x: x[1], reverse=True):
        print(f'   {d}: {c}')

    print('=' * 60)


def main():
    update = '--update' in sys.argv
    as_json = '--json' in sys.argv
    momentum_only = '--momentum' in sys.argv

    if momentum_only:
        show_momentum()
    elif as_json:
        print(json.dumps(json_output(), indent=2, default=str))
    elif update:
        update_progress()
        context_summary = seed_portfolio_contexts(dry_run=False)
        created_summary = ", ".join(
            f"{key}={value}"
            for key, value in context_summary.items()
            if key.endswith("_created") and value
        )
        if created_summary:
            print(f"✅ Portfolio context projection: {created_summary}")
        else:
            print("✅ Portfolio context projection: already current")
        # Keep the continuous Goal → Epic/Story → Project → Task → Subtask
        # hierarchy authoritative after the legacy momentum calculation.
        summary = rollup_portfolio_progress(dry_run=False)
        print(
            "\n✅ Continuous portfolio roll-up: "
            + ", ".join(f"{key}={value}" for key, value in summary.items())
        )
    else:
        show_progress()


if __name__ == '__main__':
    try:
        main()
    except (CellStoreError, ClientConfigError) as exc:
        print(f'❌ {exc}')
        sys.exit(1)

#!/usr/bin/env python3
"""
Habit Analytics — Streak tracking, completion rates, patterns, consistency scores.

Usage:
    python3 habit_analytics.py              # Full habit analytics report
    python3 habit_analytics.py --json       # JSON output
"""

import json
# Add utils directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from habits_store import open_habits_store

AUTOMATION = 'reporting-reader'
import sys
from datetime import datetime, timedelta
from pathlib import Path



def get_habit_overview():
    """Summary of all habits."""
    store = open_habits_store(AUTOMATION)
    habits = store.habits(status='active')
    today = datetime.now().date()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    month_start = today.replace(day=1).isoformat()
    d30 = (today - timedelta(days=30)).isoformat()

    days_by_habit = {}
    for log in store.habit_logs(completed=True):
        days_by_habit.setdefault(log['habit_id'], []).append(log['log_date'])

    results = []
    for h in habits:
        hid = h['id']
        days = days_by_habit.get(hid, [])
        total = len(days)
        this_week = sum(1 for d in days if d >= week_start)
        this_month = sum(1 for d in days if d >= month_start)
        last_30 = sum(1 for d in days if d >= d30)

        schedule_days = h.get('schedule_days') or []
        # The habits service owns the streak (as of the owner's today); no second formula here.
        actual_streak = int(h.get('current_streak') or 0)

        # Expected days in last 30 days based on schedule
        expected_30 = count_expected_days(today, 30, schedule_days)

        completion_rate_30 = round(last_30 / expected_30 * 100, 1) if expected_30 > 0 else 0

        results.append({
            'id': hid,
            'name': h['name'],
            'category': h['category'],
            'frequency': h['frequency'],
            'current_streak': actual_streak,
            'best_streak': max(h['best_streak'] or 0, actual_streak),
            'total_completions': total,
            'this_week': this_week,
            'this_month': this_month,
            'last_30_days': last_30,
            'expected_30': expected_30,
            'completion_rate_30': completion_rate_30,
            'last_completed': h['last_completed_at'],
        })

    return results


def calc_streak(habit_id):
    """Calculate current streak by walking backwards from today."""
    store = open_habits_store(AUTOMATION)
    habit = store.habit(habit_id)
    if not habit:
        return 0
    return int(habit.get('current_streak') or 0)  # owned by the habits service


def count_expected_days(today, days_back, schedule_days):
    """Count how many scheduled days exist in the last N days."""
    day_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
    scheduled = set(day_map.get(d.lower(), -1) for d in schedule_days)
    if not scheduled:
        return days_back

    count = 0
    for i in range(days_back):
        d = today - timedelta(days=i)
        if d.weekday() in scheduled:
            count += 1
    return count


def get_overall_score():
    """Calculate overall habit consistency score (0-100)."""
    habits = get_habit_overview()
    if not habits:
        return 0

    total_rate = sum(h['completion_rate_30'] for h in habits)
    avg_rate = total_rate / len(habits)

    # Bonus for streaks
    streak_bonus = min(10, sum(1 for h in habits if h['current_streak'] >= 7) * 2)

    return min(100, round(avg_rate + streak_bonus, 1))


def get_weekly_pattern():
    """Which days of the week have highest completion."""
    rows = open_habits_store(AUTOMATION).habit_logs(completed=True)

    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    day_counts = [0] * 7
    for r in rows:
        try:
            d = datetime.strptime(r['log_date'], '%Y-%m-%d').date()
            day_counts[d.weekday()] += 1
        except (ValueError, TypeError):
            pass

    total = sum(day_counts) or 1
    return [{'day': day_names[i], 'completions': day_counts[i], 'pct': round(day_counts[i] / total * 100, 1)}
            for i in range(7)]


def get_category_breakdown():
    """Habit performance by category."""
    habits = get_habit_overview()
    cats = {}
    for h in habits:
        cat = h['category']
        if cat not in cats:
            cats[cat] = {'habits': 0, 'avg_rate': 0, 'total_completions': 0, 'rates': []}
        cats[cat]['habits'] += 1
        cats[cat]['total_completions'] += h['total_completions']
        cats[cat]['rates'].append(h['completion_rate_30'])

    for cat in cats:
        cats[cat]['avg_rate'] = round(sum(cats[cat]['rates']) / len(cats[cat]['rates']), 1)

    return cats


def generate_report():
    """Generate text report."""
    habits = get_habit_overview()
    score = get_overall_score()
    pattern = get_weekly_pattern()
    cats = get_category_breakdown()

    lines = []
    lines.append('# 🔥 Habit Analytics')
    lines.append(f'\n*Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}*\n')

    # Overall score
    icon = '🟢' if score >= 70 else '🟡' if score >= 40 else '🔴'
    lines.append(f'## Overall Consistency: {icon} {score}/100\n')

    # Per-habit table
    lines.append('## Habit Breakdown\n')
    lines.append('| Habit | Category | Streak | Best | 30d Rate | Total |')
    lines.append('|-------|----------|--------|------|----------|-------|')
    for h in sorted(habits, key=lambda x: x['completion_rate_30'], reverse=True):
        streak_icon = '🔥' if h['current_streak'] >= 7 else ''
        lines.append(f'| {h["name"]} | {h["category"]} | {streak_icon}{h["current_streak"]} | '
                      f'{h["best_streak"]} | {h["completion_rate_30"]}% | {h["total_completions"]} |')

    # Weekly pattern
    lines.append('\n## Completion by Day of Week\n')
    lines.append('| Day | Completions | Share |')
    lines.append('|-----|------------|-------|')
    for p in pattern:
        bar = '█' * max(1, int(p['pct'] / 5))
        lines.append(f'| {p["day"]} | {p["completions"]} | {bar} {p["pct"]}% |')

    # Category breakdown
    lines.append('\n## By Category\n')
    lines.append('| Category | Habits | Avg 30d Rate | Total Completions |')
    lines.append('|----------|--------|-------------|-------------------|')
    for cat, data in sorted(cats.items()):
        lines.append(f'| {cat} | {data["habits"]} | {data["avg_rate"]}% | {data["total_completions"]} |')
    return '\n'.join(lines)


def main():
    args = sys.argv[1:]
    json_mode = '--json' in args

    if json_mode:
        result = {
            'timestamp': datetime.now().isoformat(),
            'overall_score': get_overall_score(),
            'habits': get_habit_overview(),
            'weekly_pattern': get_weekly_pattern(),
            'category_breakdown': get_category_breakdown(),
        }
        print(json.dumps(result, indent=2))
    else:
        print(generate_report())


if __name__ == '__main__':
    main()

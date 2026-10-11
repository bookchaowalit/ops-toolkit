#!/usr/bin/env python3
"""
Weekly Habit Auto-Granter
Fills missing habit_logs for the past 7 days based on each habit's schedule_days.
Only auto-grants habits that are scheduled for a given day and have no existing log.

Usage:
    python3 weekly_habit_autogranted.py              # Auto-grant past 7 days
    python3 weekly_habit_autogranted.py --days 14    # Auto-grant past 14 days
    python3 weekly_habit_autogranted.py --dry-run    # Preview without writing
"""

# Add utils directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from datetime import datetime, timedelta

from habits_store import HabitsStoreError, open_habits_store
from task_store import ClientConfigError

DAY_ABBR = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


def get_schedule_days(habit_row):
    """The habit's schedule_days list, default to all 7 days if not set."""
    raw = habit_row.get('schedule_days')
    if raw:
        return list(raw)
    return DAY_ABBR[:]


def main():
    try:
        _run()
    except (HabitsStoreError, ClientConfigError) as exc:
        print(f'❌ {exc}')
        sys.exit(1)


def _run():
    dry_run = '--dry-run' in sys.argv
    days_back = 7
    for i, arg in enumerate(sys.argv):
        if arg == '--days' and i + 1 < len(sys.argv):
            days_back = int(sys.argv[i + 1])

    store = open_habits_store('habit-autolog')

    # Get active habits
    habits = store.habits(status='active')

    if not habits:
        print('❌ No active habits found')
        sys.exit(1)

    print(f'📋 Weekly Habit Auto-Granter {"(DRY RUN)" if dry_run else ""}')
    print(f'   Period: past {days_back} days')
    print(f'   Active habits: {len(habits)}')
    print()

    today = datetime.now().date()
    total_granted = 0
    total_skipped = 0
    grant_log = []

    # One read of the window: (habit_id, day) pairs that already have a completed log
    window_start = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
    window_end = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    done = {(log['habit_id'], log['log_date'])
            for log in store.habit_logs(date_from=window_start, date_to=window_end, completed=True)}

    for day_offset in range(days_back, 0, -1):
        date = today - timedelta(days=day_offset)
        day_name = DAY_ABBR[date.weekday()]  # mon, tue, etc.
        date_str = date.strftime('%Y-%m-%d')

        day_granted = 0
        day_skipped = 0

        for habit in habits:
            schedule = get_schedule_days(habit)

            # Skip if not scheduled for this day
            if day_name not in schedule:
                continue

            # Check if already logged
            if (habit['id'], date_str) in done:
                day_skipped += 1
                total_skipped += 1
                continue

            # Auto-grant
            if not dry_run:
                store.log_habit(habit['id'], date_str, {
                    'completed': True, 'count': 1, 'notes': 'auto-granted (weekly batch)'})
                grant_log.append(f'   ✅ {habit["name"]} → {date_str} ({day_name})')
            else:
                grant_log.append(f'   🔍 {habit["name"]} → {date_str} ({day_name}) [dry-run]')

            day_granted += 1
            total_granted += 1

        status = 'GRANTED' if day_granted > 0 else 'all logged'
        print(f'   {date_str} ({day_name}): {day_granted} granted, {day_skipped} already logged — {status}')

    if not dry_run and total_granted > 0:
        # The habits service recomputes current_streak/best_streak on every log.
        print(f'\n🔄 Streaks recalculated by the habits service for {len(habits)} habits')

    print(f'\n📊 Result: {total_granted} granted, {total_skipped} already logged')
    if grant_log:
        print(f'\nDetails:')
        for entry in grant_log:
            print(entry)

    if dry_run:
        print(f'\n⚠️  DRY RUN — no changes written. Remove --dry-run to apply.')


if __name__ == '__main__':
    main()

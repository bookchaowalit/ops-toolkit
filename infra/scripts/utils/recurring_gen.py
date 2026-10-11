#!/usr/bin/env python3
"""
Recurring Task Generator — Create task instances from recurring task templates.

Checks tasks with recurrence_rule and generates new instances when needed.

Usage:
    python3 recurring_gen.py              # Dry-run (show what would be generated)
    python3 recurring_gen.py --apply      # Generate tasks in DB
"""

import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from recurring_step import next_due_dates, recurrence_step
from task_store import open_task_store

AUTOMATION_ID = 'recurring-gen'
INSTANCE_TITLE = re.compile(r'^\[R(\d+)\] (.*)$', re.S)


def parse_recurrence(rule):
    """Parse a recurrence rule string like 'daily', 'weekly', 'monthly', or 'every Nd'."""
    return recurrence_step(rule)


def parse_date(s):
    """Parse date string, handling various ISO formats."""
    if not s:
        return None
    s = s.replace('Z', '').split('T')[0]
    return date.fromisoformat(s)


def existing_instances(records, parent):
    """(occurrence, due_date, completed_at) rows of the parent's instances for the dry-run
    preview, matched by "[R<n>] <parent title>" among recurring instances (task records do
    not carry the parent's ID across backends; --apply lets the store decide exactly)."""
    rows = []
    for record in records:
        match = INSTANCE_TITLE.match(record['title'])
        if (record['is_recurring_instance'] and match and match.group(2) == parent['title']
                and record['id'] != parent['id']):
            rows.append((int(match.group(1)), record['due_date'], record['completed_at']))
    return sorted(rows, key=lambda row: row[0])


def generate_instances(apply=False):
    """Find recurring parent tasks and generate new instances."""
    store = open_task_store(None, AUTOMATION_ID)
    today = date.today()

    # Find parent recurring tasks (not instances themselves)
    records = store.list(include_archived=True)
    parents = [r for r in records
               if r['recurrence_rule'] and r['archived_at'] is None and r['status'] != 'cancelled'
               and not r['is_recurring_instance']]

    if not parents:
        print("  No recurring tasks found.")
        return 0

    generated = 0

    for parent in parents:
        rule = parent['recurrence_rule']

        if not parse_recurrence(rule):
            print(f"  ⚠️ [{parent['id']}] Unknown recurrence rule: '{rule}'")
            continue

        if apply:
            created = store.generate_occurrences(parent['id'], until=today.isoformat())
            for task_id in created:
                task = store.get(task_id)
                if task:
                    print(f"  📝 [{parent['id']}] {rule} → #{task_id}: due {task['due_date']} — {task['title'][:50]}")
            generated += len(created)
        else:
            for occ, due in next_due_dates(parent, existing_instances(records, parent), today):
                title = parent['title']
                if not title.startswith(f"[R{occ}]"):
                    title = f"[R{occ}] {parent['title']}"
                print(f"  📝 [{parent['id']}] {rule} → occ #{occ}: due {due} — {title[:50]}")
                generated += 1

    return generated


def main():
    apply = '--apply' in sys.argv

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"🔁 Recurring Task Generator — {mode}")
    print()

    count = generate_instances(apply)

    if count > 0:
        print(f"\n{'✅' if apply else 'ℹ️'} {count} task instance(s) {'generated' if apply else 'would be generated'}")
    else:
        print("\n✅ No new instances needed — all recurring tasks are up to date.")


if __name__ == '__main__':
    main()

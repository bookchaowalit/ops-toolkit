#!/usr/bin/env python3
"""
Notification Runner — Execute notification rules and queue notifications in the notifications cell.

Checks all 6 notification rule types:
  - overdue: Tasks past their due date
  - due_soon: Tasks due within N hours
  - high_priority: Active tasks with priority 1-2
  - stale: Active tasks with no updates for N days
  - blocked: Tasks in blocked status
  - reminder: Explicit task reminder_at reached

Usage:
    python3 notification_runner.py              # Run all rules
    python3 notification_runner.py --rule overdue  # Run specific rule
    python3 notification_runner.py --dry-run    # Show what would be notified
    python3 notification_runner.py --summary    # Show notification stats
"""

import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from task_store import ClientConfigError, open_task_store, utc_now
from notifications_store import NotificationsStoreError, dedupe_key, open_notifications_store


AUTOMATION_ID = 'reminder-worker'
NOTIFICATIONS_AUTOMATION = 'reminder-worker'
CLOSED_STATUSES = ('done', 'cancelled', 'archived')


def _store():
    return open_task_store(None, AUTOMATION_ID)


def _local_tasks(records):
    """Tasks that belong to this system (Leantime tasks stay out of local alerts)."""
    return [r for r in records if (r.get('external_source') or '') != 'leantime']


def _bangkok_today():
    return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime('%Y-%m-%d')


def check_overdue(config, dry_run=False):
    """Find tasks past their due date."""
    rows = sorted(
        (r for r in _local_tasks(_store().list(due_before=_bangkok_today()))
         if r['status'] not in CLOSED_STATUSES),
        key=lambda r: r['due_date'])

    notifications = []
    for r in rows:
        tid, title, status, priority, due_date, project = (
            r['id'], r['title'], r['status'], r['priority'], r['due_date'], r['project'])
        severity = 'critical' if priority <= 2 else 'warning'
        notifications.append({
            'task_id': tid,
            'title': f'Overdue: {title}',
            'message': f'Task "{title}" is overdue (due: {due_date})',
            'notification_type': 'overdue',
            'severity': severity,
            'task_title': title,
            'task_status': status,
            'task_priority': priority,
            'task_due_date': due_date,
            'task_project': project,
        })

    return notifications


def check_due_soon(config, dry_run=False):
    """Find tasks due within N hours."""
    hours = config.get('hours', 24)
    now = datetime.now(ZoneInfo('Asia/Bangkok'))
    cutoff = (now + timedelta(hours=hours)).strftime('%Y-%m-%d')

    today = _bangkok_today()
    after_cutoff = (datetime.strptime(cutoff, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    rows = sorted(
        (r for r in _local_tasks(_store().list(due_before=after_cutoff))
         if today <= r['due_date'] <= cutoff and r['status'] not in CLOSED_STATUSES),
        key=lambda r: r['due_date'])

    notifications = []
    for r in rows:
        tid, title, status, priority, due_date, project = (
            r['id'], r['title'], r['status'], r['priority'], r['due_date'], r['project'])
        notifications.append({
            'task_id': tid,
            'title': f'Due soon: {title}',
            'message': f'Task "{title}" is due within {hours}h (due: {due_date})',
            'notification_type': 'due_soon',
            'severity': 'warning',
            'task_title': title,
            'task_status': status,
            'task_priority': priority,
            'task_due_date': due_date,
            'task_project': project,
        })

    return notifications


def check_high_priority(config, dry_run=False):
    """Find active tasks with high priority (1-2)."""
    max_pri = config.get('max_priority', 2)

    horizon = (datetime.now(timezone.utc) + timedelta(hours=7, days=7)).strftime('%Y-%m-%d')
    rows = sorted(
        (r for r in _local_tasks(_store().list(status=['todo', 'in_progress']))
         if r['priority'] is not None and r['priority'] <= max_pri
         and (r['status'] == 'in_progress' or (r['due_date'] and r['due_date'] <= horizon))),
        key=lambda r: (r['priority'], r['due_date'] or ''))

    notifications = []
    for r in rows:
        tid, title, status, priority, due_date, project = (
            r['id'], r['title'], r['status'], r['priority'], r['due_date'], r['project'])
        notifications.append({
            'task_id': tid,
            'title': f'High priority: {title}',
            'message': f'Task "{title}" has priority {priority}',
            'notification_type': 'high_priority',
            'severity': 'info',
            'task_title': title,
            'task_status': status,
            'task_priority': priority,
            'task_due_date': due_date,
            'task_project': project,
        })

    return notifications


def check_stale(config, dry_run=False):
    """Find active tasks with no updates for N days."""
    days = config.get('days', 7)
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    stale = [
        r for r in _local_tasks(_store().list(status=['todo', 'in_progress']))
        if (r['status'] == 'in_progress' or r['due_date'])
        and (not r['updated_at'] or str(r['updated_at']) < cutoff)
    ]
    # ORDER BY updated_at ASC put NULLs first.
    stale.sort(key=lambda r: (r['updated_at'] is not None, str(r['updated_at'] or '')))
    rows = [(r['id'], r['title'], r['status'], r['priority'], r['due_date'], r['project'],
             None if r['updated_at'] is None else str(r['updated_at'])) for r in stale]

    notifications = []
    for row in rows:
        tid, title, status, priority, due_date, project, updated_at = row
        days_stale = 'unknown'
        if updated_at:
            try:
                dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00').split('+')[0])
                days_stale = (datetime.now() - dt).days
            except:
                pass

        notifications.append({
            'task_id': tid,
            'title': f'Stale: {title}',
            'message': f'Task "{title}" has no updates for {days_stale} days',
            'notification_type': 'stale',
            'severity': 'warning',
            'task_title': title,
            'task_status': status,
            'task_priority': priority,
            'task_due_date': due_date,
            'task_project': project,
        })

    return notifications


def check_blocked(config, dry_run=False):
    """Find tasks in blocked status."""
    rows = sorted(_local_tasks(_store().list(status=['blocked'])), key=lambda r: (r['priority'] is not None, r['priority'] or 0))

    notifications = []
    for r in rows:
        tid, title, status, priority, due_date, project = (
            r['id'], r['title'], r['status'], r['priority'], r['due_date'], r['project'])
        notifications.append({
            'task_id': tid,
            'title': f'Blocked: {title}',
            'message': f'Task "{title}" is blocked',
            'notification_type': 'blocked',
            'severity': 'critical',
            'task_title': title,
            'task_status': status,
            'task_priority': priority,
            'task_due_date': due_date,
            'task_project': project,
        })

    return notifications


def check_reminder(config, dry_run=False):
    """Observe explicit reminders locally; do not claim device delivery."""
    rows = [r for r in _local_tasks(_store().list(reminder_due_before=utc_now()))
            if r['status'] not in ('done', 'cancelled')]
    return [{
        'task_id': r['id'], 'title': f"เตือนงาน: {r['title']}",
        'message': f"ถึงเวลาเตือนงานที่ตั้งไว้: {r['reminder_at']}",
        'notification_type': 'reminder', 'severity': 'info',
        'task_title': r['title'], 'task_status': r['status'], 'task_priority': r['priority'],
        'task_due_date': r['due_date'], 'task_project': r['project'],
        'reminder_at': r['reminder_at'],
    } for r in rows]


RULE_HANDLERS = {
    'reminder': check_reminder,
    'overdue': check_overdue,
    'due_soon': check_due_soon,
    'high_priority': check_high_priority,
    'stale': check_stale,
    'blocked': check_blocked,
}


def _store_notifications():
    return open_notifications_store(NOTIFICATIONS_AUTOMATION)


def _iso_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')


def log_notifications(rule_id, notifications, dry_run=False, store=None):
    """Queue notifications in the notifications cell; returns how many were new."""
    if dry_run:
        return 0

    store = store or _store_notifications()
    today = _bangkok_today()
    count = 0
    for n in notifications:
        # Console records are local observations, not proof of device delivery.
        # One unchanged task/rule snapshot per Bangkok day prevents cron spam:
        # the dedupe key is that snapshot plus the Bangkok date.
        key = dedupe_key('notification-runner', rule_id, n['task_id'], n['notification_type'],
                         n['task_status'], n['task_due_date'], n['task_priority'], today)
        outcome, _entry = store.queue({
            'rule_id': rule_id, 'task_id': n['task_id'], 'title': n['title'], 'message': n['message'],
            'notification_type': n['notification_type'], 'severity': n['severity'], 'channel': 'console',
            'task_title': n['task_title'], 'task_status': n['task_status'], 'task_priority': n['task_priority'],
            'task_due_date': n['task_due_date'], 'task_project': n['task_project'], 'dedupe_key': key,
        })
        if outcome == 'created':
            count += 1

    return count


def mark_explicit_reminders_sent(notifications):
    """Consume explicit reminders after they are recorded in the local log."""
    store = _store()
    reminders = {n['task_id']: n.get('reminder_at') for n in notifications if n.get('task_id') is not None}
    for task_id in sorted(reminders):
        reminder_at = reminders[task_id]
        if reminder_at is None:
            task = store.get(task_id)
            reminder_at = task['reminder_at'] if task else None
        if reminder_at is None:
            continue
        # The reminder was delivered on the local console log only.
        store.record_reminder_delivered(task_id, reminder_at, 'console')
    return len(reminders)


def main():
    dry_run = '--dry-run' in sys.argv
    summary = '--summary' in sys.argv
    specific_rule = None
    if '--rule' in sys.argv:
        idx = sys.argv.index('--rule')
        if idx + 1 < len(sys.argv):
            specific_rule = sys.argv[idx + 1]

    try:
        return _run(dry_run, summary, specific_rule)
    except (NotificationsStoreError, ClientConfigError) as exc:
        print(f"❌ Notifications service unavailable: {exc}")
        sys.exit(1)


def _run(dry_run, summary, specific_rule):
    store = _store_notifications()

    if summary:
        print("📊 Notification Summary")
        print("=" * 50)
        by_type = store.stats('notification_type')
        print(f"  Total notifications logged: {sum(by_type.values())}")
        print()
        print("  By type:")
        for name, count in by_type.items():
            print(f"    {name}: {count}")
        print()
        print("  By severity:")
        for name, count in store.stats('severity').items():
            print(f"    {name}: {count}")
        print()
        print("  Rules:")
        for r in store.rules():
            print(f"    [{r['id']}] {r['name']} ({r['rule_type']}) — last checked: {r.get('last_checked_at') or 'never'}")
        return

    print("=" * 60)
    print("NOTIFICATION RUNNER")
    print("=" * 60)
    print(f"  Mode: {'DRY-RUN' if dry_run else 'APPLY'}")
    print()

    rules = store.rules(active=True)

    if specific_rule:
        rules = [r for r in rules if r['rule_type'] == specific_rule]
        if not rules:
            print(f"  ❌ Rule '{specific_rule}' not found")
            return

    total_new = 0

    for rule in rules:
        rule_id, name, rule_type = rule['id'], rule['name'], rule['rule_type']
        handler = RULE_HANDLERS.get(rule_type)
        if not handler:
            print(f"  ⚠️  Unknown rule type: {rule_type}")
            continue

        config = rule.get('config') or {}
        notifications = handler(config, dry_run)

        print(f"  [{rule_id}] {name} ({rule_type}): {len(notifications)} notifications")

        if notifications:
            logged = log_notifications(rule_id, notifications, dry_run, store=store)
            total_new += len(notifications) if dry_run else logged

            if rule_type == 'reminder' and not dry_run:
                mark_explicit_reminders_sent(notifications)

            # Update rule timestamps
            if not dry_run:
                now = _iso_now()
                store.update_rule(rule_id, {'last_checked_at': now, 'last_triggered_at': now})

        elif not dry_run:
            store.update_rule(rule_id, {'last_checked_at': _iso_now()})

    if not dry_run:
        print(f"\n✅ Logged {total_new} notifications")
    else:
        print(f"\nℹ️  Would log {total_new} notifications (dry-run)")



if __name__ == '__main__':
    main()

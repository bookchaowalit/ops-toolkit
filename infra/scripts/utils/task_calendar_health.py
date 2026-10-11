#!/usr/bin/env python3
"""Read-only task, appointment and delivery health; output aggregate metadata."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _env import PROJECT_ROOT
from notifications_store import NotificationsStoreError, open_notifications_store
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError, open_task_store

NOTIFICATIONS_AUTOMATION = 'reporting-reader'
STATUS_PATH = PROJECT_ROOT / 'data/generated/briefings/calendar-sync-status.json'


def calendar_cron_job():
    """The newest sync_calendar job from the automation cell; None when absent or unreadable."""
    try:
        jobs = open_cell_store('automation', NOTIFICATIONS_AUTOMATION).list('cron-jobs', job_type='sync_calendar')
    except (CellStoreError, ClientConfigError):
        return None
    if not jobs:
        return None
    job = max(jobs, key=lambda j: j['id'])
    return {'is_active': int(bool(job.get('is_active'))), 'last_run_at': job.get('last_run_at'),
            'last_status': job.get('last_status')}


def read_sync_status():
    try:
        value = json.loads(STATUS_PATH.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def notification_delivery_7d():
    """Seven-day delivery aggregates from the notifications cell; the string 'unavailable' when it cannot be read."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%dT00:00:00+00:00')
        entries = open_notifications_store(NOTIFICATIONS_AUTOMATION).notifications(since=since)
    except (NotificationsStoreError, ClientConfigError):
        return {'logged': 'unavailable', 'delivered': 'unavailable', 'failed': 'unavailable', 'console_only': 'unavailable'}
    delivered = sum(e.get('delivered_at') is not None for e in entries)
    undelivered = [e for e in entries if e.get('delivered_at') is None]
    return {
        'logged': len(entries),
        'delivered': delivered,
        'failed': sum(bool(e.get('delivery_error')) for e in undelivered),
        'console_only': sum(e.get('channel') == 'console' for e in undelivered),
    }


def collect_health():
    store = open_task_store(None, 'reporting-reader')
    all_tasks = store.list(include_archived=True)
    by_id = {t['id']: t for t in all_tasks}
    candidates = [t for t in all_tasks if t['archived_at'] is None
                  and t['external_source'] != 'leantime']
    active_tasks = [t for t in candidates if t['status'] not in ('done', 'cancelled')]
    overdue_before = (datetime.now(timezone.utc) + timedelta(hours=7)).date().isoformat()
    tasks = {
        'active': len(active_tasks),
        'in_progress': sum(t['status'] == 'in_progress' for t in active_tasks),
        'overdue': sum(t['due_date'] is not None and t['due_date'] < overdue_before for t in active_tasks),
        'dated': sum(bool(t['due_date']) for t in active_tasks),
        'manual_reminders': sum(t['reminder_at'] is not None for t in active_tasks),
        'calendar_linked': sum(t['calendar_event_id'] is not None for t in active_tasks),
    }
    calendar = {}
    cron = calendar_cron_job()
    notifications = notification_delivery_7d()
    status = read_sync_status()
    last_success = status.get('last_success_at')
    calendar['sync'] = status
    calendar['cron'] = cron
    calendar['freshness'] = 'unverified'
    if last_success:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_success)).total_seconds()
            calendar['freshness'] = 'fresh' if 0 <= age < 86400 else 'stale'
        except (ValueError, TypeError):
            pass
    if status.get('status') in ('error', 'check_error'):
        calendar['freshness'] = 'degraded' if last_success else 'unverified'
    # A recommendation queue: no due dates, priority, or task statuses are changed.
    blocking = {}
    for row in store.dependencies():
        dep = by_id.get(row['depends_on'])
        if dep is not None and dep['status'] is not None and dep['status'] not in ('done', 'cancelled'):
            blocking[row['task']] = True
    ready = []
    for t in candidates:
        if t['status'] not in ('todo', 'in_progress') or t['id'] in blocking:
            continue
        parent = by_id.get(t['parent_task_id'])
        if parent is not None and parent['status'] == 'blocked':
            continue
        ready.append(t)
    ready.sort(key=lambda t: (
        t['priority'] is not None, t['priority'] or 0,
        0 if t['status'] == 'in_progress' else 1,
        t['due_date'] or '9999-12-31', t['id'],
    ))
    focus = [t['id'] for t in ready[:5]]
    return {'tasks': tasks, 'calendar': calendar, 'notifications_7d': notifications,
            'suggested_focus_task_ids': focus, 'wip_limit': 5,
            'wip_exceeded': tasks['in_progress'] > 5}


def main():
    print(json.dumps(collect_health(), ensure_ascii=False))


if __name__ == '__main__':
    main()

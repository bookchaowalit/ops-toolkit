#!/usr/bin/env python3
"""Project local task deadlines to Calendar. Preview by default; --apply writes.

Timed appointments belong to Calendar. Remote edits are conflicts for review,
never implicit changes to the canonical task database.
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'utils'))
from sync_google_calendar import get_access_token, _api_request
from task_store import open_task_store

CALENDAR_ID = 'primary'
AUTOMATION_ID = 'calendar-sync'


def open_store(read_only=False):
    """(None, store); tasks live in the Tasks API (the first item is kept for caller compatibility)."""
    return None, open_task_store(None, AUTOMATION_ID)


def event_url(event_id=None):
    base = 'https://www.googleapis.com/calendar/v3/calendars/'
    url = base + urllib.parse.quote(CALENDAR_ID, safe='') + '/events'
    return url + '/' + urllib.parse.quote(event_id, safe='') if event_id else url


def get_tasks_to_sync():
    conn, store = open_store(read_only=True)
    try:
        tasks = [
            task for task in store.list(include_archived=True)
            if (task['external_source'] or '') != 'leantime'
            and (task['calendar_event_id'] is not None
                 or (not task['archived_at'] and task['status'] not in ('done', 'cancelled')
                     and bool(task['due_date'])))
        ]
        tasks.sort(key=lambda task: (-1 if task['priority'] is None else task['priority'],
                                     task['due_date'] or '', task['id']))
        return tasks
    finally:
        if conn:
            conn.close()


def is_active(task):
    return (not task['archived_at'] and task['status'] not in ('done', 'cancelled')
            and bool(task['due_date']))


def build_event(task):
    due = date.fromisoformat(task['due_date'][:10])
    details = [
        f"Solo Empire task #{task['id']}",
        f"Status: {task['status']}",
        f"Priority: P{task['priority'] or 3}",
    ]
    if task.get('project'):
        details.append(f"Project: {task['project']}")
    return {
        'summary': f"กำหนดส่ง: {task['title']}",
        'description': "\n".join(details) + "\nDeadline projection only",
        'start': {'date': due.isoformat()},
        'end': {'date': (due + timedelta(days=1)).isoformat()},
        'transparency': 'transparent',
        'reminders': {'useDefault': False, 'overrides': []},
        'extendedProperties': {'private': {
            'solo_task_id': str(task['id']), 'solo_due_date': due.isoformat(),
        }},
    }


def owns_event(event, task):
    return event.get('extendedProperties', {}).get('private', {}).get('solo_task_id') == str(task['id'])


def can_update(event, task):
    baseline = event.get('extendedProperties', {}).get('private', {}).get('solo_due_date')
    return (owns_event(event, task) and baseline is not None
            and event.get('status') != 'cancelled'
            and event.get('start', {}).get('date') == baseline
            and 'dateTime' not in event.get('start', {}))


def mutate(token, method, payload=None, event_id=None, etag=None):
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    if etag:
        headers['If-Match'] = etag
    req = urllib.request.Request(event_url(event_id),
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else {}


def save_event_id(task_id, event_id):
    conn, store = open_store()
    try:
        # Linking a calendar event is not owner activity: keep updated_at so
        # the stale-task rule still sees the task as untouched.
        current = store.get(task_id)
        legacy = {'updated_at': current['updated_at']} if current and current.get('updated_at') else None
        if conn:
            with conn:
                store.update(task_id, {'calendar_event_id': event_id}, legacy=legacy)
        else:
            store.update(task_id, {'calendar_event_id': event_id}, legacy=legacy)
    finally:
        if conn:
            conn.close()


def sync_task(token, task):
    event_id = task['calendar_event_id']
    if event_id:
        try:
            event = _api_request(event_url(event_id), headers={'Authorization': f'Bearer {token}'})
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 410):
                raise
            # Retain active links: deleting a remote event is a review decision.
            if is_active(task):
                return 'conflicts'
            save_event_id(task['id'], None)
            return 'deleted'
        if not owns_event(event, task) or not event.get('etag'):
            return 'conflicts'
        if event.get('status') == 'cancelled' and not is_active(task):
            save_event_id(task['id'], None)
            return 'deleted'
        if not can_update(event, task):
            return 'conflicts'
        if not is_active(task):
            try:
                mutate(token, 'DELETE', event_id=event_id, etag=event['etag'])
            except urllib.error.HTTPError as exc:
                if exc.code not in (404, 410):
                    raise
            save_event_id(task['id'], None)
            return 'deleted'
        mutate(token, 'PATCH', build_event(task), event_id, event['etag'])
        return 'updated'

    payload = build_event(task)
    # Stable API id makes a retry after remote success/local failure recoverable.
    payload['id'] = hashlib.sha256(f'solo-empire-task:{task["id"]}'.encode()).hexdigest()
    try:
        event = mutate(token, 'POST', payload)
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        event = _api_request(event_url(payload['id']), headers={'Authorization': f'Bearer {token}'})
        if not can_update(event, task):
            return 'conflicts'
    save_event_id(task['id'], event['id'])
    return 'created'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--task-id', type=int, action='append', help='Limit to reviewed task IDs')
    args = parser.parse_args()
    result = dict(created=0, updated=0, deleted=0, conflicts=0, errors=0)
    try:
        tasks = get_tasks_to_sync()
        if args.task_id:
            tasks = [task for task in tasks if task['id'] in args.task_id]
        result['candidates'] = len(tasks)
        if args.apply and tasks:
            token = get_access_token()
            for task in tasks:
                try:
                    result[sync_task(token, task)] += 1
                except Exception:
                    # Never log titles, API bodies, credentials or private URLs.
                    result['errors'] += 1
        result['status'] = ('error' if result['errors'] or result['conflicts']
                            else 'success' if args.apply else 'preview')
    except Exception as exc:
        result.update(status='error', errors=1, error_type=type(exc).__name__)
    print(json.dumps(result))
    return 1 if result['status'] == 'error' else 0


if __name__ == '__main__':
    sys.exit(main())

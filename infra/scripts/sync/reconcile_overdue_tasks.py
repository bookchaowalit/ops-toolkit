#!/usr/bin/env python3
"""
Bidirectional Todoist ↔ Local DB sync + overdue reconciliation.

Features:
  1. Reschedule overdue tasks to today (Todoist API + local DB)
  2. Pull completed status: tasks completed in Todoist → mark done locally
  3. Push new tasks: tasks created in Todoist directly → import to local DB
  4. Auto-archive: tasks completed > 7 days → archive (keep data, clean active view;
     tasks are never deleted)

Usage:
  python3 reconcile_overdue_tasks.py                          # dry-run (show what would happen)
  python3 reconcile_overdue_tasks.py --execute                 # apply all changes
  python3 reconcile_overdue_tasks.py --execute --todoist-only  # only Todoist sync
  python3 reconcile_overdue_tasks.py --execute --local-only    # only local tasks
  python3 reconcile_overdue_tasks.py --execute --archive-only  # only archive old completed (never deletes)
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add utils to path before local imports (the working directory may be the repo root)
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from task_store import open_task_store, utc_now

# Load .env
def load_env():
    env_path = Path(__file__).resolve().parents[3] / 'infra' / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

TOKEN = os.getenv('TODOIST_API_TOKEN')
TODOIST_BASE = 'https://api.todoist.com/api/v1'
TODAY = datetime.now().strftime('%Y-%m-%d')
ARCHIVE_AFTER_DAYS = 7

HEADERS = {
    'Authorization': f'Bearer {TOKEN}',
    'Content-Type': 'application/json',
}

REQUEST_DELAY = 0.5  # seconds between API calls
AUTOMATION_ID = 'overdue-reconciler'


def _store(db):
    return open_task_store(db, AUTOMATION_ID)


def _api_request(req, max_retries=3):
    for attempt in range(max_retries):
        try:
            return urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            if e.code in (429, 403, 500, 502, 503) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"   ⏳ Retry {attempt+1}/{max_retries} (HTTP {e.code}) in {wait}s...")
                time.sleep(wait)
                continue
            raise


def todoist_get(path):
    req = urllib.request.Request(f'{TODOIST_BASE}{path}', headers=HEADERS)
    resp = _api_request(req)
    data = json.loads(resp.read())
    return data


def todoist_update(task_id, body):
    req = urllib.request.Request(
        f'{TODOIST_BASE}/tasks/{task_id}',
        data=json.dumps(body).encode(),
        headers=HEADERS,
        method='POST',
    )
    resp = _api_request(req)
    return json.loads(resp.read())


def fetch_all_todoist_tasks():
    """Paginate through all active tasks in Todoist. Returns list of task dicts."""
    all_tasks = []
    cursor = None
    for _ in range(20):  # max 20 pages = 4000 tasks
        url = f'{TODOIST_BASE}/tasks?limit=200'
        if cursor:
            url += f'&cursor={cursor}'
        req = urllib.request.Request(url, headers=HEADERS)
        resp = _api_request(req)
        data = json.loads(resp.read())

        tasks = data if isinstance(data, list) else data.get('results', [])
        all_tasks.extend(tasks)

        if isinstance(data, dict) and data.get('next_cursor'):
            cursor = data['next_cursor']
        else:
            break
        time.sleep(REQUEST_DELAY)
    return all_tasks


# ─── Phase 1: Reschedule overdue tasks ───────────────────────────────────────

def reconcile_overdue(db, execute, todoist_only, local_only):
    """Reschedule overdue tasks to today."""
    store = _store(db)
    today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    overdue = sorted(
        (r for r in store.list(due_before=today_utc, include_archived=True)
         if r['status'] not in ('done', 'cancelled')),
        key=lambda r: (r['due_date'], r['external_source'] or ''))

    todoist_tasks = [r for r in overdue if r['external_source'] == 'todoist']
    local_tasks = [r for r in overdue if r['external_source'] is None]

    total_updated = 0
    total_errors = 0

    if not local_only:
        print(f"📋 OVERDUE TODOIST: {len(todoist_tasks)} tasks")
        updated = 0
        errors = 0
        for t in todoist_tasks:
            new_due = TODAY
            if execute:
                try:
                    todoist_update(t['external_id'], {'due_string': new_due})
                    store.update(t['id'], {'due_date': f"{new_due}T00:00:00Z"},
                                 legacy={'sync_status': 'synced'})
                    updated += 1
                    print(f"   ✅ [{t['project'] or '?'}] {t['title'][:55]}  ({t['due_date'][:10]} → {new_due})")
                except urllib.error.HTTPError as e:
                    errors += 1
                    if e.code == 404:
                        store.update(t['id'], {'status': 'cancelled'})
                        print(f"   🗑️  [{t['project'] or '?'}] {t['title'][:55]}  (404 → cancelled)")
                    else:
                        print(f"   ❌ [{t['project'] or '?'}] {t['title'][:55]}  (HTTP {e.code})")
                except Exception as e:
                    errors += 1
                    print(f"   ❌ [{t['project'] or '?'}] {t['title'][:55]}  ({e})")
                time.sleep(REQUEST_DELAY)
            else:
                print(f"   → [{t['project'] or '?'}] {t['title'][:55]}  ({t['due_date'][:10]} → {new_due})")
                updated += 1

        print(f"   → {updated} {'updated' if execute else 'to update'}, {errors} errors")
        total_updated += updated
        total_errors += errors

    if not todoist_only:
        print(f"\n📋 OVERDUE LOCAL: {len(local_tasks)} tasks")
        updated_local = 0
        for t in local_tasks:
            new_due = TODAY
            if execute:
                # the store reports due_date as YYYY-MM-DD, so local tasks get a plain date
                store.update(t['id'], {'due_date': new_due})
                updated_local += 1
                print(f"   ✅ [{t['project'] or '?'}] {t['title'][:55]}  ({(t['due_date'] or '?')[:10]} → {new_due})")
            else:
                print(f"   → [{t['project'] or '?'}] {t['title'][:55]}  ({(t['due_date'] or '?')[:10]} → {new_due})")
                updated_local += 1

        print(f"   → {updated_local} {'updated' if execute else 'to update'}")
        total_updated += updated_local

    return total_updated, total_errors


# ─── Phase 2: Pull completed from Todoist → mark done locally ────────────────

def pull_completed(db, execute):
    """Detect tasks completed in Todoist and mark done locally."""
    print(f"\n🔄 PULL COMPLETED: Comparing with Todoist...")

    # Fetch all active tasks from Todoist
    active_todoist = fetch_all_todoist_tasks()
    active_ids = {t['id'] for t in active_todoist}
    active_map = {t['id']: t for t in active_todoist}
    print(f"   Todoist active tasks: {len(active_ids)}")

    # Get all Todoist tasks from local DB that are NOT done/cancelled
    store = _store(db)
    local_active = [r for r in store.list(external_source='todoist', include_archived=True)
                    if r['status'] not in ('done', 'cancelled')]
    print(f"   Local active Todoist tasks: {len(local_active)}")

    # Tasks in local DB but NOT active in Todoist → completed (or deleted)
    completed = [r for r in local_active if r['external_id'] not in active_ids]
    print(f"   Completed in Todoist (not active): {len(completed)}")

    marked = 0
    for t in completed:
        if execute:
            store.complete(t['id'], legacy={'sync_status': 'synced', 'completed_at': utc_now()})
            marked += 1
            print(f"   ✅ [{t['project'] or '?'}] {t['title'][:55]}  → done")
        else:
            print(f"   → [{t['project'] or '?'}] {t['title'][:55]}  → done")
            marked += 1

    print(f"   → {marked} {'marked done' if execute else 'to mark done'}")
    return marked


# ─── Phase 3: Push new Todoist tasks → local DB ─────────────────────────────

def push_new_tasks(db, execute):
    """Import tasks created in Todoist directly that don't exist locally."""
    print(f"\n📥 IMPORT NEW: Checking for tasks only in Todoist...")

    active_todoist = fetch_all_todoist_tasks()
    active_ids = {t['id'] for t in active_todoist}

    # Get external_ids already in local DB
    store = _store(db)
    local_ids = {r['external_id'] for r in store.list(external_source='todoist', include_archived=True)}

    new_tasks = [t for t in active_todoist if t['id'] not in local_ids]
    print(f"   New tasks in Todoist (not in local DB): {len(new_tasks)}")

    imported = 0
    for t in new_tasks:
        due = None
        if t.get('due') and t['due'].get('date'):
            due = t['due']['date']

        # Todoist priority: 1=none, 2=low, 3=medium, 4=urgent → DB: 1-5
        priority = t.get('priority', 1)  # already 1-4 from Todoist

        labels = ','.join(t.get('labels', []))

        if execute:
            store.upsert_external('todoist', t['id'], {
                'title': t.get('content', ''),
                'description': t.get('description', ''),
                'priority': priority,
                'tags': labels,
                'due_date': due,
            }, legacy={'sync_status': 'synced'})
            imported += 1
            print(f"   ✅ {t['content'][:55]}  (due: {due or 'none'})")
        else:
            print(f"   → {t['content'][:55]}  (due: {due or 'none'})")
            imported += 1

    print(f"   → {imported} {'imported' if execute else 'to import'}")
    return imported


# ─── Phase 4: Auto-archive completed tasks (> 7 days) ────────────────────────

def auto_cleanup(db, execute):
    """Archive tasks completed more than ARCHIVE_AFTER_DAYS ago. Never deletes."""
    cutoff = (datetime.now() - timedelta(days=ARCHIVE_AFTER_DAYS)).strftime('%Y-%m-%d')
    print(f"\n📦 AUTO-ARCHIVE: Archiving tasks completed before {cutoff} ({ARCHIVE_AFTER_DAYS}+ days ago)")

    store = _store(db)
    candidates = sorted(
        (r for r in store.list(status=['done']) if r['completed_at'] and str(r['completed_at']) < cutoff),
        key=lambda r: str(r['completed_at']))

    print(f"   Tasks to archive: {len(candidates)}")

    reason = f"completed more than {ARCHIVE_AFTER_DAYS} days ago"
    archived = 0
    for t in candidates:
        if execute:
            store.archive(t['id'], reason)
            archived += 1
            print(f"   📦 [{t['project'] or '?'}] {t['title'][:55]}  (done {str(t['completed_at'])[:10]})")
        else:
            print(f"   → [{t['project'] or '?'}] {t['title'][:55]}  (done {str(t['completed_at'])[:10]})")
            archived += 1

    print(f"   → {archived} {'archived' if execute else 'to archive'}")
    return archived


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    execute = '--execute' in args
    todoist_only = '--todoist-only' in args
    local_only = '--local-only' in args
    archive_only = '--archive-only' in args

    if execute and not TOKEN:
        print('❌ TODOIST_API_TOKEN not set')
        sys.exit(1)

    mode = "DRY RUN" if not execute else "EXECUTING"
    print(f"🔄 Task Sync & Reconciliation [{mode}]")
    print(f"   Today: {TODAY}")
    print()

    db = None  # tasks live in the Tasks API; the argument is kept for caller compatibility

    total_changes = {
        'overdue_updated': 0,
        'overdue_errors': 0,
        'completed_pulled': 0,
        'new_imported': 0,
        'archived': 0,
    }

    if archive_only:
        total_changes['archived'] = auto_cleanup(db, execute)
    else:
        # Phase 1: Reschedule overdue
        if not archive_only:
            updated, errors = reconcile_overdue(db, execute, todoist_only, local_only)
            total_changes['overdue_updated'] = updated
            total_changes['overdue_errors'] = errors

        # Phase 2-4: Only if not local-only (needs Todoist API)
        if not local_only and not archive_only:
            total_changes['completed_pulled'] = pull_completed(db, execute)
            total_changes['new_imported'] = push_new_tasks(db, execute)

        # Phase 4: Auto-archive old completed (always runs)
        total_changes['archived'] = auto_cleanup(db, execute)


    # Summary
    print(f"\n{'='*50}")
    print(f"📊 SUMMARY")
    print(f"{'='*50}")
    print(f"   Overdue rescheduled: {total_changes['overdue_updated']} ({total_changes['overdue_errors']} errors)")
    print(f"   Completed pulled:    {total_changes['completed_pulled']}")
    print(f"   New imported:        {total_changes['new_imported']}")
    print(f"   Archived (old done): {total_changes['archived']}")

    if execute:
        print(f"\n✅ Sync complete!")
    else:
        print(f"\n💡 Dry run. Use --execute to apply.")


if __name__ == '__main__':
    main()

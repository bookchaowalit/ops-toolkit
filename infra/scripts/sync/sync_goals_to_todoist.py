#!/usr/bin/env python3
"""
Sync goal_tasks from the goals cell → Todoist (5-project structure).

Mapping:
  💼 Income  → book-ai + book-marketing + book-work goals
  📖 Growth  → book-dev + book-learning goals
  🏋️ Body    → book-health goals
  🧠 Life    → habit-related goals
  🎨 Creative → book-creative goals
"""

import json
import os
# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from cell_store import open_cell_store
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

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
BASE_URL = 'https://api.todoist.com/api/v1'

# Project mapping: domain → Todoist project name
DOMAIN_TO_PROJECT = {
    'book-ai': '💼 Income',
    'book-marketing': '💼 Income',
    'book-work': '💼 Income',
    'book-dev': '📖 Growth',
    'book-learning': '📖 Growth',
    'book-health': '🏋️ Body',
    'book-creative': '🎨 Creative',
}

HEADERS = {
    'Authorization': f'Bearer {TOKEN}',
    'Content-Type': 'application/json',
}


# Delay between task creation requests to avoid rate limiting (seconds)
REQUEST_DELAY = 1.5


def _api_request(req, max_retries=3):
    """HTTP request with retry on 429/500/502/503. 403 is NOT retried."""
    for attempt in range(max_retries):
        try:
            return urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise  # Never retry 403 — could be auth error or project limit
            if e.code in (429, 500, 502, 503) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"   ⏳ Retry {attempt+1}/{max_retries} (HTTP {e.code}) in {wait}s...")
                time.sleep(wait)
                continue
            raise


def api_get(path):
    req = urllib.request.Request(f'{BASE_URL}{path}', headers=HEADERS)
    resp = _api_request(req)
    data = json.loads(resp.read())
    return data.get('results', data) if isinstance(data, dict) else data


def api_post(path, body):
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        data=json.dumps(body).encode(),
        headers=HEADERS,
        method='POST',
    )
    resp = _api_request(req)
    return json.loads(resp.read())


def api_delete(path):
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        headers=HEADERS,
        method='DELETE',
    )
    _api_request(req)


def get_projects():
    """Get all Todoist projects, return name→id map."""
    projects = api_get('/projects')
    return {p['name']: p['id'] for p in projects}


def get_existing_tasks(project_id):
    """Get all active tasks in a project."""
    tasks = api_get(f'/tasks?project_id={project_id}')
    return {t['content']: t['id'] for t in tasks}


def sync():
    args = sys.argv[1:]
    json_mode = "--json" in args

    # Parse --limit N (max tasks to create per run, default 30)
    limit = 30
    if '--limit' in args:
        idx = args.index('--limit')
        if idx + 1 < len(args):
            try:
                limit = int(args[idx + 1])
            except ValueError:
                pass

    if not TOKEN:
        print('❌ TODOIST_API_TOKEN not set')
        sys.exit(1)

    try:
        goals_store = open_cell_store('goals', 'portfolio-reader')

        # Get project map
        project_map = get_projects()
        print(f'📋 Todoist projects: {len(project_map)}')
        for name, pid in project_map.items():
            print(f'   {name} → {pid}')

        # Get all goal_tasks with goal domain (open tasks of active/planning goals)
        goals_by_id = {g['id']: g for g in goals_store.list('goals', status=['active', 'planning'])}
        open_tasks = [
            t for t in goals_store.list('goal-tasks', status=['todo', 'in_progress'])
            if t['goal_id'] in goals_by_id
        ]
        open_tasks.sort(key=lambda t: (goals_by_id[t['goal_id']]['domain'] or '', t['sort_order'] or 0, t['id']))
        rows = [
            (t['id'], t['goal_id'], t['title'], t['status'], t['priority'], t['sort_order'] or 0,
             goals_by_id[t['goal_id']]['domain'], goals_by_id[t['goal_id']]['title'])
            for t in open_tasks
        ]

        # Group by target project
        by_project = {}
        for gt_id, goal_id, title, status, priority, sort_order, domain, goal_title in rows:
            project_name = DOMAIN_TO_PROJECT.get(domain, '🧠 Life')
            if project_name not in by_project:
                by_project[project_name] = []
            by_project[project_name].append({
                'gt_id': gt_id,
                'goal_id': goal_id,
                'title': title,
                'priority': priority,
                'sort_order': sort_order,
                'domain': domain,
                'goal_title': goal_title,
            })

        total_created = 0
        total_skipped = 0

        for project_name, tasks in by_project.items():
            if project_name not in project_map:
                print(f'⚠️  Project "{project_name}" not found in Todoist, skipping {len(tasks)} tasks')
                continue

            pid = project_map[project_name]
            existing = get_existing_tasks(pid)
            print(f'\n📁 {project_name} ({len(tasks)} tasks to sync)')

            for task in tasks:
                content = task['title']
                if content in existing:
                    total_skipped += 1
                    continue

                if total_created >= limit:
                    print(f'   ⏸️  Limit reached ({limit}), stopping')
                    break

                # Map priority: 1 (high)→4 (urgent), 2 (medium)→3 (high), 3 (low)→2 (medium)
                prio_map = {1: 4, 2: 3, 3: 2}
                prio = prio_map.get(task['priority'], 3)

                # Due date: sort_order * 3 days from now
                due = (datetime.now() + timedelta(days=task['sort_order'] * 3)).strftime('%Y-%m-%d')

                body = {
                    'content': content,
                    'project_id': pid,
                    'priority': prio,
                    'due': {'date': due},
                    'description': f'Goal: {task["goal_title"]}\nDomain: {task["domain"]}',
                }

                try:
                    result = api_post('/tasks', body)
                    total_created += 1
                    print(f'   ✅ {content}')
                    time.sleep(REQUEST_DELAY)
                except urllib.error.HTTPError as e:
                    if e.code == 403:
                        err_body = e.read().decode() if hasattr(e, 'read') else ''
                        try:
                            err = json.loads(err_body)
                            if err.get('error_tag') == 'MAX_ITEMS_LIMIT_REACHED':
                                print(f'   ⚠️  Project full, skipping remaining tasks in this project')
                                break  # Skip to next project
                        except (json.JSONDecodeError, KeyError):
                            pass
                    print(f'   ❌ {content}: {e}')
                    if '429' in str(e):
                        print(f'   ⏳ Rate limited, backing off 10s...')
                        time.sleep(10)
                except Exception as e:
                    print(f'   ❌ {content}: {e}')

            if total_created >= limit:
                break

        remaining = sum(len(t) for t in by_project.values()) - total_created - total_skipped
        print(f'\n📊 Sync complete: {total_created} created, {total_skipped} already exist, {remaining} remaining')

        if json_mode:
            print(json.dumps({"status": "ok", "created": total_created, "skipped": total_skipped, "remaining": remaining}))
    except Exception as e:
        print(f'❌ Todoist sync failed: {e}')
        if json_mode:
            print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)


if __name__ == '__main__':
    sync()

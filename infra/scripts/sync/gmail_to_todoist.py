#!/usr/bin/env python3
"""
Gmail → Todoist Auto-Tasks
Creates Todoist tasks from starred Gmail emails (Job Apps, Work categories).

Runs every 4 hours to sync priority emails to Todoist for follow-up.
"""

import json
# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from cell_store import open_cell_store
import sys
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_google_creds, get_env
from _http import api_request, get_access_token as _get_access_token

TODOIST_TOKEN = get_env('TODOIST_API_TOKEN')
CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN = get_google_creds()

TODOIST_BASE = 'https://api.todoist.com/api/v1'


def get_google_token():
    """Get Google access token."""
    return _get_access_token(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)


def get_todoist_headers():
    """Get Todoist API headers."""
    return {
        "Authorization": f"Bearer {TODOIST_TOKEN}",
        "Content-Type": "application/json",
    }


def fetch_starred_emails(token, days=1):
    """Fetch starred emails from last N days."""
    after_date = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
    params = urllib.parse.urlencode({
        "q": f"is:starred after:{after_date}",
        "maxResults": 50,
    })
    url = f"https://www.googleapis.com/gmail/v1/users/me/messages?{params}"
    resp = api_request(url, headers={"Authorization": f"Bearer {token}"})
    message_ids = [m["id"] for m in resp.get("messages", [])]

    emails = []
    for msg_id in message_ids:
        # Use metadata format to get only headers (much faster than full)
        msg_url = (f"https://www.googleapis.com/gmail/v1/users/me/messages/{msg_id}"
                   f"?format=metadata&metadataHeaders=From&metadataHeaders=Subject"
                   f"&metadataHeaders=Date")
        try:
            msg = api_request(msg_url, headers={"Authorization": f"Bearer {token}"})
        except Exception:
            continue

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "").strip()
        # Skip emails with empty subjects — they can't become Todoist tasks
        if not subject:
            continue
        emails.append({
            "id": msg_id,
            "from": headers.get("From", ""),
            "subject": subject,
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
        })
    return emails


def get_todoist_projects(headers):
    """Get all Todoist projects."""
    url = f"{TODOIST_BASE}/projects"
    resp = api_request(url, headers=headers)
    # API v1 returns {"results": [...], "next_cursor": ...}
    items = resp.get("results", resp) if isinstance(resp, dict) else resp
    return {p["name"]: p["id"] for p in items}


def get_existing_tasks(headers, project_id):
    """Get existing task titles in a project."""
    url = f"{TODOIST_BASE}/tasks?project_id={project_id}"
    resp = api_request(url, headers=headers)
    # API v1 returns {"results": [...], "next_cursor": ...}
    items = resp.get("results", resp) if isinstance(resp, dict) else resp
    return {t["content"] for t in items}


def create_todoist_task(headers, title, project_id, description=None, priority=3):
    """Create a new Todoist task."""
    body = {
        "content": title,
        "project_id": project_id,
        "priority": priority,  # 1=normal, 2=medium, 3=high, 4=urgent
    }
    if description:
        body["description"] = description

    url = f"{TODOIST_BASE}/tasks"
    data = json.dumps(body).encode()
    resp = api_request(url, data=data, headers=headers, method="POST")
    return resp


def main():
    json_mode = "--json" in sys.argv

    if not TODOIST_TOKEN:
        if json_mode:
            print(json.dumps({"status": "error", "error": "TODOIST_API_TOKEN not set"}))
        else:
            print("❌ TODOIST_API_TOKEN not set")
        sys.exit(1)

    try:
        # Get tokens
        google_token = get_google_token()
        todoist_headers = get_todoist_headers()

        # Fetch starred emails from last 2 days
        emails = fetch_starred_emails(google_token, days=2)

        if not emails:
            result = {"status": "success", "created": 0, "message": "No starred emails found"}
            if json_mode:
                print(json.dumps(result))
            else:
                print("📧 No starred emails to sync to Todoist")
            return

        # Get Todoist projects
        projects = get_todoist_projects(todoist_headers)
        inbox_id = projects.get("Inbox")

        if not inbox_id:
            # Use first project as fallback
            inbox_id = list(projects.values())[0] if projects else None

        if not inbox_id:
            raise RuntimeError("No Todoist projects found")

        # Get existing tasks to avoid duplicates
        existing = get_existing_tasks(todoist_headers, inbox_id)

        # Create tasks from emails
        created = 0
        skipped = 0

        for email in emails:
            subject = email["subject"]
            from_addr = email["from"]
            snippet = email["snippet"]

            # Skip if task already exists
            if subject in existing:
                skipped += 1
                continue

            # Create task with email info
            description = f"From: {from_addr}\n\n{snippet}"
            priority = 3  # High priority for starred emails

            try:
                create_todoist_task(todoist_headers, subject, inbox_id, description, priority)
                created += 1
                if not json_mode:
                    print(f"   ✅ {subject[:50]}")
                time.sleep(0.5)  # Rate limit
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    if not json_mode:
                        print(f"   ⚠️  Skipped (bad request): {subject[:40]}")
                elif e.code == 403:
                    err_body = e.read().decode() if hasattr(e, 'read') else ''
                    try:
                        err = json.loads(err_body)
                        if err.get('error_tag') == 'MAX_ITEMS_LIMIT_REACHED':
                            if not json_mode:
                                print(f"   ⚠️  Inbox project full, stopping sync")
                            break
                    except (json.JSONDecodeError, KeyError):
                        pass
                    if not json_mode:
                        print(f"   ⚠️  Failed: {subject[:40]} — {e}")
                else:
                    if not json_mode:
                        print(f"   ⚠️  Failed: {subject[:40]} — {e}")
            except Exception as e:
                if not json_mode:
                    print(f"   ⚠️  Failed: {subject[:40]} — {e}")

        # Log to the automation cell
        try:
            desc = f"Gmail→Todoist: {created} tasks created, {skipped} skipped"
            open_cell_store("automation", "cron-runner").create("automation-log", {
                "action_type": "cron", "description": desc, "affected_count": created,
                "success": True, "executed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

        # Output result
        result = {
            "status": "success",
            "created": created,
            "skipped": skipped,
            "total_starred": len(emails),
        }

        if json_mode:
            print(json.dumps(result))
        else:
            print(f"\n📧 Gmail → Todoist Sync")
            print(f"   ✅ Created {created} tasks")
            print(f"   ⏭️  Skipped {skipped} (already exist)")
            print(f"   📊 Total starred: {len(emails)}")

    except Exception as e:
        if json_mode:
            print(json.dumps({"status": "error", "error": str(e)}))
        else:
            print(f"❌ Gmail→Todoist sync failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

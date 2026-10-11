#!/usr/bin/env python3
"""
Read-only Google Calendar snapshot probe (no local persistence; the calendar_events table is retired).
Uses Google API directly via urllib (no pip dependencies).

Usage:
    python3 sync_google_calendar.py              # Fetch the default window and record status
    python3 sync_google_calendar.py --json        # JSON output
    python3 sync_google_calendar.py --check --json # Probe access; metadata only
"""

import json
import os
import tempfile
import urllib.request
import urllib.error
import urllib.parse
import time
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# Add utils to path for _env import
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_google_creds
from task_calendar_health import STATUS_PATH, read_sync_status

MAX_RETRIES = 1
RETRY_DELAY = 2


def _api_request(url, data=None, headers=None, retries=MAX_RETRIES):
    """HTTP request with retry on transient failures."""
    last_err = None
    for attempt in range(1 + retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503):
                if attempt < retries:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
            raise  # non-transient
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            raise
    raise RuntimeError(last_err)


def get_access_token():
    """Get fresh access token using refresh token."""
    CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN = get_google_creds()
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    resp = _api_request("https://oauth2.googleapis.com/token", data=data)
    return resp["access_token"]


def calendar_window(days_ahead=7, days_back=1):
    now = datetime.now(ZoneInfo('Asia/Bangkok'))
    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
    time_min = time_min - timedelta(days=days_back)
    time_max = time_min + timedelta(days=days_ahead + days_back)
    return time_min, time_max


def fetch_calendar_events(token, days_ahead=7, days_back=1, window=None):
    """Fetch a complete bounded snapshot, including all pages and tombstones."""
    time_min, time_max = window or calendar_window(days_ahead, days_back)

    params = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "maxResults": 100,
        "singleEvents": True,
        "orderBy": "startTime",
        "showDeleted": "true",
        "timeZone": "Asia/Bangkok",
    }
    events = []
    seen = set()
    while True:
        query = urllib.parse.urlencode(params)
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{query}"
        resp = _api_request(url, headers={"Authorization": f"Bearer {token}"})
        events.extend(resp.get("items", []))
        page = resp.get('nextPageToken')
        if not page:
            return events
        if page in seen:
            raise RuntimeError('Repeated Calendar page token')
        seen.add(page)
        params['pageToken'] = page


def event_time(value):
    """Preserve an instant with its offset; floating times require a zone."""
    if 'date' in value:
        return value['date'] + 'T00:00:00'
    dt = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(value['timeZone']))
    return dt.isoformat(timespec='seconds')


def record_status(status, count=None, error_type=None):
    previous = read_sync_status()
    now = datetime.now(timezone.utc).isoformat()
    payload = {'last_attempt_at': now, 'status': status,
               'last_success_at': previous.get('last_success_at')}
    if status == 'success':
        payload.update(last_success_at=now, events_found=count)
    if error_type:
        payload['error_type'] = error_type
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=STATUS_PATH.parent, delete=False) as handle:
            temporary = handle.name
            json.dump(payload, handle)
        os.replace(temporary, STATUS_PATH)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def safe_error_type(exc):
    """Return a stable health category without exposing provider details."""
    name = type(exc).__name__
    message = str(exc).lower()
    if name == 'SecretUnavailable' or (name == 'ValueError' and 'required env var' in message):
        return 'credentials_not_configured'
    if name == 'SecretResolutionError':
        return 'secret_provider_unavailable'
    return name


def main():
    json_mode = "--json" in sys.argv
    try:
        token = get_access_token()
        window = calendar_window()
        events = fetch_calendar_events(token, window=window)
        if '--check' in sys.argv:
            record_status('read_access_ok', len(events))
            print(json.dumps({'status': 'read_access_ok', 'events_found': len(events)}))
            return
        record_status('success', len(events))
        if json_mode:
            print(json.dumps({"status": "success", "events_found": len(events)}))
        else:
            print("📅 Reading Google Calendar...")
            print(f"   Found {len(events)} events")
    except urllib.error.HTTPError as e:
        record_status('check_error' if '--check' in sys.argv else 'error', error_type=f'HTTP_{e.code}')
        if e.code == 403:
            msg = ("Calendar API returned 403 — verify Calendar access/scopes "
                   "and reconnect Google through the configured secret provider.")
            if json_mode:
                print(json.dumps({"status": "error", "error": msg}))
            else:
                print(f"❌ {msg}")
        else:
            if json_mode:
                print(json.dumps({"status": "error", "error": f"HTTP {e.code}"}))
            else:
                print(f"❌ Calendar sync failed: HTTP {e.code}")
        sys.exit(1)
    except Exception as e:
        record_status('check_error' if '--check' in sys.argv else 'error', error_type=safe_error_type(e))
        if json_mode:
            print(json.dumps({"status": "error", "error_type": safe_error_type(e)}))
        else:
            print(f"❌ Calendar sync failed: {safe_error_type(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()

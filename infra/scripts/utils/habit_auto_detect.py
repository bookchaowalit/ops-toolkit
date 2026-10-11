#!/usr/bin/env python3
"""
Habit Auto-Detection from Calendar
Checks Google Calendar for habit-related events and auto-logs them.
"""

# Add utils directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

from _env import get_google_creds
from habits_store import open_habits_store

CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN = get_google_creds()

# Map calendar event titles to habit SLUGS (not numeric IDs)
HABIT_MAPPING = {
    "deep work": "deep-work",           # Deep Work Block
    "daily planning": None,             # No matching habit — will be skipped
    "exercise": "morning-exercise",     # Morning Exercise
    "gym": "morning-exercise",          # Morning Exercise
    "running": "morning-exercise",      # Morning Exercise
    "journal": "journal-reflection",    # Journal / Reflection
    "reflection": "journal-reflection", # Journal / Reflection
    "reading": "read-30min",            # Read 30 Minutes
    "learn": "learn-tech",              # Learn New Tech
    "study": "learn-tech",              # Learn New Tech
    "content": "content-creation",      # Content Creation
    "write": "content-creation",        # Content Creation
    "linkedin": "content-creation",     # Content Creation
    "networking": "networking",         # Networking / Outreach
    "outreach": "networking",           # Networking / Outreach
}


def get_access_token():
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["access_token"]


def get_today_events(token):
    """Get today's calendar events."""
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)

    params = urllib.parse.urlencode({
        "timeMin": today_start.isoformat(),
        "timeMax": today_end.isoformat(),
        "maxResults": 50,
        "singleEvents": True,
        "orderBy": "startTime",
    })
    url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp.get("items", [])


def match_event_to_habit_slug(event_title):
    """Match calendar event title to habit SLUG."""
    title_lower = event_title.lower()
    for keyword, habit_slug in HABIT_MAPPING.items():
        if habit_slug is None:
            continue  # Skip unmapped keywords
        if keyword in title_lower:
            return habit_slug
    return None


def log_habit(habit_slug, date=None, store=None):
    """Log a habit completion using slug-based lookup (the service owns streaks)."""
    store = store or open_habits_store("habit-autolog")
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    habit = store.habit_by_slug(habit_slug)
    if habit is None:
        return False, "habit_not_found"

    # Already completed that day: leave it alone
    if store.habit_logs(habit_id=habit["id"], date_from=date, date_to=date, completed=True):
        return False, "already_logged"

    outcome, _log = store.log_habit(habit["id"], date, {"completed": True, "count": 1, "duration_minutes": 0})
    return outcome != "unchanged", "logged"


def main():
    print("🔍 Habit Auto-Detection from Calendar\n")

    token = get_access_token()
    events = get_today_events(token)

    print(f"Found {len(events)} events today\n")

    logged_count = 0
    for event in events:
        title = event.get("summary", "")
        habit_slug = match_event_to_habit_slug(title)

        if habit_slug:
            success, status = log_habit(habit_slug)
            status_icon = "✅" if success else "⏭️"
            print(f"{status_icon} {title} → {habit_slug} ({status})")
            if success:
                logged_count += 1

    print(f"\n✅ Auto-logged {logged_count} habits from calendar")


if __name__ == "__main__":
    main()

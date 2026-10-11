#!/usr/bin/env python3
"""
Strava Sync — Pull recent activities from Strava → habit_logs (habits cell).

Usage:
    python3 infra/scripts/sync/sync_strava.py              # Sync last 7 days
    python3 infra/scripts/sync/sync_strava.py --days 30    # Sync last 30 days
    python3 infra/scripts/sync/sync_strava.py --json        # JSON output
    python3 infra/scripts/sync/sync_strava.py --status     # Show sync status
"""

# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
import json
import urllib.request
import urllib.error
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_strava_token
from habits_store import HabitsStoreError, open_habits_store
from task_store import ClientConfigError

STRAVA_API = "https://www.strava.com/api/v3"
MAX_RETRIES = 1
RETRY_DELAY = 2


def api_get(path, token, params=None):
    """GET request to Strava API with retry."""
    url = f"{STRAVA_API}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    last_err = None
    for attempt in range(1 + MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            })
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            last_err = f"HTTP {e.code}: {body}"
            if e.code in (429, 500, 502, 503) and attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            raise
    raise RuntimeError(last_err)


def fetch_activities(token, days=7):
    """Fetch recent Strava activities."""
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    return api_get("/athlete/activities", token, {"after": after, "per_page": "30"})


def sync_habit_logs(store, activities):
    """Map Strava activities → habit logs for the Morning Exercise habit.

    The habits service owns streaks and recomputes them on every log.
    """
    logged = 0

    # Find the Morning Exercise habit
    habit = store.habit_by_slug("morning-exercise")
    if not habit:
        return 0
    habit_id = habit["id"]

    for act in activities:
        act_date = act.get("start_date_local", "")[:10]
        moving_time = act.get("moving_time", 0)
        distance_km = round(act.get("distance", 0) / 1000, 2)
        act_type = act.get("type", "Workout")

        # Skip a day that already has any log (a manual log wins over Strava)
        if store.habit_logs(habit_id=habit_id, date_from=act_date, date_to=act_date):
            continue

        duration_min = round(moving_time / 60)
        note = f"Strava: {act_type} — {distance_km}km, {duration_min}min"

        outcome, _log = store.log_habit(habit_id, act_date, {
            "completed": True, "duration_minutes": duration_min, "notes": note})
        if outcome == "created":
            logged += 1

    return logged


def show_status(store=None):
    """Show Strava sync status."""
    try:
        store = store or open_habits_store("strava-sync")
        habit_logs = len(store.habit_logs(notes_prefix="Strava:"))
    except (HabitsStoreError, ClientConfigError) as exc:
        habit_logs = f"unavailable ({exc})"

    print(f"\n🏃 Strava Sync Status")
    print(f"{'─' * 40}")
    print(f"  Habit logs (exercise): {habit_logs}")


def main():
    args = sys.argv[1:]
    json_mode = "--json" in args
    days = 7
    if "--days" in args:
        idx = args.index("--days")
        if idx + 1 < len(args):
            days = int(args[idx + 1])

    token = get_strava_token()
    if not token:
        msg = "STRAVA_ACCESS_TOKEN not set in .env"
        if json_mode:
            print(json.dumps({"status": "error", "error": msg}))
        else:
            print(f"⚠️  {msg}")
            print("   Get a token from https://www.strava.com/settings/api")
            print("   Add to .env: STRAVA_ACCESS_TOKEN=your_token")
            show_status()
        return

    if "--status" in args:
        show_status()
        return

    try:
        activities = fetch_activities(token, days)
        habits = sync_habit_logs(open_habits_store("strava-sync"), activities)

        if json_mode:
            print(json.dumps({
                "status": "success",
                "synced": habits,
                "activities_found": len(activities),
                "habit_logs": habits,
            }))
        else:
            print(f"🏃 Syncing Strava activities (last {days} days)...")
            print(f"   Found {len(activities)} activities")
            print(f"   ✅ {habits} habit logs synced")
            show_status()
    except Exception as e:
        if json_mode:
            print(json.dumps({"status": "error", "error": str(e)}))
        else:
            print(f"❌ Strava sync failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

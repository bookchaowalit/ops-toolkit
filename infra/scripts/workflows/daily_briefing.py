#!/usr/bin/env python3
"""
Daily Briefing — Pulls data from all sources for a morning overview.
Queries: Google Calendar, Gmail, Solo Empire DB, GitHub.
Outputs a formatted briefing to stdout.
"""

# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from task_store import ClientConfigError, open_task_store
import crm_ledger
from habits_store import open_habits_store
from cell_store import open_cell_store
import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add utils to path for _env import
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_google_creds

# Briefing output directory
BRIEFING_DIR = Path(__file__).parent.parent.parent.parent / "data" / "generated" / "briefings"

CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN = get_google_creds()


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


def fetch_calendar_today(token):
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
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
        events = []
        for e in resp.get("items", []):
            start = e.get("start", {})
            time_str = start.get("dateTime", start.get("date", ""))
            if "T" in time_str:
                time_str = time_str[11:16]
            else:
                time_str = "All day"
            events.append({"time": time_str, "title": e.get("summary", "")})
        return events
    except Exception as ex:
        return [{"error": str(ex)}]


def fetch_unread_emails(token, max_results=5):
    """Get top unread emails."""
    after_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y/%m/%d")
    params = urllib.parse.urlencode({
        "q": f"is:unread after:{after_date}",
        "maxResults": max_results,
    })
    url = f"https://www.googleapis.com/gmail/v1/users/me/messages?{params}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
        msg_ids = [m["id"] for m in resp.get("messages", [])]

        emails = []
        for msg_id in msg_ids[:5]:
            msg_url = f"https://www.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full"
            msg_req = urllib.request.Request(msg_url, headers={"Authorization": f"Bearer {token}"})
            msg = json.loads(urllib.request.urlopen(msg_req).read())
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            emails.append({
                "from": headers.get("From", "")[:40],
                "subject": headers.get("Subject", "")[:60],
            })
        return emails
    except Exception as ex:
        return [{"error": str(ex)}]


def get_db_briefing():
    """Get briefing data from the Solo Empire cells."""
    data = {}

    # Active goals (with domain)
    goal_store = open_cell_store("goals", "reporting-reader")
    active_goals = sorted(
        goal_store.list("goals", status="active"),
        key=lambda g: (g.get("priority") is not None, g.get("priority") or 0, g["id"]),
    )
    data["goals"] = [(g["title"], g["status"], g["progress_percent"], g["domain"]) for g in active_goals[:5]]

    # Habit streaks (ORDER BY priority; NULL priority sorts first, as in SQLite)
    habit_store = open_habits_store("reporting-reader")
    active_habits = sorted(
        habit_store.habits(status="active"),
        key=lambda h: (h.get("priority") is not None, h.get("priority") or 0, h["id"]),
    )
    data["habits"] = [
        (h["name"], h["current_streak"], h["best_streak"], h["last_completed_at"]) for h in active_habits
    ]

    # Today's habit logs
    today = datetime.now().strftime("%Y-%m-%d")
    habit_names = {h["id"]: h["name"] for h in habit_store.habits()}
    data["today_habits"] = [
        (habit_names[log["habit_id"]], 1 if log["completed"] else 0)
        for log in habit_store.habit_logs(date_from=today, date_to=today)
        if log["habit_id"] in habit_names
    ]

    store = open_task_store(None, "reporting-reader")
    open_tasks = [
        t for t in store.list(include_archived=True) if t["status"] not in ("done", "cancelled")
    ]

    # Tasks due today (ORDER BY priority DESC, external_source: NULLs last / first as SQLite sorts them)
    due_today = [t for t in open_tasks if t["due_date"] == today]
    due_today.sort(key=lambda t: (t["external_source"] is not None, t["external_source"] or ""))
    due_today.sort(key=lambda t: (t["priority"] is None, -(t["priority"] or 0)))
    data["tasks_today"] = [
        (t["title"], t["external_source"], t["priority"], t["status"]) for t in due_today
    ]

    # Tasks overdue (past due, still open)
    overdue = [t for t in open_tasks if t["due_date"] is not None and t["due_date"] < today]
    overdue.sort(key=lambda t: t["due_date"])
    data["tasks_overdue"] = [
        (t["title"], t["external_source"], t["priority"], t["due_date"]) for t in overdue[:5]
    ]

    # Revenue, from the CRM ledger (income-pipeline); None when it is unreachable
    month_start = datetime.now().strftime("%Y-%m-01")
    year_start = datetime.now().strftime("%Y-01-01")
    try:
        rows, _totals = crm_ledger.open_crm_ledger().revenue_all()
    except (crm_ledger.LedgerError, ClientConfigError):
        rows = None
    if rows is None:
        data["revenue_month"] = data["revenue_ytd"] = data["revenue_received_all"] = None
        data["revenue_pending"] = data["revenue_total"] = None
    else:
        thb = [r for r in rows if (r.get("currency") or "THB").upper() == "THB"]
        amount = lambda r: float(r["amount"] or 0)  # noqa: E731
        data["revenue_month"] = sum(amount(r) for r in thb if r.get("received") and r["revenue_date"] >= month_start)
        data["revenue_ytd"] = sum(amount(r) for r in thb if r.get("received") and r["revenue_date"] >= year_start)
        data["revenue_pending"] = sum(amount(r) for r in thb if not r.get("received"))
        data["revenue_total"] = sum(amount(r) for r in thb)
        data["revenue_received_all"] = sum(amount(r) for r in thb if r.get("received"))

    # Goal tasks due this week (with goal context)
    week_end_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    goals_by_id = {g["id"]: g for g in goal_store.list("goals")}
    all_goal_tasks = goal_store.list("goal-tasks")
    due_tasks = sorted(
        (
            t for t in all_goal_tasks
            if t["status"] != "done" and t.get("due_date") and str(t["due_date"])[:10] <= week_end_date
            and t["goal_id"] in goals_by_id
        ),
        key=lambda t: (str(t["due_date"])[:10], t.get("priority") or 0),
    )
    data["goal_tasks_week"] = [
        (t["title"], t["status"], t["priority"], str(t["due_date"])[:10],
         goals_by_id[t["goal_id"]]["title"], goals_by_id[t["goal_id"]]["domain"])
        for t in due_tasks[:10]
    ]

    # Weekly habit completion (last 7 days)
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_logs = {}
    for log in habit_store.habit_logs(date_from=week_ago):
        week_logs[log["habit_id"]] = week_logs.get(log["habit_id"], 0) + 1
    data["habit_week"] = sorted(
        ((h["name"], week_logs.get(h["id"], 0)) for h in habit_store.habits(status="active")),
        key=lambda row: -row[1],
    )

    # Goal task completion stats
    task_counts: dict = {}
    for t in all_goal_tasks:
        task_counts[t["status"]] = task_counts.get(t["status"], 0) + 1
    data["task_stats"] = list(task_counts.items())

    return data


def format_briefing(calendar_events, emails, db_data):
    """Format the briefing as markdown."""
    now = datetime.now()
    lines = []
    lines.append(f"# ☀️ Daily Briefing — {now.strftime('%A, %B %d, %Y')}")
    lines.append("")

    # Calendar
    lines.append("## 📅 Today's Schedule")
    if calendar_events and not any("error" in e for e in calendar_events):
        if calendar_events:
            for e in calendar_events:
                lines.append(f"- **{e['time']}** — {e['title']}")
        else:
            lines.append("- No events today 🎉")
    else:
        lines.append("- _Calendar sync pending — run: python3 infra/scripts/sync/sync_google_calendar.py_")
    lines.append("")

    # Email
    lines.append("## 📧 Unread Emails")
    if emails and not any("error" in e for e in emails):
        if emails:
            for e in emails:
                lines.append(f"- **{e['from']}** — {e['subject']}")
        else:
            lines.append("- Inbox zero! 🎉")
    else:
        lines.append("- _Email check pending_")
    lines.append("")

    # Habits
    lines.append("## 💪 Habits")
    completed_today = {h[0] for h in db_data["today_habits"]} if db_data["today_habits"] else set()
    for h in db_data["habits"]:
        name, streak, best, last = h
        done = "✅" if name in completed_today else "⬜"
        lines.append(f"- {done} {name} — streak: {streak} (best: {best})")
    lines.append("")

    # Goals
    lines.append("## 🎯 Active Goals")
    for g in db_data["goals"]:
        title, status, progress, domain = g
        bar = "█" * (progress // 10) + "░" * (10 - progress // 10)
        domain_str = f" [{domain.replace('book-', '')}]" if domain else ""
        lines.append(f"- [{bar}] {title} ({progress}%){domain_str}")
    lines.append("")

    # Tasks due today
    source_icon = {"leantime": "🔷", "todoist": "🔴", "local": "✅"}
    lines.append("## 📋 Tasks Due Today")
    if db_data["tasks_today"]:
        for t in db_data["tasks_today"]:
            title, source, priority, status = t
            icon = source_icon.get(source, "📌")
            prio = f"P{priority}" if priority is not None else ""
            lines.append(f"- {icon} {title} {prio}")
    else:
        lines.append("- No tasks due today 🎉")
    lines.append("")

    # Overdue tasks
    if db_data["tasks_overdue"]:
        lines.append("## ⚠️ Overdue Tasks")
        for t in db_data["tasks_overdue"]:
            title, source, priority, due = t
            icon = source_icon.get(source, "📌")
            lines.append(f"- {icon} {title} (was due: {due})")
        lines.append("")

    # Revenue
    lines.append("## 💰 Revenue")
    if db_data["revenue_month"] is None:
        lines.append("- unavailable (CRM ledger unreachable)")
    else:
        lines.append(f"- This month: ฿{db_data['revenue_month']:,.0f}")
        lines.append(f"- YTD received: ฿{db_data.get('revenue_ytd', 0):,.0f}")
        lines.append(f"- All-time received: ฿{db_data.get('revenue_received_all', 0):,.0f}")
        lines.append(f"- Pending pipeline: ฿{db_data.get('revenue_pending', 0):,.0f}")
        lines.append(f"- Total contract: ฿{db_data.get('revenue_total', 0):,.0f}")
    lines.append("")

    # Goal tasks due this week
    if db_data.get("goal_tasks_week"):
        lines.append("## 📌 Goal Tasks Due This Week")
        for t in db_data["goal_tasks_week"]:
            title, status, prio, due, goal, domain = t
            domain_short = domain.replace("book-", "") if domain else ""
            lines.append(f"- [{status}] {title} (due: {due}) — {goal} [{domain_short}]")
        lines.append("")

    # Weekly habit stats
    if db_data.get("habit_week"):
        lines.append("## 📊 Habit Completion (7 days)")
        for h in db_data["habit_week"]:
            name, count = h
            bar = "█" * min(count, 7) + "░" * max(0, 7 - count)
            lines.append(f"- {name}: {bar} ({count}/7)")
        lines.append("")

    # Task completion stats
    if db_data.get("task_stats"):
        lines.append("## 📈 Goal Task Stats")
        total = sum(c for _, c in db_data["task_stats"])
        done = sum(c for s, c in db_data["task_stats"] if s == "done")
        lines.append(f"- Total: {total} | Done: {done} | Pending: {total - done}")
        if total > 0:
            pct = round(done / total * 100)
            lines.append(f"- Completion rate: {pct}%")
        lines.append("")

    return "\n".join(lines)


def main():
    print("Loading daily briefing...\n")

    # Get Google data
    try:
        token = get_access_token()
        calendar_events = fetch_calendar_today(token)
        emails = fetch_unread_emails(token)
    except Exception as e:
        calendar_events = [{"error": str(e)}]
        emails = [{"error": str(e)}]

    # Get DB data
    db_data = get_db_briefing()

    # Format and print
    briefing = format_briefing(calendar_events, emails, db_data)
    print(briefing)

    # Save to file
    try:
        BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        filepath = BRIEFING_DIR / f"briefing-{today}.md"
        filepath.write_text(briefing, encoding="utf-8")
        print(f"\n💾 Saved to {filepath}")
    except Exception as e:
        print(f"\n⚠️  Could not save briefing: {e}")


if __name__ == "__main__":
    main()

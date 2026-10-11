#!/usr/bin/env python3
"""
Simple Dashboard - Show all key metrics at a glance
Usage: python3 dashboard.py
"""

import csv
import os
from datetime import datetime, timedelta
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from workspace_paths import repo_path, workspace_data_path, workspace_path, workspace_tools_path
from job_client import fetch_jobs

# Canonical book-dev workspace for the nested scraping tools.
BOOK_DEV_CANONICAL = workspace_path("book-dev")
SCRAPING_TOOLS = BOOK_DEV_CANONICAL / "github" / "bookchaowalit" / "book-apps" / "tools"


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None

def read_csv(filepath):
    """Read CSV file and return list of dicts"""
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)

def get_streak(habit_name):
    """Get current streak for a habit"""
    logs = read_csv(workspace_data_path("book-life", "habits.csv"))
    habit_logs = [l for l in logs if l['habit'] == habit_name]
    habit_logs.sort(key=lambda x: x['date'], reverse=True)
    
    if not habit_logs:
        return 0
    
    streak = 0
    today = datetime.now().date()
    
    for log in habit_logs:
        log_date = datetime.strptime(log['date'], '%Y-%m-%d').date()
        if log['completed'] == 'yes':
            if log_date == today - timedelta(days=streak):
                streak += 1
            else:
                break
        else:
            break
    
    return streak

def dashboard():
    """Show dashboard"""
    print("\n" + "=" * 70)
    print("📊 SOLO EMPIRE DASHBOARD")
    print("=" * 70)
    print(f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()
    
    # Finance
    print("💰 FINANCE")
    print("-" * 70)
    transactions = read_csv(workspace_data_path("book-finance", "transactions.csv"))
    if transactions:
        this_month = [t for t in transactions if t['date'].startswith(datetime.now().strftime('%Y-%m'))]
        total_expenses = sum(float(t['amount']) for t in this_month if t['type'] == 'expense')
        total_income = sum(float(t['amount']) for t in this_month if t['type'] == 'income')
        print(f"  This Month: Income ฿{total_income:,.2f} | Expenses ฿{total_expenses:,.2f} | Net ฿{total_income - total_expenses:,.2f}")
        print(f"  Total Transactions: {len(transactions)}")
    else:
        print("  No transactions yet")
    print()
    
    # Health
    print("💪 HEALTH")
    print("-" * 70)
    workouts = read_csv(workspace_data_path("book-health", "workouts.csv"))
    nutrition = read_csv(workspace_data_path("book-health", "nutrition.csv"))
    this_week_workouts = [w for w in workouts if w['date'] >= (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')]
    print(f"  Workouts This Week: {len(this_week_workouts)}")
    if this_week_workouts:
        total_duration = sum(int(w['duration_minutes']) for w in this_week_workouts)
        total_calories = sum(int(w['calories_burned']) for w in this_week_workouts)
        print(f"  Total Duration: {total_duration} min | Calories Burned: {total_calories}")
    today_nutrition = [n for n in nutrition if n['date'] == datetime.now().strftime('%Y-%m-%d')]
    if today_nutrition:
        total_cal = sum(int(n['calories']) for n in today_nutrition)
        total_protein = sum(int(n['protein_g']) for n in today_nutrition)
        print(f"  Today's Nutrition: {total_cal} cal | {total_protein}g protein")
    print()
    
    # Life - Habits
    print("🌱 LIFE - HABITS")
    print("-" * 70)
    habits = read_csv(workspace_data_path("book-life", "habits.csv"))
    if habits:
        habit_names = sorted(set(h['habit'] for h in habits))
        today_habits = [h for h in habits if h['date'] == datetime.now().strftime('%Y-%m-%d')]
        completed_today = len([h for h in today_habits if h['completed'] == 'yes'])
        print(f"  Today: {completed_today}/{len(habit_names)} habits completed")
        print(f"  Habits: {', '.join(habit_names)}")
        for habit in habit_names[:5]:  # Show first 5
            streak = get_streak(habit)
            print(f"    • {habit}: {streak} day streak")
    else:
        print("  No habits tracked yet")
    print()
    
    # Life - Goals
    print("🎯 LIFE - GOALS")
    print("-" * 70)
    goals = read_csv(workspace_data_path("book-life", "goals.csv"))
    if goals:
        active_goals = [g for g in goals if g['status'] == 'active']
        print(f"  Active Goals: {len(active_goals)}")
        for g in active_goals[:5]:  # Show first 5
            print(f"    • {g['goal']} ({g['category']}) - Target: {g['target_date']}")
    else:
        print("  No goals set yet")
    print()
    
    # Marketing
    print("📱 MARKETING")
    print("-" * 70)
    content = read_csv(workspace_data_path("book-marketing", "content-calendar.csv"))
    if content:
        scheduled = [c for c in content if c['status'] == 'scheduled']
        published = [c for c in content if c['status'] == 'published']
        print(f"  Scheduled: {len(scheduled)} | Published: {len(published)}")
        upcoming = sorted(scheduled, key=lambda x: x['scheduled_date'])[:3]
        if upcoming:
            print(f"  Upcoming:")
            for c in upcoming:
                print(f"    • {c['scheduled_date']} - {c['title'][:50]}")
    else:
        print("  No content scheduled")
    print()
    
    # Sales
    print("🤝 SALES")
    print("-" * 70)
    job_result = fetch_jobs(dataset="job_leads", limit=500)
    leads = job_result.items if job_result.data_status in {"ok", "stale"} else []
    if leads:
        by_status = {}
        for l in leads:
            status = l['status']
            by_status[status] = by_status.get(status, 0) + 1
        print(f"  Total Leads: {len(leads)}")
        for status, count in sorted(by_status.items()):
            print(f"    {status}: {count}")
    else:
        print("  No leads yet")
    print()
    
    # Learning
    print("📚 LEARNING")
    print("-" * 70)
    courses = read_csv(workspace_data_path("book-learning", "courses.csv"))
    books = read_csv(workspace_data_path("book-learning", "books.csv"))
    if courses:
        active_courses = [c for c in courses if c['status'] == 'in-progress']
        print(f"  Active Courses: {len(active_courses)}")
        for c in active_courses[:3]:
            title = c.get('title') or c.get('name') or c.get('course_id', 'Untitled course')
            platform = c.get('platform', 'Unknown platform')
            progress = c.get('progress_pct', '?')
            print(f"    • {title} ({platform}) - {progress}%")
    if books:
        reading = [b for b in books if b['status'] == 'reading']
        print(f"  Currently Reading: {len(reading)}")
        for b in reading[:3]:
            title = b.get('title') or b.get('name') or b.get('book_id', 'Untitled book')
            author = b.get('author', 'Unknown author')
            print(f"    • {title} by {author}")
    if not courses and not books:
        print("  No learning tracked yet")
    print()
    
    # Creative
    print("🎨 CREATIVE")
    print("-" * 70)
    projects = read_csv(workspace_data_path("book-creative", "projects.csv"))
    if projects:
        active = [p for p in projects if p['status'] == 'in-progress']
        print(f"  Active Projects: {len(active)}")
        for p in active[:3]:
            print(f"    • {p['name']} ({p['type']})")
    else:
        print("  No creative projects yet")
    print()
    
    print("=" * 70)
    print("✅ Dashboard complete!")
    print()
    print("📝 Next Steps:")
    print(f"  • Run daily workflow: cd {repo_path('infra', 'scripts', 'workflows')} && python3 daily-workflow.py")
    print(f"  • Log habits: cd {workspace_tools_path('book-life')} && python3 habit-tracker.py --log --habit 'Habit' --completed")
    print(f"  • Check action plan: cat {repo_path('ACTION-PLAN.md')}")
    print("=" * 70 + "\n")

if __name__ == '__main__':
    dashboard()

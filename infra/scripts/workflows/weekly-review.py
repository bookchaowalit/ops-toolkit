#!/usr/bin/env python3
"""
Weekly Review - Generate weekly review across all working spaces
Usage: python3 weekly-review.py --week 2026-W23
"""

import argparse
import shlex
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from workspace_paths import repo_path, workspace_data_path, workspace_path, workspace_tools_path

def run_command(cmd, cwd=None):
    """Run a trusted workflow command without invoking a shell."""
    try:
        command = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        result = subprocess.run(
            command,
            shell=False,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def weekly_review(week=None):
    """Generate weekly review"""
    if not week:
        now = datetime.now()
        week = now.strftime('%Y-W%W')
    
    print(f"📊 Weekly Review for {week}")
    print("=" * 60)
    
    # Calculate date range for this week
    now = datetime.now()
    start_of_week = now - timedelta(days=now.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    
    print(f"📅 Period: {start_of_week.strftime('%Y-%m-%d')} to {end_of_week.strftime('%Y-%m-%d')}")
    print()
    
    # 1. Finance Summary
    print("\n💰 FINANCE SUMMARY")
    print("-" * 60)
    period = start_of_week.strftime('%Y-%m')
    success, output, _ = run_command(
        f"python3 financial-report.py --period {period}",
        cwd=workspace_tools_path("book-finance")
    )
    if success:
        print("✅ Financial report generated")
        report_path = workspace_path("book-finance") / "reports" / "monthly" / f"financial-report-{period}.md"
        print(f"📄 See: {report_path}")
    else:
        print("⚠️  No financial data for this period")
    
    # 2. Health Summary
    print("\n💪 HEALTH SUMMARY")
    print("-" * 60)
    print("Review your workouts and nutrition for this week:")
    print(f"📄 Workouts: {workspace_data_path('book-health', 'workouts.csv')}")
    print(f"📄 Nutrition: {workspace_data_path('book-health', 'nutrition.csv')}")
    print("\nQuestions to consider:")
    print("  - Did I complete my target workouts?")
    print("  - Did I hit my nutrition targets?")
    print("  - How's my energy level?")
    print("  - What adjustments needed?")
    
    # 3. Marketing Summary
    print("\n📱 MARKETING SUMMARY")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 manage-content.py --list",
        cwd=workspace_tools_path("book-marketing")
    )
    if success and output:
        print("Content scheduled:")
        print(output)
    else:
        print("⚠️  No content scheduled")
    
    print("\nQuestions to consider:")
    print("  - How many posts published?")
    print("  - What was the engagement?")
    print("  - What content performed best?")
    print("  - What to improve next week?")
    
    # 4. Sales Summary
    print("\n🤝 SALES SUMMARY")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 manage-leads.py --list",
        cwd=workspace_tools_path("book-sales")
    )
    if success and output:
        print("Leads:")
        print(output)
    else:
        print("⚠️  No leads")
    
    print("\nQuestions to consider:")
    print("  - How many new leads?")
    print("  - How many conversations started?")
    print("  - How many meetings booked?")
    print("  - What's the pipeline value?")
    
    # 5. Life Summary
    print("\n🌱 LIFE SUMMARY")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 habit-tracker.py --list",
        cwd=workspace_tools_path("book-life")
    )
    if success and output:
        print("Habits:")
        print(output)
    else:
        print("⚠️  No habits tracked")
    
    print("\nQuestions to consider:")
    print("  - Which habits did I maintain?")
    print("  - Which habits did I miss?")
    print("  - What's my longest streak?")
    print("  - What adjustments needed?")
    
    # 6. Learning Summary
    print("\n📚 LEARNING SUMMARY")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 learning-tracker.py --list-courses",
        cwd=workspace_tools_path("book-learning")
    )
    if success and output:
        print("Courses:")
        print(output)
    else:
        print("⚠️  No courses")
    
    success, output, _ = run_command(
        "python3 learning-tracker.py --list-books",
        cwd=workspace_tools_path("book-learning")
    )
    if success and output:
        print("\nBooks:")
        print(output)
    else:
        print("⚠️  No books")
    
    print("\nQuestions to consider:")
    print("  - What did I learn this week?")
    print("  - How much time spent learning?")
    print("  - What to focus on next week?")
    
    # 7. Creative Summary
    print("\n🎨 CREATIVE SUMMARY")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 project-tracker.py --list",
        cwd=workspace_tools_path("book-creative")
    )
    if success and output:
        print("Projects:")
        print(output)
    else:
        print("⚠️  No creative projects")
    
    print("\nQuestions to consider:")
    print("  - What creative work did I do?")
    print("  - What projects progressed?")
    print("  - What to focus on next week?")
    
    # Weekly Reflection
    print("\n" + "=" * 60)
    print("📝 WEEKLY REFLECTION")
    print("=" * 60)
    print("\nAnswer these questions:")
    print("1. What were my 3 biggest wins this week?")
    print("2. What were my 3 biggest challenges?")
    print("3. What did I learn?")
    print("4. What am I grateful for?")
    print("5. What are my top 3 priorities for next week?")
    
    print("\n" + "=" * 60)
    print("✅ Weekly review complete!")
    print("\n📝 Save your reflection to:")
    print(f"   {repo_path('docs', 'systems', 'weekly-reviews', f'{week}.md')}")

def main():
    parser = argparse.ArgumentParser(description='Generate weekly review')
    parser.add_argument('--week', help='Week (YYYY-W##)')
    
    args = parser.parse_args()
    weekly_review(args.week)

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Daily Workflow - Execute daily tasks across all working spaces
Usage: python3 daily-workflow.py --date 2026-06-07
"""

import argparse
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from workspace_paths import workspace_tools_path

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

def daily_workflow(date=None):
    """Execute daily workflow"""
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')
    
    print(f"🚀 Daily Workflow for {date}")
    print("=" * 60)
    
    # 1. Finance - Check today's expenses
    print("\n💰 FINANCE")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 financial-report.py --period " + date[:7],
        cwd=workspace_tools_path("book-finance")
    )
    if success:
        print("✅ Financial report generated")
    else:
        print("⚠️  No financial data yet for this period")
    
    # 2. Health - Check workout and nutrition
    print("\n💪 HEALTH")
    print("-" * 60)
    print("Reminder: Log today's workout and nutrition")
    print(f"  cd {workspace_tools_path('book-health')}")
    print("  python3 track-workout.py --type <type> --duration <min> --calories <cal>")
    print("  python3 track-nutrition.py --meal <meal> --calories <cal> --protein <g> --carbs <g> --fat <g>")
    
    # 3. Marketing - Check content calendar
    print("\n📱 MARKETING")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 manage-content.py --list",
        cwd=workspace_tools_path("book-marketing")
    )
    if success and output:
        print(output)
    else:
        print("⚠️  No content scheduled")
    
    # 4. Sales - Check leads
    print("\n🤝 SALES")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 manage-leads.py --list",
        cwd=workspace_tools_path("book-sales")
    )
    if success and output:
        print(output)
    else:
        print("⚠️  No leads yet")
    
    # 5. Life - Check habits and goals
    print("\n🌱 LIFE")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 habit-tracker.py --list",
        cwd=workspace_tools_path("book-life")
    )
    if success and output:
        print(output)
    else:
        print("⚠️  No habits tracked yet")
    
    # 6. Learning - Check courses and books
    print("\n📚 LEARNING")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 learning-tracker.py --list-courses",
        cwd=workspace_tools_path("book-learning")
    )
    if success and output:
        print(output)
    else:
        print("⚠️  No courses tracked yet")
    
    success, output, _ = run_command(
        "python3 learning-tracker.py --list-books",
        cwd=workspace_tools_path("book-learning")
    )
    if success and output:
        print(output)
    else:
        print("⚠️  No books tracked yet")
    
    # 7. Creative - Check projects
    print("\n🎨 CREATIVE")
    print("-" * 60)
    success, output, _ = run_command(
        "python3 project-tracker.py --list",
        cwd=workspace_tools_path("book-creative")
    )
    if success and output:
        print(output)
    else:
        print("⚠️  No creative projects tracked yet")
    
    print("\n" + "=" * 60)
    print("✅ Daily workflow complete!")
    print("\n📝 Next Steps:")
    print("  1. Log today's expenses (if any)")
    print("  2. Log workout and nutrition")
    print("  3. Check content calendar and publish if scheduled")
    print("  4. Follow up with leads")
    print("  5. Log habits")
    print("  6. Continue learning")
    print("  7. Work on creative projects")

def main():
    parser = argparse.ArgumentParser(description='Execute daily workflow')
    parser.add_argument('--date', help='Date (YYYY-MM-DD)')
    
    args = parser.parse_args()
    daily_workflow(args.date)

if __name__ == '__main__':
    main()

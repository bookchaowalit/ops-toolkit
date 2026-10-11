#!/usr/bin/env python3
"""
Master Integration Script - Manage all working spaces from one place
Usage: python3 master.py --status | --daily | --weekly | --report
"""

import argparse
import shlex
import subprocess
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from workspace_paths import repo_path, workspace_data_path, workspace_tools_path

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

def show_status():
    """Show status of all working spaces"""
    print("\n" + "=" * 70)
    print("📊 SOLO EMPIRE - ALL WORKING SPACES STATUS")
    print("=" * 70)
    print(f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()
    
    # Finance
    print("💰 FINANCE")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 financial-report.py --period " + datetime.now().strftime('%Y-%m'),
        cwd=workspace_tools_path("book-finance")
    )
    if success:
        print("✅ Financial tracking active")
    else:
        print("⚠️  No financial data yet")
    
    # Marketing
    print("\n📱 MARKETING")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 manage-content.py --list",
        cwd=workspace_tools_path("book-marketing")
    )
    if success and output:
        print("✅ Content calendar active")
        lines = output.strip().split('\n')
        if len(lines) > 2:
            print(f"   {len(lines) - 2} content items scheduled")
    else:
        print("⚠️  No content scheduled")
    
    # Sales
    print("\n🤝 SALES")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 manage-leads.py --list",
        cwd=workspace_tools_path("book-sales")
    )
    if success and output:
        print("✅ CRM active")
        lines = output.strip().split('\n')
        if len(lines) > 2:
            print(f"   {len(lines) - 2} leads tracked")
    else:
        print("⚠️  No leads yet")
    
    # Health
    print("\n💪 HEALTH")
    print("-" * 70)
    workouts = workspace_data_path("book-health", "workouts.csv")
    nutrition = workspace_data_path("book-health", "nutrition.csv")
    if os.path.exists(workouts) and os.path.getsize(workouts) > 0:
        print("✅ Workout tracking active")
    else:
        print("⚠️  No workouts logged yet")
    if os.path.exists(nutrition) and os.path.getsize(nutrition) > 0:
        print("✅ Nutrition tracking active")
    else:
        print("⚠️  No nutrition logged yet")
    
    # Life
    print("\n🌱 LIFE")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 habit-tracker.py --list",
        cwd=workspace_tools_path("book-life")
    )
    if success and output:
        print("✅ Habit tracking active")
        lines = output.strip().split('\n')
        if len(lines) > 2:
            print(f"   {len(lines) - 2} habits tracked")
    else:
        print("⚠️  No habits tracked yet")
    
    success, output, _ = run_command(
        "python3 goal-tracker.py --list",
        cwd=workspace_tools_path("book-life")
    )
    if success and output:
        print("✅ Goal tracking active")
    else:
        print("⚠️  No goals set yet")
    
    # Learning
    print("\n📚 LEARNING")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 learning-tracker.py --list-courses",
        cwd=workspace_tools_path("book-learning")
    )
    if success and output:
        print("✅ Course tracking active")
    else:
        print("⚠️  No courses tracked yet")
    
    # Creative
    print("\n🎨 CREATIVE")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 project-tracker.py --list",
        cwd=workspace_tools_path("book-creative")
    )
    if success and output:
        print("✅ Creative project tracking active")
    else:
        print("⚠️  No creative projects yet")
    
    # Strategy
    print("\n🎯 STRATEGY")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 market-analysis.py --list",
        cwd=workspace_tools_path("book-strategy")
    )
    if success and output:
        print("✅ Market analysis active")
    else:
        print("⚠️  No markets analyzed yet")
    
    success, output, _ = run_command(
        "python3 competitor-tracker.py --list",
        cwd=workspace_tools_path("book-strategy")
    )
    if success and output:
        print("✅ Competitor tracking active")
    else:
        print("⚠️  No competitors tracked yet")
    
    # Legal
    print("\n⚖️  LEGAL")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 contract-manager.py --list",
        cwd=workspace_tools_path("book-legal")
    )
    if success and output:
        print("✅ Contract management active")
    else:
        print("⚠️  No contracts tracked yet")
    
    # Design
    print("\n🎨 DESIGN")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 asset-manager.py --list",
        cwd=workspace_tools_path("book-design")
    )
    if success and output:
        print("✅ Design asset management active")
    else:
        print("⚠️  No design assets tracked yet")
    
    # Psychology
    print("\n🧠 PSYCHOLOGY")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 bias-checker.py --list",
        cwd=workspace_tools_path("book-psychology")
    )
    if success and output:
        print("✅ Bias tracking active")
    else:
        print("⚠️  No biases logged yet")
    
    # Leadership
    print("\n👥 LEADERSHIP")
    print("-" * 70)
    success, output, _ = run_command(
        "python3 decision-journal.py --list",
        cwd=workspace_tools_path("book-leadership")
    )
    if success and output:
        print("✅ Decision journal active")
    else:
        print("⚠️  No decisions logged yet")
    
    print("\n" + "=" * 70)
    print("✅ Status check complete!")
    print("=" * 70 + "\n")

def run_daily():
    """Run daily workflow across all spaces"""
    print("\n" + "=" * 70)
    print("🚀 DAILY WORKFLOW - ALL WORKING SPACES")
    print("=" * 70)
    print(f"📅 Date: {datetime.now().strftime('%Y-%m-%d')}")
    print()
    
    # Run the existing daily workflow
    success, output, _ = run_command(
        "python3 daily-workflow.py",
        cwd=repo_path("infra", "scripts", "workflows")
    )
    
    if success:
        print(output)
    else:
        print("❌ Error running daily workflow")
        print(output)
    
    print("\n" + "=" * 70)
    print("📝 REMINDERS:")
    print("  • Log all 8 habits")
    print("  • Track expenses")
    print("  • Check LinkedIn engagement")
    print("  • Send outreach messages")
    print("  • Continue learning")
    print("=" * 70 + "\n")

def run_weekly():
    """Run weekly review across all spaces"""
    print("\n" + "=" * 70)
    print("📊 WEEKLY REVIEW - ALL WORKING SPACES")
    print("=" * 70)
    print(f"📅 Week: {datetime.now().strftime('%Y-W%W')}")
    print()
    
    # Run the existing weekly review
    success, output, _ = run_command(
        "python3 weekly-review.py",
        cwd=repo_path("infra", "scripts", "workflows")
    )
    
    if success:
        print(output)
    else:
        print("❌ Error running weekly review")
        print(output)
    
    print("\n" + "=" * 70)
    print("📝 WEEKLY REFLECTION QUESTIONS:")
    print("  1. What were my 3 biggest wins?")
    print("  2. What were my 3 biggest challenges?")
    print("  3. What did I learn?")
    print("  4. What am I grateful for?")
    print("  5. What are my top 3 priorities for next week?")
    print("=" * 70 + "\n")

def generate_report():
    """Generate comprehensive report"""
    print("\n" + "=" * 70)
    print("📈 COMPREHENSIVE REPORT")
    print("=" * 70)
    print(f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()
    
    # Run dashboard
    success, output, _ = run_command(
        "python3 dashboard.py",
        cwd=repo_path("infra", "scripts", "workflows")
    )
    
    if success:
        print(output)
    else:
        print("❌ Error generating report")
    
    print("\n" + "=" * 70)
    print("📊 SUMMARY:")
    print("  • 12 working spaces active")
    print("  • 25+ Python tools available")
    print("  • 20+ CSV data files tracking")
    print("  • Integration scripts operational")
    print("=" * 70 + "\n")

def main():
    parser = argparse.ArgumentParser(description='Master integration script')
    parser.add_argument('--status', action='store_true', help='Show status of all spaces')
    parser.add_argument('--daily', action='store_true', help='Run daily workflow')
    parser.add_argument('--weekly', action='store_true', help='Run weekly review')
    parser.add_argument('--report', action='store_true', help='Generate comprehensive report')
    
    args = parser.parse_args()
    
    if args.status:
        show_status()
    elif args.daily:
        run_daily()
    elif args.weekly:
        run_weekly()
    elif args.report:
        generate_report()
    else:
        parser.print_help()

if __name__ == '__main__':
    main()

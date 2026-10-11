#!/usr/bin/env python3
"""
Daily Dashboard - MCP-Powered
ดึงข้อมูลจากทุก database และแสดง summary
"""

# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from cell_store import CellStoreError, open_cell_store
from habits_store import HabitsStoreError, open_habits_store
from task_store import ClientConfigError
import crm_ledger
import sys
from datetime import datetime, timedelta
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

def get_solo_empire_stats():
    """ดึง stats จาก Solo Empire database"""
    stats = {}

    # Projects
    try:
        stats['active_projects'] = len(open_cell_store("projects", "reporting-reader").list("projects", status="active"))
    except (CellStoreError, ClientConfigError):
        stats['active_projects'] = 0

    # Revenue this month
    today = datetime.now()
    month_start = today.replace(day=1).strftime('%Y-%m-%d')
    try:  # CRM ledger (income-pipeline); None when unreachable
        _rows, _totals = crm_ledger.open_crm_ledger().revenue_all(since=month_start)
        stats['monthly_revenue'] = crm_ledger.total_thb(_totals)
    except (crm_ledger.LedgerError, ClientConfigError):
        stats['monthly_revenue'] = None

    # Skills with progress
    try:
        stats['skills_learning'] = sum(
            1 for skill in open_cell_store("roles-skills", "reporting-reader").list("skills")
            if (skill["progress_percent"] or 0) > 0
        )
    except (CellStoreError, ClientConfigError):
        stats['skills_learning'] = 0

    # Habits streak
    try:
        active = open_habits_store("reporting-reader").habits(status="active")
        active.sort(key=lambda h: h["current_streak"], reverse=True)  # stable: ties keep id order
        stats['top_habits'] = [(h["name"], h["current_streak"]) for h in active[:5]]
    except (HabitsStoreError, ClientConfigError):
        stats['top_habits'] = []

    return stats

import os

from _env import get_env

def get_trading_stats():
    """ดึง stats จาก Trading database"""
    try:
        import psycopg2
        db_url = get_env("TRADING_DATABASE_URL", required=True)
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        stats = {}

        # Platforms
        cursor.execute("SELECT COUNT(*) FROM platforms WHERE status = 'active'")
        stats['active_platforms'] = cursor.fetchone()[0]

        # Running bots
        cursor.execute("SELECT COUNT(*) FROM bots WHERE status = 'running'")
        stats['running_bots'] = cursor.fetchone()[0]

        # Today's P&L
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute(f"""
            SELECT COALESCE(SUM(pnl), 0)
            FROM trades
            WHERE DATE(timestamp) = '{today}'
        """)
        stats['today_pnl'] = cursor.fetchone()[0]

        # Win rate (last 7 days)
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        cursor.execute(f"""
            SELECT
                COUNT(*) FILTER (WHERE pnl > 0)::float / NULLIF(COUNT(*), 0) * 100
            FROM trades
            WHERE timestamp >= '{week_ago}'
        """)
        stats['win_rate_7d'] = cursor.fetchone()[0] or 0

        # Top performing bot
        cursor.execute("""
            SELECT name, pnl
            FROM bots
            WHERE status = 'running'
            ORDER BY pnl DESC
            LIMIT 1
        """)
        result = cursor.fetchone()
        stats['top_bot'] = result[0] if result else 'None'
        stats['top_bot_pnl'] = result[1] if result else 0

        conn.close()
        return stats
    except Exception as e:
        return {'error': str(e)}

def main():
    print("=" * 70)
    print("📊 DAILY DASHBOARD - SOLO EMPIRE")
    print(f"📅 {datetime.now().strftime('%A, %B %d, %Y - %H:%M')}")
    print("=" * 70)
    print()

    # Solo Empire Stats
    print("🏢 SOLO EMPIRE")
    print("-" * 70)
    se_stats = get_solo_empire_stats()
    print(f"  Active Projects:     {se_stats['active_projects']}")
    monthly = se_stats['monthly_revenue']
    print(f"  Monthly Revenue:     {'unavailable (CRM ledger)' if monthly is None else f'฿{monthly:,.0f}'}")
    print(f"  Skills Learning:     {se_stats['skills_learning']}")
    print()
    print("  🔥 Top Habit Streaks:")
    for name, streak in se_stats['top_habits']:
        print(f"     • {name}: {streak} days")
    print()

    # Trading Stats
    print("💰 TRADING BOT")
    print("-" * 70)
    trading_stats = get_trading_stats()
    if 'error' in trading_stats:
        print(f"  ❌ Error: {trading_stats['error']}")
    else:
        print(f"  Active Platforms:    {trading_stats['active_platforms']}")
        print(f"  Running Bots:        {trading_stats['running_bots']}")
        print(f"  Today's P&L:         ฿{trading_stats['today_pnl']:,.2f}")
        print(f"  Win Rate (7d):       {trading_stats['win_rate_7d']:.1f}%")
        print(f"  Top Bot:             {trading_stats['top_bot']} (฿{trading_stats['top_bot_pnl']:,.2f})")
    print()

    # Quick Actions
    print("⚡ QUICK ACTIONS")
    print("-" * 70)
    print("  1. Query database:     'cell API list ...'")
    print("  2. Check trading:      'postgres query ...'")
    print("  3. Search repos:       'github search ...'")
    print("  4. Fetch web data:     'fetch url ...'")
    print()
    print("=" * 70)

if __name__ == '__main__':
    main()

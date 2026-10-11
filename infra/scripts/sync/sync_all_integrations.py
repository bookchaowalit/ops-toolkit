#!/usr/bin/env python3
"""
Master Sync Script for All External Integrations
Runs the remaining external-service syncs (Calendar probe, Todoist push). Local integration tables are retired.

Usage:
    python3 sync_all_integrations.py --all              # Sync everything
    python3 sync_all_integrations.py --calendar --email  # Sync specific
    python3 sync_all_integrations.py --all --json        # JSON output
    python3 sync_all_integrations.py --all --dry-run     # Offline orchestration check
    python3 sync_all_integrations.py --status            # Show sync status
"""

import sys
import argparse
import subprocess
import time
import json
from pathlib import Path
from datetime import datetime

# Add integrations to path
sys.path.insert(0, str(Path(__file__).parent.parent / "integrations"))

MAX_RETRIES = 1
RETRY_DELAY = 3  # seconds


def _run_sync(script_name, extra_args=None, timeout=120):
    """Run a sync script with retry logic. Returns structured result dict."""
    script = Path(__file__).parent / script_name
    cmd = [sys.executable, str(script)]
    if extra_args:
        cmd.extend(extra_args)

    last_err = ""
    for attempt in range(1 + MAX_RETRIES):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode == 0:
                # Try to extract synced count from stdout
                synced = _extract_count(result.stdout)
                return {'status': 'success', 'synced': synced, 'output': result.stdout.strip()}
            last_err = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        except subprocess.TimeoutExpired:
            last_err = f"timed out after {timeout}s"
        except Exception as e:
            last_err = str(e)

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    return {'status': 'error', 'error': last_err, 'synced': 0}


def _extract_count(output):
    """Try to extract a numeric count from script stdout (e.g. 'Synced 5 events')."""
    import re
    # Prefer the canonical email pipeline counters when its JSON output is used.
    for key in ("emails_persisted", "routes_created", "synced"):
        m = re.search(rf'"{key}"\s*:\s*(\d+)', output)
        if m:
            return int(m.group(1))
    for line in reversed(output.splitlines()):
        m = re.search(r'[Ss]ynced\s+(\d+)', line)
        if m:
            return int(m.group(1))
        m = re.search(r'(\d+)\s+(?:new\s+)?(?:items|events|emails|activities|records|tasks|metrics|logs)', line)
        if m:
            return int(m.group(1))
    return 0


def sync_google_calendar(days_ahead=30, days_back=7):
    """Sync Google Calendar events"""
    print("\n" + "="*70)
    print("📅 Syncing Google Calendar...")
    print("="*70)
    result = _run_sync("sync_google_calendar.py")
    if result['status'] == 'success':
        print(result.get('output', ''))
    else:
        print(f"❌ Error: {result['error']}")
    return result


def sync_gmail(days_back=7):
    """Gmail intake no longer persists to a local database; report it as retired."""
    print("\n" + "="*70)
    print("📧 Gmail sync retired")
    print("="*70)
    print("The local email pipeline was retired; Gmail is read on demand by its own scripts.")
    return {
        'status': 'success',
        'synced': 0,
        'output': 'Gmail sync retired: no local email store',
    }


def sync_todoist():
    """Sync Todoist tasks (push goals → Todoist)"""
    print("\n" + "="*70)
    print("✅ Syncing Todoist...")
    print("="*70)
    result = _run_sync("sync_goals_to_todoist.py", ["--limit", "30"], timeout=300)
    if result['status'] == 'success':
        print(result.get('output', ''))
    else:
        print(f"❌ Error: {result['error']}")
    return result


def sync_strava(days=7):
    """Sync Strava activities"""
    print("\n" + "="*70)
    print("🏃 Syncing Strava...")
    print("="*70)
    result = _run_sync("sync_strava.py", ["--days", str(days)])
    if result['status'] == 'success':
        print(result.get('output', ''))
    else:
        print(f"❌ Error: {result['error']}")
    return result


def sync_leantime(limit=50):
    """Report that employer Leantime work is managed externally."""
    print("\n" + "="*70)
    print("📋 Leantime sync disabled")
    print("="*70)
    print("Employer work is managed in nexatech-empire.")
    return {
        'status': 'success',
        'synced': 0,
        'output': 'Leantime sync disabled: employer work is managed in nexatech-empire',
    }


def show_sync_status():
    """Show status of all integrations (no local tables remain to count)."""
    print("\n" + "="*70)
    print("📊 Integration Status")
    print("="*70)
    print("Local integration tables are retired; per-service status comes from")
    print("integration_health.py and the Calendar status file.")
    print()


def show_sync_status_json():
    """Output integration status as JSON."""
    print(json.dumps({"status": "unavailable", "reason": "local integration tables retired"}, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description='Sync all external integrations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Sync all integrations
  python3 sync_all_integrations.py --all

  # Sync only Google Calendar
  python3 sync_all_integrations.py --calendar

  # Sync calendar and email
  python3 sync_all_integrations.py --calendar --email

  # Show sync status
  python3 sync_all_integrations.py --status
        """
    )

    parser.add_argument('--all', action='store_true',
                       help='Sync all integrations')
    parser.add_argument('--calendar', action='store_true',
                       help='Sync Google Calendar')
    parser.add_argument('--email', action='store_true',
                       help='Sync Gmail')
    parser.add_argument('--tasks', action='store_true',
                       help='Sync Todoist')
    parser.add_argument('--leantime', action='store_true',
                       help='Report Leantime as external-only (no local sync)')
    parser.add_argument('--strava', action='store_true',
                       help='Deprecated: Strava is intentionally paused')
    parser.add_argument('--binance', action='store_true',
                       help='Deprecated: the Binance TH sync was removed (it wrote revenue and transactions into SQLite)')
    parser.add_argument('--json', action='store_true',
                       help='Output results as JSON')
    parser.add_argument('--dry-run', action='store_true',
                       help='Validate orchestration without calling external APIs or writing data')
    parser.add_argument('--status', action='store_true',
                       help='Show sync status')
    parser.add_argument('--days-ahead', type=int, default=30,
                       help='Days ahead to sync for calendar (default: 30)')
    parser.add_argument('--days-back', type=int, default=7,
                       help='Days back to sync (default: 7)')

    args = parser.parse_args()

    if args.status:
        show_sync_status()
        return

    json_mode = getattr(args, 'json', False)

    active_requested = any([args.all, args.calendar, args.email, args.tasks, args.leantime])
    if not active_requested:
        if args.strava or args.binance:
            print('⏸ Strava and Binance TH are intentionally paused; no sync was run.')
            return
        if json_mode:
            # --json alone → show status as JSON
            show_sync_status_json()
        else:
            parser.print_help()
        return

    if args.dry_run:
        requested = []
        if args.all or args.calendar:
            requested.append('calendar')
        if args.all or args.email:
            requested.append('email')
        if args.all or args.tasks:
            requested.append('tasks')
        if args.all or args.leantime:
            requested.append('leantime')

        results = {
            service: {
                'status': 'dry_run',
                'synced': 0,
                'output': 'External API call skipped by --dry-run',
            }
            for service in requested
        }
        if json_mode:
            print(json.dumps({
                'sync_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'duration_sec': 0,
                'results': results,
                'summary': {
                    'total': len(results),
                    'success': 0,
                    'dry_run': len(results),
                    'failed': 0,
                },
            }, indent=2))
        else:
            print('\n' + '='*70)
            print('🧪 Dry-run: external API calls and writes skipped')
            print('='*70)
            for service in requested:
                print(f'⏭️  {service.title()}: skipped')
        return

    results = {}
    start_time = datetime.now()

    if not json_mode:
        print("\n" + "="*70)
        print(f"🚀 Starting sync at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)

    if args.all or args.calendar:
        results['calendar'] = sync_google_calendar(args.days_ahead, args.days_back)

    if args.all or args.email:
        results['email'] = sync_gmail(args.days_back)

    if args.all or args.tasks:
        results['tasks'] = sync_todoist()

    if args.all or args.leantime:
        results['leantime'] = sync_leantime()

    if args.strava or args.binance:
        print('⏸ Strava and Binance TH are intentionally paused; requested flags were skipped.')

    # Summary
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    if json_mode:
        summary = {
            "sync_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": round(duration, 1),
            "results": {},
        }
        ok = fail = 0
        for svc, r in results.items():
            summary["results"][svc] = {"status": r["status"], "synced": r.get("synced", 0)}
            if r["status"] == "success":
                ok += 1
            else:
                fail += 1
            if r["status"] == "error":
                summary["results"][svc]["error"] = r.get("error", "Unknown")
        summary["summary"] = {"total": len(results), "success": ok, "failed": fail}
        print(json.dumps(summary, indent=2))
        return

    print("\n" + "="*70)
    print("📊 Sync Summary")
    print("="*70)

    for service, result in results.items():
        status_icon = {
            'success': '✅',
            'not_configured': '⚠️',
            'error': '❌'
        }.get(result['status'], '❓')

        if result['status'] == 'success':
            print(f"{status_icon} {service.title()}: {result.get('synced', 0)} items synced")
        elif result['status'] == 'not_configured':
            print(f"{status_icon} {service.title()}: Not configured")
        else:
            print(f"{status_icon} {service.title()}: Error - {result.get('error', 'Unknown')}")

    print(f"\n⏱️  Total time: {duration:.1f} seconds")
    print(f"🏁 Completed at {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    main()

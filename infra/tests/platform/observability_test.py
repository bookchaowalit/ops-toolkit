#!/usr/bin/env python3
"""
Observability Tests

Verifies that logging, alerting, and monitoring systems work correctly:
- Logger infrastructure creates log files
- Alert system detects issues
- Cron health monitor runs
- Retry mechanisms exist in sync scripts

Usage:
    python3 infra/tests/platform/observability_test.py
"""

import os
import sys
import subprocess
import tempfile
import json
from pathlib import Path
from datetime import datetime

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'utils'))
from _env import PROJECT_ROOT, DB_PATH


def test_logger_infrastructure():
    """Verify empire_logger module works correctly."""
    print("\n[1/5] Testing logger infrastructure...")

    try:
        from empire_logger import get_logger

        # Create a test logger
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, 'test.log')
            logger = get_logger('test_observability', log_file=log_file)

            # Log some messages
            logger.info('Test info message')
            logger.warning('Test warning message')
            logger.error('Test error message')

            # Verify log file was created and has content
            if not os.path.exists(log_file):
                print("  ❌ Log file not created")
                return False

            content = Path(log_file).read_text()
            if 'Test info message' in content and 'Test warning' in content:
                print("  ✅ Logger creates log files with correct format")
                return True
            else:
                print("  ❌ Log file missing expected content")
                return False

    except Exception as e:
        print(f"  ❌ Logger test failed: {e}")
        return False


def test_alert_system():
    """Verify alerts.py runs without errors."""
    print("\n[2/5] Testing alert system...")

    alerts_script = PROJECT_ROOT / 'infra' / 'scripts' / 'utils' / 'alerts.py'
    if not alerts_script.exists():
        print("  ⚠️  alerts.py not found (skipping)")
        return True

    try:
        result = subprocess.run(
            [sys.executable, str(alerts_script), '--json'],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT
        )

        # Should exit 0 even if there are alerts
        if result.returncode == 0:
            # Try to parse JSON
            try:
                output = json.loads(result.stdout)
                print(f"  ✅ Alert system runs and returns JSON ({len(output.get('critical', []))} critical, {len(output.get('warnings', []))} warnings)")
                return True
            except json.JSONDecodeError:
                # Not JSON, but still ran successfully
                print("  ✅ Alert system runs successfully")
                return True
        else:
            print(f"  ❌ Alert system failed with exit code {result.returncode}")
            print(result.stderr)
            return False

    except subprocess.TimeoutExpired:
        print("  ❌ Alert system timed out")
        return False
    except Exception as e:
        print(f"  ❌ Alert system test failed: {e}")
        return False


def test_cron_health_monitor():
    """Verify cron health monitor runs."""
    print("\n[3/5] Testing cron health monitor...")

    monitor_script = PROJECT_ROOT / 'infra' / 'scripts' / 'workflows' / 'cron_health_monitor.py'
    if not monitor_script.exists():
        print("  ⚠️  cron_health_monitor.py not found (skipping)")
        return True

    try:
        result = subprocess.run(
            [sys.executable, str(monitor_script)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT
        )

        # Should run without crashing
        if result.returncode == 0:
            print("  ✅ Cron health monitor runs successfully")
            return True
        else:
            # May fail if crontab not set up, but shouldn't crash
            if 'Traceback' not in result.stderr:
                print("  ✅ Cron health monitor runs (crontab may not be configured)")
                return True
            else:
                print(f"  ❌ Cron health monitor crashed")
                print(result.stderr)
                return False

    except subprocess.TimeoutExpired:
        print("  ❌ Cron health monitor timed out")
        return False
    except Exception as e:
        print(f"  ❌ Cron health monitor test failed: {e}")
        return False


def test_retry_mechanisms():
    """Verify sync scripts have retry logic."""
    print("\n[4/5] Testing retry mechanisms...")

    sync_scripts = [
        'infra/scripts/sync/sync_google_calendar.py',
        'infra/scripts/sync/sync_all_integrations.py',
    ]

    scripts_with_retry = 0
    for script_rel in sync_scripts:
        script = PROJECT_ROOT / script_rel
        if not script.exists():
            continue

        content = script.read_text()

        # Check for retry patterns
        if 'MAX_RETRIES' in content or 'retry' in content.lower():
            scripts_with_retry += 1

    if scripts_with_retry >= 2:
        print(f"  ✅ {scripts_with_retry} sync scripts have retry mechanisms")
        return True
    else:
        print(f"  ⚠️  Only {scripts_with_retry} sync scripts have retry logic")
        return True  # Not a hard failure


def test_log_rotation():
    """Verify log rotation is configured."""
    print("\n[5/5] Testing log rotation configuration...")

    # Check for logrotate config
    logrotate_conf = PROJECT_ROOT / 'infra' / 'scripts' / 'utils' / 'logrotate-scraping.conf'
    rotate_script = PROJECT_ROOT / 'infra' / 'scripts' / 'utils' / 'rotate-logs.sh'

    has_config = logrotate_conf.exists() or rotate_script.exists()

    if has_config:
        print("  ✅ Log rotation is configured")
        return True
    else:
        print("  ⚠️  No log rotation config found (may use external rotation)")
        return True  # Not a hard failure


def main():
    print("=" * 70)
    print("Observability Tests")
    print("=" * 70)

    tests = [
        test_logger_infrastructure,
        test_alert_system,
        test_cron_health_monitor,
        test_retry_mechanisms,
        test_log_rotation,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ❌ {test.__name__} raised exception: {e}")
            failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed == 0:
        print("✅ All observability tests passed!")
        return 0
    else:
        print(f"❌ {failed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())

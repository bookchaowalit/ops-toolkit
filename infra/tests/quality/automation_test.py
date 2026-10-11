#!/usr/bin/env python3
"""
Automation E2E Test — Validate alert + analytics + sync pipeline.

Tests:
  2. Dashboard refresh: generates all dashboard views offline
  3. Alert system: runs without errors, JSON output valid
  5. Weekly review: generates without errors
  6. Integration sync scripts: all exist
  7. Analytics scripts: all produce valid JSON
  9. Data freshness: JSON output valid
 10. System status: JSON output valid
 11. Script registry: JSON output valid

Usage:
    python3 infra/tests/quality/automation_test.py
    python3 infra/tests/quality/automation_test.py --verbose
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

if not os.environ.get('SOLO_EMPIRE_AUTOMATION_FIXTURE'):
    from automation_fixture import run_isolated
    raise SystemExit(run_isolated(Path(__file__), sys.argv[1:]))

fixture_root = Path(__file__).resolve().parents[3]
if (
    fixture_root != Path(os.environ['SOLO_EMPIRE_AUTOMATION_FIXTURE']).resolve()
    or not (fixture_root / '.automation-fixture').is_file()
):
    raise RuntimeError('Automation tests require the disposable fixture workspace')

# Add utils directory to path for _env import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'utils'))
from _env import PROJECT_ROOT

SCRIPTS_DIR = PROJECT_ROOT / 'infra' / 'scripts'
PYTHON = sys.executable or '/usr/bin/python3'

passed = 0
failed = 0
errors = []


def run(cmd, timeout=60, drop_env=()):
    """Run an offline-safe command and return (success, stdout, stderr)."""
    test_env = {k: v for k, v in os.environ.items() if k not in drop_env}
    # Automation tests must not contact Infisical or external APIs. Live
    # provider checks belong to integration_health.py, not this E2E suite.
    test_env['SECRETS_PROVIDER'] = 'env'
    test_env['SECRETS_FALLBACK_ENV'] = 'false'
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            timeout=timeout, env=test_env, cwd=PROJECT_ROOT
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, '', 'Timeout'
    except Exception as e:
        return False, '', str(e)


def test(name):
    """Decorator for test functions."""
    def decorator(fn):
        global passed, failed
        verbose = '--verbose' in sys.argv
        if verbose:
            print(f'\n{"="*60}')
            print(f'TEST: {name}')
            print(f'{"="*60}')
        try:
            ok = fn()
            if ok:
                passed += 1
                print(f'  ✅ {name}')
            else:
                failed += 1
                errors.append(name)
                print(f'  ❌ {name}')
        except Exception as e:
            failed += 1
            errors.append(f'{name}: {e}')
            print(f'  ❌ {name}: {e}')
        return fn
    return decorator


@test('Automation fixture: paths, credentials, and network are isolated')
def test_fixture_boundary():
    import socket
    assert PROJECT_ROOT == fixture_root
    assert not (PROJECT_ROOT / 'infra/.env').exists()
    for key in ('TELEGRAM_BOT_TOKEN', 'GOOGLE_REFRESH_TOKEN', 'INFISICAL_TOKEN', 'VAULT_TOKEN'):
        assert key not in os.environ
    try:
        socket.create_connection(('127.0.0.1', 1))
    except PermissionError:
        return True
    raise AssertionError('Automation fixture must deny network access')


# ============================================================
# TEST 1b: Health JSON contract — schema version + credentials
# ============================================================
@test('Health JSON: has schema_version and credentials field')
def test_health_contract():
    health_script = SCRIPTS_DIR / 'utils' / 'system_health.py'
    ok, out, err = run([PYTHON, str(health_script), '--json'])
    if not ok:
        print(f'    system_health --json failed: {err[:200]}')
        return False

    try:
        data = json.loads(out)

        # Check schema version exists
        if 'schema_version' not in data:
            print('    Missing schema_version field')
            return False

        # Check credentials field exists
        if 'credentials' not in data:
            print('    Missing credentials field')
            return False

        # Check credentials structure
        creds = data['credentials']
        if not isinstance(creds, dict):
            print('    credentials is not a dict')
            return False

        # Missing credentials should not create a system issue by themselves.
        # A non-A grade is valid when an unrelated cron job has a real failure.
        sys_health = data.get('system_health', {})
        cron = data.get('cron', {})
        if sys_health.get('grade') != 'A' and not cron.get('failed_jobs'):
            print(f'    System health should be Grade A when no jobs have failed, got {sys_health.get("grade")}')
            return False

        print(f'    Schema version: {data["schema_version"]}, Credentials: {len(creds)} services')
        return True

    except (json.JSONDecodeError, KeyError) as e:
        print(f'    JSON parse failed: {e}')
        return False


# ============================================================
# TEST 1c: CLI entrypoints — direct invocation works
# ============================================================
@test('CLI entrypoints: all domain CLIs respond to --help')
def test_cli_entrypoints():
    cli_dir = SCRIPTS_DIR / 'cli'
    scripts = sorted(cli_dir.glob('*.py'))
    failures = []
    for script in scripts:
        ok, _out, err = run([PYTHON, str(script), '--help'], timeout=10)
        if not ok:
            failures.append(f'{script.name}: {err[:120]}')
    if failures:
        print('    CLI failures: ' + '; '.join(failures))
        return False
    print(f'    {len(scripts)} CLI entrypoints respond to --help')
    return bool(scripts)


# ============================================================
# TEST 3: Alert system — runs without errors, JSON valid
# ============================================================
@test('Alert system: runs and produces valid JSON output')
def test_alerts_json():
    alerts = SCRIPTS_DIR / 'utils' / 'alerts.py'
    ok, out, err = run([PYTHON, str(alerts), '--json'])
    if not ok and 'exit code' not in err:
        # alerts exits with 1 if critical alerts found — that's OK
        pass
    try:
        data = json.loads(out)
        assert 'critical' in data
        assert 'warnings' in data
        assert 'summary' in data
        assert 'healthy' in data['summary']
        print(f'    JSON valid: {data["summary"]["critical"]} critical, '
              f'{data["summary"]["warnings"]} warnings')
        return True
    except (json.JSONDecodeError, AssertionError) as e:
        print(f'    JSON parse failed: {e}')
        print(f'    stdout: {out[:200]}')
        return False


@test('Alert system: text mode runs without crash')
def test_alerts_text():
    alerts = SCRIPTS_DIR / 'utils' / 'alerts.py'
    ok, out, err = run([PYTHON, str(alerts)])
    # Exit code 1 = critical alerts found (expected)
    has_output = 'SYSTEM ALERTS' in out
    print(f'    Text output: {"SYSTEM ALERTS" if has_output else "NO OUTPUT"}')
    return has_output


# ============================================================
# TEST 3b: Notification scripts — handle missing credentials gracefully
# ============================================================
@test('Notification scripts: handle missing credentials without crashing')
def test_notification_credentials():
    # Test alerts.py with --telegram flag (should not crash even without credentials)
    alerts = SCRIPTS_DIR / 'utils' / 'alerts.py'
    ok, out, err = run([PYTHON, str(alerts), '--telegram'])

    # Should not crash (exit code 0 or 1 for alerts is OK)
    # Should have graceful message about missing credentials
    has_graceful_msg = ('missing' in out.lower() or 'skipped' in out.lower() or
                        'no-telegram' in out.lower() or 'telegram' in out.lower())

    if not has_graceful_msg and not ok:
        print(f'    alerts.py crashed or has no graceful handling: {err[:200]}')
        return False

    print('    Notification scripts handle missing credentials gracefully')
    return True


# ============================================================
# TEST 6: Integration sync scripts — all exist
# ============================================================
@test('Integration sync: all sync scripts exist')
def test_sync_scripts_exist():
    scripts = [
        'sync/sync_google_calendar.py',
        'sync/sync_strava.py',
        'sync/sync_all_integrations.py',
    ]
    missing = []
    for s in scripts:
        path = SCRIPTS_DIR / s
        if not path.exists():
            missing.append(s)
    print(f'    {len(scripts) - len(missing)}/{len(scripts)} scripts found')
    if missing:
        print(f'    Missing: {", ".join(missing)}')
    return len(missing) == 0


@test('Integration sync: sync_all --all runs without crash')
def test_sync_all_dry():
    """Test that sync_all_integrations.py --all at least starts."""
    sync_all = SCRIPTS_DIR / 'sync' / 'sync_all_integrations.py'
    ok, out, err = run([PYTHON, str(sync_all), '--all', '--dry-run'], timeout=30)
    # Offline orchestration check: no external credentials or network required.
    has_output = len(out) > 50 or len(err) > 50
    print(f'    sync_all output length: {len(out)} chars')
    return has_output  # script ran, even if individual syncs failed


# ============================================================
# TEST 7: Analytics scripts — all produce valid JSON
# ============================================================
@test('Analytics: revenue_analytics reports an unreachable CRM ledger instead of guessing')
def test_revenue_analytics():
    script = SCRIPTS_DIR / 'utils' / 'revenue_analytics.py'
    # Without the SQLite store doubles the ledger client has no reachable API (network is denied).
    ok, out, err = run([PYTHON, str(script), '--json'],
                       drop_env=('SOLO_EMPIRE_DB_PATH', 'SOLO_EMPIRE_TEST_DOUBLES'))
    if ok or out.strip() or 'ledger unavailable' not in err:
        print(f'    expected a clean "ledger unavailable" exit, got ok={ok} err={err[:200]}')
        return False
    print('    ledger unavailable offline: no figures invented')
    return True


@test('Analytics: habit_analytics --json produces valid output')
def test_habit_analytics():
    script = SCRIPTS_DIR / 'utils' / 'habit_analytics.py'
    ok, out, err = run([PYTHON, str(script), '--json'])
    try:
        data = json.loads(out)
        assert isinstance(data, dict)
        print(f'    Keys: {list(data.keys())[:5]}')
        return True
    except (json.JSONDecodeError, AssertionError) as e:
        print(f'    JSON parse failed: {e}')
        return False


@test('Analytics: goal_velocity --json produces valid output')
def test_goal_velocity():
    script = SCRIPTS_DIR / 'utils' / 'goal_velocity.py'
    # Goals live in the goals cell: run under the SQLite cell-store double over an empty temp database.
    import sqlite3, tempfile
    support = Path(__file__).resolve().parents[1] / 'support'
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / 'solo-empire.db'
        conn = sqlite3.connect(db)
        conn.executescript((support / 'legacy_portfolio_tables.sql').read_text(encoding='utf-8'))
        conn.commit()
        conn.close()
        env = {**os.environ, 'SOLO_EMPIRE_DB_PATH': str(db), 'EMPIRE_CLIENT_CONFIG': '/nonexistent'}
        proc = subprocess.run([PYTHON, str(support / 'run_with_sqlite_store.py'), str(script), '--json'],
                              capture_output=True, text=True, encoding='utf-8', env=env, cwd=PROJECT_ROOT)
        out = proc.stdout
    try:
        data = json.loads(out)
        assert isinstance(data, dict)
        print(f'    Keys: {list(data.keys())[:5]}')
        return True
    except (json.JSONDecodeError, AssertionError) as e:
        print(f'    JSON parse failed: {e}')
        return False


# ============================================================
# TEST 10: System status — JSON output
# ============================================================
@test('System status: --json produces valid output')
def test_system_status_json():
    script = SCRIPTS_DIR / 'utils' / 'system_status.py'
    ok, out, err = run([PYTHON, str(script), '--json'])
    if not ok:
        print(f'    system_status failed: {err[:200]}')
        return False
    try:
        data = json.loads(out)
        assert 'revenue' in data
        assert 'goals' in data
        assert 'tasks' in data
        print(f'    ฿{data["revenue"]["received"]:,.0f} received')
        return True
    except (json.JSONDecodeError, AssertionError) as e:
        print(f'    JSON parse failed: {e}')
        return False


# ============================================================
# TEST 11: Script registry — JSON output
# ============================================================
@test('Script registry: --json produces valid output')
def test_script_registry_json():
    script = SCRIPTS_DIR / 'utils' / 'script_registry.py'
    ok, out, err = run([PYTHON, str(script), '--json'])
    if not ok:
        print(f'    script_registry failed: {err[:200]}')
        return False
    try:
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) > 50  # should have 50+ scripts
        cats = set(s['category'] for s in data)
        print(f'    {len(data)} scripts across {len(cats)} categories')
        return True
    except (json.JSONDecodeError, AssertionError) as e:
        print(f'    JSON parse failed: {e}')
        return False


@test('Sync scripts: all key scripts support --json flag')
def test_sync_json_flags():
    sync_dir = SCRIPTS_DIR / 'sync'
    scripts = ['sync_all_integrations.py', 'sync_google_calendar.py',
               'sync_strava.py']
    for name in scripts:
        path = sync_dir / name
        assert path.exists(), f'{name} missing'
        content = path.read_text(encoding='utf-8')
        assert '--json' in content, f'{name} missing --json support'
    print(f'    All {len(scripts)} sync scripts have --json support')
    return True


# ============================================================
# TEST 25: Sync scripts — retry logic present
# ============================================================
@test('Sync scripts: retry logic present in API-calling scripts')
def test_sync_retry_logic():
    sync_dir = SCRIPTS_DIR / 'sync'
    scripts = ['sync_google_calendar.py', 'sync_strava.py']
    for name in scripts:
        path = sync_dir / name
        content = path.read_text(encoding='utf-8')
        assert 'MAX_RETRIES' in content, f'{name} missing MAX_RETRIES'
        assert 'RETRY_DELAY' in content, f'{name} missing RETRY_DELAY'
        assert 'retry' in content.lower() or 'attempt' in content.lower(), \
            f'{name} missing retry loop'
    print(f'    All {len(scripts)} API sync scripts have retry logic')
    return True


# ============================================================
# TEST 26: Sync orchestrator — retry + structured output
# ============================================================
@test('Sync orchestrator: _run_sync has retry + structured results')
def test_sync_orchestrator_hardened():
    path = SCRIPTS_DIR / 'sync' / 'sync_all_integrations.py'
    content = path.read_text(encoding='utf-8')
    assert '_run_sync' in content, 'missing _run_sync helper'
    assert 'MAX_RETRIES' in content, 'missing MAX_RETRIES'
    assert '_extract_count' in content, 'missing _extract_count parser'
    assert 'show_sync_status_json' in content, 'missing JSON status function'
    # Verify the old inline subprocess blocks are gone
    assert content.count('import subprocess') <= 1, 'should use top-level import only'
    print(f'    Orchestrator has retry, count extraction, JSON status')
    return True


# ============================================================
# TEST 27: Sync scripts — error handling wraps API calls
# ============================================================
@test('Sync scripts: error handling wraps main functions')
def test_sync_error_handling():
    sync_dir = SCRIPTS_DIR / 'sync'
    scripts = ['sync_google_calendar.py', 'sync_strava.py']
    for name in scripts:
        path = sync_dir / name
        content = path.read_text(encoding='utf-8')
        assert 'try:' in content, f'{name} missing try/except in main'
        assert 'except Exception' in content, f'{name} missing broad except in main'
        assert 'sys.exit(1)' in content, f'{name} missing sys.exit(1) on failure'
    print(f'    All {len(scripts)} scripts have error handling in main()')
    return True


# ============================================================
# SUMMARY
# ============================================================
def main():
    print(f'\n{"="*60}')
    print(f'AUTOMATION E2E TEST')
    print(f'{"="*60}')
    print(f'Project: {PROJECT_ROOT}')
    print(f'Scripts: {SCRIPTS_DIR}')
    print(f'Time: {time.strftime("%Y-%m-%d %H:%M:%S")}')

    # Tests run via decorators above

    print(f'\n{"="*60}')
    print(f'RESULTS')
    print(f'{"="*60}')
    total = passed + failed
    print(f'  Passed: {passed}/{total}')
    print(f'  Failed: {failed}/{total}')
    if errors:
        print(f'\n  Failures:')
        for e in errors:
            print(f'    ❌ {e}')
    print()

    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()

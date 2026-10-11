#!/usr/bin/env python3
"""
Security Regression Tests

Verifies that security fixes remain in place:
- No hardcoded passwords in test scripts
- Database file has secure permissions
- Security audit passes with 0 CRITICAL/HIGH issues
"""

import os
import sys
import subprocess
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'utils'))
from _env import PROJECT_ROOT


def test_no_hardcoded_passwords():
    """Verify test scripts use environment variables for passwords."""
    print("\n[1/4] Checking for hardcoded passwords...")

    test_files = [
        PROJECT_ROOT / 'projects/product/engineering/book-dev/github/bookchaowalit-ai/public/book-products/booktrading/strategy/scripts/backfill_journal.py',
    ]

    for file_path in test_files:
        if not file_path.exists():
            continue

        content = file_path.read_text()

        # Check that passwords use os.getenv
        if 'PASSWORD' in content:
            if 'os.getenv' not in content:
                print(f"  ❌ {file_path.name} has hardcoded password")
                return False

    print("  ✅ No hardcoded passwords found")
    return True


def test_database_permissions():
    """Verify database file has secure permissions (600 - owner read/write only)."""
    print("\n[2/4] Checking database file permissions...")

    db_path = PROJECT_ROOT / 'infra' / 'database' / 'operations.db'
    if not db_path.exists():
        print("  ⚠️  Database file not found (skipping)")
        return True

    mode = oct(db_path.stat().st_mode)[-3:]
    if mode == '600':
        print(f"  ✅ Database permissions: {mode} (owner read/write only)")
        return True
    else:
        print(f"  ❌ Database permissions too open: {mode} (expected 600)")
        print(f"     Fix: chmod 600 {db_path}")
        return False


def test_backup_permissions():
    """Verify database backups remain owner-only (directory 700, files 600)."""
    print("\n[3/4] Checking backup permissions...")

    backup_dir = PROJECT_ROOT / 'data' / 'backups'
    if not backup_dir.exists():
        print("  ⚠️  Backup directory not found (skipping)")
        return True

    insecure = [
        path for path in backup_dir.iterdir()
        if path.is_file()
        and path.name != '.gitkeep'
        and oct(path.stat().st_mode)[-3:] != '600'
    ]
    if insecure:
        print(f"  ❌ Insecure backup files: {', '.join(path.name for path in insecure[:3])}")
        return False

    backup_files = [path for path in backup_dir.iterdir() if path.is_file() and path.name != '.gitkeep']
    if not backup_files:
        print("  ✅ No backup artifacts present (writer enforces private permissions)")
        return True

    dir_mode = oct(backup_dir.stat().st_mode)[-3:]
    if dir_mode != '700':
        print(f"  ❌ Backup directory permissions: {dir_mode} (expected 700)")
        return False

    print("  ✅ Backup directory/files are owner-only")
    return True


def test_security_audit_passes():
    """Verify security audit has 0 CRITICAL/HIGH issues."""
    print("\n[4/4] Running security audit...")

    audit_script = PROJECT_ROOT / 'infra' / 'scripts' / 'utils' / 'security_audit.py'
    if not audit_script.exists():
        print("  ⚠️  Security audit script not found (skipping)")
        return True

    result = subprocess.run(
        [sys.executable, str(audit_script)],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT
    )

    # Parse output for CRITICAL/HIGH counts
    output = result.stdout

    # Look for summary section
    if 'CRITICAL: 0' in output and 'HIGH: 0' in output:
        print("  ✅ Security audit: 0 CRITICAL, 0 HIGH")
        return True
    else:
        print("  ❌ Security audit found CRITICAL or HIGH issues")
        print(output)
        return False


def main():
    print("=" * 70)
    print("Security Regression Tests")
    print("=" * 70)

    tests = [
        test_no_hardcoded_passwords,
        test_database_permissions,
        test_backup_permissions,
        test_security_audit_passes,
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"  ❌ Test failed with exception: {e}")
            results.append(False)

    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} passed")
    print("=" * 70)

    if all(results):
        print("✅ All security tests passed!")
        return 0
    else:
        print("❌ Some security tests failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())

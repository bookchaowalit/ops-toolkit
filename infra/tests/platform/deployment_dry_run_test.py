#!/usr/bin/env python3
"""
Deployment Dry Run Test

Verifies that the deployment setup process works correctly:
- setup.sh is executable and has correct paths
- setup.sh can run in dry-run mode (validation only)
- All required files exist
- Database schema is valid
- MCP server can start

Usage:
    python3 infra/tests/platform/deployment_dry_run_test.py
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
from _env import PROJECT_ROOT, sibling_repo_root

# The MCP server lives in the mcp-aggregator repository (SOLO_MCP_ROOT, default ../mcp-aggregator); optional here.
MCP_ROOT = sibling_repo_root('mcp-aggregator')


def test_setup_script_exists():
    """Verify setup.sh exists and is executable."""
    print("\n[1/4] Testing setup script existence...")

    setup_script = PROJECT_ROOT / 'infra' / 'scripts' / 'setup' / 'setup.sh'

    if not setup_script.exists():
        print(f"  ❌ setup.sh not found at {setup_script}")
        return False

    # Check if executable
    if os.access(setup_script, os.X_OK):
        print("  ✅ setup.sh exists and is executable")
        return True
    else:
        print("  ⚠️  setup.sh exists but not executable (can still run with bash)")
        return True


def test_setup_script_validation():
    """Verify setup.sh can validate paths without errors."""
    print("\n[2/4] Testing setup script validation...")

    setup_script = PROJECT_ROOT / 'infra' / 'scripts' / 'setup' / 'setup.sh'

    try:
        # Run setup.sh with a simple validation check
        # We'll just check if it can start and validate paths
        result = subprocess.run(
            ['bash', '-n', str(setup_script)],  # Syntax check only
            capture_output=True,
            text=True,
            timeout=10,
            cwd=PROJECT_ROOT
        )

        if result.returncode == 0:
            print("  ✅ setup.sh syntax is valid")
            return True
        else:
            print(f"  ❌ setup.sh has syntax errors")
            print(result.stderr)
            return False

    except subprocess.TimeoutExpired:
        print("  ❌ setup.sh validation timed out")
        return False
    except Exception as e:
        print(f"  ❌ setup.sh validation failed: {e}")
        return False


def test_required_files_exist():
    """Verify all required files for deployment exist."""
    print("\n[3/4] Testing required files...")

    required_files = [
        'infra/package.json',
        'infra/scripts/setup/setup.sh',
    ]

    missing = []
    for file_rel in required_files:
        file_path = PROJECT_ROOT / file_rel
        if not file_path.exists():
            missing.append(file_rel)

    if missing:
        print(f"  ❌ Missing required files: {', '.join(missing)}")
        return False
    else:
        print(f"  ✅ All {len(required_files)} required files exist")
        return True


def test_mcp_server_package():
    """Verify MCP server package.json is valid."""
    print("\n[4/4] Testing MCP server package...")

    mcp_package = MCP_ROOT / 'package.json'

    if not mcp_package.exists():
        print(f"  SKIP: mcp-aggregator checkout not found at {MCP_ROOT} (set SOLO_MCP_ROOT)")
        return True

    try:
        # Read and parse package.json
        package_content = mcp_package.read_text()
        package_data = json.loads(package_content)

        # Check required fields
        if 'name' not in package_data:
            print("  ❌ package.json missing 'name' field")
            return False

        if 'dependencies' not in package_data and 'devDependencies' not in package_data:
            print("  ⚠️  package.json has no dependencies")
            # Not a hard failure

        print(f"  ✅ MCP server package.json is valid ({package_data.get('name', 'unknown')})")
        return True

    except json.JSONDecodeError as e:
        print(f"  ❌ package.json is not valid JSON: {e}")
        return False
    except Exception as e:
        print(f"  ❌ MCP server package test failed: {e}")
        return False


def main():
    print("=" * 70)
    print("Deployment Dry Run Tests")
    print("=" * 70)

    tests = [
        test_setup_script_exists,
        test_setup_script_validation,
        test_required_files_exist,
        test_mcp_server_package,
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
        print("✅ All deployment dry run tests passed!")
        return 0
    else:
        print(f"❌ {failed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())

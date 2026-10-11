#!/usr/bin/env python3
"""
Security Audit Script

Checks for common security issues in the root-owned runtime boundary:
- Hardcoded secrets in source code
- shell=True with unsanitized input
- Wildcard CORS
- Missing authentication
- Insecure file permissions

Usage:
    python3 security_audit.py              # Run all checks
    python3 security_audit.py --json       # JSON output
    python3 security_audit.py --fix        # Auto-fix safe issues
"""

import os
import re
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from _env import PROJECT_ROOT


GENERATED_ARTIFACT_MARKERS = (
    "storybook-static/",
    "infra/test-results/playwright/",
)

# Nested product repositories under projects/ are separate security
# boundaries. Their own CI/audit must own those findings; scanning them from
# this root audit both crosses the repository contract and makes the check
# non-deterministically slow.
SOURCE_SCAN_ROOT = PROJECT_ROOT / 'infra'


def is_generated_artifact(file_path: Path) -> bool:
    """Keep generated bundles and browser traces out of source-code scans."""
    relative = file_path.relative_to(PROJECT_ROOT).as_posix()
    return any(marker in relative for marker in GENERATED_ARTIFACT_MARKERS)


class SecurityFinding:
    def __init__(self, severity, category, file_path, line, description, recommendation):
        self.severity = severity  # CRITICAL, HIGH, MEDIUM, LOW
        self.category = category
        self.file_path = file_path
        self.line = line
        self.description = description
        self.recommendation = recommendation

    def to_dict(self):
        return {
            'severity': self.severity,
            'category': self.category,
            'file': str(self.file_path),
            'line': self.line,
            'description': self.description,
            'recommendation': self.recommendation,
        }


def check_hardcoded_secrets():
    """Check for hardcoded secrets in source code."""
    findings = []

    # Patterns that indicate secrets
    secret_patterns = [
        (r'api[_-]?key\s*=\s*["\'][a-zA-Z0-9]{20,}["\']', 'API key'),
        (r'token\s*=\s*["\'][a-zA-Z0-9]{20,}["\']', 'Token'),
        (r'secret\s*=\s*["\'][a-zA-Z0-9]{20,}["\']', 'Secret'),
        (r'password\s*=\s*["\'][^"\']{8,}["\']', 'Password'),
        (r'sk-[a-zA-Z0-9]{20,}', 'Stripe/OpenAI key'),
        (r'ghp_[a-zA-Z0-9]{36}', 'GitHub PAT'),
    ]

    # Directories to skip
    skip_dirs = ['node_modules', '__pycache__', '.venv', 'venv', '.android-sdk',
                 '.next', 'dist', 'build', '.git']

    # Files to check
    check_dirs = [
        PROJECT_ROOT / 'infra' / 'scripts',
        PROJECT_ROOT / 'infra' / 'api',
        PROJECT_ROOT / 'infra' / 'mcp-server',
    ]

    for check_dir in check_dirs:
        if not check_dir.exists():
            continue

        for ext in ['*.py', '*.ts', '*.js']:
            for file_path in check_dir.rglob(ext):
                # Skip third-party directories
                if is_generated_artifact(file_path) or any(skip_dir in str(file_path) for skip_dir in skip_dirs):
                    continue

                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            for pattern, secret_type in secret_patterns:
                                if re.search(pattern, line, re.IGNORECASE):
                                    # Skip if it's a variable reference or env var
                                    if 'os.environ' in line or 'process.env' in line or '${' in line:
                                        continue
                                    # Skip if it's a comment
                                    if line.strip().startswith('#') or line.strip().startswith('//'):
                                        continue

                                    findings.append(SecurityFinding(
                                        severity='HIGH',
                                        category='Secrets',
                                        file_path=file_path.relative_to(PROJECT_ROOT),
                                        line=line_num,
                                        description=f'Possible hardcoded {secret_type}',
                                        recommendation='Move to environment variable or secrets manager'
                                    ))
                except Exception:
                    pass

    return findings


def check_shell_injection():
    """Check for shell=True with unsanitized input."""
    findings = []

    # Directories to skip (third-party, virtual environments)
    skip_dirs = ['node_modules', '__pycache__', '.venv', 'venv', '.android-sdk',
                 '.next', 'dist', 'build', '.git']

    # Check Python files
    for file_path in SOURCE_SCAN_ROOT.rglob('*.py'):
        # Skip third-party directories
        if any(skip_dir in str(file_path) for skip_dir in skip_dirs):
            continue
        # The scanner contains these strings as detection rules; do not flag
        # its own implementation as an application-level injection finding.
        if file_path.resolve() == Path(__file__).resolve():
            continue

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

                # Check for shell=True
                if 'shell=True' in content:
                    # Check if it's using subprocess with string command
                    if re.search(r'subprocess\.(run|call|Popen)\([^,]+,\s*shell=True', content):
                        findings.append(SecurityFinding(
                            severity='MEDIUM',
                            category='Injection',
                            file_path=file_path.relative_to(PROJECT_ROOT),
                            line=0,  # Would need to parse to get exact line
                            description='subprocess with shell=True',
                            recommendation='Use shell=False with argument list, or sanitize input'
                        ))

                # Check for os.system
                if 'os.system(' in content:
                    findings.append(SecurityFinding(
                        severity='MEDIUM',
                        category='Injection',
                        file_path=file_path.relative_to(PROJECT_ROOT),
                        line=0,
                        description='os.system() usage',
                        recommendation='Use subprocess.run() with argument list instead'
                    ))
        except Exception:
            pass

    return findings


def check_wildcard_cors():
    """Check for wildcard CORS headers."""
    findings = []

    # Directories to skip
    skip_dirs = ['node_modules', '__pycache__', '.venv', '.next', 'dist', 'build']

    # Check TypeScript/JavaScript files
    for ext in ['*.ts', '*.js']:
        for file_path in (PROJECT_ROOT / 'infra').rglob(ext):
            # Skip third-party directories
            if is_generated_artifact(file_path) or any(skip_dir in str(file_path) for skip_dir in skip_dirs):
                continue

            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        if re.search(r'Access-Control-Allow-Origin.*\*', line):
                            findings.append(SecurityFinding(
                                severity='LOW',
                                category='CORS',
                                file_path=file_path.relative_to(PROJECT_ROOT),
                                line=line_num,
                                description='Wildcard CORS header',
                                recommendation='Restrict to specific origins in production'
                            ))
            except Exception:
                pass

    return findings


def check_file_permissions():
    """Check for insecure file permissions."""
    findings = []

    # Check .env file
    env_file = PROJECT_ROOT / 'infra' / '.env'
    if env_file.exists():
        stat = env_file.stat()
        mode = oct(stat.st_mode)[-3:]
        if mode != '600':
            findings.append(SecurityFinding(
                severity='MEDIUM',
                category='Permissions',
                file_path='infra/.env',
                line=0,
                description=f'.env has permissions {mode} (should be 600)',
                recommendation='Run: chmod 600 infra/.env'
            ))

    # Backups contain the complete local database and must be owner-only.
    backup_dir = PROJECT_ROOT / 'data' / 'backups'
    backup_files = []
    if backup_dir.exists():
        backup_files = [
            path for path in backup_dir.iterdir()
            if path.is_file() and path.name != '.gitkeep'
        ]
        if backup_files:
            dir_mode = oct(backup_dir.stat().st_mode)[-3:]
            if dir_mode != '700':
                findings.append(SecurityFinding(
                    severity='MEDIUM',
                    category='Permissions',
                    file_path='data/backups',
                    line=0,
                    description=f'Backup directory has permissions {dir_mode}',
                    recommendation='Run: chmod 700 data/backups'
                ))
    for backup_file in backup_files:
        file_mode = oct(backup_file.stat().st_mode)[-3:]
        if file_mode != '600':
            findings.append(SecurityFinding(
                severity='MEDIUM',
                category='Permissions',
                file_path=backup_file.relative_to(PROJECT_ROOT),
                line=0,
                description=f'Backup file has permissions {file_mode}',
                recommendation=f'chmod 600 {backup_file.relative_to(PROJECT_ROOT)}'
            ))

    return findings


def run_security_audit():
    """Run all security checks."""
    print("=" * 70)
    print("Security Audit")
    print("=" * 70)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Project: {PROJECT_ROOT}")
    print()

    all_findings = []

    # Run checks
    print("[1/4] Checking for hardcoded secrets...")
    secrets = check_hardcoded_secrets()
    all_findings.extend(secrets)
    print(f"  Found {len(secrets)} potential secrets")

    print("\n[2/4] Checking for shell injection...")
    injections = check_shell_injection()
    all_findings.extend(injections)
    print(f"  Found {len(injections)} potential injection points")

    print("\n[3/4] Checking for wildcard CORS...")
    cors = check_wildcard_cors()
    all_findings.extend(cors)
    print(f"  Found {len(cors)} wildcard CORS headers")

    print("\n[4/4] Checking file permissions...")
    perms = check_file_permissions()
    all_findings.extend(perms)
    print(f"  Found {len(perms)} permission issues")

    # Summary
    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)

    by_severity = {}
    for f in all_findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1

    for severity in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
        count = by_severity.get(severity, 0)
        print(f"  {severity}: {count}")

    print(f"\nTotal: {len(all_findings)} findings")

    # Print findings by severity
    if all_findings:
        print()
        print("=" * 70)
        print("Findings")
        print("=" * 70)

        for severity in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
            severity_findings = [f for f in all_findings if f.severity == severity]
            if not severity_findings:
                continue

            print(f"\n{severity} ({len(severity_findings)}):")
            for f in severity_findings[:10]:  # Limit to 10 per severity
                print(f"  [{f.category}] {f.file_path}:{f.line}")
                print(f"    {f.description}")
                print(f"    → {f.recommendation}")

            if len(severity_findings) > 10:
                print(f"  ... and {len(severity_findings) - 10} more")

    print("=" * 70)

    return all_findings


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Security audit')
    parser.add_argument('--json', action='store_true', help='JSON output')
    parser.add_argument('--fix', action='store_true', help='Auto-fix safe issues')

    args = parser.parse_args()

    findings = run_security_audit()

    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))

    if args.fix:
        print("\n🔧 Auto-fixing safe issues...")

        # Fix .env permissions
        env_file = PROJECT_ROOT / 'infra' / '.env'
        if env_file.exists():
            os.chmod(env_file, 0o600)
            print("  ✅ Fixed .env permissions (600)")

    # Exit with error if critical/high findings
    critical_high = [f for f in findings if f.severity in ['CRITICAL', 'HIGH']]
    if critical_high:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()

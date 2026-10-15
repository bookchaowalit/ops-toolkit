#!/usr/bin/env python3
"""Validate/install the local daily hub sync timer after a complete live check."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


WORKER = Path(__file__).with_name('org_hub_registry.py')
SERVICE = 'bookchaowalit-org-hub-sync.service'
TIMER = 'bookchaowalit-org-hub-sync.timer'
CALENDAR = '*-*-* 09:00:00 Asia/Bangkok'


def unit_quote(value: str, *, argument: bool = True) -> str:
    if any(c in value for c in '\n\r\x00'):
        raise ValueError('invalid_unit_path')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if argument:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def render_units(index: Path, checkouts: Path) -> dict[str, str]:
    args = [sys.executable, str(WORKER), '--index', str(index.resolve()),
            '--checkouts-root', str(checkouts.resolve()), '--apply', '--publish']
    command = ' '.join(unit_quote(arg) for arg in args)
    gh = shutil.which('gh')
    if not gh:
        raise ValueError('github_cli_missing')
    path = str(Path(gh).parent) + os.pathsep + os.defpath
    service = '\n'.join([
        '[Unit]', 'Description=Refresh Bookchaowalit organization hub registries',
        'After=network-online.target', 'Wants=network-online.target', '',
        '[Service]', 'Type=oneshot', 'UMask=0077', 'TimeoutStartSec=600',
        'WorkingDirectory=' + str(WORKER.parents[3]).replace('%', '%%'),
        'Environment=' + unit_quote('PATH=' + path, argument=False),
        'Environment=GIT_TERMINAL_PROMPT=0', 'Environment=GH_PROMPT_DISABLED=1',
        'Environment=TZ=Asia/Bangkok', 'ExecStart=' + command, '',
    ])
    timer = '\n'.join([
        '[Unit]', 'Description=Daily organization hub registry refresh at 09:00 Bangkok', '',
        '[Timer]', 'OnCalendar=' + CALENDAR, 'Persistent=true', 'Unit=' + SERVICE, '',
        '[Install]', 'WantedBy=timers.target', '',
    ])
    return {SERVICE: service, TIMER: timer}


def _run(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    runtime = Path('/run/user') / str(os.getuid())
    if not env.get('XDG_RUNTIME_DIR') and runtime.is_dir():
        env['XDG_RUNTIME_DIR'] = str(runtime)
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--checkouts-root', type=Path, required=True)
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.index.read_text())
        expected = len(data['organizations'])
        if expected < 1:
            raise ValueError('empty_hub_index')
        preview = _run([sys.executable, str(WORKER), '--index', str(args.index.resolve()),
                        '--checkouts-root', str(args.checkouts_root.resolve()), '--check'], timeout=180)
        report = json.loads(preview.stdout)
        if (preview.returncode or report.get('schema') != 'bookchaowalit.org-hub-sync.v1'
                or report.get('status') != 'unchanged' or report.get('blocked_targets') != 0
                or report.get('checked_organization_hubs') != expected
                or report.get('references_verified') != expected + 1):
            print(json.dumps({'activated': False, 'status': 'blocked', 'code': 'live_check_not_ready'}))
            return 2
        units = render_units(args.index, args.checkouts_root)
        with tempfile.TemporaryDirectory(prefix='org-hub-sync-units-') as directory:
            paths = []
            for name, content in units.items():
                path = Path(directory) / name
                path.write_text(content)
                paths.append(str(path))
            if _run(['systemd-analyze', '--user', 'verify', *paths], timeout=30).returncode:
                raise ValueError('invalid_units')
        if not args.activate:
            print(json.dumps({'activated': False, 'status': 'ready', 'calendar': CALENDAR}))
            return 0
        target = Path.home() / '.config/systemd/user'
        target.mkdir(parents=True, exist_ok=True)
        for name, content in units.items():
            path = target / name
            if path.is_symlink() or (path.exists() and path.read_text() != content):
                raise ValueError('existing_unit_conflict')
        for name, content in units.items():
            path = target / name
            with tempfile.NamedTemporaryFile(mode='w', dir=target, delete=False) as handle:
                pending = Path(handle.name)
                handle.write(content)
            try:
                pending.chmod(0o600)
                os.replace(pending, path)
            finally:
                pending.unlink(missing_ok=True)
        for command in [['systemctl', '--user', 'daemon-reload'],
                        ['systemctl', '--user', 'enable', '--now', TIMER]]:
            if _run(command).returncode:
                raise ValueError('timer_activation_failed')
        print(json.dumps({'activated': True, 'status': 'active', 'calendar': CALENDAR,
                          'runtime': 'local-user-systemd', 'timer': TIMER}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'activated': False, 'status': 'blocked', 'code': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

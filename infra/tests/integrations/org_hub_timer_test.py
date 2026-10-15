#!/usr/bin/env python3
"""A recurring writer may activate only after a complete live unchanged check."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[3] / 'infra/scripts/sync/install_org_hub_timer.py'


class HubTimerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('org_hub_timer', SCRIPT)
            cls.installer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.installer)

    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'The hub synchronization timer installer is not implemented')

    def attempt(self, report, returncode=0):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            index = home / 'empire/registry/hubs.json'
            index.parent.mkdir(parents=True)
            index.write_text(json.dumps({'organizations': [{'organization': 'test-org'}]}))
            calls = []
            def external(args, **kwargs):
                calls.append(args)
                if '--check' in args:
                    return subprocess.CompletedProcess(args, returncode, json.dumps(report), '')
                return subprocess.CompletedProcess(args, 0, '', '')
            output = io.StringIO()
            with patch.object(self.installer.Path, 'home', return_value=home), \
                 patch.object(self.installer.subprocess, 'run', side_effect=external), \
                 contextlib.redirect_stdout(output):
                code = self.installer.main(['--index', str(index), '--checkouts-root', str(home), '--activate'])
            installed = (home / '.config/systemd/user/bookchaowalit-org-hub-sync.timer').is_file()
            enabled = any(args[:4] == ['systemctl', '--user', 'enable', '--now'] for args in calls)
            return code, installed, enabled, json.loads(output.getvalue())

    def test_drift_or_blocked_preview_cannot_install_or_enable_timer(self):
        for status, code in [('drift', 1), ('blocked', 2)]:
            with self.subTest(status=status):
                result = self.attempt({'schema': 'bookchaowalit.org-hub-sync.v1', 'status': status,
                                       'blocked_targets': 0, 'checked_organization_hubs': 1,
                                       'references_verified': 2}, code)
                self.assertEqual(result[:3], (2, False, False))

    def test_zero_exit_with_wrong_coverage_or_schema_cannot_enable_timer(self):
        good = {'schema': 'bookchaowalit.org-hub-sync.v1', 'status': 'unchanged',
                'blocked_targets': 0, 'checked_organization_hubs': 1, 'references_verified': 2}
        for override in [{'checked_organization_hubs': 0}, {'schema': 'unrelated'},
                         {'blocked_targets': 1}, {'references_verified': 1}]:
            with self.subTest(override=override):
                result = self.attempt({**good, **override})
                self.assertEqual(result[:3], (2, False, False))

    def test_verified_live_report_allows_installation_and_activation(self):
        code, installed, enabled, report = self.attempt({
            'schema': 'bookchaowalit.org-hub-sync.v1', 'status': 'unchanged',
            'blocked_targets': 0, 'checked_organization_hubs': 1, 'references_verified': 2})
        self.assertEqual((code, installed, enabled), (0, True, True))
        self.assertTrue(report['activated'])

    def test_unit_arguments_reject_control_characters(self):
        for path in ['/tmp/new\nline/hubs.json', '/tmp/carriage\rreturn/hubs.json']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.installer.render_units(Path(path), Path('/tmp/checkouts'))

    def test_unit_verifier_gets_user_runtime_when_shell_omits_xdg_variable(self):
        captured = {}
        def external(args, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': ''}), \
             patch.object(self.installer.Path, 'is_dir', return_value=True), \
             patch.object(self.installer.subprocess, 'run', side_effect=external):
            self.installer._run(['systemd-analyze', '--user', 'verify', '/tmp/example.service'])
        self.assertEqual(captured.get('env', {}).get('XDG_RUNTIME_DIR'), '/run/user/' + str(os.getuid()))


if __name__ == '__main__':
    unittest.main(verbosity=2)

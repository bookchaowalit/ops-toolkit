#!/usr/bin/env python3
"""Offline tests: the client file switches the task backend identically for every process."""
import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'infra/scripts/utils')]
import task_store
import tasks_backend

KEYS = ('SOLO_EMPIRE_TASKS_BACKEND', 'EMPIRE_TASKS_API', 'EMPIRE_CLIENT_CONFIG')


class ClientConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / 'cfg'
        self.dir.mkdir(mode=0o700)
        self.file = self.dir / 'client.env'
        env = {k: v for k, v in os.environ.items() if k not in KEYS}
        env['EMPIRE_CLIENT_CONFIG'] = str(self.file)
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, text, mode=0o600):
        self.file.write_text(text)
        os.chmod(self.file, mode)

    def test_missing_switch_is_an_error_not_a_default(self):
        with self.assertRaises(task_store.ClientConfigError) as raised:
            task_store.client_settings()
        self.assertIn('not configured', str(raised.exception))
        self.assertNotIn('127.0.0.1', str(raised.exception))
        with self.assertRaises(task_store.ClientConfigError):
            task_store.open_task_store(None, 'x')

    def test_sqlite_backend_is_a_retirement_error(self):
        message = 'the SQLite task backend was retired on 2026-10-09; tasks live in the Tasks API'
        with patch.dict(os.environ, {'SOLO_EMPIRE_TASKS_BACKEND': 'sqlite', 'EMPIRE_TASKS_API': 'http://b.test'}):
            with self.assertRaisesRegex(task_store.ClientConfigError, message):
                task_store.client_settings()
            with self.assertRaisesRegex(task_store.ClientConfigError, message):
                task_store.open_task_store(None, 'x')
        self.write('SOLO_EMPIRE_TASKS_BACKEND=sqlite\n')
        with self.assertRaisesRegex(task_store.ClientConfigError, message):
            task_store.client_settings()
        self.assertFalse(hasattr(task_store, 'SqliteTaskStore'))

    def test_open_task_store_ignores_the_connection(self):
        self.write('SOLO_EMPIRE_TASKS_BACKEND=api\nEMPIRE_TASKS_API=https://tasks.example.com\n')
        with patch.dict(os.environ, {'EMPIRE_AUTOMATION_TOKEN_X': 't'}):
            store = task_store.open_task_store(object(), 'x')
        self.assertIsInstance(store, task_store.ApiTaskStore)

    def test_file_selects_api_and_ignores_other_keys(self):
        self.write('# c\n\nSOLO_EMPIRE_TASKS_BACKEND=api\nEMPIRE_TASKS_API="https://tasks.example.com"\nOTHER=x\n')
        settings = task_store.client_settings()
        self.assertEqual((settings['backend'], settings['api_url']), ('api', 'https://tasks.example.com'))
        with patch.dict(os.environ, {'EMPIRE_AUTOMATION_TOKEN_X': 't'}):
            store = task_store.open_task_store(None, 'x')
        self.assertIsInstance(store, task_store.ApiTaskStore)
        self.assertEqual(store.base_url, 'https://tasks.example.com')

    def test_env_beats_file(self):
        self.write('SOLO_EMPIRE_TASKS_BACKEND=api\nEMPIRE_TASKS_API=https://a.example.com\n')
        with patch.dict(os.environ, {'SOLO_EMPIRE_TASKS_BACKEND': 'api', 'EMPIRE_TASKS_API': 'http://127.0.0.1:3999'}):
            settings = task_store.client_settings()
        self.assertEqual((settings['backend'], settings['api_url'], settings['api_url_source']),
                         ('api', 'http://127.0.0.1:3999', 'env'))

    def test_empty_env_does_not_win(self):
        self.write('SOLO_EMPIRE_TASKS_BACKEND=api\nEMPIRE_TASKS_API=https://a.example.com\n')
        with patch.dict(os.environ, {'SOLO_EMPIRE_TASKS_BACKEND': ''}):
            self.assertEqual(task_store.client_settings()['backend'], 'api')

    def test_bad_permissions_refused(self):
        for mode in (0o640, 0o604, 0o620, 0o666):
            self.write('SOLO_EMPIRE_TASKS_BACKEND=api\nEMPIRE_TASKS_API=https://a.example.com\n', mode)
            with self.assertRaises(task_store.ClientConfigError):
                task_store.client_settings()
            with self.assertRaises(task_store.ClientConfigError):
                task_store.open_task_store(None, 'x')

    def test_api_without_url_refused(self):
        self.write('SOLO_EMPIRE_TASKS_BACKEND=api\n')
        with self.assertRaises(task_store.ClientConfigError):
            task_store.client_settings()
        self.write('SOLO_EMPIRE_TASKS_BACKEND=api\nEMPIRE_TASKS_API=ftp://x\n')
        with self.assertRaises(task_store.ClientConfigError):
            task_store.client_settings()

    def test_non_https_url_refused_unless_loopback(self):
        bad = ('http://tasks.example.com', 'http://10.0.0.5:3601', 'http://127.0.0.1@evil.example.com',
               'http://localhost.evil.example.com', 'https://', 'http://', 'ws://127.0.0.1')
        for url in bad:
            self.write(f'SOLO_EMPIRE_TASKS_BACKEND=api\nEMPIRE_TASKS_API={url}\n')
            with self.assertRaises(task_store.ClientConfigError, msg=url):
                task_store.client_settings()
            self.file.unlink()
            with patch.dict(os.environ, {'SOLO_EMPIRE_TASKS_BACKEND': 'api', 'EMPIRE_TASKS_API': url}):
                with self.assertRaises(task_store.ClientConfigError, msg=url):
                    task_store.client_settings()
            code, _, err = self.run_cli('use-api', '--url', url)
            self.assertEqual(code, 2, url)
            self.assertFalse(self.file.exists())
        for url in ('http://127.0.0.1:3601', 'http://[::1]:3601', 'http://localhost:3601', 'https://tasks.example.com'):
            with patch.dict(os.environ, {'SOLO_EMPIRE_TASKS_BACKEND': 'api', 'EMPIRE_TASKS_API': url}):
                self.assertEqual(task_store.client_settings()['api_url'], url)
            self.assertEqual(self.run_cli('use-api', '--url', url)[0], 0, url)

    def test_api_store_never_forwards_the_token_on_redirect(self):
        import http.server
        import threading
        seen = []

        class Target(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

        target = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Target)

        class Redirect(Target):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{target.server_port}/x')
                self.end_headers()

        origin = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        for server in (target, origin):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
        store = task_store.ApiTaskStore(f'http://127.0.0.1:{origin.server_port}', 'bearer-secret', timeout=5)
        with self.assertRaises(task_store.TaskStoreError) as caught:
            store._call('GET', '/v1/tasks/number/1')
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(seen, [])

    def test_token_lookup_order_and_mode_check(self):
        repo, home = Path(self.tmp.name) / 'repo-tokens', Path(self.tmp.name) / 'home'
        (home / '.config/empire-platform/tokens').mkdir(parents=True, mode=0o700)
        home_file = home / '.config/empire-platform/tokens/probe.jwt'
        repo.mkdir(mode=0o700)
        with patch.object(task_store, 'TOKEN_DIR', repo), patch.dict(os.environ, {'HOME': str(home)}):
            with self.assertRaises(RuntimeError):
                task_store.automation_token('probe')
            home_file.write_text('home-token\n')
            os.chmod(home_file, 0o600)
            self.assertEqual(task_store.automation_token('probe'), 'home-token')
            (repo / 'probe.jwt').write_text('repo-token')
            os.chmod(repo / 'probe.jwt', 0o600)
            self.assertEqual(task_store.automation_token('probe'), 'repo-token')  # repo dir wins
            (repo / 'probe.jwt').unlink()
            for mode in (0o640, 0o604, 0o644):
                os.chmod(home_file, mode)
                with self.assertRaises(RuntimeError) as caught:
                    task_store.automation_token('probe')
                self.assertNotIn('home-token', str(caught.exception))
            with patch.dict(os.environ, {'EMPIRE_AUTOMATION_TOKEN_PROBE': 'env-token'}):
                self.assertEqual(task_store.automation_token('probe'), 'env-token')

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tasks_backend.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_cli_round_trip(self):
        code, _, err = self.run_cli('status')
        self.assertEqual(code, 2)
        self.assertIn('not configured', err)
        code, out, _ = self.run_cli('use-api', '--url', 'https://tasks.example.com')
        self.assertEqual(code, 0)
        self.assertIn('backend=api', out)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)
        self.assertEqual([p.name for p in self.dir.iterdir()], ['client.env'])
        code, out, _ = self.run_cli('status')
        self.assertEqual(code, 0)
        self.assertIn('backend=api (file)', out)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli('use-sqlite')  # rollback to SQLite is retired; the subcommand no longer exists

    def test_cli_creates_private_dir_and_rejects_bad_url(self):
        nested = Path(self.tmp.name) / 'new' / 'client.env'
        with patch.dict(os.environ, {'EMPIRE_CLIENT_CONFIG': str(nested)}):
            self.assertEqual(self.run_cli('use-api', '--url', 'http://127.0.0.1:3601')[0], 0)
            self.assertEqual(stat.S_IMODE(nested.parent.stat().st_mode), 0o700)
            self.assertEqual(self.run_cli('use-api', '--url', 'nope')[0], 2)

    def test_cli_status_never_prints_token(self):
        with patch.dict(os.environ, {'EMPIRE_AUTOMATION_TOKEN_X': 'secret-token-value'}):
            _, out, err = self.run_cli('status')
        self.assertNotIn('secret-token-value', out + err)


if __name__ == '__main__':
    unittest.main()

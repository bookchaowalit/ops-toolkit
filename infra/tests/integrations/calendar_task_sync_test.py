#!/usr/bin/env python3
"""Offline regression tests for deadline projections, appointments and reminders."""
import copy
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace
from contextlib import redirect_stdout, closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'infra/scripts/sync'), str(ROOT / 'infra/scripts/utils')]
sys.path.insert(0, str(ROOT / 'infra/tests/support'))
from sqlite_task_store import SqliteTaskStore, apply_legacy_task_tables
from sqlite_notifications_store import SqliteNotificationsStore
import sync_google_calendar as calendar
import sync_tasks_to_calendar as deadlines
import notification_runner as notifications
import task_calendar_health as health
import tasks_view


def database():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    # task_calendar_health still reads calendar_events (owned by the utils agent); remove with it.
    conn.executescript((ROOT / 'infra/tests/support/legacy_calendar_tables.sql').read_text())
    apply_legacy_task_tables(conn)  # the retired task tables live only in the test support DDL
    # cron_jobs lives in the automation cell (migration 085); its legacy DDL serves the SQLite double.
    conn.executescript((ROOT / 'infra/tests/support/legacy_operating_intake_automation_tables.sql').read_text())
    return conn


class CalendarTaskSyncTest(unittest.TestCase):
    def http_error(self, code):
        error = urllib.error.HTTPError('test', code, '', {}, None)
        self.addCleanup(error.close)
        return error

    def setUp(self):
        self.conn = database()
        self.addCleanup(self.conn.close)
        # Tasks live in the Tasks API in production; these tests run the same code on a SQLite double.
        for module in (notifications, health, tasks_view, deadlines):
            patcher = patch.object(module, 'open_task_store',
                                   lambda c, automation, channel='workflow': SqliteTaskStore(c if c is not None else self.conn))
            patcher.start()
            self.addCleanup(patcher.stop)
        # Notifications live in the notifications cell in production; same SQLite double approach.
        self.notifications_store = SqliteNotificationsStore(self.conn)
        for module in (notifications, health):
            patcher = patch.object(module, 'open_notifications_store', lambda *_a, **_k: self.notifications_store)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.task = dict(id=7, title='Example', status='todo', priority=2,
                         due_date='2026-09-09', calendar_event_id='event', archived_at=None)
        self.event = deadlines.build_event(self.task)
        self.event.update(id='event', etag='version-1')

    def test_offset_and_floating_timezone(self):
        self.assertEqual(calendar.event_time({'dateTime': '2026-09-09T09:00:00+07:00'}),
                         '2026-09-09T09:00:00+07:00')
        self.assertEqual(calendar.event_time({'dateTime': '2026-09-09T02:00:00Z'}),
                         '2026-09-09T02:00:00+00:00')
        self.assertTrue(calendar.event_time({'dateTime': '2026-09-09T09:00:00',
                                            'timeZone': 'Asia/Bangkok'}).endswith('+07:00'))
        with self.assertRaises(KeyError):
            calendar.event_time({'dateTime': '2026-09-09T09:00:00'})

    def test_pagination_including_empty_first_page(self):
        with patch.object(calendar, '_api_request', side_effect=[
            {'items': [], 'nextPageToken': 'page2'}, {'items': [{'id': 'second'}]}
        ]) as request:
            self.assertEqual(calendar.fetch_calendar_events('fake'), [{'id': 'second'}])
            self.assertIn('pageToken=page2', request.call_args.args[0])
            self.assertIn('showDeleted=true', request.call_args.args[0])

    def test_repeated_page_token_fails(self):
        with patch.object(calendar, '_api_request', return_value={'nextPageToken': 'same'}):
            with self.assertRaises(RuntimeError):
                calendar.fetch_calendar_events('fake')

    def test_projection_is_transparent_and_not_a_timed_appointment(self):
        self.assertEqual(self.event['start'], {'date': '2026-09-09'})
        self.assertEqual(self.event['end'], {'date': '2026-09-10'})
        self.assertEqual(self.event['transparency'], 'transparent')
        self.assertEqual(self.event['reminders']['overrides'], [])
        self.assertIn('Priority: P2', self.event['description'])

    def test_mutation_sends_conditional_header(self):
        with patch.object(deadlines.urllib.request, 'urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = b'{}'
            deadlines.mutate('fake', 'PATCH', {}, 'event', 'version-1')
            self.assertEqual(request.call_args.args[0].get_header('If-match'), 'version-1')
            self.assertEqual(request.call_args.kwargs['timeout'], 30)

    def test_archived_tasks_are_not_export_candidates(self):
        self.conn.executemany('INSERT INTO tasks(title,status,due_date,archived_at) VALUES (?,?,?,?)', [
            ('Active','todo','2026-09-09',None),
            ('Archived','todo','2026-09-09','2026-01-01')])
        self.conn.execute("""UPDATE tasks SET external_source='leantime' WHERE title='Archived'""")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.db'
            target = sqlite3.connect(path)
            self.conn.commit()
            self.conn.backup(target)
            target.close()
            def connect(**kwargs):
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                return conn
            with patch.object(deadlines, 'open_task_store',
                              lambda c, automation, channel='workflow': SqliteTaskStore(connect())):
                self.assertEqual(len(deadlines.get_tasks_to_sync()), 1)
            with patch.object(tasks_view, 'open_task_store',
                              lambda c, automation, channel='workflow': SqliteTaskStore(connect())):
                self.assertEqual(len(tasks_view.load_tasks()), 1)

    def test_leantime_tasks_stay_out_of_local_alerts(self):
        self.conn.execute("""INSERT INTO tasks(title,status,priority,due_date,external_source,updated_at)
            VALUES ('Employer','todo',1,'2000-01-01','leantime','2000-01-01')""")
        self.assertEqual(notifications.check_overdue({}), [])
        self.assertEqual(notifications.check_high_priority({}), [])
        self.assertEqual(notifications.check_stale({}), [])
        self.assertEqual(notifications.check_blocked({}), [])

    def test_remote_moves_and_timed_conversion_are_conflicts(self):
        for start in ({'date': '2026-09-10'}, {'dateTime': '2026-09-09T12:00:00+07:00'}):
            event = copy.deepcopy(self.event)
            event['start'] = start
            with patch.object(deadlines, '_api_request', return_value=event), \
                 patch.object(deadlines, 'mutate') as mutate, \
                 patch.object(deadlines, 'save_event_id') as save:
                self.assertEqual(deadlines.sync_task('fake', self.task), 'conflicts')
                mutate.assert_not_called()
                save.assert_not_called()

    def test_local_due_date_change_uses_etag(self):
        self.task['due_date'] = '2026-09-15'
        with patch.object(deadlines, '_api_request', return_value=self.event), \
             patch.object(deadlines, 'mutate') as mutate:
            self.assertEqual(deadlines.sync_task('fake', self.task), 'updated')
            self.assertEqual(mutate.call_args.args[2]['start']['date'], '2026-09-15')
            self.assertEqual(mutate.call_args.args[-1], 'version-1')

    def test_delete_failure_retains_link(self):
        self.task['status'] = 'done'
        with patch.object(deadlines, '_api_request', return_value=self.event), \
             patch.object(deadlines, 'mutate', side_effect=self.http_error(503)), \
             patch.object(deadlines, 'save_event_id') as save:
            with self.assertRaises(urllib.error.HTTPError):
                deadlines.sync_task('fake', self.task)
            save.assert_not_called()

    def test_already_deleted_cleanup_clears_link(self):
        self.task['status'] = 'done'
        with patch.object(deadlines, '_api_request', side_effect=self.http_error(410)), \
             patch.object(deadlines, 'save_event_id') as save:
            self.assertEqual(deadlines.sync_task('fake', self.task), 'deleted')
            save.assert_called_once_with(7, None)

    def test_create_retry_adopts_only_owned_event(self):
        self.task['calendar_event_id'] = None
        with patch.object(deadlines, 'mutate', side_effect=self.http_error(409)), \
             patch.object(deadlines, '_api_request', return_value=self.event), \
             patch.object(deadlines, 'save_event_id') as save:
            self.assertEqual(deadlines.sync_task('fake', self.task), 'created')
            save.assert_called_once_with(7, 'event')

    def test_preview_never_resolves_credentials(self):
        with patch.object(sys, 'argv', ['sync']), \
             patch.object(deadlines, 'get_tasks_to_sync', return_value=[self.task]), \
             patch.object(deadlines, 'get_access_token') as token, redirect_stdout(io.StringIO()):
            self.assertEqual(deadlines.main(), 0)
            token.assert_not_called()

    def test_conflict_exits_nonzero(self):
        with patch.object(sys, 'argv', ['sync','--apply']), \
             patch.object(deadlines, 'get_tasks_to_sync', return_value=[self.task]), \
             patch.object(deadlines, 'get_access_token', return_value='fake'), \
             patch.object(deadlines, 'sync_task', return_value='conflicts'), redirect_stdout(io.StringIO()):
            self.assertEqual(deadlines.main(), 1)

    def test_archived_tasks_excluded_from_all_notification_handlers(self):
        self.conn.execute("""INSERT INTO tasks (title,status,priority,due_date,updated_at,archived_at,reminder_at)
            VALUES ('Archived','todo',1,'2000-01-01','2000-01-01','2026-01-01','2000-01-01')""")
        for handler in notifications.RULE_HANDLERS.values():
            self.assertEqual(handler({}), [])

    def test_daily_notification_dedup_but_changed_deadline_can_notify(self):
        n = dict(task_id=1, title='Fixture', message='Fixture', notification_type='overdue',
                 severity='warning', task_title='Fixture', task_status='todo', task_priority=2,
                 task_due_date='2000-01-01', task_project=None)
        self.assertEqual(notifications.log_notifications(1, [n]), 1)
        self.assertEqual(notifications.log_notifications(1, [n]), 0)
        n['task_due_date'] = '2000-01-02'
        self.assertEqual(notifications.log_notifications(1, [n]), 1)
        entries = self.notifications_store.notifications()
        self.assertEqual(len(entries), 2)
        self.assertIsNone(entries[0]['delivered_at'])
        self.assertEqual(entries[0]['channel'], 'console')

    def test_runner_queues_through_rules_and_stamps_them(self):
        self.conn.execute("INSERT INTO tasks(title,status,priority,due_date) VALUES ('Late','todo',2,'2000-01-01')")
        overdue = self.notifications_store.create_rule({'name': 'Overdue', 'rule_type': 'overdue', 'config': {}})
        blocked = self.notifications_store.create_rule({'name': 'Blocked', 'rule_type': 'blocked', 'config': {}})
        self.notifications_store.create_rule({'name': 'Off', 'rule_type': 'stale', 'is_active': False})
        with redirect_stdout(io.StringIO()):
            notifications._run(True, False, None)  # dry-run writes nothing
            self.assertEqual(self.notifications_store.notifications(), [])
            self.assertIsNone(self.notifications_store.rule(overdue['id'])['last_checked_at'])
        with redirect_stdout(io.StringIO()) as out:
            notifications._run(False, False, None)
            notifications._run(False, False, None)  # the same Bangkok-day snapshot is not queued again
        entries = self.notifications_store.notifications()
        self.assertEqual([(e['notification_type'], e['rule_id'], e['channel']) for e in entries],
                         [('overdue', overdue['id'], 'console')])
        self.assertIsNotNone(self.notifications_store.rule(overdue['id'])['last_triggered_at'])
        self.assertIsNotNone(self.notifications_store.rule(blocked['id'])['last_checked_at'])
        self.assertIsNone(self.notifications_store.rule(blocked['id'])['last_triggered_at'])
        self.assertIn('Logged 0 notifications', out.getvalue())

    def test_runner_summary_and_unavailable_service(self):
        self.notifications_store.create_rule({'name': 'Overdue', 'rule_type': 'overdue'})
        self.notifications_store.queue({'title': 't', 'message': 'm', 'notification_type': 'overdue', 'severity': 'warning'})
        with redirect_stdout(io.StringIO()) as out:
            notifications._run(False, True, None)
        self.assertIn('Total notifications logged: 1', out.getvalue())
        self.assertIn('overdue: 1', out.getvalue())
        self.assertIn('warning: 1', out.getvalue())
        self.assertIn('Overdue (overdue) — last checked: never', out.getvalue())
        error = notifications.NotificationsStoreError(503, 'unavailable')
        with patch.object(notifications, 'open_notifications_store', side_effect=error), \
             patch.object(sys, 'argv', ['notification_runner.py']), redirect_stdout(io.StringIO()), \
             self.assertRaises(SystemExit) as raised:
            notifications.main()
        self.assertEqual(raised.exception.code, 1)

    def test_health_reports_delivery_and_degrades_without_service(self):
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        self.notifications_store.queue({'title': 'a', 'message': 'm', 'notification_type': 'x', 'sent_at': now})
        sent = self.notifications_store.queue({'title': 'b', 'message': 'm', 'notification_type': 'x', 'sent_at': now})[1]
        self.notifications_store.record_delivery(sent['id'], 'delivered', channel='telegram')
        failed = self.notifications_store.queue({'title': 'c', 'message': 'm', 'notification_type': 'x', 'sent_at': now})[1]
        self.notifications_store.record_delivery(failed['id'], 'failed', error='boom')
        self.assertEqual(health.notification_delivery_7d(),
                         {'logged': 3, 'delivered': 1, 'failed': 1, 'console_only': 2})
        with patch.object(health, 'open_notifications_store', side_effect=health.ClientConfigError('off')):
            self.assertEqual(health.notification_delivery_7d()['delivered'], 'unavailable')

    def test_explicit_reminders_are_consumed_after_local_log(self):
        self.conn.execute("""INSERT INTO tasks(title,status,priority,reminder_at,reminder_sent)
            VALUES ('Reminder','todo',2,'2000-01-01',0)""")
        findings = notifications.check_reminder({})
        self.assertEqual(len(findings), 1)
        self.assertEqual(notifications.log_notifications(1, findings), 1)
        self.assertEqual(notifications.mark_explicit_reminders_sent(findings), 1)
        self.assertEqual(self.conn.execute("SELECT reminder_sent FROM tasks").fetchone()[0], 1)

    def test_undated_backlog_does_not_generate_priority_or_stale_alerts(self):
        self.conn.execute("INSERT INTO tasks(title,status,priority,updated_at) VALUES ('Backlog','todo',1,'2000-01-01')")
        self.assertEqual(notifications.check_high_priority({}), [])
        self.assertEqual(notifications.check_stale({}), [])
        self.conn.execute("UPDATE tasks SET status='in_progress'")
        self.assertEqual(len(notifications.check_high_priority({})), 1)
        self.assertEqual(len(notifications.check_stale({})), 1)

    def test_empty_sync_status_and_failed_attempt_preserve_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(calendar, 'STATUS_PATH', Path(directory) / 'status.json'), \
                 patch.object(calendar, 'read_sync_status', side_effect=lambda: json.loads(calendar.STATUS_PATH.read_text()) if calendar.STATUS_PATH.exists() else {}):
                calendar.record_status('success', 0)
                first = json.loads(calendar.STATUS_PATH.read_text())
                calendar.record_status('error', error_type='HTTP_403')
                second = json.loads(calendar.STATUS_PATH.read_text())
                self.assertEqual(second['last_success_at'], first['last_success_at'])
                self.assertEqual(first['events_found'], 0)

    def test_secret_errors_use_public_health_categories(self):
        self.assertEqual(calendar.safe_error_type(ValueError('Required env var GOOGLE_CLIENT_ID is not set')),
                         'credentials_not_configured')
        self.assertEqual(calendar.safe_error_type(RuntimeError('provider timeout')), 'RuntimeError')

    def test_health_excludes_archived_tasks_and_blocked_dependencies(self):
        self.conn.executemany('INSERT INTO tasks(id,title,status,priority,archived_at) VALUES (?,?,?,?,?)', [
            (1,'One','todo',1,None), (2,'Two','blocked',1,None), (3,'Old','todo',1,'2026-01-01')])
        self.conn.execute('INSERT INTO task_dependencies(task_id,depends_on_task_id) VALUES (1,2)')
        with patch.object(health, 'read_sync_status', return_value={}):
            report = health.collect_health()
        self.assertEqual(report['tasks']['active'], 2)
        self.assertEqual(report['suggested_focus_task_ids'], [])
        self.assertEqual(report['calendar']['freshness'], 'unverified')


if __name__ == '__main__':
    unittest.main()

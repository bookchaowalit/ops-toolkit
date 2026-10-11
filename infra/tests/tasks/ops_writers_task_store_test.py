#!/usr/bin/env python3
"""Offline tests: ops task writers go through task_store (SQLite default + injected fake store)."""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'infra/scripts/sync'), str(ROOT / 'infra/scripts/utils')]
os.environ.pop('SOLO_EMPIRE_TASKS_BACKEND', None)
sys.path.insert(0, str(ROOT / 'infra' / 'tests' / 'support'))
from sqlite_task_store import SqliteTaskStore  # noqa: E402  (also isolates the test from the live Tasks API)
import notification_runner as notifications
import reconcile_overdue_tasks as reconcile
import recurring_gen
import sync_tasks_to_calendar as calendar_sync

# Production is API-only; the default-path tests run on the SQLite double over their temp database.
for _module in (notifications, reconcile, recurring_gen, calendar_sync):
    _module.open_task_store = lambda c, automation, **_kw: SqliteTaskStore(c)

SCHEMA = (ROOT / 'infra' / 'tests' / 'support' / 'sqlite_tasks_schema.sql')


def database(path=':memory:'):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    return conn


def record(number, **fields):
    base = dict(id=number, title=f'Task {number}', description=None, status='todo', priority=3, project=None,
                tags=[], category=None, due_date=None, completed_at=None, notes=None, external_source=None,
                external_id=None, external_url=None, estimated_minutes=None, reminder_at=None,
                reminder_sent=False, reminder_channels=None, calendar_event_id=None, recurrence_rule=None,
                archived_at=None, is_recurring_instance=False, recurrence_occurrence=None, created_at=None,
                updated_at=None)
    return {**base, **fields}


class FakeTaskStore:
    """Records calls and returns canned records."""

    def __init__(self, records=()):
        self.records = list(records)
        self.calls = []

    def _log(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def names(self):
        return [call[0] for call in self.calls]

    def get(self, number):
        self._log('get', number)
        return next((r for r in self.records if r['id'] == number), None)

    def list(self, **kwargs):
        self._log('list', **kwargs)
        return list(self.records)

    def update(self, number, fields, *, legacy=None):
        self._log('update', number, fields, legacy=legacy)

    def complete(self, number, *, legacy=None):
        self._log('complete', number, legacy=legacy)

    def archive(self, number, reason):
        self._log('archive', number, reason)

    def upsert_external(self, source, external_id, fields, *, legacy=None):
        self._log('upsert_external', source, external_id, fields, legacy=legacy)
        return 'created', 1

    def record_reminder_delivered(self, number, reminder_at, channel):
        self._log('record_reminder_delivered', number, reminder_at, channel)
        return True

    def generate_occurrences(self, number, until):
        self._log('generate_occurrences', number, until=until)
        return []


def quiet(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.db = database()
        self.addCleanup(self.db.close)
        self.statements = []
        self.db.set_trace_callback(self.statements.append)

    def test_archive_only_archives_and_never_deletes(self):
        self.db.execute("INSERT INTO tasks(title,status,completed_at) VALUES ('old','done','2000-01-01 00:00:00')")
        self.db.execute("INSERT INTO tasks(title,status,completed_at) VALUES ('new','done',datetime('now'))")
        self.assertEqual(quiet(reconcile.auto_cleanup, self.db, True), 1)
        rows = {r['title']: r for r in self.db.execute('SELECT * FROM tasks')}
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows['old']['archived_at'])
        self.assertEqual(rows['old']['archive_reason'], 'completed more than 7 days ago')
        self.assertIsNone(rows['new']['archived_at'])
        self.assertFalse([s for s in self.statements if 'DELETE' in s.upper()])
        self.assertEqual(quiet(reconcile.auto_cleanup, self.db, True), 0)  # already archived

    def test_dry_run_writes_nothing(self):
        self.db.execute("INSERT INTO tasks(title,status,completed_at) VALUES ('old','done','2000-01-01 00:00:00')")
        self.assertEqual(quiet(reconcile.auto_cleanup, self.db, False), 1)
        self.assertIsNone(self.db.execute('SELECT archived_at FROM tasks').fetchone()[0])

    def test_overdue_pull_and_import_in_sqlite(self):
        self.db.execute("INSERT INTO tasks(title,status,due_date,external_source,external_id) "
                        "VALUES ('todoist','todo','2000-01-01','todoist','t1')")
        self.db.execute("INSERT INTO tasks(title,status,due_date) VALUES ('local','todo','2000-01-01')")
        self.db.execute("INSERT INTO tasks(title,status,external_source,external_id) "
                        "VALUES ('gone','todo','todoist','t2')")
        with patch.object(reconcile, 'todoist_update'), patch.object(reconcile, 'REQUEST_DELAY', 0):
            self.assertEqual(quiet(reconcile.reconcile_overdue, self.db, True, False, False), (2, 0))
        rows = {r['title']: r for r in self.db.execute('SELECT * FROM tasks')}
        self.assertEqual(rows['todoist']['due_date'], f'{reconcile.TODAY}T00:00:00Z')
        self.assertEqual(rows['todoist']['sync_status'], 'synced')
        self.assertEqual(rows['local']['due_date'], reconcile.TODAY)
        active = [{'id': 't1', 'content': 'kept'}, {'id': 't3', 'content': 'brand new', 'description': 'd',
                                                   'priority': 4, 'labels': ['a', 'b'], 'due': {'date': '2030-01-01'}}]
        with patch.object(reconcile, 'fetch_all_todoist_tasks', return_value=active):
            self.assertEqual(quiet(reconcile.pull_completed, self.db, True), 1)
            self.assertEqual(quiet(reconcile.push_new_tasks, self.db, True), 1)
        rows = {r['title']: r for r in self.db.execute('SELECT * FROM tasks')}
        self.assertEqual(rows['gone']['status'], 'done')
        self.assertIsNotNone(rows['gone']['completed_at'])
        self.assertEqual(rows['gone']['sync_status'], 'synced')
        new = rows['brand new']
        self.assertEqual((new['tags'], new['external_id'], new['external_source'], new['sync_status'], new['priority'],
                          new['due_date'], new['status']), ('a,b', 't3', 'todoist', 'synced', 4, '2030-01-01', 'todo'))

    def test_fake_store_calls(self):
        old = record(5, status='done', completed_at='2000-01-01 00:00:00', project='p')
        fake = FakeTaskStore([old, record(6, status='done', completed_at='2999-01-01 00:00:00')])
        with patch.object(reconcile, 'open_task_store', return_value=fake) as opened:
            self.assertEqual(quiet(reconcile.auto_cleanup, self.db, True), 1)
        self.assertEqual(opened.call_args.args[1], 'overdue-reconciler')
        self.assertEqual([c for c in fake.calls if c[0] != 'list'],
                         [('archive', (5, 'completed more than 7 days ago'), {})])
        self.assertEqual(fake.calls[0], ('list', (), {'status': ['done']}))
        self.assertNotIn('delete', ' '.join(fake.names()))

    def test_fake_store_overdue_and_import(self):
        overdue = record(7, due_date='2000-01-01', external_source='todoist', external_id='t7')
        fake = FakeTaskStore([overdue])
        with patch.object(reconcile, 'open_task_store', return_value=fake), \
             patch.object(reconcile, 'todoist_update'), patch.object(reconcile, 'REQUEST_DELAY', 0):
            quiet(reconcile.reconcile_overdue, self.db, True, True, False)
            with patch.object(reconcile, 'fetch_all_todoist_tasks', return_value=[{'id': 'n1', 'content': 'x'}]):
                quiet(reconcile.push_new_tasks, self.db, True)
        updates = [c for c in fake.calls if c[0] in ('update', 'upsert_external')]
        self.assertEqual(updates[0], ('update', (7, {'due_date': f'{reconcile.TODAY}T00:00:00Z'}),
                                      {'legacy': {'sync_status': 'synced'}}))
        self.assertEqual(updates[1][0], 'upsert_external')
        self.assertEqual(updates[1][1][:2], ('todoist', 'n1'))
        self.assertEqual(updates[1][2]['legacy'], {'sync_status': 'synced'})


class NotificationReminderTest(unittest.TestCase):
    def test_reminder_sqlite_marks_sent_once(self):
        db = database()
        self.addCleanup(db.close)
        opener = patch.object(notifications, 'open_task_store', lambda c, automation, **_kw: SqliteTaskStore(db))
        opener.start()
        self.addCleanup(opener.stop)
        db.execute("INSERT INTO tasks(title,status,reminder_at,reminder_sent) VALUES ('due','todo','2000-01-01 00:00:00',0)")
        db.execute("INSERT INTO tasks(title,status,reminder_at,reminder_sent) VALUES ('done','done','2000-01-01 00:00:00',0)")
        db.execute("INSERT INTO tasks(title,status,reminder_at,reminder_sent,external_source) "
                   "VALUES ('lt','todo','2000-01-01 00:00:00',0,'leantime')")
        db.execute("INSERT INTO tasks(title,status,reminder_at,reminder_sent) VALUES ('future','todo','2999-01-01 00:00:00',0)")
        findings = notifications.check_reminder({})
        self.assertEqual([f['task_title'] for f in findings], ['due'])
        self.assertEqual(notifications.mark_explicit_reminders_sent(findings), 1)
        self.assertEqual({r[0]: r[1] for r in db.execute('SELECT title, reminder_sent FROM tasks')},
                         {'due': 1, 'done': 0, 'lt': 0, 'future': 0})
        self.assertEqual(notifications.check_reminder({}), [])

    def test_fake_store_calls(self):
        fake = FakeTaskStore([
            record(1, reminder_at='2000-01-01 00:00:00'),
            record(2, reminder_at='2000-01-01 00:00:00', status='done'),
            record(3, reminder_at='2000-01-01 00:00:00', external_source='leantime'),
        ])
        with patch.object(notifications, 'open_task_store', return_value=fake) as opened:
            findings = notifications.check_reminder({})
            self.assertEqual(notifications.mark_explicit_reminders_sent(findings), 1)
        self.assertEqual(opened.call_args.args[1], 'reminder-worker')
        self.assertEqual([f['task_id'] for f in findings], [1])
        self.assertIn('reminder_due_before', fake.calls[0][2])
        self.assertEqual(fake.calls[-1], ('record_reminder_delivered', (1, '2000-01-01 00:00:00', 'console'), {}))

    def test_fake_store_overdue_filters(self):
        fake = FakeTaskStore([record(1, due_date='2000-01-01', priority=1), record(2, due_date='2000-01-01', status='done'),
                              record(3, due_date='2000-01-01', external_source='leantime')])
        with patch.object(notifications, 'open_task_store', return_value=fake):
            found = notifications.check_overdue({})
        self.assertEqual([(n['task_id'], n['severity']) for n in found], [(1, 'critical')])
        self.assertIn('due_before', fake.calls[0][2])


class CalendarSyncTest(unittest.TestCase):
    def test_sqlite_read_and_save_event_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.db'
            seed = database(path)
            seed.execute("INSERT INTO tasks(title,status,priority,due_date) VALUES ('active','todo',2,'2030-01-01')")
            seed.execute("INSERT INTO tasks(title,status,priority) VALUES ('nodue','todo',2)")
            seed.execute("INSERT INTO tasks(title,status,priority,due_date) VALUES ('done','done',2,'2030-01-01')")
            seed.execute("INSERT INTO tasks(title,status,priority,due_date,external_source) "
                         "VALUES ('lt','todo',1,'2030-01-01','leantime')")
            seed.execute("INSERT INTO tasks(title,status,priority,calendar_event_id,archived_at) "
                         "VALUES ('linked','done',1,'ev',datetime('now'))")
            seed.commit()
            seed.close()

            def connect(**kwargs):
                conn = sqlite3.connect(path, isolation_level=None)
                conn.row_factory = sqlite3.Row
                return conn

            with patch.object(calendar_sync, 'open_task_store',
                              lambda c, automation, **_kw: SqliteTaskStore(connect())):
                tasks = calendar_sync.get_tasks_to_sync()
                self.assertEqual([t['title'] for t in tasks], ['linked', 'active'])
                self.assertEqual(tasks[1]['due_date'], '2030-01-01')
                calendar_sync.save_event_id(tasks[1]['id'], 'event-1')
                calendar_sync.save_event_id(tasks[0]['id'], None)
            check = sqlite3.connect(path)
            self.addCleanup(check.close)
            self.assertEqual(dict(check.execute('SELECT title, calendar_event_id FROM tasks '
                                                "WHERE title IN ('active','linked')").fetchall()),
                             {'active': 'event-1', 'linked': None})

    def test_fake_store_calls(self):
        fake = FakeTaskStore([record(1, due_date='2030-01-01'), record(2, status='done'),
                              record(3, status='done', calendar_event_id='ev'),
                              record(4, due_date='2030-01-01', external_source='leantime')])
        with patch.object(calendar_sync, 'open_task_store', return_value=fake) as opened:
            self.assertEqual([t['id'] for t in calendar_sync.get_tasks_to_sync()], [3, 1])  # NULL due_date sorts first
            calendar_sync.save_event_id(1, 'ev1')
        self.assertEqual(opened.call_args.args[1], 'calendar-sync')
        self.assertEqual(fake.calls[0], ('list', (), {'include_archived': True}))
        self.assertEqual(fake.calls[-1], ('update', (1, {'calendar_event_id': 'ev1'}), {'legacy': None}))


class RecurringGenTest(unittest.TestCase):
    def setUp(self):
        self.db = database()
        self.addCleanup(self.db.close)
        due = (date.today() - timedelta(days=14)).isoformat()
        self.db.execute("INSERT INTO tasks(title,status,priority,due_date,recurrence_rule,is_recurring_instance) "
                        "VALUES ('Weekly review','todo',2,?,'weekly',0)", (due,))
        opener = patch.object(recurring_gen, 'open_task_store', lambda c, automation, **_kw: SqliteTaskStore(self.db))
        opener.start()
        self.addCleanup(opener.stop)

    def test_dry_run_writes_nothing_and_apply_generates(self):
        self.assertEqual(quiet(recurring_gen.generate_instances, False), 2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
        self.assertEqual(quiet(recurring_gen.generate_instances, True), 2)
        rows = self.db.execute('SELECT title, due_date, is_recurring_instance, recurrence_occurrence, '
                               'parent_recurring_id FROM tasks WHERE id > 1 ORDER BY id').fetchall()
        self.assertEqual([(r[0], r[1], r[2], r[3], r[4]) for r in rows],
                         [('[R1] Weekly review', (date.today() - timedelta(days=7)).isoformat(), 1, 1, 1),
                          ('[R2] Weekly review', date.today().isoformat(), 1, 2, 1)])
        self.assertEqual(quiet(recurring_gen.generate_instances, True), 0)  # idempotent
        self.assertEqual(quiet(recurring_gen.generate_instances, False), 0)  # preview sees instances

    def test_fake_store_calls(self):
        parent = record(9, title='Daily', recurrence_rule='daily', due_date=date.today().isoformat())
        instance = record(10, title='[R1] Daily', recurrence_rule='daily', is_recurring_instance=True,
                          recurrence_occurrence=1)
        cancelled = record(11, recurrence_rule='daily', status='cancelled')
        fake = FakeTaskStore([parent, instance, cancelled])
        with patch.object(recurring_gen, 'open_task_store', return_value=fake) as opened:
            quiet(recurring_gen.generate_instances, True)
        self.assertEqual(opened.call_args.args[1], 'recurring-gen')
        self.assertEqual([c for c in fake.calls if c[0] == 'generate_occurrences'],
                         [('generate_occurrences', (9,), {'until': date.today().isoformat()})])
        fake = FakeTaskStore([parent])
        with patch.object(recurring_gen, 'open_task_store', return_value=fake):
            quiet(recurring_gen.generate_instances, False)
        self.assertNotIn('generate_occurrences', fake.names())


if __name__ == '__main__':
    unittest.main()

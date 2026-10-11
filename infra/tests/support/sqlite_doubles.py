#!/usr/bin/env python3
"""Install the SQLite test doubles of every store (tasks, decisions, habits, notifications, kit cells).

Importing this module patches the production ``open_*_store`` functions so the
code under test reads and writes the test database named by SOLO_EMPIRE_DB_PATH
instead of the live APIs. Used by run_with_sqlite_store.py and, for Python
subprocesses started by other runtimes (the dashboard), by
infra/tests/support/doubles_site/sitecustomize.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

SUPPORT = Path(__file__).resolve().parent
sys.path.insert(0, str(SUPPORT))
import sqlite_task_store  # noqa: E402  (also isolates the Tasks API client switch)

import task_store  # noqa: E402

task_store.open_task_store = sqlite_task_store.sqlite_opener

# Decisions: the SQLite double over the test database named by SOLO_EMPIRE_DB_PATH.
import os  # noqa: E402
import sqlite3  # noqa: E402

import decision_store  # noqa: E402
import sqlite_decision_store  # noqa: E402  (also isolates the decisions API client switch)


def _decision_opener(*_args, **_kwargs):
    return sqlite_decision_store.SqliteDecisionStore(sqlite3.connect(os.environ["SOLO_EMPIRE_DB_PATH"]))


decision_store.open_decision_store = _decision_opener

# Habits: the SQLite double over the same test database.
import habits_store  # noqa: E402
import sqlite_habits_store  # noqa: E402  (also isolates the habits API client switch)


def _habits_opener(*_args, **_kwargs):
    return sqlite_habits_store.SqliteHabitsStore(sqlite3.connect(os.environ["SOLO_EMPIRE_DB_PATH"]))


habits_store.open_habits_store = _habits_opener

# Notifications: the SQLite double over the same test database.
import notifications_store  # noqa: E402
import sqlite_notifications_store  # noqa: E402  (also isolates the notifications API client switch)


def _notifications_opener(*_args, **_kwargs):
    return sqlite_notifications_store.SqliteNotificationsStore(sqlite3.connect(os.environ["SOLO_EMPIRE_DB_PATH"]))


notifications_store.open_notifications_store = _notifications_opener

# Goals, projects and roles-skills (kit cells): the SQLite double over the same test database.
import cell_store  # noqa: E402
import sqlite_cell_store  # noqa: E402  (also isolates the cell API client switches)

cell_store.open_cell_store = sqlite_cell_store.sqlite_cell_opener(sqlite3.connect(os.environ["SOLO_EMPIRE_DB_PATH"])) \
    if os.environ.get("SOLO_EMPIRE_DB_PATH") else cell_store.open_cell_store


# The CRM ledger (revenue, invoices, clients): the SQLite double over the same test database.
import crm_ledger  # noqa: E402
import sqlite_crm_ledger  # noqa: E402  (also isolates the ledger client switch)

if os.environ.get("SOLO_EMPIRE_DB_PATH"):
    crm_ledger.open_crm_ledger = sqlite_crm_ledger.sqlite_ledger_opener(
        sqlite3.connect(os.environ["SOLO_EMPIRE_DB_PATH"]))

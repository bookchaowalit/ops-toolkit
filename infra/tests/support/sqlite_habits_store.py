#!/usr/bin/env python3
"""Test double: the retired SQLite habit tables behind the ``ApiHabitsStore`` interface.

Habits live in the habits cell (solo-habits) since 2026-10-10; production code
uses ``open_habits_store`` only and must not import this module. Behaviour
tests patch ``open_habits_store`` to return this double over a temporary SQLite
database. It mirrors the service: logs are upserts on (habit, day), and every
change to a habit's logs recomputes its streak fields with the service's rule.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# Safety: a test that imports this double must never reach the live habits API.
os.environ.pop("EMPIRE_HABITS_API", None)
os.environ["EMPIRE_CLIENT_CONFIG"] = "/nonexistent/solo-empire-test/client.env"

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from habits_store import HabitsStoreError  # noqa: E402  (the error the production client raises)

LEGACY_DDL = Path(__file__).resolve().parent / "legacy_habit_tables.sql"
HABIT_FIELDS = ("name", "slug", "description", "category", "frequency", "target_count", "target_duration_minutes",
                "status", "priority", "current_streak", "best_streak", "last_completed_at", "schedule_days",
                "preferred_time", "notes")
ROUTINE_FIELDS = ("name", "slug", "description", "routine_type", "steps", "status", "total_duration_minutes")
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def apply_legacy_habit_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(LEGACY_DDL.read_text(encoding="utf-8"))


def streak(completed_days: set[date], schedule_days: list[str] | None, today: date) -> int:
    """The service's rule (solo-habits app.streak), mirrored for tests."""
    scheduled = {WEEKDAYS[d.lower()] for d in (schedule_days or []) if d.lower() in WEEKDAYS} or set(range(7))
    count, day = 0, today
    for _ in range(366):
        if day.weekday() in scheduled:
            if day in completed_days:
                count += 1
            elif day != today:
                break
        day -= timedelta(days=1)
    return count


class SqliteHabitsStore:
    def __init__(self, conn: sqlite3.Connection, *, today: date | None = None) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.today = today
        apply_legacy_habit_tables(self.conn)  # schema.sql no longer has them (migration 082)
        for table in ("habits", "habit_logs", "routines", "routine_logs"):
            columns = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if "version" not in columns:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        self.conn.commit()

    # --- shapes ------------------------------------------------------------------------------

    @staticmethod
    def _json(value: Any, default: Any) -> Any:
        if value is None or value == "":
            return default
        return json.loads(value) if isinstance(value, str) else value

    def _habit(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["schedule_days"] = self._json(data.get("schedule_days"), None)
        data["legacy_id"] = data["id"]
        return data

    def _routine(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["steps"] = self._json(data.get("steps"), [])
        data["legacy_id"] = data["id"]
        return data

    @staticmethod
    def _habit_log(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["completed"] = bool(data["completed"])
        return data

    def _routine_log(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["completed_steps"] = self._json(data.get("completed_steps"), [])
        data["skipped_steps"] = self._json(data.get("skipped_steps"), [])
        return data

    @staticmethod
    def _store(name: str, value: Any) -> Any:
        if name in ("schedule_days", "steps", "completed_steps", "skipped_steps") and value is not None:
            return json.dumps(value)
        if name == "completed" and value is not None:
            return int(bool(value))
        return value

    def _where(self, filters: dict[str, Any]) -> tuple[str, list[Any]]:
        where, params = ["1=1"], []
        for name, value in filters.items():
            if value is None:
                continue
            if isinstance(value, list):
                where.append(f"{name} IN ({','.join('?' * len(value))})")
                params += value
            else:
                where.append(f"{name} = ?")
                params.append(value)
        return " AND ".join(where), params

    # --- habits ------------------------------------------------------------------------------

    def habits(self, *, status=None, category=None, frequency=None) -> list[dict[str, Any]]:
        where, params = self._where({"status": [status] if isinstance(status, str) else status,
                                     "category": category, "frequency": frequency})
        return [self._habit(r) for r in self.conn.execute(f"SELECT * FROM habits WHERE {where} ORDER BY id", params)]

    def habit(self, habit_id: int) -> dict[str, Any] | None:
        return self._habit(self.conn.execute("SELECT * FROM habits WHERE id = ?", (int(habit_id),)).fetchone())

    def habit_by_slug(self, slug: str) -> dict[str, Any] | None:
        return self._habit(self.conn.execute("SELECT * FROM habits WHERE slug = ?", (slug,)).fetchone())

    def _insert(self, table: str, allowed: tuple[str, ...], fields: dict[str, Any]) -> int:
        data = {k: self._store(k, v) for k, v in fields.items() if k in allowed and v is not None}
        try:
            cursor = self.conn.execute(f"INSERT INTO {table} ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                                       list(data.values()))
        except sqlite3.IntegrityError as exc:
            raise HabitsStoreError(409, "conflict") from exc
        self.conn.commit()
        return cursor.lastrowid

    def _update(self, table: str, allowed: tuple[str, ...], record_id: int, fields: dict[str, Any],
                expected_version: int | None) -> None:
        row = self.conn.execute(f"SELECT version FROM {table} WHERE id = ?", (int(record_id),)).fetchone()
        if row is None:
            raise HabitsStoreError(404, "not_found")
        if expected_version is not None and row["version"] != expected_version:
            raise HabitsStoreError(409, "version_conflict")
        data = {k: self._store(k, v) for k, v in fields.items() if k in allowed}
        if data:
            assignments = ", ".join(f"{k} = ?" for k in data)
            self.conn.execute(f"UPDATE {table} SET {assignments}, version = version + 1, updated_at = ? WHERE id = ?",
                              [*data.values(), datetime.now().isoformat(timespec="seconds"), int(record_id)])
            self.conn.commit()

    def create_habit(self, fields: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self.habit(self._insert("habits", HABIT_FIELDS, fields))

    def update_habit(self, habit_id: int, fields: dict[str, Any], *, expected_version: int | None = None
                     ) -> dict[str, Any]:
        self._update("habits", HABIT_FIELDS, habit_id, fields, expected_version)
        return self.habit(habit_id)

    def delete_habit(self, habit_id: int) -> None:
        self.conn.execute("DELETE FROM habits WHERE id = ?", (int(habit_id),))
        self.conn.commit()

    def habit_logs(self, *, habit_id=None, date_from=None, date_to=None, completed=None, notes_prefix=None,
                   order="asc") -> list[dict[str, Any]]:
        where, params = self._where({"habit_id": habit_id,
                                     "completed": None if completed is None else int(bool(completed))})
        if date_from:
            where += " AND log_date >= ?"
            params.append(date_from)
        if date_to:
            where += " AND log_date <= ?"
            params.append(date_to)
        if notes_prefix:
            where += " AND substr(coalesce(notes, ''), 1, ?) = ?"
            params += [len(notes_prefix), notes_prefix]
        direction = "DESC" if order == "desc" else "ASC"
        rows = self.conn.execute(f"SELECT * FROM habit_logs WHERE {where} ORDER BY log_date {direction}, id {direction}",
                                 params)
        return [self._habit_log(r) for r in rows]

    def _refresh_streak(self, habit_id: int) -> None:
        habit = self.habit(habit_id)
        days = {date.fromisoformat(r[0]) for r in self.conn.execute(
            "SELECT log_date FROM habit_logs WHERE habit_id = ? AND completed = 1", (habit_id,))}
        current = streak(days, habit["schedule_days"], self.today or date.today())
        last = max(days).isoformat() if days else None
        self.conn.execute("UPDATE habits SET current_streak = ?, best_streak = max(coalesce(best_streak, 0), ?),"
                          " last_completed_at = ?, version = version + 1 WHERE id = ?", (current, current, last, habit_id))

    def log_habit(self, habit_id: int, log_date: str, fields: dict[str, Any] | None = None
                  ) -> tuple[str, dict[str, Any]]:
        if self.habit(habit_id) is None:
            raise HabitsStoreError(404, "not_found")
        data = {k: self._store(k, v) for k, v in (fields or {}).items()
                if k in ("completed", "count", "duration_minutes", "notes")}
        before = self.conn.execute("SELECT * FROM habit_logs WHERE habit_id = ? AND log_date = ?",
                                   (int(habit_id), log_date)).fetchone()
        if before is None:
            columns = ["habit_id", "log_date", *data]
            self.conn.execute(f"INSERT INTO habit_logs ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                              [int(habit_id), log_date, *data.values()])
            outcome = "created"
        else:
            diff = {k: v for k, v in data.items() if before[k] != v}
            outcome = "unchanged"
            if diff:
                self.conn.execute(f"UPDATE habit_logs SET {', '.join(f'{k} = ?' for k in diff)},"
                                  " version = version + 1 WHERE id = ?", [*diff.values(), before["id"]])
                outcome = "updated"
        if outcome != "unchanged":
            self._refresh_streak(int(habit_id))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM habit_logs WHERE habit_id = ? AND log_date = ?",
                                (int(habit_id), log_date)).fetchone()
        return outcome, self._habit_log(row)

    def unlog_habit(self, habit_id: int, log_date: str) -> bool:
        gone = self.conn.execute("DELETE FROM habit_logs WHERE habit_id = ? AND log_date = ?",
                                 (int(habit_id), log_date)).rowcount
        if gone:
            self._refresh_streak(int(habit_id))
        self.conn.commit()
        return bool(gone)

    # --- routines ------------------------------------------------------------------------------

    def routines(self, *, status=None, routine_type=None) -> list[dict[str, Any]]:
        where, params = self._where({"status": [status] if isinstance(status, str) else status,
                                     "routine_type": routine_type})
        return [self._routine(r) for r in self.conn.execute(f"SELECT * FROM routines WHERE {where} ORDER BY id", params)]

    def routine(self, routine_id: int) -> dict[str, Any] | None:
        return self._routine(self.conn.execute("SELECT * FROM routines WHERE id = ?", (int(routine_id),)).fetchone())

    def routine_by_slug(self, slug: str) -> dict[str, Any] | None:
        return self._routine(self.conn.execute("SELECT * FROM routines WHERE slug = ?", (slug,)).fetchone())

    def create_routine(self, fields: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self.routine(self._insert("routines", ROUTINE_FIELDS, fields))

    def update_routine(self, routine_id: int, fields: dict[str, Any], *, expected_version: int | None = None
                       ) -> dict[str, Any]:
        self._update("routines", ROUTINE_FIELDS, routine_id, fields, expected_version)
        return self.routine(routine_id)

    def routine_logs(self, *, routine_id=None, date_from=None, date_to=None, order="asc") -> list[dict[str, Any]]:
        where, params = self._where({"routine_id": routine_id})
        if date_from:
            where += " AND log_date >= ?"
            params.append(date_from)
        if date_to:
            where += " AND log_date <= ?"
            params.append(date_to)
        direction = "DESC" if order == "desc" else "ASC"
        rows = self.conn.execute(f"SELECT * FROM routine_logs WHERE {where} ORDER BY log_date {direction},"
                                 f" id {direction}", params)
        return [self._routine_log(r) for r in rows]

    def log_routine(self, routine_id: int, log_date: str, fields: dict[str, Any] | None = None
                    ) -> tuple[str, dict[str, Any]]:
        if self.routine(routine_id) is None:
            raise HabitsStoreError(404, "not_found")
        data = {k: self._store(k, v) for k, v in (fields or {}).items()
                if k in ("completed_steps", "skipped_steps", "duration_minutes", "mood_before", "mood_after", "notes")}
        before = self.conn.execute("SELECT * FROM routine_logs WHERE routine_id = ? AND log_date = ?",
                                   (int(routine_id), log_date)).fetchone()
        if before is None:
            columns = ["routine_id", "log_date", *data]
            self.conn.execute(f"INSERT INTO routine_logs ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                              [int(routine_id), log_date, *data.values()])
            outcome = "created"
        else:
            diff = {k: v for k, v in data.items() if before[k] != v}
            outcome = "updated" if diff else "unchanged"
            if diff:
                self.conn.execute(f"UPDATE routine_logs SET {', '.join(f'{k} = ?' for k in diff)} WHERE id = ?",
                                  [*diff.values(), before["id"]])
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM routine_logs WHERE routine_id = ? AND log_date = ?",
                                (int(routine_id), log_date)).fetchone()
        return outcome, self._routine_log(row)

    def stats(self, kind: str = "habits", group_by: str = "status") -> dict[str, int]:
        table = "habits" if kind == "habits" else "routines"
        return {r[0]: r[1] for r in self.conn.execute(f"SELECT {group_by}, count(*) FROM {table} GROUP BY 1")}


def sqlite_habits_opener(conn: sqlite3.Connection):
    """A drop-in for ``open_habits_store`` that returns the double over ``conn``."""
    store = SqliteHabitsStore(conn)
    return lambda *_args, **_kwargs: store

#!/usr/bin/env python3
"""Test double: the retired SQLite notification tables behind the ``ApiNotificationsStore`` interface.

Notifications live in the notifications cell (solo-notifications) since
2026-10-10; production code uses ``open_notifications_store`` only and must not
import this module. It mirrors the service: queueing with a known
``dedupe_key`` returns the existing entry, and delivery results update an
entry atomically.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Safety: a test that imports this double must never reach the live notifications API.
os.environ.pop("EMPIRE_NOTIFICATIONS_API", None)
os.environ["EMPIRE_CLIENT_CONFIG"] = "/nonexistent/solo-empire-test/client.env"

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from notifications_store import NotificationsStoreError  # noqa: E402  (the error the production client raises)

LEGACY_DDL = Path(__file__).resolve().parent / "legacy_notification_tables.sql"
RULE_FIELDS = ("name", "description", "rule_type", "config", "channels", "project_filter", "category_filter",
               "priority_filter", "status_filter", "check_interval_minutes", "last_checked_at", "last_triggered_at",
               "is_active")
LOG_FIELDS = ("rule_id", "task_id", "title", "message", "notification_type", "severity", "channel", "task_title",
              "task_status", "task_priority", "task_due_date", "task_project", "metadata", "sent_at")


def apply_legacy_notification_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(LEGACY_DDL.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class SqliteNotificationsStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        apply_legacy_notification_tables(self.conn)  # schema.sql no longer has them (migration 083)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(notification_log)")}
        if "dedupe_key" not in columns:
            self.conn.execute("ALTER TABLE notification_log ADD COLUMN dedupe_key TEXT")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS notification_log_dedupe ON notification_log(dedupe_key)"
                              " WHERE dedupe_key IS NOT NULL")
        rule_columns = {r[1] for r in self.conn.execute("PRAGMA table_info(notification_rules)")}
        if "version" not in rule_columns:
            self.conn.execute("ALTER TABLE notification_rules ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        self.conn.commit()

    @staticmethod
    def _json(value: Any, default: Any) -> Any:
        if value in (None, ""):
            return default
        return json.loads(value) if isinstance(value, str) else value

    def _rule(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["config"] = self._json(data.get("config"), {})
        data["channels"] = self._json(data.get("channels"), ["console"])
        data["status_filter"] = self._json(data.get("status_filter"), None)
        data["is_active"] = bool(data["is_active"])
        data["legacy_id"] = data["id"]
        return data

    def _entry(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["metadata"] = self._json(data.get("metadata"), None)
        data["legacy_id"] = data["id"]
        return data

    # --- rules ---------------------------------------------------------------------------------

    def rules(self, *, active: bool | None = None, rule_type: str | None = None) -> list[dict[str, Any]]:
        where, params = ["1=1"], []
        if active is not None:
            where.append("is_active = ?")
            params.append(int(bool(active)))
        if rule_type is not None:
            where.append("rule_type = ?")
            params.append(rule_type)
        return [self._rule(r) for r in self.conn.execute(
            f"SELECT * FROM notification_rules WHERE {' AND '.join(where)} ORDER BY id", params)]

    def rule(self, rule_id: int) -> dict[str, Any] | None:
        return self._rule(self.conn.execute("SELECT * FROM notification_rules WHERE id = ?", (int(rule_id),)).fetchone())

    @staticmethod
    def _store(name: str, value: Any) -> Any:
        if name in ("config", "channels", "status_filter", "metadata") and value is not None:
            return json.dumps(value)
        if name == "is_active" and value is not None:
            return int(bool(value))
        return value

    def create_rule(self, fields: dict[str, Any]) -> dict[str, Any]:
        data = {k: self._store(k, v) for k, v in fields.items() if k in RULE_FIELDS and v is not None}
        cursor = self.conn.execute(f"INSERT INTO notification_rules ({', '.join(data)})"
                                   f" VALUES ({', '.join('?' * len(data))})", list(data.values()))
        self.conn.commit()
        return self.rule(cursor.lastrowid)

    def update_rule(self, rule_id: int, fields: dict[str, Any], *, expected_version: int | None = None
                    ) -> dict[str, Any]:
        before = self.rule(rule_id)
        if before is None:
            raise NotificationsStoreError(404, "not_found")
        if expected_version is not None and before["version"] != expected_version:
            raise NotificationsStoreError(409, "version_conflict")
        data = {k: self._store(k, v) for k, v in fields.items() if k in RULE_FIELDS}
        if data:
            self.conn.execute(f"UPDATE notification_rules SET {', '.join(f'{k} = ?' for k in data)},"
                              " version = version + 1, updated_at = ? WHERE id = ?",
                              [*data.values(), _now(), int(rule_id)])
            self.conn.commit()
        return self.rule(rule_id)

    # --- log -----------------------------------------------------------------------------------

    def notifications(self, *, notification_type=None, since=None, until=None, task_id=None, rule_id=None,
                      undelivered=None, max_attempts=None, dedupe_key=None, order="asc", limit=None
                      ) -> list[dict[str, Any]]:
        where, params = ["1=1"], []
        types = [notification_type] if isinstance(notification_type, str) else notification_type
        if types:
            where.append(f"notification_type IN ({','.join('?' * len(types))})")
            params += list(types)
        for column, op, value in (("sent_at", ">=", since), ("sent_at", "<", until), ("task_id", "=", task_id),
                                  ("rule_id", "=", rule_id), ("dedupe_key", "=", dedupe_key)):
            if value is not None:
                where.append(f"{column} {op} ?")
                params.append(value)
        if undelivered is not None:
            where.append("delivered_at IS NULL" if undelivered else "delivered_at IS NOT NULL")
        if max_attempts is not None:
            where.append("delivery_attempts < ?")
            params.append(max_attempts)
        sql = (f"SELECT * FROM notification_log WHERE {' AND '.join(where)}"
               f" ORDER BY id {'DESC' if order == 'desc' else 'ASC'}")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._entry(r) for r in self.conn.execute(sql, params)]

    def notification(self, notification_id: int) -> dict[str, Any] | None:
        return self._entry(self.conn.execute("SELECT * FROM notification_log WHERE id = ?",
                                             (int(notification_id),)).fetchone())

    def stats(self, group_by: str = "notification_type", since: str | None = None) -> dict[str, int]:
        where, params = ("sent_at >= ?", [since]) if since else ("1=1", [])
        return {r[0]: r[1] for r in self.conn.execute(
            f"SELECT {group_by}, count(*) FROM notification_log WHERE {where} GROUP BY 1", params)}

    def queue(self, fields: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        key = fields.get("dedupe_key")
        if key:
            existing = self.conn.execute("SELECT * FROM notification_log WHERE dedupe_key = ?", (key,)).fetchone()
            if existing is not None:
                return "existing", self._entry(existing)
        data = {k: self._store(k, v) for k, v in fields.items() if k in LOG_FIELDS and v is not None}
        data.setdefault("sent_at", _now())
        data.setdefault("channel", "console")
        if key:
            data["dedupe_key"] = key
        cursor = self.conn.execute(f"INSERT INTO notification_log ({', '.join(data)})"
                                   f" VALUES ({', '.join('?' * len(data))})", list(data.values()))
        self.conn.commit()
        return "created", self.notification(cursor.lastrowid)

    def record_delivery(self, notification_id: int, outcome: str, *, channel: str | None = None,
                        error: str | None = None) -> dict[str, Any]:
        if self.notification(notification_id) is None:
            raise NotificationsStoreError(404, "not_found")
        if outcome == "delivered":
            if not channel or channel == "console":
                raise NotificationsStoreError(422, "console_is_not_delivery")
            self.conn.execute("UPDATE notification_log SET delivered_at = ?, delivery_channel = ?, delivery_error = NULL,"
                              " delivery_attempts = delivery_attempts + 1 WHERE id = ?",
                              (_now(), channel, int(notification_id)))
        elif outcome == "failed":
            self.conn.execute("UPDATE notification_log SET delivery_error = ?, delivery_attempts = delivery_attempts + 1"
                              " WHERE id = ?", (error or "delivery_failed", int(notification_id)))
        else:
            self.conn.execute("UPDATE notification_log SET channel = 'console', delivery_error = NULL WHERE id = ?",
                              (int(notification_id),))
        self.conn.commit()
        return self.notification(notification_id)


def sqlite_notifications_opener(conn: sqlite3.Connection):
    """A drop-in for ``open_notifications_store`` that returns the double over ``conn``."""
    store = SqliteNotificationsStore(conn)
    return lambda *_args, **_kwargs: store

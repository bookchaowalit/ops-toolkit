#!/usr/bin/env python3
"""Test double: the CRM ledger (revenue, invoices, clients) over the legacy tables of a test database.

Production uses ``crm_ledger.open_crm_ledger`` (the income-pipeline CRM) and must
not import this module. Same interface as ``CrmLedger``.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

os.environ.pop("EMPIRE_CRM_LEDGER_API", None)
os.environ.pop("EMPIRE_CRM_LEDGER_TOKEN", None)
os.environ["EMPIRE_CLIENT_CONFIG"] = "/nonexistent/solo-empire-test/client.env"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from crm_ledger import LedgerError  # noqa: E402

LEGACY_DDL = Path(__file__).resolve().parent / "legacy_ledger_tables.sql"


class SqliteCrmLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(LEGACY_DDL.read_text(encoding="utf-8"))

    def _rows(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params)]

    def revenue(self, *, since=None, until=None, source_type=None, client_id=None, received=None, limit=500):
        where, params = ["1=1"], []
        for clause, value in (("revenue_date >= ?", since), ("revenue_date <= ?", until),
                              ("source_type = ?", source_type), ("client_id = ?", client_id)):
            if value is not None:
                where.append(clause)
                params.append(value)
        if received is not None:
            where.append("received = ?")
            params.append(1 if received else 0)
        rows = self._rows(f"SELECT * FROM revenue WHERE {' AND '.join(where)} ORDER BY revenue_date DESC, id DESC"
                          f" LIMIT {int(limit)}", params)
        totals: dict[str, float] = {}
        for r in self.conn.execute(f"SELECT currency, SUM(amount) FROM revenue WHERE {' AND '.join(where)}"
                                   " GROUP BY currency", params):
            totals[r[0] or "THB"] = float(r[1] or 0)
        return rows, totals

    def revenue_all(self, *, since=None, until=None, source_type=None, client_id=None, received=None):
        return self.revenue(since=since, until=until, source_type=source_type, client_id=client_id,
                            received=received, limit=10_000_000)

    def add_revenue(self, fields):
        key = fields.get("idempotency_key")
        existing = self.conn.execute("SELECT * FROM revenue WHERE idempotency_key = ?", (key,)).fetchone()
        if existing:
            return "existing", dict(existing)
        data = {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items()}
        cur = self.conn.execute(f"INSERT INTO revenue ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                                list(data.values()))
        self.conn.commit()
        return "created", dict(self.conn.execute("SELECT * FROM revenue WHERE id = ?", (cur.lastrowid,)).fetchone())

    def update_revenue(self, record_id, fields):
        return self._update("revenue", record_id, fields)

    def invoices(self, *, status=None, client_id=None, limit=500):
        where, params = ["1=1"], []
        for clause, value in (("status = ?", status), ("client_id = ?", client_id)):
            if value is not None:
                where.append(clause)
                params.append(value)
        return self._rows(f"SELECT * FROM invoices WHERE {' AND '.join(where)} ORDER BY issue_date DESC, id DESC"
                          f" LIMIT {int(limit)}", params)

    def add_invoice(self, fields):
        existing = self.conn.execute("SELECT * FROM invoices WHERE invoice_no = ?", (fields["invoice_no"],)).fetchone()
        if existing:
            return "existing", dict(existing)
        cur = self.conn.execute(f"INSERT INTO invoices ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                                list(fields.values()))
        self.conn.commit()
        return "created", dict(self.conn.execute("SELECT * FROM invoices WHERE id = ?", (cur.lastrowid,)).fetchone())

    def update_invoice(self, record_id, fields):
        return self._update("invoices", record_id, fields)

    def clients(self, *, status=None, q=None, limit=500):
        where, params = ["1=1"], []
        if status:
            where.append("status = ?")
            params.append(status)
        if q:
            where.append("(LOWER(name) LIKE ? OR LOWER(COALESCE(company, '')) LIKE ?)")
            params += [f"%{q.lower()}%"] * 2
        return self._rows(f"SELECT * FROM clients WHERE {' AND '.join(where)} ORDER BY id LIMIT {int(limit)}", params)

    def add_client(self, fields):
        cur = self.conn.execute(f"INSERT INTO clients ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                                list(fields.values()))
        self.conn.commit()
        return "created", dict(self.conn.execute("SELECT * FROM clients WHERE id = ?", (cur.lastrowid,)).fetchone())

    def _update(self, table, record_id, fields):
        if self.conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (record_id,)).fetchone() is None:
            raise LedgerError(404, "not_found")
        data = {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items()}
        self.conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?",
                          [*data.values(), record_id])
        self.conn.commit()
        return dict(self.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone())


def sqlite_ledger_opener(conn: sqlite3.Connection):
    """A drop-in for ``open_crm_ledger`` over ``conn``."""
    ledger = SqliteCrmLedger(conn)
    return lambda *_a, **_k: ledger

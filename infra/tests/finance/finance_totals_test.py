#!/usr/bin/env python3
"""Currency-safe finance totals must never mix THB and USD, and degrade when the CRM ledger is unreachable."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
UTILS = ROOT / "infra" / "scripts" / "utils"
sys.path.insert(0, str(UTILS))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))

import crm_ledger  # noqa: E402
import sqlite_crm_ledger  # noqa: E402
from finance_totals import (  # noqa: E402
    amount_for_currency,
    format_money,
    format_money_parts,
    revenue_currency_totals,
    revenue_overview_from_totals,
    safe_revenue_overview,
    totals_from_rows,
)


def main() -> int:
    rows = totals_from_rows([
        {"amount": 10000, "currency": "THB", "received": 1, "revenue_date": "2026-08-06"},
        {"amount": 150, "currency": "USD", "received": 1, "revenue_date": "2026-08-05"},
        {"amount": 150, "currency": "USD", "received": 1, "revenue_date": "2026-07-24"},
    ])
    overview = revenue_overview_from_totals(rows)

    assert overview["received"] == 10000
    assert overview["pending"] == 0
    assert overview["entries"] == 3
    assert amount_for_currency(rows, "USD") == 300
    assert "10150" not in overview["received_display"]
    assert "70300" not in overview["received_display"]
    assert overview["received_display"] == "฿10,000 + $300.00"
    assert format_money(150, "USD") == "$150.00"
    assert format_money_parts(rows, "received") == "฿10,000 + $300.00"

    # Rows read through the CRM ledger double, filtered by date.
    conn = sqlite3.connect(":memory:")
    opener = sqlite_crm_ledger.sqlite_ledger_opener(conn)
    conn.executemany(
        "INSERT INTO revenue (source_type, source_name, amount, currency, received, revenue_date) VALUES (?, ?, ?, ?, ?, ?)",
        [("service", "a", 100, "THB", 1, "2026-08-06"), ("service", "b", 50, "THB", 0, "2026-09-01"),
         ("service", "c", 7, "USD", 1, "2026-08-07")])
    conn.commit()
    original = crm_ledger.open_crm_ledger
    crm_ledger.open_crm_ledger = opener
    try:
        totals = revenue_currency_totals()
        assert amount_for_currency(totals, "THB", "received") == 100
        assert amount_for_currency(totals, "THB", "pending") == 50
        assert amount_for_currency(revenue_currency_totals(since="2026-09-01"), "THB", "total") == 50
        assert safe_revenue_overview()["entries"] == 3

        def unreachable():
            raise crm_ledger.LedgerError(503, "unavailable:URLError")

        crm_ledger.open_crm_ledger = unreachable
        down = safe_revenue_overview()
        assert down["unavailable"] is True and down["received_display"] == "unavailable"
    finally:
        crm_ledger.open_crm_ledger = original

    print("✅ finance: currency totals keep THB and USD separate; the ledger outage degrades to 'unavailable'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

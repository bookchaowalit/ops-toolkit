"""Currency-safe finance totals.

Never sum amounts across currencies. Dashboard fields prefixed with ฿ use
THB-only totals; mixed-currency views must list each currency separately.

Revenue rows come from the CRM ledger (``crm_ledger``); when it is unreachable the
overview is flagged ``unavailable`` and readers show that instead of failing.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

CURRENCY_SYMBOLS = {
    "THB": "฿",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
}

PRIMARY_CURRENCY = "THB"


def normalize_currency(value: Any) -> str:
    text = str(value or PRIMARY_CURRENCY).strip().upper()
    return text or PRIMARY_CURRENCY


def format_money(amount: Any, currency: Any = PRIMARY_CURRENCY, *, digits: int | None = None) -> str:
    currency = normalize_currency(currency)
    number = float(amount or 0)
    if digits is None:
        digits = 0 if currency == "THB" and number.is_integer() else 2
    formatted = f"{number:,.{digits}f}"
    symbol = CURRENCY_SYMBOLS.get(currency)
    if symbol:
        return f"{symbol}{formatted}"
    return f"{currency} {formatted}"


def format_money_parts(rows: Sequence[Mapping[str, Any]], field: str = "received") -> str:
    parts: list[str] = []
    for row in rows:
        amount = float(row.get(field) or 0)
        if amount == 0 and len(rows) > 1:
            continue
        parts.append(format_money(amount, row.get("currency")))
    if not parts:
        return format_money(0, PRIMARY_CURRENCY)
    return " + ".join(parts)


def _as_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


UNAVAILABLE = "unavailable"


def totals_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-currency entries, received, pending and total from CRM ledger revenue rows."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        currency = normalize_currency(row.get("currency"))
        bucket = grouped.setdefault(
            currency, {"currency": currency, "entries": 0, "received": 0.0, "pending": 0.0, "total": 0.0})
        amount = float(row.get("amount") or 0)
        bucket["entries"] += 1
        bucket["total"] += amount
        bucket["received" if row.get("received") else "pending"] += amount
    return [grouped[currency] for currency in sorted(grouped)]


def ledger_revenue_rows(since: str | None = None, until: str | None = None) -> list[dict[str, Any]]:
    """Every revenue row from the CRM ledger (all pages); raises LedgerError/ClientConfigError when unreachable."""
    import crm_ledger  # looked up at call time so tests can swap in the SQLite double

    rows, _totals = crm_ledger.open_crm_ledger().revenue_all(since=since, until=until)
    return rows


def revenue_currency_totals(since: str | None = None, until: str | None = None) -> list[dict[str, Any]]:
    """Per-currency entries, received, pending and total; amounts come from the ledger's own totals.

    Amounts are the ledger's totals over every matching row; ``entries`` counts every page of rows.
    """
    import crm_ledger

    ledger = crm_ledger.open_crm_ledger()
    rows, totals = ledger.revenue_all(since=since, until=until)
    _received_rows, received = ledger.revenue(since=since, until=until, received=True, limit=1)
    counts: dict[str, int] = {}
    for row in rows:
        currency = normalize_currency(row.get("currency"))
        counts[currency] = counts.get(currency, 0) + 1
    out = []
    for currency in sorted({normalize_currency(c) for c in totals}):
        total = sum(float(v) for c, v in totals.items() if normalize_currency(c) == currency)
        got = sum(float(v) for c, v in received.items() if normalize_currency(c) == currency)
        out.append({"currency": currency, "entries": counts.get(currency, 0), "received": got,
                    "pending": total - got, "total": total, "entries_complete": True})
    return out


def ledger_unavailable_errors() -> tuple[type[BaseException], ...]:
    from crm_ledger import LedgerError
    from task_store import ClientConfigError

    return (LedgerError, ClientConfigError)


def unavailable_overview() -> dict[str, Any]:
    return {
        "unavailable": True,
        "reporting_currency": PRIMARY_CURRENCY,
        "received": 0.0,
        "pending": 0.0,
        "total": 0.0,
        "total_contract": 0.0,
        "entries": 0,
        "received_display": UNAVAILABLE,
        "pending_display": UNAVAILABLE,
        "by_currency": [],
    }


def safe_revenue_overview(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Revenue overview from the CRM ledger, or an ``unavailable`` overview when the ledger cannot be reached."""
    try:
        return revenue_overview_from_totals(revenue_currency_totals(since, until))
    except ledger_unavailable_errors():
        return unavailable_overview()


def amount_for_currency(
    rows: Sequence[Mapping[str, Any]],
    currency: str = PRIMARY_CURRENCY,
    field: str = "received",
) -> float:
    currency = normalize_currency(currency)
    for row in rows:
        if normalize_currency(row.get("currency")) == currency:
            return float(row.get(field) or 0)
    return 0.0


def revenue_overview_from_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    totals = [_as_dict(row) for row in rows]
    return {
        "reporting_currency": PRIMARY_CURRENCY,
        "received": amount_for_currency(totals, PRIMARY_CURRENCY, "received"),
        "pending": amount_for_currency(totals, PRIMARY_CURRENCY, "pending"),
        "total": amount_for_currency(totals, PRIMARY_CURRENCY, "total"),
        "total_contract": amount_for_currency(totals, PRIMARY_CURRENCY, "total"),
        "entries": sum(int(row.get("entries") or 0) for row in totals),
        "received_display": format_money_parts(totals, "received"),
        "pending_display": format_money_parts(totals, "pending"),
        "by_currency": totals,
    }

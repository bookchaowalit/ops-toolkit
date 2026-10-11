#!/usr/bin/env python3
"""Record and review revenue, invoices and clients in the CRM ledger (income-pipeline).

    ledger-cli.py revenue [--since YYYY-MM-DD] [--until ...] [--type service|subscription|...] [--unreceived]
    ledger-cli.py revenue-add AMOUNT --type service --date YYYY-MM-DD [--currency THB] [--source NAME]
                  [--client ID] [--desc TEXT] [--received] [--key IDEMPOTENCY_KEY]
    ledger-cli.py revenue-received ID
    ledger-cli.py invoices [--status draft|sent|paid|overdue|cancelled]
    ledger-cli.py invoice-add NUMBER AMOUNT --issue YYYY-MM-DD --due YYYY-MM-DD [--client ID] [--revenue ID]
                  [--currency THB] [--desc TEXT]
    ledger-cli.py invoice-status ID STATUS [--paid YYYY-MM-DD]
    ledger-cli.py clients [--q TEXT]
    ledger-cli.py client-add NAME [--company TEXT] [--status lead|prospect|active|past|churned]
    ledger-cli.py summary [--since YYYY-MM-DD]

Add --json to any command for machine-readable output. Revenue and invoices live
in the CRM since 2026-10-11 (infra/scripts/utils/crm_ledger.py); this replaces the
old finance-cli and invoice-cli that wrote the control plane's SQLite copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from crm_ledger import LedgerError, open_crm_ledger  # noqa: E402
from task_store import ClientConfigError  # noqa: E402


def money(totals: dict[str, float]) -> str:
    return ", ".join(f"{amount:,.2f} {currency}" for currency, amount in sorted(totals.items())) or "0"


def emit(data, as_json: bool, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str) if as_json else text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger-cli", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", dest="as_json")
    sub = parser.add_subparsers(dest="command", required=True)
    rev = sub.add_parser("revenue")
    rev.add_argument("--since")
    rev.add_argument("--until")
    rev.add_argument("--type")
    rev.add_argument("--unreceived", action="store_true")
    add = sub.add_parser("revenue-add")
    add.add_argument("amount", type=float)
    add.add_argument("--type", required=True)
    add.add_argument("--date", default=date.today().isoformat())
    add.add_argument("--currency", default="THB")
    add.add_argument("--source")
    add.add_argument("--client", type=int)
    add.add_argument("--desc")
    add.add_argument("--received", action="store_true")
    add.add_argument("--key", help="idempotency key (default: derived from date, type, amount, source)")
    got = sub.add_parser("revenue-received")
    got.add_argument("id", type=int)
    inv = sub.add_parser("invoices")
    inv.add_argument("--status")
    inva = sub.add_parser("invoice-add")
    inva.add_argument("number")
    inva.add_argument("amount", type=float)
    inva.add_argument("--issue", default=date.today().isoformat())
    inva.add_argument("--due", required=True)
    inva.add_argument("--client", type=int)
    inva.add_argument("--revenue", type=int)
    inva.add_argument("--currency", default="THB")
    inva.add_argument("--desc")
    invs = sub.add_parser("invoice-status")
    invs.add_argument("id", type=int)
    invs.add_argument("status")
    invs.add_argument("--paid")
    cl = sub.add_parser("clients")
    cl.add_argument("--q")
    cla = sub.add_parser("client-add")
    cla.add_argument("name")
    cla.add_argument("--company")
    cla.add_argument("--status", default="lead")
    summ = sub.add_parser("summary")
    summ.add_argument("--since", default=date.today().replace(day=1).isoformat())
    args = parser.parse_args(argv)

    try:
        ledger = open_crm_ledger()
        if args.command == "revenue":
            rows, totals = ledger.revenue(since=args.since, until=args.until, source_type=args.type,
                                          received=False if args.unreceived else None)
            lines = [f"[{r['id']}] {r['revenue_date']} {r['amount']:,.2f} {r['currency']} {r['source_type']}"
                     f" {r.get('source_name') or ''}{'' if r.get('received') else ' (not received)'}" for r in rows]
            emit({"items": rows, "totals": totals}, args.as_json, "\n".join(lines + [f"Total: {money(totals)}"]))
        elif args.command == "revenue-add":
            key = args.key or "ledger-cli:" + hashlib.sha256(
                f"{args.date}|{args.type}|{args.amount}|{args.currency}|{args.source}".encode()).hexdigest()[:32]
            fields = {"source_type": args.type, "amount": args.amount, "currency": args.currency,
                      "revenue_date": args.date, "source_name": args.source, "client_id": args.client,
                      "description": args.desc, "received": args.received, "idempotency_key": key}
            outcome, row = ledger.add_revenue({k: v for k, v in fields.items() if v is not None})
            emit(row, args.as_json, f"{outcome}: revenue [{row['id']}] {row['amount']:,.2f} {row['currency']}")
        elif args.command == "revenue-received":
            row = ledger.update_revenue(args.id, {"received": True})
            emit(row, args.as_json, f"revenue [{row['id']}] received")
        elif args.command == "invoices":
            rows = ledger.invoices(status=args.status)
            emit(rows, args.as_json, "\n".join(
                f"[{r['id']}] {r['invoice_no']} {r['amount']:,.2f} {r['currency']} {r['status']} due {r['due_date']}"
                for r in rows) or "No invoices.")
        elif args.command == "invoice-add":
            fields = {"invoice_no": args.number, "amount": args.amount, "currency": args.currency,
                      "issue_date": args.issue, "due_date": args.due, "client_id": args.client,
                      "revenue_id": args.revenue, "description": args.desc}
            outcome, row = ledger.add_invoice({k: v for k, v in fields.items() if v is not None})
            emit(row, args.as_json, f"{outcome}: invoice [{row['id']}] {row['invoice_no']}")
        elif args.command == "invoice-status":
            fields = {"status": args.status, **({"paid_date": args.paid} if args.paid else {})}
            row = ledger.update_invoice(args.id, fields)
            emit(row, args.as_json, f"invoice [{row['id']}] {row['status']}")
        elif args.command == "clients":
            rows = ledger.clients(q=args.q)
            emit(rows, args.as_json, "\n".join(f"[{r['id']}] {r['name']} ({r.get('company') or '-'}) {r['status']}"
                                               for r in rows) or "No clients.")
        elif args.command == "client-add":
            outcome, row = ledger.add_client({"name": args.name, "company": args.company, "status": args.status})
            emit(row, args.as_json, f"{outcome}: client [{row['id']}] {row['name']}")
        elif args.command == "summary":
            _, totals = ledger.revenue(since=args.since)
            unpaid = [i for i in ledger.invoices() if i["status"] in ("sent", "overdue")]
            emit({"since": args.since, "revenue": totals, "unpaid_invoices": len(unpaid)}, args.as_json,
                 f"Revenue since {args.since}: {money(totals)}\nUnpaid invoices: {len(unpaid)}")
    except (LedgerError, ClientConfigError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

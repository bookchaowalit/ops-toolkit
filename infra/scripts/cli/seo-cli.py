#!/usr/bin/env python3
"""Client SEO monthly-report CLI.

Parse Search Console zips or pull Gmail Search Console/Ahrefs notices.
Never crawls the live site, never sends the report, never invents GA4.

Usage:
    python3 infra/scripts/cli/seo-cli.py --json
    python3 infra/scripts/cli/seo-cli.py status --client pixiu-watch --json
    python3 infra/scripts/cli/seo-cli.py fetch --month 2026-08 --json
    python3 infra/scripts/cli/seo-cli.py report --source gmail --month 2026-08 --apply --json
    python3 infra/scripts/cli/seo-cli.py report --month 2026-08 \\
        --gsc export.zip --ahrefs ahrefs.json --json
    task seo:report -- fetch --month 2026-08 --json
    task seo:report -- shopify --month 2026-09 --coverage --apply --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

UTILS_DIR = Path(__file__).resolve().parent.parent / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))

from seo_monthly_report import DEFAULT_CLIENT, run  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a Pixiu Watch SEO monthly pack from local exports"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="Show existing monthly packs")
    status.add_argument("--client", default=DEFAULT_CLIENT)
    status.add_argument("--json", action="store_true")

    report = sub.add_parser("report", help="Ingest exports and render a monthly pack")
    report.add_argument("--client", default=DEFAULT_CLIENT)
    report.add_argument("--month", help="YYYY-MM")
    report.add_argument("--gsc", type=Path, help="Google Search Console Performance zip")
    report.add_argument("--ahrefs", type=Path, help="Ahrefs Site Audit JSON or CSV")
    report.add_argument("--ga4", type=Path, help="Optional GA4 JSON export")
    report.add_argument("--fixture", choices=["july-2026"], help="Use the checked-in July 2026 fixture")
    report.add_argument("--source", choices=["files", "gmail"], default="files")
    report.add_argument("--apply", action="store_true", help="Write pack.json, markdown, and HTML")
    report.add_argument("--json", action="store_true")

    fetch = sub.add_parser("fetch", help="Pull Gmail Search Console/Ahrefs notices; try Search Console API")
    fetch.add_argument("--client", default=DEFAULT_CLIENT)
    fetch.add_argument("--month", required=True, help="YYYY-MM")
    fetch.add_argument("--apply", action="store_true")
    fetch.add_argument("--json", action="store_true")

    shopify = sub.add_parser("shopify", help="Pull Shopify sessions, locales, and translation coverage (read-only)")
    shopify.add_argument("--client", default=DEFAULT_CLIENT)
    shopify.add_argument("--month", required=True, help="YYYY-MM")
    shopify.add_argument("--coverage", action="store_true", help="Also scan translation coverage (slow)")
    shopify.add_argument("--apply", action="store_true", help="Write evidence/YYYY-MM/raw/shopify.json")
    shopify.add_argument("--json", action="store_true")

    parser.add_argument("--client", default=DEFAULT_CLIENT)
    return parser


def payload_from_args(args: argparse.Namespace) -> dict:
    command = args.command or "status"
    if command == "shopify":
        from seo_shopify_extract import run_extract

        return run_extract(
            client=args.client or DEFAULT_CLIENT,
            month=args.month,
            coverage=bool(args.coverage),
            apply=bool(args.apply),
        )
    return run(
        command=command,
        client=getattr(args, "client", DEFAULT_CLIENT) or DEFAULT_CLIENT,
        month=getattr(args, "month", None),
        gsc_path=getattr(args, "gsc", None),
        ahrefs_path=getattr(args, "ahrefs", None),
        ga4_path=getattr(args, "ga4", None),
        fixture=getattr(args, "fixture", None),
        source="gmail" if command == "fetch" else getattr(args, "source", "files") or "files",
        apply=bool(getattr(args, "apply", False)),
    )


def print_human(payload: dict) -> None:
    command = payload.get("command")
    print(f"SEO {command}  contract={payload.get('contract')}")
    print(f"  client: {payload.get('client_name') or payload.get('client')}")
    if command == "status":
        print(f"  website: {payload.get('website')}")
        print(f"  live_site_access: {payload.get('live_site_access')}")
        months = payload.get("months") or []
        if not months:
            print("  months: none")
        for row in months:
            flags = []
            if row.get("pack_json"):
                flags.append("pack")
            if row.get("draft_md"):
                flags.append("md")
            if row.get("client_html"):
                flags.append("html")
            if row.get("ga4"):
                flags.append("ga4")
            print(f"  {row['month']}: {', '.join(flags) or 'listed only'}")
        print(f"  next: {payload.get('next_safe_action')}")
        return
    if payload.get("contract") == "solo-empire.seo-shopify-extract.v1":
        window = payload.get("window") or {}
        print(f"  month: {payload.get('month')}  window: {window.get('since')}..{window.get('until')}")
        print(f"  status: {payload.get('status')}")
        for name, result in (payload.get("analytics") or {}).items():
            print(f"  {name}: {result.get('status')} ({len(result.get('rows') or [])} rows)")
        if payload.get("needed"):
            print(f"  needed: {', '.join(payload['needed'])}")
        print(f"  written: {payload.get('written') or payload.get('would_write')}")
        return
    kpis = payload.get("kpis") or {}
    print(f"  month: {payload.get('month')}")
    print(f"  clicks: {kpis.get('clicks')}  impressions: {kpis.get('impressions')}")
    print(f"  ctr: {kpis.get('ctr')}  position: {kpis.get('position')}")
    print(f"  ahrefs_health: {kpis.get('ahrefs_health')}  ga4: {kpis.get('ga4')}")
    print(f"  apply: {payload.get('apply')}  written: {len(payload.get('written') or [])}")
    print("  not sent, no live-site fixes")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        payload = payload_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        error = {"status": "error", "error": str(exc), "contract": "solo-empire.seo-monthly-report.v1"}
        if args.json or getattr(args, "json", False):
            print(json.dumps(error, ensure_ascii=False, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    as_json = bool(args.json or getattr(args, "json", False))
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print_human(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

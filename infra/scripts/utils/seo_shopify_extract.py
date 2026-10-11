#!/usr/bin/env python3
"""Extract Pixiu Watch storefront evidence from the Shopify Admin GraphQL API.

Pulls ShopifyQL session analytics (the GA4 stand-in for the monthly SEO
report), shop locale publish status, and optional translation coverage into
``evidence/YYYY-MM/raw/shopify.json``.

Read-only: it only sends GraphQL queries, never mutations. It needs
``SHOPIFY_ADMIN_TOKEN`` (scopes: read_reports, read_locales, read_markets_home,
read_translations) and optionally ``SHOPIFY_STORE_DOMAIN``. It never prints
the token and never invents numbers when a query fails.
"""

from __future__ import annotations

import json
import re
import urllib.error
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from seo_monthly_report import CLIENTS, DEFAULT_CLIENT


CONTRACT = "solo-empire.seo-shopify-extract.v1"
SHOPIFY_API_VERSION = "2026-07"
DEFAULT_SHOP_DOMAINS = {"pixiu-watch": "pixiu-watch.myshopify.com"}
TARGET_LOCALES = ("en", "zh-TW")
COVERAGE_TYPES = ("PRODUCT", "COLLECTION", "PAGE", "ARTICLE", "BLOG", "LINK", "SHOP_POLICY")
THAI_CHARS = re.compile("[฀-๿]")
NEEDED_SCOPES = ["read_reports", "read_locales", "read_markets_home", "read_translations"]

SHOPIFYQL_QUERY = """
query($q: String!) {
  shopifyqlQuery(query: $q) {
    tableData { columns { name dataType } rows }
    parseErrors
  }
}
"""
LOCALES_QUERY = "{ shopLocales { locale name primary published } }"
COVERAGE_QUERY = """
query($type: TranslatableResourceType!, $after: String) {
  translatableResources(resourceType: $type, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      resourceId
      translatableContent { key value }
      en: translations(locale: "en") { key value outdated }
      zh: translations(locale: "zh-TW") { key value outdated }
    }
  }
}
"""


def month_window(month: str, today: Optional[date] = None) -> dict[str, Any]:
    """Return the ShopifyQL window for ``month`` plus the two prior months.

    A month still in progress ends yesterday, and is flagged ``partial``.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month must be YYYY-MM")
    year, month_n = (int(part) for part in month.split("-"))
    first = date(year, month_n, 1)
    last = date(year, month_n, monthrange(year, month_n)[1])
    today = today or date.today()
    partial = today <= last
    until = min(last, today - timedelta(days=1))
    if until < first:
        raise ValueError(f"{month} has no completed days yet")
    start_month = month_n - 2
    start_year = year
    while start_month < 1:
        start_month += 12
        start_year -= 1
    return {
        "month": month,
        "since": date(start_year, start_month, 1).isoformat(),
        "month_start": first.isoformat(),
        "until": until.isoformat(),
        "partial": partial,
    }


def shopifyql_queries(window: dict[str, Any]) -> dict[str, str]:
    trend = f"SINCE {window['since']} UNTIL {window['until']}"
    current = f"SINCE {window['month_start']} UNTIL {window['until']}"
    return {
        "sessions_monthly": f"FROM sessions SHOW sessions, online_store_visitors TIMESERIES month {trend}",
        "sessions_by_referrer_source": f"FROM sessions SHOW sessions GROUP BY referrer_source, month {trend}",
        "search_sessions_by_engine": (
            f"FROM sessions SHOW sessions WHERE referrer_source = 'search' GROUP BY referrer_name, month {trend}"
        ),
        "search_landing_pages": (
            "FROM sessions SHOW sessions WHERE referrer_source = 'search' "
            f"GROUP BY landing_page_path {current} ORDER BY sessions DESC LIMIT 20"
        ),
        "sessions_by_device": f"FROM sessions SHOW sessions GROUP BY session_device_type {current}",
        "online_orders_monthly": f"FROM sales SHOW orders, total_sales TIMESERIES month {trend}",
    }


def table_rows(table: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize ShopifyQL rows to dicts; the API may return objects or arrays."""
    if not table:
        return []
    names = [col.get("name") for col in table.get("columns") or []]
    rows = []
    for row in table.get("rows") or []:
        if isinstance(row, dict):
            rows.append(dict(row))
        else:
            rows.append(dict(zip(names, row)))
    return rows


def graphql(
    domain: str,
    token: str,
    query: str,
    api_request: Callable[..., Any],
    variables: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    payload = api_request(
        f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json",
        data=body,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
        timeout=60,
    )
    if payload.get("errors"):
        messages = [str(err.get("message", err)) for err in payload["errors"]]
        raise RuntimeError("; ".join(messages)[:300])
    return payload.get("data") or {}


def run_shopifyql(domain: str, token: str, query: str, api_request) -> dict[str, Any]:
    data = graphql(domain, token, SHOPIFYQL_QUERY, api_request, {"q": query})
    result = data.get("shopifyqlQuery") or {}
    errors = result.get("parseErrors") or []
    if errors:
        return {"status": "parse_error", "query": query, "errors": errors, "rows": []}
    return {"status": "ok", "query": query, "rows": table_rows(result.get("tableData"))}


def translation_coverage(domain: str, token: str, api_request) -> dict[str, Any]:
    """Count missing, outdated, and still-Thai translations per resource type.

    ``handle`` is excluded because URLs stay untranslated on purpose.
    """
    summary: dict[str, Any] = {}
    for resource_type in COVERAGE_TYPES:
        counts = {
            "resources": 0,
            "fields": 0,
            "pages": 0,
            **{loc: {"missing": 0, "outdated": 0, "thai_left": 0} for loc in TARGET_LOCALES},
        }
        after = None
        while True:
            data = graphql(domain, token, COVERAGE_QUERY, api_request, {"type": resource_type, "after": after})
            block = data.get("translatableResources") or {}
            counts["pages"] += 1
            for node in block.get("nodes") or []:
                counts["resources"] += 1
                keys = [
                    item["key"]
                    for item in node.get("translatableContent") or []
                    if item.get("key") != "handle" and str(item.get("value") or "").strip()
                ]
                counts["fields"] += len(keys)
                for locale, alias in (("en", "en"), ("zh-TW", "zh")):
                    translated = {item["key"]: item for item in node.get(alias) or []}
                    for key in keys:
                        item = translated.get(key)
                        if item is None:
                            counts[locale]["missing"] += 1
                            continue
                        if item.get("outdated"):
                            counts[locale]["outdated"] += 1
                        if THAI_CHARS.search(str(item.get("value") or "")):
                            counts[locale]["thai_left"] += 1
            page_info = block.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
        summary[resource_type] = counts
    return summary


def _resolve_credentials(client: str) -> tuple[Optional[str], Optional[str]]:
    from _env import get_env, get_secret

    domain = get_env("SHOPIFY_STORE_DOMAIN") or DEFAULT_SHOP_DOMAINS.get(client)
    return domain, get_secret("SHOPIFY_ADMIN_TOKEN")


def extract_shopify(
    *,
    client: str = DEFAULT_CLIENT,
    month: str,
    coverage: bool = False,
    domain: Optional[str] = None,
    token: Optional[str] = None,
    api_request: Optional[Callable[..., Any]] = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    if client not in CLIENTS:
        raise ValueError(f"Unknown SEO client '{client}'")
    window = month_window(month, today)
    base = {
        "contract": CONTRACT,
        "client": client,
        "month": month,
        "window": window,
        "api_version": SHOPIFY_API_VERSION,
    }
    if domain is None or token is None:
        resolved_domain, resolved_token = _resolve_credentials(client)
        domain = domain or resolved_domain
        token = token or resolved_token
    if not domain or not token:
        return {
            **base,
            "status": "credentials_not_configured",
            "needed": ["SHOPIFY_ADMIN_TOKEN", "SHOPIFY_STORE_DOMAIN (optional)"],
            "needed_scopes": NEEDED_SCOPES,
        }
    if api_request is None:
        from _http import api_request as default_api_request

        api_request = default_api_request

    analytics: dict[str, Any] = {}
    for name, query in shopifyql_queries(window).items():
        try:
            analytics[name] = run_shopifyql(domain, token, query, api_request)
        except urllib.error.HTTPError as exc:
            status = "forbidden" if exc.code in {401, 403} else f"http_{exc.code}"
            analytics[name] = {"status": status, "query": query, "rows": []}
        except RuntimeError as exc:
            analytics[name] = {"status": "graphql_error", "query": query, "error": str(exc), "rows": []}

    try:
        locales = {"status": "ok", "rows": graphql(domain, token, LOCALES_QUERY, api_request).get("shopLocales") or []}
    except (urllib.error.HTTPError, RuntimeError) as exc:
        locales = {"status": "error", "error": str(exc)[:200], "rows": []}

    payload = {
        **base,
        "status": "ok",
        "shop_domain": domain,
        "analytics": analytics,
        "shop_locales": locales,
        "notes": [
            "online_orders_monthly counts Shopify checkout only; LINE/phone sales are not visible here.",
        ],
    }
    if window["partial"]:
        payload["notes"].append(f"{month} is still in progress; data ends {window['until']}.")
    if coverage:
        try:
            payload["translation_coverage"] = {
                "status": "ok",
                "locales": list(TARGET_LOCALES),
                "by_type": translation_coverage(domain, token, api_request),
            }
        except (urllib.error.HTTPError, RuntimeError) as exc:
            payload["translation_coverage"] = {"status": "error", "error": str(exc)[:200]}
    failed = [name for name, result in analytics.items() if result.get("status") != "ok"]
    if failed:
        payload["status"] = "partial"
        payload["failed_queries"] = failed
    return payload


def raw_path(client: str, month: str, dest_root: Optional[Path] = None) -> Path:
    delivery_dir = dest_root or CLIENTS[client]["delivery_dir"]
    return Path(delivery_dir) / "evidence" / month / "raw" / "shopify.json"


def run_extract(
    *,
    client: str = DEFAULT_CLIENT,
    month: str,
    coverage: bool = False,
    apply: bool = False,
    dest_root: Optional[Path] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    payload = extract_shopify(client=client, month=month, coverage=coverage, **kwargs)
    target = raw_path(client, month, dest_root)
    payload["apply"] = apply
    payload["written"] = []
    if apply and payload.get("status") in {"ok", "partial"}:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload["written"] = [str(target)]
    else:
        payload["would_write"] = str(target)
    return payload

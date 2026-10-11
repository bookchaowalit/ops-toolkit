#!/usr/bin/env python3
"""Pull Pixiu Watch SEO evidence from Gmail and optional Search Console API.

Gmail/Calendar is the default Google token. Search Console API uses a
separate ``GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN`` with
``webmasters.readonly``. Until that token exists, Gmail notifications remain
the live fallback. This module never scrapes Ahrefs or pixiuwatch.com.

It never prints tokens, never stores raw email bodies, and never invents
missing calendar-month clicks.
"""

from __future__ import annotations

import base64
import html as html_lib
import json
import re
import urllib.error
import urllib.parse
from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Callable, Optional

from seo_monthly_report import CLIENTS, DEFAULT_CLIENT, normalize_device


GSC_API_SITES = "https://www.googleapis.com/webmasters/v3/sites"
WEBMASTERS_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
AHREFS_PROJECTS_URL = "https://api.ahrefs.com/v3/site-audit/projects"
AHREFS_PROJECT_ID = "8985012"
ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
AHREFS_ISSUE_PATTERNS = (
    (r"links to redirect\s+(?:New\s+)?([\d,]+)", "Pages have links to redirects", "high"),
    (r"Image file size too large\s+(?:New\s+)?([\d,]+)", "Image file size too large", "medium"),
    (r"Canonical URL has no incoming internal links\s+(?:New\s+)?([\d,]+)", "Canonical URL has no incoming internal links", "medium"),
    (r"Noindex page in sitemap\s+(?:New\s+)?([\d,]+)", "Noindex page in sitemap", "high"),
    (r"HTTPS page links to HTTP\s+(?:New\s+)?([\d,]+)", "HTTPS page links to HTTP", "high"),
    (r"4XX page\s+(?:New\s+)?([\d,]+)", "4XX pages", "high"),
    (r"404 page\s+(?:New\s+)?([\d,]+)", "404 pages", "high"),
)


def parse_compact_number(value: str) -> Optional[float]:
    text = html_lib.unescape(str(value)).strip().upper().replace(",", "")
    match = re.fullmatch(r"([+-]?[\d.]+)([KMB])?", text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "K":
        return number * 1_000
    if suffix == "M":
        return number * 1_000_000
    if suffix == "B":
        return number * 1_000_000_000
    return number


def parse_signed_int(value: str) -> Optional[int]:
    text = html_lib.unescape(str(value)).strip().replace(",", "").replace("−", "-").replace("–", "-")
    match = re.search(r"([+-]?\d+)", text)
    if not match:
        return None
    return int(match.group(1))


def decode_gmail_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = str(part.get("mimeType") or "")
        data = (part.get("body") or {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            raw = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
            if mime == "text/html":
                raw = html_lib.unescape(re.sub(r"<[^>]+>", " ", raw))
            chunks.append(raw)
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload or {})
    text = " ".join(chunks)
    return re.sub(r"\s+", " ", text).strip()


def parse_gsc_performance_email(subject: str, text: str) -> Optional[dict[str, Any]]:
    subject_match = re.search(
        r"Your\s+([A-Za-z]+)\s+Search performance for\s+(https?://\S+)",
        subject or "",
        re.IGNORECASE,
    )
    if not subject_match:
        return None
    month_name = subject_match.group(1).lower()
    month_n = ENGLISH_MONTHS.get(month_name)
    if not month_n:
        return None
    year_match = re.search(r"(20\d{2})", text or subject or "")
    year = int(year_match.group(1)) if year_match else date.today().year
    clicks_m = re.search(r"([\d.]+K?)\s+Clicks\s+\(web\)", text, re.IGNORECASE)
    impressions_m = re.search(r"([\d.]+K?)\s+Impressions\s+\(web\)", text, re.IGNORECASE)
    pages_m = re.search(r"([\d,]+)\s+Pages with first impressions", text, re.IGNORECASE)
    if not clicks_m or not impressions_m:
        return None
    clicks_compact = clicks_m.group(1)
    impressions_compact = impressions_m.group(1)
    return {
        "source": "gmail",
        "window": "calendar_month",
        "window_label": f"{month_name.title()} {year} calendar month (Gmail notification, compact)",
        "search_type": "Web",
        "month": f"{year}-{month_n:02d}",
        "website": subject_match.group(2).rstrip(">"),
        "clicks": parse_compact_number(clicks_compact),
        "clicks_compact": clicks_compact,
        "impressions": parse_compact_number(impressions_compact),
        "impressions_compact": impressions_compact,
        "pages_first_impressions": parse_signed_int(pages_m.group(1)) if pages_m else None,
        "precision": "gmail_compact_thousands",
        "devices": [],
        "pages": [],
        "queries": [],
        "search_appearance": [],
        "ctr": None,
        "position": None,
    }


def parse_ahrefs_audit_email(subject: str, text: str, received_at: Optional[str] = None) -> dict[str, Any]:
    lowered = f"{subject} {text}".lower()
    failed = "crawl failed" in lowered
    partial = "crawl error" in lowered or "wasn't fully crawled" in lowered or "wasn&#39;t fully crawled" in lowered
    health = re.search(
        r"([\d,]+)\s+internal URLs were analyzed\.\s*Health Score\s+(\d+)\s*\(([^)]+)\)\s*·\s*Errors\s+([\d,]+)\s*\(([^)]+)\)\s*·\s*Warnings\s+([\d,]+)\s*\(([^)]+)\)\s*·\s*Notices\s+([\d,]+)\s*\(([^)]+)\)",
        text,
        re.IGNORECASE,
    )
    issues = []
    for pattern, name, priority in AHREFS_ISSUE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            issues.append({"name": name, "count": parse_signed_int(match.group(1)), "priority": priority})
    status = "ok"
    if failed and not health:
        status = "crawl_failed"
    elif partial and not health:
        status = "crawl_error"
    elif failed:
        status = "crawl_failed"
    elif partial:
        status = "crawl_error"
    payload: dict[str, Any] = {
        "source": "gmail",
        "status": status,
        "subject": subject,
        "received_at": received_at,
        "issues": [row for row in issues if row.get("count") is not None],
    }
    if health:
        payload.update(
            {
                "internal_urls": parse_signed_int(health.group(1)),
                "health_score": parse_signed_int(health.group(2)),
                "health_score_change": parse_signed_int(health.group(3)),
                "errors": parse_signed_int(health.group(4)),
                "errors_change": parse_signed_int(health.group(5)),
                "warnings": parse_signed_int(health.group(6)),
                "warnings_change": parse_signed_int(health.group(7)),
                "notices": parse_signed_int(health.group(8)),
                "notices_change": parse_signed_int(health.group(9)),
                "crawl_date": (received_at or "")[:10] or None,
            }
        )
        if status == "ok" and (failed or partial):
            payload["status"] = "ok"
    return payload


def parse_gsc_index_alert(subject: str, text: str, received_at: Optional[str] = None) -> Optional[dict[str, Any]]:
    if "prevent" not in (subject or "").lower() and "index" not in (subject or "").lower():
        return None
    reason = None
    match = re.search(r"following new reason:\s*([^.]{3,80})", text or "", re.IGNORECASE)
    if match:
        reason = match.group(1).strip()
    return {
        "type": "indexing",
        "subject": subject,
        "reason": reason,
        "received_at": received_at,
    }


def header_map(message: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name") or "").lower(): str(item.get("value") or "")
        for item in (message.get("payload") or {}).get("headers") or []
        if isinstance(item, dict)
    }


def gmail_list(token: str, query: str, api_request, limit: int = 20) -> list[str]:
    params = urllib.parse.urlencode({"q": query, "maxResults": min(limit, 50)})
    listing = api_request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    return [str(item["id"]) for item in listing.get("messages") or [] if item.get("id")]


def gmail_full(token: str, message_id: str, api_request) -> dict[str, Any]:
    return api_request(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=full",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )


def month_windows(month: str, today: Optional[date] = None) -> dict[str, str]:
    today = today or date.today()
    year, month_n = [int(part) for part in month.split("-")]
    start = date(year, month_n, 1)
    last = date(year, month_n, monthrange(year, month_n)[1])
    end = min(last, today - timedelta(days=1))
    if end < start:
        end = start
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "previous_start": prev_start.isoformat(),
        "previous_end": prev_end.isoformat(),
        "incomplete": end < last,
    }


def select_gsc_site(sites: list[str], website: str) -> Optional[str]:
    host = urllib.parse.urlparse(website).hostname or ""
    candidates = [website, website.rstrip("/") + "/", f"sc-domain:{host}"]
    for candidate in candidates:
        if candidate in sites:
            return candidate
    for site in sites:
        if host and host in site:
            return site
    return sites[0] if sites else None


def api_ctr_to_percent(value: Any) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if 0 <= abs(number) <= 1:
        return number * 100
    return number


def gsc_rows_to_items(rows: list[dict[str, Any]], *, device: bool = False) -> list[dict[str, Any]]:
    items = []
    for row in rows or []:
        keys = row.get("keys") or [""]
        key = str(keys[0]) if keys else ""
        if device:
            key = normalize_device(key)
        item = {
            "key": key,
            "clicks": row.get("clicks"),
            "impressions": row.get("impressions"),
            "ctr": api_ctr_to_percent(row.get("ctr")),
            "position": row.get("position"),
        }
        items.append(item)
    return items


def merge_previous_rows(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev_map = {row["key"]: row for row in previous}
    merged = []
    for row in current:
        item = dict(row)
        prev = prev_map.get(row["key"])
        if prev:
            item["clicks_previous"] = prev.get("clicks")
            item["impressions_previous"] = prev.get("impressions")
            item["ctr_previous"] = prev.get("ctr")
            item["position_previous"] = prev.get("position")
            if row.get("clicks") is not None and prev.get("clicks") is not None:
                item["clicks_change"] = row["clicks"] - prev["clicks"]
        merged.append(item)
    return merged


def query_search_analytics(
    token: str,
    site_url: str,
    start: str,
    end: str,
    dimensions: list[str],
    api_request,
    row_limit: int = 25,
) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(site_url, safe="")
    url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded}/searchAnalytics/query"
    body = json.dumps(
        {
            "startDate": start,
            "endDate": end,
            "dimensions": dimensions,
            "searchType": "web",
            "rowLimit": row_limit,
            "dataState": "all",
        }
    ).encode()
    payload = api_request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
        timeout=60,
    )
    return list(payload.get("rows") or [])


def gsc_pack_from_api(
    token: str,
    site_url: str,
    month: str,
    api_request,
    today: Optional[date] = None,
) -> dict[str, Any]:
    windows = month_windows(month, today)
    devices = merge_previous_rows(
        gsc_rows_to_items(
            query_search_analytics(token, site_url, windows["start"], windows["end"], ["device"], api_request, 10),
            device=True,
        ),
        gsc_rows_to_items(
            query_search_analytics(
                token, site_url, windows["previous_start"], windows["previous_end"], ["device"], api_request, 10
            ),
            device=True,
        ),
    )
    queries = merge_previous_rows(
        gsc_rows_to_items(query_search_analytics(token, site_url, windows["start"], windows["end"], ["query"], api_request, 30)),
        gsc_rows_to_items(
            query_search_analytics(
                token, site_url, windows["previous_start"], windows["previous_end"], ["query"], api_request, 30
            )
        ),
    )
    pages = merge_previous_rows(
        gsc_rows_to_items(query_search_analytics(token, site_url, windows["start"], windows["end"], ["page"], api_request, 20)),
        gsc_rows_to_items(
            query_search_analytics(
                token, site_url, windows["previous_start"], windows["previous_end"], ["page"], api_request, 20
            )
        ),
    )
    appearance = merge_previous_rows(
        gsc_rows_to_items(
            query_search_analytics(
                token, site_url, windows["start"], windows["end"], ["searchAppearance"], api_request, 10
            )
        ),
        gsc_rows_to_items(
            query_search_analytics(
                token,
                site_url,
                windows["previous_start"],
                windows["previous_end"],
                ["searchAppearance"],
                api_request,
                10,
            )
        ),
    )
    clicks = sum(row.get("clicks") or 0 for row in devices)
    impressions = sum(row.get("impressions") or 0 for row in devices)
    clicks_previous = sum(row.get("clicks_previous") or 0 for row in devices) or None
    impressions_previous = sum(row.get("impressions_previous") or 0 for row in devices) or None
    weighted = sum((row.get("position") or 0) * (row.get("impressions") or 0) for row in devices)
    weighted_prev = sum(
        (row.get("position_previous") or 0) * (row.get("impressions_previous") or 0) for row in devices
    )
    label = f"{windows['start']} to {windows['end']} (Search Console API"
    if windows["incomplete"]:
        label += ", incomplete calendar month"
    label += ")"
    return {
        "source": "search-console-api",
        "window": "calendar_month" if not windows["incomplete"] else "calendar_month_to_date",
        "window_label": label,
        "search_type": "Web",
        "source_file": f"gsc-api:{site_url}:{windows['start']}:{windows['end']}",
        "site_url": site_url,
        "clicks": clicks or None,
        "clicks_previous": clicks_previous,
        "impressions": impressions or None,
        "impressions_previous": impressions_previous,
        "ctr": (clicks / impressions * 100) if clicks and impressions else None,
        "ctr_previous": (
            (clicks_previous / impressions_previous * 100) if clicks_previous and impressions_previous else None
        ),
        "position": (weighted / impressions) if impressions else None,
        "position_previous": (weighted_prev / impressions_previous) if impressions_previous else None,
        "devices": devices,
        "pages": sorted(pages, key=lambda row: row.get("clicks") or 0, reverse=True)[:20],
        "queries": sorted(queries, key=lambda row: row.get("clicks") or 0, reverse=True)[:30],
        "search_appearance": appearance,
        "precision": "search_console_api",
    }


def probe_ahrefs_api(api_key: str, api_request, *, project_id: str = AHREFS_PROJECT_ID) -> dict[str, Any]:
    """Read Site Audit project health. This endpoint does not consume API units."""
    params = urllib.parse.urlencode({"output": "json", "select": "project_id,project_name,target_url,date,status,health_score"})
    try:
        payload = api_request(
            f"{AHREFS_PROJECTS_URL}?{params}",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        return {
            "status": "forbidden" if exc.code in {401, 403} else f"http_{exc.code}",
            "http_status": exc.code,
            "needed": "AHREFS_API_KEY from Account settings / API keys",
        }
    rows = payload.get("healthscores") or payload.get("projects") or []
    match = None
    for row in rows:
        if str(row.get("project_id")) == str(project_id):
            match = row
            break
        name = str(row.get("project_name") or "").lower()
        target = str(row.get("target_url") or "").lower()
        if "pixiu" in name or "pixiuwatch" in target:
            match = row
            break
    return {
        "status": "ok",
        "project": match,
        "project_count": len(rows),
        "units_consumed": 0,
    }


def probe_gsc_api(token: str, api_request) -> dict[str, Any]:
    try:
        payload = api_request(
            GSC_API_SITES,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        sites = [row.get("siteUrl") for row in payload.get("siteEntry") or [] if row.get("siteUrl")]
        return {"status": "ok", "sites": sites, "needed_scope": WEBMASTERS_SCOPE}
    except urllib.error.HTTPError as exc:
        return {
            "status": "forbidden" if exc.code == 403 else f"http_{exc.code}",
            "http_status": exc.code,
            "needed_scope": WEBMASTERS_SCOPE,
            "sites": [],
        }


def fetch_live(
    *,
    client: str = DEFAULT_CLIENT,
    month: str,
    token_factory: Optional[Callable[[], str]] = None,
    api_request: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    if client not in CLIENTS:
        raise ValueError(f"Unknown SEO client '{client}'")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month must be YYYY-MM")
    cfg = CLIENTS[client]
    host = urllib.parse.urlparse(cfg["website"]).hostname or "pixiuwatch.com"

    if token_factory is None or api_request is None:
        from _env import get_google_creds, get_secret
        from _http import api_request as default_api_request
        from _http import get_access_token

        def token_factory() -> str:  # type: ignore[misc]
            client_id, client_secret, refresh_token = get_google_creds()
            return get_access_token(client_id, client_secret, refresh_token)

        def gsc_token_factory() -> str:
            client_id, client_secret, refresh_token = get_google_creds()
            sc_refresh = get_secret("GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN")
            if sc_refresh:
                return get_access_token(client_id, client_secret, sc_refresh)
            return get_access_token(client_id, client_secret, refresh_token)

        api_request = default_api_request
    else:
        gsc_token_factory = token_factory  # type: ignore[misc]

    token = token_factory()
    gsc_token = gsc_token_factory()
    gsc_api = probe_gsc_api(gsc_token, api_request)
    ahrefs_api = {"status": "credentials_not_configured", "needed": "AHREFS_API_KEY"}
    try:
        from _env import get_secret as _get_secret
        ahrefs_key = _get_secret("AHREFS_API_KEY")
    except Exception:
        ahrefs_key = None
    if ahrefs_key:
        ahrefs_api = probe_ahrefs_api(ahrefs_key, api_request)
    gsc_from_api = None
    if gsc_api.get("status") == "ok":
        site_url = select_gsc_site(list(gsc_api.get("sites") or []), cfg["website"])
        gsc_api["selected_site"] = site_url
        if site_url:
            try:
                gsc_from_api = gsc_pack_from_api(gsc_token, site_url, month, api_request)
            except urllib.error.HTTPError as exc:
                gsc_api["query_status"] = f"http_{exc.code}"
                gsc_from_api = None

    gsc_ids = gmail_list(
        token,
        f'from:sc-noreply@google.com (subject:"Search performance" OR subject:"indexed") {host}',
        api_request,
        20,
    )
    ahrefs_ids = gmail_list(
        token,
        f"from:sa@ahrefs.com (Pixiuwatch OR {host})",
        api_request,
        20,
    )

    performance: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    ahrefs_events: list[dict[str, Any]] = []

    for message_id in gsc_ids:
        message = gmail_full(token, message_id, api_request)
        headers = header_map(message)
        text = decode_gmail_text(message.get("payload") or {})
        subject = headers.get("subject", "")
        received = headers.get("date")
        parsed = parse_gsc_performance_email(subject, text)
        if parsed:
            parsed["gmail_id"] = message_id
            parsed["received_at"] = received
            parsed["source_file"] = f"gmail:{message_id}"
            performance.append(parsed)
            continue
        alert = parse_gsc_index_alert(subject, text, received)
        if alert and _ahrefs_month_guess(received) == month:
            alert["gmail_id"] = message_id
            alerts.append(alert)

    for message_id in ahrefs_ids:
        message = gmail_full(token, message_id, api_request)
        headers = header_map(message)
        text = decode_gmail_text(message.get("payload") or {})
        event = parse_ahrefs_audit_email(headers.get("subject", ""), text, headers.get("date"))
        event["gmail_id"] = message_id
        ahrefs_events.append(event)

    current = gsc_from_api
    if current is None:
        current = next((row for row in performance if row.get("month") == month), None)
        if current:
            year, month_n = [int(part) for part in month.split("-")]
            prev_month = f"{year - 1}-12" if month_n == 1 else f"{year}-{month_n - 1:02d}"
            previous = next((row for row in performance if row.get("month") == prev_month), None)
            if previous:
                current = {
                    **current,
                    "clicks_previous": previous.get("clicks"),
                    "impressions_previous": previous.get("impressions"),
                    "clicks_previous_compact": previous.get("clicks_compact"),
                    "impressions_previous_compact": previous.get("impressions_compact"),
                }

    successful_ahrefs = [row for row in ahrefs_events if row.get("health_score") is not None]
    month_failures = [
        row
        for row in ahrefs_events
        if row.get("status") in {"crawl_failed", "crawl_error"}
        and _ahrefs_month_guess(row.get("received_at")) == month
    ]
    last_success = successful_ahrefs[0] if successful_ahrefs else None
    month_success = next(
        (row for row in successful_ahrefs if _ahrefs_month_guess(row.get("received_at")) == month),
        None,
    )
    ahrefs = month_success or None
    if ahrefs is None and month_failures:
        ahrefs = {
            "source": "gmail",
            "status": "crawl_failed",
            "failures": [
                {"subject": row.get("subject"), "received_at": row.get("received_at"), "status": row.get("status")}
                for row in month_failures
            ],
            "last_success": last_success,
            "issues": [],
            "health_score": None,
            "note": "Ahrefs crawls in this month failed; last successful audit is attached as last_success only.",
        }

    return {
        "client": client,
        "month": month,
        "website": cfg["website"],
        "gsc_api": gsc_api,
        "ahrefs_api": ahrefs_api,
        "gsc": current,
        "gsc_pending": current is None,
        "last_known_gsc": None if current else (performance[0] if performance else None),
        "ahrefs": ahrefs,
        "ahrefs_events": [
            {"status": row.get("status"), "subject": row.get("subject"), "received_at": row.get("received_at")}
            for row in ahrefs_events[:8]
        ],
        "alerts": alerts[:5],
        "does_not": [
            "call Search Console API successfully without webmasters.readonly",
            "log in to Ahrefs with username/password",
            "call Ahrefs API without AHREFS_API_KEY",
            "crawl pixiuwatch.com",
            "invent calendar-month clicks when the monthly Gmail notice is missing",
        ],
    }


def _ahrefs_month_guess(received_at: Optional[str]) -> str:
    if not received_at:
        return ""
    named = re.search(
        r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d{2})",
        received_at,
        re.I,
    )
    if named:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        return f"{named.group(3)}-{months[named.group(2)[:3].lower()]:02d}"
    iso = re.search(r"(20\d{2})-(\d{2})", received_at)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}"
    return ""

#!/usr/bin/env python3
"""Build a client SEO monthly pack from Search Console and Ahrefs exports.

This command never calls Google, Ahrefs, or GA4 APIs, never crawls the live
site, never sends Fastwork messages, and never invents missing metrics.

Client exports stay in the client's private delivery repository under
``projects/product/engineering/book-dev/github/bookchaowalit-freelance/<client>/seo/``,
not in a public lake landing zone.

Usage:
    python3 infra/scripts/cli/seo-cli.py --json
    python3 infra/scripts/cli/seo-cli.py status --client pixiu-watch --json
    python3 infra/scripts/cli/seo-cli.py report --month 2026-08 \\
        --gsc path/to/gsc.zip --ahrefs path/to/ahrefs.json --json
    python3 infra/scripts/cli/seo-cli.py report --fixture july-2026 --apply --json
    task seo:report -- --json
    task seo:report -- report --fixture july-2026 --json
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from _env import PROJECT_ROOT, book_dev_github_root


CONTRACT = "solo-empire.seo-monthly-report.v1"
BANGKOK = ZoneInfo("Asia/Bangkok")
DEFAULT_CLIENT = "pixiu-watch"
THAI_MONTHS = {
    1: "มกราคม",
    2: "กุมภาพันธ์",
    3: "มีนาคม",
    4: "เมษายน",
    5: "พฤษภาคม",
    6: "มิถุนายน",
    7: "กรกฎาคม",
    8: "สิงหาคม",
    9: "กันยายน",
    10: "ตุลาคม",
    11: "พฤศจิกายน",
    12: "ธันวาคม",
}
DEVICE_LABELS = {
    "MOBILE": "Mobile",
    "DESKTOP": "Desktop",
    "TABLET": "Tablet",
}
GSC_TABLE_ALIASES = {
    "queries.csv": "queries",
    "pages.csv": "pages",
    "devices.csv": "devices",
    "countries.csv": "countries",
    "dates.csv": "dates",
    "search appearance.csv": "search_appearance",
    "filters.csv": "filters",
}
CLIENTS = {
    "pixiu-watch": {
        "name": "Pixiu Watch",
        "website": "https://pixiuwatch.com/",
        "delivery_dir": (book_dev_github_root() or PROJECT_ROOT / "book-dev-github-unavailable") / "bookchaowalit-freelance" / "pixiu" / "seo",
        "live_site_access": False,
        "blocked_reason": (
            "pixiuwatch.com CMS/Shopify access is not in this workspace. "
            "Technical fixes stay on TECHNICAL-FIX-BACKLOG.md."
        ),
    }
}


JULY_2026_GSC = {
    "window": "last_28_days",
    "window_label": "Last 28 days",
    "source_file": "https___pixiuwatch.com_-Performance-on-Search-2026-07-31.zip",
    "search_type": "Web",
    "clicks": 3483,
    "clicks_previous": 3013,
    "impressions": 162366,
    "impressions_previous": 156183,
    "ctr": 2.145148615,
    "ctr_previous": 1.929146,
    "position": 7.6708,
    "position_previous": 7.58,
    "devices": [
        {
            "key": "Mobile",
            "clicks": 2653,
            "clicks_previous": 2243,
            "impressions": 113363,
            "impressions_previous": 107000,
            "ctr": 2.3403,
            "position": 6.56,
        },
        {
            "key": "Desktop",
            "clicks": 753,
            "clicks_previous": 700,
            "impressions": 45928,
            "impressions_previous": 45000,
            "ctr": 1.6391,
            "position": 10.53,
        },
        {
            "key": "Tablet",
            "clicks": 77,
            "clicks_previous": 70,
            "impressions": 3075,
            "impressions_previous": 4183,
            "ctr": 2.5041,
            "position": 5.92,
        },
    ],
    "pages": [
        {"key": "Homepage", "clicks": 531, "ctr": 3.41, "position": 5.83, "clicks_change": None},
        {
            "key": "Gold-watch-symbol knowledge article",
            "clicks": 217,
            "ctr": 2.20,
            "position": 6.23,
        },
        {"key": "Rolex collection", "clicks": 190, "ctr": 1.19, "position": 8.04},
        {"key": "Sell / trade page", "clicks": 161, "ctr": 1.26, "position": 7.12, "clicks_change": 51},
        {
            "key": "IWC second-hand collection",
            "clicks": 159,
            "ctr": 3.62,
            "position": 5.28,
            "clicks_change": 47,
        },
        {
            "key": "Jaeger-LeCoultre second-hand collection",
            "clicks": 119,
            "ctr": 4.16,
            "position": 5.18,
            "clicks_change": 56,
        },
        {
            "key": "Tudor collection",
            "clicks": 101,
            "ctr": 5.84,
            "position": 9.40,
            "clicks_change": 70,
        },
    ],
    "queries": [
        {"key": "นาฬิกามือสอง", "clicks": 90, "clicks_change": 10, "ctr": 7.47, "position": 4.02},
        {"key": "pixiu watch", "clicks": 77, "clicks_change": 13, "ctr": 17.66, "position": 1.25},
        {"key": "นาฬิกา มือสอง", "clicks": 36, "clicks_change": 9, "ctr": 16.44, "position": 3.68},
        {
            "key": "ซื้อขายนาฬิกา มือ 2",
            "clicks": 29,
            "clicks_change": -7,
            "ctr": 12.66,
            "position": 2.74,
        },
        {"key": "tudor มือสอง", "clicks": 28, "clicks_change": 14, "ctr": 18.06, "position": 5.45},
        {"key": "iwc ราคา", "clicks": 24, "clicks_change": 11, "ctr": 10.53, "position": 3.57},
        {"key": "rolex มือสอง", "clicks": 21, "clicks_change": -6, "ctr": 4.44, "position": 6.12},
        {"key": "iwc", "clicks": 20, "clicks_change": 13, "ctr": 1.96, "position": 5.67},
        {"key": "นาฬิกาหรู", "clicks": 12, "clicks_change": -18, "ctr": 1.10, "position": 8.40},
        {"key": "grand seiko มือสอง", "clicks": 8, "clicks_change": -9, "ctr": 2.20, "position": 7.10},
        {"key": "ขายนาฬิกามือสอง", "clicks": 7, "clicks_change": -8, "ctr": 3.10, "position": 6.80},
    ],
    "search_appearance": [
        {
            "key": "Product snippets",
            "clicks": 106,
            "clicks_previous": 145,
            "impressions": 2497,
            "ctr": 4.25,
            "ctr_previous": 3.21,
            "position": 7.94,
        }
    ],
}

JULY_2026_AHREFS = {
    "source": "ahrefs-site-audit",
    "crawl_date": "2026-07-30",
    "health_score": 99,
    "health_score_change": 98,
    "errors": 219,
    "errors_change": -2457,
    "warnings": 3908,
    "warnings_change": 1208,
    "notices": 3858,
    "notices_change": 3796,
    "internal_urls": 3913,
    "issues": [
        {"name": "Pages have links to redirects", "count": 3578, "priority": "high"},
        {"name": "Image file size too large", "count": 169, "priority": "medium"},
        {"name": "Canonical URL has no incoming internal links", "count": 29, "priority": "medium"},
        {"name": "Links to broken pages / 4XX / 404 pages", "count": 5, "priority": "high"},
        {"name": "Noindex page in sitemap", "count": 1, "priority": "high"},
        {"name": "HTTPS page links to HTTP", "count": 1, "priority": "high"},
        {"name": "Broken image", "count": 1, "priority": "high"},
    ],
}

FIXTURES = {
    "july-2026": {
        "client": "pixiu-watch",
        "month": "2026-07",
        "prepared_on": "2026-07-31",
        "gsc": JULY_2026_GSC,
        "ahrefs": JULY_2026_AHREFS,
        "ga4": None,
    }
}


def today_bangkok() -> date:
    return datetime.now(BANGKOK).date()


def month_label(month: str) -> str:
    year, month_n = month.split("-")
    return f"{THAI_MONTHS[int(month_n)]} {year}"


def english_month_label(month: str) -> str:
    year, month_n = month.split("-")
    return date(int(year), int(month_n), 1).strftime("%B %Y")


def client_window_label(gsc: dict[str, Any]) -> str:
    label = str(gsc.get("window_label") or "").strip()
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", label)
    if match:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
        if start.year == end.year and start.month == end.month:
            display = f"{start.day}–{end.day} {THAI_MONTHS[end.month]} {end.year}"
        else:
            display = f"{start.isoformat()}–{end.isoformat()}"
        if gsc.get("window") == "calendar_month_to_date" or "incomplete calendar month" in label.lower():
            return f"{display} (ข้อมูล Search Console API; ยังไม่ครบเดือน)"
        return display
    if gsc.get("window") == "last_28_days":
        return "28 วันล่าสุด"
    return label or "ช่วงข้อมูลที่แนบ"


def source_label(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return path.name


def parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "n/a", "N/A"}:
        return None
    text = text.replace(",", "").replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_pct(value: Any) -> Optional[float]:
    """Return a percentage point value (2.15 means 2.15%)."""
    if value is None:
        return None
    raw = str(value).strip()
    number = parse_number(value)
    if number is None:
        return None
    if "%" not in raw and 0 < abs(number) <= 1:
        return number * 100
    return number


def fmt_int(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{int(round(value)):,}"


def fmt_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"


def fmt_pos(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def fmt_delta_pct(current: Optional[float], previous: Optional[float]) -> str:
    if current is None or previous in {None, 0}:
        return "—"
    change = (current - previous) / previous * 100
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.1f}%"


def fmt_delta_pp(current: Optional[float], previous: Optional[float]) -> str:
    if current is None or previous is None:
        return "—"
    change = current - previous
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.2f} percentage points"


def rel_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in {None, 0}:
        return None
    return (current - previous) / previous * 100


def clicks_share(part: Optional[float], total: Optional[float]) -> Optional[float]:
    if part is None or not total:
        return None
    return part / total * 100


def normalize_device(name: str) -> str:
    return DEVICE_LABELS.get(name.strip().upper(), name.strip().title())


def _metric_key(name: str) -> str:
    lowered = name.strip().lower()
    if lowered in {"clicks", "click"}:
        return "clicks"
    if lowered in {"impressions", "impression"}:
        return "impressions"
    if lowered in {"ctr", "click through rate", "click-through rate"}:
        return "ctr"
    if lowered in {"position", "avg. position", "average position"}:
        return "position"
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


def parse_gsc_table(text: str) -> list[dict[str, Any]]:
    text = text.lstrip("\ufeff")
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [cell.strip() for cell in rows[0]]
    body = rows[1:]
    if body and (
        body[0][0].strip() == ""
        or any("last" in cell.lower() or "previous" in cell.lower() for cell in body[0])
    ):
        body = body[1:]

    seen: dict[str, int] = {}
    index: dict[str, int] = {}
    for i, name in enumerate(header):
        if i == 0:
            continue
        key = _metric_key(name)
        count = seen.get(key, 0)
        seen[key] = count + 1
        suffix = "current" if count == 0 else "previous"
        index[f"{key}_{suffix}"] = i

    parsed: list[dict[str, Any]] = []
    for row in body:
        if not row or not str(row[0]).strip():
            continue
        item: dict[str, Any] = {"key": row[0].strip()}
        for field, col in index.items():
            if col >= len(row):
                continue
            raw = row[col]
            if field.startswith("ctr"):
                item[field] = parse_pct(raw)
            else:
                item[field] = parse_number(raw)
        for metric in ("clicks", "impressions", "ctr", "position"):
            current = item.get(f"{metric}_current")
            previous = item.get(f"{metric}_previous")
            if f"{metric}_current" in item:
                item[metric] = current
            if metric == "clicks" and current is not None and previous is not None:
                item["clicks_change"] = current - previous
        parsed.append(item)
    return parsed


def parse_gsc_filters(text: str) -> dict[str, str]:
    text = text.lstrip("\ufeff")
    out: dict[str, str] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        key = row[0].strip().lower()
        if key in {"filter", "name"}:
            continue
        out[key] = row[1].strip()
    return out


def _zip_table_name(name: str) -> Optional[str]:
    stem = Path(name).name.lower()
    return GSC_TABLE_ALIASES.get(stem)


def parse_gsc_zip(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Search Console export not found: {path}")
    tables: dict[str, Any] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            table = _zip_table_name(info.filename)
            if not table:
                continue
            raw = archive.read(info.filename).decode("utf-8-sig")
            if table == "filters":
                tables[table] = parse_gsc_filters(raw)
            else:
                tables[table] = parse_gsc_table(raw)
    if not tables:
        raise ValueError("Search Console zip has no Queries/Pages/Devices/Dates tables")
    return summarize_gsc(tables, source_label(path))


def summarize_gsc(tables: dict[str, Any], source: Optional[str]) -> dict[str, Any]:
    devices = [
        {**row, "key": normalize_device(str(row.get("key", "")))}
        for row in tables.get("devices") or []
    ]
    queries = tables.get("queries") or []
    pages = tables.get("pages") or []
    appearance = tables.get("search_appearance") or []
    filters = tables.get("filters") or {}

    if devices:
        clicks = sum(row.get("clicks") or 0 for row in devices)
        impressions = sum(row.get("impressions") or 0 for row in devices)
        clicks_previous = sum(row.get("clicks_previous") or 0 for row in devices) or None
        impressions_previous = sum(row.get("impressions_previous") or 0 for row in devices) or None
        weighted_now = sum((row.get("position") or 0) * (row.get("impressions") or 0) for row in devices)
        weighted_prev = sum(
            (row.get("position_previous") or 0) * (row.get("impressions_previous") or 0)
            for row in devices
        )
        position = (weighted_now / impressions) if impressions else None
        position_previous = (
            (weighted_prev / impressions_previous) if impressions_previous else None
        )
    else:
        dates = tables.get("dates") or []
        clicks = sum(row.get("clicks") or 0 for row in dates) if dates else None
        impressions = sum(row.get("impressions") or 0 for row in dates) if dates else None
        clicks_previous = None
        impressions_previous = None
        position = None
        position_previous = None

    ctr = (clicks / impressions * 100) if clicks is not None and impressions else None
    ctr_previous = (
        (clicks_previous / impressions_previous * 100)
        if clicks_previous is not None and impressions_previous
        else None
    )

    window_label = (
        filters.get("date")
        or filters.get("date range")
        or filters.get("dates")
        or "Last 28 days"
    )
    search_type = filters.get("search type") or filters.get("type") or "Web"
    window = "last_28_days" if "28" in window_label.lower() or "ล่าสุด" in window_label else "custom"

    return {
        "window": window,
        "window_label": window_label,
        "search_type": search_type,
        "source_file": source,
        "clicks": clicks,
        "clicks_previous": clicks_previous,
        "impressions": impressions,
        "impressions_previous": impressions_previous,
        "ctr": ctr,
        "ctr_previous": ctr_previous,
        "position": position,
        "position_previous": position_previous,
        "devices": devices,
        "pages": sorted(pages, key=lambda row: row.get("clicks") or 0, reverse=True)[:20],
        "queries": sorted(queries, key=lambda row: row.get("clicks") or 0, reverse=True)[:30],
        "search_appearance": appearance,
    }


def parse_ahrefs(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Ahrefs export not found: {path}")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Ahrefs JSON must be an object")
        return normalize_ahrefs(data, source_label(path))
    return normalize_ahrefs(parse_ahrefs_csv(path.read_text(encoding="utf-8-sig")), source_label(path))


def parse_ahrefs_csv(text: str) -> dict[str, Any]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    issues = []
    summary: dict[str, Any] = {"issues": issues}
    for row in rows:
        lowered = {key.lower().strip(): value for key, value in row.items() if key}
        if "health" in lowered or "health_score" in lowered:
            summary["health_score"] = parse_number(lowered.get("health") or lowered.get("health_score"))
        name = lowered.get("issue") or lowered.get("name") or lowered.get("title")
        count = parse_number(lowered.get("count") or lowered.get("urls") or lowered.get("pages"))
        if name and count is not None:
            issues.append(
                {
                    "name": name.strip(),
                    "count": int(count),
                    "priority": (lowered.get("priority") or "medium").strip().lower(),
                }
            )
    return summary


def normalize_ahrefs(data: dict[str, Any], source: Optional[str]) -> dict[str, Any]:
    issues = []
    for item in data.get("issues") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("issue") or "").strip()
        count = parse_number(item.get("count"))
        if not name or count is None:
            continue
        issues.append(
            {
                "name": name,
                "count": int(count),
                "priority": str(item.get("priority") or "medium").strip().lower(),
            }
        )
    return {
        "source": data.get("source") or "ahrefs-site-audit",
        "source_file": source,
        "crawl_date": data.get("crawl_date"),
        "health_score": parse_number(data.get("health_score")),
        "health_score_change": parse_number(data.get("health_score_change")),
        "errors": parse_number(data.get("errors")),
        "errors_change": parse_number(data.get("errors_change")),
        "warnings": parse_number(data.get("warnings")),
        "warnings_change": parse_number(data.get("warnings_change")),
        "notices": parse_number(data.get("notices")),
        "notices_change": parse_number(data.get("notices_change")),
        "internal_urls": parse_number(data.get("internal_urls")),
        "issues": issues,
    }


def parse_ga4(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"GA4 export not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GA4 JSON must be an object")
    users = parse_number(data.get("users") or data.get("active_users"))
    sessions = parse_number(data.get("sessions"))
    if users is None and sessions is None:
        raise ValueError("GA4 JSON needs users and/or sessions; do not invent zeros")
    return {
        "source_file": source_label(path),
        "users": users,
        "sessions": sessions,
        "engaged_sessions": parse_number(data.get("engaged_sessions")),
        "engagement_rate": parse_pct(data.get("engagement_rate")),
    }


def limitations(pack: dict[str, Any], client_cfg: dict[str, Any]) -> list[str]:
    notes = []
    if not pack.get("ga4"):
        notes.append("GA4 calendar-month export is unavailable; do not present traffic as zero.")
    gsc = pack.get("gsc") or {}
    if pack.get("gsc_pending") or not gsc:
        notes.append(
            "Search Console calendar-month Gmail notice for this month has not arrived; clicks are pending, not zero."
        )
        last = pack.get("last_known_gsc") or {}
        if last.get("month"):
            notes.append(
                f"Latest Gmail Search Console notice is {last.get('month')} "
                f"({last.get('clicks_compact') or last.get('clicks')} clicks, "
                f"{last.get('impressions_compact') or last.get('impressions')} impressions)."
            )
    if gsc.get("window") == "last_28_days":
        notes.append(
            "Search Console figures are a rolling Last 28 days window, not a 1–31 calendar month."
        )
    if gsc.get("precision") == "gmail_compact_thousands":
        notes.append(
            "Gmail Search Console numbers are compact (3.82K / 179K). Do not treat them as exact integer exports."
        )
    ahrefs = pack.get("ahrefs") or {}
    if not ahrefs:
        notes.append("Ahrefs Site Audit export was not attached.")
    elif ahrefs.get("status") in {"crawl_failed", "crawl_error"}:
        notes.append("Ahrefs crawls in this month failed or were partial; do not reuse last month's health as this month's score.")
    else:
        notes.append("Ahrefs figures are Site Audit only, not Organic Keywords or Backlinks.")
    api = pack.get("gsc_api") or {}
    if api.get("status") and api.get("status") != "ok":
        notes.append(
            f"Search Console API is {api.get('status')}; needs {api.get('needed_scope') or 'webmasters.readonly'}."
        )
    if not client_cfg.get("live_site_access"):
        notes.append(client_cfg["blocked_reason"])
    notes.append("This CLI does not crawl, patch CMS pages, or send the report to the client.")
    return notes


def top_movers(rows: list[dict[str, Any]], *, positive: bool, limit: int = 5) -> list[dict[str, Any]]:
    scored = []
    for row in rows:
        change = row.get("clicks_change")
        if change is None:
            continue
        if positive and change > 0:
            scored.append(row)
        if not positive and change < 0:
            scored.append(row)
    scored.sort(key=lambda row: abs(row.get("clicks_change") or 0), reverse=True)
    return scored[:limit]


def seo_opportunities(gsc: dict[str, Any]) -> list[dict[str, str]]:
    """Build evidence-backed Pixiu opportunities without hiding KPI declines."""
    queries = {str(row.get("key") or "").lower(): row for row in gsc.get("queries") or []}
    pages = gsc.get("pages") or []
    appearance = (gsc.get("search_appearance") or [None])[0]
    opportunities: list[dict[str, str]] = []

    def signed_clicks(row: Optional[dict[str, Any]]) -> str:
        change = (row or {}).get("clicks_change")
        return f"{int(round(change)):+,}" if change is not None else "—"

    def page_containing(fragment: str) -> Optional[dict[str, Any]]:
        return next((row for row in pages if fragment in str(row.get("key") or "")), None)

    brand = queries.get("pixiu watch")
    homepage = next(
        (
            row
            for row in pages
            if str(row.get("key") or "").rstrip("/") == "https://pixiuwatch.com"
        ),
        None,
    )
    if brand and (brand.get("clicks_change") or 0) > 0:
        homepage_evidence = (
            f"; Homepage {fmt_int(homepage.get('clicks'))} clicks ({signed_clicks(homepage)}), "
            f"CTR {fmt_pct(homepage.get('ctr'))}"
            if homepage and (homepage.get("clicks_change") or 0) > 0
            else ""
        )
        opportunities.append(
            {
                "title": "ขยาย Brand Demand ที่กำลังโต",
                "evidence": (
                    f"`pixiu watch` {fmt_int(brand.get('clicks'))} clicks "
                    f"({signed_clicks(brand)}), CTR {fmt_pct(brand.get('ctr'))}, "
                    f"position {fmt_pos(brand.get('position'))}{homepage_evidence}"
                ),
                "action": (
                    "รักษาอันดับแบรนด์ พร้อมเพิ่ม trust signals และ CTA ซื้อ/ขาย/เทิร์นบน Homepage "
                    "เพื่อเปลี่ยน branded traffic เป็น lead"
                ),
            }
        )

    generic = queries.get("นาฬิกามือสอง")
    authentic = queries.get("นาฬิกามือสองของแท้")
    grand_seiko = queries.get("grand seiko มือสอง")
    growing_commercial = [
        row
        for row in (generic, authentic, grand_seiko)
        if row and (row.get("clicks_change") or 0) > 0
    ]
    if growing_commercial:
        evidence = "; ".join(
            f"`{row.get('key')}` {fmt_int(row.get('clicks'))} clicks ({signed_clicks(row)}), "
            f"position {fmt_pos(row.get('position'))}"
            for row in growing_commercial
        )
        opportunities.append(
            {
                "title": "ดันคีย์เวิร์ดซื้อขายแบบ Non-brand ให้เข้า Top 3",
                "evidence": evidence,
                "action": (
                    "เสริม collection copy, FAQ และ internal links ที่ตรง intent โดยเริ่มจาก "
                    "`นาฬิกามือสอง`, ความแท้ และ Grand Seiko"
                ),
            }
        )

    cleaning = page_containing("how-to-clean-your-watch-by-yourself")
    if cleaning and (cleaning.get("clicks_change") or 0) > 0:
        opportunities.append(
            {
                "title": "เปลี่ยนบทความที่โตให้เป็นเส้นทางสู่ Conversion",
                "evidence": (
                    f"บทความทำความสะอาดนาฬิกา {fmt_int(cleaning.get('clicks'))} clicks "
                    f"({signed_clicks(cleaning)}), CTR {fmt_pct(cleaning.get('ctr'))}"
                ),
                "action": (
                    "เพิ่ม internal links และ CTA ที่เกี่ยวข้องไปยังหน้า Sell/Trade และ collection "
                    "โดยไม่รบกวนเจตนาการค้นหาเชิงข้อมูล"
                ),
            }
        )

    cartier = page_containing("/collections/cartier-")
    if cartier and (cartier.get("clicks_change") or 0) > 0:
        opportunities.append(
            {
                "title": "ผลักหน้า Cartier จากขอบหน้า 1",
                "evidence": (
                    f"Cartier collection {fmt_int(cartier.get('clicks'))} clicks "
                    f"({signed_clicks(cartier)}), CTR {fmt_pct(cartier.get('ctr'))}, "
                    f"position {fmt_pos(cartier.get('position'))}"
                ),
                "action": (
                    "ปรับหัวข้อและคำอธิบาย collection พร้อมเชื่อมบทความ/หมวดที่เกี่ยวข้อง "
                    "เพื่อทดสอบการขยับจาก position ประมาณ 11 เข้าหน้าแรก"
                ),
            }
        )

    if appearance and (appearance.get("clicks_change") or 0) < 0:
        opportunities.append(
            {
                "title": "กู้ Product snippets และ Rich Results",
                "evidence": (
                    f"Product snippets {fmt_int(appearance.get('clicks'))} clicks "
                    f"({signed_clicks(appearance)}), CTR {fmt_pct(appearance.get('ctr'))}, "
                    f"position {fmt_pos(appearance.get('position'))}"
                ),
                "action": (
                    "ตรวจ Product schema, price, availability, canonical และ template indexing; "
                    "ยืนยันผลด้วย Search Console หลังแก้"
                ),
            }
        )

    return opportunities


def seo_good_news_intro(gsc: dict[str, Any]) -> str:
    """Explain why positive findings are scoped when the overall KPI falls."""
    current = gsc.get("clicks")
    previous = gsc.get("clicks_previous")
    change = rel_change(current, previous)
    window = client_window_label(gsc)
    incomplete = gsc.get("window") == "calendar_month_to_date" or "incomplete calendar month" in str(
        gsc.get("window_label") or ""
    ).lower()
    window_text = f"ช่วง {window}" + (" ซึ่งยังไม่ใช่เดือนปฏิทินเต็ม" if incomplete and "ยังไม่ครบเดือน" not in window else "")
    if change is None:
        trend = "จากข้อมูล Organic Search ที่มีอยู่"
    elif change < 0:
        trend = f"แม้ Organic Clicks ลดลง {abs(change):.1f}%"
    elif change > 0:
        trend = f"Organic Clicks เพิ่มขึ้น {change:.1f}% ในภาพรวม"
    else:
        trend = "แม้ Organic Clicks ทรงตัวในภาพรวม"
    return f"{trend} ใน{window_text} แต่ยังพบสัญญาณบวกเฉพาะกลุ่มที่สามารถต่อยอดได้ ดังนี้"


def seo_good_news(gsc: dict[str, Any]) -> list[str]:
    """Build client-ready positive findings without overstating aggregate growth."""
    queries = {str(row.get("key") or "").lower(): row for row in gsc.get("queries") or []}
    pages = gsc.get("pages") or []
    items: list[str] = []

    def page_containing(fragment: str) -> Optional[dict[str, Any]]:
        return next((row for row in pages if fragment in str(row.get("key") or "")), None)

    brand = queries.get("pixiu watch")
    homepage = next(
        (
            row
            for row in pages
            if str(row.get("key") or "").rstrip("/") in {"https://pixiuwatch.com", "Homepage"}
        ),
        None,
    )
    if brand and (brand.get("clicks_change") or 0) > 0:
        brand_item = (
            f"Brand Demand ของคำค้น `pixiu watch` เติบโตขึ้น {fmt_int(brand.get('clicks_change'))} clicks "
            f"เป็น {fmt_int(brand.get('clicks'))} clicks, CTR {fmt_pct(brand.get('ctr'))} "
            f"และอยู่ที่อันดับเฉลี่ย {fmt_pos(brand.get('position'))} สะท้อนว่าเว็บไซต์ยังมีความแข็งแรง "
            "ในคำค้นแบรนด์ และมีโอกาสเปลี่ยนผู้ค้นหาเป็นลูกค้าได้ดี"
        )
        if homepage and (homepage.get("clicks_change") or 0) > 0:
            homepage_ctr = (
                f"และ CTR เพิ่มจาก {fmt_pct(homepage.get('ctr_previous'))} เป็น {fmt_pct(homepage.get('ctr'))}"
                if homepage.get("ctr_previous") is not None
                else f"และ CTR อยู่ที่ {fmt_pct(homepage.get('ctr'))}"
            )
            brand_item += (
                f" Homepage มี Clicks เพิ่มขึ้น {fmt_int(homepage.get('clicks_change'))} ครั้ง "
                f"{homepage_ctr} แสดงว่าหน้าแรกยังดึงดูดผู้ใช้งาน และควรใช้เป็นจุดหลักในการพาผู้ใช้งาน "
                "ไปยังหน้า Buy / Sell / Trade"
            )
        items.append(brand_item)

    commercial = [
        queries.get("นาฬิกามือสอง"),
        queries.get("นาฬิกามือสองของแท้"),
        queries.get("grand seiko มือสอง"),
    ]
    commercial = [row for row in commercial if row and (row.get("clicks_change") or 0) > 0]
    if commercial:
        evidence = ", ".join(
            f"`{row.get('key')}` เพิ่ม {fmt_int(row.get('clicks_change'))} clicks "
            f"เป็น {fmt_int(row.get('clicks'))} clicks (อันดับเฉลี่ย {fmt_pos(row.get('position'))})"
            for row in commercial
        )
        items.append(
            f"กลุ่มคำค้นเชิงพาณิชย์เติบโต ได้แก่ {evidence} สะท้อนโอกาสเข้าถึงผู้ใช้งาน "
            "ที่มีความต้องการซื้อจริง"
        )

    cleaning = page_containing("how-to-clean-your-watch-by-yourself")
    cartier = page_containing("/collections/cartier-")
    page_evidence = []
    if cleaning and (cleaning.get("clicks_change") or 0) > 0:
        page_evidence.append(
            f"บทความ “วิธีทำความสะอาดนาฬิกา” มี Clicks เพิ่มขึ้น {fmt_int(cleaning.get('clicks_change'))} ครั้ง"
        )
    if cartier and (cartier.get("clicks_change") or 0) > 0:
        page_evidence.append(f"หน้า Cartier เพิ่มขึ้น {fmt_int(cartier.get('clicks_change'))} ครั้ง")
    if page_evidence:
        items.append(
            f"{' และ '.join(page_evidence)} แสดงว่า content และ category page บางส่วนยังดึง Organic Traffic "
            "ได้ดี และสามารถเชื่อมต่อไปยังหน้า Sell / Trade หรือ collection เพื่อเพิ่มโอกาสสร้าง Lead"
        )

    mobile = next((row for row in gsc.get("devices") or [] if row.get("key") == "Mobile"), None)
    mobile_share = clicks_share(mobile.get("clicks") if mobile else None, gsc.get("clicks"))
    if mobile and mobile_share is not None:
        mobile_note = ""
        if (mobile.get("clicks_change") or 0) < 0:
            mobile_note = " แม้จำนวน Clicks ไม่ได้เพิ่มขึ้นจากช่วงก่อนหน้า"
        elif (mobile.get("clicks_change") or 0) > 0:
            mobile_note = f" และเติบโตขึ้น {abs(rel_change(mobile.get('clicks'), mobile.get('clicks_previous')) or 0):.1f}%"
        items.append(
            f"Mobile ยังคงเป็นช่องทางหลัก โดยสร้าง {fmt_int(mobile.get('clicks'))} Clicks "
            f"หรือ {fmt_pct(mobile_share, 1)} ของทั้งหมด จึงควรรักษาแนวทาง Mobile-first ต่อไป{mobile_note}"
        )

    return items


def seo_improvement_intro(gsc: dict[str, Any]) -> str:
    """Set expectations that improvement items are the SEO team's plan."""
    window = client_window_label(gsc)
    return (
        f"จากข้อมูล {window} ทีม SEO พบประเด็นที่ควรวางแผนดำเนินการต่อ "
        "รายการด้านล่างเป็นข้อเสนอและแผนงานของทีม ยังไม่ใช่รายการแก้ไขที่นำขึ้นใช้งานแล้ว"
    )


def seo_improvement_plan(
    gsc: dict[str, Any],
    *,
    ahrefs_verified: Optional[dict[str, Any]],
    ahrefs_is_historical: bool,
    ahrefs_date: Optional[str],
    redirects: Optional[int],
    images: Optional[int],
    fourxx: Optional[int],
    noindex: Optional[int],
) -> list[str]:
    """Describe evidence-backed improvement opportunities as the team's plan."""
    items: list[str] = []
    appearance = (gsc.get("search_appearance") or [None])[0]
    if appearance and appearance.get("clicks_previous") is not None:
        clicks_change = appearance.get("clicks_change")
        if clicks_change is not None and clicks_change < 0:
            clicks_phrase = (
                f"ลดลง {fmt_int(abs(clicks_change))} ครั้ง "
                f"(-{abs(rel_change(appearance.get('clicks'), appearance.get('clicks_previous')) or 0):.1f}%)"
            )
        elif clicks_change is not None and clicks_change > 0:
            clicks_phrase = f"เพิ่มขึ้น {fmt_int(clicks_change)} ครั้ง"
        else:
            clicks_phrase = "ทรงตัว"
        ctr_phrase = (
            f"CTR เปลี่ยนจาก {fmt_pct(appearance.get('ctr_previous'))} เป็น {fmt_pct(appearance.get('ctr'))}"
            if appearance.get("ctr_previous") is not None
            else f"CTR อยู่ที่ {fmt_pct(appearance.get('ctr'))}"
        )
        if appearance.get("position_previous") is not None:
            position = appearance.get("position")
            previous_position = appearance.get("position_previous")
            if position is not None and previous_position is not None and position > previous_position:
                position_word = "แย่ลง"
            elif position is not None and previous_position is not None and position < previous_position:
                position_word = "ดีขึ้น"
            else:
                position_word = "ทรงตัว"
            position_phrase = (
                f"และ position {position_word}จาก {fmt_pos(previous_position)} เป็น "
                f"{fmt_pos(position)}"
            )
        else:
            position_phrase = f"และ position อยู่ที่ {fmt_pos(appearance.get('position'))}"
        items.append(
            f"Product snippets มี Clicks {fmt_int(appearance.get('clicks'))} {clicks_phrase}, "
            f"{ctr_phrase} {position_phrase}; ทีม SEO จะตรวจ Product schema, ราคา, availability, "
            "canonical และ template indexing เพื่อเพิ่มโอกาสให้สินค้าแสดงใน Google อย่างต่อเนื่อง"
        )

    decline_queries = top_movers(gsc.get("queries") or [], positive=False, limit=5)
    if decline_queries:
        query_names = ", ".join(f"`{row.get('key')}`" for row in decline_queries)
        items.append(
            f"คีย์เวิร์ดที่ Clicks ลดลงในรอบนี้ ได้แก่ {query_names}; ทีม SEO จะวิเคราะห์ "
            "Title/Meta, search intent, collection copy และ internal links แล้วจัดลำดับหน้าที่ควรปรับ"
        )

    if ahrefs_verified:
        source = (
            f"Ahrefs crawl ล่าสุดที่ยืนยันได้เมื่อ {ahrefs_date or 'รอบก่อนหน้า'}"
            if ahrefs_is_historical
            else "Ahrefs crawl รอบปัจจุบัน"
        )
        items.append(
            f"{source} พบลิงก์ที่ชี้ไปหน้า redirect {fmt_int(redirects)} รายการ, รูปใหญ่ {fmt_int(images)} URL, "
            f"canonical ที่ไม่มี incoming internal link {fmt_int(issue_count(ahrefs_verified, r'canonical'))} รายการ, "
            f"4XX {fmt_int(fourxx)} รายการ และ noindex ใน sitemap {fmt_int(noindex)} รายการ; "
            "ทีม SEO จะจัดลำดับ technical-fix backlog และเตรียมตรวจผลก่อน–หลังเมื่อได้รับสิทธิ์ CMS/Shopify"
        )
    else:
        items.append(
            "Ahrefs รอบนี้ยังไม่มี crawl ที่สำเร็จ จึงยังระบุจำนวน issue ปัจจุบันไม่ได้; "
            "ทีม SEO จะขอ crawl ใหม่ก่อนจัดลำดับ technical-fix backlog"
        )

    items.append(
        "หลังดำเนินการ ทีม SEO จะติดตาม Clicks, CTR, Average position และ Product snippets "
        "ใน Search Console เพื่อยืนยันผลก่อนสรุปว่าแก้ไขสำเร็จ"
    )
    return items


def issue_count(ahrefs: Optional[dict[str, Any]], pattern: str) -> Optional[int]:
    if not ahrefs:
        return None
    regex = re.compile(pattern, re.IGNORECASE)
    for item in ahrefs.get("issues") or []:
        if regex.search(item.get("name") or ""):
            return int(item["count"])
    return None


def verified_ahrefs_snapshot(
    ahrefs: Optional[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], bool]:
    """Return usable audit evidence and whether it predates the report month.

    A failed current-month crawl is status evidence, not a valid health score.
    Its ``last_success`` payload may still be shown as a dated carry-forward
    backlog, provided the report labels it as historical.
    """
    if not ahrefs:
        return None, False
    if ahrefs.get("status") in {"crawl_failed", "crawl_error"}:
        previous = ahrefs.get("last_success")
        if isinstance(previous, dict) and (
            previous.get("issues") or previous.get("health_score") is not None
        ):
            return previous, True
        return None, False
    return ahrefs, False


def ahrefs_snapshot_date(snapshot: Optional[dict[str, Any]]) -> Optional[str]:
    if not snapshot:
        return None
    raw = snapshot.get("received_at") or snapshot.get("crawl_date")
    if not raw:
        return None
    value = str(raw).strip()
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", value)
        return match.group(0) if match else value


def build_pack(
    *,
    client: str,
    month: str,
    gsc: Optional[dict[str, Any]],
    ahrefs: Optional[dict[str, Any]],
    ga4: Optional[dict[str, Any]],
    prepared_on: Optional[str] = None,
    allow_pending_gsc: bool = False,
    last_known_gsc: Optional[dict[str, Any]] = None,
    gsc_api: Optional[dict[str, Any]] = None,
    alerts: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if client not in CLIENTS:
        raise ValueError(f"Unknown SEO client '{client}'. Known: {', '.join(sorted(CLIENTS))}")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month must be YYYY-MM")
    if not gsc and not allow_pending_gsc:
        raise ValueError("Search Console export is required; refusing to invent clicks/impressions")
    cfg = CLIENTS[client]
    pack = {
        "contract": CONTRACT,
        "client": client,
        "client_name": cfg["name"],
        "website": cfg["website"],
        "month": month,
        "prepared_on": prepared_on or today_bangkok().isoformat(),
        "gsc": gsc,
        "gsc_pending": bool(allow_pending_gsc and not gsc),
        "last_known_gsc": last_known_gsc,
        "gsc_api": gsc_api,
        "alerts": alerts or [],
        "ahrefs": ahrefs,
        "ga4": ga4,
        "live_site_access": bool(cfg.get("live_site_access")),
        "fixes_applied": False,
        "sent": False,
    }
    pack["limitations"] = limitations(pack, cfg)
    return pack


def fixture_pack(name: str) -> dict[str, Any]:
    if name not in FIXTURES:
        raise ValueError(f"Unknown fixture '{name}'. Known: {', '.join(sorted(FIXTURES))}")
    spec = FIXTURES[name]
    return build_pack(
        client=spec["client"],
        month=spec["month"],
        gsc=spec["gsc"],
        ahrefs=spec["ahrefs"],
        ga4=spec["ga4"],
        prepared_on=spec["prepared_on"],
    )


def render_markdown(pack: dict[str, Any]) -> str:
    gsc = pack.get("gsc") or {}
    ahrefs = pack.get("ahrefs")
    ahrefs_verified, ahrefs_is_historical = verified_ahrefs_snapshot(ahrefs)
    ahrefs_status = (ahrefs or {}).get("status")
    ahrefs_date = ahrefs_snapshot_date(ahrefs_verified)
    month_en = english_month_label(pack["month"])
    month_th = month_label(pack["month"])
    mobile = next((row for row in gsc.get("devices") or [] if row.get("key") == "Mobile"), None)
    growth_pages = top_movers(gsc.get("pages") or [], positive=True, limit=4)
    good_news_intro = seo_good_news_intro(gsc)
    good_news_items = seo_good_news(gsc)
    opportunities = seo_opportunities(gsc)
    redirects = issue_count(ahrefs_verified, r"redirect")
    images = issue_count(ahrefs_verified, r"image file size|oversized")
    fourxx = issue_count(ahrefs_verified, r"4xx|404|broken page")
    noindex = issue_count(ahrefs_verified, r"noindex")
    improvement_intro = seo_improvement_intro(gsc)
    improvement_items = seo_improvement_plan(
        gsc,
        ahrefs_verified=ahrefs_verified,
        ahrefs_is_historical=ahrefs_is_historical,
        ahrefs_date=ahrefs_date,
        redirects=redirects,
        images=images,
        fourxx=fourxx,
        noindex=noindex,
    )

    def query_line(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "—"
        return ", ".join(f"`{row['key']}`" for row in rows[:5])

    device_rows = "\n".join(
        f"| {row['key']} | {fmt_int(row.get('clicks'))} | {fmt_int(row.get('impressions'))} | "
        f"{fmt_pct(row.get('ctr'))} | {fmt_pos(row.get('position'))} |"
        for row in gsc.get("devices") or []
    ) or "| — | — | — | — | — |"
    page_rows = "\n".join(
        f"| {row['key']} | {fmt_int(row.get('clicks'))} | {fmt_pct(row.get('ctr'))} | {fmt_pos(row.get('position'))} |"
        for row in (gsc.get("pages") or [])[:8]
    ) or "| — | — | — | — |"
    query_rows = "\n".join(
        f"| `{row['key']}` | {fmt_int(row.get('clicks'))} | "
        f"{fmt_int(row.get('clicks_change')) if row.get('clicks_change') is not None else '—'} | "
        f"{fmt_pct(row.get('ctr'))} | {fmt_pos(row.get('position'))} |"
        for row in (gsc.get("queries") or [])[:10]
    ) or "| — | — | — | — | — |"
    issue_rows = "\n".join(
        f"| {item['name']} | {fmt_int(item.get('count'))} | {item.get('priority', '')} |"
        for item in (ahrefs_verified or {}).get("issues") or []
    ) or "| — | — | — |"
    appearance = (gsc.get("search_appearance") or [None])[0]
    ga4_line = (
        f"{fmt_int(pack['ga4'].get('users'))} users / {fmt_int(pack['ga4'].get('sessions'))} sessions"
        if pack.get("ga4")
        else "**Pending GA4 export**"
    )
    limitations_md = "\n".join(f"- {note}" for note in pack["limitations"])
    mobile_share = clicks_share(mobile.get("clicks") if mobile else None, gsc.get("clicks"))
    if ahrefs_status in {"crawl_failed", "crawl_error"}:
        ahrefs_health = "Pending current crawl"
        ahrefs_comparison = (
            f"Latest verified: {fmt_int((ahrefs_verified or {}).get('health_score'))} "
            f"on {ahrefs_date or 'a previous crawl'}"
            if ahrefs_verified
            else f"Current status: {ahrefs_status}"
        )
    elif ahrefs:
        ahrefs_health = fmt_int(ahrefs.get("health_score"))
        ahrefs_comparison = f"Errors {fmt_int(ahrefs.get('errors'))}"
    else:
        ahrefs_health = "Pending"
        ahrefs_comparison = "Ahrefs export not attached"

    ahrefs_section_title = "Ahrefs Site Audit"
    if ahrefs_is_historical:
        ahrefs_section_title += f" — last verified crawl ({ahrefs_date or 'previous period'})"
    ahrefs_source = (
        f"current status {ahrefs_status}; last verified {ahrefs_date or 'previous period'}"
        if ahrefs_is_historical
        else (ahrefs or {}).get("source_file") or (ahrefs or {}).get("crawl_date") or "not attached"
    )
    opportunities_md = "\n".join(
        (
            f"{index}. **{item['title']}**\n"
            f"   - Evidence: {item['evidence']}\n"
            f"   - Next action: {item['action']}"
        )
        for index, item in enumerate(opportunities, start=1)
    ) or "No evidence-backed opportunity identified in the attached period."
    good_news_md = "\n".join(f"- {item}" for item in good_news_items) or "- ยังไม่มีสัญญาณบวกที่ยืนยันได้จากข้อมูลช่วงนี้"
    improvement_md = "\n".join(f"- {item}" for item in improvement_items)

    return f"""# {pack['client_name']} — SEO Monthly Report

## {month_en}

**Website:** {pack['website']}
**Report status:** Generated data pack (not sent)
**Prepared:** {pack['prepared_on']} (Asia/Bangkok)

> This report contains only verified figures from attached exports. Missing values are labelled pending. Do not present missing values as zero.

## Executive summary

{"Search Console calendar-month Gmail notice is still pending. Do not treat clicks as zero." if pack.get("gsc_pending") or not gsc.get("clicks") else f"Verified Search Console window **{gsc.get('window_label') or 'Last 28 days'}**: **{gsc.get('clicks_compact') or fmt_int(gsc.get('clicks'))} clicks** ({fmt_delta_pct(gsc.get('clicks'), gsc.get('clicks_previous'))}), **{gsc.get('impressions_compact') or fmt_int(gsc.get('impressions'))} impressions** ({fmt_delta_pct(gsc.get('impressions'), gsc.get('impressions_previous'))}), CTR **{fmt_pct(gsc.get('ctr'))}**, average position **{fmt_pos(gsc.get('position'))}**."} Ahrefs: **{ahrefs.get('status') if ahrefs and ahrefs.get('status') not in {None, 'ok'} else ahrefs_health}**.

## Client-ready summary — {month_th}

สรุปอัปเดตผลลัพธ์ SEO ประจำเดือน{month_th}

### ภาพรวม

- Clicks **{gsc.get('clicks_compact') or fmt_int(gsc.get('clicks'))}** ({fmt_delta_pct(gsc.get('clicks'), gsc.get('clicks_previous'))}) และ Impressions **{gsc.get('impressions_compact') or fmt_int(gsc.get('impressions'))}** ({fmt_delta_pct(gsc.get('impressions'), gsc.get('impressions_previous'))}) ในหน้าต่าง {gsc.get('window_label') or ('pending Gmail calendar notice' if pack.get('gsc_pending') else 'Last 28 days')} CTR {fmt_pct(gsc.get('ctr'))} (ก่อนหน้า {fmt_pct(gsc.get('ctr_previous'))})
- Average position {fmt_pos(gsc.get('position'))} (ก่อนหน้า {fmt_pos(gsc.get('position_previous'))}; ตัวเลขต่ำกว่าดีกว่า)
- มือถือสร้างคลิก {fmt_int(mobile.get('clicks') if mobile else None)} หรือ {fmt_pct(mobile_share, 1)} ของทั้งหมด

### สิ่งที่ทำได้ดี

{good_news_intro}

{good_news_md}

### สิ่งที่ปรับปรุงได้

{improvement_intro}

{improvement_md}

## SEO Opportunities

{opportunities_md}

## KPI scorecard

| Source | {month_en} | Comparison / status |
|---|---:|---|
| GA4 users / sessions | {ga4_line} | Insert only from a real GA4 export |
| Search Console clicks | **{gsc.get('clicks_compact') or fmt_int(gsc.get('clicks'))}** | {gsc.get('window_label') or ('pending Gmail calendar notice' if pack.get('gsc_pending') else '—')}; {fmt_delta_pct(gsc.get('clicks'), gsc.get('clicks_previous'))} |
| Search Console impressions | **{fmt_int(gsc.get('impressions'))}** | {fmt_delta_pct(gsc.get('impressions'), gsc.get('impressions_previous'))} |
| Search Console CTR | **{fmt_pct(gsc.get('ctr'))}** | Previous: {fmt_pct(gsc.get('ctr_previous'))} ({fmt_delta_pp(gsc.get('ctr'), gsc.get('ctr_previous'))}) |
| Search Console average position | **{fmt_pos(gsc.get('position'))}** | Previous: {fmt_pos(gsc.get('position_previous'))} |
| Ahrefs Site Audit Health Score | **{ahrefs_health}** | {ahrefs_comparison} |

## Google Search Console

| Metric | Current | Previous | Change |
|---|---:|---:|---:|
| Clicks | {fmt_int(gsc.get('clicks'))} | {fmt_int(gsc.get('clicks_previous'))} | {fmt_delta_pct(gsc.get('clicks'), gsc.get('clicks_previous'))} |
| Impressions | {fmt_int(gsc.get('impressions'))} | {fmt_int(gsc.get('impressions_previous'))} | {fmt_delta_pct(gsc.get('impressions'), gsc.get('impressions_previous'))} |
| CTR | {fmt_pct(gsc.get('ctr'))} | {fmt_pct(gsc.get('ctr_previous'))} | {fmt_delta_pp(gsc.get('ctr'), gsc.get('ctr_previous'))} |
| Average position | {fmt_pos(gsc.get('position'))} | {fmt_pos(gsc.get('position_previous'))} | Calculated from device rows when present |

### Device performance

| Device | Clicks | Impressions | CTR | Position |
|---|---:|---:|---:|---:|
{device_rows}

### Top pages by clicks

| Page | Clicks | CTR | Position |
|---|---:|---:|---:|
{page_rows}

Strongest page click movers: {query_line(growth_pages) if growth_pages else '—'}.

### Top queries

| Query | Clicks | Change | CTR | Position |
|---|---:|---:|---:|---:|
{query_rows}

### Search appearance

{f"Product snippets: {fmt_int(appearance.get('clicks'))} clicks, {fmt_int(appearance.get('impressions'))} impressions, CTR {fmt_pct(appearance.get('ctr'))}." if appearance else "No search-appearance rows in the export."}

## {ahrefs_section_title}

| Issue | Count | Priority |
|---|---:|---|
{issue_rows}

## Data limitations

{limitations_md}

## Canva-ready conclusion

> {month_en} organic search: clicks {fmt_int(gsc.get('clicks'))} ({fmt_delta_pct(gsc.get('clicks'), gsc.get('clicks_previous'))}), impressions {fmt_int(gsc.get('impressions'))} ({fmt_delta_pct(gsc.get('impressions'), gsc.get('impressions_previous'))}), CTR {fmt_pct(gsc.get('ctr'))}. Next technical work remains blocked on CMS access.

## Source notes

- Search Console: `{gsc.get('source_file') or 'attached export'}`
- Ahrefs: `{ahrefs_source}`
- GA4: `{(pack.get('ga4') or {}).get('source_file') or 'not attached'}`
- Live-site fixes applied: no
- Client send: no
"""


def render_html(pack: dict[str, Any]) -> str:
    gsc = pack.get("gsc") or {}
    ahrefs = pack.get("ahrefs")
    ahrefs_verified, ahrefs_is_historical = verified_ahrefs_snapshot(ahrefs)
    ahrefs_status = (ahrefs or {}).get("status")
    ahrefs_date = ahrefs_snapshot_date(ahrefs_verified)
    gsc_window = client_window_label(gsc)
    month_th = month_label(pack["month"])
    mobile = next((row for row in gsc.get("devices") or [] if row.get("key") == "Mobile"), None)
    decline_queries = top_movers(gsc.get("queries") or [], positive=False)
    growth_pages = top_movers(gsc.get("pages") or [], positive=True, limit=3)
    good_news_intro = seo_good_news_intro(gsc)
    good_news_items = seo_good_news(gsc)
    opportunities = seo_opportunities(gsc)
    appearance = (gsc.get("search_appearance") or [None])[0]
    redirects = issue_count(ahrefs_verified, r"redirect")
    images = issue_count(ahrefs_verified, r"image file size|oversized")
    fourxx = issue_count(ahrefs_verified, r"4xx|404|broken page")
    noindex = issue_count(ahrefs_verified, r"noindex")
    mobile_share = clicks_share(mobile.get("clicks") if mobile else None, gsc.get("clicks"))
    improvement_intro = seo_improvement_intro(gsc)
    improvement_items = seo_improvement_plan(
        gsc,
        ahrefs_verified=ahrefs_verified,
        ahrefs_is_historical=ahrefs_is_historical,
        ahrefs_date=ahrefs_date,
        redirects=redirects,
        images=images,
        fourxx=fourxx,
        noindex=noindex,
    )

    def codes(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "—"
        return ", ".join(f"<code>{html.escape(str(row['key']))}</code>" for row in rows[:5])

    def page_link(value: Any) -> str:
        raw = str(value or "")
        parsed = urlsplit(raw)
        label = unquote(parsed.path or "/") if parsed.scheme and parsed.netloc else unquote(raw)
        if len(label) > 72:
            label = label[:69].rstrip() + "…"
        escaped_label = html.escape(label)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f'<a href="{html.escape(raw, quote=True)}">{escaped_label}</a>'
        return escaped_label

    def page_links(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "—"
        return ", ".join(page_link(row.get("key")) for row in rows[:3])

    def inline_client_text(value: str) -> str:
        escaped = html.escape(value)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

    def table(headers: list[str], rows: list[list[str]], numeric: set[int]) -> str:
        head = "".join(
            f'<th class="num">{html.escape(title)}</th>' if i in numeric else f"<th>{html.escape(title)}</th>"
            for i, title in enumerate(headers)
        )
        body = []
        for row in rows:
            cells = []
            for i, value in enumerate(row):
                cls = ' class="num"' if i in numeric else ""
                cells.append(f"<td{cls}>{value}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        inner = "\n      ".join(body) if body else "<tr><td colspan='8'>—</td></tr>"
        return f"<table>\n    <thead><tr>{head}</tr></thead>\n    <tbody>\n      {inner}\n    </tbody>\n  </table>"

    device_table = table(
        ["อุปกรณ์", "Clicks", "Impressions", "CTR", "Position"],
        [
            [
                html.escape(str(row.get("key"))),
                fmt_int(row.get("clicks")),
                fmt_int(row.get("impressions")),
                fmt_pct(row.get("ctr")),
                fmt_pos(row.get("position")),
            ]
            for row in gsc.get("devices") or []
        ],
        {1, 2, 3, 4},
    )
    page_table = table(
        ["หน้า", "Clicks", "CTR", "Position"],
        [
            [
                page_link(row.get("key")),
                fmt_int(row.get("clicks")),
                fmt_pct(row.get("ctr")),
                fmt_pos(row.get("position")),
            ]
            for row in (gsc.get("pages") or [])[:8]
        ],
        {1, 2, 3},
    )
    issue_table = table(
        ["งาน", "จำนวน", "ความสำคัญ"],
        [
            [
                html.escape(str(item.get("name"))),
                fmt_int(item.get("count")),
                html.escape(
                    {"high": "สูง", "medium": "กลาง", "low": "ต่ำ"}.get(
                        str(item.get("priority") or "").lower(), str(item.get("priority") or "")
                    )
                ),
            ]
            for item in (ahrefs_verified or {}).get("issues") or []
        ],
        {1},
    )
    ga4_note = (
        "มีตัวเลข GA4 จากไฟล์ที่แนบ"
        if pack.get("ga4")
        else f"ยังไม่มีตัวเลข GA4 เดือน{month_th} จึงไม่ใส่เป็นศูนย์"
    )
    if ahrefs_status in {"crawl_failed", "crawl_error"}:
        ahrefs_kpi_value = "Crawl ไม่สำเร็จ" if ahrefs_status == "crawl_failed" else "Crawl มีข้อผิดพลาด"
        ahrefs_kpi_note = (
            f"ข้อมูลยืนยันล่าสุด {ahrefs_date or 'รอบก่อนหน้า'}: "
            f"Health {fmt_int((ahrefs_verified or {}).get('health_score'))} / 100"
            if ahrefs_verified
            else "ยังไม่มี crawl ที่สำเร็จในรอบนี้"
        )
    elif ahrefs:
        ahrefs_kpi_value = f"{fmt_int(ahrefs.get('health_score'))} / 100"
        ahrefs_kpi_note = f"Errors {fmt_int(ahrefs.get('errors'))}"
    else:
        ahrefs_kpi_value = "Pending"
        ahrefs_kpi_note = "ยังไม่ได้แนบ Ahrefs export"

    if ahrefs_verified:
        technical_source = (
            f"จากข้อมูลยืนยันล่าสุด {ahrefs_date or 'รอบก่อนหน้า'}"
            if ahrefs_is_historical
            else "จาก crawl รอบปัจจุบัน"
        )
        queue_heading = f"คิวเทคนิค (Ahrefs — {technical_source})"
        queue_note = (
            "<p class=\"source-note\">รายการนี้เป็น backlog ต่อเนื่องจาก crawl ก่อนหน้า "
            "ไม่ใช่ Health Score ของเดือนสิงหาคม</p>"
            if ahrefs_is_historical
            else ""
        )
    else:
        queue_heading = "คิวเทคนิค (Ahrefs — รอ crawl ที่สำเร็จ)"
        queue_note = ""

    client_limitations = []
    if not pack.get("ga4"):
        client_limitations.append("ยังไม่มี GA4 export รายเดือน จึงไม่แสดงค่า traffic เป็นศูนย์")
    if pack.get("gsc_pending") or not gsc:
        client_limitations.append("ข้อมูล Search Console เดือนนี้ยังรอหลักฐาน ไม่ได้ตีความว่าเป็นศูนย์")
    elif gsc.get("window") == "calendar_month_to_date" or "incomplete calendar month" in str(
        gsc.get("window_label") or ""
    ).lower():
        client_limitations.append(
            f"ข้อมูล Search Console ครอบคลุมเฉพาะ {gsc_window}"
        )
    elif gsc.get("window") == "last_28_days":
        client_limitations.append("ข้อมูล Search Console เป็นช่วง 28 วันล่าสุด ไม่ใช่เดือนปฏิทินเต็ม")
    if ahrefs_status in {"crawl_failed", "crawl_error"}:
        client_limitations.append(
            f"Ahrefs เดือนนี้ crawl ไม่สำเร็จ; backlog ที่แสดงอ้างอิงข้อมูลล่าสุด {ahrefs_date or 'รอบก่อนหน้า'} เท่านั้น"
        )
    elif not ahrefs:
        client_limitations.append("ยังไม่ได้แนบ Ahrefs Site Audit export")
    if not pack.get("live_site_access"):
        client_limitations.append("ยังไม่มีสิทธิ์ CMS/Shopify ใน workspace นี้ จึงยังไม่ได้แก้หน้าเว็บจริง")
    client_limitations.append("รายงานนี้สรุปผลและ backlog เท่านั้น ไม่ได้หมายความว่างานแก้ไขบน CMS ถูกนำขึ้นใช้งานแล้ว")
    limitations_html = "".join(f"<li>{html.escape(note)}</li>" for note in client_limitations)

    if ahrefs_is_historical:
        ahrefs_source = f"crawl เดือนนี้ไม่สำเร็จ; ข้อมูลยืนยันล่าสุด {ahrefs_date or 'รอบก่อนหน้า'}"
    else:
        ahrefs_source = (ahrefs or {}).get("source_file") or (ahrefs or {}).get("crawl_date") or "not attached"
    opportunities_html = "".join(
        (
            f"<li><strong>{html.escape(item['title'])}</strong>"
            f"<span class=\"evidence\">หลักฐาน: {inline_client_text(item['evidence'])}</span>"
            f"<span>สิ่งที่ทำต่อ: {inline_client_text(item['action'])}</span></li>"
        )
        for item in opportunities
    ) or "<li>ยังไม่มีโอกาสที่ยืนยันได้จากข้อมูลช่วงนี้</li>"
    good_news_html = "".join(
        f"<li>{inline_client_text(item)}</li>" for item in good_news_items
    ) or "<li>ยังไม่มีสัญญาณบวกที่ยืนยันได้จากข้อมูลช่วงนี้</li>"
    improvement_html = "".join(
        f"<li>{inline_client_text(item)}</li>" for item in improvement_items
    )

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(pack['client_name'])} — รายงาน SEO {month_th}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0 auto;
      max-width: 820px;
      padding: 32px 24px 64px;
      font-family: "IBM Plex Sans Thai", "Noto Sans Thai", "Sarabun", sans-serif;
      color: #1a1a1a;
      line-height: 1.55;
      background: #fff;
    }}
    h1 {{ font-size: 1.6rem; margin: 0 0 4px; }}
    h2 {{ font-size: 1.15rem; margin: 28px 0 8px; }}
    h3 {{ font-size: 1rem; margin: 18px 0 6px; }}
    p, li {{ font-size: 0.95rem; }}
    .meta {{ color: #555; font-size: 0.9rem; margin-bottom: 20px; }}
    .note {{
      background: #fff7e6;
      border: 1px solid #f0d9a6;
      padding: 10px 12px;
      font-size: 0.9rem;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; margin: 8px 0 16px; }}
    th, td {{ border-bottom: 1px solid #ddd; text-align: left; padding: 6px 8px; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #f4f1ea; }}
    td.num, th.num {{ text-align: right; }}
    .kpis {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin: 12px 0 20px; }}
    .kpi {{ border: 1px solid #e6e0d4; padding: 10px 12px; }}
    .kpi b {{ display: block; font-size: 1.2rem; }}
    .kpi span {{ color: #555; font-size: 0.82rem; }}
    .opportunities {{ padding-left: 1.25rem; }}
    .opportunities li {{ margin: 0 0 12px; padding-left: 4px; }}
    .opportunities span {{ display: block; margin-top: 3px; }}
    .opportunities .evidence {{ color: #555; font-size: 0.9rem; }}
    .source-note {{ color: #666; font-size: 0.85rem; margin-top: -2px; }}
    footer {{ margin-top: 28px; font-size: 0.8rem; color: #666; }}
    @media print {{
      body {{ padding: 12mm; max-width: none; }}
      .note {{ break-inside: avoid; }}
      table, .kpi {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <h1>{html.escape(pack['client_name'])} — รายงาน SEO ประจำเดือน{html.escape(month_th)}</h1>
  <p class="meta">เว็บไซต์: {html.escape(pack['website'].replace('https://', '').rstrip('/'))} · จัดทำ {html.escape(pack['prepared_on'])} · อ้างอิงข้อมูลที่ตรวจสอบได้</p>

  <p class="note"><strong>วิธีอ่านตัวเลข:</strong> ตัวเลข Search Console คือช่วง <strong>{html.escape(gsc_window)}</strong> {ga4_note}</p>

  <div class="kpis">
    <div class="kpi"><span>Clicks</span><b>{fmt_int(gsc.get('clicks'))}</b><span>{fmt_delta_pct(gsc.get('clicks'), gsc.get('clicks_previous'))} จากช่วงก่อนหน้า</span></div>
    <div class="kpi"><span>Impressions</span><b>{fmt_int(gsc.get('impressions'))}</b><span>{fmt_delta_pct(gsc.get('impressions'), gsc.get('impressions_previous'))}</span></div>
    <div class="kpi"><span>CTR</span><b>{fmt_pct(gsc.get('ctr'))}</b><span>ก่อนหน้า {fmt_pct(gsc.get('ctr_previous'))}</span></div>
    <div class="kpi"><span>สถานะ Ahrefs</span><b>{html.escape(ahrefs_kpi_value)}</b><span>{html.escape(ahrefs_kpi_note)}</span></div>
  </div>

  <h2>สรุปสำหรับลูกค้า</h2>
  <p>สัญญาณค้นหาออร์แกนิกในช่วงที่วัดได้คำนวณจากหลักฐาน Search Console ที่บันทึกไว้ และยังไม่มีหลักฐานว่าดำเนินการแก้ไขบน CMS แล้ว</p>

  <h3>ภาพรวม</h3>
  <ul>
    <li>Clicks {fmt_int(gsc.get('clicks'))} ({fmt_delta_pct(gsc.get('clicks'), gsc.get('clicks_previous'))}) และ Impressions {fmt_int(gsc.get('impressions'))} ({fmt_delta_pct(gsc.get('impressions'), gsc.get('impressions_previous'))})</li>
    <li>Average position {fmt_pos(gsc.get('position'))} (ก่อนหน้า {fmt_pos(gsc.get('position_previous'))}; ตัวเลขต่ำกว่าดีกว่า)</li>
    <li>มือถือสร้างคลิก {fmt_int(mobile.get('clicks') if mobile else None)} หรือ {fmt_pct(mobile_share, 1)} ของทั้งหมด</li>
  </ul>

  <h3>สิ่งที่ทำได้ดี</h3>
  <p>{inline_client_text(good_news_intro)}</p>
  <ul>
    {good_news_html}
  </ul>

  <h3>สิ่งที่ปรับปรุงได้</h3>
  <p>{inline_client_text(improvement_intro)}</p>
  <ul>
    {improvement_html}
  </ul>

  <h2>SEO Opportunities — โอกาสที่ทำต่อได้</h2>
  <ol class="opportunities">
    {opportunities_html}
  </ol>

  <h2>อุปกรณ์</h2>
  {device_table}

  <h2>หน้าที่มีคลิกสูง</h2>
  {page_table}

  <h2>{html.escape(queue_heading)}</h2>
  {queue_note}
  {issue_table}

  <h2>ข้อจำกัดของข้อมูล</h2>
  <ul>
    {limitations_html}
  </ul>

  <footer>
    แหล่งข้อมูล: Google Search Console ({html.escape(gsc_window)})
    {' และ Ahrefs Site Audit (' + html.escape(str(ahrefs_source)) + ')' if ahrefs else ''}
    · งานแก้ไขบน CMS ต้องยืนยันด้วยหลักฐานก่อนและหลังดำเนินการ
  </footer>
</body>
</html>
"""


def pack_paths(delivery_dir: Path, month: str) -> dict[str, Path]:
    evidence = delivery_dir / "evidence" / month
    return {
        "delivery_dir": delivery_dir,
        "evidence_dir": evidence,
        "pack_json": evidence / "pack.json",
        "draft_md": delivery_dir / f"{month}-seo-report-draft.md",
        "client_html": delivery_dir / f"{month}-seo-report-client.html",
    }


def existing_months(delivery_dir: Path) -> list[str]:
    months = set()
    evidence = delivery_dir / "evidence"
    if evidence.exists():
        months.update(path.name for path in evidence.iterdir() if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}", path.name))
    for path in delivery_dir.glob("*-seo-report-*.md"):
        match = re.match(r"(\d{4}-\d{2})-seo-report", path.name)
        if match:
            months.add(match.group(1))
    for path in delivery_dir.glob("*-seo-report-*.html"):
        match = re.match(r"(\d{4}-\d{2})-seo-report", path.name)
        if match:
            months.add(match.group(1))
    return sorted(months)


def status_payload(client: str, dest_root: Optional[Path] = None) -> dict[str, Any]:
    if client not in CLIENTS:
        raise ValueError(f"Unknown SEO client '{client}'. Known: {', '.join(sorted(CLIENTS))}")
    cfg = CLIENTS[client]
    delivery_dir = dest_root or cfg["delivery_dir"]
    months = []
    for month in existing_months(delivery_dir):
        paths = pack_paths(delivery_dir, month)
        pack_data = None
        if paths["pack_json"].exists():
            pack_data = json.loads(paths["pack_json"].read_text(encoding="utf-8"))
        months.append(
            {
                "month": month,
                "pack_json": paths["pack_json"].exists(),
                "draft_md": paths["draft_md"].exists(),
                "client_html": paths["client_html"].exists(),
                "ga4": bool((pack_data or {}).get("ga4")),
                "ahrefs": bool((pack_data or {}).get("ahrefs")),
            }
        )
    return {
        "contract": CONTRACT,
        "command": "status",
        "client": client,
        "client_name": cfg["name"],
        "website": cfg["website"],
        "delivery_dir": source_label(delivery_dir),
        "live_site_access": bool(cfg.get("live_site_access")),
        "blocked_reason": cfg.get("blocked_reason"),
        "months": months,
        "next_safe_action": (
            "Drop a Search Console zip and optional Ahrefs JSON, then run "
            "report --month YYYY-MM --gsc FILE --ahrefs FILE --apply"
        ),
        "does_not": [
            "call Search Console / Ahrefs / GA4 APIs",
            "crawl or patch pixiuwatch.com",
            "send Fastwork or email",
            "invent missing GA4 numbers",
        ],
    }


def write_pack(pack: dict[str, Any], paths: dict[str, Path]) -> list[str]:
    paths["evidence_dir"].mkdir(parents=True, exist_ok=True)
    written = []
    payloads = {
        paths["pack_json"]: json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        paths["draft_md"]: render_markdown(pack),
        paths["client_html"]: render_html(pack),
    }
    for path, content in payloads.items():
        path.write_text(content, encoding="utf-8")
        written.append(source_label(path) or str(path))
    return written


def run(
    *,
    command: str = "status",
    client: str = DEFAULT_CLIENT,
    month: Optional[str] = None,
    gsc_path: Optional[Path] = None,
    ahrefs_path: Optional[Path] = None,
    ga4_path: Optional[Path] = None,
    fixture: Optional[str] = None,
    source: str = "files",
    apply: bool = False,
    dest_root: Optional[Path] = None,
) -> dict[str, Any]:
    if command in {"status", None} and not fixture and not month and gsc_path is None and source == "files":
        payload = status_payload(client, dest_root)
        payload["apply"] = False
        return payload

    if fixture:
        pack = fixture_pack(fixture)
        client = pack["client"]
        month = pack["month"]
    elif source == "gmail" or command == "fetch":
        from seo_live_fetch import fetch_live

        if not month:
            raise ValueError("gmail fetch requires --month YYYY-MM")
        live = fetch_live(client=client, month=month)
        pack = build_pack(
            client=client,
            month=month,
            gsc=live.get("gsc"),
            ahrefs=live.get("ahrefs"),
            ga4=None,
            allow_pending_gsc=True,
            last_known_gsc=live.get("last_known_gsc"),
            gsc_api=live.get("gsc_api"),
            alerts=live.get("alerts"),
        )
        pack["live_fetch"] = {
            "gsc_api": live.get("gsc_api"),
            "ahrefs_api": live.get("ahrefs_api"),
            "gsc_pending": live.get("gsc_pending"),
            "ahrefs_events": live.get("ahrefs_events"),
            "does_not": live.get("does_not"),
        }
    else:
        if not month:
            raise ValueError("report requires --month YYYY-MM or --fixture")
        if gsc_path is None:
            raise ValueError("report requires --gsc zip, --source gmail, or --fixture")
        pack = build_pack(
            client=client,
            month=month,
            gsc=parse_gsc_zip(gsc_path),
            ahrefs=parse_ahrefs(ahrefs_path) if ahrefs_path else None,
            ga4=parse_ga4(ga4_path),
        )

    cfg = CLIENTS[client]
    delivery_dir = dest_root or cfg["delivery_dir"]
    paths = pack_paths(delivery_dir, month or pack["month"])
    written: list[str] = []
    if apply:
        written = write_pack(pack, paths)

    return {
        "contract": CONTRACT,
        "command": "fetch" if command == "fetch" or source == "gmail" else "report",
        "apply": apply,
        "client": pack["client"],
        "month": pack["month"],
        "prepared_on": pack["prepared_on"],
        "kpis": {
            "clicks": (pack.get("gsc") or {}).get("clicks"),
            "impressions": (pack.get("gsc") or {}).get("impressions"),
            "ctr": (pack.get("gsc") or {}).get("ctr"),
            "position": (pack.get("gsc") or {}).get("position"),
            "clicks_compact": (pack.get("gsc") or {}).get("clicks_compact"),
            "ahrefs_health": (pack.get("ahrefs") or {}).get("health_score"),
            "ahrefs_status": (pack.get("ahrefs") or {}).get("status"),
            "ga4": pack.get("ga4") is not None,
            "gsc_pending": pack.get("gsc_pending"),
        },
        "gsc_api": pack.get("gsc_api"),
        "ahrefs_api": (pack.get("live_fetch") or {}).get("ahrefs_api"),
        "alerts": pack.get("alerts") or [],
        "live_fetch": pack.get("live_fetch"),
        "limitations": pack["limitations"],
        "files": {
            "pack_json": source_label(paths["pack_json"]),
            "draft_md": source_label(paths["draft_md"]),
            "client_html": source_label(paths["client_html"]),
        },
        "written": written,
        "fixes_applied": False,
        "sent": False,
        "live_site_access": pack["live_site_access"],
    }

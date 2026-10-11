#!/usr/bin/env python3
"""Read-only WhatsApp Business Cloud API metadata for Solo Empire.

The adapter uses Meta Graph API GET requests to list phone-number metadata for
one configured WhatsApp Business Account. It never reads messages, sends
messages, manages templates, registers numbers, or performs any mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_env  # noqa: E402


MAX_LIMIT = 100
DEFAULT_GRAPH_API_VERSION = "v25.0"
GRAPH_API_BASE = "https://graph.facebook.com"
REDACTION_PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)[^\s,;]+"), r"\1[REDACTED]"),
)


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    return type(exc).__name__


def redact_text(value: object, max_length: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:max_length] if text else None


def graph_api_version() -> str:
    value = os.environ.get("WHATSAPP_GRAPH_API_VERSION", DEFAULT_GRAPH_API_VERSION).strip()
    if not re.fullmatch(r"v\d+\.\d+", value):
        raise ValueError("invalid_graph_api_version")
    return value


def mask_phone_number(value: object) -> str | None:
    phone = redact_text(value, 80)
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "••••"
    suffix = digits[-4:]
    prefix = "+" if phone.startswith("+") else ""
    return f"{prefix}•••{suffix}"


def call_graph(path: str, token: str, params: dict[str, str]) -> object:
    query = urllib.parse.urlencode(params)
    url = f"{GRAPH_API_BASE}/{graph_api_version()}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "solo-empire-whatsapp-business-readonly/1.0",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_phone_number(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown"}
    item: dict[str, object] = {}
    phone_id = redact_text(value.get("id"), 120)
    verified_name = redact_text(value.get("verified_name"), 240)
    quality_rating = redact_text(value.get("quality_rating"), 40)
    masked_phone = mask_phone_number(value.get("display_phone_number"))
    if phone_id:
        item["id"] = phone_id
    if verified_name:
        item["verified_name"] = verified_name
    if masked_phone:
        item["display_phone_number_masked"] = masked_phone
    if quality_rating:
        item["quality_rating"] = quality_rating
    item.setdefault("id", "unknown")
    return item


def report(status: str, resource: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "whatsapp-business",
        "status": status,
        "resource": resource,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    return payload


def run(resource: str, limit: int, waba_id: str, offline: bool) -> dict[str, object]:
    if offline:
        return report("offline", resource, phone_numbers=[], offline=True)

    try:
        token = get_env("WHATSAPP_CLOUD_API_TOKEN")
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))
    missing: list[str] = []
    if not token:
        missing.append("WHATSAPP_CLOUD_API_TOKEN")
    if not waba_id:
        missing.append("WHATSAPP_BUSINESS_ACCOUNT_ID")
    if missing:
        return report("not_configured", resource, missing=missing)

    try:
        payload = call_graph(
            f"{waba_id}/phone_numbers",
            token,
            {
                "fields": "id,verified_name,display_phone_number,quality_rating",
                "limit": str(limit),
            },
        )
        if not isinstance(payload, dict):
            raise ValueError("Meta Graph response must be an object")
        if isinstance(payload.get("error"), dict):
            return report("error", resource, error_class="meta_graph_error")
        values = payload.get("data")
        if not isinstance(values, list):
            raise ValueError("Meta Graph response must contain data")
        phones = [normalize_phone_number(item) for item in values[:limit]]
        paging = payload.get("paging")
        return report(
            "healthy",
            resource,
            phone_numbers=phones,
            waba_id=redact_text(waba_id, 120),
            graph_api_version=graph_api_version(),
            limit=limit,
            returned=len(phones),
            has_more=isinstance(paging, dict) and bool(paging.get("next")),
        )
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only WhatsApp Business phone metadata")
    parser.add_argument("--resource", choices=("phone-numbers",), default="phone-numbers")
    parser.add_argument("--waba-id", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")
    waba_id = args.waba_id.strip()
    if len(waba_id) > 120 or (waba_id and not re.fullmatch(r"[A-Za-z0-9_-]+", waba_id)):
        parser.error("--waba-id must contain only letters, numbers, underscores, or hyphens")

    print(json.dumps(run(args.resource, args.limit, waba_id, args.offline), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

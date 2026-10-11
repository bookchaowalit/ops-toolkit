#!/usr/bin/env python3
"""Read-only Sentry issue summary for the Solo Empire API.

This script intentionally exposes only bounded issue metadata. It never reads
events, stack traces, attachments, source maps, or changes Sentry state.

Usage:
    python3 sentry_readonly.py --json
    python3 sentry_readonly.py --limit 10 --json
    python3 sentry_readonly.py --offline --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_env  # noqa: E402


DEFAULT_BASE_URL = "https://sentry.io"
MAX_LIMIT = 100
REDACTION_PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)[^\s,;]+"), r"\1[REDACTED]"),
)


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    return type(exc).__name__


def redact_text(value: object, max_length: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:max_length] if text else None


def get_base_url() -> str:
    base_url = get_env("SENTRY_BASE_URL") or DEFAULT_BASE_URL
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("SENTRY_BASE_URL must be an http(s) URL without credentials")
    return base_url.rstrip("/")


def fetch_json(url: str, token: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "solo-empire-sentry-readonly/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_project(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, str] = {}
    for key in ("slug", "name"):
        item = redact_text(value.get(key), 120)
        if item:
            result[key] = item
    return result or None


def normalize_issue(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown", "title": "Unknown Sentry issue"}

    item: dict[str, object] = {}
    for key in (
        "id", "shortId", "status", "substatus", "level", "count", "userCount",
        "firstSeen", "lastSeen", "permalink",
    ):
        raw = value.get(key)
        if isinstance(raw, (str, int, float, bool)):
            safe_value = redact_text(raw, 300) if isinstance(raw, str) else raw
            if safe_value is not None:
                item[key] = safe_value

    title = redact_text(value.get("title"), 300)
    if title:
        item["title"] = title
    else:
        item["title"] = "Untitled Sentry issue"

    project = normalize_project(value.get("project"))
    if project:
        item["project"] = project

    if "id" not in item:
        item["id"] = "unknown"
    return item


def report(status: str, issues: list[dict[str, object]] | None = None, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "sentry",
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issues": issues or [],
    }
    payload.update(extra)
    return payload


def run(limit: int, offline: bool) -> dict[str, object]:
    if offline:
        return report("offline", offline=True)

    try:
        token = get_env("SENTRY_AUTH_TOKEN")
        org_slug = get_env("SENTRY_ORG_SLUG")
    except Exception as exc:
        return report("error", error_class=safe_error(exc))
    if not token or not org_slug:
        missing = []
        if not token:
            missing.append("SENTRY_AUTH_TOKEN")
        if not org_slug:
            missing.append("SENTRY_ORG_SLUG")
        return report("not_configured", missing=missing)

    try:
        base_url = get_base_url()
        org = urllib.parse.quote(str(org_slug).strip(), safe="")
        query = urllib.parse.urlencode({"query": "is:unresolved", "limit": str(limit)})
        url = f"{base_url}/api/0/organizations/{org}/issues/?{query}"
        payload = fetch_json(url, token)
        if not isinstance(payload, list):
            raise ValueError("Sentry issues response must be a list")
        issues = [normalize_issue(item) for item in payload[:limit]]
        return report("healthy", issues, organization=redact_text(org_slug, 120), limit=limit)
    except Exception as exc:
        return report("error", error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Sentry unresolved issue summary")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")

    payload = run(args.limit, args.offline)
    # JSON is the stable machine-readable contract for both the CLI and API
    # wrapper. Keep --json for consistency with the other sync commands.
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

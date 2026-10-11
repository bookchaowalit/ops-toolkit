#!/usr/bin/env python3
"""Read-only PostHog saved-insight summary for the Solo Empire API.

The private PostHog API returns full insight definitions, including query
objects and possible event properties. This adapter deliberately projects only
bounded metadata and never captures events, evaluates flags, or mutates data.

Usage:
    python3 posthog_readonly.py --json
    python3 posthog_readonly.py --limit 10 --json
    python3 posthog_readonly.py --offline --json
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


DEFAULT_HOST = "https://us.posthog.com"
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


def get_host() -> str:
    host = get_env("POSTHOG_HOST") or DEFAULT_HOST
    parsed = urllib.parse.urlparse(host)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("POSTHOG_HOST must be an http(s) URL without credentials")
    return host.rstrip("/")


def fetch_json(url: str, api_key: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "solo-empire-posthog-readonly/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for tag in value[:20]:
        safe_tag = redact_text(tag, 80)
        if safe_tag:
            tags.append(safe_tag)
    return tags


def normalize_insight(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown", "name": "Unknown PostHog insight"}

    item: dict[str, object] = {}
    for source_key, output_key, max_length in (
        ("id", "id", 80),
        ("short_id", "short_id", 80),
        ("name", "name", 300),
        ("derived_name", "derived_name", 300),
        ("description", "description", 500),
        ("created_at", "created_at", 80),
        ("updated_at", "updated_at", 80),
        ("last_viewed_at", "last_viewed_at", 80),
        ("last_refresh", "last_refresh", 80),
    ):
        raw = value.get(source_key)
        if isinstance(raw, (str, int, float, bool)):
            safe_value = redact_text(raw, max_length) if isinstance(raw, str) else raw
            if safe_value is not None:
                item[output_key] = safe_value

    for key in ("deleted", "favorited", "is_sample"):
        if isinstance(value.get(key), bool):
            item[key] = value[key]

    tags = normalize_tags(value.get("tags"))
    if tags:
        item["tags"] = tags

    if "id" not in item:
        item["id"] = "unknown"
    if "name" not in item:
        item["name"] = "Untitled PostHog insight"
    return item


def report(status: str, insights: list[dict[str, object]] | None = None, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "posthog",
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "insights": insights or [],
    }
    payload.update(extra)
    return payload


def run(limit: int, offline: bool) -> dict[str, object]:
    if offline:
        return report("offline", offline=True)

    try:
        api_key = get_env("POSTHOG_API_KEY")
        project_id = get_env("POSTHOG_PROJECT_ID")
    except Exception as exc:
        return report("error", error_class=safe_error(exc))

    if not api_key or not project_id:
        missing = []
        if not api_key:
            missing.append("POSTHOG_API_KEY")
        if not project_id:
            missing.append("POSTHOG_PROJECT_ID")
        return report("not_configured", missing=missing)

    try:
        project = urllib.parse.quote(str(project_id).strip(), safe="")
        query = urllib.parse.urlencode({"format": "json", "limit": str(limit)})
        url = f"{get_host()}/api/projects/{project}/insights/?{query}"
        payload = fetch_json(url, api_key)
        if isinstance(payload, dict):
            raw_insights = payload.get("results") or []
            total_count = payload.get("count")
        elif isinstance(payload, list):
            raw_insights = payload
            total_count = len(payload)
        else:
            raise ValueError("PostHog insights response must be an object or list")
        if not isinstance(raw_insights, list):
            raise ValueError("PostHog insights results must be a list")

        insights = [normalize_insight(item) for item in raw_insights[:limit]]
        result: dict[str, object] = {
            "project_id": redact_text(project_id, 120),
            "limit": limit,
            "returned": len(insights),
        }
        if isinstance(total_count, int):
            result["total_count"] = total_count
        return report("healthy", insights, **result)
    except Exception as exc:
        return report("error", error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PostHog saved insight summary")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")

    # JSON is the stable machine-readable contract for both the CLI and API
    # wrapper. Keep --json for consistency with the other sync commands.
    print(json.dumps(run(args.limit, args.offline), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

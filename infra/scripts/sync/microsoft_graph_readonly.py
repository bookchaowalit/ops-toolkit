#!/usr/bin/env python3
"""Read-only Microsoft Graph signed-in-user metadata for Solo Empire.

The adapter requires a delegated Microsoft Graph access token and only calls
GET /me with an explicit $select. It never reads mail, files, calendar,
Teams, contacts, or performs any mutation.
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


GRAPH_API_URL = "https://graph.microsoft.com/v1.0/me"
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


def authorization_header(token: str) -> str:
    stripped = token.strip()
    return stripped if stripped.lower().startswith("bearer ") else f"Bearer {stripped}"


def call_graph(token: str) -> object:
    query = urllib.parse.urlencode({"$select": "id,displayName"})
    request = urllib.request.Request(
        f"{GRAPH_API_URL}?{query}",
        headers={
            "Accept": "application/json",
            "Authorization": authorization_header(token),
            "User-Agent": "solo-empire-microsoft-graph-readonly/1.0",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_profile(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    item: dict[str, object] = {}
    for key in ("id", "displayName"):
        safe_value = redact_text(value.get(key), 240)
        if safe_value:
            item[key] = safe_value
    return item


def report(status: str, resource: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "microsoft-graph",
        "status": status,
        "resource": resource,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    return payload


def run(resource: str, offline: bool) -> dict[str, object]:
    if offline:
        return report("offline", resource, profile={}, offline=True)

    try:
        token = get_env("MICROSOFT_GRAPH_ACCESS_TOKEN")
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))
    if not token:
        return report("not_configured", resource, missing=["MICROSOFT_GRAPH_ACCESS_TOKEN"])

    try:
        payload = call_graph(token)
        if not isinstance(payload, dict) or isinstance(payload.get("error"), dict):
            return report("error", resource, error_class="microsoft_graph_error")
        profile = normalize_profile(payload)
        if not profile.get("id"):
            raise ValueError("Microsoft Graph /me response must contain id")
        return report("healthy", resource, profile=profile)
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Microsoft Graph signed-in-user metadata")
    parser.add_argument("--resource", choices=("me",), default="me")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(run(args.resource, args.offline), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

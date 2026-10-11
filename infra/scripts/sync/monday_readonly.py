#!/usr/bin/env python3
"""Read-only monday.com board metadata for Solo Empire.

The adapter uses the monday.com GraphQL API with a boards:read token and only
queries bounded board metadata. It never reads items, columns, updates, docs,
or performs any mutation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_env  # noqa: E402


MAX_LIMIT = 100
MONDAY_API_URL = "https://api.monday.com/v2"
BOARDS_QUERY = """
query ($limit: Int!) {
  boards(limit: $limit) {
    id
    name
    state
  }
}
""".strip()
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
    """Return monday's raw token format, tolerating an accidentally prefixed value."""
    stripped = token.strip()
    return stripped[7:].strip() if stripped.lower().startswith("bearer ") else stripped


def call_monday(token: str, limit: int) -> object:
    request_body = json.dumps({
        "query": BOARDS_QUERY,
        "variables": {"limit": limit},
    }).encode("utf-8")
    request = urllib.request.Request(
        MONDAY_API_URL,
        data=request_body,
        headers={
            "Accept": "application/json",
            "Authorization": authorization_header(token),
            "Content-Type": "application/json",
            "User-Agent": "solo-empire-monday-readonly/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_board(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown"}
    item: dict[str, object] = {}
    board_id = value.get("id")
    if isinstance(board_id, (int, str)) and str(board_id).strip():
        item["id"] = str(board_id).strip()
    for key in ("name", "state"):
        safe_value = redact_text(value.get(key), 240)
        if safe_value:
            item[key] = safe_value
    item.setdefault("id", "unknown")
    return item


def report(status: str, resource: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "monday",
        "status": status,
        "resource": resource,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    return payload


def run(resource: str, limit: int, offline: bool) -> dict[str, object]:
    if offline:
        return report("offline", resource, boards=[], limit=limit, returned=0, offline=True)

    try:
        token = get_env("MONDAY_API_TOKEN")
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))
    if not token:
        return report("not_configured", resource, missing=["MONDAY_API_TOKEN"])

    try:
        payload = call_monday(token, limit)
        if not isinstance(payload, dict):
            return report("error", resource, error_class="monday_api_error")
        if isinstance(payload.get("errors"), list) and payload["errors"]:
            return report("error", resource, error_class="monday_graphql_error")
        data = payload.get("data")
        values = data.get("boards") if isinstance(data, dict) else None
        if not isinstance(values, list):
            return report("error", resource, error_class="monday_api_error")
        boards = [normalize_board(item) for item in values[:limit]]
        return report("healthy", resource, boards=boards, limit=limit, returned=len(boards))
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only monday.com board metadata")
    parser.add_argument("--resource", choices=("boards",), default="boards")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")
    print(json.dumps(run(args.resource, args.limit, args.offline), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

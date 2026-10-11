#!/usr/bin/env python3
"""Read-only Slack public-channel summary for the Solo Empire API.

This adapter only calls ``auth.test`` and ``conversations.list`` for public
channels. It never reads messages, sends messages, joins channels, or changes
workspace state.

Usage:
    python3 slack_readonly.py --json
    python3 slack_readonly.py --limit 10 --json
    python3 slack_readonly.py --offline --json
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


MAX_LIMIT = 100
SLACK_API_BASE = "https://slack.com/api"
REDACTION_PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)[^\s,;]+"), r"\1[REDACTED]"),
)


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    return type(exc).__name__


def redact_text(value: object, max_length: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:max_length] if text else None


def call_slack(method: str, token: str, params: dict[str, str] | None = None, post: bool = False) -> object:
    query = urllib.parse.urlencode(params or {})
    url = f"{SLACK_API_BASE}/{method}"
    data = query.encode("utf-8") if post else None
    if not post and query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "solo-empire-slack-readonly/1.0",
        },
        method="POST" if post else "GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def slack_error(payload: object) -> str | None:
    if not isinstance(payload, dict) or payload.get("ok") is True:
        return None
    error = redact_text(payload.get("error"), 100)
    return f"slack_{error}" if error else "slack_api_error"


def normalize_channel(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown", "name": "unknown"}
    item: dict[str, object] = {}
    for key in ("id", "name"):
        safe_value = redact_text(value.get(key), 120)
        if safe_value:
            item[key] = safe_value
    for key in ("is_channel", "is_private", "is_archived", "is_member", "is_shared", "is_ext_shared"):
        if isinstance(value.get(key), bool):
            item[key] = value[key]
    if isinstance(value.get("num_members"), int) and value["num_members"] >= 0:
        item["num_members"] = value["num_members"]
    item.setdefault("id", "unknown")
    item.setdefault("name", "unknown")
    return item


def report(status: str, channels: list[dict[str, object]] | None = None, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "slack",
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channels": channels or [],
    }
    payload.update(extra)
    return payload


def run(limit: int, offline: bool) -> dict[str, object]:
    if offline:
        return report("offline", offline=True)

    try:
        token = get_env("SLACK_BOT_TOKEN")
    except Exception as exc:
        return report("error", error_class=safe_error(exc))
    if not token:
        return report("not_configured", missing=["SLACK_BOT_TOKEN"])

    try:
        auth_payload = call_slack("auth.test", token, post=True)
        auth_error = slack_error(auth_payload)
        if auth_error:
            return report("error", error_class=auth_error)

        payload = call_slack(
            "conversations.list",
            token,
            {
                "exclude_archived": "true",
                "limit": str(limit),
                "types": "public_channel",
            },
        )
        api_error = slack_error(payload)
        if api_error:
            return report("error", error_class=api_error)
        if not isinstance(payload, dict) or not isinstance(payload.get("channels"), list):
            raise ValueError("Slack conversations.list response must contain channels")

        channels = [normalize_channel(item) for item in payload["channels"][:limit]]
        response_metadata = payload.get("response_metadata")
        next_cursor = response_metadata.get("next_cursor") if isinstance(response_metadata, dict) else ""
        return report(
            "healthy",
            channels,
            workspace=redact_text(auth_payload.get("team"), 120) if isinstance(auth_payload, dict) else None,
            team_id=redact_text(auth_payload.get("team_id"), 80) if isinstance(auth_payload, dict) else None,
            limit=limit,
            returned=len(channels),
            has_more=bool(next_cursor),
        )
    except Exception as exc:
        return report("error", error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Slack public-channel summary")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")

    print(json.dumps(run(args.limit, args.offline), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

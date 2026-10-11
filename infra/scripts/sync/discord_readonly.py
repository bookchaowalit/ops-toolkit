#!/usr/bin/env python3
"""Read-only Discord guild metadata for the Solo Empire API.

The adapter uses a Discord bot token and only calls GET endpoints. It returns
bounded partial guild metadata and never reads messages or members, sends
messages, opens DMs, registers commands, or performs any mutation.
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
DISCORD_API_BASE = "https://discord.com/api/v10"
REDACTION_PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bot\s+)[^\s,;]+"), r"\1[REDACTED]"),
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


def discord_authorization(token: str) -> str:
    """Accept a raw bot token while avoiding duplicate auth schemes."""
    stripped = token.strip()
    if stripped.lower().startswith(("bot ", "bearer ")):
        return stripped
    return f"Bot {stripped}"


def call_discord(path: str, token: str, params: dict[str, str] | None = None) -> object:
    query = urllib.parse.urlencode(params or {})
    url = f"{DISCORD_API_BASE}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": discord_authorization(token),
            "User-Agent": "solo-empire-discord-readonly/1.0",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_guild(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown"}
    item: dict[str, object] = {}
    for key in ("id", "name", "icon"):
        safe_value = redact_text(value.get(key), 240)
        if safe_value:
            item[key] = safe_value
    if isinstance(value.get("owner"), bool):
        item["owner"] = value["owner"]
    features = value.get("features")
    if isinstance(features, list):
        item["features"] = [feature for feature in features[:50] if isinstance(feature, str)][:50]
    item.setdefault("id", "unknown")
    return item


def report(status: str, resource: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "discord",
        "status": status,
        "resource": resource,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    return payload


def run(resource: str, limit: int, offline: bool) -> dict[str, object]:
    if offline:
        return report("offline", resource, guilds=[], offline=True)

    try:
        token = get_env("DISCORD_BOT_TOKEN")
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))
    if not token:
        return report("not_configured", resource, missing=["DISCORD_BOT_TOKEN"])

    try:
        payload = call_discord("users/@me/guilds", token, {"limit": str(limit)})
        if not isinstance(payload, list):
            raise ValueError("Discord guild response must be a list")
        guilds = [normalize_guild(item) for item in payload[:limit]]
        return report(
            "healthy",
            resource,
            guilds=guilds,
            limit=limit,
            returned=len(guilds),
            has_more=len(payload) > limit,
        )
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Discord guild metadata")
    parser.add_argument("--resource", choices=("guilds",), default="guilds")
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

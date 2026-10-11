#!/usr/bin/env python3
"""Read-only Notion page/data-source inventory for the Solo Empire API.

This adapter only calls Notion's search endpoint. It returns bounded metadata
and deliberately drops page properties, block content, user details, covers,
and icons. It never creates, updates, archives, or deletes Notion objects.

Usage:
    python3 notion_readonly.py --json
    python3 notion_readonly.py --query "project" --limit 10 --json
    python3 notion_readonly.py --offline --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_env  # noqa: E402


MAX_LIMIT = 100
MAX_QUERY_LENGTH = 100
NOTION_API_BASE = "https://api.notion.com"
DEFAULT_NOTION_VERSION = "2026-03-11"
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


def call_notion(token: str, body: dict[str, object], version: str) -> object:
    request = urllib.request.Request(
        f"{NOTION_API_BASE}/v1/search",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": version,
            "User-Agent": "solo-empire-notion-readonly/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def notion_error(payload: object) -> str | None:
    if not isinstance(payload, dict) or payload.get("object") != "error":
        return None
    code = redact_text(payload.get("code"), 80)
    return f"notion_{code}" if code else "notion_api_error"


def _extract_title(value: object) -> str | None:
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            text = item.get("plain_text")
            if not isinstance(text, str):
                text_value = item.get("text")
                text = text_value.get("content") if isinstance(text_value, dict) else None
            if isinstance(text, str):
                parts.append(text)
        return redact_text("".join(parts))
    return redact_text(value)


def _result_title(value: dict[str, object]) -> str | None:
    direct_title = _extract_title(value.get("title"))
    if direct_title:
        return direct_title
    properties = value.get("properties")
    if not isinstance(properties, dict):
        return None
    for prop in properties.values():
        if not isinstance(prop, dict) or prop.get("type") != "title":
            continue
        title = _extract_title(prop.get("title"))
        if title:
            return title
    return None


def normalize_result(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown", "object": "unknown"}

    item: dict[str, object] = {}
    for key in ("id", "object", "created_time", "last_edited_time", "url", "public_url"):
        safe_value = redact_text(value.get(key), 240)
        if safe_value:
            item[key] = safe_value
    for key in ("in_trash", "is_archived", "is_locked"):
        if isinstance(value.get(key), bool):
            item[key] = value[key]

    parent = value.get("parent")
    if isinstance(parent, dict):
        safe_parent: dict[str, object] = {}
        parent_type = redact_text(parent.get("type"), 80)
        if parent_type:
            safe_parent["type"] = parent_type
        for key in ("page_id", "database_id", "data_source_id"):
            parent_id = redact_text(parent.get(key), 100)
            if parent_id:
                safe_parent[key] = parent_id
        if safe_parent:
            item["parent"] = safe_parent

    title = _result_title(value)
    if title:
        item["title"] = title
    item.setdefault("id", "unknown")
    item.setdefault("object", "unknown")
    return item


def report(status: str, results: list[dict[str, object]] | None = None, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "notion",
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results or [],
    }
    payload.update(extra)
    return payload


def run(limit: int, query: str | None, offline: bool) -> dict[str, object]:
    if offline:
        return report("offline", offline=True)

    try:
        token = get_env("NOTION_BOT_TOKEN")
    except Exception as exc:
        return report("error", error_class=safe_error(exc))
    if not token:
        return report("not_configured", missing=["NOTION_BOT_TOKEN"])

    body: dict[str, object] = {"page_size": limit}
    if query:
        body["query"] = query
    version = os.environ.get("NOTION_VERSION", DEFAULT_NOTION_VERSION).strip() or DEFAULT_NOTION_VERSION

    try:
        payload = call_notion(token, body, version)
        api_error = notion_error(payload)
        if api_error:
            return report("error", error_class=api_error)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Notion search response must contain results")

        results = [normalize_result(item) for item in payload["results"][:limit]]
        return report(
            "healthy",
            results,
            query=query,
            version=version,
            limit=limit,
            returned=len(results),
            has_more=bool(payload.get("has_more")),
        )
    except Exception as exc:
        return report("error", error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Notion page/data-source inventory")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--query", default="")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")
    query = args.query.strip()
    if len(query) > MAX_QUERY_LENGTH:
        parser.error(f"--query must be at most {MAX_QUERY_LENGTH} characters")

    print(json.dumps(run(args.limit, query or None, args.offline), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

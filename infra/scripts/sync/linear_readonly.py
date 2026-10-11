#!/usr/bin/env python3
"""Read-only Linear team and project metadata for the Solo Empire API.

The adapter uses Linear's GraphQL query endpoint with a personal API key. It
returns bounded IDs/names/keys only and never sends a mutation, reads issue
descriptions/comments, or changes Linear state.

Usage:
    python3 linear_readonly.py --resource teams --json
    python3 linear_readonly.py --resource projects --limit 10 --json
    python3 linear_readonly.py --resource teams --offline --json
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
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
TEAM_QUERY = """
query Teams($first: Int!) {
  teams(first: $first) {
    nodes { id name key }
    pageInfo { hasNextPage }
  }
}
"""
PROJECT_QUERY = """
query Projects($first: Int!) {
  projects(first: $first) {
    nodes { id name }
    pageInfo { hasNextPage }
  }
}
"""
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


def call_linear(token: str, query: str, variables: dict[str, object]) -> object:
    request = urllib.request.Request(
        LINEAR_GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "solo-empire-linear-readonly/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def has_graphql_error(payload: object) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("errors"), list) and bool(payload["errors"])


def normalize_team(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown"}
    item: dict[str, object] = {}
    for key in ("id", "name", "key"):
        safe_value = redact_text(value.get(key), 240)
        if safe_value:
            item[key] = safe_value
    item.setdefault("id", "unknown")
    return item


def normalize_project(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"id": "unknown"}
    item: dict[str, object] = {}
    for key in ("id", "name"):
        safe_value = redact_text(value.get(key), 240)
        if safe_value:
            item[key] = safe_value
    item.setdefault("id", "unknown")
    return item


def report(status: str, resource: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "linear",
        "status": status,
        "resource": resource,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    return payload


def run(resource: str, limit: int, offline: bool) -> dict[str, object]:
    if offline:
        return report(
            "offline",
            resource,
            **({"teams": []} if resource == "teams" else {"projects": []}),
            offline=True,
        )

    try:
        token = get_env("LINEAR_API_KEY")
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))
    if not token:
        return report("not_configured", resource, missing=["LINEAR_API_KEY"])

    try:
        query = TEAM_QUERY if resource == "teams" else PROJECT_QUERY
        payload = call_linear(token, query, {"first": limit})
        if has_graphql_error(payload):
            return report("error", resource, error_class="linear_graphql_error")
        data = payload.get("data") if isinstance(payload, dict) else None
        connection = data.get(resource) if isinstance(data, dict) else None
        nodes = connection.get("nodes") if isinstance(connection, dict) else None
        if not isinstance(nodes, list):
            raise ValueError(f"Linear {resource} response must contain nodes")
        normalized = [
            normalize_team(item) if resource == "teams" else normalize_project(item)
            for item in nodes[:limit]
        ]
        page_info = connection.get("pageInfo") if isinstance(connection, dict) else None
        key = resource
        return report(
            "healthy",
            resource,
            **{
                key: normalized,
                "limit": limit,
                "returned": len(normalized),
                "has_more": bool(page_info.get("hasNextPage")) if isinstance(page_info, dict) else False,
            },
        )
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Linear team/project metadata")
    parser.add_argument("--resource", choices=("teams", "projects"), default="teams")
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

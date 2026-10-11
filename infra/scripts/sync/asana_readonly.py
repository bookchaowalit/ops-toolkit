#!/usr/bin/env python3
"""Read-only Asana workspace and project metadata for the Solo Empire API.

The adapter only calls Asana GET endpoints. It returns bounded workspace or
project metadata and never reads task descriptions, comments, custom fields,
or performs any create/update/delete operation.

Usage:
    python3 asana_readonly.py --resource workspaces --json
    python3 asana_readonly.py --resource projects --workspace 12345 --json
    python3 asana_readonly.py --resource workspaces --offline --json
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
MAX_WORKSPACE_LENGTH = 100
ASANA_API_BASE = "https://app.asana.com/api/1.0"
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


def call_asana(path: str, token: str, params: dict[str, str]) -> object:
    query = urllib.parse.urlencode(params)
    url = f"{ASANA_API_BASE}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "solo-empire-asana-readonly/1.0",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_workspace(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"gid": "unknown", "resource_type": "workspace"}
    item: dict[str, object] = {}
    for key in ("gid", "resource_type", "name", "path", "uri"):
        safe_value = redact_text(value.get(key), 240)
        if safe_value:
            item[key] = safe_value
    if isinstance(value.get("is_organization"), bool):
        item["is_organization"] = value["is_organization"]
    item.setdefault("gid", "unknown")
    item.setdefault("resource_type", "workspace")
    return item


def normalize_project(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"gid": "unknown", "resource_type": "project"}
    item: dict[str, object] = {}
    for key in ("gid", "resource_type", "name", "permalink_url"):
        safe_value = redact_text(value.get(key), 240)
        if safe_value:
            item[key] = safe_value
    for key in ("archived", "public"):
        if isinstance(value.get(key), bool):
            item[key] = value[key]
    item.setdefault("gid", "unknown")
    item.setdefault("resource_type", "project")
    return item


def report(status: str, resource: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service": "asana",
        "status": status,
        "resource": resource,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    return payload


def run(resource: str, limit: int, workspace: str | None, offline: bool) -> dict[str, object]:
    if offline:
        offline_payload: dict[str, object] = {
            "workspaces" if resource == "workspaces" else "projects": [],
            "offline": True,
        }
        if workspace:
            offline_payload["workspace"] = workspace
        return report("offline", resource, **offline_payload)
    if resource == "projects" and not workspace:
        return report("error", resource, error_class="workspace_required")

    try:
        token = get_env("ASANA_PERSONAL_ACCESS_TOKEN")
    except Exception as exc:
        return report("error", resource, error_class=safe_error(exc))
    if not token:
        return report("not_configured", resource, missing=["ASANA_PERSONAL_ACCESS_TOKEN"])

    try:
        if resource == "workspaces":
            payload = call_asana("workspaces", token, {"limit": str(limit)})
            key = "workspaces"
            values = payload.get("data") if isinstance(payload, dict) else None
            normalized = [normalize_workspace(item) for item in values[:limit]] if isinstance(values, list) else None
        else:
            payload = call_asana(
                "projects",
                token,
                {"workspace": workspace or "", "limit": str(limit), "archived": "false"},
            )
            key = "projects"
            values = payload.get("data") if isinstance(payload, dict) else None
            normalized = [normalize_project(item) for item in values[:limit]] if isinstance(values, list) else None
        if normalized is None:
            raise ValueError(f"Asana {resource} response must contain data")
        next_page = payload.get("next_page") if isinstance(payload, dict) else None
        return report(
            "healthy",
            resource,
            **{
                key: normalized,
                "limit": limit,
                "returned": len(normalized),
                "has_more": bool(next_page.get("offset")) if isinstance(next_page, dict) else False,
                **({"workspace": workspace} if workspace else {}),
            },
        )
    except Exception as exc:
        error_payload: dict[str, object] = {"error_class": safe_error(exc)}
        if workspace:
            error_payload["workspace"] = workspace
        return report("error", resource, **error_payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Asana workspace/project metadata")
    parser.add_argument("--resource", choices=("workspaces", "projects"), default="workspaces")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")
    workspace = args.workspace.strip() or None
    if workspace and len(workspace) > MAX_WORKSPACE_LENGTH:
        parser.error(f"--workspace must be at most {MAX_WORKSPACE_LENGTH} characters")
    if args.resource == "projects" and not workspace and not args.offline:
        parser.error("--workspace is required for --resource projects")

    print(json.dumps(run(args.resource, args.limit, workspace, args.offline), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

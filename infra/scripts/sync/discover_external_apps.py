#!/usr/bin/env python3
"""Discover public API and MCP catalog metadata without loading credentials.

This is discovery only. It does not install an integration, call a provider
business API, store payloads in SQLite, or change the Data Lake. Promote a
discovered record to a real integration only after the normal lake-first,
credential, privacy, and focused-test gates are complete.

Usage:
    python3 discover_external_apps.py --json
    python3 discover_external_apps.py --source apis-guru --limit 10000 --json
    python3 discover_external_apps.py --source all --limit 1000 --offline --json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


APIS_GURU_URL = "https://api.apis.guru/v2/list.json"
MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0.1/servers"
MAX_LIMIT = 10_000
PAGE_SIZE = 100


def fetch_json(url: str, timeout: float) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "solo-empire-external-discovery/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_apis_guru(payload: object, limit: int) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("APIs.guru payload must be an object")

    records: list[dict] = []
    for provider, entry in sorted(payload.items()):
        if len(records) >= limit or not isinstance(entry, dict):
            break
        preferred = entry.get("preferred")
        versions = entry.get("versions") or {}
        version = versions.get(preferred) if preferred else None
        if not isinstance(version, dict):
            version = next((item for item in versions.values() if isinstance(item, dict)), {})
        info = version.get("info") if isinstance(version, dict) else {}
        info = info if isinstance(info, dict) else {}
        categories = info.get("x-apisguru-categories") or []
        origin = info.get("x-origin") or []
        origin_url = origin[0].get("url") if origin and isinstance(origin[0], dict) else None
        record_id = f"apis-guru:{provider}"
        records.append({
            "id": record_id,
            "name": info.get("title") or provider,
            "source": "apis-guru",
            "status": "discovered",
            "provider": provider,
            "category": categories[0] if categories else "uncategorized",
            "version": version.get("info", {}).get("version") if isinstance(version.get("info"), dict) else preferred,
            "api_spec_url": origin_url or version.get("swaggerUrl") or version.get("link"),
            "source_url": APIS_GURU_URL,
            "updated_at": version.get("updated"),
        })
    return records


def normalize_mcp_page(payload: object) -> tuple[list[dict], str | None]:
    if not isinstance(payload, dict):
        raise ValueError("MCP Registry payload must be an object")

    records: list[dict] = []
    for row in payload.get("servers") or []:
        if not isinstance(row, dict):
            continue
        server = row.get("server") if isinstance(row.get("server"), dict) else row
        metadata = row.get("_meta") or {}
        official = metadata.get("io.modelcontextprotocol.registry/official") or {}
        remotes = server.get("remotes") or []
        remote_url = remotes[0].get("url") if remotes and isinstance(remotes[0], dict) else None
        name = server.get("title") or server.get("name") or "Unknown MCP server"
        records.append({
            "id": f"mcp-registry:{server.get('name', name)}",
            "name": name,
            "source": "mcp-registry",
            "status": official.get("status") or "discovered",
            "provider": server.get("name"),
            "category": "mcp-server",
            "version": server.get("version"),
            "api_spec_url": remote_url,
            "source_url": MCP_REGISTRY_URL,
            "updated_at": official.get("updatedAt") or official.get("publishedAt"),
        })
    metadata = payload.get("metadata") or {}
    next_cursor = metadata.get("nextCursor") if isinstance(metadata, dict) else None
    return records, next_cursor


def discover_apis_guru(limit: int, timeout: float) -> tuple[list[dict], dict]:
    payload = fetch_json(APIS_GURU_URL, timeout)
    records = normalize_apis_guru(payload, limit)
    return records, {"source": "apis-guru", "status": "ok", "count": len(records)}


def discover_mcp_registry(limit: int, timeout: float) -> tuple[list[dict], dict]:
    records: list[dict] = []
    cursor: str | None = None
    pages = 0
    while len(records) < limit and pages < (MAX_LIMIT // PAGE_SIZE) + 1:
        query = {"limit": str(min(PAGE_SIZE, limit - len(records)))}
        if cursor:
            query["cursor"] = cursor
        url = f"{MCP_REGISTRY_URL}?{urllib.parse.urlencode(query)}"
        page, cursor = normalize_mcp_page(fetch_json(url, timeout))
        records.extend(page)
        pages += 1
        if not cursor or not page:
            break
    return records[:limit], {
        "source": "mcp-registry",
        "status": "ok",
        "count": len(records[:limit]),
        "pages": pages,
    }


def discover(source: str, limit: int, timeout: float, offline: bool) -> dict:
    if offline:
        return {
            "schema_version": "external-app-discovery.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "limit": limit,
            "offline": True,
            "sources": [{"source": source, "status": "offline", "count": 0}],
            "total_discovered": 0,
            "items": [],
        }

    sources = ["apis-guru", "mcp-registry"] if source == "all" else [source]
    items: list[dict] = []
    source_reports: list[dict] = []
    failures: list[dict] = []

    # Keep a predictable share for each source when both are requested.
    quotas = {name: limit // len(sources) for name in sources}
    quotas[sources[-1]] += limit - sum(quotas.values())
    for name in sources:
        try:
            if name == "apis-guru":
                records, report = discover_apis_guru(quotas[name], timeout)
            else:
                records, report = discover_mcp_registry(quotas[name], timeout)
            items.extend(records)
            source_reports.append(report)
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            failure = {"source": name, "status": "error", "error_class": type(exc).__name__, "count": 0}
            source_reports.append(failure)
            failures.append(failure)

    return {
        "schema_version": "external-app-discovery.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "limit": limit,
        "offline": False,
        "sources": source_reports,
        "total_discovered": len(items),
        "items": items[:limit],
        "partial": bool(failures),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover public API and MCP catalog metadata")
    parser.add_argument("--source", choices=("apis-guru", "mcp-registry", "all"), default="all")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")
    if args.timeout <= 0 or args.timeout > 60:
        parser.error("--timeout must be greater than 0 and at most 60 seconds")

    result = discover(args.source, args.limit, args.timeout, args.offline)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Discovered {result['total_discovered']} records from {args.source}")
        for report in result["sources"]:
            print(f"  {report['source']}: {report['status']} ({report['count']})")
    return 0 if not result.get("partial") else 2


if __name__ == "__main__":
    raise SystemExit(main())

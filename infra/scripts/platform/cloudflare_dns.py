#!/usr/bin/env python3
"""Plan and apply Cloudflare DNS for bookchaowalit.com from the domain registry.

infra/platform/domains.yaml is the desired state. Each platform entry with a
`dns` block declares one record (type, content, proxied). This tool:

  --list    read-only: print the zone's current records (name, type, target)
  --plan    read-only: diff desired records against Cloudflare
  --apply   create or update only the planned records; requires --confirm

It never deletes records, never touches hostnames that have no `dns` block
(the Vercel wildcard keeps serving those), and never prints the API token.
The token is resolved through secret_resolver (Infisical first).

Usage:
    SECRETS_PROVIDER=infisical-cli INFISICAL_CLI_ENABLED=true \
      python3 infra/scripts/platform/cloudflare_dns.py --plan
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
from secret_resolver import get_secret  # noqa: E402

REGISTRY_PATH = ROOT / "infra" / "platform" / "domains.yaml"
API = "https://api.cloudflare.com/client/v4"
ALLOWED_TYPES = {"CNAME", "A", "AAAA", "TXT"}


def api(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read() or b"{}")
    if not payload.get("success"):
        codes = [item.get("code") for item in payload.get("errors", [])]
        messages = [item.get("message") for item in payload.get("errors", [])]
        raise RuntimeError(f"Cloudflare API {method} {path.split('?')[0]} failed: {codes} {messages}")
    return payload


def desired_records(registry: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for entry in registry.get("platform", []):
        dns_entries = entry.get("dns") or []
        if isinstance(dns_entries, dict):
            dns_entries = [dns_entries]
        for dns in dns_entries:
            if dns.get("type") not in ALLOWED_TYPES:
                raise ValueError(f"{entry['host']}: unsupported dns type {dns.get('type')!r}")
            name = dns.get("name", entry["host"])
            if not name.endswith(registry["zone"]):
                raise ValueError(f"{name}: outside zone {registry['zone']}")
            records.append(
                {
                    "name": name,
                    "type": dns["type"],
                    "content": dns["content"],
                    "proxied": bool(dns.get("proxied", False)),
                    "ttl": 1,
                    "comment": f"solo-empire domains.yaml: {entry['purpose']}"[:100],
                }
            )
    return records


def zone_id(zone: str, token: str) -> str:
    result = api("GET", f"/zones?name={urllib.parse.quote(zone)}", token)["result"]
    if not result:
        raise RuntimeError(f"zone {zone} not visible to this token")
    return result[0]["id"]


def current_records(zid: str, token: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = api("GET", f"/zones/{zid}/dns_records?per_page=100&page={page}", token)
        records.extend(payload["result"])
        if page >= payload["result_info"].get("total_pages", 1):
            return records
        page += 1


def plan(desired: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for record in existing:
        by_name.setdefault(record["name"], []).append(record)
    actions = []
    for record in desired:
        matches = by_name.get(record["name"], [])
        same_type = [item for item in matches if item["type"] == record["type"]]
        if not matches:
            actions.append(("create", record, None))
        elif same_type and all(
            same_type[0].get(key) == record[key] for key in ("content", "proxied")
        ):
            actions.append(("noop", record, same_type[0]))
        elif same_type:
            actions.append(("update", record, same_type[0]))
        else:
            actions.append(("conflict", record, matches[0]))
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true")
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="required with --apply")
    args = parser.parse_args()

    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    token = get_secret("CLOUDFLARE_API_TOKEN", required=True)
    try:
        zid = zone_id(registry["zone"], token)
        existing = current_records(zid, token)
    except RuntimeError as error:
        print(f"  FAIL: {error}")
        print(f"  HINT: the token needs Zone > DNS > Read (plan) and Edit (apply) on {registry['zone']}")
        return 1

    if args.list:
        for record in sorted(existing, key=lambda item: (item["name"], item["type"])):
            content = record["content"] if record["type"] in {"CNAME", "A", "AAAA", "MX", "NS"} else "<redacted>"
            print(f"  {record['type']:6} {record['name']:45} {content} proxied={record.get('proxied')}")
        print(f"  OK: {len(existing)} records")
        return 0

    actions = plan(desired_records(registry), existing)
    for action, record, current in actions:
        detail = f"{record['type']} {record['name']} -> {record['content']} proxied={record['proxied']}"
        if current and action in {"update", "conflict"}:
            detail += f" (current {current['type']} {current['content']})"
        print(f"  {action.upper():8} {detail}")
    if any(action == "conflict" for action, _, _ in actions):
        print("  FAIL: resolve conflicts in domains.yaml before applying")
        return 1
    if args.plan:
        return 0
    if not args.confirm:
        print("  FAIL: --apply needs --confirm")
        return 1
    for action, record, current in actions:
        if action == "create":
            api("POST", f"/zones/{zid}/dns_records", token, record)
        elif action == "update" and current:
            api("PATCH", f"/zones/{zid}/dns_records/{current['id']}", token, record)
    print("  OK: applied; re-run --plan to confirm NOOP, then `task domains:snapshot`")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Manage Neon projects for Solo Empire systems (one project per system).

  --list                      read-only: list projects (id, name, region)
  --ensure NAME --region R    create the project when it does not exist; idempotent
  --store-uri NAME            write the project's connection URI to Infisical
                              (path /<infisical-path>, secret CRM_DATABASE_URL by default)
                              without printing it. Direct (unpooled) by default because
                              crm-api uses session-level SET and startup options that
                              PgBouncer transaction pooling does not keep; pass --pooled
                              for stateless high-concurrency services.

The API key is resolved through secret_resolver (Infisical first). Connection
strings are never printed or logged.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
from secret_resolver import DEFAULT_INFISICAL_PROJECT_ID, SecretResolutionError, get_secret  # noqa: E402

API = "https://console.neon.tech/api/v2"


def api(method: str, path: str, key: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read() or b"{}").get("message", "")
        raise RuntimeError(f"Neon API {method} {path.split('?')[0]} failed: HTTP {error.code} {detail}") from None


def org_query(key: str) -> str:
    try:
        org = get_secret("NEON_ORG_ID") or ""
    except SecretResolutionError:
        org = ""  # optional; a personal or single-org key works without it
    if not org:
        orgs = api("GET", "/users/me/organizations", key).get("organizations", [])
        org = orgs[0]["id"] if len(orgs) == 1 else ""
    return f"?org_id={urllib.parse.quote(org)}" if org else ""


def projects(key: str) -> list[dict[str, Any]]:
    return api("GET", f"/projects{org_query(key)}", key).get("projects", [])


def find(key: str, name: str) -> dict[str, Any] | None:
    return next((item for item in projects(key) if item["name"] == name), None)


def ensure(key: str, name: str, region: str) -> dict[str, Any]:
    existing = find(key, name)
    if existing:
        print(f"  OK: project {name} exists ({existing['id']}, {existing['region_id']})")
        return existing
    query = org_query(key)
    body: dict[str, Any] = {"project": {"name": name, "region_id": region, "pg_version": 16}}
    if query:
        body["project"]["org_id"] = urllib.parse.unquote(query.split("=", 1)[1])
    created = api("POST", "/projects", key, body)["project"]
    print(f"  OK: created project {name} ({created['id']}, {created['region_id']})")
    return created


def connection_uri(key: str, project_id: str, *, pooled: bool) -> str:
    branches = api("GET", f"/projects/{project_id}/branches", key)["branches"]
    branch = next(item for item in branches if item.get("default") or item.get("primary"))
    databases = api("GET", f"/projects/{project_id}/branches/{branch['id']}/databases", key)["databases"]
    database = databases[0]
    query = urllib.parse.urlencode(
        {"database_name": database["name"], "role_name": database["owner_name"], "branch_id": branch["id"], "pooled": "true" if pooled else "false"}
    )
    return api("GET", f"/projects/{project_id}/connection_uri?{query}", key)["uri"]


def store_in_infisical(name: str, value: str, path: str) -> None:
    project = os.environ.get("INFISICAL_PROJECT_ID", DEFAULT_INFISICAL_PROJECT_ID)
    environment = os.environ.get("INFISICAL_ENVIRONMENT", "dev")
    subprocess.run(
        ["infisical", "secrets", "folders", "create", "--name", path.strip("/"), "--path", "/",
         "--env", environment, "--projectId", project],
        capture_output=True, text=True, check=False,
    )
    result = subprocess.run(
        ["infisical", "secrets", "set", f"{name}={value}", "--path", path, "--env", environment,
         "--projectId", project, "--silent"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"infisical secrets set failed for {name} at {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true")
    group.add_argument("--ensure", metavar="NAME")
    group.add_argument("--store-uri", metavar="NAME")
    parser.add_argument("--region", default="aws-ap-southeast-1")
    parser.add_argument("--secret-name", default="CRM_DATABASE_URL")
    parser.add_argument("--infisical-path", default="/crm")
    parser.add_argument("--pooled", action="store_true", help="store the PgBouncer pooled URI instead of the direct one")
    args = parser.parse_args()

    key = get_secret("NEON_API_KEY", required=True)
    try:
        if args.list:
            for item in projects(key):
                print(f"  {item['id']:28} {item['name']:28} {item['region_id']}")
            return 0
        if args.ensure:
            ensure(key, args.ensure, args.region)
            return 0
        project = find(key, args.store_uri)
        if not project:
            print(f"  FAIL: project {args.store_uri} not found")
            return 1
        store_in_infisical(args.secret_name, connection_uri(key, project["id"], pooled=args.pooled), args.infisical_path)
        print(f"  OK: stored {args.secret_name} in Infisical {args.infisical_path} (value not shown)")
        return 0
    except RuntimeError as error:
        print(f"  FAIL: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

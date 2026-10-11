#!/usr/bin/env python3
"""Snapshot and check the bookchaowalit.com subdomain registry.

infra/platform/domains.yaml is the hand-maintained source of truth for the zone,
naming rules, and platform subdomains. The portfolio fleet bound in Vercel is
too large to list by hand, so `--snapshot` reads it from the logged-in Vercel
CLI (read-only `vercel domains inspect`), probes each hostname over HTTPS, and
writes repository-catalog/snapshots/bookchaowalit-subdomains.json.

`--check` is offline and deterministic: it validates the registry and makes
sure reserved platform names do not collide with hostnames already bound in the
latest snapshot. Neither mode changes DNS or provider state.

Usage:
    python3 infra/scripts/platform/domains_registry.py --snapshot
    python3 infra/scripts/platform/domains_registry.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    sys.exit(1)


ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "infra" / "platform" / "domains.yaml"
SNAPSHOT_PATH = ROOT / "infra" / "platform" / "bookchaowalit-subdomains.json"

SCHEMA_VERSION = "solo-empire.domains.v1"
SNAPSHOT_SCHEMA = "solo-empire.domains-snapshot.v1"
STATUSES = {"live", "reserved", "planned-repoint", "retire"}
ACCESS = {"public", "public-token", "public-signed", "protected"}
REQUIRED_PLATFORM_FIELDS = ("host", "purpose", "xaas", "target", "access", "status", "phase")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_registry() -> dict[str, Any]:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}


def load_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOT_PATH.exists():
        return None
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def check_registry(registry: dict[str, Any], snapshot: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    zone = registry.get("zone", "")
    if registry.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not zone:
        errors.append("zone is required")
    forbidden = tuple(registry.get("naming", {}).get("forbidden_prefixes", []))
    bound = {item["host"]: item for item in (snapshot or {}).get("hosts", [])}
    seen: set[str] = set()
    for index, entry in enumerate(registry.get("platform", [])):
        label = entry.get("host", f"platform[{index}]")
        for field in REQUIRED_PLATFORM_FIELDS:
            if not entry.get(field):
                errors.append(f"{label}: missing {field}")
        host = entry.get("host", "")
        if host in seen:
            errors.append(f"{host}: duplicate host")
        seen.add(host)
        if zone and not host.endswith(f".{zone}"):
            errors.append(f"{host}: must be a subdomain of {zone}")
        if host.split(".")[0].startswith(forbidden):
            errors.append(f"{host}: uses a forbidden prefix")
        if entry.get("status") not in STATUSES:
            errors.append(f"{host}: unknown status {entry.get('status')!r}")
        if entry.get("access") not in ACCESS:
            errors.append(f"{host}: unknown access {entry.get('access')!r}")
        if entry.get("status") == "reserved" and host in bound:
            errors.append(
                f"{host}: reserved but already bound to Vercel project {bound[host]['project']}; "
                "mark it planned-repoint or pick another name"
            )
        if entry.get("status") == "planned-repoint" and not entry.get("current"):
            errors.append(f"{host}: planned-repoint needs a current field")
    for entry in registry.get("follow_up", []):
        if entry.get("status") not in STATUSES:
            errors.append(f"{entry.get('host')}: unknown follow-up status")
    if snapshot is None:
        errors.append(f"{rel(SNAPSHOT_PATH)} is missing; run `task domains:snapshot`")
    elif snapshot.get("zone") != zone:
        errors.append("snapshot zone does not match registry zone")
    return errors


def read_vercel_bindings(zone: str) -> list[tuple[str, str]]:
    vercel = shutil.which("vercel")
    if not vercel:
        raise RuntimeError("vercel CLI not found; install it and run `vercel login`")
    # Run outside the repository so the CLI never links or writes project config here.
    with tempfile.TemporaryDirectory() as workdir:
        result = subprocess.run(
            [vercel, "domains", "inspect", zone],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError("`vercel domains inspect` failed; check `vercel whoami`")
    output = result.stdout + result.stderr
    projects_section = output.split("Projects", 1)[-1]
    host_pattern = re.compile(rf"\b((?:[a-z0-9-]+\.)*{re.escape(zone)})\b")
    bindings: list[tuple[str, str]] = []
    for line in projects_section.splitlines():
        hosts = host_pattern.findall(line)
        if not hosts:
            continue
        project = line.split()[0]
        bindings.extend((host, project) for host in hosts)
    return sorted(set(bindings))


def probe(host: str) -> int:
    request = urllib.request.Request(f"https://{host}", method="GET", headers={"User-Agent": "solo-empire-domains/1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


def write_snapshot(registry: dict[str, Any]) -> dict[str, Any]:
    zone = registry["zone"]
    bindings = read_vercel_bindings(zone)
    with ThreadPoolExecutor(max_workers=24) as pool:
        codes = list(pool.map(probe, [host for host, _ in bindings]))
    hosts = [
        {"host": host, "project": project, "provider": "vercel", "http_status": code}
        for (host, project), code in zip(bindings, codes)
    ]
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "zone": zone,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "vercel domains inspect (read-only) + HTTPS GET probe",
        "summary": {
            "hosts": len(hosts),
            "projects": len({item["project"] for item in hosts}),
            "http_status": dict(sorted(Counter(str(item["http_status"]) for item in hosts).items())),
        },
        "hosts": hosts,
    }
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true", help="read Vercel bindings, probe them, write the snapshot")
    group.add_argument("--check", action="store_true", help="validate the registry against the latest snapshot")
    args = parser.parse_args()

    registry = load_registry()
    if args.snapshot:
        try:
            snapshot = write_snapshot(registry)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            print(f"  FAIL: {error}")
            return 1
        print(f"  OK: wrote {rel(SNAPSHOT_PATH)}")
        print(f"  OK: {snapshot['summary']}")
        return 0

    errors = check_registry(registry, load_snapshot())
    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1
    platform = registry.get("platform", [])
    print(f"  OK: {len(platform)} platform subdomains valid; no collision with the latest snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())

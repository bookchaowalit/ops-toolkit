#!/usr/bin/env python3
"""Offline contract and repository-shape check for a Book Platform scaffold."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "schema_version",
    "platform_id",
    "repository",
    "status",
    "api_version",
    "data_owner",
    "capabilities",
    "depends_on",
    "current_implementation",
    "migration",
}
SOURCE_STATUSES = {
    "current-implementation-in-solo-empire",
    "adjacent-domain-repository",
    "new-boundary",
    "implemented-in-this-repository",
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> int:
    contract_path = ROOT / "contract.json"
    readme_path = ROOT / "README.md"
    if not contract_path.is_file():
        fail("contract.json is missing")
    if not readme_path.is_file():
        fail("README.md is missing")
    if not (ROOT / "scripts" / "check.sh").is_file():
        fail("scripts/check.sh is missing")

    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"contract.json is invalid JSON: {exc}")
    if not isinstance(contract, dict):
        fail("contract.json root must be an object")
    missing = sorted(REQUIRED - set(contract))
    if missing:
        fail(f"contract.json missing keys: {missing}")
    if contract["schema_version"] != "book-platform.contract.v1":
        fail("unsupported schema_version")
    if contract["status"] != "scaffolded":
        fail("scaffold status must remain scaffolded until activation evidence exists")
    for key in ("platform_id", "repository", "api_version", "data_owner"):
        if not isinstance(contract[key], str) or not contract[key].strip():
            fail(f"{key} must be a non-empty string")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", contract["platform_id"]):
        fail("platform_id has an invalid slug")
    if not re.fullmatch(r"platform-[a-z0-9][a-z0-9-]{1,62}", contract["repository"]):
        fail("repository has an invalid slug")
    for key in ("capabilities", "depends_on"):
        if not isinstance(contract[key], list) or not all(
            isinstance(item, str) and item.strip() for item in contract[key]
        ):
            fail(f"{key} must be a list of non-empty strings")
    implementation = contract["current_implementation"]
    if not isinstance(implementation, dict):
        fail("current_implementation must be an object")
    source_status = implementation.get("source_status", "current-implementation-in-solo-empire")
    if source_status not in SOURCE_STATUSES:
        fail(f"current_implementation.source_status {source_status!r} is unsupported")
    expected_repository = (
        contract["repository"] if source_status == "implemented-in-this-repository" else "solo-empire"
    )
    if implementation.get("repository") != expected_repository:
        fail(f"current_implementation.repository must be {expected_repository}")
    source_path = implementation.get("source_path")
    source_paths = implementation.get("source_paths", [] if source_path == "none" else [source_path])
    if not isinstance(source_paths, list) or not all(isinstance(item, str) and item.strip() for item in source_paths):
        fail("current_implementation.source_paths must be a list of non-empty strings")
    if len(set(source_paths)) != len(source_paths):
        fail("current_implementation.source_paths must not contain duplicates")
    if any(item.startswith("/") or ".." in item.split("/") for item in source_paths):
        fail("current_implementation.source_paths must be repository-relative")
    if source_path != (source_paths[0] if source_paths else "none"):
        fail("current_implementation.source_path must be the first source_paths entry or none")
    if (source_status == "new-boundary") != (not source_paths):
        fail("current_implementation.source_paths must be empty exactly when source_status is new-boundary")
    interfaces = contract.get("interfaces", [])
    if not isinstance(interfaces, list):
        fail("interfaces must be a list")
    seen: set[str] = set()
    for index, interface in enumerate(interfaces):
        if not isinstance(interface, dict):
            fail(f"interfaces[{index}] must be an object")
        for key in ("id", "kind", "direction", "name", "status", "source"):
            if not isinstance(interface.get(key), str) or not interface[key].strip():
                fail(f"interfaces[{index}].{key} must be a non-empty string")
        if not interface["id"].startswith(f"{contract['platform_id']}.") or interface["id"] in seen:
            fail(f"interfaces[{index}].id must be unique and start with {contract['platform_id']}.")
        seen.add(interface["id"])
        if interface["direction"] not in {"provides", "consumes"}:
            fail(f"interfaces[{index}].direction must be provides or consumes")
    migration = contract["migration"]
    if not isinstance(migration, dict):
        fail("migration must be an object")
    for key in ("compatibility_adapter", "shadow_parity", "cutover", "rollback"):
        if not isinstance(migration.get(key), str) or not migration[key].strip():
            fail(f"migration.{key} must be a non-empty string")

    readme = readme_path.read_text(encoding="utf-8")
    for heading in ("## Scope", "## Boundary", "## Local verification", "## Migration gate"):
        if heading not in readme:
            fail(f"README.md is missing {heading}")
    if "__" in readme or "__" in contract_path.read_text(encoding="utf-8"):
        fail("scaffold placeholder remains")
    print(f"OK: {contract['repository']} ({contract['platform_id']}) scaffold contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

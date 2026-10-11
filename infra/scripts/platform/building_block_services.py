#!/usr/bin/env python3
"""Validate and query the shared building-block services catalog.

The catalog decides, for every horizontal capability a Solo Empire app may
need, whether it is built centrally, bought from a provider, or hybrid, and how
apps reach it. This tool is read-only: it never calls a provider or reads a
secret value.

Usage:
    building_block_services.py check
    building_block_services.py summary [--json]
    building_block_services.py list [--category C] [--decision D] [--status S] [--phase P] [--json]
    building_block_services.py show <service-id> [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import repo_file, sibling_ref_file  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = PROJECT_ROOT / "infra" / "platform" / "building-block-services.yaml"
CLOUD_PROVIDERS_PATH = PROJECT_ROOT / "infra" / "platform" / "cloud-providers.yaml"
# Canonical owner: bookchaowalit/solo-integrations (sibling checkout; override SOLO_INTEGRATIONS_ROOT).
EXTERNAL_APPS_REF = "bookchaowalit/solo-integrations:data/external_app_registry.ts"
EXTERNAL_APPS_PATH = sibling_ref_file(EXTERNAL_APPS_REF)

SCHEMA_VERSION = "solo-empire.building-block-services.v1"
DECISIONS = ("build", "hybrid", "buy")
STATUSES = ("live", "partial", "planned", "deferred")
PHASES = ("now", "next", "later", "on_demand")
ACCESS_MODES = frozenset({"gateway", "sdk", "edge", "platform", "library"})
DATA_CLASSES = frozenset({"public", "internal", "personal", "sensitive", "regulated"})
REQUIRED_FIELDS = ("id", "category", "name", "decision", "difficulty", "primary", "alternatives")
ALLOWED_FIELDS = frozenset(
    REQUIRED_FIELDS + ("refs", "status", "phase", "data_class", "approval", "contract", "impl", "note")
)
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CONTRACT_PATTERN = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /[\w/{}:-]+$")
EXTERNAL_ID_PATTERN = re.compile(r"\bid:\s*'([a-z0-9-]+)'")


class CatalogError(ValueError):
    """Raised when the building-block catalog violates its contract."""


def known_provider_refs() -> set[str] | None:
    """Provider and external-app ids, or None when the solo-integrations checkout is absent.

    CI checks out only this repository, so a missing sibling skips the ref check with a warning.
    """
    if not EXTERNAL_APPS_PATH.is_file():
        print(
            f"  WARN: {EXTERNAL_APPS_REF} not found at {EXTERNAL_APPS_PATH}; "
            "skipping refs check (set SOLO_INTEGRATIONS_ROOT)",
            file=sys.stderr,
        )
        return None
    refs: set[str] = set()
    providers = yaml.safe_load(CLOUD_PROVIDERS_PATH.read_text(encoding="utf-8")) or {}
    refs.update(p["id"] for p in providers.get("providers", []) if isinstance(p, dict) and "id" in p)
    refs.update(EXTERNAL_ID_PATTERN.findall(EXTERNAL_APPS_PATH.read_text(encoding="utf-8")))
    return refs


def resolve(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Return services with catalog defaults applied."""
    defaults = catalog.get("defaults") or {}
    return [{**defaults, "refs": [], "impl": [], **service} for service in catalog.get("services") or []]


def validate(catalog: Any, provider_refs: set[str] | None = None, root: Path = PROJECT_ROOT) -> list[str]:
    errors: list[str] = []
    if not isinstance(catalog, dict):
        return ["catalog must be a mapping"]
    if catalog.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if catalog.get("deployment_enabled") is not False:
        errors.append("deployment_enabled must be false; the catalog is a contract, not a deployer")
    policy = catalog.get("policy") or {}
    if policy.get("apps_embed_provider_secrets") is not False:
        errors.append("policy.apps_embed_provider_secrets must be false")

    categories: dict[str, dict[str, Any]] = {}
    for category in catalog.get("categories") or []:
        cid = category.get("id")
        if not isinstance(cid, str) or not ID_PATTERN.fullmatch(cid):
            errors.append(f"invalid category id: {cid!r}")
            continue
        if cid in categories:
            errors.append(f"duplicate category id: {cid}")
        if category.get("access") not in ACCESS_MODES:
            errors.append(f"category {cid}: unknown access mode {category.get('access')!r}")
        gateway = category.get("gateway")
        if gateway is not None and not (isinstance(gateway, str) and gateway.startswith("/api/")):
            errors.append(f"category {cid}: gateway must be null or an /api/ prefix")
        categories[cid] = category

    services = resolve(catalog)
    if not services:
        errors.append("services must not be empty")
    seen: set[str] = set()
    used_categories: set[str] = set()
    for service in services:
        sid = service.get("id")
        label = f"service {sid!r}"
        for field in REQUIRED_FIELDS:
            if field not in service:
                errors.append(f"{label}: missing {field}")
        unknown = set(service) - ALLOWED_FIELDS
        if unknown:
            errors.append(f"{label}: unknown fields {sorted(unknown)}")
        if not isinstance(sid, str) or not ID_PATTERN.fullmatch(sid):
            errors.append(f"{label}: id must be kebab-case")
        elif sid in seen:
            errors.append(f"duplicate service id: {sid}")
        else:
            seen.add(sid)

        category = service.get("category")
        if category not in categories:
            errors.append(f"{label}: unknown category {category!r}")
        used_categories.add(category)

        decision = service.get("decision")
        difficulty = service.get("difficulty")
        if decision not in DECISIONS:
            errors.append(f"{label}: decision must be one of {DECISIONS}")
        if not isinstance(difficulty, int) or isinstance(difficulty, bool) or not 1 <= difficulty <= 5:
            errors.append(f"{label}: difficulty must be an integer 1-5")
        elif decision == "build" and difficulty >= 4:
            errors.append(f"{label}: difficulty {difficulty} exceeds the build ceiling; use hybrid or buy")
        elif difficulty == 5 and decision != "buy":
            errors.append(f"{label}: difficulty 5 must be bought")

        if not isinstance(service.get("primary"), str) or not service["primary"].strip():
            errors.append(f"{label}: primary provider or owner is required")
        if not isinstance(service.get("alternatives"), list):
            errors.append(f"{label}: alternatives must be a list")
        if service.get("status") not in STATUSES:
            errors.append(f"{label}: status must be one of {STATUSES}")
        if service.get("phase") not in PHASES:
            errors.append(f"{label}: phase must be one of {PHASES}")
        data_class = service.get("data_class")
        if data_class not in DATA_CLASSES:
            errors.append(f"{label}: unknown data_class {data_class!r}")
        if not isinstance(service.get("approval"), bool):
            errors.append(f"{label}: approval must be a boolean")
        elif data_class == "regulated" and not service["approval"]:
            errors.append(f"{label}: regulated data requires approval: true")

        refs = service.get("refs")
        if not isinstance(refs, list):
            errors.append(f"{label}: refs must be a list")
        elif provider_refs is not None:
            for ref in refs:
                if ref not in provider_refs:
                    errors.append(f"{label}: ref {ref!r} is not in cloud-providers.yaml or solo-integrations external_app_registry.ts")

        contract = service.get("contract")
        if contract is not None and not (isinstance(contract, str) and CONTRACT_PATTERN.fullmatch(contract)):
            errors.append(f"{label}: contract must look like 'POST /api/...'")

        impl = service.get("impl")
        if not isinstance(impl, list):
            errors.append(f"{label}: impl must be a list of repository paths")
        else:
            for path in impl:
                # CI checks out only this repository; nested Book Dev repositories are absent there.
                nested = Path(path).parts[:4] == ("projects", "product", "engineering", "book-dev")
                if nested and root == PROJECT_ROOT:
                    # <github>/<owner>/<visibility>/<repo>/...: verify only when that clone is present.
                    target = repo_file(path)
                    checkout = repo_file(Path(*Path(path).parts[:8]))
                    if not checkout.is_dir():
                        continue
                elif nested:
                    continue
                else:
                    target = root / path
                if not target.exists():
                    errors.append(f"{label}: impl path does not exist: {path}")
        if service.get("status") in {"live", "partial"} and not impl and service.get("category") != "devinfra":
            errors.append(f"{label}: status {service.get('status')} needs at least one impl path")

    unused = set(categories) - used_categories
    if unused:
        errors.append(f"categories without services: {sorted(unused)}")
    return errors


def load_catalog(path: Path = CATALOG_PATH, check: bool = True) -> dict[str, Any]:
    catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
    if check:
        errors = validate(catalog, known_provider_refs())
        if errors:
            raise CatalogError("\n".join(errors))
    return catalog


def summarize(catalog: dict[str, Any]) -> dict[str, Any]:
    services = resolve(catalog)
    by_category: dict[str, dict[str, int]] = {}
    for category in catalog["categories"]:
        members = [s for s in services if s["category"] == category["id"]]
        by_category[category["id"]] = {
            "name": category["name"],
            "access": category["access"],
            "gateway": category.get("gateway"),
            "services": len(members),
            **{decision: sum(1 for s in members if s["decision"] == decision) for decision in DECISIONS},
        }
    return {
        "schema_version": catalog["schema_version"],
        "total_services": len(services),
        "decision": dict(Counter(s["decision"] for s in services)),
        "status": dict(Counter(s["status"] for s in services)),
        "phase": dict(Counter(s["phase"] for s in services)),
        "approval_required": sum(1 for s in services if s["approval"]),
        "categories": by_category,
        "adopt_now": [s["id"] for s in services if s["phase"] == "now"],
    }


def filter_services(catalog: dict[str, Any], **criteria: str | None) -> list[dict[str, Any]]:
    services = resolve(catalog)
    for key, value in criteria.items():
        if value:
            services = [s for s in services if s.get(key) == value]
    return services


def _print_table(services: list[dict[str, Any]]) -> None:
    marks = {1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴", 5: "🔴🔴"}
    for s in services:
        print(
            f"{s['category']:<14} {s['id']:<24} {s['decision']:<7} {marks[s['difficulty']]:<3} "
            f"{s['status']:<8} {s['phase']:<9} {s['primary']}"
        )
    print(f"\n{len(services)} services")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    p_summary = sub.add_parser("summary")
    p_summary.add_argument("--json", action="store_true")
    p_list = sub.add_parser("list")
    p_list.add_argument("--category")
    p_list.add_argument("--decision", choices=DECISIONS)
    p_list.add_argument("--status", choices=STATUSES)
    p_list.add_argument("--phase", choices=PHASES)
    p_list.add_argument("--json", action="store_true")
    p_show = sub.add_parser("show")
    p_show.add_argument("service_id")
    p_show.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        catalog = load_catalog()
    except CatalogError as error:
        print(f"building-block catalog invalid:\n{error}", file=sys.stderr)
        return 1

    if args.command == "check":
        print(f"building-block catalog ok: {len(catalog['services'])} services, {len(catalog['categories'])} categories")
        return 0
    if args.command == "summary":
        summary = summarize(catalog)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(f"{summary['total_services']} services | decision {summary['decision']} | status {summary['status']}")
            for cid, row in summary["categories"].items():
                print(f"  {cid:<14} {row['services']:>3}  build {row['build']:>2}  hybrid {row['hybrid']:>2}  buy {row['buy']:>2}  via {row['access']}")
            print(f"adopt now ({len(summary['adopt_now'])}): {', '.join(summary['adopt_now'])}")
        return 0
    if args.command == "list":
        services = filter_services(
            catalog, category=args.category, decision=args.decision, status=args.status, phase=args.phase
        )
        if args.json:
            print(json.dumps(services, ensure_ascii=False, indent=2))
        else:
            _print_table(services)
        return 0
    matches = filter_services(catalog, id=args.service_id)
    if not matches:
        print(f"unknown service: {args.service_id}", file=sys.stderr)
        return 1
    print(json.dumps(matches[0], ensure_ascii=False, indent=2) if args.json else yaml.safe_dump(matches[0], allow_unicode=True, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

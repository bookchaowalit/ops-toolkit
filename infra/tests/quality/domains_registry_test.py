#!/usr/bin/env python3
"""Tests for the bookchaowalit.com subdomain registry guard."""

from __future__ import annotations

import copy
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "infra" / "scripts" / "platform"))
import domains_registry


def registry() -> dict:
    return domains_registry.load_registry()


def snapshot() -> dict:
    return {"zone": "bookchaowalit.com", "hosts": [{"host": "recipe.bookchaowalit.com", "project": "recipe-frontend"}]}


def test_committed_registry_is_valid() -> None:
    assert domains_registry.check_registry(registry(), domains_registry.load_snapshot()) == []


def test_reserved_host_collision_is_rejected() -> None:
    data = copy.deepcopy(registry())
    data["platform"][0]["host"] = "recipe.bookchaowalit.com"
    # The collision rule applies to reserved names only; do not depend on which status the first entry has today.
    data["platform"][0]["status"] = "reserved"
    errors = domains_registry.check_registry(data, snapshot())
    assert any("already bound" in error for error in errors)


def test_host_outside_zone_is_rejected() -> None:
    data = copy.deepcopy(registry())
    data["platform"][0]["host"] = "api.example.com"
    assert any("must be a subdomain" in error for error in domains_registry.check_registry(data, snapshot()))


def test_forbidden_prefix_is_rejected() -> None:
    data = copy.deepcopy(registry())
    data["platform"][0]["host"] = "test-api.bookchaowalit.com"
    assert any("forbidden prefix" in error for error in domains_registry.check_registry(data, snapshot()))


def test_missing_snapshot_is_reported() -> None:
    assert any("missing" in error for error in domains_registry.check_registry(registry(), None))


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_"):
            func()
            print(f"  OK: {name}")

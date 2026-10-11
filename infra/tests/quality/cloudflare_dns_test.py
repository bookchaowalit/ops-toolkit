#!/usr/bin/env python3
"""Offline tests for the declarative Cloudflare DNS planner."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "infra" / "scripts" / "platform"))
import cloudflare_dns


REGISTRY = {
    "zone": "bookchaowalit.com",
    "platform": [
        {
            "host": "api.bookchaowalit.com",
            "purpose": "api",
            "dns": [
                {"type": "CNAME", "content": "x.up.railway.app"},
                {"type": "TXT", "name": "_verify.api.bookchaowalit.com", "content": "v=1"},
            ],
        },
        {"host": "hub.bookchaowalit.com", "purpose": "hub"},
    ],
}


def test_only_entries_with_dns_are_desired() -> None:
    names = [record["name"] for record in cloudflare_dns.desired_records(REGISTRY)]
    assert names == ["api.bookchaowalit.com", "_verify.api.bookchaowalit.com"]


def test_plan_creates_updates_and_noops() -> None:
    desired = cloudflare_dns.desired_records(REGISTRY)
    existing = [{"id": "1", "name": "_verify.api.bookchaowalit.com", "type": "TXT", "content": "v=1", "proxied": False}]
    actions = {record["name"]: action for action, record, _ in cloudflare_dns.plan(desired, existing)}
    assert actions == {"api.bookchaowalit.com": "create", "_verify.api.bookchaowalit.com": "noop"}
    existing.append({"id": "2", "name": "api.bookchaowalit.com", "type": "CNAME", "content": "old", "proxied": True})
    actions = {record["name"]: action for action, record, _ in cloudflare_dns.plan(desired, existing)}
    assert actions["api.bookchaowalit.com"] == "update"


def test_type_mismatch_is_a_conflict_not_an_overwrite() -> None:
    desired = cloudflare_dns.desired_records(REGISTRY)
    existing = [{"id": "3", "name": "api.bookchaowalit.com", "type": "A", "content": "1.2.3.4", "proxied": True}]
    actions = {record["name"]: action for action, record, _ in cloudflare_dns.plan(desired, existing)}
    assert actions["api.bookchaowalit.com"] == "conflict"


def test_record_outside_zone_is_rejected() -> None:
    bad = {"zone": "bookchaowalit.com", "platform": [{"host": "a.bookchaowalit.com", "purpose": "a", "dns": {"type": "CNAME", "name": "a.example.com", "content": "x"}}]}
    try:
        cloudflare_dns.desired_records(bad)
    except ValueError:
        return
    raise AssertionError("record outside the zone was accepted")


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_"):
            func()
            print(f"  OK: {name}")

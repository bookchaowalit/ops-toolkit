#!/usr/bin/env python3
"""Offline tests for workspace-local Matrix target bindings."""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "templates", "architecture-docs checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from matrix_targets import MatrixTargetError, build_plan, google_sheet_create_plan, link_target  # noqa: E402
from matrix_sync import load_yaml  # noqa: E402


MAPPING = ARCHITECTURE_ROOT / "templates" / "matrices" / "sync-mappings.yaml"


def test_plan_is_provider_neutral():
    mapping = load_yaml(MAPPING)
    payload = build_plan(mapping, ["google-sheets", "microsoft-excel", "notion"])
    assert payload["dry_run"] is True
    assert len(payload["targets"]) == 3
    assert all("link_command" in item for item in payload["targets"])


def test_link_keeps_binding_local_and_can_enable():
    mapping = copy.deepcopy(load_yaml(MAPPING))
    with tempfile.TemporaryDirectory():
        result = link_target(
            mapping,
            "google-sheets",
            "https://docs.google.com/spreadsheets/d/sheet-123/edit",
            None,
            True,
        )
    assert result["resource_id"] == "sheet-123"
    target = next(item for item in mapping["targets"] if item["id"] == "google-sheets")
    assert target["resource_id"] == "sheet-123"
    assert target["resource_url"].endswith("/edit")
    assert target["enabled"] is True


def test_link_requires_https():
    mapping = copy.deepcopy(load_yaml(MAPPING))
    try:
        link_target(mapping, "google-sheets", "http://example.test/sheet", "sheet-123", False)
    except MatrixTargetError as exc:
        assert str(exc) == "resource_url_must_use_https"
    else:
        raise AssertionError("insecure URL was accepted")


def test_google_create_plan_is_explicit():
    mapping = load_yaml(MAPPING)
    target = next(item for item in mapping["targets"] if item["id"] == "google-sheets")
    plan = google_sheet_create_plan(target, "Demo Matrix")
    assert plan["operation"] == "create-and-bind"
    assert plan["write_requires"] == "--apply"
    assert "seed the canonical CSV header" in plan["steps"]


if __name__ == "__main__":
    for test in (test_plan_is_provider_neutral, test_link_keeps_binding_local_and_can_enable, test_link_requires_https, test_google_create_plan_is_explicit):
        test()
        print(f"✅ {test.__name__}")

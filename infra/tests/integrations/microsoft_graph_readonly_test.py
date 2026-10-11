#!/usr/bin/env python3
"""Offline tests for the Microsoft Graph read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from microsoft_graph_readonly import normalize_profile, run  # noqa: E402


def test_normalize_profile_keeps_only_safe_metadata():
    normalized = normalize_profile({
        "id": "user-123",
        "displayName": "Solo Empire",
        "mail": "private@example.com",
        "businessPhones": ["+6612345678"],
        "mailboxSettings": {"timeZone": "private"},
    })
    assert normalized == {"id": "user-123", "displayName": "Solo Empire"}
    assert "private@example.com" not in json.dumps(normalized)
    assert "private" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        payload = run("me", offline=True)
    assert payload["service"] == "microsoft-graph"
    assert payload["status"] == "offline"
    assert payload["profile"] == {}


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run("me", offline=False)
    assert payload["service"] == "microsoft-graph"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["MICROSOFT_GRAPH_ACCESS_TOKEN"]


def test_provider_metadata_drops_unselected_fields():
    fake_response = {
        "id": "user-123",
        "displayName": "Solo Empire",
        "mail": "private@example.com",
        "messages": [{"subject": "private message"}],
    }
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "env",
        "MICROSOFT_GRAPH_ACCESS_TOKEN": "access-token",
    }, clear=True), patch("microsoft_graph_readonly.call_graph", return_value=fake_response):
        payload = run("me", offline=False)
    assert payload["status"] == "healthy"
    assert payload["profile"] == {"id": "user-123", "displayName": "Solo Empire"}
    assert "private@example.com" not in json.dumps(payload)
    assert "private message" not in json.dumps(payload)


if __name__ == "__main__":
    for test in (
        test_normalize_profile_keeps_only_safe_metadata,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
        test_provider_metadata_drops_unselected_fields,
    ):
        test()
        print(f"✅ {test.__name__}")

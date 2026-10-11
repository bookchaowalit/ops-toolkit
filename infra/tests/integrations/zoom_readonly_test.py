#!/usr/bin/env python3
"""Offline tests for the Zoom read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from zoom_readonly import normalize_profile, run  # noqa: E402


def test_normalize_profile_keeps_safe_summary_only():
    normalized = normalize_profile({
        "id": "zoom-user-123",
        "display_name": "Solo Empire",
        "status": "active",
        "email": "private@example.com",
        "account_id": "private-account",
        "role_id": "private-role",
    })
    assert normalized == {
        "id": "zoom-user-123",
        "display_name": "Solo Empire",
        "status": "active",
    }
    assert "private@example.com" not in json.dumps(normalized)
    assert "private-account" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        payload = run("me", offline=True)
    assert payload["service"] == "zoom"
    assert payload["status"] == "offline"
    assert payload["profile"] == {}


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run("me", offline=False)
    assert payload["service"] == "zoom"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["ZOOM_ACCESS_TOKEN"]


def test_provider_metadata_drops_sensitive_fields():
    fake_response = {
        "id": "zoom-user-123",
        "display_name": "Solo Empire",
        "status": "active",
        "email": "private@example.com",
        "account_id": "private-account",
        "meetings": [{"topic": "private meeting"}],
    }
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "env",
        "ZOOM_ACCESS_TOKEN": "access-token",
    }, clear=True), patch("zoom_readonly.call_zoom", return_value=fake_response):
        payload = run("me", offline=False)
    assert payload["status"] == "healthy"
    assert payload["profile"] == {
        "id": "zoom-user-123",
        "display_name": "Solo Empire",
        "status": "active",
    }
    assert "private@example.com" not in json.dumps(payload)
    assert "private meeting" not in json.dumps(payload)


if __name__ == "__main__":
    for test in (
        test_normalize_profile_keeps_safe_summary_only,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
        test_provider_metadata_drops_sensitive_fields,
    ):
        test()
        print(f"✅ {test.__name__}")

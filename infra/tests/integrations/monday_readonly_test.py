#!/usr/bin/env python3
"""Offline tests for the monday.com read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from monday_readonly import authorization_header, normalize_board, run  # noqa: E402


def test_authorization_header_uses_monday_raw_token_format():
    assert authorization_header("api-token") == "api-token"
    assert authorization_header("Bearer api-token") == "api-token"


def test_normalize_board_keeps_metadata_only():
    normalized = normalize_board({
        "id": 1234567890,
        "name": "Solo Empire",
        "state": "active",
        "items": [{"name": "private item"}],
        "columns": [{"title": "private column"}],
        "description": "private description",
    })
    assert normalized == {
        "id": "1234567890",
        "name": "Solo Empire",
        "state": "active",
    }
    assert "private item" not in json.dumps(normalized)
    assert "private column" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        payload = run("boards", 20, offline=True)
    assert payload["service"] == "monday"
    assert payload["status"] == "offline"
    assert payload["boards"] == []
    assert payload["limit"] == 20


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run("boards", 20, offline=False)
    assert payload["service"] == "monday"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["MONDAY_API_TOKEN"]


def test_provider_metadata_drops_nested_content():
    fake_response = {
        "data": {
            "boards": [{
                "id": 1234567890,
                "name": "Solo Empire",
                "state": "active",
                "items": [{"name": "private item"}],
            }],
        },
    }
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "env",
        "MONDAY_API_TOKEN": "api-token",
    }, clear=True), patch("monday_readonly.call_monday", return_value=fake_response):
        payload = run("boards", 20, offline=False)
    assert payload["status"] == "healthy"
    assert payload["boards"] == [{
        "id": "1234567890",
        "name": "Solo Empire",
        "state": "active",
    }]
    assert "private item" not in json.dumps(payload)


if __name__ == "__main__":
    for test in (
        test_authorization_header_uses_monday_raw_token_format,
        test_normalize_board_keeps_metadata_only,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
        test_provider_metadata_drops_nested_content,
    ):
        test()
        print(f"✅ {test.__name__}")

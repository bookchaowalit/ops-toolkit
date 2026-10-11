#!/usr/bin/env python3
"""Offline tests for the WhatsApp Business read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from whatsapp_business_readonly import normalize_phone_number, run  # noqa: E402


def test_normalize_phone_number_masks_full_number_and_drops_content():
    normalized = normalize_phone_number({
        "id": "phone-123",
        "verified_name": "Solo Empire",
        "display_phone_number": "+66812345678",
        "quality_rating": "GREEN",
        "messages": [{"text": "private message"}],
    })
    assert normalized == {
        "id": "phone-123",
        "verified_name": "Solo Empire",
        "display_phone_number_masked": "+•••5678",
        "quality_rating": "GREEN",
    }
    assert "+66812345678" not in json.dumps(normalized)
    assert "private message" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        payload = run("phone-numbers", 10, "", offline=True)
    assert payload["service"] == "whatsapp-business"
    assert payload["status"] == "offline"
    assert payload["phone_numbers"] == []


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run("phone-numbers", 10, "", offline=False)
    assert payload["service"] == "whatsapp-business"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == [
        "WHATSAPP_CLOUD_API_TOKEN",
        "WHATSAPP_BUSINESS_ACCOUNT_ID",
    ]


def test_provider_metadata_is_bounded_and_redacted():
    fake_response = {
        "data": [{
            "id": "phone-123",
            "verified_name": "Solo Empire",
            "display_phone_number": "+66812345678",
            "quality_rating": "GREEN",
            "messages": [{"text": "private message"}],
        }],
        "paging": {"next": "https://example.invalid/next"},
    }
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "env",
        "WHATSAPP_CLOUD_API_TOKEN": "access-token",
        "WHATSAPP_GRAPH_API_VERSION": "v25.0",
    }, clear=True), patch("whatsapp_business_readonly.call_graph", return_value=fake_response):
        payload = run("phone-numbers", 10, "waba-123", offline=False)
    assert payload["status"] == "healthy"
    assert payload["phone_numbers"][0]["display_phone_number_masked"] == "+•••5678"
    assert payload["has_more"] is True
    assert "+66812345678" not in json.dumps(payload)
    assert "private message" not in json.dumps(payload)


if __name__ == "__main__":
    for test in (
        test_normalize_phone_number_masks_full_number_and_drops_content,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
        test_provider_metadata_is_bounded_and_redacted,
    ):
        test()
        print(f"✅ {test.__name__}")

#!/usr/bin/env python3
"""Offline tests for the Slack read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from slack_readonly import normalize_channel, run  # noqa: E402


def test_normalize_channel_drops_topics_and_purpose():
    normalized = normalize_channel({
        "id": "C123",
        "name": "general",
        "is_channel": True,
        "is_private": False,
        "is_archived": False,
        "is_member": True,
        "topic": {"value": "private project details"},
        "purpose": {"value": "private purpose"},
        "num_members": 4,
    })
    assert normalized["id"] == "C123"
    assert normalized["name"] == "general"
    assert normalized["num_members"] == 4
    assert "topic" not in normalized
    assert "purpose" not in normalized
    assert "private project" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        payload = run(10, offline=True)
    assert payload["service"] == "slack"
    assert payload["status"] == "offline"
    assert payload["channels"] == []


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run(10, offline=False)
    assert payload["service"] == "slack"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["SLACK_BOT_TOKEN"]
    assert "slack-bot-token" not in json.dumps(payload).lower()


if __name__ == "__main__":
    for test in (
        test_normalize_channel_drops_topics_and_purpose,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
    ):
        test()
        print(f"✅ {test.__name__}")

#!/usr/bin/env python3
"""Offline tests for the Discord read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from discord_readonly import normalize_guild, run  # noqa: E402


def test_normalize_guild_drops_sensitive_and_content_fields():
    normalized = normalize_guild({
        "id": "guild-123",
        "name": "Solo Empire",
        "icon": "icon-hash",
        "owner": True,
        "features": ["COMMUNITY", "NEWS"],
        "permissions": "8",
        "members": [{"user": {"id": "private-member"}}],
        "channels": [{"name": "private-channel"}],
    })
    assert normalized == {
        "id": "guild-123",
        "name": "Solo Empire",
        "icon": "icon-hash",
        "owner": True,
        "features": ["COMMUNITY", "NEWS"],
    }
    assert "private-member" not in json.dumps(normalized)
    assert "private-channel" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        payload = run("guilds", 10, offline=True)
    assert payload["service"] == "discord"
    assert payload["status"] == "offline"
    assert payload["guilds"] == []


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run("guilds", 10, offline=False)
    assert payload["service"] == "discord"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["DISCORD_BOT_TOKEN"]
    assert "discord-token" not in json.dumps(payload).lower()


if __name__ == "__main__":
    for test in (
        test_normalize_guild_drops_sensitive_and_content_fields,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
    ):
        test()
        print(f"✅ {test.__name__}")

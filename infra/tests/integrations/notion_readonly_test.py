#!/usr/bin/env python3
"""Offline tests for the Notion read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from notion_readonly import normalize_result, run  # noqa: E402


def test_normalize_result_drops_properties_and_user_details():
    normalized = normalize_result({
        "object": "page",
        "id": "page-123",
        "created_time": "2026-08-08T00:00:00.000Z",
        "last_edited_time": "2026-08-08T01:00:00.000Z",
        "url": "https://www.notion.so/page-123",
        "properties": {
            "Name": {
                "type": "title",
                "title": [{"plain_text": "Project index"}],
            },
            "Private": {"type": "rich_text", "rich_text": [{"plain_text": "private content"}]},
        },
        "created_by": {"id": "person-secret"},
        "last_edited_by": {"id": "person-secret"},
        "parent": {"type": "database_id", "database_id": "db-123"},
    })
    assert normalized["id"] == "page-123"
    assert normalized["title"] == "Project index"
    assert normalized["parent"] == {"type": "database_id", "database_id": "db-123"}
    assert "properties" not in normalized
    assert "created_by" not in normalized
    assert "private content" not in json.dumps(normalized)
    assert "person-secret" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        payload = run(10, None, offline=True)
    assert payload["service"] == "notion"
    assert payload["status"] == "offline"
    assert payload["results"] == []


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run(10, None, offline=False)
    assert payload["service"] == "notion"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["NOTION_BOT_TOKEN"]
    assert "notion-bot-token" not in json.dumps(payload).lower()


if __name__ == "__main__":
    for test in (
        test_normalize_result_drops_properties_and_user_details,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
    ):
        test()
        print(f"✅ {test.__name__}")

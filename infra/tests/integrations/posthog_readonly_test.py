#!/usr/bin/env python3
"""Offline tests for the PostHog read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from posthog_readonly import normalize_insight, run  # noqa: E402


def test_normalize_insight_drops_private_query_payload():
    normalized = normalize_insight({
        "id": 42,
        "short_id": "T-42",
        "name": "Weekly activation",
        "query": {"source": {"series": [{"event": "$pageview"}]}},
        "result": [{"count": 999}],
        "hogql": "select * from events",
        "description": "token=should-not-leak",
        "tags": ["growth", "analytics"],
        "created_at": "2026-08-08T00:00:00Z",
    })

    assert normalized["id"] == 42
    assert normalized["name"] == "Weekly activation"
    assert normalized["description"] == "token=[REDACTED]"
    assert normalized["tags"] == ["growth", "analytics"]
    assert "query" not in normalized
    assert "result" not in normalized
    assert "hogql" not in normalized
    assert "$pageview" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        payload = run(10, offline=True)
    assert payload["service"] == "posthog"
    assert payload["status"] == "offline"
    assert payload["insights"] == []


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run(10, offline=False)
    assert payload["service"] == "posthog"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["POSTHOG_API_KEY", "POSTHOG_PROJECT_ID"]
    assert "API key" not in json.dumps(payload)


if __name__ == "__main__":
    for test in (
        test_normalize_insight_drops_private_query_payload,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
    ):
        test()
        print(f"✅ {test.__name__}")

#!/usr/bin/env python3
"""Offline tests for the Linear read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from linear_readonly import normalize_project, normalize_team, run  # noqa: E402


def test_normalize_team_drops_extra_fields():
    normalized = normalize_team({
        "id": "team-123",
        "name": "Engineering",
        "key": "ENG",
        "private_description": "private details",
    })
    assert normalized == {"id": "team-123", "name": "Engineering", "key": "ENG"}
    assert "private details" not in json.dumps(normalized)


def test_normalize_project_is_metadata_only():
    normalized = normalize_project({
        "id": "project-123",
        "name": "Roadmap",
        "description": "private issue context",
        "issues": [{"id": "issue-123", "title": "private issue"}],
    })
    assert normalized == {"id": "project-123", "name": "Roadmap"}
    assert "private issue" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        teams = run("teams", 10, offline=True)
        projects = run("projects", 10, offline=True)
    assert teams["service"] == "linear"
    assert teams["status"] == "offline"
    assert teams["teams"] == []
    assert projects["projects"] == []


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run("teams", 10, offline=False)
    assert payload["service"] == "linear"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["LINEAR_API_KEY"]
    assert "linear-api-key" not in json.dumps(payload).lower()


if __name__ == "__main__":
    for test in (
        test_normalize_team_drops_extra_fields,
        test_normalize_project_is_metadata_only,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
    ):
        test()
        print(f"✅ {test.__name__}")

#!/usr/bin/env python3
"""Offline tests for the Asana read-only adapter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from asana_readonly import normalize_project, normalize_workspace, run  # noqa: E402


def test_normalize_workspace_drops_sensitive_fields():
    normalized = normalize_workspace({
        "gid": "workspace-123",
        "resource_type": "workspace",
        "name": "Solo Empire",
        "email_domains": ["private.example"],
        "is_organization": True,
    })
    assert normalized["gid"] == "workspace-123"
    assert normalized["name"] == "Solo Empire"
    assert normalized["is_organization"] is True
    assert "email_domains" not in normalized
    assert "private.example" not in json.dumps(normalized)


def test_normalize_project_is_compact_metadata_only():
    normalized = normalize_project({
        "gid": "project-123",
        "resource_type": "project",
        "name": "Roadmap",
        "permalink_url": "https://app.asana.com/0/project-123",
        "archived": False,
        "custom_fields": [{"name": "private", "text_value": "secret"}],
    })
    assert normalized["gid"] == "project-123"
    assert normalized["name"] == "Roadmap"
    assert normalized["archived"] is False
    assert "custom_fields" not in normalized
    assert "private" not in json.dumps(normalized)


def test_offline_report_never_needs_credentials():
    with patch.dict(os.environ, {}, clear=True):
        workspaces = run("workspaces", 10, None, offline=True)
        projects = run("projects", 10, "workspace-123", offline=True)
    assert workspaces["service"] == "asana"
    assert workspaces["status"] == "offline"
    assert workspaces["workspaces"] == []
    assert projects["projects"] == []


def test_missing_credentials_is_safe():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True):
        payload = run("workspaces", 10, None, offline=False)
    assert payload["service"] == "asana"
    assert payload["status"] == "not_configured"
    assert payload["missing"] == ["ASANA_PERSONAL_ACCESS_TOKEN"]
    assert "asana-personal-access-token" not in json.dumps(payload).lower()


if __name__ == "__main__":
    for test in (
        test_normalize_workspace_drops_sensitive_fields,
        test_normalize_project_is_compact_metadata_only,
        test_offline_report_never_needs_credentials,
        test_missing_credentials_is_safe,
    ):
        test()
        print(f"✅ {test.__name__}")

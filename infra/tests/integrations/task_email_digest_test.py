#!/usr/bin/env python3
"""Offline tests for the categorized task email digest."""

from __future__ import annotations

import json
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

import task_email_digest  # noqa: E402
from task_email_digest import build_digest, task_group  # noqa: E402


def test_path_group_prefers_path_tag():
    assert task_group({
        "project": "🌐 Generalist Paths",
        "category": "learning",
        "tags": json.dumps(["generalist-path", "path:cooking-chef"]),
    }) == "cooking-chef"


def test_digest_groups_and_prioritizes_blocked_tasks():
    body, metadata = build_digest([
        {
            "id": 2, "title": "Build art artifact", "status": "todo", "priority": 3,
            "project": "🌐 Generalist Paths", "category": "learning",
            "tags": '["path:design-visual-art"]', "due_date": None,
        },
        {
            "id": 1, "title": "Fix blocked path", "status": "blocked", "priority": 3,
            "project": "🌐 Generalist Paths", "category": "learning",
            "tags": '["path:development"]', "due_date": "2026-08-06",
        },
    ], generated_at="2026-08-06 10:00 ICT")

    assert metadata == {
        "status": "ok",
        "open_tasks": 2,
        "shown_tasks": 2,
        "groups": {"design-visual-art": 1, "development": 1},
    }
    assert body.index("#1") < body.index("#2")
    assert "development / learning" in body
    assert "design-visual-art / learning" in body


def test_gmail_transport_uses_oauth_and_send_endpoint():
    original_get_env = task_email_digest.get_env
    original_get_google_creds = task_email_digest.get_google_creds
    original_get_access_token = task_email_digest.get_access_token
    original_api_request = task_email_digest.api_request
    captured = {}
    values = {
        "GOOGLE_GMAIL_SEND_REFRESH_TOKEN": "send-refresh-token",
        "GMAIL_FROM": "bookchaowalit@gmail.com",
    }

    def fake_get_env(key, default=None, required=False):
        return values.get(key, default)

    def fake_api_request(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return {"id": "gmail-message-1"}

    task_email_digest.get_env = fake_get_env
    task_email_digest.get_google_creds = lambda: ("client-id", "client-secret", "shared-refresh")
    task_email_digest.get_access_token = lambda client_id, client_secret, refresh: "access-token"
    task_email_digest.api_request = fake_api_request
    try:
        message_id = task_email_digest.send_digest_gmail(
            "hello digest", recipient="bookchaowalit@gmail.com", subject="Test digest"
        )
    finally:
        task_email_digest.get_env = original_get_env
        task_email_digest.get_google_creds = original_get_google_creds
        task_email_digest.get_access_token = original_get_access_token
        task_email_digest.api_request = original_api_request

    payload = json.loads(captured["data"].decode("utf-8"))
    raw = base64.urlsafe_b64decode(payload["raw"] + "===").decode("utf-8")
    assert message_id == "gmail-message-1"
    assert captured["url"].endswith("/users/me/messages/send")
    assert captured["headers"]["Authorization"] == "Bearer access-token"
    assert "Subject: Test digest" in raw
    assert "To: bookchaowalit@gmail.com" in raw
    assert "hello digest" in raw


if __name__ == "__main__":
    test_path_group_prefers_path_tag()
    test_digest_groups_and_prioritizes_blocked_tasks()
    test_gmail_transport_uses_oauth_and_send_endpoint()
    print("✅ task_email_digest tests passed")

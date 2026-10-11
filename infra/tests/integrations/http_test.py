"""Tests for safe Google OAuth error handling."""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

import _http  # noqa: E402


def test_invalid_grant_is_classified_without_provider_body():
    error = urllib.error.HTTPError(
        "https://oauth2.googleapis.com/token", 400, "Bad Request", {}, None
    )
    error.safe_provider_error = "invalid_grant"
    with patch.object(_http, "api_request", side_effect=error):
        try:
            _http.get_access_token("client-id", "client-secret", "refresh-token")
        except _http.GoogleOAuthError as exc:
            assert str(exc) == "google_oauth_invalid_grant"
            assert "refresh-token" not in str(exc)
        else:  # pragma: no cover - assertion guard
            raise AssertionError("expected GoogleOAuthError")


def test_non_dict_token_response_is_safe_error():
    with patch.object(_http, "api_request", return_value={"ok": True}):
        try:
            _http.get_access_token("client-id", "client-secret", "refresh-token")
        except _http.GoogleOAuthError as exc:
            assert str(exc) == "google_oauth_invalid_request"
        else:  # pragma: no cover - assertion guard
            raise AssertionError("expected GoogleOAuthError")


#!/usr/bin/env python3
"""Offline tests for Infisical/Vault/env secret resolution and health probes.

No real network calls — HTTP and CLI are mocked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

from secret_resolver import (  # noqa: E402
    STATUS_AUTHENTICATION_FAILED,
    STATUS_CREDENTIALS_NOT_CONFIGURED,
    STATUS_OK,
    STATUS_PROVIDER_UNREACHABLE,
    STATUS_SECRET_NOT_FOUND,
    SecretResolutionError,
    SecretUnavailable,
    get_secret,
    get_secret_with_source,
    probe_secret,
    probe_secrets,
)
from secrets_health import main as secrets_health_main  # noqa: E402


def test_env_provider():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env", "TEST_SECRET": "env-value"}, clear=True):
        assert get_secret("TEST_SECRET", required=True) == "env-value"
        assert get_secret_with_source("TEST_SECRET", required=True) == ("env-value", "env")
        probe = probe_secret("TEST_SECRET")
        assert probe["status"] == STATUS_OK
        assert "value" not in probe


def test_infisical_api_provider():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "google-client-id"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret_with_source("GOOGLE_CLIENT_ID", required=True) == ("google-client-id", "infisical")
        assert request.call_count == 2


def test_source_aware_env_fallback_is_reported_without_exposing_the_value():
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "SECRETS_FALLBACK_ENV": "true",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "TEST_SECRET": "fallback-value",
    }, clear=True), patch(
        "secret_resolver._safe_http_json",
        side_effect=SecretResolutionError("private provider detail"),
    ):
        value, source = get_secret_with_source("TEST_SECRET", required=True)
        assert value == "fallback-value"
        assert source == "env"


def test_infisical_cli_is_noninteractive():
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical-cli",
        "INFISICAL_CLI_ENABLED": "true",
        "INFISICAL_CLI": "/usr/bin/infisical",
        "INFISICAL_PROJECT_ID": "project-id",
    }, clear=True), patch(
        "secret_resolver.shutil.which", return_value="/usr/bin/infisical"
    ), patch(
        "secret_resolver.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["infisical"], returncode=0, stdout="cli-value\n", stderr=""
        ),
    ) as run:
        assert get_secret("GOOGLE_CLIENT_ID", required=True) == "cli-value"
        assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_infisical_cli_authentication_failure_is_classified():
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical-cli",
        "INFISICAL_CLI_ENABLED": "true",
        "INFISICAL_CLI": "/usr/bin/infisical",
        "INFISICAL_PROJECT_ID": "project-id",
    }, clear=True), patch(
        "secret_resolver.shutil.which", return_value="/usr/bin/infisical"
    ), patch(
        "secret_resolver.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["infisical"],
            returncode=1,
            stdout="",
            stderr="authentication required",
        ),
    ):
        probe = probe_secret("GOOGLE_CLIENT_ID")
        assert probe["status"] == STATUS_AUTHENTICATION_FAILED
        assert "value" not in probe


def test_infisical_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "todoist-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("TODOIST_API_TOKEN", required=True) == "todoist-token"
        assert "secretPath=%2Ftodoist" in request.call_args_list[1].args[0]


def test_binance_futures_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "demo-api-key"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("BINANCE_FUTURES_API_KEY", required=True) == "demo-api-key"
        assert "secretPath=%2Ftrading" in request.call_args_list[1].args[0]
        assert "projectId=65cc2f7f-4e1d-4123-aa06-5978ccaae861" in request.call_args_list[1].args[0]


def test_line_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "line-channel-secret"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("LINE_SOLO_EMPIRE_WEBHOOK_SECRET", required=True) == "line-channel-secret"
        assert "secretPath=%2Fline" in request.call_args_list[1].args[0]


def test_stripe_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "stripe-webhook-secret"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("STRIPE_WEBHOOK_SECRET", required=True) == "stripe-webhook-secret"
        assert "secretPath=%2Fstripe" in request.call_args_list[1].args[0]


def test_sentry_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "sentry-auth-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("SENTRY_AUTH_TOKEN", required=True) == "sentry-auth-token"
        assert "secretPath=%2Fsentry" in request.call_args_list[1].args[0]


def test_posthog_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "posthog-api-key"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("POSTHOG_API_KEY", required=True) == "posthog-api-key"
        assert "secretPath=%2Fposthog" in request.call_args_list[1].args[0]


def test_slack_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "slack-bot-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("SLACK_BOT_TOKEN", required=True) == "slack-bot-token"
        assert "secretPath=%2Fslack" in request.call_args_list[1].args[0]


def test_notion_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "notion-bot-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("NOTION_BOT_TOKEN", required=True) == "notion-bot-token"
        assert "secretPath=%2Fnotion" in request.call_args_list[1].args[0]


def test_asana_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "asana-personal-access-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("ASANA_PERSONAL_ACCESS_TOKEN", required=True) == "asana-personal-access-token"
        assert "secretPath=%2Fasana" in request.call_args_list[1].args[0]


def test_linear_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "linear-api-key"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("LINEAR_API_KEY", required=True) == "linear-api-key"
        assert "secretPath=%2Flinear" in request.call_args_list[1].args[0]


def test_discord_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "discord-bot-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("DISCORD_BOT_TOKEN", required=True) == "discord-bot-token"
        assert "secretPath=%2Fdiscord" in request.call_args_list[1].args[0]


def test_whatsapp_business_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "whatsapp-cloud-api-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("WHATSAPP_CLOUD_API_TOKEN", required=True) == "whatsapp-cloud-api-token"
        assert "secretPath=%2Fwhatsapp-business" in request.call_args_list[1].args[0]


def test_microsoft_graph_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "microsoft-graph-access-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("MICROSOFT_GRAPH_ACCESS_TOKEN", required=True) == "microsoft-graph-access-token"
        assert "secretPath=%2Fmicrosoft-graph" in request.call_args_list[1].args[0]


def test_zoom_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "zoom-access-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("ZOOM_ACCESS_TOKEN", required=True) == "zoom-access-token"
        assert "secretPath=%2Fzoom" in request.call_args_list[1].args[0]


def test_monday_folder_routing():
    responses = [
        {"accessToken": "short-lived-token", "expiresIn": 7200},
        {"secret": {"secretValue": "monday-api-token"}},
    ]
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
        "INFISICAL_SECRET_PATH": "/google",
    }, clear=True), patch("secret_resolver._safe_http_json", side_effect=responses) as request:
        assert get_secret("MONDAY_API_TOKEN", required=True) == "monday-api-token"
        assert "secretPath=%2Fmonday" in request.call_args_list[1].args[0]


def test_vault_kv2_provider():
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "vault",
        "VAULT_ADDR": "https://vault.example.com",
        "VAULT_TOKEN": "vault-token",
        "VAULT_KV_MOUNT": "secret",
        "VAULT_SECRET_PATH": "solo-empire/google",
    }, clear=True), patch(
        "secret_resolver._safe_http_json",
        return_value={"data": {"data": {"GOOGLE_REFRESH_TOKEN": "refresh-token"}}},
    ) as request:
        assert get_secret("GOOGLE_REFRESH_TOKEN", required=True) == "refresh-token"
        assert request.call_count == 1


def test_explicit_provider_fails_closed_without_fallback():
    """Explicit Vault mode must not silently use local env."""
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "vault",
        "VAULT_ADDR": "https://vault.example.com",
        "GOOGLE_CLIENT_ID": "local-value",
    }, clear=True):
        try:
            get_secret("GOOGLE_CLIENT_ID", required=True)
        except SecretUnavailable as exc:
            assert "not_configured" in str(exc)
            return
        raise AssertionError("explicit Vault mode must not silently use local env")


def test_missing_bootstrap_credentials():
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "infisical"}, clear=True):
        try:
            get_secret("GOOGLE_CLIENT_ID", required=True)
            raise AssertionError("expected SecretUnavailable")
        except SecretUnavailable as exc:
            assert "not_configured" in str(exc)
        probe = probe_secret("GOOGLE_CLIENT_ID")
        assert probe["status"] == STATUS_CREDENTIALS_NOT_CONFIGURED
        assert "value" not in probe


def test_provider_unreachable_no_silent_fallback():
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "vault",
        "VAULT_ADDR": "https://vault.example.com",
        "VAULT_TOKEN": "vault-token",
        "GOOGLE_CLIENT_ID": "should-not-be-used",
    }, clear=True), patch(
        "secret_resolver._safe_http_json",
        side_effect=SecretResolutionError("secret_provider_unreachable"),
    ):
        try:
            get_secret("GOOGLE_CLIENT_ID", required=True)
            raise AssertionError("expected provider failure")
        except SecretResolutionError as exc:
            assert "unreachable" in str(exc)
        probe = probe_secret("GOOGLE_CLIENT_ID")
        assert probe["status"] == STATUS_PROVIDER_UNREACHABLE
        assert probe.get("error_class") == STATUS_PROVIDER_UNREACHABLE


def test_authentication_failed_status():
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "infisical",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID": "machine-id",
        "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET": "machine-secret",
        "INFISICAL_PROJECT_ID": "project-id",
    }, clear=True), patch(
        "secret_resolver._safe_http_json",
        side_effect=SecretResolutionError("secret_provider_http_401"),
    ):
        probe = probe_secret("GOOGLE_CLIENT_ID")
        assert probe["status"] == STATUS_AUTHENTICATION_FAILED


def test_secret_not_found_status():
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "vault",
        "VAULT_ADDR": "https://vault.example.com",
        "VAULT_TOKEN": "vault-token",
    }, clear=True), patch(
        "secret_resolver._safe_http_json",
        return_value={"data": {"data": {}}},
    ):
        probe = probe_secret("GOOGLE_CLIENT_ID")
        assert probe["status"] == STATUS_SECRET_NOT_FOUND


def test_probe_secrets_report_shape_and_no_values():
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "env",
        "GOOGLE_CLIENT_ID": "client-id-value-xyz",
        "GOOGLE_CLIENT_SECRET": "client-secret-value-xyz",
        "GOOGLE_REFRESH_TOKEN": "refresh-token-value-xyz",
    }, clear=True):
        report = probe_secrets()
        assert report["status"] == STATUS_OK
        assert report["provider"] == "env"
        assert report["mode"] == "env"
        assert len(report["secrets"]) == 3
        blob = json.dumps(report)
        for forbidden in (
            "client-id-value-xyz",
            "client-secret-value-xyz",
            "refresh-token-value-xyz",
        ):
            assert forbidden not in blob
        for item in report["secrets"]:
            assert set(item.keys()) <= {"name", "status", "error_class"}


def test_secrets_health_cli_json_exit_codes():
    from io import StringIO
    with patch.dict(os.environ, {"SECRETS_PROVIDER": "env"}, clear=True), \
            patch("sys.stdout", new_callable=StringIO) as out:
        code = secrets_health_main(["--json"])
        assert code == 1  # google secrets missing
        payload = json.loads(out.getvalue())
        assert payload["status"] != STATUS_OK
        assert "client-secret" not in out.getvalue().lower()
    with patch.dict(os.environ, {
        "SECRETS_PROVIDER": "env",
        "GOOGLE_CLIENT_ID": "id",
        "GOOGLE_CLIENT_SECRET": "sec",
        "GOOGLE_REFRESH_TOKEN": "ref",
    }, clear=True), patch("sys.stdout", new_callable=StringIO) as out:
        code = secrets_health_main(["--json"])
        assert code == 0
        payload = json.loads(out.getvalue())
        assert payload["status"] == STATUS_OK
        assert "sec" not in json.dumps(payload.get("secrets"))


if __name__ == "__main__":
    for test in (
        test_env_provider,
        test_infisical_api_provider,
        test_source_aware_env_fallback_is_reported_without_exposing_the_value,
        test_infisical_cli_is_noninteractive,
        test_infisical_folder_routing,
        test_binance_futures_folder_routing,
        test_line_folder_routing,
        test_stripe_folder_routing,
        test_sentry_folder_routing,
        test_posthog_folder_routing,
        test_slack_folder_routing,
        test_notion_folder_routing,
        test_asana_folder_routing,
        test_linear_folder_routing,
        test_discord_folder_routing,
        test_whatsapp_business_folder_routing,
        test_microsoft_graph_folder_routing,
        test_zoom_folder_routing,
        test_monday_folder_routing,
        test_vault_kv2_provider,
        test_explicit_provider_fails_closed_without_fallback,
        test_missing_bootstrap_credentials,
        test_provider_unreachable_no_silent_fallback,
        test_authentication_failed_status,
        test_secret_not_found_status,
        test_probe_secrets_report_shape_and_no_values,
        test_secrets_health_cli_json_exit_codes,
    ):
        test()
        print(f"PASS {test.__name__}")

#!/usr/bin/env python3
"""Offline notification failure, privacy, and cron exit-status regressions."""

import contextlib
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
import alerts
from secret_resolver import SecretResolutionError


class AlertDeliveryTests(unittest.TestCase):
    def test_generic_message_sender_posts_without_exposing_credentials(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true}'

        response = Response()
        with patch.object(alerts, "get_env", side_effect=["fixture-token", "fixture-chat"]), patch("urllib.request.urlopen", return_value=response) as transport:
            alerts.send_telegram_message("Finance aggregate alert")

        request = transport.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "fixture-chat")
        self.assertEqual(payload["text"], "Finance aggregate alert")
        self.assertNotIn("fixture-token", request.data.decode("utf-8"))

    def test_provider_failure_never_reaches_transport(self):
        with patch.object(alerts, "get_env", side_effect=SecretResolutionError("infisical_cli_failed")), patch("urllib.request.urlopen") as transport:
            with self.assertRaisesRegex(alerts.AlertDeliveryError, "^secret_provider_unreachable$"):
                alerts._send_telegram([], [], [], date.today())
            transport.assert_not_called()

    def test_missing_configuration_is_not_a_success(self):
        with patch.object(alerts, "get_env", return_value=None):
            with self.assertRaisesRegex(alerts.AlertDeliveryError, "^telegram_credentials_missing$"):
                alerts._send_telegram([], [], [], date.today())

    def test_transport_exception_does_not_expose_request_details(self):
        with patch.object(alerts, "get_env", return_value="fixture"), patch("urllib.request.urlopen", side_effect=URLError("sensitive request fixture")):
            with self.assertRaisesRegex(alerts.AlertDeliveryError, "^telegram_delivery_failed$"):
                alerts._send_telegram([], [], [], date.today())

    def test_http_auth_failure_is_classified_without_response_details(self):
        failure = HTTPError("https://api.telegram.org/bot<redacted>/sendMessage", 401, "unauthorized", {}, None)
        with patch.object(alerts, "get_env", return_value="fixture"), patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaisesRegex(alerts.AlertDeliveryError, "^telegram_authentication_failed$"):
                alerts._send_telegram([], [], [], date.today())

    def test_invalid_bot_token_404_is_classified_as_authentication_failure(self):
        failure = HTTPError("https://api.telegram.org/bot<redacted>/getMe", 404, "not found", {}, None)
        with patch.object(alerts, "get_env", return_value="fixture"), patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaisesRegex(alerts.AlertDeliveryError, "^telegram_authentication_failed$"):
                alerts.send_telegram_message("Finance aggregate alert")

    def test_main_reports_failure(self):
        output = io.StringIO()
        with patch.object(alerts, "check_alerts", side_effect=alerts.AlertDeliveryError("secret_provider_unreachable")), contextlib.redirect_stderr(output):
            self.assertEqual(alerts.main(), 1)
        self.assertIn('"error": "secret_provider_unreachable"', output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_detecting_critical_alerts_is_still_a_successful_check(self):
        with patch.object(alerts, "check_alerts", return_value=3):
            self.assertEqual(alerts.main(), 0)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Offline tests for the explicit RunPod Stop Pod provider hook."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "platform" / "runpod_stop_pod_hook.py"
SPEC = importlib.util.spec_from_file_location("runpod_stop_pod_hook", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
runpod_stop_pod_hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runpod_stop_pod_hook)


class FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit: int):
        self.read_limit = limit
        return b"{}"


class RunpodStopPodHookTest(unittest.TestCase):
    def base_env(self) -> dict[str, str]:
        return {
            "RUNPOD_AUTO_STOP_ENABLED": "true",
            "RUNPOD_POD_ID": "pod_ABC123",
            "RUNPOD_API_KEY": "test-only-key",
        }

    def test_disabled_hook_does_not_construct_an_opener(self):
        opener_factory = Mock()
        env = self.base_env()
        env["RUNPOD_AUTO_STOP_ENABLED"] = "false"
        with self.assertRaisesRegex(runpod_stop_pod_hook.StopHookError, "not_enabled"):
            runpod_stop_pod_hook.stop_pod(env, opener_factory=opener_factory)
        opener_factory.assert_not_called()

    def test_invalid_pod_id_and_missing_key_fail_closed(self):
        opener_factory = Mock()
        env = self.base_env()
        env["RUNPOD_POD_ID"] = "../other-pod"
        with self.assertRaisesRegex(runpod_stop_pod_hook.StopHookError, "pod_id_invalid"):
            runpod_stop_pod_hook.stop_pod(env, opener_factory=opener_factory)
        env = self.base_env()
        env.pop("RUNPOD_API_KEY")
        with self.assertRaisesRegex(runpod_stop_pod_hook.StopHookError, "api_key_unavailable"):
            runpod_stop_pod_hook.stop_pod(env, opener_factory=opener_factory)
        opener_factory.assert_not_called()

    def test_header_injection_in_api_key_fails_closed(self):
        opener_factory = Mock()
        env = self.base_env()
        env["RUNPOD_API_KEY"] = "test-only-key\nInjected: true"
        with self.assertRaisesRegex(runpod_stop_pod_hook.StopHookError, "api_key_invalid"):
            runpod_stop_pod_hook.stop_pod(env, opener_factory=opener_factory)
        opener_factory.assert_not_called()

    def test_success_uses_fixed_https_stop_endpoint_and_bounded_response_read(self):
        response = FakeResponse()
        opener = Mock()
        opener.open.return_value = response
        opener_factory = Mock(return_value=opener)

        result = runpod_stop_pod_hook.stop_pod(self.base_env(), opener_factory=opener_factory)

        self.assertEqual(result, {"status": "stopped", "pod_id": "pod_ABC123"})
        opener_factory.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://rest.runpod.io/v1/pods/pod_ABC123/stop")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-key")
        self.assertIsNone(request.data)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)
        self.assertEqual(response.read_limit, 1024 * 1024 + 1)

    def test_non_success_status_is_reported_without_provider_body(self):
        response = FakeResponse(status=202)
        opener = Mock()
        opener.open.return_value = response
        opener_factory = Mock(return_value=opener)
        with self.assertRaisesRegex(runpod_stop_pod_hook.StopHookError, "http_202"):
            runpod_stop_pod_hook.stop_pod(self.base_env(), opener_factory=opener_factory)


if __name__ == "__main__":
    unittest.main()

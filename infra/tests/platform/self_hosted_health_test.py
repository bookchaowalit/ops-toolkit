#!/usr/bin/env python3
"""Contract tests for the local self-hosted read-only health adapter."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "platform" / "self_hosted_health.py"
SPEC = importlib.util.spec_from_file_location("self_hosted_health", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
self_hosted_health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(self_hosted_health)


def fake_run(command, timeout=15):
    if command[:2] == ["docker", "ps"]:
        return {
            "ok": True,
            "error_class": None,
            "stdout": "\n".join(
                [
                    json.dumps({"Names": "se-prometheus", "State": "running", "Status": "Up 2 days (healthy)"}),
                    json.dumps({"Names": "se-grafana", "State": "running", "Status": "Up 2 days (unhealthy)"}),
                    json.dumps({"Names": "unrelated-app", "State": "running", "Status": "Up 2 days (healthy)"}),
                ]
            ),
        }
    if command[:3] == ["kubectl", "get", "nodes"]:
        return {
            "ok": True,
            "error_class": None,
            "stdout": json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "solo-dev-server",
                                "labels": {"node-role.kubernetes.io/control-plane": ""},
                            },
                            "status": {
                                "conditions": [{"type": "Ready", "status": "True"}],
                                "nodeInfo": {"kubeletVersion": "v1.35.5+k3s1"},
                            },
                        }
                    ]
                }
            ),
        }
    if command[:3] == ["kubectl", "get", "applications.argoproj.io"]:
        return {
            "ok": True,
            "error_class": None,
            "stdout": json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"namespace": "argocd", "name": "ai-doc-assistant-dev"},
                            "status": {
                                "sync": {"status": "Unknown"},
                                "health": {"status": "Healthy"},
                                "secretPayload": "should-not-appear",
                            },
                        }
                    ]
                }
            ),
        }
    raise AssertionError(f"Unexpected command: {command}")


class SelfHostedHealthTest(unittest.TestCase):
    def test_health_is_read_only_and_bounds_docker_and_kubernetes_metadata(self):
        with patch.object(self_hosted_health, "_run", side_effect=fake_run) as run:
            payload = self_hosted_health.run_health()

        self.assertEqual(payload["status"], "degraded")
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["execution_enabled"])
        self.assertFalse(payload["write_attempted"])
        self.assertEqual(payload["summary"]["docker_running"], 2)
        self.assertEqual(payload["summary"]["kubernetes_ready"], 1)
        self.assertEqual(payload["components"][0]["summary"]["unhealthy"], 1)
        self.assertEqual(payload["components"][1]["summary"]["argo_healthy"], 1)
        self.assertNotIn("unrelated-app", json.dumps(payload))
        self.assertNotIn("secretPayload", json.dumps(payload))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all(command[0] in {"docker", "kubectl"} for command in commands))
        self.assertTrue(all(not any(flag in command for flag in ("rm", "stop", "delete", "apply", "scale")) for command in commands))

    def test_missing_local_runtimes_are_reported_without_raising(self):
        with patch.object(
            self_hosted_health,
            "_run",
            return_value={"ok": False, "stdout": "", "error_class": "command_not_found"},
        ):
            payload = self_hosted_health.run_health()

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["summary"]["unavailable"], 2)
        self.assertTrue(all(component["available"] is False for component in payload["components"]))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Contract tests for the read-only self-hosted readiness preflight."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "platform" / "self_hosted_readiness.py"
SPEC = importlib.util.spec_from_file_location("self_hosted_readiness", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
self_hosted_readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(self_hosted_readiness)


class SelfHostedReadinessTest(unittest.TestCase):
    def test_profile_evaluation_blocks_conflicts_and_requires_explicit_target(self):
        host = {
            "cpu_cores": 16,
            "memory_gib": 32,
            "free_disk_gib": 100,
            "docker": {"available": True},
            "listening_ports": {
                "ports": [
                    {"port": 80, "scope": "wildcard"},
                    {"port": 443, "scope": "wildcard"},
                    {"port": 3000, "scope": "wildcard"},
                ]
            },
        }
        report = self_hosted_readiness.evaluate_profile(
            "coolify",
            host=host,
            docker={"status": "healthy"},
            kubernetes={"status": "healthy"},
            existing_container_count=4,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["conflicting_ports"], [80, 443])
        self.assertIn("port-conflict", report["blockers"])
        self.assertIn("existing-workloads-on-host", report["blockers"])
        self.assertIn("target-host-not-selected", report["blockers"])

    def test_readiness_report_is_bounded_and_read_only(self):
        command_outputs = {
            ("docker", "version", "--format", "{{json .Server}}"): '{"Version":"28.0.0"}\n',
            ("docker", "compose", "version", "--short"): "2.39.0\n",
            ("ss", "-ltnH"): "LISTEN 0 128 0.0.0.0:80 0.0.0.0:*\n",
        }

        def fake_run(command, timeout=15):
            key = tuple(command)
            if key in command_outputs:
                return {"ok": True, "stdout": command_outputs[key], "error_class": None}
            if command[:2] == ["docker", "ps"]:
                return {"ok": True, "stdout": '{"Names":"se-web","State":"running","secretPayload":"hidden"}\n', "error_class": None}
            if command[:4] == ["kubectl", "get", "nodes", "-o"]:
                return {
                    "ok": True,
                    "stdout": json.dumps({"items": [{"metadata": {"name": "node-1", "labels": {}}, "status": {"conditions": [{"type": "Ready", "status": "True"}], "nodeInfo": {"kubeletVersion": "v1"}}}]}),
                    "error_class": None,
                }
            if command[:4] == ["kubectl", "get", "applications.argoproj.io", "-A"]:
                return {"ok": True, "stdout": json.dumps({"items": []}), "error_class": None}
            return {"ok": False, "stdout": "", "error_class": "command_not_found"}

        with patch.object(self_hosted_readiness, "_run", side_effect=fake_run), patch.object(
            self_hosted_readiness, "_memory_bytes", return_value=8 * 1024**3
        ), patch.object(self_hosted_readiness, "_disk_bytes", return_value=50 * 1024**3), patch.object(
            self_hosted_readiness,
            "docker_report",
            return_value={"status": "healthy", "summary": {"self_hosted": 1}},
        ), patch.object(
            self_hosted_readiness,
            "kubernetes_report",
            return_value={"status": "healthy", "summary": {"ready": 1}},
        ):
            payload = self_hosted_readiness.run_readiness("coolify")

        serialized = json.dumps(payload)
        self.assertEqual(payload["schema_version"], "solo-empire.self-hosted-readiness.v1")
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["execution_enabled"])
        self.assertFalse(payload["write_attempted"])
        self.assertNotIn("secretPayload", serialized)
        self.assertNotIn("docker stop", serialized)
        self.assertEqual(payload["profiles"][0]["conflicting_ports"], [80])


if __name__ == "__main__":
    unittest.main()

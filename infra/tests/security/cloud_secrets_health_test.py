"""Registry-driven presence checks never expose credential values."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from secrets_health import cloud_secrets_health
from secret_resolver import _cloud_secret_paths


class CloudSecretsHealthTest(unittest.TestCase):
    def test_offline_checks_all_providers_without_probing(self):
        probe = Mock()
        report = cloud_secrets_health(offline=True, probe=probe)
        self.assertEqual(len(report["providers"]), 17)
        self.assertTrue(all(item["presence"] == "not_checked" for p in report["providers"] for item in p["secrets"]))
        probe.assert_not_called()

    def test_scoped_presence_is_allowlisted(self):
        probe = Mock(return_value={
            "status": "ok", "mode": "env", "provider": "env",
            "value": "PRIVATE_SENTINEL", "payload": "PRIVATE_SENTINEL",
            "secrets": [
                {"name": "UPSTASH_API_KEY", "status": "ok", "unexpected": "PRIVATE_SENTINEL"},
                {"name": "UPSTASH_EMAIL", "status": "secret_not_found"},
                {"name": "UPSTASH_REDIS_REST_TOKEN", "status": "provider_unreachable", "error_class": "timeout"},
            ],
        })
        report = cloud_secrets_health(provider_id="upstash", probe=probe)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(report))
        self.assertEqual(probe.call_args.args[0], ["UPSTASH_API_KEY", "UPSTASH_EMAIL", "UPSTASH_REDIS_REST_TOKEN"])
        self.assertEqual([s["presence"] for s in report["providers"][0]["secrets"]], ["present", "missing", "unavailable"])

    def test_registry_routes_all_management_secrets(self):
        paths = _cloud_secret_paths()
        for key, path in (
            ("NEON_API_KEY", "/neon"), ("RUNPOD_API_KEY", "/runpod"),
            ("RENDER_API_KEY", "/render"), ("RAILWAY_API_TOKEN", "/railway"),
            ("UPSTASH_EMAIL", "/upstash"), ("VERCEL_TEAM_ID", "/vercel"),
        ):
            self.assertEqual(paths[key], path)

    def test_unknown_provider_never_probes(self):
        probe = Mock()
        with self.assertRaises(KeyError):
            cloud_secrets_health(provider_id="invalid", probe=probe)
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()

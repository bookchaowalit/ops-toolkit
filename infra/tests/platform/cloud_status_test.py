#!/usr/bin/env python3
"""Tests for normalized, provider-scoped cloud status aggregation."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import unittest.mock
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "platform"))

import cloud_status  # noqa: E402


def provider(
    provider_id: str,
    *,
    capabilities: dict[str, bool] | None = None,
    paas: bool = False,
    cli_binary: str | None = None,
) -> dict:
    return {
        "id": provider_id,
        "name": provider_id.title(),
        "category": "test",
        "adapter_status": "partial",
        "write_policy": "proposal_only",
        "capabilities": capabilities
        or {"health": False, "inventory": False, "usage": False, "billing": False},
        "paas": paas,
        "cli_binary": cli_binary,
        "cli_install": {"url": "https://example.com/cli"},
        "project_usage": [],
    }


def registry(*providers: dict) -> dict:
    return {
        "read_only_by_default": True,
        "write_actions_enabled": False,
        "write_entrypoint": "paas:action:propose",
        "providers": list(providers),
    }


class CloudStatusTest(unittest.TestCase):
    def test_offline_snapshot_includes_registry_providers_and_capability_gaps(self):
        cloudflare = provider(
            "cloudflare",
            capabilities={"health": False, "inventory": False, "usage": True, "billing": False},
        )
        render = provider(
            "render",
            capabilities={"health": True, "inventory": True, "usage": False, "billing": False},
            paas=True,
        )
        neon = provider("neon")
        health_runner = Mock(return_value={"providers": [{"id": "render", "status": "offline"}]})
        inventory_runner = Mock(return_value={"providers": [{"id": "render", "status": "offline"}]})
        r2_runner = Mock(
            return_value={
                "status": "not_checked",
                "payload_gib": 0,
                "auth_source": "not_checked",
            }
        )
        snapshot_runner = Mock(return_value={"costs": {"status": "available"}, "errors": []})

        result = cloud_status.build_status(
            registry=registry(cloudflare, render, neon),
            health_runner=health_runner,
            inventory_runner=inventory_runner,
            usage_runners={"cloudflare": r2_runner},
            billing_runners={},
            snapshot_runner=snapshot_runner,
        )

        self.assertEqual([item["id"] for item in result["providers"]], ["cloudflare", "render", "neon"])
        self.assertEqual(result["mode"], "offline")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["write_actions_enabled"])
        self.assertEqual(result["providers"][0]["usage"]["status"], "not_checked")
        self.assertNotIn("payload_gib", result["providers"][0]["usage"]["data"])
        self.assertEqual(result["providers"][1]["health"]["status"], "offline")
        self.assertEqual(result["providers"][2]["usage"]["status"], "not_supported")
        self.assertEqual(result["costs"]["status"], "available")
        health_runner.assert_called_once_with(live=False)
        inventory_runner.assert_called_once_with(live=False)
        r2_runner.assert_called_once_with(live=False)
        snapshot_runner.assert_called_once_with(live=False)
        output = io.StringIO()
        with redirect_stdout(output):
            cloud_status._print_status_table(result)
        self.assertNotIn("0.00 GiB", output.getvalue())

    def test_offline_health_credential_skip_is_not_reported_as_missing(self):
        result = cloud_status._adapter_capability(
            {
                "status": "not_configured",
                "credential_state": "not_checked",
                "auth_source": "not_checked",
                "error_class": "offline_credential_check_skipped",
            }
        )

        self.assertEqual(result["status"], "not_checked")
        self.assertEqual(result["data"]["credential_state"], "not_checked")

    def test_provider_filter_limits_live_calls_to_selected_provider(self):
        cloudflare = provider(
            "cloudflare",
            capabilities={"health": False, "inventory": False, "usage": True, "billing": False},
        )
        github = provider(
            "github",
            capabilities={"health": True, "inventory": False, "usage": True, "billing": True},
        )
        vercel = provider(
            "vercel",
            capabilities={"health": True, "inventory": True, "usage": True, "billing": True},
            paas=True,
        )
        health_runner = Mock()
        single_health_runner = Mock()
        inventory_runner = Mock()
        r2_runner = Mock(return_value={"status": "healthy", "auth_source": "secret_manager"})
        github_runner = Mock()
        vercel_runner = Mock()
        snapshot_runner = Mock(return_value={"costs": {"status": "available"}, "errors": []})

        result = cloud_status.build_status(
            live=True,
            provider_id="cloudflare",
            registry=registry(cloudflare, github, vercel),
            health_runner=health_runner,
            single_health_runner=single_health_runner,
            inventory_runner=inventory_runner,
            usage_runners={"cloudflare": r2_runner, "github": github_runner},
            billing_runners={"vercel": vercel_runner},
            snapshot_runner=snapshot_runner,
        )

        self.assertEqual([item["id"] for item in result["providers"]], ["cloudflare"])
        self.assertEqual(result["providers"][0]["auth_sources"], {"usage": "secret_manager"})
        health_runner.assert_not_called()
        single_health_runner.assert_not_called()
        inventory_runner.assert_not_called()
        r2_runner.assert_called_once_with(live=True)
        github_runner.assert_not_called()
        vercel_runner.assert_not_called()
        snapshot_runner.assert_called_once_with(live=False)

    def test_filtered_paas_uses_single_provider_health_and_inventory(self):
        render = provider(
            "render",
            capabilities={"health": True, "inventory": True, "usage": False, "billing": False},
            paas=True,
        )
        railway = provider(
            "railway",
            capabilities={"health": True, "inventory": True, "usage": False, "billing": False},
            paas=True,
        )
        health_runner = Mock()
        single_health_runner = Mock(return_value={"id": "render", "status": "healthy"})
        inventory_runner = Mock(
            return_value={"providers": [{"id": "render", "status": "healthy", "resources": []}]}
        )

        result = cloud_status.build_status(
            live=True,
            provider_id="render",
            registry=registry(render, railway),
            health_runner=health_runner,
            single_health_runner=single_health_runner,
            inventory_runner=inventory_runner,
            usage_runners={},
            billing_runners={},
            snapshot_runner=Mock(return_value={"costs": {"status": "unavailable"}, "errors": []}),
        )

        self.assertEqual(result["providers"][0]["health"]["status"], "healthy")
        self.assertEqual(result["providers"][0]["inventory"]["status"], "healthy")
        health_runner.assert_not_called()
        single_health_runner.assert_called_once_with("render", live=True)
        inventory_runner.assert_called_once_with(live=True, provider_id="render")

    def test_github_billing_does_not_claim_usage_quantities_are_costs(self):
        github = provider(
            "github",
            capabilities={"health": True, "inventory": False, "usage": True, "billing": True},
        )
        result = cloud_status.build_status(
            live=True,
            provider_id="github",
            registry=registry(github),
            usage_runners={
                "github": Mock(
                    return_value={
                        "status": "healthy",
                        "actions_minutes": 12,
                        "auth_source": "cli_session",
                    }
                )
            },
            billing_runners={},
            snapshot_runner=Mock(return_value={"costs": {"status": "available"}, "errors": []}),
        )

        billing = result["providers"][0]["billing"]
        self.assertEqual(billing["status"], "usage_only")
        self.assertFalse(billing["costs_available"])
        self.assertEqual(result["providers"][0]["health"]["status"], "healthy")
        self.assertEqual(
            result["providers"][0]["auth_sources"],
            {"health": "cli_session", "usage": "cli_session", "billing": "cli_session"},
        )

    def test_vercel_billing_adapter_supplies_usage_summary_without_duplicate_call(self):
        vercel = provider(
            "vercel",
            capabilities={"health": True, "inventory": True, "usage": True, "billing": True},
            paas=True,
        )
        billing_runner = Mock(
            return_value={
                "status": "healthy",
                "charge_count": 3,
                "billed_cost_by_currency": {"USD": 2.5},
                "window": {"from": "2026-09-01", "to": "2026-09-30"},
                "auth_source": "env",
            }
        )
        result = cloud_status.build_status(
            live=True,
            provider_id="vercel",
            registry=registry(vercel),
            single_health_runner=Mock(return_value={"id": "vercel", "status": "healthy"}),
            inventory_runner=Mock(return_value={"providers": []}),
            usage_runners={},
            billing_runners={"vercel": billing_runner},
            snapshot_runner=Mock(return_value={"costs": {"status": "available"}, "errors": []}),
        )

        self.assertEqual(result["providers"][0]["usage"]["data"]["charge_count"], 3)
        self.assertEqual(result["providers"][0]["billing"]["status"], "healthy")
        self.assertEqual(result["providers"][0]["auth_sources"]["usage"], "env")
        billing_runner.assert_called_once_with(live=True)
        output = io.StringIO()
        with redirect_stdout(output):
            cloud_status._print_status_table(result)
        self.assertIn("3 charges", output.getvalue())
        self.assertIn("USD 2.50", output.getvalue())

    def test_hyperscaler_health_reports_cli_login_without_credentials(self):
        gcp = provider(
            "gcp",
            capabilities={"health": True, "inventory": False, "usage": False, "billing": False},
            cli_binary="gcloud",
        )
        session_runner = Mock(return_value={"status": "healthy", "auth_source": "cli_session", "source": "cli_session"})
        payload = cloud_status.build_status(
            live=True,
            provider_id="gcp",
            registry=registry(gcp),
            usage_runners={},
            billing_runners={},
            read_runners={},
            snapshot_runner=Mock(return_value={"costs": {"status": "unavailable"}, "errors": []}),
            cli_session_health_runner=session_runner,
        )
        session_runner.assert_called_once_with(gcp, live=True)
        health = payload["providers"][0]["health"]
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(payload["providers"][0]["auth_sources"], {"health": "cli_session"})
        self.assertNotIn("credential", health["data"])

    def test_hyperscaler_health_is_not_checked_offline(self):
        with unittest.mock.patch.object(cloud_status, "resolve_provider_auth") as resolver:
            result = cloud_status.check_cli_session_health(provider("aws", cli_binary="aws"), live=False)
        resolver.assert_not_called()
        self.assertEqual(cloud_status._adapter_capability(result)["status"], "not_checked")

    def test_unknown_provider_is_rejected_before_adapter_calls(self):
        usage_runner = Mock()
        with self.assertRaisesRegex(KeyError, "unknown"):
            cloud_status.build_status(
                provider_id="unknown",
                registry=registry(provider("cloudflare")),
                usage_runners={"cloudflare": usage_runner},
                snapshot_runner=Mock(),
            )
        usage_runner.assert_not_called()

    def test_unavailable_local_costs_are_not_rendered_as_zero(self):
        payload = {
            "mode": "offline",
            "providers": [],
            "costs": {
                "status": "unavailable",
                "scope": "local finance ledger aggregate; not attributed",
                "data": {
                    "planned_monthly_equivalent": {},
                    "actual_paid_by_currency": {},
                },
            },
            "write_actions_enabled": False,
            "write_entrypoint": "paas:action:propose",
        }
        output = io.StringIO()
        with redirect_stdout(output):
            cloud_status._print_status_table(payload)

        self.assertIn("Local ledger: unavailable", output.getvalue())
        self.assertNotIn("planned/month", output.getvalue())
        self.assertNotIn("0.00", output.getvalue())

    def test_unverified_adapter_and_cost_metrics_are_not_normalized_to_zero(self):
        cloudflare = provider(
            "cloudflare",
            capabilities={"health": False, "inventory": False, "usage": True, "billing": False},
        )
        result = cloud_status.build_status(
            provider_id="cloudflare",
            registry=registry(cloudflare),
            usage_runners={
                "cloudflare": Mock(
                    return_value={
                        "status": "not_checked",
                        "payload_gib": 0,
                        "object_count": 0,
                    }
                )
            },
            billing_runners={},
            snapshot_runner=Mock(
                return_value={
                    "costs": {
                        "status": "unavailable",
                        "planned_monthly_equivalent": {},
                        "actual_paid_by_currency": {},
                    },
                    "errors": [],
                }
            ),
        )

        self.assertEqual(result["providers"][0]["usage"]["status"], "not_checked")
        self.assertEqual(result["providers"][0]["usage"]["data"], {"status": "not_checked"})
        self.assertIsNone(result["costs"]["data"])

    def test_tools_doctor_only_checks_binaries_and_recommends_missing_tools(self):
        cloudflare = provider("cloudflare", cli_binary="wrangler")
        render = provider("render", cli_binary="render")
        upstash = provider("upstash", cli_binary=None)
        lookup = Mock(side_effect=lambda binary: "C:/tools/wrangler.exe" if binary == "wrangler" else None)

        result = cloud_status.build_tools_doctor(
            registry=registry(cloudflare, render, upstash),
            which=lookup,
        )

        self.assertEqual(result["summary"], {"providers": 3, "registered_tools": 2, "installed": 1, "missing": 1, "version_errors": 0})
        self.assertEqual(result["tools"][0]["status"], "installed")
        self.assertEqual(result["tools"][1]["status"], "missing")
        self.assertNotIn("resolved_path", result["tools"][0])
        self.assertIn("url", result["tools"][1]["recommendation"])
        self.assertEqual(result["tools"][2]["status"], "not_registered")
        lookup.assert_any_call("wrangler")
        lookup.assert_any_call("render")
        self.assertEqual(lookup.call_count, 2)


if __name__ == "__main__":
    unittest.main()

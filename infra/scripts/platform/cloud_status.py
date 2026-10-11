#!/usr/bin/env python3
"""Aggregate read-only cloud provider health, inventory, usage, and costs."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from auth_resolver import resolve_provider_auth  # noqa: E402
from cloud_providers import CloudProviderRegistryError, load_registry  # noqa: E402
from cloud_tools import build_tools_doctor  # noqa: E402
from cloud_read_adapters import READ_PROVIDERS, collect_provider  # noqa: E402
from cloud_usage import build_snapshot  # noqa: E402
from cloudflare_r2_usage import run_metrics as run_r2_metrics  # noqa: E402
from github_actions_usage import run_usage as run_github_actions_usage  # noqa: E402
from paas_health import check_paas_provider, run_health_check  # noqa: E402
from paas_inventory import INVENTORIES, run_inventory  # noqa: E402
from platform_output import configure_utf8_stdio  # noqa: E402
from vercel_billing import run_billing as run_vercel_billing  # noqa: E402


SCHEMA_VERSION = "solo-empire.cloud-status.v1"
AUTH_SOURCES = {"secret_manager", "env", "cli_session"}
USAGE_ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "cloudflare": run_r2_metrics,
    "github": run_github_actions_usage,
}
BILLING_ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "vercel": run_vercel_billing,
}
CLI_SESSION_HEALTH_PROVIDERS = frozenset({"gcp", "azure", "aws"})


def check_cli_session_health(provider: dict[str, Any], *, live: bool = False) -> dict[str, Any]:
    """Report whether the local CLI is logged in; never returns credentials."""
    scope = "local CLI login only; not resource health"
    if not live:
        return {"status": "not_configured", "credential_state": "not_checked", "source": "cli_session"}
    auth = resolve_provider_auth(provider)
    if auth.status == "configured":
        return {"status": "healthy", "auth_source": auth.auth_source, "source": "cli_session", "scope": scope}
    result = {"status": "error" if auth.status == "error" else "not_configured", "source": "cli_session"}
    if auth.error_class:
        result["error_class"] = auth.error_class
    return result

def _unavailable_capability(provider: dict[str, Any], name: str) -> dict[str, Any]:
    declared = bool(provider.get("capabilities", {}).get(name, False))
    return {
        "status": "not_implemented" if declared else "not_supported",
        "declared": declared,
        "implemented": False,
        "data": None,
    }


def _adapter_capability(result: dict[str, Any]) -> dict[str, Any]:
    status = result.get("status")
    normalized_status = status if isinstance(status, str) and status else "unknown"
    if normalized_status == "not_configured" and (
        result.get("credential_state") == "not_checked"
        or result.get("error_class") == "offline_credential_check_skipped"
    ):
        normalized_status = "not_checked"
    if normalized_status in {"healthy", "available", "partial"}:
        data = result
    else:
        data = {
            key: result[key]
            for key in (
                "status",
                "mode",
                "auth_source",
                "credential_state",
                "error_class",
                "next_action",
                "source",
                "reason",
            )
            if isinstance(result.get(key), str)
        }
        if "status" in data:
            data["status"] = normalized_status
        if isinstance(result.get("errors"), list):
            data["errors"] = [
                {key: item[key] for key in ("id", "resource_type", "error_class") if isinstance(item.get(key), str)}
                for item in result["errors"] if isinstance(item, dict)
            ]
    return {
        "status": normalized_status,
        "declared": True,
        "implemented": True,
        "data": data,
    }


def _by_provider(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    providers = payload.get("providers")
    if not isinstance(providers, list):
        return {}
    return {
        item["id"]: item
        for item in providers
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _auth_sources(capabilities: Mapping[str, dict[str, Any]]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for capability, projection in capabilities.items():
        data = projection.get("data")
        source = projection.get("auth_source")
        if source is None and isinstance(data, dict):
            source = data.get("auth_source")
        if isinstance(source, str) and source in AUTH_SOURCES:
            sources[capability] = source
    return sources


def _vercel_usage(billing: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": billing.get("status", "unknown"),
        "source": "vercel_billing",
        "charge_count": billing.get("charge_count"),
        "billed_cost_by_currency": billing.get("billed_cost_by_currency"),
        "window": billing.get("window"),
        "auth_source": billing.get("auth_source"),
    }


def _github_billing(usage: dict[str, Any]) -> dict[str, Any]:
    usage_status = usage.get("status")
    status = "usage_only" if usage_status == "healthy" else usage_status
    if not isinstance(status, str) or not status:
        status = "unknown"
    return {
        "status": status,
        "declared": True,
        "implemented": True,
        "costs_available": False,
        "source": "github_actions_usage",
        "data": {"usage_status": usage_status},
        "auth_source": usage.get("auth_source"),
    }


def _github_health(usage: dict[str, Any]) -> dict[str, Any]:
    health = {
        "id": "github",
        "status": usage.get("status", "unknown"),
        "source": "github_actions_usage",
    }
    for key in ("auth_source", "credential_state", "error_class"):
        value = usage.get(key)
        if isinstance(value, str):
            health[key] = value
    return health


def build_status(
    *,
    live: bool = False,
    provider_id: str | None = None,
    registry: dict[str, Any] | None = None,
    health_runner: Callable[..., dict[str, Any]] | None = None,
    single_health_runner: Callable[..., dict[str, Any]] | None = None,
    inventory_runner: Callable[..., dict[str, Any]] | None = None,
    usage_runners: Mapping[str, Callable[..., dict[str, Any]]] | None = None,
    billing_runners: Mapping[str, Callable[..., dict[str, Any]]] | None = None,
    read_runners: Mapping[str, Callable[..., dict[str, Any]]] | None = None,
    snapshot_runner: Callable[..., dict[str, Any]] | None = None,
    cli_session_health_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a normalized snapshot while honoring provider-scoped live reads."""
    registry = load_registry() if registry is None else registry
    registry_providers = registry.get("providers")
    if not isinstance(registry_providers, list):
        raise CloudProviderRegistryError("registry providers must be a list")
    provider_by_id = {
        provider["id"]: provider
        for provider in registry_providers
        if isinstance(provider, dict) and isinstance(provider.get("id"), str)
    }
    if provider_id is not None and provider_id not in provider_by_id:
        raise KeyError(f"Unknown cloud provider: {provider_id}")

    selected = [provider_by_id[provider_id]] if provider_id else registry_providers
    health_by_id: dict[str, dict[str, Any]] = {}
    inventory_by_id: dict[str, dict[str, Any]] = {}

    if provider_id is None:
        health_payload = (health_runner or run_health_check)(live=live)
        health_by_id = _by_provider(health_payload)
        inventory_payload = (inventory_runner or run_inventory)(live=live)
        inventory_by_id = _by_provider(inventory_payload)
    else:
        provider = selected[0]
        if provider.get("paas") is True and provider.get("capabilities", {}).get("health") is True:
            health = (single_health_runner or check_paas_provider)(provider_id, live=live)
            if isinstance(health, dict):
                health_by_id[provider_id] = health
        if provider_id in INVENTORIES:
            inventory_payload = (inventory_runner or run_inventory)(live=live, provider_id=provider_id)
            inventory_by_id = _by_provider(inventory_payload)

    usage_adapters = USAGE_ADAPTERS if usage_runners is None else usage_runners
    billing_adapters = BILLING_ADAPTERS if billing_runners is None else billing_runners
    usage_by_id: dict[str, dict[str, Any]] = {}
    billing_by_id: dict[str, dict[str, Any]] = {}
    for provider in selected:
        current_id = provider["id"]
        if not any(provider.get("capabilities", {}).get(key) for key in ("health", "inventory")):
            continue
        if read_runners is None:
            bundle = collect_provider(current_id, live=live) if current_id in READ_PROVIDERS else None
        else:
            runner = read_runners.get(current_id)
            bundle = runner(live=live) if runner else None
        if bundle is not None:
            for capability, index in (
                ("health", health_by_id), ("inventory", inventory_by_id), ("usage", usage_by_id),
            ):
                if capability in bundle and provider["capabilities"].get(capability):
                    index[current_id] = bundle[capability]
    for provider in selected:
        current_id = provider["id"]
        capabilities = provider.get("capabilities", {})
        if (
            current_id in CLI_SESSION_HEALTH_PROVIDERS
            and capabilities.get("health") is True
            and current_id not in health_by_id
        ):
            health_by_id[current_id] = (cli_session_health_runner or check_cli_session_health)(
                provider, live=live
            )
        usage_runner = usage_adapters.get(current_id)
        if current_id not in usage_by_id and capabilities.get("usage") is True and usage_runner is not None:
            usage_by_id[current_id] = usage_runner(live=live)
        if (
            current_id == "github"
            and capabilities.get("health") is True
            and current_id in usage_by_id
        ):
            health_by_id[current_id] = _github_health(usage_by_id[current_id])

        billing_runner = billing_adapters.get(current_id)
        if capabilities.get("billing") is True and billing_runner is not None:
            billing_by_id[current_id] = billing_runner(live=live)
        elif current_id == "github" and capabilities.get("billing") is True:
            github_usage = usage_by_id.get(current_id)
            if github_usage is not None:
                billing_by_id[current_id] = _github_billing(github_usage)

        if current_id == "vercel" and capabilities.get("usage") is True:
            vercel_billing = billing_by_id.get(current_id)
            if vercel_billing is not None:
                usage_by_id[current_id] = _vercel_usage(vercel_billing)

    financial_snapshot = (snapshot_runner or build_snapshot)(live=False)
    costs = financial_snapshot.get("costs")
    # The SQLite cost store was retired 2026-10-11; no snapshot source reports it any more.
    cost_errors: list[dict[str, Any]] = []
    cost_status = costs.get("status", "unavailable") if isinstance(costs, dict) else "unavailable"
    cost_data = costs if cost_status == "available" else None

    normalized_providers: list[dict[str, Any]] = []
    for provider in selected:
        current_id = provider["id"]
        capabilities = {
            "health": _adapter_capability(health_by_id[current_id])
            if current_id in health_by_id
            else _unavailable_capability(provider, "health"),
            "inventory": _adapter_capability(inventory_by_id[current_id])
            if current_id in inventory_by_id
            else _unavailable_capability(provider, "inventory"),
            "usage": _adapter_capability(usage_by_id[current_id])
            if current_id in usage_by_id
            else _unavailable_capability(provider, "usage"),
            "billing": _adapter_capability(billing_by_id[current_id])
            if current_id in billing_by_id
            else _unavailable_capability(provider, "billing"),
        }
        if current_id == "github" and current_id in billing_by_id:
            capabilities["billing"] = billing_by_id[current_id]
        normalized_providers.append(
            {
                "id": current_id,
                "name": provider["name"],
                "category": provider["category"],
                "adapter_status": provider["adapter_status"],
                "write_policy": provider["write_policy"],
                "capabilities": provider["capabilities"],
                "health": capabilities["health"],
                "inventory": capabilities["inventory"],
                "usage": capabilities["usage"],
                "billing": capabilities["billing"],
                "auth_sources": _auth_sources(capabilities),
                "project_usage": provider.get("project_usage", []),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "live" if live else "offline",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "read_only": registry.get("read_only_by_default") is True,
        "write_actions_enabled": registry.get("write_actions_enabled") is True,
        "write_entrypoint": registry.get("write_entrypoint"),
        "provider_filter": provider_id,
        "providers": normalized_providers,
        "costs": {
            "status": cost_status,
            "scope": "local finance ledger aggregate; not attributed to individual providers",
            "provider_attributed": False,
            "data": cost_data,
            "errors": cost_errors,
        },
        "summary": {
            "providers": len(normalized_providers),
            "health_adapters": sum(item["health"]["implemented"] for item in normalized_providers),
            "inventory_adapters": sum(item["inventory"]["implemented"] for item in normalized_providers),
            "usage_adapters": sum(item["usage"]["implemented"] for item in normalized_providers),
            "billing_adapters": sum(item["billing"]["implemented"] for item in normalized_providers),
            "provider_cost_adapters": sum(
                item["billing"].get("status") in {"healthy", "available"}
                for item in normalized_providers
            ),
        },
    }


def _format_number(value: Any, *, precision: int = 2) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return f"{number:,.{precision}f}"


def _currency_totals(value: Any) -> str:
    if not isinstance(value, dict):
        return "none recorded"
    totals = []
    for currency, amount in sorted(value.items()):
        formatted = _format_number(amount)
        if formatted is not None and isinstance(currency, str):
            totals.append(f"{currency} {formatted}")
    return ", ".join(totals) if totals else "none recorded"


def _inventory_label(provider: dict[str, Any]) -> str:
    capability = provider["inventory"]
    data = capability.get("data")
    summary = data.get("summary") if isinstance(data, dict) else None
    resources = summary.get("resources") if isinstance(summary, dict) else None
    count = _format_number(resources, precision=0)
    if capability["status"] in {"healthy", "available"} and count is not None:
        return f"{capability['status']} ({count} resources)"
    return capability["status"]


def _usage_label(provider: dict[str, Any]) -> str:
    capability = provider["usage"]
    data = capability.get("data")
    if capability["status"] not in {"healthy", "available"} or not isinstance(data, dict):
        return capability["status"]
    if provider["id"] == "cloudflare":
        amount = _format_number(data.get("payload_gib"))
        if amount is not None:
            return f"{capability['status']} ({amount} GiB)"
    if provider["id"] == "github":
        minutes = _format_number(data.get("actions_minutes"), precision=0)
        if minutes is not None:
            return f"{capability['status']} ({minutes} minutes)"
    if provider["id"] == "vercel":
        charges = _format_number(data.get("charge_count"), precision=0)
        if charges is not None:
            return f"{capability['status']} ({charges} charges)"
    return capability["status"]


def _billing_label(provider: dict[str, Any]) -> str:
    capability = provider["billing"]
    data = capability.get("data")
    if (
        provider["id"] == "vercel"
        and capability["status"] in {"healthy", "available"}
        and isinstance(data, dict)
        and isinstance(data.get("billed_cost_by_currency"), dict)
    ):
        totals = _currency_totals(data["billed_cost_by_currency"])
        if totals != "none recorded":
            return f"{capability['status']} ({totals})"
    if provider["id"] == "github" and capability["status"] == "usage_only":
        return "usage only (no charge amount)"
    return capability["status"]


def _print_status_table(payload: dict[str, Any]) -> None:
    print(f"Cloud status ({payload['mode']}, read-only)")
    print("PROVIDER      HEALTH           INVENTORY        USAGE            BILLING / COST")
    for provider in payload["providers"]:
        print(
            f"{provider['id']:<13} "
            f"{provider['health']['status']:<16} "
            f"{_inventory_label(provider):<16} "
            f"{_usage_label(provider):<16} "
            f"{_billing_label(provider)}"
        )
    costs = payload["costs"]
    cost_data = costs.get("data")
    if costs.get("status") == "available" and isinstance(cost_data, dict):
        planned = _currency_totals(cost_data.get("planned_monthly_equivalent"))
        paid = _currency_totals(cost_data.get("actual_paid_by_currency"))
        print(
            f"Local ledger: {costs['status']}; planned/month {planned}; "
            f"paid {paid} (aggregate, not provider-attributed)"
        )
    else:
        print(f"Local ledger: {costs['status']} ({costs['scope']})")
    print(
        f"Writes enabled: {str(payload['write_actions_enabled']).lower()}; "
        f"write entrypoint: {payload['write_entrypoint']}"
    )


def _print_tools_table(payload: dict[str, Any]) -> None:
    print("Cloud provider CLI tools (read-only; no installation attempted)")
    print("PROVIDER      BINARY       STATUS       VERSION       RECOMMENDATION")
    for tool in payload["tools"]:
        recommendation = tool.get("recommendation") or {}
        next_step = recommendation.get("command") or recommendation.get("url") or "-"
        print(
            f"{tool['provider_id']:<13} "
            f"{str(tool['binary'] or '-'):<12} "
            f"{tool['status']:<12} "
            f"{str(tool.get('version') or tool.get('version_status', '-')):<13} "
            f"{next_step}"
        )
    summary = payload["summary"]
    print(
        f"Installed: {summary['installed']}/{summary['registered_tools']}; "
        f"missing: {summary['missing']}"
    )


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "doctor"), nargs="?", default="status")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="make bounded read-only provider API calls")
    mode.add_argument("--offline", action="store_true", help="use local data only (default)")
    parser.add_argument("--provider", help="limit output and live calls to one provider ID")
    parser.add_argument("--json", action="store_true", help="print the normalized JSON result")
    parser.add_argument("--check-versions", action="store_true", help="doctor: run fixed version-only commands")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        if args.live or args.provider:
            parser.error("doctor does not accept --live or --provider")
        try:
            payload = build_tools_doctor(check_versions=args.check_versions)
        except CloudProviderRegistryError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_tools_table(payload)
        return 1 if payload["summary"]["version_errors"] else 0
    if args.check_versions:
        parser.error("--check-versions is only supported by doctor")
    try:
        payload = build_status(live=args.live, provider_id=args.provider)
    except CloudProviderRegistryError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        parser.error(str(exc.args[0]))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_status_table(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

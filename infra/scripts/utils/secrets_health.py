#!/usr/bin/env python3
"""
Secrets provider health check — never prints secret values.

Reports whether Infisical, Vault, or env can resolve the named Google
pipeline credentials. Output is status/provider/names only.

Usage:
    python3 secrets_health.py
    python3 secrets_health.py --json
    python3 secrets_health.py --names GOOGLE_CLIENT_ID,GOOGLE_REFRESH_TOKEN
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform"))
from _env import load_env  # noqa: E402
from cloud_providers import (  # noqa: E402
    CloudProviderRegistryError, auth_env_names, load_registry, secret_names,
)
from platform_output import configure_utf8_stdio  # noqa: E402

load_env()

from secret_resolver import (  # noqa: E402
    GOOGLE_PIPELINE_SECRETS,
    STATUS_OK,
    probe_secrets,
)


REPORT_FIELDS = {"schema_version", "status", "mode", "provider", "provider_configured", "fallback_env"}
SECRET_FIELDS = {"name", "status", "error_class"}


def _sanitize_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: report[key] for key in REPORT_FIELDS if key in report},
        "secrets": [
            {key: item[key] for key in SECRET_FIELDS if key in item}
            for item in report.get("secrets", []) if isinstance(item, dict)
        ],
    }


def cloud_secrets_health(
    *,
    provider_id: str | None = None,
    offline: bool = False,
    registry: dict[str, Any] | None = None,
    probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    registry = load_registry() if registry is None else registry
    providers = registry["providers"]
    if provider_id is not None:
        providers = [provider for provider in providers if provider["id"] == provider_id]
        if not providers:
            raise KeyError(f"Unknown cloud provider: {provider_id}")
    provider_names = {
        provider["id"]: list(dict.fromkeys(auth_env_names(provider) + secret_names(provider)))
        for provider in providers
    }
    names = list(dict.fromkeys(name for group in provider_names.values() for name in group))
    if offline:
        report = {
            "status": "not_checked", "mode": "offline", "provider": "not_checked",
            "secrets": [{"name": name, "status": "not_checked"} for name in names],
        }
    else:
        report = _sanitize_report((probe or probe_secrets)(names))
    indexed = {item["name"]: item for item in report["secrets"]}
    output_providers = []
    for provider in providers:
        secrets = []
        for name in provider_names[provider["id"]]:
            item = indexed.get(name, {"name": name, "status": "provider_unreachable"})
            status = item["status"]
            presence = (
                "present" if status == STATUS_OK
                else "missing" if status in {"secret_not_found", "credentials_not_configured"}
                else "not_checked" if offline else "unavailable"
            )
            secrets.append({**item, "presence": presence})
        output_providers.append({"id": provider["id"], "secrets": secrets})
    return {
        **report,
        "schema_version": "solo-empire.cloud-secrets-health.v1",
        "read_only": True,
        "scope": "secret-name presence only; not provider authentication or CLI session health",
        "providers": output_providers,
    }


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Check secret provider readiness without printing secret values"
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--names",
        default=None,
        help="Comma-separated secret names to probe (default: Google pipeline set)",
    )
    parser.add_argument("--cloud", action="store_true", help="probe names from the cloud provider registry")
    parser.add_argument("--provider", help="limit --cloud to one provider")
    parser.add_argument("--offline", action="store_true", help="--cloud: list names without reading secrets")
    args = parser.parse_args(argv)
    if args.cloud:
        if args.names is not None:
            parser.error("--cloud cannot be combined with --names")
        try:
            report = cloud_secrets_health(provider_id=args.provider, offline=args.offline)
        except (CloudProviderRegistryError, KeyError) as exc:
            parser.error(str(exc.args[0]))
    else:
        if args.provider or args.offline:
            parser.error("--provider and --offline require --cloud")
        names = tuple(n.strip() for n in (args.names or ",".join(GOOGLE_PIPELINE_SECRETS)).split(",") if n.strip())
        report = _sanitize_report(probe_secrets(names))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print("Secrets health")
        print(f"  status:   {report['status']}")
        print(f"  mode:     {report['mode']}")
        print(f"  provider: {report['provider']}")
        if "provider_configured" in report:
            print(f"  configured: {report['provider_configured']}")
            print(f"  fallback_env: {report['fallback_env']}")
        print("  secrets:")
        for item in report["secrets"]:
            extra = f" ({item['error_class']})" if item.get("error_class") else ""
            print(f"    - {item['name']}: {item['status']}{extra}")
        for provider in report.get("providers", []):
            print(f"  {provider['id']}: " + ", ".join(
                f"{item['name']}={item['presence']}" for item in provider["secrets"]
            ))

    return 0 if report["status"] in {STATUS_OK, "not_checked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

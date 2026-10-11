#!/usr/bin/env python3
"""Read-only health inventory for the local self-hosted platform.

The adapter inspects the existing Docker Compose lab and local Kubernetes
cluster. It never runs a Docker, kubectl, Helm, or k3d mutation command and it
does not return container environments, mounts, ports, logs, or raw API
payloads.

Usage:
    python3 self_hosted_health.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from platform_output import configure_utf8_stdio  # noqa: E402


SCHEMA_VERSION = "solo-empire.self-hosted-health.v1"
SELF_HOSTED_PREFIXES = ("se-", "solo-empire-frappe-", "k3d-solo-")


def _run(command: list[str], timeout: int = 15) -> dict[str, Any]:
    """Run a bounded read-only command without returning stderr."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "error_class": "command_not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "error_class": "command_timeout"}
    if completed.returncode != 0:
        return {"ok": False, "stdout": "", "error_class": "command_failed"}
    return {"ok": True, "stdout": completed.stdout, "error_class": None}


def _container_state(entry: dict[str, Any]) -> str:
    text = str(entry.get("Status") or entry.get("State") or "").lower()
    if "unhealthy" in text:
        return "unhealthy"
    if "healthy" in text:
        return "healthy"
    if str(entry.get("State") or "").lower() == "running" or text.startswith("up "):
        return "running"
    return "unknown"


def docker_report() -> dict[str, Any]:
    result = _run(["docker", "ps", "--format", "{{json .}}"])
    if not result["ok"]:
        return {
            "id": "docker-compose",
            "name": "Docker Compose self-hosted lab",
            "status": "unavailable",
            "available": False,
            "error_class": result["error_class"],
            "summary": {"running": 0, "healthy": 0, "unhealthy": 0, "self_hosted": 0},
            "containers": [],
        }

    containers: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in str(result["stdout"]).splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Names") or "")
        if not name.startswith(SELF_HOSTED_PREFIXES):
            continue
        state = _container_state(entry)
        containers.append({"name": name, "state": state})

    summary = {
        "running": len(containers),
        "healthy": sum(item["state"] == "healthy" for item in containers),
        "unhealthy": sum(item["state"] == "unhealthy" for item in containers),
        "self_hosted": len(containers),
    }
    status = "degraded" if summary["unhealthy"] or invalid_lines else "healthy" if containers else "empty"
    report: dict[str, Any] = {
        "id": "docker-compose",
        "name": "Docker Compose self-hosted lab",
        "status": status,
        "available": True,
        "summary": summary,
        "containers": containers,
    }
    if invalid_lines:
        report["invalid_lines"] = invalid_lines
    return report


def _node_roles(labels: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    for key, value in labels.items():
        if key.startswith("node-role.kubernetes.io/"):
            role = key.split("/", 1)[1] or str(value)
            if role not in roles:
                roles.append(role)
    if not roles and labels.get("kubernetes.io/role"):
        roles.append(str(labels["kubernetes.io/role"]))
    return sorted(roles)


def kubernetes_report() -> dict[str, Any]:
    nodes_result = _run(["kubectl", "get", "nodes", "-o", "json"])
    if not nodes_result["ok"]:
        return {
            "id": "k3s",
            "name": "Local K3s/k3d cluster",
            "status": "unavailable",
            "available": False,
            "error_class": nodes_result["error_class"],
            "summary": {"nodes": 0, "ready": 0, "argo_apps": 0, "argo_healthy": 0},
            "nodes": [],
            "applications": [],
        }

    try:
        nodes_payload = json.loads(nodes_result["stdout"])
    except json.JSONDecodeError:
        nodes_payload = None
    if not isinstance(nodes_payload, dict) or not isinstance(nodes_payload.get("items"), list):
        return {
            "id": "k3s",
            "name": "Local K3s/k3d cluster",
            "status": "degraded",
            "available": True,
            "error_class": "kubectl_invalid_json",
            "summary": {"nodes": 0, "ready": 0, "argo_apps": 0, "argo_healthy": 0},
            "nodes": [],
            "applications": [],
        }

    nodes: list[dict[str, Any]] = []
    for item in nodes_payload["items"]:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        conditions = status.get("conditions") if isinstance(status.get("conditions"), list) else []
        ready = any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        )
        nodes.append(
            {
                "name": metadata.get("name"),
                "ready": ready,
                "roles": _node_roles(metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}),
                "kubelet_version": status.get("nodeInfo", {}).get("kubeletVersion")
                if isinstance(status.get("nodeInfo"), dict)
                else None,
            }
        )

    apps_result = _run(["kubectl", "get", "applications.argoproj.io", "-A", "-o", "json"])
    applications: list[dict[str, Any]] = []
    apps_error: str | None = None
    if apps_result["ok"]:
        try:
            apps_payload = json.loads(apps_result["stdout"])
        except json.JSONDecodeError:
            apps_payload = None
            apps_error = "argocd_invalid_json"
        if isinstance(apps_payload, dict) and isinstance(apps_payload.get("items"), list):
            for item in apps_payload["items"]:
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                status = item.get("status") if isinstance(item.get("status"), dict) else {}
                health = status.get("health") if isinstance(status.get("health"), dict) else {}
                sync = status.get("sync") if isinstance(status.get("sync"), dict) else {}
                applications.append(
                    {
                        "namespace": metadata.get("namespace"),
                        "name": metadata.get("name"),
                        "sync": sync.get("status"),
                        "health": health.get("status"),
                    }
                )
    else:
        apps_error = str(apps_result["error_class"])

    summary = {
        "nodes": len(nodes),
        "ready": sum(node["ready"] for node in nodes),
        "argo_apps": len(applications),
        "argo_healthy": sum(app["health"] == "Healthy" for app in applications),
    }
    report = {
        "id": "k3s",
        "name": "Local K3s/k3d cluster",
        "status": "healthy" if nodes and summary["ready"] == len(nodes) else "degraded",
        "available": True,
        "summary": summary,
        "nodes": nodes,
        "applications": applications,
    }
    if apps_error:
        report["applications_error_class"] = apps_error
    return report


def run_health(*, docker: bool = True, kubernetes: bool = True) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    if docker:
        components.append(docker_report())
    if kubernetes:
        components.append(kubernetes_report())
    unavailable = sum(component["status"] == "unavailable" for component in components)
    degraded = sum(component["status"] == "degraded" for component in components)
    healthy = sum(component["status"] == "healthy" for component in components)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "local-live",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "execution_enabled": False,
        "write_attempted": False,
        "components": components,
        "summary": {
            "components": len(components),
            "healthy": healthy,
            "degraded": degraded,
            "unavailable": unavailable,
            "docker_running": sum(component.get("summary", {}).get("running", 0) for component in components),
            "kubernetes_ready": sum(component.get("summary", {}).get("ready", 0) for component in components),
        },
        "status": "unavailable" if unavailable == len(components) and bool(components) else "degraded" if unavailable or degraded else "healthy",
    }


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Read-only local self-hosted health inventory")
    parser.add_argument("--docker-only", action="store_true")
    parser.add_argument("--k8s-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.docker_only and args.k8s_only:
        parser.error("--docker-only and --k8s-only cannot be combined")
    payload = run_health(docker=not args.k8s_only, kubernetes=not args.docker_only)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

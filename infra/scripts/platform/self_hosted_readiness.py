#!/usr/bin/env python3
"""Read-only readiness checks for a local self-hosted PaaS candidate.

This report is a preflight only. It does not install, stop, restart, remove,
apply, or alter Docker, Kubernetes, firewall, DNS, or host configuration.
It is deliberately local: a remote host must be selected and checked through
an explicit, separately approved workflow.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from platform_output import configure_utf8_stdio  # noqa: E402
from self_hosted_health import _run, docker_report, kubernetes_report


SCHEMA_VERSION = "solo-empire.self-hosted-readiness.v1"
REPO_ROOT = SCRIPT_DIR.parents[2]
LISTENING_PORT_RE = re.compile(r"(?::|\])([0-9]+)$")


INSTALL_PROFILES: dict[str, dict[str, Any]] = {
    "coolify": {
        "name": "Coolify",
        "ports": [22, 80, 443, 6001, 6002, 8000],
        "minimum": {"cpu_cores": 2, "memory_gib": 2, "free_disk_gib": 30},
        "fresh_host_recommended": True,
        "source": "https://coolify.io/docs/get-started/installation",
    },
    "caprover": {
        "name": "CapRover",
        "ports": [80, 443, 3000, 996],
        "minimum": {"cpu_cores": None, "memory_gib": None, "free_disk_gib": None},
        "fresh_host_recommended": True,
        "source": "https://caprover.com/docs/firewall.html",
    },
    "dokku": {
        "name": "Dokku",
        "ports": [22, 80, 443],
        "minimum": {"cpu_cores": None, "memory_gib": 1, "free_disk_gib": None},
        "fresh_host_recommended": True,
        "source": "https://dokku.com/docs/getting-started/installation/",
    },
}


def _memory_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _disk_bytes() -> int | None:
    try:
        return int(shutil.disk_usage(REPO_ROOT).free)
    except OSError:
        return None


def _docker_runtime() -> dict[str, Any]:
    result = _run(["docker", "version", "--format", "{{json .Server}}"])
    if not result["ok"]:
        return {"available": False, "error_class": result["error_class"]}
    try:
        payload = json.loads(str(result["stdout"]).strip())
    except json.JSONDecodeError:
        payload = {}
    return {
        "available": True,
        "server_version": payload.get("Version") if isinstance(payload, dict) else None,
    }


def _compose_runtime() -> dict[str, Any]:
    result = _run(["docker", "compose", "version", "--short"])
    return {
        "available": bool(result["ok"]),
        "version": str(result["stdout"]).strip() if result["ok"] else None,
        **({} if result["ok"] else {"error_class": result["error_class"]}),
    }


def _scope(address: str) -> str:
    normalized = address.strip("[]")
    if normalized in {"*", "0.0.0.0", "::"}:
        return "wildcard"
    try:
        parsed = ipaddress.ip_address(normalized.split("%", 1)[0])
    except ValueError:
        return "unknown"
    if parsed.is_loopback:
        return "loopback"
    if parsed.is_private or parsed.is_link_local:
        return "private"
    return "public"


def _parse_listening_ports(stdout: str) -> list[dict[str, Any]]:
    ports: dict[tuple[int, str], dict[str, Any]] = {}
    for line in stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        local_address = fields[3]
        match = LISTENING_PORT_RE.search(local_address)
        if not match:
            continue
        try:
            port = int(match.group(1))
        except ValueError:
            continue
        address = local_address[: match.start()].rstrip(":") or "*"
        key = (port, _scope(address))
        ports[key] = {"port": port, "scope": key[1]}
    return sorted(ports.values(), key=lambda item: (item["port"], item["scope"]))


def _listening_ports() -> dict[str, Any]:
    result = _run(["ss", "-ltnH"])
    if not result["ok"]:
        return {"available": False, "ports": [], "error_class": result["error_class"]}
    ports = _parse_listening_ports(str(result["stdout"]))
    return {"available": True, "ports": ports}


def _backup_evidence() -> dict[str, Any]:
    paths = {
        "backup_runbook": REPO_ROOT / "operations/platform/HOMELAB-K3S-RUNBOOK.md",
        "backup_directory": REPO_ROOT / "data/backups",
    }
    return {
        "contract_files_present": all(path.exists() for key, path in paths.items() if key != "backup_directory"),
        "backup_artifacts_present": paths["backup_directory"].is_dir()
        and any(paths["backup_directory"].iterdir()),
    }


def _host_snapshot() -> dict[str, Any]:
    memory = _memory_bytes()
    disk = _disk_bytes()
    return {
        "os": platform.system(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_cores": os.cpu_count(),
        "memory_gib": round(memory / 1024**3, 2) if memory is not None else None,
        "free_disk_gib": round(disk / 1024**3, 2) if disk is not None else None,
        "docker": _docker_runtime(),
        "docker_compose": _compose_runtime(),
        "listening_ports": _listening_ports(),
        "backup_evidence": _backup_evidence(),
    }


def _port_conflicts(profile: dict[str, Any], ports: dict[str, Any]) -> list[int]:
    listening = {int(item["port"]) for item in ports.get("ports", [])}
    return [port for port in profile["ports"] if port in listening]


def evaluate_profile(
    profile_id: str,
    *,
    host: dict[str, Any],
    docker: dict[str, Any],
    kubernetes: dict[str, Any],
    existing_container_count: int,
) -> dict[str, Any]:
    profile = INSTALL_PROFILES[profile_id]
    minimum = profile["minimum"]
    conflicts = _port_conflicts(profile, host.get("listening_ports", {}))
    blockers: list[str] = []
    if conflicts:
        blockers.append("port-conflict")
    if profile["fresh_host_recommended"] and existing_container_count:
        blockers.append("existing-workloads-on-host")
    if not host.get("docker", {}).get("available"):
        blockers.append("docker-unavailable")
    if minimum["cpu_cores"] and (host.get("cpu_cores") or 0) < minimum["cpu_cores"]:
        blockers.append("insufficient-cpu")
    if minimum["memory_gib"] and (host.get("memory_gib") or 0) < minimum["memory_gib"]:
        blockers.append("insufficient-memory")
    if minimum["free_disk_gib"] and (host.get("free_disk_gib") or 0) < minimum["free_disk_gib"]:
        blockers.append("insufficient-disk")
    blockers.extend(["target-host-not-selected", "domain-and-ingress-not-approved"])
    return {
        "id": profile_id,
        "name": profile["name"],
        "status": "blocked" if blockers else "ready_for_review",
        "ports": profile["ports"],
        "conflicting_ports": conflicts,
        "blockers": sorted(set(blockers)),
        "minimum": minimum,
        "source": profile["source"],
        "readiness_notes": [
            "This is a preflight report only; no installation or host mutation was attempted.",
            f"Docker containers detected in the local boundary: {existing_container_count}.",
            f"K3s status: {kubernetes.get('status', 'unknown')}.",
            f"Docker status: {docker.get('status', 'unknown')}.",
        ],
    }


def run_readiness(profile_id: str | None = None) -> dict[str, Any]:
    host = _host_snapshot()
    docker = docker_report()
    kubernetes = kubernetes_report()
    selected = [profile_id] if profile_id else list(INSTALL_PROFILES)
    profiles = [
        evaluate_profile(
            item,
            host=host,
            docker=docker,
            kubernetes=kubernetes,
            existing_container_count=int(docker.get("summary", {}).get("self_hosted", 0)),
        )
        for item in selected
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "local-preflight",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "execution_enabled": False,
        "write_attempted": False,
        "status": "blocked" if any(item["status"] == "blocked" for item in profiles) else "ready_for_review",
        "host": host,
        "self_hosted": {"docker": docker, "kubernetes": kubernetes},
        "profiles": profiles,
        "summary": {
            "profiles": len(profiles),
            "blocked": sum(item["status"] == "blocked" for item in profiles),
            "ready_for_review": sum(item["status"] == "ready_for_review" for item in profiles),
            "listening_ports": len(host.get("listening_ports", {}).get("ports", [])),
        },
    }


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Read-only self-hosted PaaS readiness preflight")
    parser.add_argument("--profile", choices=sorted(INSTALL_PROFILES))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(run_readiness(args.profile), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

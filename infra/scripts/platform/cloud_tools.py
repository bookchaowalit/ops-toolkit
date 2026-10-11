"""Registry-driven local cloud CLI diagnostics; never installs or logs in."""

from __future__ import annotations

import re
import os
import shutil
import subprocess
from typing import Any, Callable

from cloud_providers import load_registry


VERSION_PATTERN = re.compile(r"(?<![\w.-])v?(\d{1,5}\.\d{1,5}(?:\.\d{1,5})?)(?![\w.])")
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def build_tools_doctor(
    *,
    registry: dict[str, Any] | None = None,
    which: Callable[[str], str | None] | None = None,
    check_versions: bool = False,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    registry = load_registry() if registry is None else registry
    find_binary = shutil.which if which is None else which
    runner = subprocess.run if process_runner is None else process_runner
    environment = os.environ.copy()
    environment.update({
        "WRANGLER_SEND_METRICS": "false",
        "VERCEL_TELEMETRY_DISABLED": "1",
        "HEROKU_DISABLE_AUTOUPDATE": "1",
        "HEROKU_SKIP_ANALYTICS": "1",
        "NO_COLOR": "1",
    })
    tools = []
    for provider in registry["providers"]:
        binary = provider.get("cli_binary")
        path = find_binary(binary) if binary else None
        tool = {
            "provider_id": provider["id"],
            "provider": provider["name"],
            "binary": binary,
            "status": "installed" if path else "missing" if binary else "not_registered",
            "version_status": "not_checked",
            "version": None,
            "recommendation": provider.get("cli_install") if binary and not path else None,
        }
        if check_versions and path:
            command = provider.get("cli_version_command")
            timeout = provider.get("cli_version_timeout_seconds", 8)
            if (
                not isinstance(command, list) or len(command) != 2
                or command[0] != binary or command[1] not in {"--version", "version"}
            ):
                tool["version_status"] = "error"
                tool["error_class"] = "invalid_cli_version_command"
            elif isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 60:
                tool["version_status"] = "error"
                tool["error_class"] = "invalid_cli_version_timeout"
            else:
                try:
                    result = runner(
                        [path, command[1]],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout,
                        check=False,
                        env=environment,
                    )
                    if result.returncode != 0:
                        tool["version_status"] = "error"
                        tool["error_class"] = "cli_version_failed"
                    else:
                        match = VERSION_PATTERN.search(
                            ANSI_PATTERN.sub(
                                "", (result.stdout or "")[:4096] + "\n" + (result.stderr or "")[:4096]
                            )
                        )
                        tool["version_status"] = "ok" if match else "unrecognized"
                        tool["version"] = match.group(1) if match else None
                except subprocess.TimeoutExpired:
                    tool["version_status"] = "error"
                    tool["error_class"] = "cli_version_timeout"
                except OSError:
                    tool["version_status"] = "error"
                    tool["error_class"] = "cli_version_unavailable"
        tools.append(tool)
    return {
        "schema_version": "solo-empire.cloud-tools-doctor.v1",
        "read_only": True,
        "install_attempted": False,
        "versions_checked": check_versions,
        "summary": {
            "providers": len(tools),
            "registered_tools": sum(bool(item["binary"]) for item in tools),
            "installed": sum(item["status"] == "installed" for item in tools),
            "missing": sum(item["status"] == "missing" for item in tools),
            "version_errors": sum(item["version_status"] == "error" for item in tools),
        },
        "tools": tools,
    }

#!/usr/bin/env python3
"""Small, non-printing Infisical write helper for interactive rotations.

Secret values are passed to the Infisical CLI through a short-lived mode-0600
file instead of command-line arguments, so they do not appear in ``ps`` or
shell history.  The file is removed on every exit path and CLI output is
never returned to callers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from secret_resolver import DEFAULT_INFISICAL_PROJECT_ID


class InfisicalSecretWriteError(RuntimeError):
    """Safe error class for a failed secret-manager write."""


def store_secret(name: str, value: str, *, path: str | None = None) -> None:
    """Create or replace one Infisical secret without exposing its value."""
    if not name or "=" in name or "\n" in name:
        raise InfisicalSecretWriteError("invalid_secret_name")
    if not value or "\n" in value or "\r" in value:
        raise InfisicalSecretWriteError("invalid_secret_value")

    binary = shutil.which(os.environ.get("INFISICAL_CLI", "infisical"))
    if not binary:
        raise InfisicalSecretWriteError("infisical_cli_unavailable")

    secret_path = path or os.environ.get("INFISICAL_SECRET_PATH", "/google")
    environment = os.environ.get("INFISICAL_ENVIRONMENT", "dev")
    project_id = os.environ.get("INFISICAL_PROJECT_ID", DEFAULT_INFISICAL_PROJECT_ID)
    fd, temp_name = tempfile.mkstemp(prefix=".infisical-secret-", text=True)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
        command = [
            binary, "secrets", "set", "--file", str(temp_path),
            "--env", environment, "--path", secret_path,
            "--projectId", project_id, "--silent",
        ]
        child_env = os.environ.copy()
        child_env["INFISICAL_DISABLE_UPDATE_CHECK"] = "true"
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=child_env,
        )
        if result.returncode != 0:
            raise InfisicalSecretWriteError("infisical_secret_write_failed")
    except (OSError, subprocess.TimeoutExpired):
        raise InfisicalSecretWriteError("infisical_secret_write_failed") from None
    finally:
        temp_path.unlink(missing_ok=True)


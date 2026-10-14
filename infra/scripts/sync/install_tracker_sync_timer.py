#!/usr/bin/env python3
"""Install the pilot's local user timer only after credential preflight passes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
UNIT_NAMES = ("tracker-sync-pilot.service", "tracker-sync-pilot.timer")


def unit_quote(value: str) -> str:
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("invalid_unit_path")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def render_units(root: Path, python: str) -> dict[str, str]:
    templates = root / "infra/templates/tracker-sync"
    result = {}
    for name in UNIT_NAMES:
        text = (templates / name).read_text()
        # Quote complete argument/path values rather than a path prefix.
        lines = []
        for line in text.splitlines():
            if line.startswith("WorkingDirectory="):
                # This directive takes a path, not the ExecStart argument grammar.
                line = "WorkingDirectory=" + str(root).replace("%", "%%")
            elif line.startswith("ExecStart="):
                args = [python, str(root / "infra/scripts/sync/tracker_sync.py"),
                        "--config", str(root / "infra/platform/tracker-sync-pilot.json"),
                        "--state-dir", str(root / ".runtime/tracker-sync-pilot"),
                        "--apply", "--json", "--quiet"]
                line = "ExecStart=" + " ".join(unit_quote(v) for v in args)
            lines.append(line)
        result[name] = "\n".join(lines) + "\n"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true", help="Install and enable after preflight")
    args = parser.parse_args()
    try:
        preflight = subprocess.run(
            [sys.executable, str(ROOT / "infra/scripts/sync/tracker_sync.py"),
             "--config", str(ROOT / "infra/platform/tracker-sync-pilot.json"), "--preflight", "--json"],
            capture_output=True, text=True, timeout=45, check=False)
        if preflight.returncode:
            # Worker stdout is its bounded, privacy-safe report; never print stderr.
            try:
                report = json.loads(preflight.stdout)
            except ValueError:
                report = {"status": "blocked", "code": "preflight_failed"}
            print(json.dumps({"activated": False, "preflight": report}))
            return 2
        units = render_units(ROOT, sys.executable)
        with tempfile.TemporaryDirectory(prefix="tracker-sync-units-") as temporary:
            paths = []
            for name, content in units.items():
                path = Path(temporary) / name
                path.write_text(content)
                paths.append(str(path))
            validated = subprocess.run(["systemd-analyze", "--user", "verify", *paths],
                                       capture_output=True, timeout=15, check=False)
            if validated.returncode:
                raise ValueError("unit_validation_failed")
        if not args.activate:
            print(json.dumps({"activated": False, "status": "ready", "units_valid": True}))
            return 0
        # Credential presence is insufficient: verify live reads and the current
        # pilot plan without mutating provider data before enabling a schedule.
        preview = subprocess.run(
            [sys.executable, str(ROOT / "infra/scripts/sync/tracker_sync.py"),
             "--config", str(ROOT / "infra/platform/tracker-sync-pilot.json"),
             "--state-dir", str(ROOT / ".runtime/tracker-sync-pilot"), "--json"],
            capture_output=True, text=True, timeout=90, check=False)
        if preview.returncode:
            try:
                report = json.loads(preview.stdout)
            except ValueError:
                report = {"status": "blocked", "code": "live_preview_failed"}
            print(json.dumps({"activated": False, "preview": report}))
            return 2
        target = Path.home() / ".config/systemd/user"
        target.mkdir(parents=True, exist_ok=True)
        for name, content in units.items():
            path = target / name
            if path.is_symlink() or (path.exists() and path.read_text() != content):
                raise ValueError("existing_unit_conflict")
        for name, content in units.items():
            path = target / name
            with tempfile.NamedTemporaryFile(mode="w", dir=target, delete=False) as handle:
                handle.write(content)
                pending = Path(handle.name)
            os.chmod(pending, 0o600)
            os.replace(pending, path)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, timeout=15)
        subprocess.run(["systemctl", "--user", "enable", "--now", "tracker-sync-pilot.timer"],
                       check=True, capture_output=True, timeout=15)
        print(json.dumps({"activated": True, "runtime": "local-user-systemd", "interval_minutes": 15}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"activated": False, "status": "blocked", "code": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

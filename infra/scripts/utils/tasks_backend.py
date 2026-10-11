#!/usr/bin/env python3
"""Show or switch the task backend for every process through the client file.

    tasks_backend.py status
    tasks_backend.py use-api --url https://tasks.example.com

The file (``~/.config/empire-platform/client.env``, or ``$EMPIRE_CLIENT_CONFIG``)
holds only non-secret KEY=VALUE lines and is written 0600 in a 0700 directory.
Tokens are never read, printed, or stored here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_store import ClientConfigError, client_config_path, client_settings, validate_api_url  # noqa: E402


def write_api(path: Path, url: str) -> None:
    if any(ch in url for ch in "\r\n"):
        raise ClientConfigError("--url must be a single line")
    validate_api_url(url)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".client.env.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"SOLO_EMPIRE_TASKS_BACKEND=api\nEMPIRE_TASKS_API={url}\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    use_api = sub.add_parser("use-api")
    use_api.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    try:
        path = client_config_path()
        if args.command == "use-api":
            write_api(path, args.url.strip())
        settings = client_settings()
    except ClientConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.command == "status" and args.json:
        print(json.dumps(settings))
    else:
        print(f"backend={settings['backend']} ({settings['backend_source']})")
        print(f"api_url={settings['api_url']} ({settings['api_url_source']})")
        print(f"config={settings['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

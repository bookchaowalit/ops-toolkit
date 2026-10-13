#!/usr/bin/env python3
"""Provision a scoped client token for the hosted Central AI Gateway.

Run by the owner only. The script generates a high-entropy bearer token for one
subject, stores the raw token in exactly the places requested, and prints ONLY
the API_TOKENS_JSON entry (subject, sha256 token_hash, scopes, optional
expires_at). The raw token is never printed, logged, or passed on a command
line.

Destinations (at least one is required):
  --out-file PATH        write the raw token to a new file with 0600 permissions
                         (refuses to overwrite an existing file)
  --vercel-env NAME      pipe the raw token on stdin to
                         `vercel env add NAME <target>` (default target: production)

The JSON entry is printed only after every requested destination succeeded, so
a failed run leaves no half-registered credential.

Example:
  bash infra/scripts/setup/run-python3.sh infra/scripts/security/provision_ai_gateway_client.py \\
    --subject ev3-web --vercel-env EV3_AI_GATEWAY_TOKEN --vercel-cwd <ev3-repo>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hash_api_token import token_hash  # noqa: E402

# Keep these in sync with the AI gateway auth (bookchaowalit/platform-ai-gateway-api; formerly infra/api/auth.ts).
SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9:_-]{1,63}$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
VERCEL_TARGETS = ("production", "preview", "development")
DEFAULT_SCOPES = ("ai:generate", "api:read")
TOKEN_BYTES = 32


class ProvisionError(Exception):
    """A provisioning step failed; the message never contains the token."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subject", required=True, help="client subject, e.g. ev3-web")
    parser.add_argument(
        "--scopes",
        default=" ".join(DEFAULT_SCOPES),
        help='space- or comma-separated scopes (default: "ai:generate api:read")',
    )
    parser.add_argument("--expires-at", help="optional ISO-8601 expiry, e.g. 2027-03-31T00:00:00Z")
    parser.add_argument("--out-file", type=Path, help="write the raw token to this new 0600 file")
    parser.add_argument("--vercel-env", help="Vercel environment variable name, e.g. EV3_AI_GATEWAY_TOKEN")
    parser.add_argument("--vercel-target", default="production", choices=VERCEL_TARGETS)
    parser.add_argument("--vercel-cwd", type=Path, help="linked Vercel project directory (the EV3 web repo)")
    args = parser.parse_args(argv)

    if not SUBJECT_PATTERN.match(args.subject):
        parser.error("--subject must match [A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
    scopes = [scope for scope in re.split(r"[\s,]+", args.scopes.strip()) if scope]
    if not scopes or len(scopes) > 20 or any(not SCOPE_PATTERN.match(scope) for scope in scopes):
        parser.error("--scopes must contain 1-20 valid scope identifiers")
    if "*" in scopes:
        parser.error("wildcard scope is not allowed for gateway clients")
    args.scopes = list(dict.fromkeys(scopes))
    if args.expires_at:
        try:
            datetime.fromisoformat(args.expires_at.replace("Z", "+00:00"))
        except ValueError:
            parser.error("--expires-at must be an ISO-8601 timestamp")
    if not args.out_file and not args.vercel_env:
        parser.error("choose at least one destination: --out-file and/or --vercel-env")
    if args.vercel_env and not ENV_NAME_PATTERN.match(args.vercel_env):
        parser.error("--vercel-env must be an upper-case environment variable name")
    if args.vercel_cwd and not args.vercel_cwd.is_dir():
        parser.error("--vercel-cwd must be an existing directory")
    return args


def write_token_file(path: Path, token: str) -> None:
    path = path.expanduser()
    if path.exists():
        raise ProvisionError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    # os.open honors the mode on POSIX; enforce it explicitly in case of umask quirks.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def vercel_command() -> list[str]:
    override = os.environ.get("VERCEL_BIN", "").strip()
    if override:
        return shlex.split(override)
    resolved = shutil.which("vercel")
    if not resolved:
        raise ProvisionError("vercel CLI was not found on PATH; install it or use --out-file")
    return [resolved]


def add_vercel_env(name: str, target: str, token: str, cwd: Path | None) -> None:
    command = [*vercel_command(), "env", "add", name, target]
    try:
        result = subprocess.run(
            command,
            input=token,
            text=True,
            capture_output=True,
            cwd=str(cwd) if cwd else None,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProvisionError(f"vercel env add failed to run ({type(error).__name__})") from None
    if result.returncode != 0:
        # Vercel output does not echo the value, but keep only a short tail and
        # scrub the token defensively before surfacing any diagnostics.
        tail = (result.stderr or result.stdout or "").replace(token, "[redacted]").strip().splitlines()[-3:]
        detail = " | ".join(tail) if tail else "no output"
        raise ProvisionError(f"vercel env add exited with {result.returncode}: {detail}")


def token_entry(subject: str, token: str, scopes: list[str], expires_at: str | None) -> dict[str, object]:
    entry: dict[str, object] = {"subject": subject, "token_hash": token_hash(token), "scopes": scopes}
    if expires_at:
        entry["expires_at"] = expires_at
    return entry


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    try:
        if args.out_file:
            write_token_file(args.out_file, token)
            print(f"raw token written to {args.out_file.expanduser()} (mode 0600)", file=sys.stderr)
        if args.vercel_env:
            add_vercel_env(args.vercel_env, args.vercel_target, token, args.vercel_cwd)
            print(f"raw token stored in Vercel env {args.vercel_env} ({args.vercel_target})", file=sys.stderr)
    except ProvisionError as error:
        print(f"provisioning failed: {error}", file=sys.stderr)
        print("no API_TOKENS_JSON entry was emitted; discard any partial destination", file=sys.stderr)
        return 1

    print(
        "add this entry to the API_TOKENS_JSON array of the hosted AI gateway:",
        file=sys.stderr,
    )
    print(json.dumps(token_entry(args.subject, token, args.scopes, args.expires_at), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

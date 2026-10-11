#!/usr/bin/env python3
"""Print a SHA-256 hash for a bearer token without echoing the raw token."""

from __future__ import annotations

import getpass
import hashlib
import sys


def token_hash(token: str) -> str:
    """Return the API_TOKENS_JSON token_hash for a raw bearer token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def main() -> None:
    token = getpass.getpass("Bearer token (input hidden): ").strip() if sys.stdin.isatty() else sys.stdin.read().strip()
    if not token:
        raise SystemExit("token is required")
    print(token_hash(token))


if __name__ == "__main__":
    main()

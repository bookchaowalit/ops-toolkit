#!/usr/bin/env python3
"""Keep isolated (Z3) social identities and account secrets out of Git.

The account registry under ``accounts/`` in the accounts repository may describe public (Z1), business
(Z2) and separated faceless brands (Z2-separated). A strictly isolated persona
(Z3, legacy ``identity_layer: anonymous``) must live only in its own private
vault: a Git record would itself prove who owns it. This guard fails on any new
Z3 record, on email addresses, and on secret-looking fields. Known legacy Z3
records stay explicit warnings until they are migrated out.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
ACCOUNTS_DIR = "accounts"
# The account metadata lives in the bookchaowalit/accounts repository
# (sibling checkout, ../accounts); override with SOLO_ACCOUNTS_ROOT.
ACCOUNTS_REPO = Path(os.environ.get("SOLO_ACCOUNTS_ROOT") or ROOT.parent / "accounts")
TRUST_ZONES = {"z0-root", "z1-public", "z2-business", "z2-separated", "z3-isolated"}
ISOLATED_ZONES = {"z3-isolated", "z0-root"}
ISOLATED_LAYERS = {"anonymous"}
# Paths tolerated as warnings while a Z3 record is migrated to its vault.
LEGACY_ISOLATED: set[str] = set()
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SECRET_KEY = re.compile(
    r"^\s*-?\s*(password|passwd|recovery_codes?|backup_codes?|totp_secret|otp_secret|"
    r"api_key|access_token|refresh_token|cookie|phone|phone_number)\s*:\s*\S",
    re.IGNORECASE | re.MULTILINE,
)


def account_files(root: Path) -> list[Path]:
    base = root / ACCOUNTS_DIR
    if not base.is_dir():
        return []
    return sorted(base.rglob("*.yaml")) + sorted(base.rglob("*.yml"))


def is_isolated(relative: str, data: dict) -> bool:
    if "/anonymous/" in relative:
        return True
    if str(data.get("identity_layer", "")).strip() in ISOLATED_LAYERS:
        return True
    return str(data.get("trust_zone", "")).strip() in ISOLATED_ZONES


def audit(root: Path = ROOT, legacy: set[str] | None = None) -> tuple[list[str], list[str]]:
    legacy = LEGACY_ISOLATED if legacy is None else legacy
    errors: list[str] = []
    warnings: list[str] = []
    for path in account_files(root):
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for match in EMAIL.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            errors.append(f"{relative}:{line}: email address in Git; store a vault reference instead")
        for match in SECRET_KEY.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            errors.append(f"{relative}:{line}: secret-like field '{match.group(1)}' must stay in the vault")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            errors.append(f"{relative}: invalid YAML ({exc.__class__.__name__})")
            continue
        if not isinstance(data, dict) or path.name != "account.yaml":
            continue
        zone = data.get("trust_zone")
        if zone is not None and zone not in TRUST_ZONES:
            errors.append(f"{relative}: unknown trust_zone '{zone}'")
        if is_isolated(relative, data):
            message = f"{relative}: isolated (Z3/Z0) identity must not be stored in Git"
            (warnings if relative in legacy else errors).append(message)
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args()
    errors, warnings = audit(ACCOUNTS_REPO if ACCOUNTS_REPO.is_dir() else ROOT)
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings}, indent=2))
    else:
        for item in warnings:
            print(f"WARN  {item}")
        for item in errors:
            print(f"ERROR {item}")
        print("identity isolation: " + ("ok" if not errors else f"{len(errors)} error(s)")
              + (f", {len(warnings)} legacy warning(s)" if warnings else ""))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

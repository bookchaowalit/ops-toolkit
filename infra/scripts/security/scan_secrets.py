#!/usr/bin/env python3
"""Scan the repository for credential-shaped literals without printing them.

Engine selection:
- ``gitleaks`` on PATH: run ``gitleaks detect --no-git`` over the working tree
  with the repository ``.gitleaks.toml`` and ``--redact``.
- otherwise (or ``--engine builtin``): scan every tracked text file from
  ``git ls-files`` with a small token-shaped pattern set.

Findings are reported as ``path:line: rule`` only; matched values are never
printed. Exit code 1 means at least one finding.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ".gitleaks.toml"
MAX_BYTES = 5 * 1024 * 1024

# Prefix boundary: the token must not continue an identifier or slug.
_B = r"(?<![A-Za-z0-9_-])"
RULES: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern)
    for name, pattern in {
        "aws-access-key-id": _B + r"AKIA[0-9A-Z]{16}(?![0-9A-Z])",
        "github-token": _B + r"gh[pousr]_[A-Za-z0-9]{36,}",
        "github-fine-grained-pat": _B + r"github_pat_[A-Za-z0-9_]{22,}",
        "anthropic-api-key": _B + r"sk-ant-[A-Za-z0-9_-]{20,}",
        "openai-project-key": _B + r"sk-proj-[A-Za-z0-9_-]{20,}",
        "google-api-key": _B + r"AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])",
        "google-oauth-client-secret": _B + r"GOCSPX-[A-Za-z0-9_-]{24,}",
        "shopify-access-token": _B + r"shp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}",
        "gitlab-pat": _B + r"glpat-[A-Za-z0-9_-]{20,}",
        "stripe-secret-key": _B + r"[sr]k_(?:live|test)_[A-Za-z0-9]{20,}",
        "slack-token": _B + r"xox[abprs]-[A-Za-z0-9-]{10,}",
        "firecrawl-api-key": _B + r"fc-[a-f0-9]{32}(?![a-f0-9])",
        "private-key-block": r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY(?: BLOCK)?-----",
        "known-leaked-google-client-id": r"CLIENT_ID = \"4452[0-9]{6,}",
    }.items()
}
# Cheap literal-prefix prefilter (no lookbehind) so most files skip line scans.
PREFILTER = re.compile(
    r"AKIA|gh[pousr]_|github_pat_|sk-(?:ant|proj)-|AIza|GOCSPX-|shp(?:at|ca|pa|ss)_"
    r"|glpat-|[sr]k_(?:live|test)_|xox[abprs]-|fc-[a-f0-9]{32}|PRIVATE KEY|CLIENT_ID = \"4452"
)
BYTES_PREFILTER = re.compile(PREFILTER.pattern.encode())


def load_allowlist(root: Path) -> list[re.Pattern[str]]:
    """Reuse the reviewed line allowlist from .gitleaks.toml."""
    path = root / CONFIG
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [re.compile(item) for item in data.get("allowlist", {}).get("regexes", [])]


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    )
    return [p for p in result.stdout.decode("utf-8").split("\0") if p]


def scan_text(text: str, allowlist: list[re.Pattern[str]]) -> list[tuple[int, str]]:
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not PREFILTER.search(line):
            continue
        if any(allowed.search(line) for allowed in allowlist):
            continue
        for rule, pattern in RULES.items():
            if pattern.search(line):
                findings.append((lineno, rule))
    return findings


def builtin_scan(root: Path, paths: list[str] | None = None) -> list[str]:
    allowlist = load_allowlist(root)
    results = []
    for rel in paths if paths is not None else tracked_files(root):
        path = root / rel
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192] or not BYTES_PREFILTER.search(data):
            continue
        text = data.decode("utf-8", errors="replace")
        results.extend(f"{rel}:{line}: {rule}" for line, rule in scan_text(text, allowlist))
    return results


def gitleaks_scan(root: Path, binary: str) -> int:
    command = [
        binary, "detect", "--no-git", "--source", str(root),
        "--redact", "--no-banner", "--exit-code", "1",
    ]
    if (root / CONFIG).is_file():
        command += ["--config", str(root / CONFIG)]
    return subprocess.run(command, cwd=root, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["auto", "gitleaks", "builtin"], default="auto")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    binary = shutil.which("gitleaks")
    if args.engine == "gitleaks" and not binary:
        print("FAIL: gitleaks not found on PATH", file=sys.stderr)
        return 2
    if args.engine != "builtin" and binary:
        code = gitleaks_scan(args.root, binary)
        print("OK: gitleaks found no secrets" if code == 0 else "FAIL: gitleaks reported findings")
        return code

    findings = builtin_scan(args.root)
    for finding in findings:
        print(finding)
    if findings:
        print(f"FAIL: {len(findings)} credential-shaped literal(s) found (values redacted)")
        return 1
    print("OK: no credential-shaped literals in tracked files (builtin scan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

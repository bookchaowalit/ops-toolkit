#!/usr/bin/env python3
"""Report tracked files that nothing else in the repository references.

The scan is read-only: it never deletes, moves, or stages files. It lists
candidates for a human or agent to verify before any cleanup decision.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = "solo-empire.unused-files.v1"

# Separate data, generated output, and runtime boundaries are never candidates.
EXCLUDED_ROOTS = (
    ".git/", "data/", "artifacts/", "diary/", "node_modules/", ".agents/",
    ".claude/agents/", ".codex/agents/", ".gemini/agents/",
)
# Conventional entrypoints are discovered by tools, not by explicit references.
ENTRYPOINT_NAMES = frozenset({
    "README.md", "SKILL.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "Taskfile.yml",
    "package.json", "pyproject.toml", "requirements.txt", "tsconfig.json",
    "__init__.py", "conftest.py", "openai.yaml", "Dockerfile", "Makefile",
    "CHANGELOG.md", "CODEOWNERS", "index.html", "index.ts", "index.js", "index.md",
})
ENTRYPOINT_PATTERNS = (
    re.compile(r"(^|/)\.[^/]+$"),                       # dotfiles
    re.compile(r"(^|/)\.github/workflows/"),              # CI workflows
    re.compile(r"(^|/)\.github/(hooks|agents)/"),
    re.compile(r"(_test|\.test|\.spec)\.[a-z]+$"),        # tests are run by glob
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)(migrations|fixtures)/"),
)
CODE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".sh", ".ps1"})
TEXT_SUFFIXES = CODE_SUFFIXES | frozenset({
    ".md", ".yml", ".yaml", ".json", ".toml", ".txt", ".html", ".css", ".cfg",
    ".ini", ".mdx", ".sql", ".csv", ".env.example",
})
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")
MAX_TEXT_BYTES = 2_000_000


def _git(args: list[str], root: Path) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=off", *args], cwd=root, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def tracked_files(root: Path) -> list[str]:
    output = _git(["ls-files", "-s", "-z"], root)
    files = []
    for entry in output.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        if meta.startswith("160000"):  # nested repository gitlink
            continue
        files.append(path)
    return files


def is_excluded(path: str) -> bool:
    return any(path.startswith(root) for root in EXCLUDED_ROOTS)


def is_entrypoint(path: str) -> bool:
    if PurePosixPath(path).name in ENTRYPOINT_NAMES:
        return True
    return any(pattern.search(path) for pattern in ENTRYPOINT_PATTERNS)


def _is_text(path: str) -> bool:
    name = PurePosixPath(path).name
    return PurePosixPath(path).suffix.lower() in TEXT_SUFFIXES or name in ENTRYPOINT_NAMES


def reference_index(root: Path, files: Iterable[str]) -> dict[str, set[str]]:
    """Map each token to up to two files containing it (enough to exclude self)."""
    index: dict[str, set[str]] = {}
    for path in files:
        if not _is_text(path):
            continue
        full = root / path
        try:
            if full.stat().st_size > MAX_TEXT_BYTES:
                continue
            text = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        tokens: set[str] = set()
        for token in TOKEN_PATTERN.findall(text):
            tokens.add(token)
            # "infra/scripts/foo.py" and "utils.foo" both expose "foo.py" / "foo".
            for part in re.split(r"[/.]", token):
                if part:
                    tokens.add(part)
            tokens.add(token.rsplit("/", 1)[-1])
        for token in tokens:
            holders = index.setdefault(token, set())
            if len(holders) < 2:
                holders.add(path)
    return index


def _referenced_elsewhere(token: str, path: str, index: dict[str, set[str]]) -> bool:
    return bool(index.get(token, set()) - {path})


def last_commit_dates(root: Path) -> dict[str, str]:
    dates: dict[str, str] = {}
    current = ""
    for line in _git(["log", "--format=%x00%cs", "--name-only"], root).splitlines():
        if line.startswith("\0"):
            current = line[1:]
        elif line and line not in dates:
            dates[line] = current
    return dates


def scan(
    root: Path, prefixes: list[str], limit: int | None = None, kind: str | None = None
) -> dict:
    files = tracked_files(root)
    index = reference_index(root, files)
    candidates = []
    scanned = 0
    for path in files:
        if is_excluded(path) or is_entrypoint(path):
            continue
        if prefixes and not any(path.startswith(prefix) for prefix in prefixes):
            continue
        scanned += 1
        pure = PurePosixPath(path)
        if _referenced_elsewhere(pure.name, path, index):
            continue
        # Code modules are often referenced by stem (imports); unique stems only.
        if pure.suffix in CODE_SUFFIXES and _referenced_elsewhere(pure.stem, path, index):
            continue
        if kind == "code" and pure.suffix not in CODE_SUFFIXES:
            continue
        candidates.append(path)

    dates = last_commit_dates(root) if candidates else {}
    rows = []
    for path in sorted(candidates):
        full = root / path
        rows.append({
            "path": path,
            "kind": "code" if PurePosixPath(path).suffix in CODE_SUFFIXES else "file",
            "bytes": full.stat().st_size if full.exists() else None,
            "last_commit": dates.get(path),
            "reason": "no reference to its filename in any other tracked file",
        })
    rows.sort(key=lambda row: (row["last_commit"] or "", row["path"]))
    return {
        "contract": CONTRACT,
        "mode": "report-only",
        "scanned_files": scanned,
        "candidate_count": len(rows),
        "excluded_roots": list(EXCLUDED_ROOTS),
        "candidates": rows[:limit] if limit else rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="repository-relative path prefixes to scan")
    parser.add_argument("--json", action="store_true", help="print the JSON contract")
    parser.add_argument("--kind", choices=("code",), help="only report code/script files")
    parser.add_argument("--limit", type=int, default=None, help="max candidates to print")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    prefixes = [p.replace("\\", "/").lstrip("./") for p in args.paths]
    report = scan(args.root.resolve(), prefixes, args.limit, args.kind)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"{report['candidate_count']} unreferenced of {report['scanned_files']} scanned (report-only)")
    for row in report["candidates"]:
        print(f"  {row['last_commit'] or 'untracked-date':>10}  {row['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

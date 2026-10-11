#!/usr/bin/env python3
"""
Source of Truth Cleanup Guard

Fails when production/config code reintroduces:
  - data/solo-empire.db as an application DB path
  - legacy database/solo-empire.db (missing infra/ prefix)
  - hardcoded absolute checkout paths (/home/.../solo-empire)
  - duplicate PROJECT_ROOT / DB_PATH / BACKUP_DIR definitions

Canonical Python paths live in: infra/scripts/utils/_env.py
Canonical DB relative path:     data/runtime/solo-empire.db
Canonical env override:         SOLO_EMPIRE_DB_PATH

Historical docs (CHANGELOG, sessions, archives) may mention legacy paths.

Usage:
    python3 infra/tests/quality/source_of_truth_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from _env import PROJECT_ROOT, DB_PATH, BACKUP_DIR, DEFAULT_DB_REL  # noqa: E402

# ── Scan roots / exclusions ─────────────────────────────────────────

CODE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".go",
    ".yml",
    ".yaml",
    ".sh",
    ".service",
    ".conf",
    ".json",
}

# Directories never scanned (nested repos, deps, runtime, knowledge bulk)
EXCLUDE_DIR_NAMES = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".android-sdk",
    "longevity-and-wellness",
    "clients",
}

# Path segment prefixes (relative to repo root) to skip entirely
EXCLUDE_PREFIXES = (
    "projects/product/engineering/book-dev/",  # nested repository clones (gitignored bulk)
    "data/generated/reports/",
    "data/backups/",
    "data/generated/briefings/",
    "data/raw/",
    "data/runtime/",
)

# Relative path prefixes where legacy mentions are allowed (docs/history only)
ALLOW_LEGACY_DOC_PREFIXES = (
    "docs/CHANGELOG.md",
    "docs/AI-REFERENCE-MANIFEST.md",
    "sessions/",
    "operations/AH-",
    "operations/AH_AUDIT",
    "infra/scripts/archive/",
    "infra/tests/quality/source_of_truth_test.py",
    "infra/tests/database/database_source_guard.py",  # documents mirror intentionally
    "infra/tests/quality/smoke_test.py",  # scans for stale paths
    "infra/package.json",  # lint:paths pattern string
)

# Files allowed to *define* SSOT symbols
ALLOWED_DEFINITIONS = {
    "DB_PATH": {
        "infra/scripts/utils/_env.py",
        # Language-local resolved path after SOLO_EMPIRE_DB_PATH (not a second policy)
        "infra/mcp-server/src/db.ts",
    },
    "PROJECT_ROOT": {
        "infra/scripts/utils/_env.py",
        # Standalone modules: importing _env runs load_env() as an import side
        # effect (populates os.environ from the local env file), so these derive
        # the same root from their own location instead.
        "infra/scripts/platform/gpu_worker_policy.py",
        "infra/scripts/platform/llm_lab_budget.py",
        "infra/scripts/platform/cloud_providers.py",
        "infra/scripts/agent/subagent_contract.py",
        "infra/scripts/agent/harness_agents.py",
        "infra/scripts/agent/harness_skills.py",
        "infra/scripts/utils/unused_files.py",
        "infra/scripts/docpack/repo_facts.py",
        "infra/scripts/agent/router.py",
        "infra/scripts/agent/context_pack.py",
        "infra/scripts/utils/task_store.py",
        "infra/scripts/utils/docpack_coverage.py",
        "infra/scripts/platform/building_block_services.py",
        # mock.patch.multiple(guard, PROJECT_ROOT=root, ...) keyword argument, not a definition.
        "infra/tests/quality/operating_model_test.py",
    },
    "BACKUP_DIR": {
        "infra/scripts/utils/_env.py",
        # Shell local backup destination for stack volumes (not Solo Empire DB SSOT)
        "infra/self-hosted-stack/scripts/backup.sh",
    },
}

# Patterns
RE_DATA_DB = re.compile(r"(?<![\w./-])data/solo-empire\.db\b")
# legacy host path: database/solo-empire.db without infra/ immediately before
RE_LEGACY_DB = re.compile(r"(?<!infra/)(?<![\w])database/solo-empire\.db\b")
# absolute checkout hardcodes (user home + solo-empire or book-everything path)
RE_HARDCODE_HOME = re.compile(
    r"/home/[^/\s\"']+/(?:book-everything/)?solo-empire\b"
    r"|/home/[^/\s\"']+/book_solo\b"
    r"|/home/[^/\s\"']+/book-dev/"
)

DEF_PATTERNS = {
    "DB_PATH": re.compile(r"^\s*(?:export\s+)?(?:const|let|var)?\s*DB_PATH\s*="),
    "PROJECT_ROOT": re.compile(r"^\s*(?:export\s+)?(?:const|let|var)?\s*PROJECT_ROOT\s*="),
    "BACKUP_DIR": re.compile(r"^\s*(?:export\s+)?(?:const|let|var)?\s*BACKUP_DIR\s*="),
}


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


# Only scan operational trees (never walk nested clones / domain bulk).
SCAN_ROOTS = (
    "infra",
    "docs",
    "dashboard",
)


def is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIR_NAMES:
        return True
    r = rel(path)
    return any(r.startswith(p) for p in EXCLUDE_PREFIXES)


def is_allowed_legacy_doc(path: Path) -> bool:
    r = rel(path)
    return any(r == p or r.startswith(p) for p in ALLOW_LEGACY_DOC_PREFIXES)


def iter_under(root: Path, suffixes: set[str]):
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes and path.name not in {"Dockerfile"}:
            continue
        if is_excluded(path):
            continue
        yield path


def iter_code_files():
    # Repository-level config plus the centralized infra package
    for name in (
        "infra/cli.ts",
        "infra/.env.example",
    ):
        p = PROJECT_ROOT / name
        if p.is_file():
            yield p
    for sub in SCAN_ROOTS:
        yield from iter_under(PROJECT_ROOT / sub, CODE_EXTENSIONS)


def iter_doc_files():
    """Markdown under core docs and root documentation categories."""
    root_docs = (
        "README.md",
        "docs/ARCHITECTURE.md",
        "AGENTS.md",
        "docs/CHANGELOG.md",
    )
    for name in root_docs:
        p = PROJECT_ROOT / name
        if p.is_file() and not is_allowed_legacy_doc(p):
            yield p
    doc_roots = (
        "docs",
        "documentation-assets",
        "integrations",
        "knowledge",
        "mcp",
        "operations",
        "opportunities",
        "repository-catalog",
        "roles",
        "sessions",
        "systems",
    )
    for doc_root in doc_roots:
        yield from iter_under(PROJECT_ROOT / doc_root, {".md"})
    yield from iter_under(PROJECT_ROOT / "infra", {".md"})


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def scan_legacy_paths() -> list[dict]:
    violations: list[dict] = []
    files = list(iter_code_files()) + list(iter_doc_files())
    for path in files:
        if is_allowed_legacy_doc(path):
            continue
        r = rel(path)
        for i, line in enumerate(read_lines(path), 1):
            stripped = line.strip()
            # Skip pure comments that only document the forbidden pattern (except we still
            # want to catch hardcoded paths in comments of production scripts).
            # Container mount targets /data/solo-empire.db are not data/solo-empire.db host paths.
            if RE_DATA_DB.search(line):
                # Allow docker volume *target* only when clearly container path after :
                # e.g. ../database/solo-empire.db:/data/solo-empire.db:ro
                if re.search(r":/data/solo-empire\.db\b", line):
                    pass  # container FS target after host mount — OK
                elif "data/solo-empire.db" in line and "data/runtime" not in line:
                    violations.append(
                        {
                            "kind": "data/solo-empire.db",
                            "file": r,
                            "line": i,
                            "content": stripped[:160],
                        }
                    )
            if RE_LEGACY_DB.search(line):
                # Allow relative mount from self-hosted-stack: ../database/solo-empire.db
                if re.search(r"\.\./database/solo-empire\.db", line):
                    pass
                else:
                    violations.append(
                        {
                            "kind": "legacy database/solo-empire.db",
                            "file": r,
                            "line": i,
                            "content": stripped[:160],
                        }
                    )
            if RE_HARDCODE_HOME.search(line):
                # systemd %h templates and $HOME examples are fine; only absolute /home/...
                violations.append(
                    {
                        "kind": "hardcoded /home/... path",
                        "file": r,
                        "line": i,
                        "content": stripped[:160],
                    }
                )
    return violations


def scan_duplicate_definitions() -> dict[str, list[dict]]:
    violations: dict[str, list[dict]] = {k: [] for k in DEF_PATTERNS}
    for path in iter_code_files():
        r = rel(path)
        if path.suffix not in {".py", ".ts", ".tsx", ".js", ".go", ".sh"}:
            continue
        # Venture-local DBs are not Solo Empire SSOT
        if r.startswith("ventures/"):
            continue
        for i, line in enumerate(read_lines(path), 1):
            if line.strip().startswith("#") or line.strip().startswith("//"):
                continue
            for name, pattern in DEF_PATTERNS.items():
                if pattern.search(line):
                    if r not in ALLOWED_DEFINITIONS[name]:
                        violations[name].append(
                            {
                                "file": r,
                                "line": i,
                                "content": line.strip()[:160],
                            }
                        )
    return violations


def test_canonical_env_paths() -> bool:
    print("\n[1/4] Canonical path constants (_env.py)")
    ok = True
    expected_db = (PROJECT_ROOT / "data" / "runtime" / "solo-empire.db").resolve()
    if DB_PATH.resolve() != expected_db and not (
        # allow SOLO_EMPIRE_DB_PATH override during test runs
        __import__("os").environ.get("SOLO_EMPIRE_DB_PATH")
    ):
        print(f"  ❌ DB_PATH default unexpected: {DB_PATH}")
        ok = False
    else:
        print(f"  ✅ DB_PATH → {DB_PATH}")
    if DEFAULT_DB_REL.as_posix() != "data/runtime/solo-empire.db":
        print(f"  ❌ DEFAULT_DB_REL wrong: {DEFAULT_DB_REL}")
        ok = False
    else:
        print(f"  ✅ DEFAULT_DB_REL = {DEFAULT_DB_REL.as_posix()}")
    expected_backup = (PROJECT_ROOT / "data" / "backups").resolve()
    if BACKUP_DIR.resolve() != expected_backup:
        print(f"  ❌ BACKUP_DIR unexpected: {BACKUP_DIR}")
        ok = False
    else:
        print(f"  ✅ BACKUP_DIR → {BACKUP_DIR}")
    return ok


def test_no_duplicate_definitions() -> bool:
    print("\n[2/4] Duplicate PROJECT_ROOT / DB_PATH / BACKUP_DIR definitions")
    violations = scan_duplicate_definitions()
    ok = True
    for name, items in violations.items():
        if items:
            ok = False
            print(f"  ❌ {name}: {len(items)} extra definition(s)")
            for v in items[:12]:
                print(f"     {v['file']}:{v['line']}: {v['content']}")
            if len(items) > 12:
                print(f"     ... +{len(items) - 12} more")
            if name in ("DB_PATH", "PROJECT_ROOT", "BACKUP_DIR"):
                print(f"     Fix: import from _env (Python) or use resolveDbPath/SOLO_EMPIRE_DB_PATH")
        else:
            print(f"  ✅ {name}: only allowed definitions")
    return ok


def test_no_legacy_db_paths() -> bool:
    print("\n[3/4] Legacy DB path references (data/…, database/… without infra/)")
    # Filter scan_legacy to kinds related to db
    items = [
        v
        for v in scan_legacy_paths()
        if v["kind"] in ("data/solo-empire.db", "legacy database/solo-empire.db")
    ]
    if items:
        print(f"  ❌ {len(items)} legacy DB path reference(s)")
        for v in items[:20]:
            print(f"     [{v['kind']}] {v['file']}:{v['line']}: {v['content']}")
        if len(items) > 20:
            print(f"     ... +{len(items) - 20} more")
        print("     Use data/runtime/solo-empire.db or SOLO_EMPIRE_DB_PATH")
        return False
    print("  ✅ No disallowed data/ or bare database/ solo-empire.db references")
    return True


def test_no_hardcoded_home_paths() -> bool:
    print("\n[4/4] Hardcoded absolute checkout paths")
    items = [v for v in scan_legacy_paths() if v["kind"] == "hardcoded /home/... path"]
    if items:
        print(f"  ❌ {len(items)} hardcoded absolute path(s)")
        for v in items[:20]:
            print(f"     {v['file']}:{v['line']}: {v['content']}")
        if len(items) > 20:
            print(f"     ... +{len(items) - 20} more")
        print("     Resolve from script location, %h, $HOME, or PROJECT_ROOT / REPO_ROOT")
        return False
    print("  ✅ No hardcoded /home/.../solo-empire (or book-everything) paths in scanned files")
    return True


def main() -> int:
    print("=" * 70)
    print("Source of Truth Cleanup Guard")
    print("=" * 70)
    print(f"Repo: {PROJECT_ROOT}")
    print(f"Canonical DB: data/runtime/solo-empire.db")

    tests = [
        test_canonical_env_paths,
        test_no_duplicate_definitions,
        test_no_legacy_db_paths,
        test_no_hardcoded_home_paths,
    ]
    passed = failed = 0
    for t in tests:
        try:
            if t():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__} exception: {e}")
            failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)
    if failed:
        print("❌ Source-of-truth guard failed — remove duplicates/legacy paths before merge")
        return 1
    print("✅ Single source of truth intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validate the production tools catalog.

the production-tools.yaml catalog in the book-production-platform checkout is the single source of truth for the Solo
Empire production tool catalog (roadmap T5.1). This guard keeps it honest
(roadmap T5.3): complete schema fields, real owning repositories, real CLI
entry points, the canonical runtime vocabulary, read-only consistency with
runtime ownership recorded in repository-catalog/registries/runtime-boundaries.yaml.
The generated dashboard page was retired on 2026-10-11.

The guard is deterministic and offline. It never executes or probes
Blender/Unity/FreeCADCmd/FFmpeg; runtime probing belongs to
`book-production doctor` (roadmap T5.2) inside the owning repository.
SOLO_EMPIRE_CI=1 checks registered metadata for absent ignored checkouts;
local validation and --require-checkouts keep file-existence checks strict.

Usage:
    python3 infra/scripts/utils/production_tools_catalog_guard.py --check
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    sys.exit(1)


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import (  # noqa: E402
    ARCHITECTURE_ROOT, KNOWLEDGE_ROOT, PROJECT_ROOT, SKILLS_ROOT, book_dev_github_root, display_path,
    production_tools_catalog, registry_path, repo_file,
)

CATALOG_PATH = production_tools_catalog()  # owned by book-production-platform
# Runtime ownership lives in the registry repository: SOLO_REGISTRY_DIR points at its repository-catalog folder.
RUNTIME_BOUNDARIES_PATH = registry_path("registries/runtime-boundaries.yaml")

SCHEMA_VERSION = "solo-empire.production-tools.v1"
CANONICAL_RUNTIMES = frozenset({"blender", "ffmpeg", "freecadcmd", "unity"})
GATE_STATUSES = {
    "accepted": "synthetic acceptance gate passed",
    "pilot-ready": "deterministic gate passed; human/visual gate pending",
    "needs-human-gate": "waiting for a recorded human review gate (listening/visual/fresh-install smoke)",
    "blocked": "cannot proceed until an external dependency is resolved",
    "experimental": "implementation exists without a recorded acceptance gate",
}
REQUIRED_TOOL_FIELDS = (
    "id",
    "name",
    "owning_repo",
    "cli_entry_point",
    "cli_command",
    "input_schema",
    "outputs",
    "runtimes",
    "gate_status",
    "evidence",
)
OPTIONAL_TOOL_FIELDS = ("notes", "boundary_modules", "capability_tracks")
LIST_FIELDS = ("outputs", "runtimes", "evidence")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return display_path(path)


def fail(message: str, errors: list[str]) -> None:
    errors.append(message)


def normalize_repo(value: str) -> str:
    return value.replace("\\", "/").rstrip("/")


def load_catalog(errors: list[str]) -> dict[str, Any] | None:
    if not CATALOG_PATH.is_file():
        fail(f"missing {rel(CATALOG_PATH)}", errors)
        return None
    try:
        data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        fail(f"{rel(CATALOG_PATH)} does not parse: {error}", errors)
        return None
    if not isinstance(data, dict):
        fail(f"{rel(CATALOG_PATH)} root must be a mapping", errors)
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        fail(f"schema_version must be {SCHEMA_VERSION}", errors)
    vocabulary = data.get("runtime_vocabulary")
    if not isinstance(vocabulary, list) or set(vocabulary) != CANONICAL_RUNTIMES:
        fail(f"runtime_vocabulary must list exactly {sorted(CANONICAL_RUNTIMES)}", errors)
    statuses = data.get("gate_statuses")
    if not isinstance(statuses, dict) or set(statuses) != set(GATE_STATUSES):
        fail("gate_statuses must define exactly the documented enum", errors)
    elif any(str(statuses[key]).strip() != GATE_STATUSES[key] for key in GATE_STATUSES):
        fail("gate_statuses definitions drifted from the documented enum", errors)
    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        fail("tools must be a non-empty list", errors)
    return data


def _resolve(path: Path) -> Path:
    """Legacy repository-relative path -> owning checkout (default root) or a path below a test root."""
    return repo_file(path) if ROOT == PROJECT_ROOT else ROOT / path


def _allowed_roots() -> list[Path]:
    if ROOT != PROJECT_ROOT:
        return [ROOT]
    github = book_dev_github_root()
    return [PROJECT_ROOT, ARCHITECTURE_ROOT, KNOWLEDGE_ROOT, SKILLS_ROOT, *([github] if github else [])]


def contained_path(value: str) -> Path | None:
    """Reject lexical traversal and symlink escapes before checking availability."""
    path = Path(value)
    if (
        not value.strip() or any(ord(char) < 32 for char in value)
        or value != path.as_posix() or "\\" in value or ":" in value or path.is_absolute()
        or ".." in path.parts
    ):
        return None
    target = _resolve(path)
    if not any(target.resolve().is_relative_to(root.resolve()) for root in _allowed_roots()):
        return None
    return target


CATALOG_REPO = "book-production-platform"
CATALOG_REPO_URL = "https://github.com/bookchaowalit/book-production-platform/blob/main"


def is_schema_path(value: str) -> bool:
    return " " not in value and ("/" in value or value.endswith((".json", ".yaml", ".yml")))


def localize_tool(tool: Any, repositories: dict[str, Any], errors: list[str]) -> Any:
    """Map catalog paths onto this checkout's layout for validation.

    The catalog stays layout-free: ``owning_repo`` is ``bookchaowalit/<repo>``,
    ``cli_entry_point`` and a path ``input_schema`` are relative to that repository,
    and evidence is relative to the catalog's own repository. runtime-boundaries.yaml
    says where each repository is checked out here. Older hub-relative values pass
    through unchanged.
    """
    if not isinstance(tool, dict) or not str(tool.get("owning_repo", "")).startswith("bookchaowalit/"):
        return tool

    def checkout(name: str) -> str | None:
        entry = repositories.get(name)
        path = entry.get("path") if isinstance(entry, dict) else None
        return normalize_repo(path) if isinstance(path, str) and path else None

    name = str(tool["owning_repo"]).split("/", 1)[1]
    owner, catalog_repo = checkout(name), checkout(CATALOG_REPO)
    if owner is None or catalog_repo is None:
        fail(f"tool {tool.get('id')} owning_repo {tool['owning_repo']!r} is not a runtime-boundaries repository", errors)
        return tool
    local = dict(tool, owning_repo=owner)
    if isinstance(tool.get("cli_entry_point"), str) and tool["cli_entry_point"]:
        local["cli_entry_point"] = f"{owner}/{tool['cli_entry_point']}"
    schema = tool.get("input_schema")
    if isinstance(schema, str) and is_schema_path(schema.strip()):
        local["input_schema"] = f"{owner}/{schema.strip()}"
    if isinstance(tool.get("evidence"), list):
        local["evidence"] = [f"{catalog_repo}/{item}" if isinstance(item, str) and item.strip() else item
                             for item in tool["evidence"]]
    return local


def check_tool(
    tool: Any, index: int, errors: list[str], *, known_repos: set[str] | None = None,
    require_checkouts: bool = False, warnings: list[str] | None = None,
) -> None:
    if not isinstance(tool, dict):
        fail(f"tools[{index}] must be a mapping", errors)
        return
    label = f"tools[{index}]"
    missing: list[str] = []
    for field in REQUIRED_TOOL_FIELDS:
        value = tool.get(field)
        if field == "runtimes":
            # An explicit empty list is valid: the tool needs no canonical runtime.
            if not isinstance(value, list):
                missing.append(field)
        elif value in (None, "", [], {}):
            missing.append(field)
    if missing:
        fail(f"{label} missing required fields: {', '.join(missing)}", errors)
        return
    unknown = sorted(set(tool) - set(REQUIRED_TOOL_FIELDS) - set(OPTIONAL_TOOL_FIELDS))
    if unknown:
        fail(f"{label} has unknown fields: {', '.join(unknown)}", errors)

    tool_id = str(tool["id"])
    if not ID_RE.match(tool_id):
        fail(f"{label} id must match {ID_RE.pattern}", errors)
    else:
        label = f"tool {tool_id}"

    for field in LIST_FIELDS:
        value = tool.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            fail(f"{label} field {field} must be a list of non-empty strings", errors)
    capability_tracks = tool.get("capability_tracks")
    if capability_tracks is not None and (
        not isinstance(capability_tracks, list)
        or not all(isinstance(item, str) and item.strip() for item in capability_tracks)
    ):
        fail(f"{label} capability_tracks must be a list of non-empty strings", errors)
    if not isinstance(tool.get("cli_command"), str) or not tool["cli_command"].strip():
        fail(f"{label} cli_command must be a non-empty string", errors)
    if not isinstance(tool.get("input_schema"), str) or not tool["input_schema"].strip():
        fail(f"{label} input_schema must be a non-empty string or path", errors)

    runtimes = tool.get("runtimes")
    if isinstance(runtimes, list):
        unknown_runtimes = sorted({str(item) for item in runtimes} - CANONICAL_RUNTIMES)
        if unknown_runtimes:
            fail(
                f"{label} uses runtimes outside the canonical vocabulary: {', '.join(unknown_runtimes)}",
                errors,
            )

    gate_status = str(tool.get("gate_status"))
    if gate_status not in GATE_STATUSES:
        fail(f"{label} has unknown gate_status {gate_status!r}", errors)

    owning_repo = str(tool.get("owning_repo", ""))
    repo_path = contained_path(owning_repo)
    if repo_path is None:
        fail(f"{label} owning_repo must be a contained repository-relative path: {owning_repo}", errors)
        return
    portable_missing = (
        os.environ.get("SOLO_EMPIRE_CI") == "1" and not require_checkouts
        and owning_repo in (known_repos or set())
        and Path(owning_repo).parts[:4] == ("projects", "product", "engineering", "book-dev")
        and not repo_path.exists() and not repo_path.is_symlink()
    )
    if not repo_path.is_dir():
        if portable_missing:
            if warnings is not None:
                warnings.append(f"SKIP: {label} checkout absent; registered runtime metadata only: {owning_repo}")
        else:
            fail(f"{label} owning_repo does not exist: {owning_repo}", errors)

    def check_file(value: str, field: str, *, owned: bool = False) -> None:
        path = contained_path(value)
        if path is None:
            fail(f"{label} {field} must be a contained repository-relative path: {value}", errors)
            return
        resolved = path.resolve()
        resolved_owner = repo_path.resolve()
        under_owner = (
            path.is_relative_to(repo_path) and path != repo_path
            and resolved.is_relative_to(resolved_owner) and resolved != resolved_owner
        )
        if owned and not under_owner:
            fail(f"{label} {field} must belong to owning_repo: {value}", errors)
        elif not path.is_file() and not (portable_missing and under_owner):
            fail(f"{label} {field} path does not exist: {value}", errors)

    check_file(str(tool.get("cli_entry_point", "")), "cli_entry_point", owned=True)

    input_schema = str(tool.get("input_schema", "")).strip()
    if (
        (" " not in input_schema and ("/" in input_schema or input_schema.endswith((".json", ".yaml", ".yml"))))
        or "\\" in input_schema or input_schema.startswith(("/", "../"))
    ):
        check_file(input_schema, "input_schema")

    for evidence in tool.get("evidence", []) or []:
        if not isinstance(evidence, str) or not evidence.strip():
            fail(f"{label} has a non-string evidence entry", errors)
            continue
        check_file(evidence.split("#", 1)[0], "evidence")


def load_runtime_boundaries(errors: list[str]) -> dict[str, Any]:
    if not RUNTIME_BOUNDARIES_PATH.is_file():
        fail(f"missing {rel(RUNTIME_BOUNDARIES_PATH)}", errors)
        return {}
    try:
        data = yaml.safe_load(RUNTIME_BOUNDARIES_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        fail(f"{rel(RUNTIME_BOUNDARIES_PATH)} does not parse: {error}", errors)
        return {}
    if not isinstance(data, dict):
        fail(f"{rel(RUNTIME_BOUNDARIES_PATH)} root must be a mapping", errors)
        return {}
    if data.get("schema_version") != "solo-empire.runtime-boundaries.v1":
        fail("runtime-boundaries.yaml schema_version is invalid", errors)
    return data


def check_runtime_ownership(tool: dict[str, Any], boundaries: dict[str, Any], errors: list[str]) -> None:
    modules = boundaries.get("modules") or {}
    repositories = boundaries.get("repositories") or {}
    if not isinstance(modules, dict) or not isinstance(repositories, dict):
        fail("runtime-boundaries.yaml modules/repositories must be mappings", errors)
        return
    recorded = tool.get("boundary_modules") or []
    if not isinstance(recorded, list):
        fail(f"tool {tool['id']} boundary_modules must be a list", errors)
        return
    owning_repo = normalize_repo(str(tool["owning_repo"]))
    for name in recorded:
        module = modules.get(name)
        if not isinstance(module, dict):
            fail(f"tool {tool['id']} lists boundary module {name!r} missing from runtime-boundaries.yaml", errors)
            continue
        repo_name = module.get("current_repository")
        repo_entry = repositories.get(repo_name)
        if not isinstance(repo_entry, dict) or not repo_entry.get("path"):
            fail(
                f"tool {tool['id']} boundary module {name!r} has no resolvable current_repository",
                errors,
            )
            continue
        module_path = normalize_repo(str(repo_entry["path"]))
        if module_path != owning_repo:
            fail(
                f"tool {tool['id']} owning_repo {owning_repo!r} conflicts with runtime-boundaries "
                f"module {name!r} owned by {repo_name} at {module_path!r}",
                errors,
            )


def validate_catalog(
    *, require_checkouts: bool = False, warnings: list[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    catalog = load_catalog(errors)
    if catalog is None or errors:
        return catalog, errors
    tools = catalog.get("tools") or []
    boundaries = load_runtime_boundaries(errors)
    repositories = boundaries.get("repositories")
    known_repos: set[str] = set()
    if not isinstance(repositories, dict):
        fail("runtime-boundaries.yaml repositories must be a mapping", errors)
    else:
        for name, repository in repositories.items():
            path = repository.get("path") if isinstance(repository, dict) else None
            if not isinstance(path, str) or contained_path(path) is None:
                fail(f"runtime-boundaries.yaml repository {name!r} has an invalid path", errors)
            else:
                known_repos.add(path)
    seen: set[str] = set()
    tools = [localize_tool(tool, repositories if isinstance(repositories, dict) else {}, errors)
             for tool in tools]
    for index, tool in enumerate(tools):
        check_tool(tool, index, errors, known_repos=known_repos,
                   require_checkouts=require_checkouts, warnings=warnings)
        tool_id = tool.get("id") if isinstance(tool, dict) else None
        if isinstance(tool_id, str):
            if tool_id in seen:
                fail(f"duplicate tool id {tool_id!r}", errors)
            seen.add(tool_id)
    if errors:
        return catalog, errors
    for tool in tools:
        check_runtime_ownership(tool, boundaries, errors)
    return catalog, errors


def summary_line(catalog: dict[str, Any]) -> str:
    tools = catalog["tools"]
    counts: dict[str, int] = {}
    for tool in tools:
        counts[tool["gate_status"]] = counts.get(tool["gate_status"], 0) + 1
    status_summary = ", ".join(f"{status}={counts[status]}" for status in sorted(counts))
    return f"{len(tools)} production tools cataloged ({status_summary})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the catalog (default)")
    parser.add_argument("--require-checkouts", action="store_true", help="require local runtime checkouts even in CI")
    args = parser.parse_args()

    if not RUNTIME_BOUNDARIES_PATH.is_file():
        print("  SKIP: production tools guard needs the registry: set SOLO_REGISTRY_DIR to the repository-catalog folder")
        return 0
    if not CATALOG_PATH.is_file() and os.environ.get("SOLO_EMPIRE_CI") == "1":
        print(f"  WARN: SKIP production tools catalog absent ({CATALOG_PATH}); set SOLO_BOOK_PRODUCTION_PLATFORM_ROOT")
        return 0

    warnings: list[str] = []
    catalog, errors = validate_catalog(require_checkouts=args.require_checkouts, warnings=warnings)
    for warning in warnings:
        print(f"  {warning}")
    if errors or catalog is None:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1

    print(f"  OK: {summary_line(catalog)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

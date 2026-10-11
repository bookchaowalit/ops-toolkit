"""Canonical paths for Solo Empire domain workspaces.

The machine-readable registry in ``projects/workspaces.yaml`` is the source
of truth. Runtime code uses only entries marked ``active``; ``planned`` entries
reserve future paths without making tools depend on missing directories.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

try:
    import yaml
except ImportError as exc:  # pragma: no cover - covered by environment setup
    raise RuntimeError("PyYAML is required to load projects/workspaces.yaml") from exc

from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, book_dev_github_root


# Workspace docs live in the solo-knowledge repository (SOLO_KNOWLEDGE_ROOT).
REGISTRY_PATH = KNOWLEDGE_ROOT / "projects" / "workspaces.yaml"
# Workspaces whose checkout is a local-only (gitignored) tree owned by solo-empire.
EXTERNAL_WORKSPACE_PATHS = frozenset({"projects/product/engineering/book-dev"})
VALID_STATUSES = frozenset({"active", "planned"})


def load_workspace_registry(registry_path: Path | None = None) -> tuple[dict[str, str], ...]:
    """Validate registry metadata without requiring workspace directories to exist."""

    registry_path = REGISTRY_PATH if registry_path is None else registry_path
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"Invalid workspace registry version in {registry_path}")

    entries = data.get("workspaces")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Workspace registry must contain a non-empty workspaces list: {registry_path}")

    normalized: list[dict[str, str]] = []
    seen_slugs: set[str] = set()
    seen_paths: set[str] = set()
    required = ("slug", "name", "bu", "group", "path", "status")

    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Workspace registry entry {index} must be a mapping")
        missing = [field for field in required if not isinstance(entry.get(field), str) or not entry[field].strip()]
        if missing:
            raise ValueError(f"Workspace registry entry {index} missing fields: {', '.join(missing)}")

        slug = entry["slug"]
        path_text = entry["path"]
        status = entry["status"]
        path = PurePosixPath(path_text)
        if path_text in seen_paths:
            raise ValueError(f"Duplicate workspace path in {registry_path}: {path_text}")
        if slug in seen_slugs:
            raise ValueError(f"Duplicate workspace slug in {registry_path}: {slug}")
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid workspace status {status!r} for {slug}")
        parts = path_text.split("/")
        if (
            len(parts) != 4
            or any(part in {"", ".", ".."} for part in parts)
            or "\\" in path_text
            or any(character.isspace() for character in path_text)
            or path.as_posix() != path_text
            or parts != ["projects", entry["bu"], entry["group"], slug]
        ):
            raise ValueError(
                f"Workspace path must match canonical projects/<bu>/<group>/<slug> for {slug}: {path_text}"
            )
        if "scope" in entry and (not isinstance(entry["scope"], str) or not entry["scope"].strip()):
            raise ValueError(f"Workspace scope must be non-empty text for {slug}")

        seen_slugs.add(slug)
        seen_paths.add(path_text)
        record = {field: entry[field] for field in required}
        if "scope" in entry:
            record["scope"] = entry["scope"]
        normalized.append(record)

    return tuple(normalized)


# Retain the original loader name for existing internal callers.
_load_registry = load_workspace_registry

def _load_or_empty() -> tuple[dict[str, str], ...]:
    # The registry lives in solo-knowledge (optional sibling): without it no workspace resolves.
    return load_workspace_registry() if REGISTRY_PATH.is_file() else ()


WORKSPACE_REGISTRY = _load_or_empty()
_WORKSPACE_BY_SLUG = {entry["slug"]: entry for entry in WORKSPACE_REGISTRY}

# Backward-compatible mapping for callers that only need active workspace paths.
WORKSPACE_LAYOUT: dict[str, tuple[str, ...]] = {
    entry["slug"]: tuple(PurePosixPath(entry["path"]).parts)
    for entry in WORKSPACE_REGISTRY
    if entry["status"] == "active"
}


def repo_path(*parts: str) -> Path:
    """Return a path for a local-only workspace.

    The Book Dev tree (``projects/product/engineering/book-dev``) is the folder that
    holds the repository clones; it is derived from the clone layout or
    SOLO_BOOK_DEV_GITHUB_ROOT and never points at a registry-hub checkout.
    """

    github = book_dev_github_root()
    book_dev = ("projects", "product", "engineering", "book-dev")
    if github is not None and tuple(parts[:4]) == book_dev:
        return github.parent.joinpath(*parts[4:])
    return PROJECT_ROOT.joinpath(*parts)


def knowledge_path(*parts: str) -> Path:
    """Return a path rooted at the solo-knowledge repository."""

    return KNOWLEDGE_ROOT.joinpath(*parts)


def workspace_record(slug: str) -> dict[str, str]:
    """Return registry metadata for an active or planned workspace."""

    try:
        return dict(_WORKSPACE_BY_SLUG[slug])
    except KeyError as exc:
        known = ", ".join(sorted(_WORKSPACE_BY_SLUG))
        raise KeyError(f"Unknown workspace slug {slug!r}. Registered: {known}") from exc


def workspace_path(slug: str) -> Path:
    """Return the canonical repository path for an active workspace slug."""

    record = workspace_record(slug)
    if record["status"] != "active":
        raise ValueError(f"Workspace {slug!r} is registered as planned, not active")
    parts = PurePosixPath(record["path"]).parts
    if record["path"] in EXTERNAL_WORKSPACE_PATHS:
        return repo_path(*parts)
    return knowledge_path(*parts)


def workspace_tools_path(slug: str) -> Path:
    """Return the tools directory for an active workspace."""

    return workspace_path(slug) / "tools"


def workspace_data_path(slug: str, *parts: str) -> Path:
    """Return a path under an active workspace's data directory."""

    return workspace_path(slug).joinpath("data", *parts)

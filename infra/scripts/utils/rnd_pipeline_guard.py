#!/usr/bin/env python3
"""Validate the central Science and R&D lifecycle contract and tracker."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, repo_rel, repo_file, ARCHITECTURE_ROOT, book_dev_github_root, skip_without_architecture


def contained(root: Path, path: str) -> Path:
    """Resolve a repository-relative registry path; legacy hub paths map to the owning clone."""
    if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError("registry paths must be repository-relative")
    if root == PROJECT_ROOT:
        return repo_file(path).resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes repository: {path}")
    return resolved


CONTRACT_PATH = ARCHITECTURE_ROOT / "systems" / "research-and-development" / "rnd-lifecycle.yaml"
TRACKER_PATH = ARCHITECTURE_ROOT / "systems" / "research-and-development" / "initiatives.csv"
ID_RE = re.compile(r"^RND-\d{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_TOP_LEVEL = (
    "version",
    "kind",
    "contract",
    "status",
    "tracker_path",
    "template_path",
    "workspace_registry",
    "workspace_roots",
    "workspace_bindings",
    "domains",
    "allowed_statuses",
    "required_tracker_columns",
    "stages",
    "controls",
)


def relative(path: Path) -> str:
    try:
        return repo_rel(path)
    except ValueError:
        return path.as_posix()


def _path_from_repo(value: str, label: str, errors: list[str], *, must_exist: bool = True) -> Path | None:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in value.split("/"):
        errors.append(f"{label}: must be a repository-relative path without '..'")
        return None
    path = repo_file(value)
    resolved = path.resolve()
    roots = (PROJECT_ROOT.resolve(), KNOWLEDGE_ROOT.resolve(), ARCHITECTURE_ROOT.resolve(), *([book_dev_github_root().resolve()] if book_dev_github_root() else []))
    if resolved not in roots and not any(root in resolved.parents for root in roots):
        errors.append(f"{label}: resolved path escapes the repository: {value}")
        return None
    if must_exist and not path.exists():
        errors.append(f"{label}: path does not exist: {value}")
        return None
    return path


def _required(mapping: dict[str, Any], fields: list[str] | tuple[str, ...], label: str, errors: list[str]) -> None:
    for field in fields:
        if str(mapping.get(field, "")).strip() == "":
            errors.append(f"{label}: missing required field {field!r}")


def _date(value: str, label: str, errors: list[str]) -> None:
    if not DATE_RE.fullmatch(value):
        errors.append(f"{label}: expected YYYY-MM-DD, found {value!r}")
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        errors.append(f"{label}: invalid calendar date {value!r}")


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"contract root must be a mapping: {relative(path)}")
    return value


def load_tracker(path: Path = TRACKER_PATH) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"tracker has no header: {relative(path)}")
        return [dict(row) for row in reader]


def _external_evidence(contract: dict[str, Any], errors: list[str], warnings: list[str], *, require_checkouts: bool) -> dict[str, Path]:
    declared: dict[str, Path] = {}
    entries = contract.get("external_evidence", [])
    if not isinstance(entries, list):
        errors.append("external evidence: declarations must be a list")
        return declared
    for index, entry in enumerate(entries):
        label = f"external evidence[{index}]"
        if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("repository", "checkout_root", "commit")):
            errors.append(f"{label}: owner and pin provenance are required")
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", entry["repository"]) or not re.fullmatch(r"[0-9a-f]{40}", entry["commit"]):
            errors.append(f"{label}: invalid repository or commit pin")
            continue
        try:
            checkout = contained(PROJECT_ROOT, entry["checkout_root"])
        except ValueError:
            errors.append(f"{label}: invalid owner checkout path")
            continue
        files = entry.get("files")
        if not isinstance(files, list) or not files:
            errors.append(f"{label}: files must be a non-empty list")
            continue
        available = checkout.exists()
        has_boundary = available and checkout.is_dir() and (checkout / ".git").exists()
        if available and not has_boundary:
            errors.append(f"{label}: owner checkout has no Git boundary")
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("blob"), str) or not re.fullmatch(r"[0-9a-f]{40}", item["blob"]):
                errors.append(f"{label}: each file needs a relative path and blob pin")
                continue
            try:
                path = contained(checkout, item["path"])
                reference = repo_rel(path)
            except ValueError:
                errors.append(f"{label}: file path escapes the owning checkout")
                continue
            if reference in declared:
                errors.append(f"{label}: duplicate external evidence path")
            declared[reference] = checkout
            if available and not path.is_file():
                errors.append(f"{label}: declared external evidence file is missing")
            if not available:
                (errors if require_checkouts else warnings).append(
                    f"unverified external evidence: {reference} (owner checkout not present; pinned metadata only)"
                )
            elif has_boundary:
                try:
                    result = subprocess.run(
                        ["git", "-C", str(checkout), "rev-parse", "--verify", f"{entry['commit']}:{item['path']}"],
                        capture_output=True, text=True, timeout=10, check=False,
                    )
                    verified = result.returncode == 0 and result.stdout.strip() == item["blob"]
                except (OSError, subprocess.TimeoutExpired):
                    verified = False
                if not verified:
                    errors.append(f"{label}: pinned file provenance unavailable or mismatched")
    return declared


def validate(contract_path: Path = CONTRACT_PATH, tracker_path: Path = TRACKER_PATH, *, warnings: list[str] | None = None, require_checkouts: bool | None = None) -> list[str]:
    errors: list[str] = []
    warnings = warnings if warnings is not None else []
    if require_checkouts is None:
        require_checkouts = os.environ.get("SOLO_EMPIRE_CI") != "1"
    if not contract_path.is_file():
        return [f"missing R&D contract: {relative(contract_path)}"]
    if not tracker_path.is_file():
        return [f"missing R&D tracker: {relative(tracker_path)}"]

    try:
        contract = load_contract(contract_path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        return [f"unable to load R&D contract: {exc}"]

    _required(contract, REQUIRED_TOP_LEVEL, "contract", errors)
    if contract.get("version") != 1:
        errors.append("contract: version must be 1")
    if contract.get("kind") != "research-and-development-lifecycle":
        errors.append("contract: kind must be research-and-development-lifecycle")
    if contract.get("contract") != "solo-empire.rnd-lifecycle.v1":
        errors.append("contract: contract must be solo-empire.rnd-lifecycle.v1")
    if contract.get("status") != "current":
        errors.append("contract: status must be current")
    external = _external_evidence(contract, errors, warnings, require_checkouts=require_checkouts)

    for field in ("template_path", "workspace_registry"):
        _path_from_repo(str(contract.get(field, "")), f"contract.{field}", errors)

    tracker_rel = str(contract.get("tracker_path", ""))
    if tracker_rel and tracker_rel != relative(tracker_path):
        errors.append(f"contract.tracker_path: expected {relative(tracker_path)!r}, found {tracker_rel!r}")

    workspace_roots = contract.get("workspace_roots")
    if not isinstance(workspace_roots, list) or not workspace_roots:
        errors.append("contract.workspace_roots: must be a non-empty list")
        workspace_roots = []
    root_paths = []
    for index, root in enumerate(workspace_roots):
        path = _path_from_repo(str(root), f"contract.workspace_roots[{index}]", errors)
        if path is not None and not path.is_dir():
            errors.append(f"contract.workspace_roots[{index}]: must be a directory")
        if path is not None:
            root_paths.append(path.resolve())

    bindings = contract.get("workspace_bindings")
    binding_ids: set[str] = set()
    if not isinstance(bindings, list) or not bindings:
        errors.append("contract.workspace_bindings: must be a non-empty list")
        bindings = []
    for index, binding in enumerate(bindings):
        label = f"contract.workspace_bindings[{index}]"
        if not isinstance(binding, dict):
            errors.append(f"{label}: must be a mapping")
            continue
        _required(binding, ("id", "domain", "path"), label, errors)
        binding_id = str(binding.get("id", ""))
        if binding_id in binding_ids:
            errors.append(f"{label}: duplicate workspace id {binding_id!r}")
        binding_ids.add(binding_id)
        path = _path_from_repo(str(binding.get("path", "")), f"{label}.path", errors)
        if path is not None:
            resolved = path.resolve()
            if not path.is_dir():
                errors.append(f"{label}.path: must be a directory")
            if not any(resolved == root or root in resolved.parents for root in root_paths):
                errors.append(f"{label}.path: outside registered R&D roots")

    domains = contract.get("domains")
    if not isinstance(domains, list) or not domains or any(not isinstance(item, str) for item in domains):
        errors.append("contract.domains: must be a non-empty list of strings")
        domains = []
    for index, binding in enumerate(bindings):
        if isinstance(binding, dict) and binding.get("domain") not in domains:
            errors.append(f"contract.workspace_bindings[{index}].domain: unsupported domain {binding.get('domain')!r}")
    statuses = contract.get("allowed_statuses")
    if not isinstance(statuses, list) or not statuses or any(not isinstance(item, str) for item in statuses):
        errors.append("contract.allowed_statuses: must be a non-empty list of strings")
        statuses = []

    columns = contract.get("required_tracker_columns")
    if not isinstance(columns, list) or not columns or any(not isinstance(item, str) for item in columns):
        errors.append("contract.required_tracker_columns: must be a non-empty list of strings")
        columns = []

    stages = contract.get("stages")
    stage_map: dict[str, dict[str, Any]] = {}
    if not isinstance(stages, list) or not stages:
        errors.append("contract.stages: must be a non-empty list")
        stages = []
    for index, stage in enumerate(stages):
        label = f"contract.stages[{index}]"
        if not isinstance(stage, dict):
            errors.append(f"{label}: must be a mapping")
            continue
        _required(stage, ("id", "required_fields", "gate"), label, errors)
        stage_id = str(stage.get("id", ""))
        if stage_id in stage_map:
            errors.append(f"{label}: duplicate stage id {stage_id!r}")
        required_fields = stage.get("required_fields")
        if not isinstance(required_fields, list) or not required_fields or any(not isinstance(item, str) for item in required_fields):
            errors.append(f"{label}.required_fields: must be a non-empty list of strings")
        stage_map[stage_id] = stage

    try:
        rows = load_tracker(tracker_path)
    except (OSError, ValueError, csv.Error) as exc:
        return errors + [f"unable to load R&D tracker: {exc}"]

    with tracker_path.open(newline="", encoding="utf-8") as handle:
        fieldnames = csv.DictReader(handle).fieldnames or []
    missing_columns = [column for column in columns if column not in fieldnames]
    if missing_columns:
        errors.append(f"tracker: missing required columns: {', '.join(missing_columns)}")

    seen_ids: set[str] = set()
    for index, row in enumerate(rows, start=2):
        label = f"tracker row {index}"
        initiative_id = row.get("initiative_id", "").strip()
        if not ID_RE.fullmatch(initiative_id):
            errors.append(f"{label}: initiative_id must match RND-####")
        if initiative_id in seen_ids:
            errors.append(f"{label}: duplicate initiative_id {initiative_id!r}")
        seen_ids.add(initiative_id)

        for column in columns:
            if not row.get(column, "").strip():
                errors.append(f"{label}: missing required tracker column {column!r}")

        domain = row.get("domain", "").strip()
        if domain not in domains:
            errors.append(f"{label}: unsupported domain {domain!r}")
        status = row.get("status", "").strip()
        if status not in statuses:
            errors.append(f"{label}: unsupported status {status!r}")
        stage_id = row.get("stage", "").strip()
        stage = stage_map.get(stage_id)
        if stage is None:
            errors.append(f"{label}: unsupported stage {stage_id!r}")
        else:
            for field in stage.get("required_fields", []):
                if not row.get(field, "").strip():
                    errors.append(f"{label}: stage {stage_id!r} requires {field!r}")

        workspace_value = row.get("workspace_path", "").strip()
        workspace = _path_from_repo(workspace_value, f"{label}.workspace_path", errors)
        if workspace is not None:
            resolved = workspace.resolve()
            if not any(resolved == root or root in resolved.parents for root in root_paths):
                errors.append(f"{label}: workspace_path is outside registered R&D roots: {workspace_value!r}")

        for date_field in ("review_on", "started_on", "updated_on"):
            _date(row.get(date_field, "").strip(), f"{label}.{date_field}", errors)

        for path_field in ("evidence_paths", "outcome_artifact"):
            raw_paths = row.get(path_field, "").strip()
            if not raw_paths:
                continue
            paths = raw_paths.split(";") if path_field == "evidence_paths" else [raw_paths]
            for path_index, path_value in enumerate(paths):
                value = path_value.strip()
                checkout = external.get(value) if path_field == "evidence_paths" else None
                _path_from_repo(
                    value, f"{label}.{path_field}[{path_index}]", errors,
                    must_exist=checkout is None or checkout.exists(),
                )

    return errors


def report(contract_path: Path = CONTRACT_PATH, tracker_path: Path = TRACKER_PATH, *, require_checkouts: bool | None = None) -> dict[str, Any]:
    warnings: list[str] = []
    errors = validate(contract_path, tracker_path, warnings=warnings, require_checkouts=require_checkouts)
    try:
        count = len(load_tracker(tracker_path)) if tracker_path.is_file() else 0
    except (OSError, ValueError, csv.Error):
        count = 0
    return {
        "contract": "solo-empire.rnd-lifecycle.v1",
        "status": "pass" if not errors else "fail",
        "initiative_count": count,
        "tracker": relative(tracker_path),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    skip_without_architecture("rnd_pipeline_guard.py")  # standalone: no architecture-docs checkout -> SKIP, not FAIL
    parser = argparse.ArgumentParser(description="Validate the central Science and R&D pipeline")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-checkouts", action="store_true", default=None, help="require declared external evidence checkouts, including portable CI")
    args = parser.parse_args()
    result = report(require_checkouts=args.require_checkouts)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 70)
        print("Solo Empire — Science and R&D Pipeline Guard")
        print("=" * 70)
        print(f"  Tracker: {result['tracker']}")
        print(f"  Initiatives: {result['initiative_count']}")
        for warning in result["warnings"]:
            print(f"  WARN: {warning}")
        if result["errors"]:
            for error in result["errors"]:
                print(f"  FAIL: {error}")
            print(f"Result: FAIL ({len(result['errors'])} errors)")
        else:
            print("  ✅ tracked contract, stages, tracker rows, dates, and evidence declarations are valid")
            print("Result: OK")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("rnd_pipeline_guard.py")
    raise SystemExit(main())

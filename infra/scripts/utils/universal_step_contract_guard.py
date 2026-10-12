#!/usr/bin/env python3
"""Validate the repository-wide universal step visualization contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - repository toolchain normally provides PyYAML
    print("FAIL: PyYAML is required (import yaml)")
    raise SystemExit(1)

from _env import PROJECT_ROOT, ARCHITECTURE_ROOT, display_path, skip_without_architecture  # noqa: E402


CONTRACT_PATH = ARCHITECTURE_ROOT / "systems" / "architecture" / "universal-step-contract.yaml"
EXPECTED_KIND = "universal-step-visualization-contract"
EXPECTED_FIELDS = {
    "step_id",
    "scope",
    "title",
    "purpose",
    "entry_criteria",
    "inputs",
    "actions",
    "decisions",
    "outputs",
    "completion_rule",
    "quality_gate",
    "evidence_refs",
    "owner",
    "dependencies",
    "risks",
    "rollback_target",
    "next_step",
    "status",
    "quality",
}
EXPECTED_DIMENSIONS = {"understanding", "execution", "quality", "repeatability", "transfer"}
EXPECTED_STATUS_VALUES = {"complete", "current", "upcoming", "blocked", "unverified", "needs-review", "operating", "not-applicable"}
EXPECTED_QUALITY_STATUS_VALUES = {"passed", "missing", "unknown", "not-applicable"}
EXPECTED_COVERAGE_SURFACES = {"domain", "role", "project", "venture", "learning", "operating_cycle"}
EXPECTED_COVERAGE_STATUSES = {"implemented", "implemented-pilot", "not-implemented", "unverified"}


def load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ValueError(f"missing required file: {display_path(path)}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {display_path(path)}: {exc}") from exc


def as_list(value: Any, label: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    return value


def non_empty_strings(values: list[Any], label: str, errors: list[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} contains a non-empty string requirement")
            continue
        if value in result:
            errors.append(f"{label} contains duplicate value: {value}")
        result.add(value)
    return result


def validate_step_contract(contract: dict[str, Any]) -> list[str]:
    """Return structural errors for the universal step contract."""

    errors: list[str] = []
    if not isinstance(contract, dict):
        return ["contract must be a mapping"]
    if contract.get("kind") != EXPECTED_KIND:
        errors.append(f"contract kind must be {EXPECTED_KIND}")
    if contract.get("version") != 1:
        errors.append("contract version must be 1")
    for key in ("status", "contract_id", "owner", "description", "sources", "completion_policy", "mapping"):
        if not contract.get(key):
            errors.append(f"contract missing non-empty {key}")

    fields = as_list(contract.get("required_fields"), "required_fields", errors)
    field_ids: set[str] = set()
    for index, row in enumerate(fields, 1):
        if not isinstance(row, dict):
            errors.append(f"required_fields[{index}] must be a mapping")
            continue
        field_id = row.get("id")
        if not isinstance(field_id, str) or not field_id.strip():
            errors.append(f"required_fields[{index}] missing id")
            continue
        if field_id in field_ids:
            errors.append(f"duplicate required field: {field_id}")
        field_ids.add(field_id)
        for key in ("type", "required", "empty_policy", "visual_label", "meaning"):
            if key not in row or row[key] in (None, ""):
                errors.append(f"field {field_id} missing {key}")
        if row.get("empty_policy") not in {"forbidden", "explicit_na"}:
            errors.append(f"field {field_id} has invalid empty_policy")
        if row.get("required") is not True:
            errors.append(f"field {field_id} must be required")
    if field_ids != EXPECTED_FIELDS:
        errors.append(
            "required field coverage mismatch: "
            f"missing={sorted(EXPECTED_FIELDS - field_ids)} extra={sorted(field_ids - EXPECTED_FIELDS)}"
        )

    status_values = non_empty_strings(as_list(contract.get("status_values"), "status_values", errors), "status_values", errors)
    if status_values != EXPECTED_STATUS_VALUES:
        errors.append(
            "status coverage mismatch: "
            f"missing={sorted(EXPECTED_STATUS_VALUES - status_values)} extra={sorted(status_values - EXPECTED_STATUS_VALUES)}"
        )
    quality_status_values = non_empty_strings(
        as_list(contract.get("quality_status_values"), "quality_status_values", errors),
        "quality_status_values",
        errors,
    )
    if quality_status_values != EXPECTED_QUALITY_STATUS_VALUES:
        errors.append(
            "quality status coverage mismatch: "
            f"missing={sorted(EXPECTED_QUALITY_STATUS_VALUES - quality_status_values)} "
            f"extra={sorted(quality_status_values - EXPECTED_QUALITY_STATUS_VALUES)}"
        )

    dimensions = as_list(contract.get("quality_dimensions"), "quality_dimensions", errors)
    dimension_ids: set[str] = set()
    for index, row in enumerate(dimensions, 1):
        if not isinstance(row, dict):
            errors.append(f"quality_dimensions[{index}] must be a mapping")
            continue
        dimension_id = row.get("id")
        if not isinstance(dimension_id, str) or not dimension_id.strip():
            errors.append(f"quality_dimensions[{index}] missing id")
            continue
        if dimension_id in dimension_ids:
            errors.append(f"duplicate quality dimension: {dimension_id}")
        dimension_ids.add(dimension_id)
        for key in ("label", "question"):
            if not row.get(key):
                errors.append(f"quality dimension {dimension_id} missing {key}")
    if dimension_ids != EXPECTED_DIMENSIONS:
        errors.append(
            "quality dimension coverage mismatch: "
            f"missing={sorted(EXPECTED_DIMENSIONS - dimension_ids)} extra={sorted(dimension_ids - EXPECTED_DIMENSIONS)}"
        )

    visualization = contract.get("visualization")
    if not isinstance(visualization, dict):
        errors.append("visualization must be a mapping")
    else:
        levels = as_list(visualization.get("levels"), "visualization.levels", errors)
        level_ids = set()
        for index, row in enumerate(levels, 1):
            if not isinstance(row, dict):
                errors.append(f"visualization.levels[{index}] must be a mapping")
                continue
            level_id = row.get("id")
            if not isinstance(level_id, str) or not level_id.strip():
                errors.append(f"visualization.levels[{index}] missing id")
                continue
            level_ids.add(level_id)
            if not row.get("purpose"):
                errors.append(f"visualization level {level_id} missing purpose")
            required = as_list(row.get("required_fields"), f"visualization level {level_id}.required_fields", errors)
            for field_id in non_empty_strings(required, f"visualization level {level_id}.required_fields", errors):
                if field_id not in field_ids:
                    errors.append(f"visualization level {level_id} references unknown field: {field_id}")
        if level_ids != {"map", "detail", "evidence"}:
            errors.append(f"visualization level coverage mismatch: {sorted(level_ids)}")
        interaction = visualization.get("interaction")
        if not isinstance(interaction, dict) or interaction.get("default_open") != "current_or_blocked":
            errors.append("visualization.interaction.default_open must be current_or_blocked")

    return errors


def validate_coverage_inventory(inventory: Any) -> list[str]:
    """Return structural errors for the explicit surface coverage inventory."""

    errors: list[str] = []
    if not isinstance(inventory, dict):
        return ["coverage_inventory must be a mapping"]
    if inventory.get("contract") != "solo-empire.step-coverage.v1":
        errors.append("coverage_inventory contract must be solo-empire.step-coverage.v1")
    if inventory.get("status") not in {"partial", "complete"}:
        errors.append("coverage_inventory status must be partial or complete")
    if not inventory.get("policy"):
        errors.append("coverage_inventory policy is required")

    surfaces = inventory.get("surfaces")
    if not isinstance(surfaces, dict):
        return errors + ["coverage_inventory.surfaces must be a mapping"]
    surface_ids = set(surfaces)
    if surface_ids != EXPECTED_COVERAGE_SURFACES:
        errors.append(
            "coverage surface mismatch: "
            f"missing={sorted(EXPECTED_COVERAGE_SURFACES - surface_ids)} "
            f"extra={sorted(surface_ids - EXPECTED_COVERAGE_SURFACES)}"
        )

    for surface_id in sorted(EXPECTED_COVERAGE_SURFACES & surface_ids):
        row = surfaces.get(surface_id)
        if not isinstance(row, dict):
            errors.append(f"coverage surface {surface_id} must be a mapping")
            continue
        status = row.get("status")
        if status not in EXPECTED_COVERAGE_STATUSES:
            errors.append(f"coverage surface {surface_id} has invalid status")
        for key in ("scope", "evidence_state"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                errors.append(f"coverage surface {surface_id} missing {key}")
        source_projection = row.get("source_projection")
        visual_component = row.get("visual_component")
        if status in {"implemented", "implemented-pilot"}:
            for key, value in (("source_projection", source_projection), ("visual_component", visual_component)):
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"coverage surface {surface_id} missing {key}")
                elif not (PROJECT_ROOT / value).is_file():
                    errors.append(f"coverage surface {surface_id} {key} does not resolve: {value}")
        elif source_projection is not None or visual_component is not None:
            errors.append(f"coverage surface {surface_id} must not claim an adapter while {status}")
    return errors


def main() -> int:
    skip_without_architecture("universal_step_contract_guard.py")  # standalone: no architecture-docs checkout -> SKIP, not FAIL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    parser.add_argument("--coverage", action="store_true", help="emit the explicit surface coverage inventory")
    args = parser.parse_args()
    contract = None
    try:
        contract = load_yaml(CONTRACT_PATH)
        errors = validate_step_contract(contract)
        coverage = contract.get("coverage_inventory") if isinstance(contract, dict) else None
        errors.extend(validate_coverage_inventory(coverage))
        from universal_step_record import definitions, validate_definitions
        errors.extend(validate_definitions(definitions()))
    except (OSError, ValueError) as exc:
        errors = [str(exc)]

    result = {
        "contract": "solo-empire.step.v1",
        "path": str(display_path(CONTRACT_PATH)),
        "status": "ok" if not errors else "failed",
        "errors": errors,
    }
    if args.coverage:
        result["coverage"] = contract.get("coverage_inventory") if isinstance(contract, dict) else None
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Universal step contract: {result['path']}")
        if errors:
            for error in errors:
                print(f"  FAIL: {error}")
            print("Result: FAIL")
        else:
            print(f"  OK: {len(EXPECTED_FIELDS)} required fields")
            print(f"  OK: {len(EXPECTED_DIMENSIONS)} quality dimensions")
            print("Result: OK")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

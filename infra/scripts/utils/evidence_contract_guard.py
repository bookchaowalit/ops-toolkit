#!/usr/bin/env python3
"""Validate and audit the repository-wide evidence and trust contract.

The contract is deliberately provider-neutral.  It validates the policy
registry, composes a matrix for every registered workspace × shared lifecycle
stage, and reports the difference between a declared control and an observed
workspace signal.

Usage:
    task evidence:check
    task evidence:audit -- --json
    task evidence:audit:strict -- --json
    python3 infra/scripts/utils/evidence_contract_guard.py --audit --write

``--check`` validates the contract and registry coverage only.  ``--audit``
reports observed gaps without hiding them.  ``--strict`` is an explicit
release/readiness gate and fails while an active domain boundary or lifecycle
row remains unverified or lacks an accepted exception.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - repository toolchain normally provides PyYAML
    print("FAIL: PyYAML is required (import yaml)")
    raise SystemExit(1)

from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, repo_rel, repo_file, ARCHITECTURE_ROOT, skip_without_architecture  # noqa: E402


CONTRACT_PATH = ARCHITECTURE_ROOT / "docs" / "registries" / "DOMAIN-EVIDENCE-AUDIT-MATRIX.yaml"
WORKSPACES_PATH = KNOWLEDGE_ROOT / "projects" / "workspaces.yaml"
LIFECYCLE_PATH = ARCHITECTURE_ROOT / "docs" / "lifecycle" / "DEVELOPMENT-LIFECYCLE.yaml"
WORKFLOW_PATH = ARCHITECTURE_ROOT / "systems" / "architecture" / "book-dev-domain-workflows.yaml"
REPORT_JSON_PATH = PROJECT_ROOT / "data" / "generated" / "briefings" / "domain-evidence-audit-matrix.json"
REPORT_MD_PATH = PROJECT_ROOT / "data" / "generated" / "briefings" / "domain-evidence-audit-matrix.md"

EXPECTED_KIND = "universal-evidence-audit-matrix"


def rel(path: Path) -> str:
    return repo_rel(path)


def load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ValueError(f"missing required file: {rel(path)}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {rel(path)}: {exc}") from exc


def as_mapping(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be a mapping")
        return {}
    return value


def as_list(value: Any, label: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    return value


def unique_strings(values: list[Any], label: str, errors: list[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} contains a non-empty string requirement")
            continue
        if value in result:
            errors.append(f"{label} contains duplicate value: {value}")
        result.add(value)
    return result


def validate_contract(
    contract: dict[str, Any],
    workspaces: dict[str, Any],
    lifecycle: dict[str, Any],
    workflows: dict[str, Any],
) -> list[str]:
    """Return structural contract errors without inspecting runtime evidence."""

    errors: list[str] = []
    if contract.get("kind") != EXPECTED_KIND:
        errors.append(f"contract kind must be {EXPECTED_KIND}")
    if contract.get("version") != 1:
        errors.append("contract version must be 1")
    for key in ("status", "contract_id", "owner", "accountable_approver", "sources", "enforcement"):
        if not isinstance(contract.get(key), (str, dict)) or not contract.get(key):
            errors.append(f"contract missing non-empty {key}")

    sources = as_mapping(contract.get("sources"), "sources", errors)
    for key, expected in {
        "domain_registry": WORKSPACES_PATH,
        "lifecycle_registry": LIFECYCLE_PATH,
        "domain_workflow_registry": WORKFLOW_PATH,
    }.items():
        value = sources.get(key)
        if value != rel(expected):
            errors.append(f"sources.{key} must point to {rel(expected)}")
    for key, value in sources.items():
        if isinstance(value, str) and value and not value.startswith(("http://", "https://")):
            if not (repo_file(value)).is_file():
                errors.append(f"sources.{key} points to missing file: {value}")

    enforcement = as_mapping(contract.get("enforcement"), "enforcement", errors)
    for key in ("default_mode", "audit_mode", "strict_mode", "missing_data_status", "completion_rule"):
        if not enforcement.get(key):
            errors.append(f"enforcement missing {key}")
    not_applicable_requires = as_list(
        enforcement.get("not_applicable_requires"),
        "enforcement.not_applicable_requires",
        errors,
    )
    unique_strings(not_applicable_requires, "enforcement.not_applicable_requires", errors)

    field_rows = as_list(contract.get("field_catalog"), "field_catalog", errors)
    field_ids: set[str] = set()
    for index, row in enumerate(field_rows, 1):
        item = as_mapping(row, f"field_catalog[{index}]", errors)
        field_id = item.get("id")
        if not isinstance(field_id, str) or not field_id.strip():
            errors.append(f"field_catalog[{index}] missing id")
            continue
        if field_id in field_ids:
            errors.append(f"duplicate field id: {field_id}")
        field_ids.add(field_id)
        for key in ("type", "required", "empty_policy", "meaning"):
            if key not in item or item[key] in (None, ""):
                errors.append(f"field {field_id} missing {key}")
        if item.get("empty_policy") not in {"forbidden", "explicit_na"}:
            errors.append(f"field {field_id} has invalid empty_policy")

    evidence_rows = as_list(contract.get("evidence_catalog"), "evidence_catalog", errors)
    evidence_ids: set[str] = set()
    for index, row in enumerate(evidence_rows, 1):
        item = as_mapping(row, f"evidence_catalog[{index}]", errors)
        evidence_id = item.get("id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            errors.append(f"evidence_catalog[{index}] missing id")
            continue
        if evidence_id in evidence_ids:
            errors.append(f"duplicate evidence id: {evidence_id}")
        evidence_ids.add(evidence_id)
        for key in ("type", "minimum"):
            if not item.get(key):
                errors.append(f"evidence {evidence_id} missing {key}")

    for enum_name in ("status_values", "privacy_classes", "retention_classes"):
        values = as_list(contract.get(enum_name), enum_name, errors)
        unique_strings(values, enum_name, errors)

    lifecycle_rows = as_list(contract.get("lifecycle_stages"), "lifecycle_stages", errors)
    contract_stage_ids: set[str] = set()
    for index, row in enumerate(lifecycle_rows, 1):
        item = as_mapping(row, f"lifecycle_stages[{index}]", errors)
        stage_id = item.get("id")
        if not isinstance(stage_id, str) or not stage_id.strip():
            errors.append(f"lifecycle_stages[{index}] missing id")
            continue
        if stage_id in contract_stage_ids:
            errors.append(f"duplicate lifecycle stage id: {stage_id}")
        contract_stage_ids.add(stage_id)
        if not item.get("gate"):
            errors.append(f"lifecycle stage {stage_id} missing gate")
        required_fields = as_list(item.get("required_fields"), f"stage {stage_id}.required_fields", errors)
        required_evidence = as_list(item.get("required_evidence"), f"stage {stage_id}.required_evidence", errors)
        for field_id in unique_strings(required_fields, f"stage {stage_id}.required_fields", errors):
            if field_id not in field_ids:
                errors.append(f"stage {stage_id} references unknown field: {field_id}")
        for evidence_id in unique_strings(required_evidence, f"stage {stage_id}.required_evidence", errors):
            if evidence_id not in evidence_ids:
                errors.append(f"stage {stage_id} references unknown evidence: {evidence_id}")

    lifecycle_rows_source = lifecycle.get("stages")
    source_stage_ids = {
        item.get("id")
        for item in as_list(lifecycle_rows_source, "lifecycle registry stages", errors)
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if contract_stage_ids != source_stage_ids:
        errors.append(
            "lifecycle stage coverage mismatch: "
            f"missing={sorted(source_stage_ids - contract_stage_ids)} "
            f"extra={sorted(contract_stage_ids - source_stage_ids)}"
        )

    domain_coverage = as_mapping(contract.get("domain_coverage"), "domain_coverage", errors)
    for key in ("workspace_registry", "active_status", "planned_status", "required_domain_fields", "workflow_registry", "default_profile", "group_profiles"):
        if key not in domain_coverage or domain_coverage[key] in (None, "", []):
            errors.append(f"domain_coverage missing {key}")

    required_domain_fields = as_list(domain_coverage.get("required_domain_fields"), "domain_coverage.required_domain_fields", errors)
    unique_strings(required_domain_fields, "domain_coverage.required_domain_fields", errors)
    default_profile = as_mapping(domain_coverage.get("default_profile"), "domain_coverage.default_profile", errors)
    default_fields = as_list(default_profile.get("required_fields"), "domain_coverage.default_profile.required_fields", errors)
    default_evidence = as_list(default_profile.get("required_evidence"), "domain_coverage.default_profile.required_evidence", errors)
    for field_id in unique_strings(default_fields, "domain_coverage.default_profile.required_fields", errors):
        if field_id not in field_ids:
            errors.append(f"default profile references unknown field: {field_id}")
    for evidence_id in unique_strings(default_evidence, "domain_coverage.default_profile.required_evidence", errors):
        if evidence_id not in evidence_ids:
            errors.append(f"default profile references unknown evidence: {evidence_id}")

    workspace_rows = as_list(workspaces.get("workspaces"), "workspace registry workspaces", errors)
    workspace_groups: set[str] = set()
    allowed_statuses = {domain_coverage.get("active_status"), domain_coverage.get("planned_status")}
    for index, row in enumerate(workspace_rows, 1):
        item = as_mapping(row, f"workspace[{index}]", errors)
        for key in required_domain_fields:
            if not item.get(key):
                errors.append(f"workspace[{index}] missing {key}")
        if item.get("status") not in allowed_statuses:
            errors.append(f"workspace {item.get('slug', index)} has unsupported status: {item.get('status')}")
        if isinstance(item.get("group"), str):
            workspace_groups.add(item["group"])

    group_profiles = as_mapping(domain_coverage.get("group_profiles"), "domain_coverage.group_profiles", errors)
    missing_profiles = sorted(workspace_groups - set(group_profiles))
    if missing_profiles:
        errors.append(f"missing group profiles: {missing_profiles}")
    for group, profile in group_profiles.items():
        profile_map = as_mapping(profile, f"group profile {group}", errors)
        profile_evidence = as_list(profile_map.get("required_evidence"), f"group profile {group}.required_evidence", errors)
        for evidence_id in unique_strings(profile_evidence, f"group profile {group}.required_evidence", errors):
            if evidence_id not in evidence_ids:
                errors.append(f"group profile {group} references unknown evidence: {evidence_id}")
        signals = as_list(profile_map.get("artifact_signals"), f"group profile {group}.artifact_signals", errors)
        unique_strings(signals, f"group profile {group}.artifact_signals", errors)

    workflow_packs = as_mapping(workflows.get("capability_packs"), "workflow registry capability_packs", errors)
    workflow_groups = as_mapping(workflows.get("group_defaults"), "workflow registry group_defaults", errors)
    workflow_overrides = as_mapping(workflows.get("domain_overrides"), "workflow registry domain_overrides", errors)
    for group, pack_ids in workflow_groups.items():
        if not isinstance(pack_ids, list) or not pack_ids:
            errors.append(f"workflow group {group} must declare at least one capability pack")
            continue
        for pack_id in pack_ids:
            if pack_id not in workflow_packs:
                errors.append(f"workflow group {group} references unknown capability pack: {pack_id}")
    for slug, override in workflow_overrides.items():
        if not isinstance(override, dict):
            errors.append(f"workflow override {slug} must be a mapping")
            continue
        for pack_id in override.get("packs", []):
            if pack_id not in workflow_packs:
                errors.append(f"workflow override {slug} references unknown capability pack: {pack_id}")
    missing_workflow_groups = sorted(workspace_groups - set(workflow_groups))
    if missing_workflow_groups:
        errors.append(f"workflow registry missing group defaults: {missing_workflow_groups}")
    for workspace in workspace_rows:
        profile = compose_workflow_profile(workspace, workflows)
        if not profile["pack_ids"] or not profile["stages"] or not profile["evidence_patterns"]:
            errors.append(f"workspace {workspace.get('slug')} cannot compose a non-empty workflow/evidence profile")

    return errors


def compose_workflow_profile(workspace: dict[str, Any], workflows: dict[str, Any]) -> dict[str, Any]:
    packs = as_mapping(workflows.get("capability_packs"), "workflow capability_packs", [])
    defaults = as_mapping(workflows.get("group_defaults"), "workflow group_defaults", [])
    overrides = as_mapping(workflows.get("domain_overrides"), "workflow domain_overrides", [])
    override = overrides.get(workspace.get("slug"), {})
    if not isinstance(override, dict):
        override = {}
    pack_ids = override.get("packs") or defaults.get(workspace.get("group"), [])
    if not isinstance(pack_ids, list):
        pack_ids = []
    stages: list[str] = []
    evidence_patterns: list[str] = []
    workflow_ids: list[str] = []
    for pack_id in pack_ids:
        pack = packs.get(pack_id, {})
        if not isinstance(pack, dict):
            continue
        workflow_id = pack.get("workflow_id")
        if isinstance(workflow_id, str) and workflow_id not in workflow_ids:
            workflow_ids.append(workflow_id)
        for value in pack.get("stages", []):
            if isinstance(value, str) and value not in stages:
                stages.append(value)
        for value in pack.get("evidence", []):
            if isinstance(value, str) and value not in evidence_patterns:
                evidence_patterns.append(value)
    return {
        "pack_ids": [value for value in pack_ids if isinstance(value, str)],
        "workflow_id": "+".join(workflow_ids),
        "stages": stages,
        "evidence_patterns": evidence_patterns,
    }


def signal_present(workspace_path: Path, signal: str) -> bool:
    candidate = workspace_path / signal
    if candidate.exists():
        return True
    if "/" in signal or not workspace_path.is_dir():
        return False
    try:
        return any(child.name == signal for child in workspace_path.iterdir())
    except OSError:
        return False


def audit_domain(
    workspace: dict[str, Any],
    profile: dict[str, Any],
    group_profile: dict[str, Any],
) -> dict[str, Any]:
    status = workspace.get("status")
    workspace_path = repo_file(str(workspace.get("path", "")))
    if status == "planned":
        return {
            "slug": workspace.get("slug"),
            "name": workspace.get("name"),
            "bu": workspace.get("bu"),
            "group": workspace.get("group"),
            "path": workspace.get("path"),
            "registry_status": status,
            "audit_status": "planned",
            "workspace_exists": workspace_path.is_dir(),
            "readme_present": False,
            "workflow_id": profile.get("workflow_id"),
            "workflow_stages": profile.get("stages", []),
            "required_artifact_signals": group_profile.get("artifact_signals", []),
            "observed_artifact_signals": [],
            "missing_artifact_signals": [],
        }

    exists = workspace_path.is_dir()
    readme_present = signal_present(workspace_path, "README.md") if exists else False
    signals = [value for value in group_profile.get("artifact_signals", []) if isinstance(value, str)]
    observed = [value for value in signals if exists and signal_present(workspace_path, value)]
    missing = [value for value in signals if value not in observed]
    audit_status = "observed" if exists and readme_present and not missing else "unverified"
    return {
        "slug": workspace.get("slug"),
        "name": workspace.get("name"),
        "bu": workspace.get("bu"),
        "group": workspace.get("group"),
        "path": workspace.get("path"),
        "registry_status": status,
        "audit_status": audit_status,
        "workspace_exists": exists,
        "readme_present": readme_present,
        "workflow_id": profile.get("workflow_id"),
        "workflow_stages": profile.get("stages", []),
        "required_artifact_signals": signals,
        "observed_artifact_signals": observed,
        "missing_artifact_signals": missing,
    }


def build_report(
    contract: dict[str, Any],
    workspaces: dict[str, Any],
    workflows: dict[str, Any],
    structural_errors: list[str],
) -> dict[str, Any]:
    domain_coverage = contract.get("domain_coverage", {})
    group_profiles = domain_coverage.get("group_profiles", {})
    stages = contract.get("lifecycle_stages", [])
    workspace_rows = [item for item in workspaces.get("workspaces", []) if isinstance(item, dict)]
    domain_reports: list[dict[str, Any]] = []
    matrix_rows: list[dict[str, Any]] = []
    all_required_fields = [
        item["id"]
        for item in contract.get("field_catalog", [])
        if isinstance(item, dict) and item.get("required") is True and isinstance(item.get("id"), str)
    ]
    default_profile = domain_coverage.get("default_profile", {})
    default_fields = default_profile.get("required_fields", []) if isinstance(default_profile, dict) else []
    default_evidence = default_profile.get("required_evidence", []) if isinstance(default_profile, dict) else []

    for workspace in workspace_rows:
        group = workspace.get("group")
        profile = compose_workflow_profile(workspace, workflows)
        group_profile = group_profiles.get(group, {}) if isinstance(group_profiles, dict) else {}
        if not isinstance(group_profile, dict):
            group_profile = {}
        audit = audit_domain(workspace, profile, group_profile)
        domain_reports.append(audit)
        domain_fields = list(dict.fromkeys(all_required_fields + [value for value in default_fields if isinstance(value, str)]))
        domain_evidence = list(dict.fromkeys(
            [value for value in default_evidence if isinstance(value, str)]
            + [value for value in group_profile.get("required_evidence", []) if isinstance(value, str)]
        ))
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_fields = [value for value in stage.get("required_fields", []) if isinstance(value, str)]
            stage_evidence = [value for value in stage.get("required_evidence", []) if isinstance(value, str)]
            matrix_rows.append(
                {
                    "row_id": f"{workspace.get('slug')}:{stage.get('id')}",
                    "domain": workspace.get("slug"),
                    "domain_name": workspace.get("name"),
                    "business_unit": workspace.get("bu"),
                    "group": group,
                    "workspace_path": workspace.get("path"),
                    "registry_status": workspace.get("status"),
                    "lifecycle_stage": stage.get("id"),
                    "required_fields": list(dict.fromkeys(domain_fields + stage_fields)),
                    "required_evidence": list(dict.fromkeys(domain_evidence + stage_evidence)),
                    "gate": stage.get("gate"),
                    "domain_boundary_status": audit.get("audit_status"),
                    "step_status": "planned" if workspace.get("status") == "planned" else "unverified",
                    "completion_rule": "required evidence + named gate decision or accepted exception",
                }
            )

    counts = Counter(item.get("audit_status") for item in domain_reports)
    # Explicit pilot records only. Boundary observations never imply lifecycle completion.
    from universal_step_record import latest, evaluate, project_role_packages, apply_role_gate
    for row in matrix_rows:
        if row['domain'] != 'book-dev':
            continue
        mapped = []
        for pack_id in workflows.get('domain_overrides', {}).get('book-dev', {}).get('packs', []):
            pack = workflows.get('capability_packs', {}).get(pack_id, {})
            for step_id, definition in pack.get('step_definitions', {}).items():
                if row['lifecycle_stage'] not in definition.get('lifecycle_stage_ids', []):
                    continue
                canonical_step_id = f'book-dev:{pack_id}:{step_id}'
                record = latest('domain:book-dev', canonical_step_id)
                required = list(dict.fromkeys(definition['required_evidence'] + row['required_evidence']))
                projected = evaluate({**definition, 'required_evidence': required}, record)
                role_packages = project_role_packages('domain:book-dev', canonical_step_id)
                mapped.append(apply_role_gate(projected, role_packages))
        row['runtime_record_reason'] = 'ไม่มี explicit mapped record ที่ผ่าน lifecycle requirements'
        row['actual_evidence_count'] = len(set(
            ref for item in mapped
            for ref in item.get('aggregated_evidence_refs', item['evidence_refs'])
        ))
        if mapped and all(item['completed'] for item in mapped):
            row['step_status'] = 'complete'
            row['runtime_record_reason'] = 'ทุก mapped step ผ่าน required evidence และ accepted gate'
        elif any(item['status'] == 'blocked' for item in mapped):
            row['step_status'] = 'blocked'
        elif any(item['status'] == 'needs-review' for item in mapped):
            row['step_status'] = 'needs-review'
        elif any(item['evidence_refs'] for item in mapped):
            row['step_status'] = 'needs-review'
    step_counts = Counter(item.get("step_status") for item in matrix_rows)
    return {
        "contract": contract.get("contract_id"),
        "contract_kind": contract.get("kind"),
        "generated_from": {
            "contract": rel(CONTRACT_PATH),
            "domain_registry": rel(WORKSPACES_PATH),
            "lifecycle_registry": rel(LIFECYCLE_PATH),
            "domain_workflow_registry": rel(WORKFLOW_PATH),
        },
        "structural_errors": structural_errors,
        "summary": {
            "registered_domain_count": len(domain_reports),
            "active_domain_count": sum(1 for item in domain_reports if item.get("registry_status") == "active"),
            "planned_domain_count": sum(1 for item in domain_reports if item.get("registry_status") == "planned"),
            "lifecycle_stage_count": len(stages),
            "matrix_row_count": len(matrix_rows),
            "domain_status_counts": dict(sorted(counts.items())),
            "step_status_counts": dict(sorted(step_counts.items())),
            "unverified_active_domain_count": sum(
                1 for item in domain_reports
                if item.get("registry_status") == "active" and item.get("audit_status") == "unverified"
            ),
            "unverified_active_step_count": sum(
                1 for item in matrix_rows
                if item.get("registry_status") == "active" and item.get("step_status") == "unverified"
            ),
        },
        "domains": domain_reports,
        "matrix": matrix_rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Domain Evidence Audit Matrix",
        "",
        f"> Contract: `{report['contract']}`  ",
        f"> Source: `{report['generated_from']['contract']}`  ",
        "> This is a generated audit view. The YAML contract and canonical domain/lifecycle registries remain the source of truth.",
        "",
        "## Summary",
        "",
        f"- Registered domains: **{summary['registered_domain_count']}** ({summary['active_domain_count']} active, {summary['planned_domain_count']} planned)",
        f"- Lifecycle stages: **{summary['lifecycle_stage_count']}**",
        f"- Matrix rows: **{summary['matrix_row_count']}**",
        f"- Active domains with unverified boundary evidence: **{summary['unverified_active_domain_count']}**",
        f"- Active lifecycle rows not yet accepted: **{summary['unverified_active_step_count']}**",
        "",
        "## Domain boundary audit",
        "",
        "| Domain | Group | Registry | Boundary | Observed signals | Missing signals |",
        "|---|---|---|---|---:|---:|",
    ]
    for domain in report["domains"]:
        lines.append(
            f"| `{domain['slug']}` | `{domain['group']}` | `{domain['registry_status']}` | "
            f"`{domain['audit_status']}` | {len(domain['observed_artifact_signals'])} | {len(domain['missing_artifact_signals'])} |"
        )
    lines.extend(
        [
            "",
            "## Gate semantics",
            "",
            "- `policy-defined` means the matrix declares what is required; it is not completion evidence.",
            "- `observed` means the bounded domain workspace signals are present; each lifecycle row still needs its own evidence and gate decision.",
            "- `unverified` means evidence is missing, stale, unresolved, or not yet accepted.",
            "- `planned` means the domain is registered but not active; it must be covered before activation.",
            "- `accepted-exception` is the only safe way to proceed with a known gap: owner, reason, compensating control, and review date are required.",
            "",
            "## Commands",
            "",
            "```bash",
            "task evidence:check",
            "task evidence:audit -- --json",
            "task evidence:audit:strict -- --json",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def print_human(report: dict[str, Any], strict: bool) -> None:
    summary = report["summary"]
    print("=" * 70)
    print("Solo Empire — Universal Evidence Contract Guard")
    print("=" * 70)
    if report["structural_errors"]:
        for error in report["structural_errors"]:
            print(f"  FAIL: {error}")
        print(f"Result: FAIL ({len(report['structural_errors'])} structural errors)")
        return
    print(f"  ✅ registered domains: {summary['registered_domain_count']} ({summary['active_domain_count']} active / {summary['planned_domain_count']} planned)")
    print(f"  ✅ lifecycle stages: {summary['lifecycle_stage_count']}")
    print(f"  ✅ matrix rows: {summary['matrix_row_count']}")
    print(f"  ℹ️ active domains with unverified boundary evidence: {summary['unverified_active_domain_count']}")
    print(f"  ℹ️ active lifecycle rows awaiting evidence/acceptance: {summary['unverified_active_step_count']}")
    if strict and (
        summary["unverified_active_domain_count"]
        or summary["unverified_active_step_count"]
    ):
        print("  FAIL: strict mode requires every active domain boundary and lifecycle row to be evidenced or explicitly excepted")
        print("Result: FAIL (strict evidence gate)")
        return
    print("Result: OK (structural contract and coverage are valid; observed gaps remain visible)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the contract and registry coverage")
    parser.add_argument("--audit", action="store_true", help="build the full domain × lifecycle audit matrix")
    parser.add_argument("--strict", action="store_true", help="fail when active domains remain unverified")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of human-readable output")
    parser.add_argument("--write", action="store_true", help="write generated JSON and Markdown audit views")
    return parser.parse_args()


def main() -> int:
    skip_without_architecture("evidence_contract_guard.py")  # standalone: no architecture-docs checkout -> SKIP, not FAIL
    args = parse_args()
    should_audit = args.audit or args.strict or args.write or not args.check
    try:
        contract = load_yaml(CONTRACT_PATH)
        workspaces = load_yaml(WORKSPACES_PATH)
        lifecycle = load_yaml(LIFECYCLE_PATH)
        workflows = load_yaml(WORKFLOW_PATH)
    except ValueError as exc:
        if args.json:
            print(json.dumps({"structural_errors": [str(exc)]}, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL: {exc}")
        return 1

    if not isinstance(contract, dict) or not isinstance(workspaces, dict) or not isinstance(lifecycle, dict) or not isinstance(workflows, dict):
        errors = ["contract and all source registries must have mapping roots"]
    else:
        errors = validate_contract(contract, workspaces, lifecycle, workflows)

    report = build_report(contract, workspaces, workflows, errors) if isinstance(contract, dict) and isinstance(workspaces, dict) and isinstance(workflows, dict) else {
        "contract": None,
        "structural_errors": errors,
        "summary": {},
        "domains": [],
        "matrix": [],
    }
    if args.write and not errors:
        REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        REPORT_MD_PATH.write_text(render_markdown(report), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.check and not should_audit:
        print("Solo Empire — Universal Evidence Contract Guard")
        if errors:
            for error in errors:
                print(f"  FAIL: {error}")
            print(f"Result: FAIL ({len(errors)} structural errors)")
        else:
            print(f"  ✅ contract fields: {len(contract.get('field_catalog', []))}")
            print(f"  ✅ evidence classes: {len(contract.get('evidence_catalog', []))}")
            print(f"  ✅ lifecycle stages: {len(contract.get('lifecycle_stages', []))}")
            print(f"  ✅ all registered domain groups have workflow and evidence profiles")
            print("Result: OK")
    else:
        print_human(report, args.strict)

    if errors:
        return 1
    if args.strict and (
        report["summary"].get("unverified_active_domain_count", 0)
        or report["summary"].get("unverified_active_step_count", 0)
    ):
        return 1
    return 0


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("evidence_contract_guard.py")
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline tests for the universal evidence contract and audit matrix."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "docs/registries", "architecture-docs checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

from evidence_contract_guard import (  # noqa: E402
    CONTRACT_PATH,
    LIFECYCLE_PATH,
    WORKFLOW_PATH,
    WORKSPACES_PATH,
    build_report,
    load_yaml,
    validate_contract,
)


def main() -> int:
    contract = load_yaml(CONTRACT_PATH)
    workspaces = load_yaml(WORKSPACES_PATH)
    lifecycle = load_yaml(LIFECYCLE_PATH)
    workflows = load_yaml(WORKFLOW_PATH)

    errors = validate_contract(contract, workspaces, lifecycle, workflows)
    assert not errors, errors

    report = build_report(contract, workspaces, workflows, errors)
    summary = report["summary"]
    registered = workspaces["workspaces"]
    active = [item for item in registered if item["status"] == "active"]
    planned = [item for item in registered if item["status"] == "planned"]
    stage_count = len(contract["lifecycle_stages"])
    assert summary["registered_domain_count"] == len(registered)
    assert summary["active_domain_count"] == len(active)
    assert summary["planned_domain_count"] == len(planned)
    assert summary["lifecycle_stage_count"] == stage_count
    assert summary["matrix_row_count"] == len(registered) * stage_count
    assert len(report["domains"]) == len(registered)
    assert len(report["matrix"]) == len(registered) * stage_count
    assert len({row["row_id"] for row in report["matrix"]}) == len(registered) * stage_count

    active_rows = [row for row in report["matrix"] if row["registry_status"] == "active"]
    planned_rows = [row for row in report["matrix"] if row["registry_status"] == "planned"]
    assert len(active_rows) == len(active) * stage_count
    assert len(planned_rows) == len(planned) * stage_count
    assert all(row["required_fields"] for row in report["matrix"])
    assert all(row["required_evidence"] for row in report["matrix"])
    assert all(row["step_status"] == "unverified" for row in active_rows)
    assert all(row["step_status"] == "planned" for row in planned_rows)

    print("Evidence contract tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

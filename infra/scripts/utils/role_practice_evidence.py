#!/usr/bin/env python3
"""Record bounded, local practice evidence for an atomic role.

This workflow connects a reviewable artifact to the role's linked skills and
active task without claiming that the role is production-ready. It is
idempotent for the same role/skill/evidence path and never changes skill
progress automatically.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from _env import PROJECT_ROOT
from cell_store import CellStoreError, open_cell_store
from role_integration_sync import validate_registries
from task_store import ClientConfigError, open_task_store


# Stable provenance key stored in the roles-skills cell; the registry is loaded through the integration
# validator from its canonical category path.
ROLE_SKILL_SOURCE = "docs/ROLE-LEARNING.yaml"
EVIDENCE_BLOCKLIST = {".env", ".env.local", ".env.production", "credentials", "secrets"}


def parse_records(values: list[str]) -> list[tuple[str, int | None]]:
    records: list[tuple[str, int | None]] = []
    for value in values:
        role_id, separator, task_value = value.partition(":")
        role_id = role_id.strip()
        if not role_id:
            raise ValueError(f"--record expects ROLE_ID[:TASK_ID], got {value!r}")
        if not separator:
            records.append((role_id, None))
            continue
        if not task_value.isdigit():
            raise ValueError(f"--record task id must be numeric, got {value!r}")
        records.append((role_id, int(task_value)))
    return records


def safe_evidence_path(value: str) -> tuple[Path, str]:
    candidate = (PROJECT_ROOT / value).resolve()
    try:
        relative = candidate.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("evidence path must remain inside the Solo Empire repository") from exc
    if any(part in EVIDENCE_BLOCKLIST for part in relative.parts):
        raise ValueError("evidence path points to a blocked secret or environment boundary")
    if not candidate.is_file():
        raise ValueError(f"evidence file does not exist: {value}")
    return candidate, relative.as_posix()


def load_role_registries() -> tuple[dict[str, dict], dict[str, dict]]:
    _catalog, _learning, _routing, paths, catalog_by_id, _learning_by_id = validate_registries()
    path_by_id = {
        row["role_id"]: row
        for row in paths.get("roles", [])
        if isinstance(row, dict) and row.get("role_id")
    }
    return catalog_by_id, path_by_id


def select_task(store,
    role_id: str,
    requested_task_id: int | None,
) -> dict:
    """Pick the role's task. Assignments and tasks are read only through the store."""
    if requested_task_id is not None:
        assigned = store.assignments(
            task=requested_task_id, assignee_type="role", assignee_id=role_id, assignment_type="assigned"
        )
        row = store.get(requested_task_id) if assigned else None
        if row is None:
            raise ValueError(
                f"task #{requested_task_id} is not assigned to atomic role {role_id}"
            )
        return row

    task_ids = sorted(
        {
            int(r["task"])
            for r in store.assignments(assignee_type="role", assignee_id=role_id, assignment_type="assigned")
        }
    )
    active = []
    for task_id in task_ids:
        candidate = store.get(task_id)
        if candidate is not None and candidate["status"] in ("todo", "in_progress", "blocked"):
            active.append(candidate)
    if not active:
        raise ValueError(f"no active task is assigned to atomic role {role_id}")
    return min(active, key=lambda t: (t["priority"] if t["priority"] is not None else -1, t["id"]))


def record_for_role(role_id: str,
    requested_task_id: int | None,
    evidence_path: str,
    catalog_by_id: dict[str, dict],
    path_by_id: dict[str, dict],
    mark_in_progress: bool,
    store=None,
    roles_skills=None,
) -> dict[str, int | str]:
    if role_id not in catalog_by_id:
        raise ValueError(f"unknown atomic role: {role_id}")
    if role_id not in path_by_id:
        raise ValueError(f"role path is missing for atomic role: {role_id}")

    store = store or open_task_store(None, "role-evidence")
    task = select_task(store, role_id, requested_task_id)
    roles_skills = roles_skills or open_cell_store("roles-skills", "role-evidence")
    skill_by_id = {row["id"]: row for row in roles_skills.list("skills")}
    links = sorted(
        (
            link
            for link in roles_skills.list("role-skill-links", role_id=role_id)
            if link.get("source_registry") == ROLE_SKILL_SOURCE
        ),
        key=lambda link: link["id"],
    )
    skills = [skill_by_id[link["skill_id"]] for link in links if link["skill_id"] in skill_by_id]
    if not skills:
        raise ValueError(f"no linked learning skills exist for atomic role {role_id}")

    role_name = str(catalog_by_id[role_id].get("name", role_id))
    path = path_by_id[role_id]
    now = datetime.now().isoformat(timespec="seconds")
    title = f"Role practice evidence: {role_name}"
    description = (
        f"Bounded local practice for task #{task['id']}. "
        f"Practice action: {path['practice_action']} "
        f"Success signal: {path['success_signal']} "
        f"Evidence: {evidence_path}"
    )
    next_steps = (
        "Review the artifact, resolve any failing checks, and repeat the workflow "
        "before claiming operational or production readiness."
    )
    inserted = 0
    for skill in skills:
        already = any(
            entry.get("evidence_url") == evidence_path and entry.get("title") == title
            for entry in roles_skills.list("progress-log", skill_id=skill["id"])
        )
        if already:
            continue
        roles_skills.create(
            "progress-log",
            {
                "skill_id": skill["id"],
                "activity_type": "practice",
                "title": title,
                "description": description,
                "what_learned": str(path["practice_action"]),
                "challenges": "This is bounded local evidence; external providers, production state, and live service health are not inferred.",
                "next_steps": next_steps,
                "evidence_url": evidence_path,
                "progress_before": skill["progress_percent"],
                "progress_after": skill["progress_percent"],
                "logged_at": now,
            },
            idempotency_key=f"role-practice:{role_id}:{skill['id']}:{evidence_path}",
        )
        roles_skills.update("skills", skill["id"], {"last_practiced": date.today().isoformat()})
        inserted += 1

    note = f"practice evidence: {evidence_path} (recorded {date.today().isoformat()})"
    if note not in str(task["notes"] or ""):
        combined_notes = "; ".join(part for part in [str(task["notes"] or "").strip(), note] if part)
        changes: dict[str, object] = {"notes": combined_notes}
        if mark_in_progress and task["status"] == "todo":
            changes["status"] = "in_progress"
        store.update(task["id"], changes, legacy={"updated_at": now})

    return {"role_id": role_id, "task_id": task["id"], "skills": len(skills), "logs_inserted": inserted}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record",
        action="append",
        required=True,
        metavar="ROLE_ID[:TASK_ID]",
        help="role to record; optionally choose its assigned task",
    )
    parser.add_argument("--evidence", required=True, help="existing repository-relative evidence file")
    parser.add_argument(
        "--mark-in-progress",
        action="store_true",
        help="move a matching todo task to in_progress; never marks a task done",
    )
    args = parser.parse_args()

    records = parse_records(args.record)
    _evidence_file, evidence_path = safe_evidence_path(args.evidence)
    catalog_by_id, path_by_id = load_role_registries()
    results = [
        record_for_role(
            role_id,
            task_id,
            evidence_path,
            catalog_by_id,
            path_by_id,
            args.mark_in_progress,
        )
        for role_id, task_id in records
    ]

    print("Role practice evidence (applied)")
    print(f"  evidence: {evidence_path}")
    for result in results:
        print(
            f"  - {result['role_id']}: task #{result['task_id']}; "
            f"skills={result['skills']}; new progress logs={result['logs_inserted']}"
        )
    print("  task completion: unchanged (use explicit task workflow after review)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, CellStoreError, ClientConfigError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)

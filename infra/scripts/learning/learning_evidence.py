#!/usr/bin/env python3
"""Record explicit, reviewable evidence for a tracked learning skill.

This workflow never changes skill progress automatically. It only links an
existing repository artifact to a skill through the roles-skills cell's ``progress-log`` evidence_url.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import PROJECT_ROOT, repo_file, repo_rel  # noqa: E402
from cell_store import CellStoreError, open_cell_store  # noqa: E402
from task_store import ClientConfigError  # noqa: E402

AUTOMATION = "owner-cli"


BLOCKED_PARTS = {".env", ".env.local", ".env.production", "credentials", "secrets"}
ACTIVITY_TYPES = ("practice", "project", "teaching")


def safe_evidence_path(value: str) -> str:
    candidate = repo_file(value).resolve()
    try:
        relative = Path(repo_rel(candidate))
    except ValueError as exc:
        raise ValueError("evidence path must remain inside the Solo Empire repository") from exc
    if any(part in BLOCKED_PARTS for part in relative.parts):
        raise ValueError("evidence path points to a blocked secret or environment boundary")
    if not candidate.is_file():
        raise ValueError(f"evidence file does not exist: {value}")
    return relative.as_posix()


def resolve_skill(store: Any, value: str) -> dict[str, Any]:
    """Find a skill by numeric id or slug in the roles-skills cell."""
    if value.isdigit():
        row = store.get("skills", int(value))
    else:
        rows = [r for r in store.list("skills") if r["slug"] == value]
        row = rows[0] if rows else None
    if row is None:
        raise ValueError(f"skill not found: {value}")
    return row


def record_evidence(
    skill: dict[str, Any],
    evidence_path: str,
    title: str,
    what_learned: str,
    activity_type: str,
    skills_store: Any = None,
) -> dict[str, Any]:
    if activity_type not in ACTIVITY_TYPES:
        raise ValueError(f"activity type must be one of: {', '.join(ACTIVITY_TYPES)}")

    skills_store = skills_store or open_cell_store("roles-skills", AUTOMATION, channel="cli")
    existing = [
        entry
        for entry in skills_store.list("progress-log", skill_id=skill["id"])
        if entry.get("evidence_url") == evidence_path and entry.get("title") == title
    ]
    if existing:
        return {
            "skill_id": skill["id"],
            "skill": skill["name"],
            "evidence": evidence_path,
            "log_id": existing[0]["id"],
            "inserted": False,
            "progress": skill["progress_percent"],
        }

    now = datetime.now().isoformat(timespec="seconds")
    created = skills_store.create(
        "progress-log",
        {
            "skill_id": skill["id"],
            "activity_type": activity_type,
            "title": title,
            "description": "Explicit repository evidence recorded through the learning evidence workflow.",
            "what_learned": what_learned,
            "next_steps": "Review the artifact and decide separately whether skill progress should change.",
            "evidence_url": evidence_path,
            "progress_before": skill["progress_percent"],
            "progress_after": skill["progress_percent"],
            "logged_at": now,
        },
        idempotency_key=f"learning-evidence:{skill['id']}:{evidence_path}:{title}",
    )
    log_id = created["id"]
    skills_store.update("skills", skill["id"], {"last_practiced": date.today().isoformat()})
    return {
        "skill_id": skill["id"],
        "skill": skill["name"],
        "evidence": evidence_path,
        "log_id": log_id,
        "inserted": True,
        "progress": skill["progress_percent"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", required=True, help="skill slug or numeric skill id")
    parser.add_argument("--evidence", required=True, help="repository-relative artifact path")
    parser.add_argument("--title", required=True, help="short evidence title")
    parser.add_argument("--what-learned", required=True, help="concise learning outcome")
    parser.add_argument("--activity-type", choices=ACTIVITY_TYPES, default="practice")
    parser.add_argument("--dry-run", action="store_true", help="validate and preview without writing")
    parser.add_argument("--json", action="store_true", help="print result as JSON")
    args = parser.parse_args(argv)

    try:
        evidence_path = safe_evidence_path(args.evidence)
        skills_store = open_cell_store("roles-skills", AUTOMATION, channel="cli")
        skill = resolve_skill(skills_store, args.skill)
        preview = {
            "skill_id": skill["id"],
            "skill": skill["name"],
            "evidence": evidence_path,
            "title": args.title,
            "activity_type": args.activity_type,
            "progress": skill["progress_percent"],
            "dry_run": args.dry_run,
        }
        if args.dry_run:
            result = preview
        else:
            result = record_evidence(
                skill,
                evidence_path,
                args.title,
                args.what_learned,
                args.activity_type,
                skills_store,
            )
            result["dry_run"] = False
    except (OSError, ValueError, CellStoreError, ClientConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        action = "validated" if result.get("dry_run") else "recorded"
        duplicate = " (already existed)" if result.get("inserted") is False else ""
        print(f"Evidence {action}{duplicate}")
        print(f"  skill: {result['skill']} (#{result['skill_id']})")
        print(f"  evidence: {result['evidence']}")
        print(f"  progress unchanged: {result['progress']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

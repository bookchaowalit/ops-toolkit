#!/usr/bin/env python3
"""Create or update canonical SQLite tasks for engagement next actions.

The sync is idempotent. It uses ``external_source`` + ``external_id`` as the
stable key and writes the resulting task IDs back to the tracked engagement
registry without touching nested repositories.

Usage:
    python3 infra/scripts/utils/engagement_tasks_sync.py
    task engagements:sync-tasks
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    sys.exit(1)

from _env import PROJECT_ROOT, ARCHITECTURE_ROOT, display_path
from task_store import open_task_store


REGISTRY_PATH = ARCHITECTURE_ROOT / "docs" / "registries" / "ENGAGEMENT-REGISTRY.yaml"
EXTERNAL_SOURCE = "solo_empire_engagements"


def load_registry() -> dict:
    data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("engagements"), list):
        raise ValueError(f"Invalid engagement registry: {REGISTRY_PATH}")
    return data


def replace_task_id(text: str, slug: str, task_id: int) -> str:
    block_pattern = re.compile(
        rf"(?ms)(^  - slug: {re.escape(slug)}\n.*?)(?=^  - slug: |\Z)"
    )
    match = block_pattern.search(text)
    if not match:
        raise ValueError(f"Engagement slug not found in registry text: {slug}")
    block = match.group(1)
    field = re.search(r"(?m)^    task_ids:\s*(.*)$", block)
    if field is None:
        raise ValueError(f"Expected exactly one task_ids field for engagement: {slug}")
    current_ids = yaml.safe_load(field.group(1))
    if not isinstance(current_ids, list):
        raise ValueError(f"task_ids must be a list for engagement: {slug}")
    normalized_ids: list[int] = []
    for current_id in current_ids:
        try:
            normalized_id = int(current_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"task_ids must contain integers for engagement: {slug}") from exc
        if normalized_id not in normalized_ids:
            normalized_ids.append(normalized_id)
    if task_id not in normalized_ids:
        normalized_ids.append(task_id)
    replacement = f"    task_ids: [{', '.join(str(item) for item in normalized_ids)}]"
    updated, count = re.subn(
        r"(?m)^    task_ids:.*$",
        replacement,
        block,
        count=1,
    )
    if count != 1:
        raise ValueError(f"Expected exactly one task_ids field for engagement: {slug}")
    return text[: match.start(1)] + updated + text[match.end(1) :]


def task_description(record: dict) -> str:
    risks = record.get("risks") or []
    lines = [
        f"Engagement: {record['name']} ({record['slug']})",
        f"Work type: {record['work_type']} | Client: {record['client']}",
        f"Source checkout: {record['source_path']}",
        f"Registry: docs/registries/ENGAGEMENT-REGISTRY.yaml",
    ]
    if record.get("delivery_workspace"):
        lines.append(f"Delivery workspace: {record['delivery_workspace']}")
    if risks:
        lines.append("Risks: " + "; ".join(str(risk) for risk in risks))
    return "\n".join(lines)


def sync_tasks(data: dict) -> dict[str, int]:
    task_ids: dict[str, int] = {}
    store = open_task_store(None, "engagement-sync")

    for record in data["engagements"]:
        action = record.get("next_action")
        if not isinstance(action, str) or not action.strip():
            continue
        slug = record["slug"]
        stable_id = f"engagement:{slug}:next-action"
        title = f"[Engagement] {record['name']}: {action}"
        description = task_description(record)
        priority = int(record.get("priority", 3))
        tags = json.dumps(
            ["engagement", record["work_type"], slug, "review"],
            ensure_ascii=False,
        )
        fields = {
            "title": title,
            "description": description,
            "priority": priority,
            "project": f"engagements/{slug}",
            "tags": tags,
            "category": "operations",
            "due_date": record.get("next_review_on"),
        }
        outcome, task_id = store.upsert_external(EXTERNAL_SOURCE, stable_id, fields)
        if outcome == "created":
            print(f"  + created task #{task_id}: {slug}")
        elif outcome == "updated":
            print(f"  ↻ updated task #{task_id}: {slug}")
        else:
            print(f"  = unchanged task #{task_id}: {slug}")
        task_ids[slug] = task_id

    return task_ids


def update_registry(task_ids: dict[str, int]) -> None:
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    original = text
    for slug, task_id in task_ids.items():
        text = replace_task_id(text, slug, task_id)
    if text != original:
        REGISTRY_PATH.write_text(text, encoding="utf-8")


def main() -> int:
    try:
        data = load_registry()
        task_ids = sync_tasks(data)
        update_registry(task_ids)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}")
        return 1

    print(f"✅ Engagement task sync complete: {len(task_ids)} task links written")
    print(f"   Registry: {display_path(REGISTRY_PATH)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

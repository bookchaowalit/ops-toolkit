#!/usr/bin/env python3
"""Review engagement health against the registry and canonical task database.

Operational findings are warnings: they describe work that needs attention but
do not make the repository invalid. Integrity findings are errors: they mean a
registry task link cannot be trusted. ``--strict`` fails only on integrity
findings, which makes the command safe to use from ``docs:check``.

Usage:
    python3 infra/scripts/utils/engagement_review.py
    python3 infra/scripts/utils/engagement_review.py --strict
    python3 infra/scripts/utils/engagement_review.py --as-of 2026-08-06 --days 7
    task engagements:review
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    sys.exit(1)

from _env import PROJECT_ROOT, ARCHITECTURE_ROOT, skip_without_architecture
from task_store import TaskStore, open_task_store


REGISTRY_PATH = ARCHITECTURE_ROOT / "docs" / "registries" / "ENGAGEMENT-REGISTRY.yaml"
EXTERNAL_SOURCE = "solo_empire_engagements"
OPEN_TASK_STATUSES = frozenset({"todo", "in_progress", "blocked"})
CLOSED_TASK_STATUSES = frozenset({"done", "cancelled"})
DEFAULT_DUE_SOON_DAYS = 7
PORTABLE_CI = os.environ.get("SOLO_EMPIRE_CI") == "1"


def load_registry() -> dict:
    data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("engagements"), list):
        raise ValueError(f"Invalid engagement registry: {REGISTRY_PATH}")
    return data


def parse_date(value: object, label: str) -> dt.date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value!r}") from exc


def task_ids(record: dict) -> list[int]:
    raw_ids = record.get("task_ids") or []
    if not isinstance(raw_ids, list):
        raise ValueError(f"{record.get('slug', '<unknown>')}: task_ids must be a list")
    ids: list[int] = []
    for raw_id in raw_ids:
        try:
            parsed = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{record.get('slug', '<unknown>')}: task_ids must contain integers"
            ) from exc
        if parsed <= 0:
            raise ValueError(f"{record.get('slug', '<unknown>')}: task IDs must be positive")
        if parsed not in ids:
            ids.append(parsed)
    return ids


def fetch_tasks(store: TaskStore, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    return {int(task["id"]): task for task in store.list(numbers=ids, include_archived=True)}


def finding(
    severity: str,
    category: str,
    slug: str,
    message: str,
    *,
    task_id: int | None = None,
) -> dict:
    result = {
        "severity": severity,
        "category": category,
        "slug": slug,
        "message": message,
    }
    if task_id is not None:
        result["task_id"] = task_id
    return result


def review(data: dict, as_of: dt.date, due_soon_days: int) -> dict:
    records = data["engagements"]
    due_soon_limit = as_of + dt.timedelta(days=due_soon_days)
    findings: list[dict] = []
    task_ids_to_fetch: list[int] = []
    record_ids: dict[str, list[int]] = {}

    for record in records:
        slug = str(record.get("slug", "<unknown>"))
        ids = task_ids(record)
        record_ids[slug] = ids
        task_ids_to_fetch.extend(ids)

    task_validation_available = not PORTABLE_CI
    if task_validation_available:
        tasks = fetch_tasks(
            open_task_store(None, "engagement-reader"), sorted(set(task_ids_to_fetch))
        )
    else:
        tasks = {}
        findings.append(
            finding(
                "warning",
                "runtime",
                "<environment>",
                "CI portability mode: Tasks API task-link validation is skipped",
            )
        )

    for record in records:
        slug = str(record.get("slug", "<unknown>"))
        status = str(record.get("status", "unknown"))
        ids = record_ids[slug]
        current_statuses = {"active", "paused"}
        next_action = record.get("next_action")
        review_date = parse_date(record.get("next_review_on"), f"{slug}.next_review_on")

        if status in current_statuses:
            if review_date is None:
                findings.append(finding("warning", "metadata", slug, "next review date is not scheduled"))
            elif review_date < as_of:
                findings.append(
                    finding("warning", "overdue_review", slug, f"review date {review_date.isoformat()} is overdue")
                )
            elif review_date <= due_soon_limit:
                findings.append(
                    finding(
                        "warning",
                        "due_soon_review",
                        slug,
                        f"review date {review_date.isoformat()} is due within {due_soon_days} days",
                    )
                )
            if not isinstance(next_action, str) or not next_action.strip():
                findings.append(finding("warning", "metadata", slug, "next action is not set"))
            if not record.get("delivery_workspace"):
                findings.append(finding("warning", "delivery_workspace", slug, "active engagement has no delivery workspace"))

        risks = record.get("risks") or []
        if risks and status in {"active", "paused", "proposed"}:
            findings.append(
                finding("warning", "risk", slug, "known risk: " + "; ".join(str(risk) for risk in risks))
            )

        if next_action and status in current_statuses and not ids:
            findings.append(finding("error", "missing_task_link", slug, "next action has no linked SQLite task"))

        if not task_validation_available:
            continue

        for task_id in ids:
            task = tasks.get(task_id)
            if task is None:
                findings.append(
                    finding("error", "missing_task", slug, f"linked task #{task_id} does not exist", task_id=task_id)
                )
                continue
            if task["external_source"] != EXTERNAL_SOURCE:
                findings.append(
                    finding(
                        "error",
                        "task_source",
                        slug,
                        f"linked task #{task_id} has unexpected source {task['external_source']!r}",
                        task_id=task_id,
                    )
                )
            expected_id_prefix = f"engagement:{slug}:"
            external_id = task["external_id"]
            if (
                not isinstance(external_id, str)
                or not external_id.startswith(expected_id_prefix)
                or not external_id[len(expected_id_prefix):].strip()
            ):
                findings.append(
                    finding(
                        "error",
                        "task_identity",
                        slug,
                        f"linked task #{task_id} has unexpected external_id; "
                        f"expected {expected_id_prefix!r} with a task key",
                        task_id=task_id,
                    )
                )
            expected_project = f"engagements/{slug}"
            if task["project"] != expected_project:
                findings.append(
                    finding(
                        "error",
                        "task_scope",
                        slug,
                        f"linked task #{task_id} is outside project {expected_project!r}",
                        task_id=task_id,
                    )
                )

            task_status = str(task["status"] or "todo")
            task_due = parse_date(task["due_date"], f"task #{task_id}.due_date")
            if task_status == "blocked":
                findings.append(
                    finding("warning", "blocked", slug, f"task #{task_id} is blocked: {task['title']}", task_id=task_id)
                )
            if task_status in OPEN_TASK_STATUSES and task_due is not None:
                if task_due < as_of:
                    findings.append(
                        finding(
                            "warning",
                            "overdue_task",
                            slug,
                            f"task #{task_id} is overdue since {task_due.isoformat()}: {task['title']}",
                            task_id=task_id,
                        )
                    )
                elif task_due <= due_soon_limit:
                    findings.append(
                        finding(
                            "warning",
                            "due_soon_task",
                            slug,
                            f"task #{task_id} is due {task_due.isoformat()}: {task['title']}",
                            task_id=task_id,
                        )
                    )
            if next_action and task_status in CLOSED_TASK_STATUSES:
                findings.append(
                    finding(
                        "warning",
                        "stale_task_link",
                        slug,
                        f"task #{task_id} is {task_status} while next action is still set",
                        task_id=task_id,
                    )
                )

    return {
        "as_of": as_of.isoformat(),
        "due_soon_days": due_soon_days,
        "engagement_count": len(records),
        "active_count": sum(1 for record in records if record.get("status") == "active"),
        "findings": findings,
        "error_count": sum(1 for item in findings if item["severity"] == "error"),
        "warning_count": sum(1 for item in findings if item["severity"] == "warning"),
    }


def render(report: dict) -> str:
    findings = report["findings"]
    lines = [
        "Engagement Review",
        "=================",
        f"As of: {report['as_of']} | Due-soon window: {report['due_soon_days']} days",
        f"Engagements: {report['engagement_count']} ({report['active_count']} active)",
        f"Findings: {report['warning_count']} warnings, {report['error_count']} errors",
        "",
    ]
    if not findings:
        lines.append("✅ No engagement findings.")
        return "\n".join(lines)

    for item in findings:
        marker = "ERROR" if item["severity"] == "error" else "WARN"
        task_suffix = f" [#{item['task_id']}]" if "task_id" in item else ""
        lines.append(f"[{marker}] {item['slug']}{task_suffix}: {item['message']}")
    lines.extend(
        [
            "",
            "Actions:",
            "- Update task status in SQLite; do not edit generated dashboard views.",
            "- After changing next_action or next_review_on, run engagements:sync-tasks and engagements:dashboard.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    skip_without_architecture("engagement_review.py")  # standalone: no architecture-docs checkout -> SKIP, not FAIL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="fail when task-link integrity errors are found")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable JSON report")
    parser.add_argument("--as-of", help="review date in YYYY-MM-DD format (default: today)")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DUE_SOON_DAYS,
        help=f"days considered due soon (default: {DEFAULT_DUE_SOON_DAYS})",
    )
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days must be zero or greater")

    try:
        as_of = parse_date(args.as_of, "--as-of") if args.as_of else dt.date.today()
        assert as_of is not None
        report = review(load_registry(), as_of, args.days)
    except (AssertionError, OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 1 if args.strict and report["error_count"] else 0


if __name__ == "__main__":
    sys.exit(main())

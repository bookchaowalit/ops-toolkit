#!/usr/bin/env python3
"""Queue deduplicated engagement review digests in the notifications cell.

The command is safe by default: it previews a digest and does not write or
send anything. Use ``--apply`` to append one deduplicated digest to the
notifications cell (solo-notifications). External delivery remains controlled by
the configured notifier channels.

Only these findings become notifications:
  - overdue review dates or tasks;
  - blocked tasks;
  - task-link integrity errors;
  - review dates/tasks due within the configured notification window.

Usage:
    python3 infra/scripts/utils/engagement_notifications.py --dry-run
    python3 infra/scripts/utils/engagement_notifications.py --apply
    task engagements:notify
    task engagements:notify -- --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

from engagement_review import load_registry, review
from notifications_store import NotificationsStoreError, dedupe_key, open_notifications_store
from task_store import ClientConfigError, TaskStore, open_task_store


NOTIFICATION_TYPE = "engagement_review"
NOTIFICATIONS_AUTOMATION = "engagement-sync"
NOTIFICATION_SOURCE = "solo_empire_engagements"
DEFAULT_DUE_SOON_DAYS = 3
NOTIFIABLE_CATEGORIES = frozenset(
    {
        "overdue_review",
        "overdue_task",
        "blocked",
        "missing_task_link",
        "missing_task",
        "task_source",
        "task_scope",
        "stale_task_link",
        "due_soon_review",
        "due_soon_task",
    }
)
CRITICAL_CATEGORIES = frozenset(
    {
        "overdue_review",
        "overdue_task",
        "blocked",
        "missing_task_link",
        "missing_task",
        "task_source",
        "task_scope",
        "stale_task_link",
    }
)


def fingerprint(item: dict) -> str:
    payload = {
        "category": item["category"],
        "slug": item["slug"],
        "task_id": item.get("task_id"),
        "message": item["message"],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def notifiable_findings(report: dict) -> list[dict]:
    return [
        item for item in report["findings"] if item["category"] in NOTIFIABLE_CATEGORIES
    ]


def load_seen_fingerprints(store) -> set[str]:
    seen: set[str] = set()
    for entry in store.notifications(notification_type=NOTIFICATION_TYPE):
        payload = entry.get("metadata")
        if not isinstance(payload, dict):
            continue
        for value in payload.get("fingerprints", []):
            if isinstance(value, str):
                seen.add(value)
    return seen


def digest_message(findings: list[dict]) -> str:
    lines = ["Engagement review findings:"]
    for item in findings:
        task = f" [#{item['task_id']}]" if "task_id" in item else ""
        lines.append(f"- {item['slug']}{task}: {item['message']}")
    lines.append("")
    lines.append("Run `task engagements:review` for the full review.")
    return "\n".join(lines)


def task_snapshot(store: TaskStore, task_ids: list[int]) -> dict[int, dict]:
    if not task_ids:
        return {}
    return {int(task["id"]): task for task in store.list(numbers=task_ids, include_archived=True)}


def queue_digest(findings: list[dict], *, apply: bool) -> dict:
    fingerprints = [fingerprint(item) for item in findings]
    store = open_notifications_store(NOTIFICATIONS_AUTOMATION)
    seen = load_seen_fingerprints(store)
    new_items = [
        item for item, item_fingerprint in zip(findings, fingerprints)
        if item_fingerprint not in seen
    ]
    result = {
        "new_count": len(new_items),
        "deduplicated_count": len(findings) - len(new_items),
        "queued": False,
        "fingerprints": [fingerprint(item) for item in new_items],
    }
    if not apply or not new_items:
        return result

    task_ids = sorted({int(item["task_id"]) for item in new_items if "task_id" in item})
    tasks: dict[int, dict] = {}
    if task_ids:
        tasks = task_snapshot(open_task_store(None, "engagement-reader"), task_ids)
    severity = "critical" if any(item["category"] in CRITICAL_CATEGORIES for item in new_items) else "warning"
    title = f"Engagement review: {len(new_items)} new finding(s)"
    message = digest_message(new_items)
    metadata = {
        "source": NOTIFICATION_SOURCE,
        "fingerprints": result["fingerprints"],
        "as_of": dt.date.today().isoformat(),
        "categories": sorted({item["category"] for item in new_items}),
    }
    first_task = tasks.get(task_ids[0]) if task_ids else None
    outcome, _entry = store.queue(
        {
            "task_id": task_ids[0] if task_ids else None,
            "title": title,
            "message": message,
            "notification_type": NOTIFICATION_TYPE,
            "severity": severity,
            "channel": "console",
            "task_title": "Engagement review digest",
            "task_status": "open",
            "task_priority": first_task["priority"] if first_task else None,
            "task_due_date": first_task["due_date"] if first_task else None,
            "task_project": "engagements",
            "metadata": metadata,
            # The same new fingerprints can only be queued once, even across concurrent runs.
            "dedupe_key": dedupe_key("engagement-review", sorted(result["fingerprints"])),
        }
    )
    result["queued"] = outcome == "created"
    if outcome == "existing":  # a concurrent run queued the same findings first
        result["deduplicated_count"] += result["new_count"]
        result["new_count"] = 0
    return result


def render(report: dict, result: dict, *, apply: bool) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [
        "Engagement Notification Bridge",
        "===============================",
        f"Mode: {mode}",
        f"Review date: {report['as_of']} | Due-soon window: {report['due_soon_days']} days",
        f"Notifiable findings: {len(notifiable_findings(report))}",
        f"New: {result['new_count']} | Deduplicated: {result['deduplicated_count']}",
    ]
    if result["queued"]:
        lines.append("✅ One digest queued in the notifications cell.")
    elif not apply:
        lines.append("ℹ️  No database write. Use --apply to queue new findings.")
    else:
        lines.append("✅ Nothing new to queue.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="queue one deduplicated digest in the notifications cell")
    mode.add_argument("--dry-run", action="store_true", help="preview without writing (default)")
    parser.add_argument("--as-of", help="review date in YYYY-MM-DD format (default: today)")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DUE_SOON_DAYS,
        help=f"notification due-soon window in days (default: {DEFAULT_DUE_SOON_DAYS})",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable JSON summary")
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days must be zero or greater")

    try:
        as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
        report = review(load_registry(), as_of, args.days)
        findings = notifiable_findings(report)
        result = queue_digest(findings, apply=args.apply)
    except (OSError, ValueError, RuntimeError, NotificationsStoreError, ClientConfigError) as exc:
        print(f"FAIL: {exc}")
        return 1

    if args.json:
        print(json.dumps({"report": report, "notification": result}, ensure_ascii=False, indent=2))
    else:
        print(render(report, result, apply=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())

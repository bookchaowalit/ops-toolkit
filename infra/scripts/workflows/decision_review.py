#!/usr/bin/env python3
"""Create idempotent review tasks for decisions that reached review date.

The workflow creates only a task and a review record in the decisions cell. It
does not mark a review complete and does not execute the decision action.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
UTILS_DIR = SCRIPT_DIR.parent / "utils"
sys.path.insert(0, str(UTILS_DIR))

from decision_store import open_decision_store  # noqa: E402
from task_store import open_task_store  # noqa: E402


REVIEW_TASK_SOURCE = "solo_empire_decision_review"


def review_view(review: dict, decision: dict) -> dict:
    """The joined shape this workflow reports: review fields plus the decision's privacy and title."""
    return {"review_id": review["id"], "decision_id": review["decision_id"], "due_date": str(review["due_date"]),
            "status": review["status"], "task_id": review["task_id"], "privacy_class": decision["privacy_class"],
            "title": decision["title"]}


def safe_review_payload(row: dict) -> dict:
    private = row["privacy_class"] != "internal"
    return {
        "review_id": row["review_id"],
        "decision_id": row["decision_id"],
        "due_date": row["due_date"],
        "status": row["status"],
        "task_id": row["task_id"],
        "privacy_class": row["privacy_class"],
        "title": row["title"] if not private else None,
        "redacted": private,
    }


def parse_as_of(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def eligible_decisions(decisions, as_of: date) -> list[dict]:
    rows = decisions.all(review_due_on_or_before=as_of.isoformat(),
                         status=["approved", "in_progress", "completed", "deferred"])
    return sorted((r for r in rows if r["review_date"]), key=lambda r: (str(r["review_date"]), r["id"]))


def find_task(store, external_id: str) -> dict | None:
    return store.find_external(REVIEW_TASK_SOURCE, external_id)


def task_title(decision: dict) -> str:
    if decision["privacy_class"] == "internal":
        return f"Review decision #{decision['id']}: {decision['title']}"
    return f"Review private decision #{decision['id']}"


def ensure_review_task(decision: dict, review: dict, now: str, store) -> tuple[int, bool]:
    """The review task for ``review`` (found or created); the caller records it on the review."""
    external_id = f"decision-review:{review['review_id']}"
    existing_task = find_task(store, external_id)
    if existing_task:
        return int(existing_task["id"]), False
    _outcome, task_id = store.upsert_external(
        REVIEW_TASK_SOURCE,
        external_id,
        {
            "title": task_title(decision),
            "description": f"Record the outcome for decision review #{review['review_id']} using decision-cli.py.",
            "status": "todo",
            "priority": 2 if decision["approval_required"] else 3,
            "project": "decisions",
            "category": "ops",
            "tags": json.dumps(["decision-review", f"decision:{decision['id']}"], separators=(",", ":")),
            "due_date": review["due_date"],
        },
        legacy={"sync_status": "local", "created_at": now, "updated_at": now},
    )
    return int(task_id), True


def sync_reviews(as_of: date, dry_run: bool = False) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    decisions = open_decision_store("decision-review")
    eligible = eligible_decisions(decisions, as_of)
    result = {
        "as_of": as_of.isoformat(),
        "eligible": len(eligible),
        "created_reviews": 0,
        "created_tasks": 0,
        "existing_reviews": 0,
        "dry_run": dry_run,
        "reviews": [],
    }

    tasks = None if dry_run else open_task_store(None, "decision-review")

    def existing_review(decision: dict) -> dict | None:
        due = str(decision["review_date"])
        found = [r for r in decisions.reviews(decision_id=decision["id"], limit=500) if str(r["due_date"]) == due]
        return found[0] if found else None

    for decision in eligible:
        review = existing_review(decision)
        if dry_run:
            result["reviews"].append(
                safe_review_payload(review_view(review, decision))
                if review
                else {
                    "review_id": None,
                    "decision_id": decision["id"],
                    "due_date": str(decision["review_date"]),
                    "status": "would_create",
                    "task_id": None,
                    "privacy_class": decision["privacy_class"],
                    "title": decision["title"] if decision["privacy_class"] == "internal" else None,
                    "redacted": decision["privacy_class"] != "internal",
                }
            )
            continue
        if review is None:
            _outcome, review = decisions.put_review(decision["id"], str(decision["review_date"]), {"status": "pending"})
            result["created_reviews"] += 1
        else:
            result["existing_reviews"] += 1
        view = review_view(review, decision)
        task_id, created = ensure_review_task(decision, view, now, tasks)
        if view["task_id"] != task_id:
            decisions.update_review(view["review_id"], {"task_id": task_id})
        # Keyed on the review, not on this run: a lost event from an earlier run is added now.
        recorded = any(e["event_type"] == "review_task_created"
                       and (e.get("metadata") or {}).get("review_id") == int(view["review_id"])
                       for e in decisions.history(int(decision["id"])))
        if not recorded:
            decisions.add_event(int(decision["id"]), "review_task_created", actor=REVIEW_TASK_SOURCE,
                                metadata={"review_id": int(view["review_id"]), "task_id": task_id,
                                          "due_date": view["due_date"]})
        result["created_tasks"] += int(created)
        result["reviews"].append(safe_review_payload({**view, "task_id": task_id}))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync", action="store_true", help="Create pending review records and local tasks")
    parser.add_argument("--dry-run", action="store_true", help="Preview review tasks without writing")
    parser.add_argument("--as-of", help="Review date in YYYY-MM-DD format; defaults to today")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args()

    result = sync_reviews(parse_as_of(args.as_of), dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("Decision Review Workflow")
        for key, value in result.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

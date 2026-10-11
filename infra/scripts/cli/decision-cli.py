#!/usr/bin/env python3
"""Inspect and approve Solo Empire decision records.

Usage:
    python3 decision-cli.py list [--status proposed] [--json]
    python3 decision-cli.py show <id> [--include-private]
    python3 decision-cli.py events <id> [--include-private]
    python3 decision-cli.py reviews [--status pending] [--json]
    python3 decision-cli.py approve <id> --confirm [--reason "..."]
    python3 decision-cli.py reject <id> --confirm [--reason "..."]
    python3 decision-cli.py defer <id> --confirm [--reason "..."]
    python3 decision-cli.py complete <id> --confirm --outcome "..."
    python3 decision-cli.py review <review_id> --confirm --outcome "..."

This CLI changes decision state and writes an audit event. It never executes
the action described by a decision or contacts an external provider.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "utils"))

from decision_records import clean_text  # noqa: E402
from decision_store import DecisionStoreError, open_decision_store  # noqa: E402
from task_store import open_task_store  # noqa: E402


SAFE_DECISION_FIELDS = (
    "id",
    "title",
    "domain",
    "decision",
    "status",
    "approval_required",
    "approval_status",
    "action_type",
    "action_status",
    "privacy_class",
    "confidence",
    "review_date",
    "next_action",
    "actual_outcome",
    "created_at",
    "updated_at",
)


def decisions():
    # The owner's own CLI, not an automation: its token is the delegated owner-cli token.
    return open_decision_store("owner-cli", channel="cli")


def decision_payload(row: dict, include_private: bool = False) -> dict:
    privacy = row["privacy_class"]
    if privacy != "internal" and not include_private:
        return {
            "id": row["id"],
            "domain": row["domain"],
            "status": row["status"],
            "approval_required": int(bool(row["approval_required"])),
            "approval_status": row["approval_status"],
            "action_type": row["action_type"],
            "action_status": row["action_status"],
            "privacy_class": privacy,
            "redacted": True,
        }
    payload = {field: row[field] for field in SAFE_DECISION_FIELDS}
    payload["approval_required"] = int(bool(row["approval_required"]))
    return payload


def event_payload(row: dict, include_private: bool = False) -> dict:
    payload = dict(row)
    payload["metadata"] = json.dumps(row.get("metadata") or {}, separators=(",", ":"))
    if row["privacy_class"] != "internal" and not include_private:
        payload["reason"] = None
        payload["redacted"] = True
    return payload


def fetch_decision(store, decision_id: int) -> dict:
    row = store.get(decision_id)
    if not row:
        raise ValueError(f"Decision #{decision_id} not found")
    return row


def list_decisions(args: argparse.Namespace) -> dict:
    filters = {"approval_status": args.approval_status}
    limit = min(max(args.limit, 1), 100)
    if args.privacy_class:  # filter over every match, then page (the API has no privacy filter)
        rows = [r for r in decisions().all(status=args.status, **filters) if r["privacy_class"] == args.privacy_class]
        rows = rows[:limit]
    else:
        rows = decisions().list(status=args.status, limit=limit, **filters)
    return {"decisions": [decision_payload(row, args.include_private) for row in rows], "count": len(rows)}


def show_decision(args: argparse.Namespace) -> dict:
    return {"decision": decision_payload(fetch_decision(decisions(), args.decision_id), args.include_private)}


def show_events(args: argparse.Namespace) -> dict:
    store = decisions()
    decision = fetch_decision(store, args.decision_id)
    rows = sorted(store.events(args.decision_id, limit=500), key=lambda e: e["id"])
    rows = [{**row, "privacy_class": decision["privacy_class"]} for row in rows]
    return {
        "decision_id": args.decision_id,
        "events": [event_payload(row, args.include_private) for row in rows],
        "count": len(rows),
    }


def review_view(review: dict, decision: dict) -> dict:
    return {**review, "review_id": review["id"], "due_date": str(review["due_date"]),
            "privacy_class": decision["privacy_class"], "title": decision["title"]}


def review_payload(row: dict, include_private: bool = False) -> dict:
    private = row["privacy_class"] != "internal"
    payload = {
        "review_id": row["review_id"],
        "decision_id": row["decision_id"],
        "due_date": row["due_date"],
        "status": row["status"],
        "task_id": row["task_id"],
        "privacy_class": row["privacy_class"],
        "reviewer": row["reviewer"],
        "reviewed_at": row["reviewed_at"],
        "title": row["title"],
    }
    if private and not include_private:
        payload.update({"title": None, "outcome": None, "notes": None, "redacted": True})
    else:
        payload.update({"outcome": row["outcome"], "notes": row["notes"], "redacted": False})
    return payload


def list_reviews(args: argparse.Namespace) -> dict:
    store = decisions()
    rows = store.reviews(status=args.status, limit=min(max(args.limit, 1), 100))
    cache: dict[int, dict] = {}
    views = []
    for review in rows:
        decision = cache.setdefault(review["decision_id"], fetch_decision(store, review["decision_id"]))
        views.append(review_view(review, decision))
    return {"reviews": [review_payload(row, args.include_private) for row in views], "count": len(views)}


def complete_review(args: argparse.Namespace) -> dict:
    store = decisions()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    actor = clean_text(args.actor or os.environ.get("SOLO_EMPIRE_ACTOR") or "human", 120)
    outcome = clean_text(args.outcome)
    notes = clean_text(args.notes)
    review = store.review(args.review_id)
    if not review:
        raise ValueError(f"Decision review #{args.review_id} not found")
    if review["status"] != "pending":
        raise ValueError("Only a pending review can be completed")
    if not outcome:
        raise ValueError("Review completion requires --outcome")
    # Close the task and record the event first, the review last: a failure leaves the review
    # pending, and a retry redoes only what is missing (both steps are idempotent).
    if review["task_id"]:
        tasks = open_task_store(None, "owner-cli", channel="cli")
        task = tasks.get(int(review["task_id"]))
        if task is not None and task["status"] not in ("done", "cancelled"):
            tasks.complete(int(review["task_id"]), legacy={"completed_at": now, "updated_at": now})
    already = any(e["event_type"] == "reviewed" and (e.get("metadata") or {}).get("review_id") == args.review_id
                  for e in store.events(review["decision_id"], limit=500))
    if not already:
        store.add_event(review["decision_id"], "reviewed", actor=actor or "human", reason=outcome,
                        metadata={"source": "decision-cli", "review_id": args.review_id})
    updated = store.update_review(args.review_id, {"status": "completed", "reviewer": actor or "human",
                                                   "outcome": outcome, "notes": notes, "reviewed_at": now},
                                  expected_version=review["version"])
    decision = fetch_decision(store, review["decision_id"])
    return {
        "success": True,
        "action": "reviewed",
        "actor": actor or "human",
        "review": review_payload(review_view(updated, decision), args.include_private),
    }


def mutate_decision(args: argparse.Namespace, event_type: str) -> dict:
    store = decisions()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    actor = clean_text(args.actor or os.environ.get("SOLO_EMPIRE_ACTOR") or "human", 120)
    reason = clean_text(getattr(args, "reason", None))
    outcome = clean_text(getattr(args, "outcome", None))
    before = fetch_decision(store, args.decision_id)
    updates: dict = {}
    if event_type == "approved":
        if not before["approval_required"]:
            raise ValueError("This decision does not require approval")
        if before["status"] not in {"draft", "proposed"} or before["approval_status"] != "pending":
            raise ValueError("Only a proposed decision with pending approval can be approved")
        updates.update({"status": "approved", "approval_status": "approved",
                        "action_status": "queued" if before["action_type"] != "none" else "none", "decided_at": now})
    elif event_type == "rejected":
        if before["status"] not in {"draft", "proposed"} or before["approval_status"] != "pending":
            raise ValueError("Only a proposed decision with pending approval can be rejected")
        updates.update({"status": "rejected", "approval_status": "rejected", "action_status": "blocked",
                        "decided_at": now})
    elif event_type == "deferred":
        if before["status"] in {"completed", "rejected", "superseded"}:
            raise ValueError("A closed decision cannot be deferred")
        updates.update({"status": "deferred", "action_status": "blocked"})
    elif event_type == "completed":
        if not outcome:
            raise ValueError("Completion requires --outcome")
        if before["status"] in {"completed", "rejected", "superseded"}:
            raise ValueError("This decision is already closed")
        if before["approval_required"] and before["approval_status"] != "approved":
            raise ValueError("Approval is required before completing this decision")
        updates.update({"status": "completed", "actual_outcome": outcome,
                        "action_status": "executed" if before["action_type"] != "none" else "none",
                        "decided_at": now})
    else:
        raise ValueError(f"Unsupported decision action: {event_type}")
    # expected_version: a concurrent change makes this refuse instead of overwriting it.
    after = store.update(args.decision_id, updates,
                         event={"event_type": event_type, "actor": actor or "human", "reason": reason or outcome,
                                "metadata": {"source": "decision-cli"}},
                         expected_version=before["version"])
    return {
        "success": True,
        "action": event_type,
        "actor": actor or "human",
        "decision": decision_payload(after, getattr(args, "include_private", False)),
    }


def add_common(parser: argparse.ArgumentParser, include_private: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    if include_private:
        parser.add_argument(
            "--include-private",
            action="store_true",
            help="Include private/sensitive decision text in local output",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="List decision records")
    list_parser.add_argument("--status")
    list_parser.add_argument("--approval-status")
    list_parser.add_argument("--privacy-class")
    list_parser.add_argument("--limit", type=int, default=50)
    add_common(list_parser, include_private=True)

    show_parser = commands.add_parser("show", help="Show one decision")
    show_parser.add_argument("decision_id", type=int)
    add_common(show_parser, include_private=True)

    events_parser = commands.add_parser("events", help="Show decision audit events")
    events_parser.add_argument("decision_id", type=int)
    add_common(events_parser, include_private=True)

    reviews_parser = commands.add_parser("reviews", help="List decision reviews")
    reviews_parser.add_argument("--status")
    reviews_parser.add_argument("--limit", type=int, default=50)
    add_common(reviews_parser, include_private=True)

    for action in ("approve", "reject", "defer"):
        action_parser = commands.add_parser(action, help=f"{action.title()} a decision")
        action_parser.add_argument("decision_id", type=int)
        action_parser.add_argument("--confirm", action="store_true", required=True)
        action_parser.add_argument("--actor")
        action_parser.add_argument("--reason")
        add_common(action_parser, include_private=True)

    complete_parser = commands.add_parser("complete", help="Record a decision outcome")
    complete_parser.add_argument("decision_id", type=int)
    complete_parser.add_argument("--confirm", action="store_true", required=True)
    complete_parser.add_argument("--outcome", required=True)
    complete_parser.add_argument("--actor")
    complete_parser.add_argument("--reason")
    add_common(complete_parser, include_private=True)

    review_parser = commands.add_parser("review", help="Record a decision review outcome")
    review_parser.add_argument("review_id", type=int)
    review_parser.add_argument("--confirm", action="store_true", required=True)
    review_parser.add_argument("--outcome", required=True)
    review_parser.add_argument("--notes")
    review_parser.add_argument("--actor")
    add_common(review_parser, include_private=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "list":
            result = list_decisions(args)
        elif args.command == "show":
            result = show_decision(args)
        elif args.command == "events":
            result = show_events(args)
        elif args.command == "reviews":
            result = list_reviews(args)
        elif args.command == "review":
            if not args.confirm:
                raise ValueError("Explicit --confirm is required to complete a review")
            result = complete_review(args)
        else:
            result = mutate_decision(args, {"approve": "approved", "reject": "rejected", "defer": "deferred", "complete": "completed"}[args.command])
        if args.json:
            print(json.dumps(result, ensure_ascii=False, default=str))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (FileNotFoundError, ValueError, DecisionStoreError) as error:
        print(f"decision-cli error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

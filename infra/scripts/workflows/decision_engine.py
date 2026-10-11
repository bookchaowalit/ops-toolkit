#!/usr/bin/env python3
"""Create idempotent decision proposals from safe operational signals.

This workflow recommends decisions; it never executes the resulting action.
Records live in the decisions cell (``open_decision_store``) with a stable
``dedupe_key`` so a dashboard refresh can run repeatedly without duplicating
proposals.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
UTILS_DIR = SCRIPT_DIR.parent / "utils"
AGENT_DIR = SCRIPT_DIR.parent / "agent"
sys.path.insert(0, str(UTILS_DIR))
sys.path.insert(0, str(AGENT_DIR))

from cell_store import open_cell_store  # noqa: E402
from decision_store import DecisionStoreError, open_decision_store  # noqa: E402
from decision_context import build_decision_context  # noqa: E402
from task_store import open_task_store  # noqa: E402


ENGINE_SOURCE = "decision_engine"


def collect_signals() -> list[dict]:
    """Collect deterministic signals without reading raw private payloads."""
    signals: list[dict] = []

    failed_jobs = open_cell_store("automation", "decision-engine").list(
        "cron-jobs", is_active=True, last_status="failed"
    )
    for row in sorted(failed_jobs, key=lambda job: job["job_type"]):
        job_label = row["name"] or row["job_type"]
        signals.append(
            {
                "dedupe_key": f"cron_failed:{row['job_type']}",
                "title": f"Review failed automation: {job_label}",
                "domain": "operations",
                "decision": f"Repair or explicitly pause the {row['job_type']} automation before relying on this pipeline.",
                "rationale": "The latest registered cron run failed.",
                "evidence_refs": [f"cron_jobs:{row['id']}"],
                "confidence": 0.99,
                "expected_outcome": "The automation is healthy, intentionally paused, or has a documented fallback.",
                "next_action": f"Inspect the {row['job_type']} job output and choose repair or pause.",
                "action_type": "external_change",
                "approval_required": 1,
                "privacy_class": "internal",
                "source_ref": f"cron_jobs:{row['id']}",
            }
        )

    utc_today = datetime.now(timezone.utc).date().isoformat()
    overdue_tasks = sum(
        1
        for task in open_task_store(None, "reporting-reader").list(due_before=utc_today, include_archived=True)
        if task["status"] not in ("done", "cancelled")
    )
    if overdue_tasks:
        signals.append(
            {
                "dedupe_key": "overdue_tasks",
                "title": "Reprioritize overdue tasks",
                "domain": "operations",
                "decision": "Review overdue tasks and choose which to reschedule, delegate, or complete.",
                "rationale": f"There are {overdue_tasks} open task(s) past their due date.",
                "evidence_refs": ["tasks:overdue"],
                "confidence": 0.97,
                "expected_outcome": "Every overdue task has a current owner, date, or explicit disposition.",
                "next_action": "Review the overdue task queue and update dates or status.",
                "action_type": "task",
                "approval_required": 0,
                "privacy_class": "internal",
                "source_ref": "tasks:overdue",
            }
        )

    today_utc = datetime.now(timezone.utc).date().isoformat()
    overdue_goals = sum(
        1
        for goal in open_cell_store("goals", "decision-engine").list("goals", status="active")
        if goal.get("due_date") and str(goal["due_date"])[:10] < today_utc
    )
    if overdue_goals:
        signals.append(
            {
                "dedupe_key": "overdue_goals",
                "title": "Review overdue goals",
                "domain": "life",
                "decision": "Choose whether to recommit, reduce scope, defer, or close overdue goals.",
                "rationale": f"There are {overdue_goals} active goal(s) past their due date.",
                "evidence_refs": ["goals:overdue"],
                "confidence": 0.96,
                "expected_outcome": "Overdue goals have an explicit current status and next review date.",
                "next_action": "Review overdue goals during the next planning session.",
                "action_type": "task",
                "approval_required": 0,
                "privacy_class": "private",
                "source_ref": "goals:overdue",
            }
        )

    return signals


def upsert_signal(store, signal: dict, now: str) -> str:
    """Create or refresh the proposal for one signal through the decisions cell."""
    existing = store.by_dedupe(signal["dedupe_key"])
    # Do not overwrite a human-approved, completed, rejected, or superseded decision.
    if existing is not None and existing["status"] not in {"draft", "proposed"}:
        return "preserved"
    approval_required = bool(signal["approval_required"])
    fields = {
        "title": signal["title"],
        "domain": signal["domain"],
        "decision": signal["decision"],
        "rationale": signal["rationale"],
        "evidence_refs": list(signal["evidence_refs"]),
        "confidence": signal["confidence"],
        "expected_outcome": signal["expected_outcome"],
        "next_action": signal["next_action"],
        "status": "proposed",
        "actual_outcome": None,
        "approval_required": approval_required,
        "approval_status": "pending" if approval_required else "not_required",
        "action_type": signal["action_type"],
        "action_status": "pending_approval" if approval_required else "none",
        "privacy_class": signal["privacy_class"],
        "source_type": ENGINE_SOURCE,
        "source_ref": signal["source_ref"],
    }
    event = {"event_type": "refreshed", "actor": ENGINE_SOURCE, "metadata": {"dedupe_key": signal["dedupe_key"]}}
    try:
        if existing is None:
            outcome, _record = store.upsert(signal["dedupe_key"], fields, event=event)
            return {"created": "created", "refreshed": "updated", "unchanged": "updated"}[outcome]
        def same(current, wanted) -> bool:
            if isinstance(current, float) or isinstance(wanted, float):  # confidence is float4 in the cell
                return current is not None and wanted is not None and abs(float(current) - float(wanted)) < 1e-6
            return current == wanted

        if all(same(existing.get(name), value) for name, value in fields.items()):
            return "updated"  # nothing new in the signal: no write, no history entry
        # Compare-and-set: a human decision made after the read is never overwritten.
        store.update(existing["id"], fields, event=event, expected_version=existing["version"])
        return "updated"
    except DecisionStoreError as exc:
        if exc.status == 409:  # changed meanwhile (or created concurrently): leave it for the next run
            return "preserved"
        raise


def close_cleared_signals(store, active_keys: set[str], now: str) -> int:
    cleared = 0
    for row in store.all(source_type=ENGINE_SOURCE, status=["draft", "proposed"]):
        if row["dedupe_key"] and row["dedupe_key"] not in active_keys:
            try:
                store.update(
                    row["id"],
                    {"status": "completed", "actual_outcome": "Signal cleared by the latest decision-engine run.",
                     "action_status": "none"},
                    event={"event_type": "signal_cleared", "actor": ENGINE_SOURCE,
                           "metadata": {"dedupe_key": row["dedupe_key"]}},
                    expected_version=row["version"],
                )
            except DecisionStoreError as exc:
                if exc.status == 409:  # approved or edited after the read: not ours to close
                    continue
                raise
            cleared += 1
    return cleared


def sync_decisions(
    dry_run: bool = False,
    *,
    include_context: bool = False,
    as_of: str | None = None,
    window_days: int = 7,
    diary_root: Path | None = None,
) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    signals = collect_signals()
    result = {"signals": len(signals), "created": 0, "updated": 0, "preserved": 0, "cleared": 0}
    if include_context:
        result["context"] = build_decision_context(
            as_of=as_of,
            window_days=window_days,
            diary_root=diary_root,
        )

    if dry_run:
        result["dedupe_keys"] = [signal["dedupe_key"] for signal in signals]
        return result

    store = open_decision_store("decision-engine")
    for signal in signals:
        result[upsert_signal(store, signal, now)] += 1
    result["cleared"] = close_cleared_signals(store, {signal["dedupe_key"] for signal in signals}, now)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate idempotent decision proposals from operational signals")
    parser.add_argument("--sync", action="store_true", help="Persist current proposals (default)")
    parser.add_argument("--dry-run", action="store_true", help="Inspect signals without writing")
    parser.add_argument("--with-context", action="store_true", help="Include the privacy-safe Finance/Diary/Goals context pack")
    parser.add_argument("--as-of", help="Context date in YYYY-MM-DD")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--diary-root", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args()

    result = sync_decisions(
        dry_run=args.dry_run,
        include_context=args.with_context,
        as_of=args.as_of,
        window_days=args.window_days,
        diary_root=args.diary_root,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("Decision Engine")
        for key, value in result.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

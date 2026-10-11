#!/usr/bin/env python3
"""Manage the evidence-backed weekly planning cycle.

The script is intentionally deterministic. It never stores prompts, model
responses, credentials, or private payloads. Goals are the objective layer;
``goal_metrics`` are relational key results; ``operating`` cell cycles are short
portfolio commitments; AI spend/usage signals live in solo-ai-gateway, so the
AI KPIs report ``no_data`` here.

Examples:
    python3 operating_cycle.py report --write
    python3 operating_cycle.py check
    python3 operating_cycle.py snapshot --slug adaptive-foundation-2026-w34
    python3 operating_cycle.py start --slug adaptive-foundation-2026-w34 --confirm
    python3 operating_cycle.py review --slug adaptive-foundation-2026-w34 \
        --decision close --summary "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

UTILS_DIR = Path(__file__).resolve().parent
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))

from _env import PROJECT_ROOT, REPORTS_DIR  # noqa: E402
from cell_store import CellStoreError, open_cell_store  # noqa: E402
from task_store import ClientConfigError, open_task_store  # noqa: E402

AUTOMATION_ID = "portfolio-reader"
CELL_FIELDS = ("legacy_id", "version")  # cell bookkeeping, not part of the cycle contract


REPORT_PATH = REPORTS_DIR / "WEEKLY-PLANNING-CYCLE.md"
LEGACY_REPORT_PATHS = (
    REPORTS_DIR / "ADAPTIVE-OPERATING-CYCLE.md",
    REPORTS_DIR / "AI-OPERATING-CYCLE.md",
)
CHECK_STATUSES = {"no_data", "on_track", "at_risk", "off_track", "achieved"}
CONTRACT = "solo-empire.weekly-planning-cycle.v1"


class CycleError(ValueError):
    """Raised when a cycle command violates the operating contract."""


def operating() -> Any:
    """The operating cell (cycles, cycle tasks/goal links/reviews, KPIs and their measurements)."""
    return open_cell_store("operating", AUTOMATION_ID)


def row_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """A cell record without the cell's bookkeeping fields."""
    return {k: v for k, v in row.items() if k not in CELL_FIELDS} if row is not None else None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def select_cycle(slug: str | None = None) -> dict[str, Any]:
    store = operating()
    if slug:
        rows = store.list("cycles", slug=slug)
        row = rows[0] if rows else None
    else:
        rows = store.list("cycles", status=["active", "planned", "in_review"])
        rank = {"active": 0, "in_review": 1}
        rows.sort(key=lambda r: (str(r["start_date"]), r["id"]), reverse=True)
        rows.sort(key=lambda r: rank.get(r["status"], 2))
        row = rows[0] if rows else None
    if row is None:
        raise CycleError("no planned, active, or reviewable operating cycle found")
    return row_dict(row)


def iso_end_exclusive(end_date: str) -> str:
    return (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()


def cycle_tasks(cycle_id: int) -> list[dict[str, Any]]:
    links = operating().list("cycle-tasks", cycle_id=cycle_id)
    tasks = {
        task["id"]: task
        for task in open_task_store(None, AUTOMATION_ID).list(
            numbers=sorted({link["task_id"] for link in links}), include_archived=True
        )
    }
    rows = []
    for link in links:
        task = tasks.get(link["task_id"])
        if task is None:
            continue
        rows.append({
            "commitment_type": link["commitment_type"],
            "estimate_minutes": link["estimate_minutes"],
            "evidence_required": int(bool(link["evidence_required"])),
            "id": task["id"], "title": task["title"], "status": task["status"],
            "priority": task["priority"], "project": task["project"],
            "progress_percent": task["progress_percent"],
            "task_estimated_minutes": task["estimated_minutes"],
            "actual_minutes": task["actual_minutes"], "due_date": task["due_date"],
        })
    rows.sort(key=lambda row: (
        0 if row["commitment_type"] == "committed" else 1,
        row["priority"] is not None, row["priority"] if row["priority"] is not None else 0,
        row["id"],
    ))
    result = []
    for row in rows:
        item = dict(row)
        item["estimate_minutes"] = (
            item["estimate_minutes"]
            if item["estimate_minutes"] is not None
            else item["task_estimated_minutes"]
        )
        result.append(item)
    return result


def linked_objectives(cycle_id: int) -> list[dict[str, Any]]:
    links = [(row["goal_id"], row["link_type"]) for row in operating().list("cycle-goal-links", cycle_id=cycle_id)]
    if not links:
        return []
    goals = {
        goal["id"]: goal
        for goal in open_cell_store("goals", AUTOMATION_ID).list("goals", id=sorted({row[0] for row in links}))
    }
    items = [
        {
            "id": goals[goal_id]["id"], "title": goals[goal_id]["title"], "status": goals[goal_id]["status"],
            "progress_percent": goals[goal_id]["progress_percent"], "due_date": goals[goal_id]["due_date"],
            "link_type": link_type, "_priority": goals[goal_id]["priority"],
        }
        for goal_id, link_type in links
        if goal_id in goals
    ]
    items.sort(key=lambda g: (g["link_type"] or "", g["_priority"] is not None, g["_priority"] or 0, g["id"]))
    for item in items:
        del item["_priority"]
    return items


def linked_key_results(goal_ids: list[int]) -> list[dict[str, Any]]:
    if not goal_ids:
        return []
    columns = ("goal_id", "metric_key", "name", "metric_type", "direction", "unit", "baseline_value",
               "target_value", "current_value", "status", "source_type", "evidence_ref")
    metrics = open_cell_store("goals", AUTOMATION_ID).list("goal-metrics", goal_id=list(goal_ids))
    metrics.sort(key=lambda m: (m["goal_id"], m["metric_type"] or "", m["metric_key"] or ""))
    return [{column: metric.get(column) for column in columns} for metric in metrics]


def task_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    committed = [item for item in tasks if item["commitment_type"] == "committed"]
    completed = [item for item in committed if item["status"] == "done"]
    blocked = [item for item in committed if item["status"] == "blocked"]
    estimated = sum(int(item["estimate_minutes"] or 0) for item in committed)
    completed_minutes = sum(
        int(item["actual_minutes"] or item["estimate_minutes"] or 0)
        for item in completed
    )
    return {
        "total": len(tasks),
        "committed": len(committed),
        "completed": len(completed),
        "blocked": len(blocked),
        "todo": sum(1 for item in committed if item["status"] == "todo"),
        "in_progress": sum(1 for item in committed if item["status"] == "in_progress"),
        "estimated_minutes": estimated,
        "completed_minutes": completed_minutes,
        "delivery_rate": (len(completed) / len(committed) * 100) if committed else None,
        "commitment_accuracy": (completed_minutes / estimated * 100) if estimated else None,
    }


def metric_status(value: float | None, target: float, direction: str) -> str:
    if value is None:
        return "no_data"
    if direction == "decrease":
        if value <= target:
            return "achieved"
        if target > 0 and value <= target * 1.25:
            return "on_track"
        if target > 0 and value <= target * 2:
            return "at_risk"
        return "off_track"
    if direction == "maintain":
        return "achieved" if value == target else "at_risk"
    if value >= target:
        return "achieved"
    if target > 0 and value >= target * 0.8:
        return "on_track"
    if target > 0 and value >= target * 0.5:
        return "at_risk"
    return "off_track"


def derive_kpis(cycle: dict[str, Any],
    tasks: list[dict[str, Any]],
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    store = operating()
    definitions = sorted(store.list("kpis", status="active"), key=lambda row: row["id"])
    committed_tasks = [item for item in tasks if item["commitment_type"] == "committed"]
    planning_only = cycle["status"] == "planned" and not any(
        item["status"] in {"in_progress", "done", "blocked"} for item in committed_tasks
    )
    values: dict[str, float | None] = {
        "cycle-delivery-rate": None if planning_only else summary["delivery_rate"],
        "cycle-commitment-accuracy": None if planning_only else summary["commitment_accuracy"],
        "cycle-carryover-rate": (
            None
            if planning_only
            else (
                sum(1 for item in tasks if item["commitment_type"] == "carried_over")
                / len(committed_tasks) * 100
                if committed_tasks else None
            )
        ),
        # AI usage KPIs are owned by solo-ai-gateway; no local source.
        "ai-telemetry-success-rate": None,
        "ai-budget-compliance": None,
        "ai-cost-usd": None,
        "cycle-review-complete": (
            100.0
            if store.list("cycle-reviews", cycle_id=cycle["id"])
            else None
        ),
        "evidence-gate-rate": None,
    }
    result = []
    for definition in definitions:
        value = values.get(definition["slug"])
        result.append({
            "slug": definition["slug"],
            "name": definition["name"],
            "unit": definition["unit"],
            "direction": definition["direction"],
            "target_value": definition["target_value"],
            "source_kind": definition["source_kind"],
            "metric_group": definition.get("metric_group", "operating"),
            "measurement_cadence": definition.get("measurement_cadence", "weekly"),
            "value": value,
            "status": metric_status(value, definition["target_value"], definition["direction"]),
        })
    return result


def snapshot_cycle(cycle: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = cycle_tasks(cycle["id"])
    summary = task_summary(tasks)
    metrics = derive_kpis(cycle, tasks, summary)
    now = now_iso()
    store = operating()
    for metric in metrics:
        definitions = store.list("kpis", slug=metric["slug"])
        if not definitions:
            continue
        available = metric["value"] is not None
        fields = {
            "value": metric["value"] if available else 0,
            "target_value": metric["target_value"],
            "status": metric["status"],
            "measured_at": now,
            "source_ref": f"operating_cycles:{cycle['slug']}",
            "metadata_json": json.dumps({"data_available": available}, sort_keys=True),
        }
        existing = store.list("kpi-measurements", kpi_id=definitions[0]["id"], cycle_id=cycle["id"])
        if existing:
            store.update("kpi-measurements", existing[0]["id"], fields)
        else:
            store.create("kpi-measurements", {"kpi_id": definitions[0]["id"], "cycle_id": cycle["id"], **fields})
    goal_ids = [item["id"] for item in linked_objectives(cycle["id"])]
    if goal_ids:
        goal_store = open_cell_store("goals", AUTOMATION_ID)
        explicit_status = "achieved" if cycle["status"] in {"planned", "active", "in_review", "closed"} else "no_data"
        review_exists = bool(store.list("cycle-reviews", cycle_id=cycle["id"]))
        for metric_key, current, status in (
            ("explicit-cycle", 1, explicit_status),
            ("review-completion", 100 if review_exists else 0, "achieved" if review_exists else "no_data"),
        ):
            for metric in goal_store.list("goal-metrics", goal_id=goal_ids, metric_key=metric_key):
                goal_store.update("goal-metrics", metric["id"], {"current_value": current, "status": status})
    return metrics


def report_payload(cycle: dict[str, Any]) -> dict[str, Any]:
    tasks = cycle_tasks(cycle["id"])
    summary = task_summary(tasks)
    objectives = linked_objectives(cycle["id"])
    key_results = linked_key_results([item["id"] for item in objectives])
    metrics = derive_kpis(cycle, tasks, summary)
    reviews = operating().list("cycle-reviews", cycle_id=cycle["id"])
    review = row_dict(reviews[0]) if reviews else None
    warnings = []
    if cycle["status"] == "planned":
        warnings.append("cycle is planned; no execution should start until Owner explicitly starts it")
    if summary["blocked"]:
        warnings.append(f"{summary['blocked']} committed task(s) are blocked")
    warnings.append("AI usage lives in solo-ai-gateway; AI-specific KPIs are no_data")
    if cycle["status"] == "closed" and review is None:
        warnings.append("closed cycle has no review record")
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cycle": cycle,
        "objectives": objectives,
        "key_results": key_results,
        "tasks": tasks,
        "task_summary": summary,
        "kpis": metrics,
        "review": review,
        "warnings": warnings,
    }


def markdown_report(payload: dict[str, Any]) -> str:
    cycle = payload["cycle"]
    summary = payload["task_summary"]
    lines = [
        "---",
        "title: Weekly Planning Cycle Dashboard",
        "aliases: [Weekly Cycle, Portfolio Cycle, Weekly Review, OKR KPI Control]",
        "tags: [dashboard, portfolio, planning, okr, kpi]",
        "status: generated",
        "parent: dashboard",
        "related: [CURRENT-STATE, ANALYTICS, ../operations/WEEKLY-PLANNING-CYCLE.md, ../operations/TECHNOLOGY-WATCH.md]",
        "---",
        "",
        "# Weekly Planning Cycle Dashboard",
        "",
        f"> Generated: {payload['generated_at']}",
        f"> Cycle: `{cycle['slug']}` · **{cycle['status']}** · {cycle['start_date']} → {cycle['end_date']}",
        "",
        "## Control Gate",
        "",
        f"- Focus: {cycle['focus']}",
        f"- Owner: {cycle['owner']}",
        f"- Capacity: {cycle['capacity_minutes'] or 'not set'} minutes",
        "- Lifecycle: `planned → active → in_review → closed`",
        "- Rule: task status is not evidence; completion requires an artifact or accepted exception.",
        "",
        "## Cadence Policy",
        "",
        "| Layer | Default horizon | Decision |",
        "|---|---:|---|",
        "| Strategy / OKR | 6–8 weeks | Select outcome objectives and measurable KRs |",
        "| Portfolio cycle | 1 week | Start, stop, continue, or reorder work |",
        "| Execution loop | 1–3 days | Produce one reviewable result or experiment |",
        "| Cycle review | Weekly | Inspect outcome, flow, quality, economics, risk, and carryover |",
        "| Objective reset | 6–8 weeks | Rebaseline assumptions and revise the next OKR window |",
        "",
        "## Objective and Key Results",
        "",
        "| Objective | Status | Progress | Link |",
        "|---|---|---:|---|",
    ]
    for objective in payload["objectives"]:
        lines.append(
            f"| {objective['title']} | {objective['status']} | {objective['progress_percent'] or 0}% | {objective['link_type']} |"
        )
    if not payload["objectives"]:
        lines.append("| No linked objective | — | — | Add a goal link before starting |")
    lines.extend([
        "",
        "| Key Result | Current | Target | Status | Source |",
        "|---|---:|---:|---|---|",
    ])
    for result in payload["key_results"]:
        current = result["current_value"] if result["current_value"] is not None else "—"
        lines.append(
            f"| {result['name']} | {current} {result['unit']} | {result['target_value']} {result['unit']} | {result['status']} | {result['source_type']} |"
        )
    if not payload["key_results"]:
        lines.append("| No key results registered | — | — | no_data | — |")
    lines.extend([
        "",
        "## Committed Work",
        "",
        f"{summary['completed']}/{summary['committed']} committed tasks done · {summary['blocked']} blocked · {summary['estimated_minutes']} estimated minutes",
        "",
        "| Task | Status | Commitment | Estimate | Evidence gate |",
        "|---|---|---|---:|---|",
    ])
    for task in payload["tasks"]:
        lines.append(
            f"| #{task['id']} {task['title']} | {task['status']} | {task['commitment_type']} | {task['estimate_minutes'] or 0}m | {'required' if task['evidence_required'] else 'not required'} |"
        )
    if not payload["tasks"]:
        lines.append("| No committed tasks | — | — | — | — |")
    lines.extend([
        "",
        "## Operating KPI",
        "",
        "| KPI | Group | Value | Target | Status |",
        "|---|---|---:|---:|---|",
    ])
    for metric in payload["kpis"]:
        value = "no_data" if metric["value"] is None else f"{metric['value']:.2f} {metric['unit']}"
        target = f"{metric['target_value']} {metric['unit']}"
        lines.append(
            f"| {metric['name']} | {metric['metric_group']} | {value} | {target} | {metric['status']} |"
        )
    lines.extend([
        "",
        "AI usage and spend are reported by solo-ai-gateway, not by this repository.",
        "",
        "## Technology Watch",
        "",
        "New data, techniques, tools, and models are reviewed through the [Technology Watch](../operations/TECHNOLOGY-WATCH.md) policy.",
        "A new item starts as `Assess`; it needs a bounded trial and evidence before it can become an adopted default.",
        "",
        "## Warnings and Next Actions",
        "",
    ])
    if payload["warnings"]:
        lines.extend(f"- ⚠️ {warning}" for warning in payload["warnings"])
    else:
        lines.append("- ✅ No control warnings")
    if cycle["status"] == "planned":
        lines.extend([
            "- Review the objective, capacity, KPI targets, and committed tasks.",
            f"- Start explicitly: `task operating:cycle -- start --slug {cycle['slug']} --confirm`",
        ])
    elif cycle["status"] in {"active", "in_review"}:
        lines.append(f"- Refresh KPI snapshot: `task operating:cycle -- snapshot --slug {cycle['slug']}`")
        lines.append(f"- Review at the end: `task operating:cycle -- review --slug {cycle['slug']} --decision close --summary \"...\"`")
    lines.extend([
        "",
        "## Operating Contract",
        "",
        "This is a generated view. The operating, goals and tasks services are canonical. AI may recommend priorities and summarize evidence, but it may not mark a task done, start a cycle, spend money, publish, or change production without the relevant owner approval.",
        "",
        "**Source:** the operating cell via `infra/scripts/utils/ai_cycle.py`.",
        "",
    ])
    return "\n".join(lines)


def command_check() -> list[str]:
    errors: list[str] = []
    store = operating()
    cycles = sorted(store.list("cycles"), key=lambda row: row["id"])
    task_links: dict[int, list[int]] = {}
    for link in store.list("cycle-tasks"):
        task_links.setdefault(link["cycle_id"], []).append(link["task_id"])
    reviewed = {review["cycle_id"] for review in store.list("cycle-reviews")}
    active_count = sum(1 for cycle in cycles if cycle["status"] == "active")
    if active_count > 1:
        errors.append(f"more than one active operating cycle: {active_count}")
    for cycle in cycles:
        if cycle["status"] == "closed" and cycle["id"] not in reviewed:
            errors.append(f"closed cycle lacks review: {cycle['slug']}")
        if cycle["status"] == "planned":
            linked = task_links.get(cycle["id"], [])
            statuses = {
                task["id"]: task["status"]
                for task in open_task_store(None, AUTOMATION_ID).list(
                    numbers=sorted(set(linked)), include_archived=True
                )
            }
            started = sum(1 for task_id in linked if statuses.get(task_id) in ("in_progress", "done"))
            if started:
                errors.append(f"planned cycle has started work ({started} task(s)): {cycle['slug']}")
    for cycle in cycles:
        if cycle["status"] in ("planned", "active", "in_review") and not task_links.get(cycle["id"]):
            errors.append(f"execution cycle has no committed task links: {cycle['slug']}")
    return errors


def write_report(payload: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(markdown_report(payload), encoding="utf-8")
    for legacy_path in LEGACY_REPORT_PATHS:
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(
            "---\n"
            "title: Legacy Weekly Planning Cycle Dashboard Redirect\n"
            "status: compatibility\n"
            "---\n\n"
            "# Dashboard moved\n\n"
            "The portfolio-wide dashboard is now generated at "
            "[`WEEKLY-PLANNING-CYCLE.md`](WEEKLY-PLANNING-CYCLE.md).\n",
            encoding="utf-8",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the Solo Empire weekly planning cycle")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="Show the selected cycle and KPI report")
    report.add_argument("--slug")
    report.add_argument("--write", action="store_true", help="Write WEEKLY-PLANNING-CYCLE.md under the reports directory (SOLO_REPORTS_DIR)")
    report.add_argument("--json", action="store_true")

    check = sub.add_parser("check", help="Validate cycle lifecycle and execution gates")
    check.add_argument("--json", action="store_true")

    snapshot = sub.add_parser("snapshot", help="Persist a deterministic KPI snapshot")
    snapshot.add_argument("--slug")
    snapshot.add_argument("--json", action="store_true")

    start = sub.add_parser("start", help="Start a planned cycle after explicit confirmation")
    start.add_argument("--slug", required=True)
    start.add_argument("--confirm", action="store_true")

    review = sub.add_parser("review", help="Record a cycle review and close the cycle")
    review.add_argument("--slug", required=True)
    review.add_argument("--decision", required=True, choices=("continue", "adjust", "pause", "close"))
    review.add_argument("--summary", required=True)
    review.add_argument("--delivered", default="")
    review.add_argument("--carryover", default="")
    review.add_argument("--blockers", default="")
    review.add_argument("--learnings", default="")
    review.add_argument("--evidence-ref")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "check":
            errors = command_check()
            payload = {"contract": CONTRACT, "status": "error" if errors else "ok", "errors": errors}
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json else None))
            return 1 if errors else 0

        if args.command == "start":
            if not args.confirm:
                raise CycleError("starting a cycle requires --confirm")
            cycle = select_cycle(args.slug)
            if cycle["status"] != "planned":
                raise CycleError(f"cycle {args.slug} is {cycle['status']}, not planned")
            store = operating()
            active = [row for row in store.list("cycles", status="active") if row["slug"] != args.slug]
            if active:
                raise CycleError(f"another cycle is already active: {active[0]['slug']}")
            store.update("cycles", cycle["id"], {"status": "active"})
            print(json.dumps({"status": "active", "slug": args.slug}, ensure_ascii=False))
            return 0

        if args.command == "review":
            cycle = select_cycle(args.slug)
            if cycle["status"] not in {"active", "in_review", "planned"}:
                raise CycleError(f"cycle {args.slug} is {cycle['status']} and cannot be reviewed")
            store = operating()
            fields = {
                "summary": args.summary, "delivered_summary": args.delivered,
                "carryover_summary": args.carryover, "blocker_summary": args.blockers,
                "learning_summary": args.learnings, "decision": args.decision,
                "evidence_ref": args.evidence_ref, "reviewed_at": now_iso(),
            }
            existing = store.list("cycle-reviews", cycle_id=cycle["id"])
            if existing:
                store.update("cycle-reviews", existing[0]["id"], fields)
            else:
                store.create("cycle-reviews", {"cycle_id": cycle["id"], "reviewer": "Owner", **fields})
            store.update("cycles", cycle["id"], {"status": "closed", "decision": args.decision})
            print(json.dumps({"status": "closed", "slug": args.slug, "decision": args.decision}, ensure_ascii=False))
            return 0

        cycle = select_cycle(getattr(args, "slug", None))
        if args.command == "snapshot":
            metrics = snapshot_cycle(cycle)
            result = {"cycle": cycle["slug"], "metrics": metrics, "status": "snapshot_saved"}
            print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
            return 0
        payload = report_payload(cycle)
        if args.write:
            write_report(payload)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(markdown_report(payload))
        return 0
    except (CycleError, CellStoreError, ClientConfigError, OSError) as exc:
        payload = {"contract": CONTRACT, "status": "error", "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a privacy-safe Daily or Weekly Solo Empire decision brief.

The brief is a read-only view over the aggregate decision context pack. It
does not create decisions, mutate finance records, create tasks, send
messages, or execute any queued action. File output requires ``--write``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

UTILS_DIR = Path(__file__).resolve().parents[1] / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))
from _env import PROJECT_ROOT  # noqa: E402

AGENT_DIR = PROJECT_ROOT / "infra" / "scripts" / "agent"
for path in (AGENT_DIR, UTILS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from decision_context import CONTEXT_CONTRACT, build_decision_context  # noqa: E402


BRIEF_CONTRACT = "solo-empire.decision-brief.v1"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "generated" / "briefings"


def _default_window_days(cadence: str) -> int:
    if cadence == "daily":
        return 1
    if cadence == "weekly":
        return 7
    raise ValueError("cadence must be daily or weekly")


def _decision_rollup() -> dict[str, dict[str, int]]:
    """Aggregate counts only; the records come from the decisions cell."""
    from decision_store import open_decision_store

    by_domain: Counter[str] = Counter()
    pending_by_domain: Counter[str] = Counter()
    for row in open_decision_store("governance-reader").all(status=["draft", "proposed", "approved", "in_progress", "deferred"]):
        domain = str(row["domain"] or "general")
        by_domain[domain] += 1
        if row["approval_status"] == "pending":
            pending_by_domain[domain] += 1
    return {
        "open_by_domain": dict(sorted(by_domain.items())),
        "pending_approval_by_domain": dict(sorted(pending_by_domain.items())),
    }


def _priority_queue(context: dict[str, Any]) -> list[dict[str, Any]]:
    goals = context["goals"]
    decisions = context["decisions"]
    candidates = [
        (
            "P1",
            "decisions:pending_approval",
            "Review decisions waiting for approval",
            int(decisions.get("pending_approval", 0)),
        ),
        (
            "P2",
            "goals:overdue",
            "Review overdue goals and choose the next commitment",
            int(goals.get("overdue_goals", 0)),
        ),
        (
            "P2",
            "goals:due_in_window",
            "Review goals due in the brief window",
            int(goals.get("due_in_window", 0)),
        ),
    ]
    items = [
        {
            "priority": priority,
            "ref": ref,
            "summary": summary,
            "count": count,
            "requires_human_review": True,
        }
        for priority, ref, summary, count in candidates
        if count > 0
    ]
    if not items:
        items.append(
            {
                "priority": "INFO",
                "ref": "system:aggregate-scan:clear",
                "summary": "No configured aggregate exception was detected",
                "count": 0,
                "requires_human_review": False,
            }
        )
    return items


def _action_queue(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Return review prompts only; this list is never executed by the script."""
    goals = context["goals"]
    decisions = context["decisions"]
    candidates = [
        (
            "goals:overdue",
            "Choose whether to continue, defer, or re-plan overdue goals",
            int(goals.get("overdue_goals", 0)),
        ),
        (
            "goals:due_in_window",
            "Check due-soon goals against available capacity",
            int(goals.get("due_in_window", 0)),
        ),
        (
            "decisions:pending_approval",
            "Review and explicitly approve, reject, or defer pending decisions",
            int(decisions.get("pending_approval", 0)),
        ),
    ]
    return [
        {
            "ref": ref,
            "count": count,
            "next_step": next_step,
            "automatic_execution": False,
        }
        for ref, next_step, count in candidates
        if count > 0
    ]


def build_decision_brief(
    *,
    cadence: str = "daily",
    as_of: str | date | None = None,
    window_days: int | None = None,
    diary_root: str | Path | None = None,
    include_goal_titles: bool = False,
) -> dict[str, Any]:
    """Build the bounded brief payload without writing or mutating state."""
    if cadence not in {"daily", "weekly"}:
        raise ValueError("cadence must be daily or weekly")
    effective_window = window_days if window_days is not None else _default_window_days(cadence)
    context = build_decision_context(
        as_of=as_of,
        window_days=effective_window,
        diary_root=diary_root,
        include_goal_titles=include_goal_titles,
    )
    return {
        "contract": BRIEF_CONTRACT,
        "privacy_class": "private",
        "cadence": cadence,
        "as_of": context["as_of"],
        "window": context["window"],
        "context_contract": CONTEXT_CONTRACT,
        "priorities": _priority_queue(context),
        "action_queue": _action_queue(context),
        "decision_rollup": _decision_rollup(),
        "context": context,
        "policy": {
            "read_only": True,
            "raw_diary_text_included": False,
            "goal_titles_default": False,
            "automatic_execution": False,
            "outbound_messages": False,
        },
    }


def _metric(value: Any) -> str:
    return "n/a" if value is None else str(value)


def render_markdown(brief: dict[str, Any]) -> str:
    context = brief["context"]
    diary = context["diary"]
    goals = context["goals"]
    decisions = context["decisions"]
    lines = [
        f"# {brief['cadence'].title()} Decision Brief — {brief['as_of']}",
        "",
        f"- Contract: `{brief['contract']}`",
        f"- Window: `{brief['window']['start']}` → `{brief['window']['end']}` ({brief['window']['days']} day(s))",
        "- Mode: aggregate-only, read-only",
        "",
        "## Priority queue",
    ]
    for item in brief["priorities"]:
        lines.append(
            f"- **{item['priority']}** `{item['ref']}` — {item['summary']} ({item['count']})"
        )

    lines.extend(
        [
            "",
            "## Diary",
            f"- Markdown entries in window: {diary['markdown_entries']}",
            f"- Latest entry date: {_metric(diary['latest_entry_date'])}",
            "",
            "## Goals",
            f"- Active: {goals['active_goals']}; overdue: {goals['overdue_goals']}; due in window: {goals['due_in_window']}",
            f"- Average progress: {_metric(goals['average_progress_percent'])}%",
            f"- By category: {_format_mapping(goals['by_category'])}",
            "",
            "## Decisions",
            f"- Open: {decisions['open_decisions']}; pending approval: {decisions['pending_approval']}",
            f"- By domain: {_format_mapping(decisions['by_domain'])}",
            f"- Pending approval by domain: {_format_mapping(brief['decision_rollup']['pending_approval_by_domain'])}",
            "",
            "## Human review queue",
        ]
    )
    if brief["action_queue"]:
        for item in brief["action_queue"]:
            lines.append(f"- `{item['ref']}` — {item['next_step']} ({item['count']})")
    else:
        lines.append("- No review item was raised by the aggregate scan.")

    lines.extend(
        [
            "",
            "## Guardrails",
            "- This brief contains no diary text, journal content, or goal titles by default.",
            "- The review queue is a prompt for the operator; it does not create tasks, send messages, or execute decisions.",
            "- Use the relevant approval gate before any consequential action.",
            "",
        ]
    )
    return "\n".join(lines)


def _format_mapping(values: dict[str, Any]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


def _resolve_output_dir(value: str | Path | None) -> Path:
    path = Path(value or DEFAULT_OUTPUT_DIR).expanduser()
    return (PROJECT_ROOT / path if not path.is_absolute() else path).resolve()


def _write_outputs(brief: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"decision-brief-{brief['cadence']}-{brief['as_of']}"
    markdown_path = output_dir / f"{stem}.md"
    json_path = output_dir / f"{stem}.json"
    markdown_path.write_text(render_markdown(brief), encoding="utf-8")
    json_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"markdown": str(markdown_path), "json": str(json_path)}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a privacy-safe Daily or Weekly decision brief")
    parser.add_argument("--cadence", choices=("daily", "weekly"), default="daily")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--window-days", type=int)
    parser.add_argument("--diary-root", type=Path)
    parser.add_argument("--include-goal-titles", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--write", action="store_true", help="Write Markdown and JSON files")
    parser.add_argument("--json", action="store_true", help="Print the bounded payload as JSON")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        brief = build_decision_brief(
            cadence=args.cadence,
            as_of=args.as_of,
            window_days=args.window_days,
            diary_root=args.diary_root,
            include_goal_titles=args.include_goal_titles,
        )
        output_paths = _write_outputs(brief, _resolve_output_dir(args.output_dir)) if args.write else {}
    except (ValueError, OSError) as exc:
        print(json.dumps({"contract": BRIEF_CONTRACT, "status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2

    if args.json:
        print(
            json.dumps(
                {"contract": BRIEF_CONTRACT, "status": "written" if args.write else "preview", "output_paths": output_paths, "brief": brief},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_markdown(brief))
        if args.write:
            print(f"Outputs written under: {_resolve_output_dir(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

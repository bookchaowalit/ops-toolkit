#!/usr/bin/env python3
"""Build a privacy-safe Diary + Goals + Decisions context pack.

The pack contains bounded aggregates and stable references only. It never
reads diary Markdown content or goal titles by default. Finance stats left the
pack on 2026-10-11 with the finance tables (revenue, invoices and clients live
in the CRM ledger).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:
    from .utf8_stdio import configure_utf8_stdio
except ImportError:  # pragma: no cover - direct CLI execution
    from utf8_stdio import configure_utf8_stdio

UTILS_DIR = Path(__file__).resolve().parents[1] / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))
from _env import PROJECT_ROOT  # noqa: E402

DEFAULT_DIARY_ROOT = PROJECT_ROOT / "diary" / "entries"
CONTEXT_CONTRACT = "solo-empire.decision-context.v1"
DIARY_SUFFIXES = {".md", ".markdown"}


def _bounded_date(value: str | date | None, field: str) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc


def _window(as_of: str | date | None, window_days: int) -> tuple[date, date]:
    if window_days < 1 or window_days > 31:
        raise ValueError("window_days must be between 1 and 31")
    end = _bounded_date(as_of, "as_of")
    return end - timedelta(days=window_days - 1), end


def _round_metric(value: Any) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _diary_stats(root_value: str | Path | None, start: date, end: date) -> dict[str, Any]:
    root = Path(root_value or os.environ.get("SOLO_EMPIRE_DIARY_ROOT") or DEFAULT_DIARY_ROOT).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {
            "boundary_status": "missing",
            "markdown_entries": 0,
            "dates_with_entries": [],
            "latest_entry_date": None,
        }

    date_counts: list[dict[str, Any]] = []
    total = 0
    for offset in range((end - start).days + 1):
        entry_date = start + timedelta(days=offset)
        day_root = root / f"{entry_date.year:04d}" / f"{entry_date.month:02d}" / f"{entry_date.day:02d}"
        if day_root.is_symlink() or not day_root.is_dir():
            continue
        count = sum(
            1 for item in day_root.iterdir()
            if not item.is_symlink() and item.is_file() and item.suffix.lower() in DIARY_SUFFIXES
        )
        if count:
            total += count
            date_counts.append({"date": entry_date.isoformat(), "entry_count": count})
    latest = date_counts[-1]["date"] if date_counts else None
    return {
        "boundary_status": "available",
        "markdown_entries": total,
        "dates_with_entries": date_counts,
        "latest_entry_date": latest,
    }


def _goal_stats(as_of: date,
    due_until: date,
    include_titles: bool,
) -> dict[str, Any]:
    from cell_store import open_cell_store

    rows = sorted(
        open_cell_store("goals", "governance-reader").list("goals", status="active"),
        key=lambda row: (
            row["priority"] is not None, row["priority"] or 0,
            row["due_date"] is None, str(row["due_date"] or "")[:10], row["id"],
        ),
    )[:50]
    by_category: Counter[str] = Counter()
    overdue = 0
    due_next_window = 0
    progress_values: list[int] = []
    references: list[dict[str, Any]] = []
    for row in rows:
        category = str(row["category"] or "uncategorized")
        by_category[category] += 1
        progress = max(0, min(100, int(row["progress_percent"] or 0)))
        progress_values.append(progress)
        due_date = row["due_date"]
        if due_date:
            due = _bounded_date(due_date, "goal due_date")
            if due < as_of:
                overdue += 1
            elif due <= due_until:
                due_next_window += 1
        reference: dict[str, Any] = {
            "goal_id": row["id"],
            "category": category,
            "type": row["type"],
            "priority": row["priority"],
            "progress_percent": progress,
            "due_date": due_date,
        }
        if include_titles:
            reference["title"] = " ".join(str(row["title"] or "").split())[:120]
        references.append(reference)
    return {
        "active_goals": len(rows),
        "overdue_goals": overdue,
        "due_in_window": due_next_window,
        "average_progress_percent": _round_metric(sum(progress_values) / len(progress_values)) if progress_values else None,
        "by_category": dict(sorted(by_category.items())),
        "references": references,
        "goal_titles_included": include_titles,
    }


def _decision_stats() -> dict[str, Any]:
    """Aggregate counts only; the records come from the decisions cell."""
    from decision_store import open_decision_store

    rows = open_decision_store("governance-reader").all(status=["draft", "proposed", "approved", "in_progress", "deferred"])
    by_domain: Counter[str] = Counter()
    pending_approval = 0
    open_total = 0
    for row in rows:
        domain = str(row["domain"] or "general")
        by_domain[domain] += 1
        open_total += 1
        if row["approval_status"] == "pending":
            pending_approval += 1
    return {
        "open_decisions": open_total,
        "pending_approval": pending_approval,
        "by_domain": dict(sorted(by_domain.items())),
    }


def build_decision_context(
    *,
    as_of: str | date | None = None,
    window_days: int = 7,
    diary_root: str | Path | None = None,
    include_goal_titles: bool = False,
) -> dict[str, Any]:
    """Build a bounded cross-domain context pack without raw content."""
    start, end = _window(as_of, window_days)
    return {
        "contract": CONTEXT_CONTRACT,
        "privacy_class": "private",
        "as_of": end.isoformat(),
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": window_days,
        },
        "diary": {
            **_diary_stats(diary_root, start, end),
            "raw_markdown_included": False,
            "raw_journal_content_included": False,
        },
        "goals": _goal_stats(end,
            end + timedelta(days=window_days - 1),
            include_goal_titles,
        ),
        "decisions": _decision_stats(),
        "policy": {
            "raw_diary_text_included": False,
            "goal_titles_default": False,
        },
        "source_refs": [
            f"diary:metadata:{start.isoformat()}:{end.isoformat()}",
            "goals:active",
            "decision_records:open",
        ],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a private, aggregate Solo Empire decision context pack")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--diary-root", type=Path)
    parser.add_argument("--include-goal-titles", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    configure_utf8_stdio()
    args = _parse_args()
    try:
        result = build_decision_context(
            as_of=args.as_of,
            window_days=args.window_days,
            diary_root=args.diary_root,
            include_goal_titles=args.include_goal_titles,
        )
    except ValueError as exc:
        print(json.dumps({"contract": CONTEXT_CONTRACT, "status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

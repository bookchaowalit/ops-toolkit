"""Shared decision text helpers.

Decision records, their lifecycle events, and reviews live in the decisions
cell (``decision_store.open_decision_store``); the cell appends the lifecycle
event in the same transaction as each change. This module only bounds
operator-entered text and never executes the action a decision describes.
"""

from __future__ import annotations


EVENT_TYPES = {
    "created",
    "refreshed",
    "approved",
    "rejected",
    "deferred",
    "completed",
    "signal_cleared",
    "review_task_created",
    "reviewed",
}


def clean_text(value: str | None, limit: int = 500) -> str | None:
    """Keep operator-entered audit text bounded and single-line."""
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized[:limit] or None

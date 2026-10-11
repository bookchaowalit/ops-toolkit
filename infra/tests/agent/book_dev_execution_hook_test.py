#!/usr/bin/env python3
"""Offline contract tests for the Book Dev execution hook."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "workflows"))
from book_dev_execution_hook import (  # noqa: E402
    build_ticket,
    queue_execution,
    render_execution_markdown,
    select_candidate,
)


def candidate(task_id: int = 2135, dirty: bool = True) -> dict:
    return {
        "task": {
            "id": task_id,
            "title": f"[Book Dev] owner/repo-{task_id} — review dirty changes",
            "description": "bounded",
            "status": "todo",
            "priority": 2,
            "project": "Book Dev / Per-Repository",
            "due_date": None,
            "updated_at": "2026-08-11 00:00:00",
        },
        "repository": {
            "ref": f"owner/repo-{task_id}",
            "path": f"projects/product/engineering/book-dev/github/owner/repo-{task_id}",
            "state": "dirty" if dirty else "clean",
            "dirty": dirty,
            "dirty_file_count": 3 if dirty else 0,
            "branch": "main",
            "upstream": "origin/main",
            "has_upstream": True,
        },
        "repository_ref": f"owner/repo-{task_id}",
        "recommendation": "Inspect the existing dirty diff and map it to this task before making any change.",
        "context_files": ["repository-catalog/portfolio/BOOK-DEV-BACKLOG-BD.md"],
        "verification": ["Capture the exact verification command and its output."],
        "approval_gate": ["No commit without owner approval."],
        "agent_prompt": "Work on exactly one task.",
    }


def report_with(*candidates: dict) -> dict:
    return {
        "contract": "solo-empire.book-dev-engineering-loop.v1",
        "generated_at": "2026-08-11T00:00:00+00:00",
        "summary": {"candidate_count": len(candidates)},
        "candidates": list(candidates),
    }


def main() -> int:
    first = candidate(2135)
    second = candidate(2158)
    report = report_with(first, second)

    selected = select_candidate(report)
    assert selected is not None
    assert selected["task"]["id"] == 2135

    unresolved = {
        "task": {"id": 1, "title": "x", "status": "todo", "priority": 2},
        "repository": None,
        "recommendation": "resolve",
        "context_files": [],
        "verification": [],
        "approval_gate": [],
        "agent_prompt": "",
    }
    assert select_candidate(report_with(unresolved)) is None

    ticket = build_ticket(report=report, candidate=first, generated_at="2026-08-11T01:00:00+00:00")
    assert ticket["contract"] == "solo-empire.book-dev-engineering-loop-execution.v1"
    assert ticket["status"] == "queued"
    assert ticket["policy"]["automatic_release_closeout"] is True
    assert ticket["policy"]["automatic_merge"] is False
    assert ticket["policy"]["ai_provider_call_default"] is False
    assert ticket["fingerprint"]

    markdown = render_execution_markdown(ticket)
    assert "Book Dev Execution Ticket" in markdown
    assert "scoped commit/push" in markdown

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        latest = root / "latest.json"
        execution_dir = root / "execution"
        latest.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

        queued = queue_execution(
            latest_json=latest,
            execution_dir=execution_dir,
            write=True,
            force=True,
        )
        assert queued["status"] == "queued"
        assert (execution_dir / "current.json").is_file()
        assert (execution_dir / "current.md").is_file()
        assert (execution_dir / "state.json").is_file()
        history = list((execution_dir / "history").glob("*.json"))
        assert len(history) == 1

        # Idempotent: same fingerprint stays open.
        again = queue_execution(
            latest_json=latest,
            execution_dir=execution_dir,
            write=True,
            force=False,
        )
        assert again["status"] == "skipped_existing_open"

        # Force replaces the open ticket.
        forced = queue_execution(
            latest_json=latest,
            execution_dir=execution_dir,
            write=True,
            force=True,
        )
        assert forced["status"] == "queued"

        # Hook dry-run without env is not_configured.
        dry = queue_execution(
            latest_json=latest,
            execution_dir=execution_dir,
            write=True,
            force=True,
            invoke_hook=True,
            hook_dry_run=True,
        )
        assert dry["hook"]["status"] in {"not_configured", "dry_run"}

        os.environ["BOOK_DEV_AI_HOOK"] = "echo ticket={ticket} markdown={markdown} task={task_id}"
        try:
            dispatched = queue_execution(
                latest_json=latest,
                execution_dir=execution_dir,
                write=True,
                force=True,
                invoke_hook=True,
                hook_dry_run=False,
            )
            assert dispatched["hook"]["invoked"] is True
            assert dispatched["hook"]["status"] == "dispatched"
            assert dispatched["status"] == "dispatched"
            assert "ticket=" in (dispatched["hook"].get("stdout_tail") or "")
        finally:
            os.environ.pop("BOOK_DEV_AI_HOOK", None)

    print("Book Dev execution hook tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

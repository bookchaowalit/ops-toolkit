#!/usr/bin/env python3
"""Quest Marker for the active Guild OS season.

Reads the Season Quest Board under ``goals/seasons/`` whose window covers
today, then lists open tasks linked to its Main Quest goal through
``task_goal_links``. The board stays the source of season focus; this module
only renders a read-only marker for TODO-TODAY.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from task_store import open_task_store

AUTOMATION_ID = "portfolio-reader"

SEASONS_DIR = Path(__file__).resolve().parents[3] / "goals" / "seasons"

WINDOW_RE = re.compile(r"Season / window\s*\|\s*([^|·]+?)\s*·\s*(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})")
GOAL_ID_RE = re.compile(r"`goals\.id`\s*(\d+)")
MAIN_QUEST_RE = re.compile(r"^- \*\*Main Quest:\*\*\s*(.+?)(?=^- \*\*|^\s*$)", re.M | re.S)
BOSS_FIGHT_RE = re.compile(r"^- \*\*Boss Fight:\*\*\s*(.+?)(?=^- \*\*|^\s*$)", re.M | re.S)


@dataclass(frozen=True)
class SeasonBoard:
    season: str
    start: date
    end: date
    goal_id: int | None
    main_quest: str
    boss_fight: str
    path: Path


def _flatten(text: str) -> str:
    return " ".join(text.replace("**", "").split())


def parse_board(path: Path) -> SeasonBoard | None:
    text = path.read_text(encoding="utf-8")
    window = WINDOW_RE.search(text)
    if not window:
        return None
    goal = GOAL_ID_RE.search(text)
    quest = MAIN_QUEST_RE.search(text)
    boss = BOSS_FIGHT_RE.search(text)
    return SeasonBoard(
        season=window.group(1).strip(),
        start=date.fromisoformat(window.group(2)),
        end=date.fromisoformat(window.group(3)),
        goal_id=int(goal.group(1)) if goal else None,
        main_quest=_flatten(quest.group(1)) if quest else "",
        boss_fight=_flatten(boss.group(1)) if boss else "",
        path=path,
    )


def active_board(today: date, seasons_dir: Path = SEASONS_DIR) -> SeasonBoard | None:
    """Return the latest-starting board whose window covers ``today``."""
    boards = [
        board
        for path in sorted(seasons_dir.glob("*-quest-board.md"))
        if (board := parse_board(path)) and board.start <= today <= board.end
    ]
    return max(boards, key=lambda board: board.start, default=None)


def next_actions(goal_id: int, limit: int = 3) -> list[tuple]:
    """Open tasks linked to the Main Quest goal, earliest due first."""
    store = open_task_store(None, AUTOMATION_ID)
    numbers = sorted({link["task"] for link in store.links(target_type="goal", target_id=goal_id)})
    if not numbers:
        return []
    open_tasks = [
        task for task in store.list(numbers=numbers)
        if task["status"] not in ("done", "cancelled", "archived")
    ]
    open_tasks.sort(key=lambda task: (task["due_date"] is None, task["due_date"] or "", task["id"]))
    return [(task["id"], task["title"], task["due_date"]) for task in open_tasks[:limit]]


def render_marker(today: date, seasons_dir: Path = SEASONS_DIR) -> str:
    """Markdown Quest Marker section; empty-state text when no season is active."""
    board = active_board(today, seasons_dir)
    lines = ["## Quest Marker", ""]
    if board is None:
        lines += [
            "- *No active Season Quest Board covers today — start one from "
            "`templates/goals/season-quest-board.md`.*",
            "",
        ]
        return "\n".join(lines)

    days_left = (board.end - today).days
    lines += [
        f"**Season:** {board.season} ({days_left} days left) · board: `goals/seasons/{board.path.name}`",
        "",
        f"- **Main Quest:** {board.main_quest or '*not set*'}",
        f"- **Boss Fight:** {board.boss_fight or '*not set*'}",
        "",
        "### Next Action",
        "",
    ]
    actions = next_actions(board.goal_id) if board.goal_id else []
    if actions:
        for task_id, title, due in actions:
            overdue = " ⚠ overdue" if due and due < today.isoformat() else ""
            lines.append(f"- [ ] #{task_id} {title} (due {due or 'n/a'}{overdue})")
    else:
        lines.append("- *No open task linked to the Main Quest — add the next action within 2 hours.*")
    lines.append("")
    return "\n".join(lines)

#!/usr/bin/env python3
"""Portfolio readers read and update tasks only through the task store adapter.

Run: bash scripts/setup/run-python3.sh tests/tasks/portfolio_readers_task_store_test.py
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
for sub in ("utils", "workflows"):
    sys.path.insert(0, str(ROOT / "infra" / "scripts" / sub))

from _env import skip_without_knowledge  # noqa: E402

skip_without_knowledge("portfolio_readers_task_store_test.py")  # needs solo-knowledge; skipped in CI when absent

# cron_jobs and the operating tables left schema.sql (migration 085); their legacy DDL serves the tests.
LEGACY_OIA = Path(__file__).resolve().parents[1] / "support" / "legacy_operating_intake_automation_tables.sql"
# The goals, projects and roles-skills tables left schema.sql (migration 084); fixtures still seed them.
LEGACY_PORTFOLIO = Path(__file__).resolve().parents[1] / "support" / "legacy_portfolio_tables.sql"

import yaml  # noqa: E402

import ai_cycle  # noqa: E402
import book_dev_loop  # noqa: E402
import domain_enrichment  # noqa: E402
import portfolio_domain_loop  # noqa: E402
import portfolio_progress  # noqa: E402
import season_quest  # noqa: E402
import update_goal_progress  # noqa: E402
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402
from sqlite_task_store import SqliteTaskStore, apply_legacy_task_tables  # noqa: E402

AUTOMATION = "portfolio-reader"
TASK_TABLE_SQL = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE_INFO\(|FOREIGN_KEY_LIST\()\s*\"?(?:tasks|task_assignments|task_goal_links"
    r"|task_project_links|task_context_links|task_skill_links|task_role_suggestions|task_types)\b",
    re.IGNORECASE,
)
INCOME = "\U0001f4bc Income"

TASKS = [
    # id, title, status, priority, project, due_date, parent, archived_at, description, completed_at
    (1, "Root plan", "todo", 1, INCOME, None, None, None, None, None),
    (2, "Child done", "done", 2, INCOME, None, 1, None, None, "2026-10-05 10:00:00"),
    (3, "Child doing", "in_progress", 2, INCOME, None, 1, None, None, None),
    (4, "[Book Dev] acme/app — build", "todo", 2, "Misc", "2026-11-01", None, None, None, None),
    (5, "Tooling", "in_progress", 1, "Book-Dev Tools", "2026-10-20", None, None, None, None),
    (6, "Notes", "blocked", 3, None, None, None, None, "About BOOK DEV flow", None),
    (7, "Archived", "todo", 2, INCOME, None, None, "2026-10-01 00:00:00", None, None),
    (8, "Cancelled", "cancelled", 2, INCOME, None, None, None, None, None),
    (9, "Plan without goal", "todo", 3, "Ops", None, None, None, None, None),
    (10, "Plan child", "blocked", 3, "Ops", None, 9, None, None, None),
    (11, "Unknown key", "todo", 2, "mystery-key", None, None, None, None, None),
    (12, "Cycle task", "in_progress", 2, "cycle", None, None, None, None, None),
    (13, "Mixed case", "todo", 4, "my BOOKish dev", None, None, None, None, None),
    (14, "[book dev] x/y", "todo", 2, "Elsewhere", "2026-10-02", None, None, None, None),
]


def build_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(LEGACY_OIA.read_text(encoding="utf-8"))
    conn.executescript(LEGACY_PORTFOLIO.read_text(encoding="utf-8"))
    apply_legacy_task_tables(conn)
    conn.executemany(
        "INSERT INTO tasks (id, title, status, priority, project, due_date, parent_task_id, archived_at,"
        " description, completed_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-10-01 08:00:00')",
        TASKS,
    )
    conn.executescript(
        """
        INSERT INTO goals (id, title, status, domain, priority) VALUES (1, 'Income goal', 'active', 'book-strategy', 1);
        INSERT INTO projects (id, name, slug, status, domain) VALUES
            (1, '\U0001f4bc Income', 'income', 'active', 'book-strategy'), (2, 'Ops', 'ops', 'active', 'book-work');
        INSERT INTO goal_project_links (goal_id, project_id) VALUES (1, 1);
        INSERT INTO portfolio_work_items (id, item_type, title, goal_id, project_id, source_key, source)
            VALUES (1, 'story', 'Story', 1, 1, 'fixture:story', 'fixture');
        INSERT INTO work_item_task_links (work_item_id, task_id) VALUES (1, 12);
        INSERT INTO task_goal_links (task_id, goal_id) VALUES (1, 1);
        INSERT INTO task_project_links (task_id, project_id) VALUES (1, 1), (4, 2);
        INSERT INTO portfolio_contexts (id, slug, name, context_type) VALUES (1, 'personal', 'Personal', 'personal');
        INSERT INTO task_context_links (task_id, context_id) VALUES (1, 1);
        INSERT INTO skills (id, name, slug) VALUES (1, 'Python', 'python');
        INSERT INTO task_skill_links (task_id, skill_id, match_reason) VALUES (4, 1, 'fixture');
        INSERT INTO task_assignments (task_id, assignee_type, assignee_id) VALUES (6, 'role', 'ROLE_ID'), (1, 'user', '1');
        INSERT INTO task_role_suggestions (task_id, role_id, confidence, match_reason, review_status, source_registry)
            VALUES (5, 'ROLE_ID', 'high', 'why', 'accepted', 'registry'), (9, 'ROLE_ID', 'low', 'x', 'suggested', 'registry');
        INSERT INTO operating_cycles (id, slug, name, status, start_date, end_date, focus)
            VALUES (1, 'c1', 'Cycle', 'planned', '2026-10-05', '2026-10-11', 'focus');
        INSERT INTO operating_cycle_tasks (cycle_id, task_id, commitment_type, estimate_minutes) VALUES
            (1, 12, 'committed', 30), (1, 5, 'stretch', NULL), (1, 9, 'committed', NULL);
        """.replace("ROLE_ID", first_role_id())
    )
    conn.execute("UPDATE tasks SET estimated_minutes = 45 WHERE id IN (5, 9)")
    conn.commit()
    return conn


def first_role_id() -> str:
    path = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-CATALOG.yaml"
    if not path.is_file():  # solo-architecture checkout absent: any role id satisfies the fixture
        return "fixture-role"
    catalog = yaml.safe_load((ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-CATALOG.yaml").read_text(encoding="utf-8"))
    return str(next(role["id"] for role in catalog["roles"] if isinstance(role, dict) and role.get("id")))


def copy_of(conn: sqlite3.Connection) -> sqlite3.Connection:
    clone = sqlite3.connect(":memory:")
    clone.row_factory = sqlite3.Row
    conn.backup(clone)
    return clone


def jsonable(value):
    """JSON-normalise, dropping the volatile updated_at stamps."""

    def strip(item):
        if isinstance(item, dict):
            return {k: strip(v) for k, v in item.items() if k != "updated_at"}
        if isinstance(item, list):
            return [strip(v) for v in item]
        return item

    return strip(json.loads(json.dumps(value, default=str, sort_keys=True)))


def snapshot(mods: SimpleNamespace, conn: sqlite3.Connection, workdir: Path, task_tables: list[str],
             progress_of) -> dict:
    """Everything the nine readers produce on ``conn``; the same function ran on the pre-adapter code."""
    out: dict = {}

    # portfolio_progress: dry run, then a real run with the report written to workdir
    out["rollup_dry"] = mods.portfolio_progress.rollup(dry_run=True)
    out["progress_after_dry"] = progress_of()
    mods.portfolio_progress.REPORT_PATH = workdir / "PORTFOLIO-PROGRESS.md"
    out["rollup"] = mods.portfolio_progress.rollup(dry_run=False)
    out["rollup_again"] = mods.portfolio_progress.rollup(dry_run=True)
    out["task_progress"] = progress_of()
    report = mods.portfolio_progress.REPORT_PATH.read_text(encoding="utf-8")
    out["report_rows"] = [line for line in report.splitlines() if line.startswith("| #")]

    # update_goal_progress: only the Income project maps to book-strategy
    mods.update_goal_progress.DOMAINS_DIR = workdir / "projects"
    (workdir / "projects" / "book-strategy").mkdir(parents=True, exist_ok=True)
    mods.update_goal_progress.ENRICHMENT_JSON = workdir / "enrichment.json"
    shadow = copy_of(conn)
    with patch("builtins.print"), patch.object(mods.update_goal_progress, "open_cell_store", sqlite_cell_opener(shadow)):
        out["goals_updated"] = mods.update_goal_progress.update_goal_progress()
        scores = mods.update_goal_progress.update_enrichment_scores()
    out["goal_progress"] = [r[0] for r in shadow.execute("SELECT progress_percent FROM goals ORDER BY id")]
    out["enrichment_tasks"] = [(d["domain"], d["tasks_total"], d["tasks_done"]) for d in scores["domains"]]

    # season_quest
    out["next_actions"] = [list(row) for row in mods.season_quest.next_actions(1, limit=5)]
    out["next_actions_none"] = [list(row) for row in mods.season_quest.next_actions(99)]

    # domain_enrichment reads the goals, projects and tasks through the stores
    out["domain_enrichment"] = jsonable(mods.domain_enrichment.load_db_data())

    # portfolio_domain_loop / book_dev_loop
    profile = {"task_projects": [INCOME, "Ops"], "task_project_prefixes": ["book"], "title_prefixes": ["[Book Dev]"]}
    out["domain_tasks"] = jsonable(mods.portfolio_domain_loop.load_domain_tasks(profile))
    out["book_dev_tasks"] = jsonable(mods.book_dev_loop.load_book_dev_tasks())

    # ai_cycle
    out["cycle_tasks"] = jsonable(mods.ai_cycle.cycle_tasks(1))
    out["cycle_errors"] = mods.ai_cycle.command_check()

    return out


MODULES = SimpleNamespace(
    ai_cycle=ai_cycle, book_dev_loop=book_dev_loop, domain_enrichment=domain_enrichment,
    portfolio_domain_loop=portfolio_domain_loop, portfolio_progress=portfolio_progress,
    season_quest=season_quest, update_goal_progress=update_goal_progress,
)
TASK_TABLES = [
    "tasks", "task_assignments", "task_goal_links", "task_project_links", "task_context_links",
    "task_skill_links", "task_role_suggestions", "task_types",
]


class Spy:
    """A SqliteTaskStore over a copy of the database, recording the automation ids it was opened with."""

    def __init__(self, shadow: sqlite3.Connection) -> None:
        self.shadow = shadow
        self.opened: list[str] = []

    def __call__(self, conn, automation, **_kwargs):
        self.opened.append(automation)
        return SqliteTaskStore(self.shadow)


def patch_all(spy: Spy, cell_conn: sqlite3.Connection | None = None):
    stack = [patch.object(getattr(MODULES, name), "open_task_store", spy) for name in vars(MODULES)]
    if cell_conn is not None:  # goals / projects / roles-skills live in cells; run on the SQLite double
        opener = sqlite_cell_opener(cell_conn)
        stack += [patch.object(getattr(MODULES, name), "open_cell_store", opener)
                  for name in vars(MODULES) if hasattr(getattr(MODULES, name), "open_cell_store")]
    return stack


def run_snapshot(conn: sqlite3.Connection, task_tables: list[str], task_db: sqlite3.Connection | None = None) -> dict:
    """``task_db`` is where the task rows live when the readers get them from a store over another database."""
    holder = task_db or conn

    def progress_of() -> list[int]:
        return [r[0] for r in holder.execute("SELECT progress_percent FROM tasks ORDER BY id")]

    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
        if task_db is None:  # tasks live in the Tasks API in production; run the readers on the SQLite double
            for patcher in patch_all(Spy(conn), conn):
                stack.enter_context(patcher)
        return snapshot(MODULES, conn, Path(tmp), task_tables, progress_of)


# Captured by running snapshot() on the pre-adapter code (git HEAD) over build_db().
EXPECTED = json.loads(r"""{
 "book_dev_tasks": [
  {
   "description": null,
   "due_date": "2026-10-20",
   "id": 5,
   "parent_task_id": null,
   "priority": 1,
   "project": "Book-Dev Tools",
   "status": "in_progress",
   "title": "Tooling"
  },
  {
   "description": null,
   "due_date": "2026-10-02",
   "id": 14,
   "parent_task_id": null,
   "priority": 2,
   "project": "Elsewhere",
   "status": "todo",
   "title": "[book dev] x/y"
  },
  {
   "description": null,
   "due_date": "2026-11-01",
   "id": 4,
   "parent_task_id": null,
   "priority": 2,
   "project": "Misc",
   "status": "todo",
   "title": "[Book Dev] acme/app — build"
  },
  {
   "description": null,
   "due_date": null,
   "id": 13,
   "parent_task_id": null,
   "priority": 4,
   "project": "my BOOKish dev",
   "status": "todo",
   "title": "Mixed case"
  },
  {
   "description": "About BOOK DEV flow",
   "due_date": null,
   "id": 6,
   "parent_task_id": null,
   "priority": 3,
   "project": null,
   "status": "blocked",
   "title": "Notes"
  }
 ],
 "cycle_errors": [
  "planned cycle has started work (2 task(s)): c1"
 ],
 "cycle_tasks": [
  {
   "actual_minutes": 0,
   "commitment_type": "committed",
   "due_date": null,
   "estimate_minutes": 30,
   "evidence_required": 1,
   "id": 12,
   "priority": 2,
   "progress_percent": 50,
   "project": "cycle",
   "status": "in_progress",
   "task_estimated_minutes": null,
   "title": "Cycle task"
  },
  {
   "actual_minutes": 0,
   "commitment_type": "committed",
   "due_date": null,
   "estimate_minutes": 45,
   "evidence_required": 1,
   "id": 9,
   "priority": 3,
   "progress_percent": 0,
   "project": "Ops",
   "status": "todo",
   "task_estimated_minutes": 45,
   "title": "Plan without goal"
  },
  {
   "actual_minutes": 0,
   "commitment_type": "stretch",
   "due_date": "2026-10-20",
   "estimate_minutes": 45,
   "evidence_required": 1,
   "id": 5,
   "priority": 1,
   "progress_percent": 50,
   "project": "Book-Dev Tools",
   "status": "in_progress",
   "task_estimated_minutes": 45,
   "title": "Tooling"
  }
 ],
 "domain_enrichment": [
  {
   "book-strategy": {
    "active": 1,
    "total": 1
   }
  },
  {
   "book-strategy": 1,
   "book-work": 1
  },
  {
   "book-creative": {
    "done": 0,
    "total": 0
   },
   "book-health": {
    "done": 0,
    "total": 0
   },
   "book-learning": {
    "done": 0,
    "total": 0
   },
   "book-life": {
    "done": 0,
    "total": 0
   },
   "book-strategy": {
    "done": 1,
    "total": 4
   },
   "book-work": {
    "done": 0,
    "total": 0
   }
  }
 ],
 "domain_tasks": [
  {
   "description": null,
   "due_date": "2026-10-20",
   "id": 5,
   "parent_task_id": null,
   "priority": 1,
   "project": "Book-Dev Tools",
   "status": "in_progress",
   "title": "Tooling"
  },
  {
   "description": null,
   "due_date": null,
   "id": 3,
   "parent_task_id": 1,
   "priority": 2,
   "project": "💼 Income",
   "status": "in_progress",
   "title": "Child doing"
  },
  {
   "description": null,
   "due_date": null,
   "id": 1,
   "parent_task_id": null,
   "priority": 1,
   "project": "💼 Income",
   "status": "todo",
   "title": "Root plan"
  },
  {
   "description": null,
   "due_date": "2026-10-02",
   "id": 14,
   "parent_task_id": null,
   "priority": 2,
   "project": "Elsewhere",
   "status": "todo",
   "title": "[book dev] x/y"
  },
  {
   "description": null,
   "due_date": "2026-11-01",
   "id": 4,
   "parent_task_id": null,
   "priority": 2,
   "project": "Misc",
   "status": "todo",
   "title": "[Book Dev] acme/app — build"
  },
  {
   "description": null,
   "due_date": null,
   "id": 7,
   "parent_task_id": null,
   "priority": 2,
   "project": "💼 Income",
   "status": "todo",
   "title": "Archived"
  },
  {
   "description": null,
   "due_date": null,
   "id": 9,
   "parent_task_id": null,
   "priority": 3,
   "project": "Ops",
   "status": "todo",
   "title": "Plan without goal"
  },
  {
   "description": null,
   "due_date": null,
   "id": 10,
   "parent_task_id": 9,
   "priority": 3,
   "project": "Ops",
   "status": "blocked",
   "title": "Plan child"
  }
 ],
 "enrichment_tasks": [
  [
   "book-strategy",
   4,
   1
  ]
 ],
 "goal_progress": [
  25
 ],
 "goals_updated": 1,
 "next_actions": [
  [
   1,
   "Root plan",
   null
  ]
 ],
 "next_actions_none": [],
 "progress_after_dry": [
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0
 ],
 "report_rows": [
  "| #1 Income goal (active) | 50% | income (62%) | 0 | 1 | 0/1 | 2 |",
  "| #9 Plan without goal | Ops | 0% | 0/1 | 1 |"
 ],
 "rollup": {
  "goal_tasks_updated": 0,
  "goals_updated": 1,
  "projects_updated": 2,
  "tasks_updated": 5,
  "work_items_updated": 1
 },
 "rollup_again": {
  "goal_tasks_updated": 0,
  "goals_updated": 0,
  "projects_updated": 0,
  "tasks_updated": 0,
  "work_items_updated": 0
 },
 "rollup_dry": {
  "goal_tasks_updated": 0,
  "goals_updated": 1,
  "projects_updated": 2,
  "tasks_updated": 5,
  "work_items_updated": 1
 },
 "task_progress": [
  75,
  100,
  50,
  0,
  50,
  0,
  0,
  0,
  0,
  0,
  0,
  50,
  0,
  0
 ]
}""")


def test_default_sqlite_path_matches_pre_adapter_output():
    conn = build_db()
    got = jsonable(run_snapshot(conn, TASK_TABLES))
    for key, expected in EXPECTED.items():
        assert got[key] == expected, (key, got[key], expected)


def test_readers_use_the_store_and_never_query_task_tables_directly():
    master = build_db()
    # Readers and writers see only the main connection; the store gets a separate copy of the data.
    conn = copy_of(master)
    shadow = copy_of(master)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    spy = Spy(shadow)
    patches = patch_all(spy, conn)
    for p in patches:
        p.start()
    try:
        got = jsonable(run_snapshot(conn, TASK_TABLES, shadow))
    finally:
        for p in patches:
            p.stop()
    direct = [sql for sql in statements if TASK_TABLE_SQL.search(sql)]
    assert not direct, direct[:5]
    assert spy.opened and set(spy.opened) == {AUTOMATION, "progress-rollup"}, set(spy.opened)
    # The progress roll-up wrote task progress through the store (the copy), not through the main connection.
    assert [r[0] for r in master.execute("SELECT progress_percent FROM tasks ORDER BY id")] == [0] * len(TASKS)
    assert got["task_progress"] != [0] * len(TASKS)
    for key, expected in EXPECTED.items():
        assert got[key] == expected, (key, got[key], expected)


def test_dry_run_rollup_never_writes_through_the_store():
    conn = build_db()
    calls: list[str] = []

    class Guarded(SqliteTaskStore):
        def update(self, number, fields, *, legacy=None):
            calls.append("update")
            super().update(number, fields, legacy=legacy)

    with patch.object(portfolio_progress, "open_task_store", lambda c, aid: Guarded(conn)), \
            patch.object(portfolio_progress, "open_cell_store", sqlite_cell_opener(conn)):
        counts = portfolio_progress.rollup(dry_run=True)
    assert calls == [] and counts["tasks_updated"] == EXPECTED["rollup_dry"]["tasks_updated"] > 0


def test_book_dev_filter_is_ascii_case_insensitive_like_sqlite_like():
    conn = build_db()
    with patch.object(book_dev_loop, "open_task_store", lambda c, aid, **_: SqliteTaskStore(conn)):
        ids = [task["id"] for task in book_dev_loop.load_book_dev_tasks()]
    legacy = [row[0] for row in conn.execute(
        """
        SELECT id FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
          AND (title LIKE '[Book Dev]%' OR lower(coalesce(project, '')) LIKE '%book%dev%'
               OR lower(coalesce(description, '')) LIKE '%book dev%')
        ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'todo' THEN 1 WHEN 'blocked' THEN 2 ELSE 3 END,
                 priority ASC, CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date ASC, id ASC
        """
    )]
    assert ids == legacy and set(ids) == {4, 5, 6, 13, 14}, (ids, legacy)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--capture":
        # Run against the pre-adapter modules (put them first on sys.path) to regenerate EXPECTED.
        print(json.dumps(jsonable(run_snapshot(build_db(), TASK_TABLES)), sort_keys=True))
        sys.exit(0)
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print(f"ok {name}")
    print("Portfolio readers task store tests passed")

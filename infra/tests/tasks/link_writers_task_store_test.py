#!/usr/bin/env python3
"""Seed and sync writers read and write task link/assignment/suggestion tables only through the task store.

Run: bash scripts/setup/run-python3.sh tests/tasks/link_writers_task_store_test.py
"""

from __future__ import annotations

import contextlib
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "docs/registries", "solo-architecture checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "db"))

SCHEMA = (ROOT / 'infra' / 'tests' / 'support' / 'sqlite_tasks_schema.sql')

import autonomous_portfolio_seed  # noqa: E402
import portfolio_context_seed  # noqa: E402
import role_portfolio_seed  # noqa: E402
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import apply_legacy_portfolio_tables, sqlite_cell_opener  # noqa: E402  (isolates the cell APIs)
from sqlite_task_store import SqliteTaskStore  # noqa: E402

OTHER_TABLES = """
CREATE TABLE collaborators (id INTEGER PRIMARY KEY, name TEXT, role TEXT, is_active INT);
"""


def cells(conn: sqlite3.Connection, *modules):
    """Run the modules against the goals / projects / roles-skills cells backed by ``conn``."""
    stack = contextlib.ExitStack()
    opener = sqlite_cell_opener(conn)
    for module in modules:
        stack.enter_context(patch.object(module, "open_cell_store", opener))
    return stack


def tasks_db(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    apply_legacy_portfolio_tables(conn)  # goals, projects and roles-skills tables behind the cell double
    conn.executescript(OTHER_TABLES)
    return conn


def add_task(conn: sqlite3.Connection, title: str, project: str | None = None, status: str = "todo",
             progress: int = 0) -> int:
    return conn.execute(
        "INSERT INTO tasks (title, status, project, progress_percent) VALUES (?, ?, ?, ?)",
        (title, status, project, progress)).lastrowid


class SpyStore(SqliteTaskStore):
    """The real SQLite store that records the calls made through it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self.calls: list[tuple] = []

    def __getattribute__(self, name: str):
        attr = super().__getattribute__(name)
        if name in {"link", "links", "assign", "assignments", "suggest_role", "role_suggestions", "list"}:
            def wrapper(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return attr(*args, **kwargs)
            return wrapper
        return attr


def spy_opener(spy: SpyStore, opened: list[str]):
    def opener(conn, automation, **_):
        opened.append(automation)
        return spy
    return opener


# ── autonomous_portfolio_seed ───────────────────────────────
def test_autonomous_run_links_assignments_and_counts():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        setup = tasks_db(str(path))
        setup.commit()
        setup.close()
        spy_holder: list[SpyStore] = []
        opened: list[str] = []

        def connect():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn

        def opener(conn, automation, **_):
            if conn is None:  # production opens the store without a connection; autocommit stands in for the API
                conn = sqlite3.connect(path, isolation_level=None)
                conn.row_factory = sqlite3.Row
            spy = SpyStore(conn)
            spy_holder.append(spy)
            opened.append(automation)
            return spy

        with patch.object(autonomous_portfolio_seed, "open_task_store", opener), \
                cells(connect(), autonomous_portfolio_seed):
            first = autonomous_portfolio_seed.run()
            second = autonomous_portfolio_seed.run()
        assert first == second and first["tasks"] == len(autonomous_portfolio_seed.TASKS)
        assert set(opened) == {"portfolio-seeder"}
        check = connect()
        n = first["tasks"]
        assert check.execute("SELECT COUNT(*) FROM task_goal_links").fetchone()[0] == n
        assert check.execute("SELECT COUNT(*) FROM task_project_links").fetchone()[0] == n
        owner = check.execute("SELECT assignee_type, assignee_id, assignee_name, assignment_type FROM task_assignments").fetchall()
        assert len(owner) == n and {tuple(r) for r in owner} == {("user", "1", "Owner", "assigned")}
        counts = check.execute("SELECT slug, task_count FROM projects WHERE task_count > 0").fetchall()
        assert sum(r[1] for r in counts) == n
        assert check.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 1
        assert check.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == len(autonomous_portfolio_seed.PROJECTS)
        assert check.execute("SELECT COUNT(*) FROM goal_project_links").fetchone()[0] == len(autonomous_portfolio_seed.PROJECTS)
        assert any(c[0] == "links" and c[2] == {"target_type": "project"} for s in spy_holder for c in s.calls)


# ── portfolio_context_seed ──────────────────────────────────
def goal_project_fixture() -> sqlite3.Connection:
    conn = tasks_db()
    conn.executescript("""
        INSERT INTO projects (id, slug, name) VALUES
            (1, 'solo-empire', 'Solo'), (2, 'book-ai', 'AI'), (3, 'personal-business', 'Business');
        INSERT INTO goals (id, title, category, domain, progress_percent) VALUES
            (10, 'g1', 'tech', 'x', 20), (11, 'g2', 'business', 'x', 60), (12, 'g3', 'tech', 'x', 40);
    """)
    t1, t2, t3 = (add_task(conn, f"t{i}", "solo-empire") for i in range(3))
    for task, goal, project in ((t1, 10, 1), (t1, 11, 2), (t2, 10, 2), (t3, 12, 1)):
        conn.execute("INSERT INTO task_goal_links (task_id, goal_id, link_type, source) VALUES (?, ?, 'primary', 's')",
                     (task, goal))
        if project and (task, project) not in {(t1, 2)}:
            conn.execute("INSERT OR IGNORE INTO task_project_links (task_id, project_id, source) VALUES (?, ?, 's')",
                         (task, project))
    conn.execute("INSERT INTO goal_project_links (goal_id, project_id, relation_type, weight, source)"
                 " VALUES (11, 3, 'delivery', 1, 's')")
    return conn


def test_goal_project_links_match_old_join_and_use_store():
    conn = goal_project_fixture()
    old = {tuple(r) for r in conn.execute(
        "SELECT DISTINCT tgl.goal_id, tpl.project_id FROM task_goal_links tgl "
        "JOIN task_project_links tpl ON tpl.task_id = tgl.task_id")}
    spy, opened = SpyStore(conn), []
    with patch.object(portfolio_context_seed, "open_task_store", spy_opener(spy, opened)), \
            cells(conn, portfolio_context_seed):
        portfolio_context_seed.ensure_goal_project_links({})
    assert opened == ["portfolio-seeder"]
    created = {(r[0], r[1]) for r in conn.execute("SELECT goal_id, project_id FROM goal_project_links WHERE source = ?",
                                                  (portfolio_context_seed.SOURCE,))}
    # Every task-derived pair not already linked was created; nothing else from the join is missing.
    existing = {(11, 3)}
    assert (old - existing) <= created
    assert {c[0] for c in spy.calls} == {"links"}


def test_task_contexts_reads_goal_links_through_store():
    conn = goal_project_fixture()
    spy, opened = SpyStore(conn), []
    with patch.object(portfolio_context_seed, "open_task_store", spy_opener(spy, opened)), \
            cells(conn, portfolio_context_seed):
        mapping = portfolio_context_seed.task_contexts({"personal-life": 99}, {}, {1: [200]}, {10: [100, 101], 11: [102], 12: [103]})
    ids = [r[0] for r in conn.execute("SELECT id FROM tasks ORDER BY id")]
    assert mapping[ids[0]] == [200, 100, 101, 102] and mapping[ids[1]] == [200, 100, 101] and mapping[ids[2]] == [200, 103]
    assert opened == ["portfolio-seeder"]


def test_context_report_counts_tasks_from_store_links():
    conn = tasks_db()
    conn.executescript("""
        INSERT INTO portfolio_contexts (id, slug, name, context_type, scope) VALUES
            (1, 'ctx-a', 'A', 'personal', 'personal'), (2, 'ctx-b', 'B', 'personal', 'personal');
        INSERT INTO goals (id, title, progress_percent) VALUES (5, 'g5', 30), (6, 'g6', 50);
        INSERT INTO goal_context_links (goal_id, context_id) VALUES (5, 1), (6, 1);
        INSERT INTO projects (id, slug, name) VALUES (7, 'p', 'P'), (8, 'q', 'Q');
        INSERT INTO project_context_links (project_id, context_id) VALUES (7, 1), (8, 1);
    """)
    ids = [add_task(conn, f"t{i}") for i in range(3)]
    for task, ctx in ((ids[0], 1), (ids[1], 1), (ids[2], 2), (ids[0], 2)):
        conn.execute("INSERT INTO task_context_links (task_id, context_id, source) VALUES (?, ?, 's')", (task, ctx))
    spy, opened = SpyStore(conn), []
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "r.md"
        with patch.object(portfolio_context_seed, "REPORT_PATH", report), \
                patch.object(portfolio_context_seed, "open_task_store", spy_opener(spy, opened)), \
                cells(conn, portfolio_context_seed):
            portfolio_context_seed.write_report()
        lines = {l.split("`")[1]: l for l in report.read_text(encoding="utf-8").splitlines() if l.startswith("| `ctx-")}
    # Goals | Projects | Tasks | Progress
    assert "| 2 | 2 | 2 | 40.0% |" in lines["ctx-a"] and "| 0 | 0 | 2 | 0% |" in lines["ctx-b"]
    assert opened == ["portfolio-seeder"] and spy.calls[0][2] == {"target_type": "context"}


# ── role_portfolio_seed ─────────────────────────────────────
def test_role_skill_links_upsert_through_store():
    conn = tasks_db()
    conn.execute("INSERT INTO collaborators VALUES (1, 'n', 'owner', 1)")  # unrelated table stays untouched
    spy, opened = SpyStore(conn), []
    with patch.object(role_portfolio_seed, "open_task_store", spy_opener(spy, opened)):
        task_id, created = role_portfolio_seed.ensure_role_task({"id": "role-x", "name": "Role X"}, 3, ["a", "b", "zz"], {"a": 11, "b": 12})
    assert created and set(opened) == {"portfolio-seeder"}
    skills = conn.execute("SELECT skill_id, link_type, match_reason, source_registry FROM task_skill_links "
                          "ORDER BY skill_id").fetchall()
    assert [tuple(r) for r in skills] == [
        (11, "accepted", "role baseline for role-x", "docs/ROLE-LEARNING.yaml"),
        (12, "accepted", "role baseline for role-x", "docs/ROLE-LEARNING.yaml"),
    ]
    assignment = conn.execute("SELECT task_id, assignee_type, assignee_id, assignee_name, assignment_type "
                              "FROM task_assignments").fetchall()
    assert [tuple(r) for r in assignment] == [(task_id, "role", "role-x", "Role X", "assigned")]
    assert [tuple(r) for r in conn.execute("SELECT goal_id, link_type, source FROM task_goal_links")] == [
        (3, "primary", role_portfolio_seed.SOURCE)]
    assert {c[0] for c in spy.calls} >= {"assign", "link"}
    # a second pass finds the active assigned task through store.assignments and writes nothing
    spy.calls.clear()
    with patch.object(role_portfolio_seed, "open_task_store", spy_opener(spy, opened)):
        assert role_portfolio_seed.ensure_role_task({"id": "role-x", "name": "Role X"}, 3, ["a"], {"a": 11}) == (task_id, False)
    assert spy.calls[0][0] == "assignments" and spy.calls[0][2] == {"assignee_type": "role", "assignee_id": "role-x"}


OLD_STORIES = """
SELECT DISTINCT tgl.goal_id, tpl.project_id, t.id AS task_id, t.title,
       t.description, t.status, t.progress_percent, e.id AS epic_id
FROM task_goal_links tgl
JOIN tasks t ON t.id = tgl.task_id
JOIN task_project_links tpl ON tpl.task_id = t.id
JOIN portfolio_work_items e
  ON e.goal_id = tgl.goal_id AND e.project_id = tpl.project_id AND e.item_type = 'epic'
ORDER BY tgl.goal_id, tpl.project_id, t.id
"""


def test_work_hierarchy_stories_match_old_join_and_use_store():
    conn = goal_project_fixture()
    conn.execute("UPDATE tasks SET status = 'in_progress', progress_percent = 30 WHERE id = 1")
    conn.execute("UPDATE tasks SET archived_at = datetime('now') WHERE id = 3")
    spy, opened = SpyStore(conn), []
    with patch.object(role_portfolio_seed, "open_task_store", spy_opener(spy, opened)), \
            cells(conn, role_portfolio_seed):
        counts = role_portfolio_seed.ensure_work_hierarchy({"solo-empire": 1})
    assert set(opened) == {"portfolio-seeder"}
    old = conn.execute(OLD_STORIES).fetchall()
    stories = conn.execute("SELECT source_key, title, status, progress_percent, parent_item_id FROM portfolio_work_items "
                           "WHERE item_type = 'story' ORDER BY id").fetchall()
    expected_keys = {f"{role_portfolio_seed.SOURCE}:story:{r['task_id']}:{r['goal_id']}:{r['project_id']}" for r in old}
    assert old and {r[0] for r in stories} == expected_keys and counts["stories_created"] == len(old)
    by_key = {r[0]: r for r in stories}
    for r in old:
        story = by_key[f"{role_portfolio_seed.SOURCE}:story:{r['task_id']}:{r['goal_id']}:{r['project_id']}"]
        assert story[1] == f"Story: {r['title']}" and story[2] == r["status"] and story[4] == r["epic_id"]
        assert story[3] == role_portfolio_seed._task_progress(r["status"], r["progress_percent"])
    assert conn.execute("SELECT COUNT(*) FROM work_item_task_links").fetchone()[0] == len(old)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"✅ {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"❌ {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)

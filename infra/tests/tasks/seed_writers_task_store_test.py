#!/usr/bin/env python3
"""Portfolio seed writers touch tasks only through the task store adapter.

Run: bash scripts/setup/run-python3.sh tests/tasks/seed_writers_task_store_test.py
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "docs/registries", "architecture-docs checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "db"))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import apply_legacy_portfolio_tables, sqlite_cell_opener  # noqa: E402  (isolates the cell APIs)
from sqlite_task_store import use_connection, uses_sqlite_store  # noqa: E402  (also isolates the test from the live Tasks API)

SCHEMA = (ROOT / 'infra' / 'tests' / 'support' / 'sqlite_tasks_schema.sql')

import autonomous_portfolio_seed  # noqa: E402
import portfolio_context_seed  # noqa: E402
import role_portfolio_seed  # noqa: E402



def cells(conn: sqlite3.Connection, *modules):
    """Run the modules against the goals / projects / roles-skills cells backed by ``conn``."""
    stack = contextlib.ExitStack()
    opener = sqlite_cell_opener(conn)
    for module in modules:
        stack.enter_context(patch.object(module, "open_cell_store", opener))
    return stack


def tasks_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    apply_legacy_portfolio_tables(conn)  # goals, projects and roles-skills tables behind the cell double
    use_connection(conn)  # production opens the Tasks store without a connection; the double uses this one
    return conn


def rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()


class FakeTaskStore:
    def __init__(self, listed: list[dict] | None = None) -> None:
        self.calls: list[tuple] = []
        self.listed = listed or []
        self.next_number = 40
        self.link_rows: list[dict] = []
        self.assignment_rows: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return list(self.listed)

    def create(self, fields, *, legacy=None):
        self.calls.append(("create", dict(fields), legacy))
        self.next_number += 1
        return self.next_number

    def upsert_external(self, source, external_id, fields, *, legacy=None):
        self.calls.append(("upsert_external", source, external_id, dict(fields), legacy))
        self.next_number += 1
        return "created", self.next_number

    def update(self, number, fields, *, legacy=None):
        self.calls.append(("update", number, dict(fields), legacy))

    def add_dependency(self, task, depends_on, dependency_type="finish_to_start"):
        self.calls.append(("add_dependency", task, depends_on))
        return "created"

    def links(self, *, task=None, target_type=None, target_id=None):
        self.calls.append(("links", task, target_type, target_id))
        return [row for row in self.link_rows
                if (task is None or row["task"] == task) and (target_type is None or row["target_type"] == target_type)
                and (target_id is None or row["target_id"] == target_id)]

    def link(self, task, target_type, target_id, attributes=None, *, update=False):
        self.calls.append(("link", task, target_type, target_id, dict(attributes or {}), update))
        return "created"

    def assignments(self, *, task=None, assignee_type=None, assignee_id=None, assignment_type=None):
        self.calls.append(("assignments", task, assignee_type, assignee_id, assignment_type))
        return [row for row in self.assignment_rows if assignee_type in (None, row["assignee_type"])
                and assignee_id in (None, row["assignee_id"])]

    def assign(self, task, assignee_type, assignee_id, assignee_name=None, assignment_type="assigned"):
        self.calls.append(("assign", task, assignee_type, assignee_id, assignee_name, assignment_type))
        return "created"


def writes(fake: FakeTaskStore) -> list[tuple]:
    return [call for call in fake.calls if call[0] in ("create", "upsert_external", "update")]


# ── autonomous_portfolio_seed ───────────────────────────────
@uses_sqlite_store(autonomous_portfolio_seed)
def test_autonomous_goal_projects_and_links_are_idempotent():
    conn = tasks_db()
    with cells(conn, autonomous_portfolio_seed):
        goal_ids = {autonomous_portfolio_seed.ensure_goal(False) for _ in range(2)}
        parent = None
        for _ in range(2):
            ids = {}
            for values in autonomous_portfolio_seed.PROJECTS:
                ids[values["slug"]] = autonomous_portfolio_seed.ensure_project(values, parent, False)
            parent = ids["autonomous-systems-portfolio"]
        for _ in range(2):
            autonomous_portfolio_seed.insert_link(
                "goal-project-links", ("goal_id", "project_id"),
                {"goal_id": goal_ids.copy().pop(), "project_id": parent, "relation_type": "delivery", "weight": 1,
                 "source": "s"}, False)
        assert autonomous_portfolio_seed.ensure_goal(True) == goal_ids.copy().pop()
    assert len(goal_ids) == 1
    assert conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == len(autonomous_portfolio_seed.PROJECTS)
    assert conn.execute("SELECT COUNT(*) FROM goal_project_links").fetchone()[0] == 1
    assert conn.execute("SELECT parent_project_id FROM projects WHERE slug='regulatory-knowledge-base'").fetchone()[0] == parent


def test_autonomous_project_slug_owned_elsewhere_is_refused():
    conn = tasks_db()
    conn.execute("INSERT INTO projects (slug, name, notes) VALUES ('regulatory-knowledge-base', 'Mine', 'hand written')")
    conn.commit()
    values = next(v for v in autonomous_portfolio_seed.PROJECTS if v["slug"] == "regulatory-knowledge-base")
    with cells(conn, autonomous_portfolio_seed):
        try:
            autonomous_portfolio_seed.ensure_project(values, None, False)
        except ValueError as exc:
            assert "outside roadmap ownership" in str(exc)
        else:
            raise AssertionError("expected ValueError")


@uses_sqlite_store(autonomous_portfolio_seed)
def test_autonomous_ensure_task_is_idempotent():
    conn = tasks_db()
    values = autonomous_portfolio_seed.TASKS[0]
    ids = [autonomous_portfolio_seed.ensure_task(values, 7, 8, "owner-1", "Owner", False) for _ in range(2)]
    found = rows(conn)
    assert ids[0] == ids[1] == found[0]["id"] and len(found) == 1
    task = found[0]
    assert task["sync_status"] == "local" and task["status"] == "todo" and task["created_at"] == task["updated_at"]
    assert f"task={values['key']}" in task["notes"] and json.loads(task["tags"])[0] == "autonomous-systems-roadmap"
    assert conn.execute("SELECT COUNT(*) FROM task_goal_links").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM task_assignments").fetchone()[0] == 1


def test_autonomous_ensure_task_uses_store_calls():
    conn = tasks_db()
    values = autonomous_portfolio_seed.TASKS[0]
    fake = FakeTaskStore()
    with patch.object(autonomous_portfolio_seed, "open_task_store", return_value=fake):
        task_id = autonomous_portfolio_seed.ensure_task(values, 7, 8, "o", "Owner", False)
    assert task_id == 41
    kind, fields, legacy = writes(fake)[0]
    assert kind == "create" and fields["title"] == values["title"] and fields["estimated_minutes"] == 180
    assert legacy["sync_status"] == "local" and legacy["created_at"] == legacy["updated_at"]
    source = autonomous_portfolio_seed.ROADMAP_SOURCE
    assert [call for call in fake.calls if call[0] in ("link", "assign")] == [
        ("link", 41, "goal", 7, {"link_type": "primary", "source": source}, False),
        ("link", 41, "project", 8, {"relation_type": "delivery", "weight": 1, "source": source}, False),
        ("assign", 41, "user", "o", "Owner", "assigned"),
    ]
    existing = {"id": 5, "title": values["title"], "notes": f"source={autonomous_portfolio_seed.ROADMAP_SOURCE}; task={values['key']}; x"}
    fake = FakeTaskStore([existing])
    with patch.object(autonomous_portfolio_seed, "open_task_store", return_value=fake):
        assert autonomous_portfolio_seed.ensure_task(values, 7, 8, "o", "Owner", False) == 5
    kind, number, fields, legacy = writes(fake)[0]
    assert (kind, number) == ("update", 5) and "title" not in fields and set(legacy) == {"updated_at"}


# ── role_portfolio_seed ─────────────────────────────────────
ROLE = {"id": "role-x", "name": "Role X"}


@uses_sqlite_store(role_portfolio_seed)
def test_role_task_is_idempotent_and_links_use_store_ids():
    conn = tasks_db()
    first = role_portfolio_seed.ensure_role_task(ROLE, 3, [], {})
    second = role_portfolio_seed.ensure_role_task(ROLE, 3, [], {})
    found = rows(conn)
    assert first[1] is True and second == (first[0], False) and len(found) == 1
    task = found[0]
    assert task["sync_status"] == "local" and task["priority"] == 4 and task["estimated_minutes"] == 90
    assert task["category"] == "learning" and task["notes"].endswith("role=role-x; goal=3")
    with cells(conn, role_portfolio_seed):
        assert role_portfolio_seed.ensure_active_task_goal_links("role-x", 3) == 0  # already linked
        conn.execute("DELETE FROM task_goal_links")
        assert role_portfolio_seed.ensure_active_task_goal_links("role-x", 3) == 1
        assert role_portfolio_seed.ensure_active_task_goal_links("role-x", 3) == 0
    conn.execute("INSERT INTO projects (id, slug, name) VALUES (1, 'solo-empire', 'Solo')")
    assert role_portfolio_seed.ensure_task_project_links({"solo-empire": 1}) == 1
    assert role_portfolio_seed.ensure_task_project_links({"solo-empire": 1}) == 0


def test_role_goal_task_project_and_allocation_are_idempotent():
    conn = tasks_db()
    role = {"id": "role-x", "name": "Role X", "summary": "Does X.", "category": "engineering"}
    with cells(conn, role_portfolio_seed):
        role_portfolio_seed._PROJECT_IDS.clear()
        project_id, created = role_portfolio_seed.ensure_project("solo-empire", "Solo Empire", "solo-empire")
        assert created and role_portfolio_seed.ensure_project("solo-empire", "Solo Empire", "solo-empire") == (
            project_id, False)
        goal_id, created = role_portfolio_seed.ensure_goal(role)
        assert created and role_portfolio_seed.ensure_goal(role) == (goal_id, False)
        task_id, created = role_portfolio_seed.ensure_goal_task(goal_id, role)
        assert created and role_portfolio_seed.ensure_goal_task(goal_id, role) == (task_id, False)
        assert role_portfolio_seed.ensure_project_allocation("role-x", goal_id) is True
        assert role_portfolio_seed.ensure_project_allocation("role-x", goal_id) is False
        epic = {"item_type": "epic", "title": "E", "description": "d", "goal_id": goal_id, "project_id": project_id,
                "parent_item_id": None, "status": "todo", "progress_percent": 0, "source_key": "k:epic"}
        epic_id, created = role_portfolio_seed.ensure_work_item(epic)
        assert created and role_portfolio_seed.ensure_work_item({**epic, "status": "done"}) == (epic_id, False)
    role_portfolio_seed._PROJECT_IDS.clear()
    assert conn.execute("SELECT COUNT(*), MIN(category) FROM goals").fetchone()[:] == (1, "tech")
    assert conn.execute("SELECT COUNT(*) FROM role_goal_links").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM goal_tasks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM role_project_allocations").fetchone()[0] == 1
    assert conn.execute("SELECT status FROM portfolio_work_items").fetchall()[0][0] == "done"


def test_role_seed_dry_run_writes_nothing_to_the_cells():
    conn = tasks_db()
    role_portfolio_seed._PROJECT_IDS.clear()
    role_portfolio_seed._DRY_RUN["on"] = True
    try:
        with cells(conn, role_portfolio_seed):
            goal_id, created = role_portfolio_seed.ensure_goal({"id": "r", "name": "R", "summary": "s"})
            assert created and goal_id < 0
            role_portfolio_seed.ensure_project("solo-empire", "Solo", "solo-empire")
    finally:
        role_portfolio_seed._DRY_RUN["on"] = False
        role_portfolio_seed._PROJECT_IDS.clear()
    for table in ("goals", "projects", "role_goal_links"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_role_task_uses_store_calls():
    conn = tasks_db()
    fake = FakeTaskStore()
    with patch.object(role_portfolio_seed, "open_task_store", return_value=fake):
        task_id, created = role_portfolio_seed.ensure_role_task(ROLE, 3, [], {})
    assert (task_id, created) == (41, True)
    kind, fields, legacy = writes(fake)[0]
    assert kind == "create" and fields["project"] == "solo-empire" and fields["status"] == "todo"
    assert legacy["sync_status"] == "local" and legacy["created_at"] == legacy["updated_at"]
    assert [call for call in fake.calls if call[0] in ("assign", "link")] == [
        ("assign", 41, "role", "role-x", "Role X", "assigned"),
        ("link", 41, "goal", 3, {"link_type": "primary", "source": role_portfolio_seed.SOURCE}, False),
    ]
    fake = FakeTaskStore([{"id": 9, "priority": 2, "project": "p"}, {"id": 10, "priority": 1, "project": "p"}])
    fake.assignment_rows = [{"task": 9, "assignee_type": "role", "assignee_id": "role-x"}]
    with patch.object(role_portfolio_seed, "open_task_store", return_value=fake):
        assert role_portfolio_seed.ensure_role_task(ROLE, 3, [], {}) == (9, False)
    assert fake.calls[0] == ("assignments", None, "role", "role-x", None)
    assert fake.calls[1] == ("list", {"status": ["todo", "in_progress", "blocked"], "include_archived": True})
    assert not writes(fake)


# ── portfolio_context_seed ──────────────────────────────────
REGISTRY = {"roles": [{"id": "writer", "name": "Writer", "contexts": ["ctx"]}]}


def context_db() -> sqlite3.Connection:
    conn = tasks_db()
    conn.execute("INSERT INTO projects (id, slug, name) VALUES (1, 'solo-empire', 'Solo')")
    conn.execute("INSERT INTO goals (id, title) VALUES (5, 'Goal')")
    conn.execute("INSERT INTO personal_roles (id, slug, name, category) VALUES (1, 'writer', 'Writer', 'creative')")
    conn.execute("INSERT INTO personal_role_goal_links (personal_role_id, goal_id, link_type, source)"
                 " VALUES (1, 5, 'primary', 's')")
    conn.execute("INSERT INTO goal_project_links (goal_id, project_id, relation_type, weight, source)"
                 " VALUES (5, 1, 'delivery', 1, 's')")
    conn.commit()
    return conn


def baselines(conn):
    with cells(conn, portfolio_context_seed):
        return portfolio_context_seed.ensure_personal_role_baselines(REGISTRY, {"writer": 1}, {"ctx": 1})


def test_context_and_personal_role_ensure_are_idempotent():
    conn = tasks_db()
    with cells(conn, portfolio_context_seed):
        ids = {portfolio_context_seed.ensure_context({"slug": "ctx", "name": "Ctx", "context_type": "personal"}) for _ in range(2)}
        parent = portfolio_context_seed.ensure_context({"slug": "p", "name": "P", "context_type": "personal"})
        portfolio_context_seed.ensure_context({"slug": "ctx", "name": "Renamed", "context_type": "personal"}, parent)
        role = {"id": "writer", "name": "Writer", "category": "creative", "summary": "s", "source_paths": ["a", "b"]}
        role_ids = {portfolio_context_seed.ensure_personal_role(role) for _ in range(2)}
        links = portfolio_context_seed.LinkIndex()
        created = [links.create("goal-context-links", {"goal_id": 1, "context_id": 2, "relation_type": "primary"})
                   for _ in range(2)]
        assert [portfolio_context_seed.LinkIndex().create(
            "goal-context-links", {"goal_id": 1, "context_id": 2}) for _ in range(1)] == [False]
        project_id = portfolio_context_seed.ensure_workspace_project({"slug": "book-x", "name": "X", "bu": "life", "path": "p"})
        assert portfolio_context_seed.ensure_workspace_project({"slug": "book-x"}) == project_id
    assert len(ids) == 1 and len(role_ids) == 1 and created == [True, False]
    assert conn.execute("SELECT name, parent_context_id, source FROM portfolio_contexts WHERE slug='ctx'"
                        ).fetchone()[:] == ("Renamed", parent, portfolio_context_seed.SOURCE)
    assert conn.execute("SELECT description, source_path FROM personal_roles").fetchone()[:] == ("s", "a; b")
    assert conn.execute("SELECT COUNT(*) FROM goal_context_links").fetchone()[0] == 1


def test_manual_context_is_not_overwritten():
    conn = tasks_db()
    conn.execute("INSERT INTO portfolio_contexts (slug, name, context_type, source) VALUES ('m', 'Mine', 'personal', 'manual')")
    conn.commit()
    with cells(conn, portfolio_context_seed):
        portfolio_context_seed.ensure_context({"slug": "m", "name": "Theirs", "context_type": "personal"})
    assert conn.execute("SELECT name FROM portfolio_contexts WHERE slug='m'").fetchone()[0] == "Mine"


@uses_sqlite_store(portfolio_context_seed)
def test_personal_role_baseline_task_is_idempotent():
    conn = context_db()
    for _ in range(2):
        baselines(conn)
    found = rows(conn)
    assert len(found) == 1
    task = found[0]
    assert task["title"] == "Personal role baseline: Writer" and task["sync_status"] == "local"
    assert task["project"] == "solo-empire" and task["priority"] == 4 and task["estimated_minutes"] == 60
    assert json.loads(task["tags"]) == ["personal-role-baseline", "personal-role:writer", "goal:5"]
    assert conn.execute("SELECT task_id FROM personal_role_task_links").fetchall()[0][0] == task["id"]
    assert conn.execute("SELECT COUNT(*) FROM task_goal_links").fetchone()[0] == 1
    # A task with the same title already linked to the goal is reused, even without a role link.
    conn.execute("DELETE FROM personal_role_task_links")
    baselines(conn)
    assert len(rows(conn)) == 1


def test_personal_role_baseline_uses_store_calls():
    conn = context_db()
    fake = FakeTaskStore()
    with patch.object(portfolio_context_seed, "open_task_store", return_value=fake):
        baselines(conn)
    assert ("list", {"include_archived": True}) in fake.calls
    assert ("links", None, "goal", 5) in fake.calls
    kind, fields, legacy = writes(fake)[0]
    assert kind == "create" and fields["category"] == "learning" and fields["estimated_minutes"] == 60
    assert ("link", 41, "goal", 5, {"link_type": "primary", "source": portfolio_context_seed.PERSONAL_ROLE_BASELINE_SOURCE},
            False) in fake.calls
    assert legacy["sync_status"] == "local" and legacy["created_at"] == legacy["updated_at"]
    assert conn.execute("SELECT task_id FROM personal_role_task_links").fetchone()[0] == 41


def test_context_task_reads_use_store():
    conn = context_db()
    fake = FakeTaskStore([{"id": 3, "project": "solo-empire", "category": "income"},
                          {"id": 4, "project": None, "category": None}])
    with patch.object(portfolio_context_seed, "open_task_store", return_value=fake), \
            cells(conn, portfolio_context_seed):
        assert portfolio_context_seed.ensure_all_task_project_links() == 1
    assert [call[1] for call in fake.calls if call[0] == "list"] == [{"include_archived": True}]
    assert [call for call in fake.calls if call[0] == "link"] == [
        ("link", 3, "project", 1, {"relation_type": "delivery", "weight": 1, "source": portfolio_context_seed.SOURCE}, False)
    ]



def test_dry_run_never_writes_to_the_tasks_api():
    """A --dry-run once wrote task links with placeholder ids; it must only read."""
    class Recorder:
        def __init__(self):
            self.calls = []

        def links(self, **kwargs):
            self.calls.append(("links", kwargs))
            return []

        def __getattr__(self, name):
            def write(*args, **kwargs):
                self.calls.append((name, args))
                raise AssertionError(f"dry-run wrote through {name}")
            return write

    recorder = Recorder()
    with patch.object(role_portfolio_seed, "open_task_store", lambda *_a, **_k: recorder), \
            patch.dict(role_portfolio_seed._DRY_RUN, {"on": True}):
        store = role_portfolio_seed._task_store()
        assert store.create({"title": "x"}) < 0
        assert store.link(5, "context", -8, {}) == "created"
        assert store.link(5, "context", 7, {}) == "created"
        store.assign(5, "role", "r", "R", "assigned")
        assert store.unlink(5, "context", 7) is False
    assert [name for name, _ in recorder.calls] == ["links"], recorder.calls

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

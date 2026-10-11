#!/usr/bin/env python3
"""Role routing, governance and evidence scripts read/write tasks only through the task store.

Run: bash scripts/setup/run-python3.sh tests/tasks/role_readers_task_store_test.py
"""

from __future__ import annotations

import contextlib
import io
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

from _env import skip_without_knowledge  # noqa: E402

skip_without_knowledge("role_readers_task_store_test.py")  # needs solo-knowledge; skipped in CI when absent

# The goals, projects and roles-skills tables left schema.sql (migration 084); fixtures still seed them.
LEGACY_PORTFOLIO = Path(__file__).resolve().parents[1] / "support" / "legacy_portfolio_tables.sql"
LEGACY_COLLABORATORS = Path(__file__).resolve().parents[1] / "support" / "legacy_collaborator_tables.sql"

import role_integration_sync as sync_mod  # noqa: E402
import role_practice_evidence  # noqa: E402
import role_task_guard as guard  # noqa: E402
import role_task_mapper as mapper  # noqa: E402
import role_task_workflow as workflow  # noqa: E402
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import sqlite_cell_opener  # noqa: E402
from sqlite_task_store import SqliteTaskStore, apply_legacy_task_tables  # noqa: E402

SOURCE = sync_mod.TASK_ROLE_SOURCE


class Spy:
    """Replacement for open_task_store: records the automation id, returns the SQLite store."""

    def __init__(self, conn=None) -> None:
        self.automations: list[str] = []
        self.conn = conn  # used when the caller passes no connection (tasks need none)

    def __call__(self, conn, automation, **_kw):
        self.automations.append(automation)
        return SqliteTaskStore(conn if conn is not None else self.conn)


def make_db(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or ":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(LEGACY_PORTFOLIO.read_text(encoding="utf-8"))
    apply_legacy_task_tables(conn)
    conn.executescript(LEGACY_COLLABORATORS.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = OFF")  # fixtures include orphan links on purpose
    return conn


def seed(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        INSERT INTO tasks (id, title, status, priority, project, tags, parent_task_id, archived_at) VALUES
            (1, 'Ship api backend api', 'todo', 1, 'book-api', '["api","backend"]', NULL, NULL),
            (2, 'Write docs', 'blocked', NULL, 'book-api', NULL, 1, NULL),
            (3, 'Old work', 'done', 2, NULL, NULL, NULL, NULL),
            (4, 'Archived but todo', 'todo', 2, NULL, NULL, NULL, '2026-01-01 00:00:00');
        INSERT INTO goals (id, title, status) VALUES (7, 'Goal seven', 'active'), (8, 'Goal eight', 'active');
        INSERT INTO projects (id, name, slug, status) VALUES (5, 'API', 'book-api', 'active'), (6, 'Other', 'other-proj', 'active');
        INSERT INTO task_goal_links (task_id, goal_id, link_type) VALUES
            (1, 8, 'supporting'), (1, 7, 'primary'), (2, 7, 'primary'), (3, 99, 'primary'), (50, 7, 'primary');
        INSERT INTO task_project_links (task_id, project_id, relation_type) VALUES
            (1, 5, 'delivery'), (1, 6, 'supporting'), (51, 5, 'delivery');
        INSERT INTO collaborators (id, name, role, is_active) VALUES (1, 'Owner Person', 'owner', 1);
        INSERT INTO task_assignments (task_id, assignee_type, assignee_id, assignee_name, assignment_type) VALUES
            (1, 'role', 'backend-engineer', 'Backend', 'assigned'),
            (2, 'role', 'ghost-role', NULL, 'assigned'),
            (3, 'role', 'backend-engineer', 'Backend', 'assigned'),
            (1, 'user', '1', 'Owner Person', 'assigned');
        INSERT INTO task_role_suggestions (task_id, role_id, confidence, match_reason, review_status, source_registry) VALUES
            (1, 'backend-engineer', 'high', 'r1', 'accepted', 'docs/ROLE-TASK-ROUTING.yaml'),
            (2, 'technical-writer', 'medium', 'r2', 'suggested', 'docs/ROLE-TASK-ROUTING.yaml'),
            (2, 'backend-engineer', 'high', 'r3', 'suggested', 'docs/ROLE-TASK-ROUTING.yaml'),
            (3, 'qa-test-engineer', 'low', 'r4', 'suggested', 'docs/ROLE-TASK-ROUTING.yaml'),
            (2, 'x-role', 'low', 'r5', 'suggested', 'other.yaml');
    """)
    conn.commit()


def rows(conn, sql, *params):
    return [tuple(r) for r in conn.execute(sql, params).fetchall()]


# ---- role_task_workflow ------------------------------------------------

def test_workflow_load_state_matches_legacy_sql_result():
    conn = make_db()
    seed(conn)
    spy = Spy(conn)
    with patch.object(workflow, "open_task_store", spy), patch.object(workflow, "open_cell_store", sqlite_cell_opener(conn)):
        state = workflow.load_state()
    assert spy.automations == ["role-router"]
    # active tasks incl. archived one, ordered by COALESCE(priority, 3), id
    assert list(state["tasks"]) == [1, 4, 2]
    assert state["tasks"][2]["parent_status"] == "todo" and state["tasks"][2]["parent_title"] == "Ship api backend api"
    assert state["tasks"][1]["parent_status"] is None
    assert dict(state["assignments"]) == {1: ["backend-engineer"], 2: ["ghost-role"], 3: ["backend-engineer"]}
    assert state["assignment_names"][(2, "ghost-role")] == "ghost-role"
    assert state["assignment_names"][(1, "backend-engineer")] == "Backend"
    assert dict(state["user_owners"]) == {1: ["Owner Person"]}
    assert [r["role_id"] for r in state["suggestions"][2]] == ["backend-engineer", "technical-writer"]
    assert state["suggestions"][2][0]["task_id"] == 2 and state["suggestions"][2][0]["review_status"] == "suggested"
    assert 50 not in state["goals"] and 3 not in state["goals"] or state["goals"].get(3) is None
    # goal links order: link_type then goal id => primary(7) before supporting(8)
    assert state["goals"][1] == ["#7 Goal seven", "#8 Goal eight"]
    assert state["projects"][1] == ["book-api", "other-proj"]  # delivery before supporting


def test_workflow_decisions_and_claims_go_through_store():
    conn = make_db()
    seed(conn)
    spy = Spy(conn)
    roles = {"backend-engineer": {"name": "Backend Engineer"}, "technical-writer": {"name": "TW"}}
    with patch.object(workflow, "open_task_store", spy):
        store = workflow.open_task_store(conn, workflow.AUTOMATION)
    assert workflow.apply_decisions(store, roles, [(2, "technical-writer")], [(2, "backend-engineer")], [], False) == 2
    status = dict(rows(conn, "SELECT role_id, review_status FROM task_role_suggestions WHERE task_id = 2 AND source_registry = ?", SOURCE))
    assert status == {"technical-writer": "accepted", "backend-engineer": "dismissed"}
    # explicit assignment: accepts the suggestion and inserts the role owner
    conn.execute("DELETE FROM task_assignments WHERE task_id = 2")
    assert workflow.apply_decisions(store, roles, [], [], [(2, "technical-writer")], False) == 1
    assert rows(conn, "SELECT assignee_type, assignee_id, assignee_name, assignment_type FROM task_assignments WHERE task_id = 2") == [
        ("role", "technical-writer", "TW", "assigned")]
    # replacement refused
    try:
        workflow.apply_decisions(store, roles, [], [], [(2, "backend-engineer")], False)
    except ValueError as exc:
        assert "already has role owner" in str(exc)
    else:
        raise AssertionError("replacement must be refused")
    try:
        workflow.apply_decisions(store, roles, [(1, "technical-writer")], [], [], False)
    except ValueError as exc:
        assert "no routing suggestion" in str(exc)
    else:
        raise AssertionError("missing suggestion must raise")
    # dry-run writes nothing
    assert workflow.apply_decisions(store, roles, [(2, "backend-engineer")], [], [], True) == 1
    assert rows(conn, "SELECT review_status FROM task_role_suggestions WHERE task_id=2 AND role_id='backend-engineer'") == [("dismissed",)]
    # claim-all: every task (archived too) without the canonical owner assignment
    owner = {"id": "1", "name": "Owner Person"}
    assert workflow.claim_all_tasks_for_owner(store, owner, True) == 3
    assert workflow.claim_all_tasks_for_owner(store, owner, False) == 3
    assert workflow.claim_all_tasks_for_owner(store, owner, False) == 0
    assert rows(conn, "SELECT count(*) FROM task_assignments WHERE assignee_type='user' AND assignee_id='1'") == [(4,)]


# ---- role_task_mapper --------------------------------------------------

def test_mapper_counts_candidates_and_acceptance():
    conn = make_db()
    seed(conn)
    store = SqliteTaskStore(conn)
    c = mapper.query_counts(store)
    assert c == {
        "tasks": 4, "active_tasks": 3, "assigned_tasks": 3, "accepted_tasks": 1, "suggested_tasks": 2,
        "suggested_rows": 3, "accepted_rows": 1, "unmapped_tasks": 1, "high_review_tasks": 1,
        "medium_review_tasks": 1, "low_review_tasks": 1,
    }, c
    routing = {"qa-test-engineer": {"role_id": "qa-test-engineer", "terms": ["archived", "todo"]}}
    assert mapper.candidate_mappings(store, routing) == [(4, "qa-test-engineer")]
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.md"
        with patch.object(mapper, "REPORT_PATH", report):
            mapper.write_report(store, 2, 3)
        text = report.read_text(encoding="utf-8")
    assert "| Total tasks | 4 |" in text and "| backend-engineer | 1 |" in text
    lines = [l for l in text.splitlines() if l.startswith("| #")]
    assert [l.split(" | ")[2] for l in lines] == ["backend-engineer", "technical-writer", "qa-test-engineer"], lines
    # run() accepts through review_role with expected_status=suggested
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        disk = make_db(str(db_path))
        seed(disk)
        disk.commit()
        disk.close()
        spy = Spy(sqlite3.connect(db_path))
        with patch.object(mapper, "open_task_store", spy), \
                patch.object(mapper, "REPORT_PATH", Path(tmp) / "r.md"), \
                patch.object(mapper, "candidate_mappings", return_value=[(2, "backend-engineer"), (1, "backend-engineer")]), \
                patch.object(mapper, "sync_role_integrations"), patch.object(mapper, "PROJECT_ROOT", Path(tmp)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            assert mapper.run(False) == 0
        spy.conn.commit()  # the double writes through its connection
        check = sqlite3.connect(db_path)
        assert check.execute("SELECT review_status FROM task_role_suggestions WHERE task_id=2 AND role_id='backend-engineer'").fetchone()[0] == "accepted"
        check.close()
    assert spy.automations == ["role-router"]
    assert "accepted mappings: 1" in out.getvalue()


# ---- role_integration_sync --------------------------------------------

def test_sync_upserts_through_store_and_preserves_review_state():
    _catalog, learning, routing, _paths, _by_id, learning_by_id = sync_mod.validate_registries()
    rule = next(r for r in routing["rules"] if r.get("terms"))
    title = " ".join(rule["terms"][:2])
    expected = sync_mod.matching_roles(
        {"title": title, "description": None, "category": None, "project": None, "tags": []}, {rule["role_id"]: rule}
    )[rule["role_id"]][0]
    conn = make_db()
    cells = sqlite_cell_opener(conn)
    slugs = {s for row in learning_by_id.values() for s in row["skill_slugs"]}
    for slug in sorted(slugs):
        cells("roles-skills").create("skills", {"name": slug, "slug": slug})
    conn.execute("INSERT INTO tasks (id, title, status) VALUES (1, ?, 'todo')", (title,))
    conn.execute("INSERT INTO tasks (id, title, status) VALUES (2, 'zzz', 'todo')")
    sid = cells("roles-skills").list("skills")[0]["id"]
    # pre-existing reviewed rows that the old ON CONFLICT clauses kept
    conn.execute("INSERT INTO task_role_suggestions (task_id, role_id, confidence, match_reason, review_status, source_registry)"
                 " VALUES (1, ?, 'low', 'old', 'accepted', ?)", (rule["role_id"], SOURCE))
    conn.execute("INSERT INTO task_role_suggestions (task_id, role_id, confidence, match_reason, review_status, source_registry)"
                 " VALUES (2, 'stale-role', 'low', 'old', 'suggested', ?)", (SOURCE,))
    conn.execute("INSERT INTO task_role_suggestions (task_id, role_id, confidence, match_reason, review_status, source_registry)"
                 " VALUES (2, 'kept-role', 'low', 'old', 'accepted', ?)", (SOURCE,))
    conn.execute("INSERT INTO task_skill_links (task_id, skill_id, link_type, match_reason, source_registry)"
                 " VALUES (2, ?, 'suggested', 'stale', ?)", (sid, sync_mod.TASK_SKILL_SOURCE))
    conn.execute("INSERT INTO task_skill_links (task_id, skill_id, link_type, match_reason, source_registry)"
                 " VALUES (1, ?, 'accepted', 'stale', ?)", (sid, sync_mod.TASK_SKILL_SOURCE))
    conn.commit()
    spy = Spy(conn)
    with patch.object(sync_mod, "open_cell_store", cells), patch.object(sync_mod, "open_task_store", spy), \
            contextlib.redirect_stdout(io.StringIO()):
        assert sync_mod.sync(__import__("argparse").Namespace(include_history=False, dry_run=False)) == 0
    assert spy.automations == ["role-router"]
    check = conn
    sug = {(r[0], r[1]): r[2:] for r in check.execute(
        "SELECT task_id, role_id, confidence, review_status FROM task_role_suggestions WHERE source_registry = ?", (SOURCE,))}
    assert sug[(1, rule["role_id"])][1] == "accepted"          # review_status kept
    assert sug[(1, rule["role_id"])][0] == expected            # confidence refreshed
    assert (2, "stale-role") not in sug                        # stale suggested row removed
    assert sug[(2, "kept-role")][1] == "accepted"              # reviewed row kept
    links = {(r[0], r[1]): (r[2], r[3]) for r in check.execute(
        "SELECT task_id, skill_id, link_type, match_reason FROM task_skill_links")}
    assert (2, sid) not in links                               # stale suggested link removed
    if (1, sid) in links:
        assert links[(1, sid)][0] == "accepted"                # link_type not overwritten
    steps = cells("roles-skills").list("role-path-steps")
    links_in_cell = cells("roles-skills").list("role-skill-links")
    assert steps and links_in_cell                              # projections written to the cell
    assert all(r["source_registry"] == sync_mod.ROLE_PATH_SOURCE for r in steps)
    # idempotent: a second run leaves the same rows
    with patch.object(sync_mod, "open_cell_store", cells), patch.object(sync_mod, "open_task_store", spy), \
            contextlib.redirect_stdout(io.StringIO()):
        assert sync_mod.sync(__import__("argparse").Namespace(include_history=False, dry_run=False)) == 0
    assert [r["id"] for r in cells("roles-skills").list("role-path-steps")] == [r["id"] for r in steps]
    assert [r["id"] for r in cells("roles-skills").list("role-skill-links")] == [r["id"] for r in links_in_cell]


# ---- role_task_guard ---------------------------------------------------

def test_guard_reads_through_store_and_reports_findings():
    conn_path = tempfile.mkdtemp()
    db_path = Path(conn_path) / "t.db"
    conn = make_db(str(db_path))
    seed(conn)
    conn.execute("INSERT INTO task_skill_links (task_id, skill_id, match_reason) VALUES (1, 12345, 'x')")
    conn.commit()
    conn.close()
    out = io.StringIO()
    cell_conn = sqlite3.connect(db_path)
    spy = Spy(cell_conn)
    with patch.object(guard, "open_task_store", spy), \
            patch.object(guard, "open_cell_store", sqlite_cell_opener(cell_conn)), \
            patch.object(sys, "argv", ["role_task_guard.py", "--live"]), contextlib.redirect_stdout(out):
        code = guard.main()
    cell_conn.close()
    text = out.getvalue()
    assert code == 1 and spy.automations == ["governance-reader"]
    assert "unknown atomic role assignment(s): ghost-role" in text
    assert "unknown role suggestion(s): " in text and "x-role" in text
    assert "unknown task learning skill link(s): 12345" in text
    assert "invalid task-goal link rows: 2" in text          # (3, goal 99): goal missing; (task 50, goal 7): task missing
    assert "invalid portfolio hierarchy link rows: 1" in text  # task_project_links for missing task 51
    assert "active role task(s) without a project link: 2" in text
    assert "active role task(s) without an Epic/Story link: 1, 2" in text
    assert "roles without an active task: " in text and "without a goal link" not in text


def test_guard_reports_use_store():
    _catalog, learning, _routing, paths, catalog_by_id, learning_by_id = guard.validate_registries()
    role_id = next(iter(catalog_by_id))
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        conn = make_db(str(db_path))
        conn.executescript(f"""
            INSERT INTO tasks (id, title, status, priority, project) VALUES
                (1, 'A', 'todo', 2, 'p1'), (2, 'B', 'todo', NULL, NULL), (3, 'C', 'done', 1, NULL);
            INSERT INTO task_assignments (task_id, assignee_type, assignee_id, assignment_type) VALUES
                (1, 'role', '{role_id}', 'assigned'), (2, 'role', '{role_id}', 'assigned'), (3, 'role', '{role_id}', 'assigned');
            INSERT INTO task_role_suggestions (task_id, role_id, confidence, match_reason, review_status, source_registry) VALUES
                (1, '{role_id}', 'high', 'r', 'suggested', '{SOURCE}'), (3, '{role_id}', 'high', 'r', 'suggested', '{SOURCE}'),
                (2, '{role_id}', 'high', 'r', 'accepted', '{SOURCE}');
        """)
        conn.commit()
        conn.close()
        cell_conn = sqlite3.connect(db_path)
        spy = Spy(cell_conn)
        reports = {name: Path(tmp) / f"{name}.md" for name in ("REPORT_PATH", "PATH_REPORT_PATH", "PORTFOLIO_REPORT_PATH")}
        with patch.object(guard, "open_task_store", spy), \
                patch.object(guard, "open_cell_store", sqlite_cell_opener(cell_conn)), \
                patch.multiple(guard, **reports):
            guard._write_report(catalog_by_id, learning_by_id, paths)
        cell_conn.close()
        main_report = reports["REPORT_PATH"].read_text(encoding="utf-8")
        row = next(l for l in main_report.splitlines() if l.startswith(f"| `{role_id}`"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[-3:] == ["1", "1", "3"], cells          # active suggestions, accepted mappings, assigned tasks
        portfolio = reports["PORTFOLIO_REPORT_PATH"].read_text(encoding="utf-8")
        assert "#2 B (todo, no-project)<br>#1 A (todo, p1)" in portfolio  # NULL priority sorts first
        assert spy.automations == ["governance-reader"] * 3


# ---- role_practice_evidence -------------------------------------------

def test_practice_evidence_select_task_uses_store_assignments():
    conn = make_db()
    seed(conn)
    spy = Spy()
    store = spy(conn, "role-evidence")
    assert role_practice_evidence.select_task(store, "backend-engineer", None)["id"] == 1
    assert role_practice_evidence.select_task(store, "backend-engineer", 3)["id"] == 3
    try:
        role_practice_evidence.select_task(store, "backend-engineer", 2)
    except ValueError as exc:
        assert "not assigned" in str(exc)
    else:
        raise AssertionError("unassigned task must be rejected")
    calls = []
    real = store.assignments
    store.assignments = lambda **kw: (calls.append(kw), real(**kw))[1]
    role_practice_evidence.select_task(store, "backend-engineer", None)
    assert calls and calls[0]["assignee_type"] == "role"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("Role readers task store tests passed")

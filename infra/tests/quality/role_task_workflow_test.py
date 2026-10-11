"""Offline tests for the role review and multi-goal queue workflow."""

from pathlib import Path
import sys
from copy import deepcopy


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from _env import PROJECT_ROOT  # noqa: E402

from role_task_workflow import (  # noqa: E402
    parse_role_pairs,
    queue_rows,
    readiness,
    review_candidates,
)


def state_fixture():
    return {
        "tasks": {
            1: {
                "id": 1,
                "title": "Ship the API",
                "status": "todo",
                "priority": 1,
                "project": "book-api",
                "parent_task_id": None,
                "parent_status": None,
            },
            2: {
                "id": 2,
                "title": "Write tests",
                "status": "blocked",
                "priority": 2,
                "project": "book-api",
                "parent_task_id": 1,
                "parent_status": "todo",
            },
            3: {
                "id": 3,
                "title": "Research",
                "status": "todo",
                "priority": 3,
                "project": "book-research",
                "parent_task_id": None,
                "parent_status": None,
            },
        },
        "assignments": {1: ["backend-engineer"], 2: [], 3: []},
        "assignment_names": {},
        "user_owners": {1: ["Owner"], 2: ["Owner"], 3: ["Owner"]},
        "suggestions": {
            1: [{"role_id": "backend-engineer", "confidence": "high", "review_status": "accepted"}],
            2: [{"role_id": "qa-test-engineer", "confidence": "high", "review_status": "accepted"}],
            3: [
                {"role_id": "product-market-researcher", "confidence": "high", "review_status": "suggested"},
                {"role_id": "data-analyst-bi", "confidence": "high", "review_status": "suggested"},
            ],
        },
        "goals": {1: ["#1 API goal"], 3: ["#2 Research goal"]},
        "projects": {1: ["book-api"], 2: ["book-api"], 3: ["book-research"]},
    }


def test_parse_role_pairs():
    assert parse_role_pairs(["12:devsecops-engineer"], "--assign") == [(12, "devsecops-engineer")]


def test_readiness_respects_blocked_and_parent_dependencies():
    state = state_fixture()
    assert readiness(state["tasks"][1]) == "ready"
    assert readiness(state["tasks"][2]) == "blocked"


def test_review_candidates_only_returns_unresolved_active_role_choices():
    candidates = review_candidates(state_fixture())
    assert [row["task"]["id"] for row in candidates] == [3]
    assert candidates[0]["high"] == ["product-market-researcher", "data-analyst-bi"]


def test_queue_prioritizes_confirmed_ready_work_and_keeps_blocked_work_visible():
    rows = queue_rows(state_fixture())
    assert rows[0]["task"]["id"] == 1
    assert {row["task"]["id"] for row in rows[1:]} == {2, 3}
    assert rows[0]["owner_state"] == "assigned"
    assert rows[0]["readiness"] == "ready"
    blocked = next(row for row in rows if row["task"]["id"] == 2)
    unrouted = next(row for row in rows if row["task"]["id"] == 3)
    assert blocked["readiness"] == "blocked"
    assert unrouted["owner_state"] == "role-routing-needed"


def test_priority_cannot_be_overridden_by_role_assignment_bonus():
    state = deepcopy(state_fixture())
    state["tasks"][4] = {
        "id": 4,
        "title": "Low-priority role baseline",
        "status": "todo",
        "priority": 4,
        "project": "solo-empire",
        "parent_task_id": None,
        "parent_status": None,
    }
    state["assignments"][4] = ["technical-writer"]
    state["suggestions"][4] = []
    state["user_owners"][4] = ["Owner"]
    state["projects"][4] = ["solo-empire"]

    state["assignments"][1] = []
    state["suggestions"][1] = [
        {"role_id": "backend-engineer", "confidence": "high", "review_status": "suggested"}
    ]

    rows = queue_rows(state)
    assert rows[0]["task"]["id"] == 1
    assert next(row for row in rows if row["task"]["id"] == 4)["rank"] > 1


if __name__ == "__main__":
    for name in dir():
        if name.startswith("test_"):
            globals()[name]()
    print("Role task workflow tests passed")

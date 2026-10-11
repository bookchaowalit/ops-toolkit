#!/usr/bin/env python3
"""Offline tests for the reversible goal portfolio mapping rules."""

import importlib.util
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "scripts" / "utils" / "goal_portfolio_mapping.py"
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))
from sqlite_cell_store import SqliteCellStore  # noqa: E402  (also isolates the cell API client switches)

spec = importlib.util.spec_from_file_location("goal_portfolio_mapping", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def goal(goal_id: int, title: str, *, status: str = "active", category: str = "business") -> dict:
    return {
        "id": goal_id,
        "title": title,
        "description": None,
        "type": "project",
        "category": category,
        "status": status,
        "priority": 1,
        "progress_percent": 0,
        "due_date": None,
    }


def test_mapping_rules() -> None:
    rows = [
        goal(1, "Global Income: พิสูจน์รายได้ $1 จากนอกประเทศไทย"),
        goal(2, "สร้าง AI product/service แรก"),
        goal(3, "สร้าง audience อย่างน้อย 1 ช่องทาง"),
        goal(4, "Deep dive Infra/DevOps", category="tech"),
        goal(5, "Blood work + VO2 max check", category="health"),
        goal(6, "Future curiosity", status="planning", category="life"),
        goal(7, "Build operating capability: Project Manager / Delivery Manager", status="planning"),
        goal(8, "Complete Ironman Distance Triathlon", status="paused", category="health"),
        goal(9, "Deep dive ด้าน AI/ML", category="tech"),
    ]
    mappings = module.build_mappings(rows, "2026-Q3")
    by_id = {item["goal_id"]: item for item in mappings}

    assert by_id[1]["portfolio_bucket"] == "active_bet"
    assert by_id[1]["attention_class"] == "focus"
    assert by_id[2]["canonical_goal"] == "ai-service-market-loop"
    assert by_id[2]["attention_class"] == "parallel"
    assert by_id[3]["canonical_goal"] == "ai-service-market-loop"
    assert by_id[4]["portfolio_bucket"] == "capability_backlog"
    assert by_id[5]["portfolio_bucket"] == "infrastructure"
    assert by_id[6]["attention_class"] == "option"
    assert by_id[7]["portfolio_bucket"] == "large_scale_builder"
    assert by_id[7]["attention_class"] == "background"
    assert by_id[8]["portfolio_bucket"] == "someday_curiosity"
    assert by_id[8]["attention_class"] == "option"
    assert by_id[9]["canonical_goal"] == "ai-ml-depth"


def test_duplicate_rows_share_one_canonical_representative() -> None:
    rows = [goal(20, "สร้าง AI product/service แรก"), goal(21, "สร้าง AI product/service แรก")]
    mappings = module.build_mappings(rows, "2026-Q3")
    assert {item["canonical_goal"] for item in mappings} == {"ai-service-market-loop"}
    assert {item["canonical_goal_id"] for item in mappings} == {20}


def test_summary_counts_families_not_duplicate_rows() -> None:
    rows = [
        goal(30, "ระบุ 3 AI arbitrage niches"),
        goal(31, "ระบุ 3 AI arbitrage niches"),
        goal(32, "Global Income: พิสูจน์รายได้ $1 จากนอกประเทศไทย"),
    ]
    payload = module.summary(module.build_mappings(rows, "2026-Q3"))
    assert payload["mapped_rows"] == 3
    assert payload["canonical_families"] == 2
    assert payload["focus_families"] == 1


def test_report_records_current_season_decision() -> None:
    rows = [
        goal(40, "Global Income: พิสูจน์รายได้ $1 จากนอกประเทศไทย"),
        goal(41, "Build operating capability: Project Manager / Delivery Manager", status="planning"),
        goal(42, "Deep dive ด้าน AI/ML", category="tech"),
    ]
    report = module.render_report(module.build_mappings(rows, "2026-Q3"), "2026-Q3")
    assert "## Current-season decision" in report
    assert "ไม่มีการเลื่อน goal ใหม่เข้า Focus" in report
    assert "Large-Scale Builder Track เป็น Background" in report


def goals_store() -> "SqliteCellStore":
    conn = sqlite3.connect(":memory:")
    store = SqliteCellStore(conn, "goals")
    conn.executescript(
        """
        INSERT INTO goals (id, title, status, category, type, priority)
            VALUES (1, 'Global Income: x', 'active', 'business', 'project', 1),
                   (2, 'สร้าง AI product/service แรก', 'active', 'business', 'project', 1),
                   (3, 'สร้าง AI product/service แรก', 'completed', 'business', 'project', 1),
                   (4, 'Future curiosity', 'planning', 'life', 'project', 3);
        """
    )
    return store


def test_apply_mappings_is_an_idempotent_upsert_and_views_are_python() -> None:
    store = goals_store()
    mappings = module.build_mappings(module.load_goals(store), "2026-Q3")
    module.apply_mappings(store, mappings)
    module.apply_mappings(store, mappings)  # second run: no duplicates, no rewrite
    rows = store.list("goal-portfolio-mappings")
    assert [row["goal_id"] for row in rows] == [1, 2, 3, 4]
    assert {row["canonical_goal_id"] for row in rows if row["canonical_goal"] == "ai-service-market-loop"} == {2}

    goals = module.load_goals(store)
    current = module.current_portfolio(goals, rows)
    assert [item["goal_id"] for item in current] == [1, 2, 3, 4]
    assert current[0]["portfolio_bucket"] == "active_bet" and current[0]["status"] == "active"
    assert module.current_portfolio(goals, rows + [{**rows[0], "goal_id": 99}]) == current  # inner join on goals

    summary = module.attention_collision_summary(goals, rows)
    by_key = {(r["attention_class"], r["collision_group"]): r for r in summary}
    # goal 3 (completed) is excluded; one family per distinct canonical goal
    assert by_key[("focus", "cash-global")] == {
        "current_season": "2026-Q3", "attention_class": "focus", "collision_group": "cash-global",
        "canonical_goal_count": 1, "attention_cost": 5,
    }
    assert by_key[("parallel", "market-offer")]["canonical_goal_count"] == 1
    assert by_key[("option", "planned")]["attention_cost"] == 0


if __name__ == "__main__":
    test_apply_mappings_is_an_idempotent_upsert_and_views_are_python()
    test_mapping_rules()
    test_duplicate_rows_share_one_canonical_representative()
    test_summary_counts_families_not_duplicate_rows()
    test_report_records_current_season_decision()
    print("✅ goal portfolio mapping tests passed")

#!/usr/bin/env python3
"""Idempotently project the approved autonomous-systems roadmap into the goals, projects and tasks stores.

Only rows carrying ROADMAP_SOURCE are created or updated. Existing unrelated
goals, projects, tasks, and links are never deleted or rewritten.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from cell_store import CellStoreError, open_cell_store  # noqa: E402
from task_owner import canonical_owner  # noqa: E402
from task_store import ClientConfigError, open_task_store  # noqa: E402


ROADMAP_SOURCE = "roadmap:autonomous-systems-2026-2029"
ROADMAP_PATH = "bookchaowalit/solo-knowledge:plans/decision-context/AUTONOMOUS-SYSTEMS-PORTFOLIO-ROADMAP-2026-2029.md"
TODAY = "2026-08-28"
AUTOMATION_ID = "portfolio-seeder"


PROJECTS = [
    {
        "slug": "autonomous-systems-portfolio",
        "name": "Autonomous Systems Portfolio",
        "category": "portfolio",
        "type": "portfolio-program",
        "status": "active",
        "priority": 1,
        "phase": "execution-foundation",
        "description": "One capability universe from trusted agents to simulation and supervised physical AI.",
        "revenue_model": "service-to-pilot-to-product",
        "tech_stack": ["TypeScript", "Python", "SQLite", "Parquet", "DuckDB", "OpenTelemetry"],
        "docs_url": ROADMAP_PATH,
    },
    {
        "slug": "regulatory-knowledge-base",
        "name": "Regulatory Knowledge Base",
        "category": "portfolio",
        "type": "research-product",
        "status": "active",
        "priority": 1,
        "phase": "human-review-and-market-signal",
        "description": "Cash and proof anchor for AI trust, regulatory research, provenance, and evaluation.",
        "revenue_model": "research-review-or-bounded-pilot",
        "tech_stack": ["Python", "RAG", "Evaluation", "Security", "Governance"],
        "docs_url": "projects/product/rnd/book-ai/applications/rag-demo/regulatory-knowledge-base-mini-project.md",
    },
    {
        "slug": "personal-agent-runtime",
        "name": "Personal Agent Runtime",
        "category": "portfolio",
        "type": "agent-runtime",
        "status": "active",
        "priority": 1,
        "phase": "offline-mvp",
        "description": "Local-first resumable agent workflow with explicit permissions, approval, memory, and audit.",
        "revenue_model": "setup-evaluation-review-and-private-pilot",
        "tech_stack": ["Python", "State Machine", "FastAPI Adapter", "Pydantic Schemas", "LangGraph Adapter", "Temporal Adapter", "SQLite", "JSON Event Log", "Playwright Adapter"],
        "docs_url": "projects/product/rnd/book-ai/applications/agent-runtime/README.md",
    },
    {
        "slug": "ai-society-simulator",
        "name": "AI Society Simulator",
        "category": "portfolio",
        "type": "agent-based-simulation",
        "status": "planning",
        "priority": 2,
        "phase": "gate-brief",
        "description": "Reproducible multi-agent society simulation for economics, policy, and evaluation scenarios.",
        "revenue_model": "scenario-and-evaluation-lab",
        "tech_stack": ["Python", "Mesa Baseline", "DuckDB", "Parquet", "Seeded Runs"],
        "docs_url": "projects/product/rnd/book-ai/applications/agent-society-simulator/README.md",
    },
    {
        "slug": "autonomous-home-digital-twin",
        "name": "Autonomous Home Digital Twin",
        "category": "portfolio",
        "type": "edge-iot-lab",
        "status": "planning",
        "priority": 3,
        "phase": "gate-brief",
        "description": "Local-first home world model with simulated sensors, safe recommendations, and actuator gates.",
        "revenue_model": "privacy-first-energy-diagnostic-or-integration-pilot",
        "tech_stack": ["Python", "Paho MQTT Adapter", "Mosquitto", "Home Assistant Adapter", "Edge Events"],
        "docs_url": "projects/product/rnd/book-ai/applications/autonomous-home/README.md",
    },
    {
        "slug": "robot-skill-runtime",
        "name": "Robot Skill Runtime",
        "category": "portfolio",
        "type": "embodied-ai-runtime",
        "status": "planning",
        "priority": 3,
        "phase": "gate-brief",
        "description": "Simulator-first constrained task runtime for inspectable robot plans and safe handoff.",
        "revenue_model": "simulation-and-evaluation-integration",
        "tech_stack": ["Python", "ROS 2 Candidate", "Webots Candidate", "Planning", "Safety Review"],
        "docs_url": "projects/product/rnd/book-ai/applications/robot-skill-runtime/README.md",
    },
]


TASKS = [
    {
        "key": "p0-human-review",
        "title": "Regulatory KB — complete human review packet",
        "description": "Use the existing review guide to review all gold cases and source authorization decisions; agent must not approve on behalf of a human.",
        "status": "todo",
        "priority": 1,
        "project": "regulatory-knowledge-base",
        "category": "learning",
        "due_date": "2026-09-10",
        "estimated_minutes": 180,
        "evidence": "projects/product/rnd/book-ai/evaluation/regulatory_kb_reviewer_packet-2026-08-28.md",
    },
    {
        "key": "p0-market-feedback",
        "title": "Regulatory KB — test one-page offer with target user",
        "description": "Turn the existing evidence package into one bounded offer and record user, reviewer, objection, or paid-pilot feedback.",
        "status": "todo",
        "priority": 1,
        "project": "regulatory-knowledge-base",
        "category": "income",
        "due_date": "2026-09-10",
        "estimated_minutes": 120,
        "evidence": "bookchaowalit/solo-catalog:docs/business/flagship-ai-trust-case-study.md",
    },
    {
        "key": "p1-offline-mvp",
        "title": "Personal Agent Runtime — implement offline MVP",
        "description": "Implement the local state machine, explicit permission gate, approval pause, restart/resume, memory, idempotency, audit events, tests, and evidence report.",
        "status": "in_progress",
        "priority": 1,
        "project": "personal-agent-runtime",
        "category": "tech",
        "due_date": "2026-09-05",
        "estimated_minutes": 360,
        "evidence": "projects/product/rnd/book-ai/applications/agent-runtime/README.md",
    },
    {
        "key": "p1-human-review",
        "title": "Personal Agent Runtime — review permissions and threat model",
        "description": "Human review the permission matrix, restart evidence, idempotency behavior, and threat model before real-tool or provider integration.",
        "status": "todo",
        "priority": 1,
        "project": "personal-agent-runtime",
        "category": "tech",
        "due_date": "2026-09-11",
        "estimated_minutes": 90,
        "evidence": "projects/product/rnd/book-ai/applications/agent-runtime/threat-model.md",
    },
    {
        "key": "p2-metric-gate",
        "title": "AI Society Simulator — approve question and metric design",
        "description": "Approve one bounded society question, a rule-based baseline, three metrics, uncertainty treatment, and a 20-seed evaluation plan.",
        "status": "todo",
        "priority": 2,
        "project": "ai-society-simulator",
        "category": "learning",
        "due_date": "2026-09-30",
        "estimated_minutes": 120,
        "evidence": "projects/product/rnd/book-ai/applications/agent-society-simulator/README.md",
    },
    {
        "key": "p3-access-gate",
        "title": "Autonomous Home — identify sensor and supervised access context",
        "description": "Identify a safe fixture or partner context for sensor/edge testing; no hardware purchase or actuator control is part of this task.",
        "status": "todo",
        "priority": 3,
        "project": "autonomous-home-digital-twin",
        "category": "learning",
        "due_date": "2026-10-31",
        "estimated_minutes": 90,
        "evidence": "projects/product/rnd/book-ai/applications/autonomous-home/README.md",
    },
    {
        "key": "p4-mentor-gate",
        "title": "Robot Skill Runtime — identify simulator and supervised mentor gate",
        "description": "Identify a simulator path and qualified mentor/partner requirements before any real robot, bounded workspace, or actuator test.",
        "status": "todo",
        "priority": 3,
        "project": "robot-skill-runtime",
        "category": "learning",
        "due_date": "2027-06-30",
        "estimated_minutes": 90,
        "evidence": "projects/product/rnd/book-ai/applications/robot-skill-runtime/README.md",
    },
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_goal(dry_run: bool) -> int:
    marker = f"source={ROADMAP_SOURCE}"
    goals = open_cell_store("goals", AUTOMATION_ID)
    row = next((g for g in goals.list("goals") if marker in str(g.get("notes") or "")), None)
    fields = {
        "title": "Autonomous Systems Portfolio: Cash + Proof + Portability",
        "description": "Execute the approved autonomous-systems portfolio from the Regulatory KB cash/proof anchor through trusted agents, simulation, edge, and supervised embodied AI.",
        "type": "yearly",
        "category": "business",
        "status": "active",
        "priority": 1,
        "start_date": TODAY,
        "due_date": "2027-08-28",
        "key_results": json.dumps([
            "Regulatory KB human review and market signal",
            "Personal Agent Runtime offline MVP and human review",
            "P2-P4 gate briefs with evidence boundaries",
        ], ensure_ascii=False),
        "notes": f"{marker}; portfolio-goal:cash-proof-portability; plan={ROADMAP_PATH}",
        "domain": "portfolio:autonomous-systems",
    }
    if dry_run:
        return int(row["id"]) if row else 0
    if row:
        goals.update("goals", int(row["id"]), fields)
        return int(row["id"])
    return int(goals.create("goals", fields)["id"])


def ensure_project(values: dict, parent_id: int | None, dry_run: bool) -> int:
    marker = f"source={ROADMAP_SOURCE}"
    projects = open_cell_store("projects", AUTOMATION_ID)
    existing = next((p for p in projects.list("projects") if p["slug"] == values["slug"]), None)
    fields = {
        "name": values["name"], "description": values["description"], "category": values["category"],
        "type": values["type"], "status": values["status"], "priority": values["priority"],
        "phase": values["phase"], "revenue_model": values["revenue_model"],
        "tech_stack": json.dumps(values["tech_stack"], ensure_ascii=False), "docs_url": values.get("docs_url"),
        "notes": f"{marker}; roadmap={ROADMAP_PATH}; evidence-first=true",
    }
    if parent_id is not None:
        fields["parent_project_id"] = parent_id
    if dry_run:
        return int(existing["id"]) if existing else 0
    if existing:
        existing_notes = existing.get("notes")
        if existing_notes and marker not in str(existing_notes):
            raise ValueError(f"project slug already exists outside roadmap ownership: {values['slug']}")
        projects.update("projects", int(existing["id"]), fields)
        return int(existing["id"])
    return int(projects.create("projects", {"slug": values["slug"], **fields})["id"])


def insert_link(plural: str, key: tuple[str, str], values: dict, dry_run: bool) -> None:
    """Create a goal-side link unless the (goal, project) pair already exists (the old INSERT OR IGNORE)."""
    if dry_run:
        return
    goals = open_cell_store("goals", AUTOMATION_ID)
    if goals.list(plural, **{key[0]: values[key[0]], key[1]: values[key[1]]}):
        return
    goals.create(plural, values)


def ensure_task(values: dict,
    goal_id: int,
    project_id: int,
    owner_id: str,
    owner_name: str,
    dry_run: bool,
) -> int:
    marker = f"source={ROADMAP_SOURCE}"
    store = open_task_store(None, "portfolio-seeder")
    needle = f"{marker}; task={values['key']}".lower()
    # Same rule as the old `notes LIKE '%needle%' AND title = ?` (first match by id).
    existing = next(
        (task for task in store.list(include_archived=True)
         if task["title"] == values["title"] and needle in str(task.get("notes") or "").lower()),
        None,
    )
    notes = f"{marker}; task={values['key']}; evidence={values['evidence']}; human-approval-required=true"
    tags = json.dumps([
        "autonomous-systems-roadmap",
        f"portfolio-goal:cash-proof-portability",
        f"economic-system:current-market",
        f"evidence:{values['evidence']}",
    ], ensure_ascii=False)
    fields = (
        values["title"], values["description"], values["status"], values["priority"], values["project"],
        tags, values["category"], values["due_date"], notes, values["estimated_minutes"], now(),
    )
    if dry_run:
        return int(existing["id"]) if existing else 0
    if existing:
        task_id = int(existing["id"])
        store.update(
            task_id,
            {
                "description": fields[1], "status": fields[2], "priority": fields[3], "project": fields[4],
                "tags": fields[5], "category": fields[6], "due_date": fields[7], "notes": fields[8],
                "estimated_minutes": fields[9],
            },
            legacy={"updated_at": fields[10]},
        )
    else:
        task_id = int(store.create(
            {
                "title": fields[0], "description": fields[1], "status": fields[2], "priority": fields[3],
                "project": fields[4], "tags": fields[5], "category": fields[6], "due_date": fields[7],
                "notes": fields[8], "estimated_minutes": fields[9],
            },
            legacy={"sync_status": "local", "created_at": fields[10], "updated_at": fields[10]},
        ))
    store.link(task_id, "goal", goal_id, {"link_type": "primary", "source": ROADMAP_SOURCE}, update=False)
    store.link(
        task_id, "project", project_id,
        {"relation_type": "delivery", "weight": 1, "source": ROADMAP_SOURCE}, update=False,
    )
    store.assign(task_id, "user", owner_id, owner_name, "assigned")
    return task_id


def run(dry_run: bool = False) -> dict[str, int]:
    goal_id = ensure_goal(dry_run)
    project_ids: dict[str, int] = {}
    for values in PROJECTS:
        parent_id = project_ids.get("autonomous-systems-portfolio")
        project_ids[values["slug"]] = ensure_project(values, parent_id, dry_run)
    if not dry_run:
        goal_id = ensure_goal(False)
        project_ids = {
            p["slug"]: int(p["id"])
            for p in open_cell_store("projects", AUTOMATION_ID).list("projects")
            if ROADMAP_SOURCE in str(p.get("notes") or "")
        }
    for slug, relation, weight in [
        ("autonomous-systems-portfolio", "delivery", 1),
        ("regulatory-knowledge-base", "delivery", 3),
        ("personal-agent-runtime", "delivery", 3),
        ("ai-society-simulator", "supporting", 2),
        ("autonomous-home-digital-twin", "supporting", 2),
        ("robot-skill-runtime", "supporting", 2),
    ]:
        if goal_id and project_ids.get(slug):
            insert_link(
                "goal-project-links", ("goal_id", "project_id"),
                {"goal_id": goal_id, "project_id": project_ids[slug], "relation_type": relation,
                 "weight": weight, "source": ROADMAP_SOURCE},
                dry_run,
            )
    owner = canonical_owner()
    owner_id, owner_name = owner["id"], owner["name"]
    task_ids = []
    for values in TASKS:
        project_id = project_ids.get(values["project"])
        if goal_id and project_id:
            task_ids.append(ensure_task(values, goal_id, project_id, owner_id, owner_name, dry_run))
    if not dry_run:
        done_ids = {
            int(task["id"])
            for task in open_task_store(None, "portfolio-seeder").list(status=["done"], include_archived=True)
        }
        project_links = open_task_store(None, "portfolio-seeder").links(target_type="project")
        projects = open_cell_store("projects", AUTOMATION_ID)
        for project_id in project_ids.values():
            linked = [int(link["task"]) for link in project_links if link["target_id"] == project_id]
            projects.update("projects", project_id, {
                "task_count": len(linked),
                "completed_count": sum(1 for task_id in linked if task_id in done_ids),
            })
    return {"goal_id": goal_id, "projects": len(project_ids), "tasks": len(task_ids)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = run(dry_run=args.dry_run)
    except (CellStoreError, ClientConfigError) as exc:
        print(f"Autonomous portfolio seed unavailable: {exc}")
        raise SystemExit(1)
    mode = "dry-run" if args.dry_run else "synced"
    print(f"Autonomous portfolio seed ({mode})")
    for key, value in result.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()

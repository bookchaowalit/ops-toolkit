#!/usr/bin/env python3
"""
Auto-update goal progress + domain enrichment scores.

Links tasks to goals via shared domain:
- Tasks → Projects → domain
- Goals → domain
- Progress = completed tasks / total tasks for that domain

Also computes domain enrichment scores and saves to JSON.
"""

import json
# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from _env import PROJECT_ROOT
from task_store import ClientConfigError, open_task_store
from cell_store import CellStoreError, open_cell_store
from datetime import datetime

AUTOMATION_ID = "portfolio-reader"  # task reads
CELL_AUTOMATION_ID = "progress-rollup"  # goals (read, progress write) and projects (read)

# Resolve DB path
DOMAINS_DIR = PROJECT_ROOT / "projects"
ENRICHMENT_JSON = PROJECT_ROOT / "data" / "indexes" / "domain-enrichment.json"


# Direct mapping: personal task.project → domain.
# Employer projects are managed in nexatech-empire.
PROJECT_DOMAIN_MAP = {
    # Todoist (personal projects)
    "💼 Income": "book-strategy",
    "📖 Growth": "book-learning",
    "🧠 Life": "book-life",
    "🏋️ Body": "book-health",
    "🎨 Creative": "book-creative",
}


def domain_task_counts(store, projects):
    """(total excluding cancelled, done) for tasks in ``projects``, archived included."""
    wanted = set(projects)
    tasks = [t for t in store.list(include_archived=True) if t["project"] in wanted]
    return (
        sum(1 for t in tasks if t["status"] != "cancelled"),
        sum(1 for t in tasks if t["status"] == "done"),
    )


def update_goal_progress():
    """Update goal progress_percent based on task completion in their domain."""

    goals_store = open_cell_store("goals", CELL_AUTOMATION_ID)

    # Get all active goals with domains
    goals = [g for g in goals_store.list("goals", status="active") if g["domain"] is not None]

    if not goals:
        print("   No active goals with domains found")
        return 0

    store = open_task_store(None, AUTOMATION_ID)
    updated = 0
    for goal in goals:
        goal_id, title, domain = goal["id"], goal["title"], goal["domain"]
        # Count tasks mapped to this domain
        domain_projects = [
            proj for proj, dom in PROJECT_DOMAIN_MAP.items() if dom == domain
        ]

        if not domain_projects:
            continue

        # Count total tasks in this domain
        total, completed = domain_task_counts(store, domain_projects)

        if total == 0:
            continue

        # Calculate progress
        progress = int((completed / total) * 100)

        # Get current progress
        current = goal["progress_percent"] or 0

        # Only update if changed
        if progress != current:
            goals_store.update("goals", goal_id, {"progress_percent": progress})
            print(f"   📊 {title[:40]}... {current}% → {progress}% ({completed}/{total} tasks)")
            updated += 1

    return updated


def update_enrichment_scores():
    """Compute and save domain enrichment scores to JSON."""
    # Content metrics per domain
    store = open_task_store(None, AUTOMATION_ID)
    goals_store = open_cell_store("goals", CELL_AUTOMATION_ID)
    projects_store = open_cell_store("projects", CELL_AUTOMATION_ID)
    domain_scores = []
    for domain_path in sorted(DOMAINS_DIR.glob("book-*")):
        domain_name = domain_path.name
        if not domain_path.exists():
            continue

        all_files = [f for f in domain_path.rglob("*") if f.is_file()]
        md_files = [f for f in all_files if f.suffix == ".md"]
        total_size = sum(f.stat().st_size for f in all_files if f.exists())

        # Count placeholders
        placeholders = 0
        for md in md_files:
            try:
                content = md.read_text(errors="ignore")
                if "TODO" in content or "placeholder" in content.lower() or len(content) < 100:
                    placeholders += 1
            except Exception:
                pass

        # DB metrics
        domain_projects = [p for p, d in PROJECT_DOMAIN_MAP.items() if d == domain_name]
        task_total = 0
        task_done = 0
        if domain_projects:
            task_total, task_done = domain_task_counts(store, domain_projects)

        goal_count = len(goals_store.list("goals", domain=domain_name, status="active"))

        project_count = len(projects_store.list("projects", domain=domain_name))

        # Score: higher = needs more enrichment
        score = 0
        if len(all_files) == 0:
            score += 50
        elif len(all_files) < 5:
            score += 30
        elif len(all_files) < 20:
            score += 10

        real_content = len(md_files) - placeholders
        if real_content == 0 and len(md_files) > 0:
            score += 20
        if goal_count > 0 and len(all_files) < 10:
            score += 25
        if project_count > 0 and len(all_files) < 10:
            score += 15
        if task_total > 0 and len(all_files) < 10:
            score += 20
        if total_size < 100_000:
            score += 10

        domain_scores.append({
            "domain": domain_name,
            "score": score,
            "files": len(all_files),
            "md_files": len(md_files),
            "size_kb": round(total_size / 1024, 1),
            "goals": goal_count,
            "projects": project_count,
            "tasks_total": task_total,
            "tasks_done": task_done,
            "updated_at": datetime.now().isoformat(),
        })

    domain_scores.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "generated_at": datetime.now().isoformat(),
        "total_domains": len(domain_scores),
        "high_priority": len([d for d in domain_scores if d["score"] >= 40]),
        "medium_priority": len([d for d in domain_scores if 20 <= d["score"] < 40]),
        "low_priority": len([d for d in domain_scores if d["score"] < 20]),
        "domains": domain_scores,
    }

    ENRICHMENT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(ENRICHMENT_JSON, "w") as f:
        json.dump(output, f, indent=2)

    # Log top 3 needing enrichment
    top3 = domain_scores[:3]
    for d in top3:
        if d["score"] >= 20:
            print(f"   📚 {d['domain']:25} enrichment score: {d['score']} (files:{d['files']}, goals:{d['goals']}, tasks:{d['tasks_done']}/{d['tasks_total']})")

    return output


def main():
    print("📊 Updating Goal Progress + Enrichment Scores")

    # 1. Update goal progress
    updated = update_goal_progress()
    print(f"   ✅ Updated {updated} goals")

    # 2. Update enrichment scores
    print()
    enrichment = update_enrichment_scores()
    print(f"   ✅ Enrichment scores saved → {ENRICHMENT_JSON.name}")
    print(f"   📊 {enrichment['total_domains']} domains | 🔴{enrichment['high_priority']} 🟡{enrichment['medium_priority']} 🟢{enrichment['low_priority']}")



if __name__ == "__main__":
    try:
        main()
    except (CellStoreError, ClientConfigError) as exc:
        print(f"❌ {exc}")
        sys.exit(1)

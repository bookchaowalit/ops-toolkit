#!/usr/bin/env python3
"""
Domain Content Enrichment Prioritizer

Analyzes all book-* domains and prioritizes which need enrichment based on:
- Content depth (file count, markdown count, subdirectories)
- Linked goals/projects (active vs none)
- Task activity (tasks in domain)
- Content quality signals (unique content vs placeholders)

Output: Prioritized list of domains needing enrichment
"""

# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from _env import PROJECT_ROOT
from task_store import open_task_store
from cell_store import open_cell_store
from datetime import datetime

AUTOMATION_ID = "portfolio-reader"

# Resolve paths
DOMAINS_DIR = PROJECT_ROOT / "projects"

# Project → domain mapping for tasks
PROJECT_DOMAIN_MAP = {
    "VWS - (Water)": "book-work",
    "Carpark": "book-work",
    "💼 Income": "book-strategy",
    "📖 Growth": "book-learning",
    "🧠 Life": "book-life",
    "🏋️ Body": "book-health",
    "🎨 Creative": "book-creative",
}


def scan_domain(domain_path):
    """Scan a domain folder for content metrics."""
    if not domain_path.exists():
        return None

    all_files = list(domain_path.rglob("*"))
    files = [f for f in all_files if f.is_file()]
    md_files = [f for f in files if f.suffix == ".md"]
    dirs = [d for d in all_files if d.is_dir()]

    # Check for placeholder content
    placeholder_count = 0
    for md in md_files:
        try:
            content = md.read_text(errors="ignore")
            if "TODO" in content or "placeholder" in content.lower() or len(content) < 100:
                placeholder_count += 1
        except:
            pass

    # Calculate size
    total_size = 0
    for f in files:
        try:
            total_size += f.stat().st_size
        except OSError:
            pass

    return {
        "files": len(files),
        "md_files": len(md_files),
        "dirs": len(dirs),
        "size_mb": total_size / (1024 * 1024),
        "placeholders": placeholder_count,
        "real_content": len(md_files) - placeholder_count,
    }


def load_db_data():
    """Load goals, projects, and tasks from the cells and the Tasks API."""
    # Goals by domain
    goals = {}
    for goal in open_cell_store("goals", AUTOMATION_ID).list("goals"):
        if goal["domain"] is None:
            continue
        entry = goals.setdefault(goal["domain"], {"total": 0, "active": 0})
        entry["total"] += 1
        entry["active"] += 1 if goal["status"] == "active" else 0

    # Projects by domain
    projects = {}
    for project in open_cell_store("projects", AUTOMATION_ID).list("projects"):
        if project["domain"] is not None:
            projects[project["domain"]] = projects.get(project["domain"], 0) + 1

    # Tasks by domain (via project mapping)
    tasks = {}
    store = open_task_store(None, AUTOMATION_ID)
    all_tasks = store.list(include_archived=True)
    for proj, domain in PROJECT_DOMAIN_MAP.items():
        in_project = [task for task in all_tasks if task["project"] == proj]
        count = sum(1 for task in in_project if task["status"] != "cancelled")
        done = sum(1 for task in in_project if task["status"] == "done")
        if domain not in tasks:
            tasks[domain] = {"total": 0, "done": 0}
        tasks[domain]["total"] += count
        tasks[domain]["done"] += done

    return goals, projects, tasks


def calculate_priority_score(stats, goal_data, project_count, task_data):
    """Calculate enrichment priority score (higher = needs more work)."""
    score = 0

    # No content at all
    if stats["files"] == 0:
        score += 50
    elif stats["files"] < 5:
        score += 30
    elif stats["files"] < 20:
        score += 10

    # No real content (all placeholders)
    if stats["real_content"] == 0 and stats["md_files"] > 0:
        score += 20

    # Has active goals but no content
    if goal_data and goal_data.get("active", 0) > 0 and stats["files"] < 10:
        score += 25

    # Has projects but no content
    if project_count > 0 and stats["files"] < 10:
        score += 15

    # Has tasks but no content
    if task_data and task_data.get("total", 0) > 0 and stats["files"] < 10:
        score += 20

    # Small size
    if stats["size_mb"] < 0.1:
        score += 10

    return score


def main():
    print("📚 Domain Content Enrichment Prioritizer")
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Domains: {DOMAINS_DIR}")
    print()

    # Load DB data
    goals, projects, tasks = load_db_data()

    # Scan all domains
    domains = sorted(DOMAINS_DIR.glob("book-*"))
    if not domains:
        print("   ⚠️  No domain folders found!")
        return

    print(f"   📁 Scanning {len(domains)} domains...")
    print()

    # Analyze each domain
    results = []
    for domain_path in domains:
        domain_name = domain_path.name
        stats = scan_domain(domain_path)
        if not stats:
            continue

        goal_data = goals.get(domain_name, {"total": 0, "active": 0})
        project_count = projects.get(domain_name, 0)
        task_data = tasks.get(domain_name, {"total": 0, "done": 0})

        score = calculate_priority_score(stats, goal_data, project_count, task_data)

        results.append({
            "name": domain_name,
            "stats": stats,
            "goals": goal_data,
            "projects": project_count,
            "tasks": task_data,
            "score": score,
        })

    # Sort by priority score (highest first)
    results.sort(key=lambda x: x["score"], reverse=True)

    # Display results
    print("   🎯 Priority Domains (needs enrichment):")
    print("   " + "=" * 70)

    for i, r in enumerate(results[:15], 1):
        s = r["stats"]
        g = r["goals"]
        t = r["tasks"]

        priority = "🔴" if r["score"] >= 40 else "🟡" if r["score"] >= 20 else "🟢"

        print(f"   {i:2}. {priority} {r['name']:25} Score: {r['score']:3}")
        print(f"       Files: {s['files']:5} | MD: {s['md_files']:4} | Size: {s['size_mb']:.1f}MB | Placeholders: {s['placeholders']}")

        if g["total"] > 0:
            print(f"       Goals: {g['active']} active / {g['total']} total", end="")
        if r["projects"] > 0:
            print(f" | Projects: {r['projects']}", end="")
        if t["total"] > 0:
            print(f" | Tasks: {t['done']}/{t['total']} done", end="")
        print()
        print()

    # Summary
    high_priority = len([r for r in results if r["score"] >= 40])
    medium_priority = len([r for r in results if 20 <= r["score"] < 40])
    low_priority = len([r for r in results if r["score"] < 20])

    print("   " + "=" * 70)
    print(f"   📊 Summary:")
    print(f"      🔴 High priority (score ≥40): {high_priority} domains")
    print(f"      🟡 Medium priority (score 20-39): {medium_priority} domains")
    print(f"      🟢 Low priority (score <20): {low_priority} domains")
    print()

    # Recommendations
    print("   💡 Recommendations:")
    print("   " + "-" * 70)

    top = results[0]
    if top["score"] >= 40:
        print(f"   1. Start with {top['name']} — it has goals/projects but minimal content")

    # Find domains with active goals but no content
    goal_domains = [r for r in results if r["goals"]["active"] > 0 and r["stats"]["files"] < 10]
    if goal_domains:
        names = ", ".join([d["name"].replace("book-", "") for d in goal_domains[:3]])
        print(f"   2. Focus on goal-linked domains: {names}")

    # Find domains with tasks but no content
    task_domains = [r for r in results if r["tasks"]["total"] > 0 and r["stats"]["files"] < 10]
    if task_domains:
        names = ", ".join([d["name"].replace("book-", "") for d in task_domains[:3]])
        print(f"   3. Add content for active task domains: {names}")

    print()
    print("   ✅ Analysis complete")


if __name__ == "__main__":
    main()

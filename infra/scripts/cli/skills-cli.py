#!/usr/bin/env python3
"""
Solo Empire Skills CLI — Manage skills and learning progress
Usage:
    python3 infra/scripts/cli/skills-cli.py                          # Show all active skills
    python3 infra/scripts/cli/skills-cli.py view <id>                # View skill detail
    python3 infra/scripts/cli/skills-cli.py log <id> <hours> "What learned"  # Log progress
    python3 infra/scripts/cli/skills-cli.py level <id> <level>       # Update level
    python3 infra/scripts/cli/skills-cli.py progress <id> [0-100]    # Update progress %
    python3 infra/scripts/cli/skills-cli.py revenue                  # Show revenue-relevant skills
    python3 infra/scripts/cli/skills-cli.py categories               # Show categories

``--enrich`` records activity signals for review only. It does not increase
skill progress or invested hours; explicit evidence belongs in
the roles-skills cell's ``progress-log`` ``evidence_url``.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError
import sys
from pathlib import Path
from datetime import date, datetime, timedelta


LEVEL_ICONS = {"L1_exploring": "🌱", "L2_capable": "🌿", "L3_proficient": "🌳", "L4_expert": "🏔️"}
STATUS_ICONS = {"not_started": "⚪", "practicing": "🟡", "proficient": "🟢", "paused": "⏸️"}
PRIORITY_ICONS = {1: "🔴", 2: "🟠", 3: "🟡", 4: "⚪"}

def _cell(name):
    """The owner-cli store of a kit cell (skills live in roles-skills, goals in goals)."""
    return open_cell_store(name, "owner-cli", channel="cli")


def show_skills(category=None, status=None, revenue_only=False):
    """Show skills with progress."""
    filters = {}
    if category:
        filters["category_id"] = int(category)
    if revenue_only:
        filters["revenue_relevant"] = True
    if status:
        filters["status"] = status
    elif not revenue_only:
        filters["status"] = ["practicing", "proficient"]

    roles_skills = _cell("roles-skills")
    category_names = {c["id"]: c["name"] for c in roles_skills.list("skill-categories")}
    usage: dict[int, dict] = {}
    for entry in roles_skills.list("progress-log"):
        used = usage.setdefault(entry["skill_id"], {"log_count": 0, "total_hours": 0.0})
        used["log_count"] += 1
        used["total_hours"] += entry.get("hours_spent") or 0
    skills = []
    for row in roles_skills.list("skills", **filters):
        used = usage.get(row["id"])
        skills.append(
            {
                **row,
                "category_name": category_names.get(row["category_id"]),
                "log_count": used["log_count"] if used else 0,
                "total_hours": used["total_hours"] if used else None,
            }
        )
    skills.sort(key=lambda r: (r["priority"] is not None, r["priority"] or 0, -(r["progress_percent"] or 0), r["id"]))

    if not skills:
        print("📚 No skills found.")
        return

    print("📚 SOLO EMPIRE SKILLS")
    print("=" * 60)

    current_cat = None
    for s in skills:
        if s["category_name"] != current_cat:
            current_cat = s["category_name"]
            print(f"\n  📁 {current_cat}")
            print("  " + "-" * 40)

        s_icon = STATUS_ICONS.get(s["status"], "⚪")
        l_icon = LEVEL_ICONS.get(s["level"], "🌱")
        p_icon = PRIORITY_ICONS.get(s["priority"], "⚪")

        pct = s["progress_percent"] or 0
        bar_len = 15
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        hours = s["total_hours"] or 0
        rev_tag = "💰" if s["revenue_relevant"] else "  "
        print(f"  [{s['id']}] {s_icon} {l_icon} {p_icon} {rev_tag} {s['name']}")
        print(f"       {bar} {pct}% | {hours:.1f}h | {s['log_count']} logs")

    total = len(skills)
    avg_pct = sum(s["progress_percent"] or 0 for s in skills) / total if total else 0
    total_hours = sum(s["total_hours"] or 0 for s in skills)
    print(f"\n  📊 {total} skills | avg: {avg_pct:.0f}% | {total_hours:.1f}h total")
    print()

def view_skill(skill_id):
    """View skill detail with progress logs."""
    roles_skills = _cell("roles-skills")
    s = roles_skills.get("skills", skill_id)

    if not s:
        print(f"❌ Skill #{skill_id} not found")
        return
    category = roles_skills.get("skill-categories", s["category_id"]) if s["category_id"] is not None else None
    s = {**s, "category_name": category["name"] if category else None}

    logs = sorted(
        roles_skills.list("progress-log", skill_id=skill_id),
        key=lambda log: (str(log.get("logged_at") or ""), log["id"]),
        reverse=True,
    )[:10]

    s_icon = STATUS_ICONS.get(s["status"], "⚪")
    l_icon = LEVEL_ICONS.get(s["level"], "🌱")
    p_icon = PRIORITY_ICONS.get(s["priority"], "⚪")

    print(f"\n{l_icon} {s_icon} [{s['id']}] {p_icon} {s['name']}")
    print("=" * 50)
    if s["description"]:
        print(f"  {s['description'][:120]}")
    print(f"  Category: {s['category_name']} | Level: {s['level'] or 'Not started'}")
    print(f"  Priority: P{s['priority']} | Progress: {s['progress_percent'] or 0}%")
    if s["hours_invested"]:
        print(f"  Hours: {s['hours_invested']}")
    if s["target_level"]:
        print(f"  Target: {s['target_level']}")
    if s["target_completion_date"]:
        print(f"  Target Date: {s['target_completion_date']}")
    if s["key_concepts"]:
        print(f"  Key Concepts: {s['key_concepts'][:80]}")

    if logs:
        print(f"\n  📝 RECENT LOGS ({len(logs)})")
        print("  " + "-" * 40)
        for log in logs:
            print(f"    {str(log['logged_at'] or '')[:10]} | {log['hours_spent'] or 0}h | {log['title']}")
            if log["what_learned"]:
                print(f"      → {log['what_learned'][:60]}")
    else:
        print(f"\n  📝 No logs yet. Log: skills-cli.py log {skill_id} <hours> \"What learned\"")
    print()

def log_progress(skill_id, hours, title, what_learned=None):
    """Log learning progress."""
    roles_skills = _cell("roles-skills")
    s = roles_skills.get("skills", skill_id)
    if not s:
        print(f"❌ Skill #{skill_id} not found")
        return

    hours = float(hours)
    new_hours = (s["hours_invested"] or 0) + hours

    # The skill total commits first (version-checked); the log then carries a stable key so a retry cannot double it.
    roles_skills.update("skills", skill_id, {"hours_invested": new_hours,
                                              "last_practiced": date.today().isoformat()},
                        expected_version=s.get("version"))
    stamp = datetime.now().isoformat()
    try:
        roles_skills.create("progress-log", {
            "skill_id": skill_id, "activity_type": "study", "title": title,
            "what_learned": what_learned, "hours_spent": hours, "logged_at": stamp,
        }, idempotency_key=f"skills-cli-log:{skill_id}:{stamp}")
    except (CellStoreError, ClientConfigError):
        print(f"❌ The skill now shows {new_hours:.1f}h, but its log entry was not saved. "
              "Do not run this command again; add the log entry by hand instead.")
        raise

    print(f"✅ Logged {hours}h for [{skill_id}] {s['name']}")
    print(f"   Total hours: {new_hours:.1f}h")

def update_level(skill_id, level):
    """Update skill level."""
    valid = ["L1_exploring", "L2_capable", "L3_proficient", "L4_expert"]
    if level not in valid:
        print(f"❌ Invalid level. Use: {', '.join(valid)}")
        return

    s = _cell("roles-skills").update("skills", skill_id, {"level": level})
    l_icon = LEVEL_ICONS.get(level, "🌱")
    print(f"✅ [{skill_id}] {s['name']} → {l_icon} {level}")

def update_progress(skill_id, pct):
    """Update skill progress percentage."""
    pct = max(0, min(100, int(pct)))
    s = _cell("roles-skills").update("skills", skill_id, {"progress_percent": pct})
    print(f"✅ [{skill_id}] {s['name']} → {pct}%")

def show_categories():
    """Show all skill categories."""
    roles_skills = _cell("roles-skills")
    counts = roles_skills.stats("skills", "category_id")
    cats = [{**c, "skill_count": int(counts.get(str(c["id"]), 0))} for c in roles_skills.list("skill-categories")]

    print("\n📁 SKILL CATEGORIES")
    print("=" * 50)
    for c in cats:
        print(f"  [{c['id']}] {c['name']} ({c['skill_count']} skills)")
        if c["description"]:
            print(f"      {c['description'][:60]}")
    print()

def main():
    try:
        _run(sys.argv[1:])
    except (CellStoreError, ClientConfigError) as exc:
        print(f"❌ {exc}")
        sys.exit(1)


def _run(args):

    if not args:
        show_skills()
    elif args[0] == "view" and len(args) >= 2:
        view_skill(int(args[1]))
    elif args[0] == "log" and len(args) >= 3:
        what = args[3] if len(args) > 3 else None
        log_progress(int(args[1]), args[2], "Study session", what)
    elif args[0] == "level" and len(args) >= 3:
        update_level(int(args[1]), args[2])
    elif args[0] == "progress" and len(args) >= 3:
        update_progress(int(args[1]), int(args[2]))
    elif args[0] == "revenue":
        show_skills(revenue_only=True)
    elif args[0] == "categories":
        show_categories()
    elif args[0] == "all":
        show_skills(status=None)
    elif args[0] == "--enrich" or args[0] == "enrich":
        enrich_skills()
    else:
        print(__doc__)

# ── Skill Enrichment ──────────────────────────────────────────────

# Maps goal domains to skill slugs
DOMAIN_SKILL_MAP = {
    'book-ai': ['ai-agents', 'rag', 'nlp-llm', 'machine-learning', 'mlops'],
    'book-dev': ['backend', 'api-design', 'database', 'devops', 'software-architecture', 'linux', 'containers', 'cloud', 'data-engineering'],
    'book-marketing': ['seo-content', 'email-marketing', 'copywriting'],
    'book-sales': ['consultative-selling', 'business-strategy-skill'],
    'book-work': ['backend', 'api-design', 'devops', 'business-strategy-skill'],
    'book-learning': ['software-architecture', 'data-engineering', 'machine-learning'],
    'book-health': [],
    'book-creative': ['copywriting'],
    'book-finance': ['business-strategy-skill'],
}


def enrich_skills():
    """Record activity signals without claiming learning mastery."""
    now = datetime.now().isoformat()
    enriched_count = 0
    roles_skills = _cell("roles-skills")

    # Get all active skills indexed by slug
    active_skills = {}
    for row in _cell("roles-skills").list("skills"):
        if row.get("status") not in (None, 'not_started'):
            active_skills[row['slug']] = dict(row)

    if not active_skills:
        print("No active skills to enrich.")
        return

    # Track which skills got signals
    skill_signals = {}  # slug -> {'tasks': 0, 'sources': set()}

    # Completed tasks → skill matching via goal domain
    goals_cell = _cell("goals")
    domain_by_goal = {g["id"]: g["domain"] for g in goals_cell.list("goals")}
    done_tasks = [
        t for t in goals_cell.list("goal-tasks", status="done")
        if t.get("completed_at") and t.get("goal_id") in domain_by_goal
    ]
    done_tasks.sort(key=lambda t: t["completed_at"], reverse=True)
    tasks = [{**t, "domain": domain_by_goal[t["goal_id"]]} for t in done_tasks[:50]]
    for t in tasks:
        domain = t['domain'] or ''
        slugs = DOMAIN_SKILL_MAP.get(domain, [])
        for slug in slugs:
            if slug in active_skills:
                skill_signals.setdefault(slug, {'tasks': 0, 'sources': set()})
                skill_signals[slug]['tasks'] += 1
                skill_signals[slug]['sources'].add('tasks')

    if not skill_signals:
        print("No activity signals found to enrich skills.")
        return

    # Apply enrichment
    print("🔄 SKILL ENRICHMENT")
    print("=" * 60)

    for slug, signals in sorted(skill_signals.items(), key=lambda x: x[1]['tasks'], reverse=True):
        sk = active_skills.get(slug)
        if not sk:
            continue

        skill_id = sk['id']
        old_pct = sk['progress_percent'] or 0

        # Create progress-log entry
        source_str = ', '.join(signals['sources'])
        title = f"Activity enrichment ({source_str})"
        already_logged = any(
            log.get("title") == title and str(log.get("logged_at") or "")[:10] == now[:10]
            for log in roles_skills.list("progress-log", skill_id=skill_id)
        )
        if already_logged:
            print(f"  [{skill_id}] {sk['name']}")
            print("       activity already captured today; unchanged")
            continue

        summary_parts = []
        if signals['tasks']:
            summary_parts.append(f"{signals['tasks']} tasks completed")
        summary = f"Auto-enriched from: {', '.join(summary_parts)}"

        roles_skills.create("progress-log", {
            "skill_id": skill_id, "activity_type": "review", "title": title,
            "what_learned": summary, "logged_at": now,
        }, idempotency_key=f"skills-enrich:{skill_id}:{title}:{now[:10]}")

        enriched_count += 1
        src_icons = {'tasks': '✅'}
        icons = ' '.join(src_icons.get(s, '?') for s in sorted(signals['sources']))
        print(f"  [{skill_id}] {sk['name']}")
        print(f"       {icons} | activity captured; progress/hours unchanged ({old_pct}%)")

    print(f"\n  ✅ Enriched {enriched_count} skills from activity data.")


if __name__ == "__main__":
    main()

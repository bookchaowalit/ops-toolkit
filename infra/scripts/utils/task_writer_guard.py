#!/usr/bin/env python3
"""Fail when code reads or writes the task, decision or habit tables outside their adapters.

Decisions (decision_records, decision_events, decision_reviews) live in the
decisions cell since 2026-10-10; every caller goes through
infra/scripts/utils/decision_store.py, the same way as tasks below. Habits
(habits, habit_logs, routines, routine_logs) live in the habits cell and go
through infra/scripts/utils/habits_store.py. Notifications (notification_rules,
notification_log, notifications) live in the notifications cell and go through
infra/scripts/utils/notifications_store.py or the Go notifier's client. Goals,
projects/portfolio and roles/skills (26 tables) live in three kit cells and go
through infra/scripts/utils/cell_store.py or infra/mcp-server/src/cell-store.ts.

The task tables (tasks and its link, assignment, suggestion, and type tables)
move to the Tasks API at cutover. Every reader and writer goes through
infra/scripts/utils/task_store.py so it can switch with
SOLO_EMPIRE_TASKS_BACKEND=api without a second copy of its logic. The
allowlist names the files that are not migrated yet; each entry says why and
goes away when that file moves or retires. New direct access fails.
Statements may span lines; a table name built at runtime (f-strings,
string concatenation) is not detected, so review covers that case.

    python3 infra/scripts/utils/task_writer_guard.py [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCANNED = ("infra", "services")
SUFFIXES = {".py", ".ts", ".js", ".mjs", ".go"}
TABLES_LIST = (
    # Tasks (the Tasks API).
    "tasks", "task_assignments", "task_goal_links", "task_project_links", "task_context_links", "task_skill_links",
    "task_role_suggestions", "task_types", "task_dependencies", "task_comments", "task_attachments",
    # Decisions, habits and notifications (their cells).
    "decision_records", "decision_events", "decision_reviews", "habits", "habit_logs", "routines", "routine_logs",
    "notification_rules", "notification_log", "notifications",
    # Goals, projects/portfolio and roles/skills (kit cells, infra/scripts/utils/cell_store.py) since 2026-10-10.
    "goals", "goal_tasks", "goal_metrics", "goal_portfolio_mappings", "productivity_goals", "goal_project_links",
    "goal_context_links", "goal_task_project_links", "goal_portfolio_current", "goal_attention_collision_summary",
    "projects", "project_repos", "portfolio_contexts", "project_context_links", "portfolio_work_items",
    "work_item_task_links", "work_item_goal_task_links", "personal_roles", "personal_role_context_links",
    "personal_role_goal_links", "personal_role_project_links", "personal_role_task_links", "role_goal_links",
    "skill_categories", "skills", "role_path_steps", "role_project_allocations", "role_skill_links",
    "progress_log",
    # Entity graph and AI telemetry, retired on 2026-10-11 (migration 088).
    "entity_registry", "entity_links", "entity_gap_reviews", "ai_run_telemetry", "ai_prompt_cache",
    "ai_conversation_logs",
    # Operating, intake and automation (kit cells) since 2026-10-10.
    "operating_cycles", "operating_cycle_tasks", "operating_cycle_goal_links", "operating_cycle_reviews",
    "operating_kpi_definitions", "operating_kpi_measurements", "source_registry", "intake_queue", "intake_events",
    "cron_jobs", "automation_rules", "workflow_pipelines", "automation_log",
    # Empty domains whose local code was deleted (migration 086). "content" and "journal" are left out:
    # they are common words in prose and SQL review covers them.
    "content_calendar", "content_series_episodes", "content_series_episode_metrics", "learning_challenges",
    "learning_challenge_assignments", "health_metrics", "time_entries", "time_logs", "business_kpis",
    "business_health_snapshots",
    # The CRM, finance and revenue (bookchaowalit/crm; ledger via infra/scripts/utils/crm_ledger.py), 087.
    "clients", "revenue", "invoices", "crm_deals", "crm_activities", "crm_agency_bookings", "crm_agency_consents",
    "crm_agency_deal_state", "crm_agency_documents", "crm_agency_invoice_links", "crm_agency_notifications",
    "crm_agency_offers", "crm_agency_slots", "crm_workflow_evidence", "niches", "outreach_logs", "job_leads",
    "asset_metrics", "opportunities", "opportunity_actions", "financial_transactions", "finance_accounts",
    "finance_account_aliases", "finance_budget_plans", "finance_cashflow_plans", "finance_investments",
    "finance_liabilities", "finance_market_price_snapshots", "finance_reconciliations",
    "finance_subscription_aliases", "finance_subscription_reviews", "finance_subscriptions", "finance_tax_items",
    "finance_valuation_snapshots",
)
TABLES = "(" + "|".join(TABLES_LIST) + ")"
ACCESS = re.compile(
    r"\b(insert(\s+or\s+\w+)?\s+into|update|delete\s+from|from|join)\s+[\"'`]?" + TABLES + r"\b(?![_./])(?!\s+import\b)",
    re.IGNORECASE)

# Only the adapters touch the task tables directly; every caller goes through them.
PENDING: dict[str, str] = {}


def is_fixture(path: str) -> bool:
    """Tests and fixtures build throwaway databases; they are not writers."""
    name = Path(path).name
    return ("/tests/" in f"/{path}" or name.endswith(("_test.py", "_test.go", ".test.ts"))
            or name.startswith("test_") or name == "automation_fixture.py")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "--", *SCANNED],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if Path(p).suffix in SUFFIXES and "node_modules/" not in p]


def scan(files: list[str]) -> dict:
    direct: dict[str, list[int]] = {}
    for path in files:
        if is_fixture(path):
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = sorted({text.count("\n", 0, m.start()) + 1 for m in ACCESS.finditer(text)})
        if lines:
            direct[path] = lines
    violations = {p: lines for p, lines in direct.items() if p not in PENDING}
    stale = sorted(p for p in PENDING if p not in direct)
    return {"ok": not violations and not stale, "violations": violations, "stale_allowlist": stale,
            "pending": {p: PENDING[p] for p in sorted(direct) if p in PENDING}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = scan(tracked_files())
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for path, lines in report["violations"].items():
            print(f"direct task/decision-table access outside the stores: {path}:{','.join(map(str, lines))}")
        for path in report["stale_allowlist"]:
            print(f"allowlisted file no longer touches the task tables; remove it from PENDING: {path}")
        print(f"task writer guard: {'ok' if report['ok'] else 'FAILED'} "
              f"({len(report['pending'])} adapter files allowlisted)")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

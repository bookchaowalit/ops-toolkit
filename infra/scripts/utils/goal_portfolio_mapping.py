#!/usr/bin/env python3
"""Build the reversible goal portfolio and attention mapping.

The canonical goal and goal-task rows are never merged, deleted, or rewritten by
this script. It only upserts rows of the goals cell's ``goal-portfolio-mappings``
resource and can render a reviewable report. The former SQL views
``goal_portfolio_current`` and ``goal_attention_collision_summary`` are the
functions ``current_portfolio`` and ``attention_collision_summary`` here.

Examples:
    python3 goal_portfolio_mapping.py
    python3 goal_portfolio_mapping.py --apply --report
    python3 goal_portfolio_mapping.py --apply --report --json   # writes $SOLO_REPORTS_DIR/GOAL-PORTFOLIO-MAP.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from _env import PROJECT_ROOT, REPORTS_DIR
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError

AUTOMATION_ID = "portfolio-seeder"

MAPPING_FIELDS = (
    "portfolio_bucket", "attention_class", "attention_cost", "collision_group", "canonical_goal",
    "canonical_goal_id", "current_season", "mapping_confidence", "mapping_source", "notes",
)


PORTFOLIO_BUCKETS = {
    "active_bet",
    "infrastructure",
    "maintenance",
    "capability_backlog",
    "option_portfolio",
    "large_scale_builder",
    "frontier",
    "someday_curiosity",
}

ATTENTION_CLASSES = {"focus", "parallel", "background", "maintenance", "explore", "option"}

BUCKET_LABELS = {
    "active_bet": "Active Bets",
    "infrastructure": "Infrastructure",
    "maintenance": "Maintenance",
    "capability_backlog": "Capability Backlog",
    "option_portfolio": "Option Portfolio",
    "large_scale_builder": "Large-Scale Builder Track",
    "frontier": "Frontier",
    "someday_curiosity": "Someday / Curiosity",
}

ATTENTION_LABELS = {
    "focus": "Focus",
    "parallel": "Parallel",
    "background": "Background",
    "maintenance": "Maintenance",
    "explore": "Explore",
    "option": "Option",
}


def current_season(today: date | None = None) -> str:
    """Return a stable YYYY-QN season label."""
    value = today or date.today()
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def normalize_title(value: str | None) -> str:
    """Normalize title text for deterministic family matching."""
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def _mapping(
    *,
    bucket: str,
    attention: str,
    cost: int,
    collision: str,
    canonical: str,
    confidence: str,
    notes: str,
) -> dict[str, Any]:
    if bucket not in PORTFOLIO_BUCKETS:
        raise ValueError(f"Unknown portfolio bucket: {bucket}")
    if attention not in ATTENTION_CLASSES:
        raise ValueError(f"Unknown attention class: {attention}")
    if not 0 <= cost <= 5:
        raise ValueError(f"Attention cost must be between 0 and 5: {cost}")
    return {
        "portfolio_bucket": bucket,
        "attention_class": attention,
        "attention_cost": cost,
        "collision_group": collision,
        "canonical_goal": canonical,
        "mapping_confidence": confidence,
        "mapping_source": "goal-portfolio-rule-v1",
        "notes": notes,
    }


def classify_goal(row: dict[str, Any]) -> dict[str, Any]:
    """Classify one goal without changing the goal itself.

    The explicit title families are intentionally conservative. Unknown goals are
    placed in a reversible review bucket rather than being promoted to focus.
    """
    title = normalize_title(row.get("title"))
    category = normalize_title(row.get("category"))
    status = row.get("status")

    if "global income" in title:
        return _mapping(
            bucket="active_bet",
            attention="focus",
            cost=5,
            collision="cash-global",
            canonical="global-income-first-dollar",
            confidence="high",
            notes="Current cash-proof-portability bet: prove received payment from a non-Thai payer.",
        )

    if "autonomous systems portfolio" in title:
        return _mapping(
            bucket="active_bet",
            attention="background",
            cost=1,
            collision="proof-product",
            canonical="autonomous-systems-cash-proof-portability",
            confidence="high",
            notes="Portfolio anchor kept in background for 2026-Q4; fed by Main Quest proof artifacts (Guild OS one-main-quest rule).",
        )

    if (
        "ai product/service" in title
        or "ai arbitrage niche" in title
        or "เลือก niche หลัก" in title
        or "setup distribution channel" in title
        or "สร้าง audience" in title
    ):
        return _mapping(
            bucket="active_bet",
            attention="parallel",
            cost=3,
            collision="market-offer",
            canonical="ai-service-market-loop",
            confidence="high",
            notes="Niche, offer, and distribution steps run inside the global-income Main Quest line for 2026-Q4.",
        )

    if "weekly planning cycle" in title or "evidence-backed delivery" in title:
        return _mapping(
            bucket="infrastructure",
            attention="parallel",
            cost=3,
            collision="operating-rhythm",
            canonical="weekly-evidence-backed-delivery",
            confidence="high",
            notes="Operating cadence that protects focus; not a separate product bet.",
        )

    if "deep dive ai/ml" in title or "deep dive ด้าน ai/ml" in title:
        return _mapping(
            bucket="capability_backlog",
            attention="explore",
            cost=2,
            collision="technical-learning",
            canonical="ai-ml-depth",
            confidence="high",
            notes="Technical depth option; activate only when it is the selected learning lane.",
        )

    if "deep dive infra/devops" in title:
        return _mapping(
            bucket="capability_backlog",
            attention="explore",
            cost=2,
            collision="technical-learning",
            canonical="infra-devops-depth",
            confidence="high",
            notes="Technical depth option; do not run as a second deep-focus lane by default.",
        )

    if "blood work" in title or "vo2 max" in title:
        return _mapping(
            bucket="infrastructure",
            attention="maintenance",
            cost=2,
            collision="healthspan",
            canonical="healthspan-maintenance",
            confidence="high",
            notes="Always-on health infrastructure; not a growth bet.",
        )

    large_scale_role_markers = (
        "executive sponsor / business owner",
        "project manager / delivery manager",
        "business analyst",
        "solution / enterprise architect",
        "finance / accounting controller",
        "legal / compliance officer",
        "procurement / vendor manager",
        "chief of staff / executive assistant",
        "business operations manager",
        "technical program manager / scrum master",
        "account manager / client partner",
        "contract manager",
        "risk manager / internal auditor",
    )
    if title.startswith("build operating capability:") and any(
        marker in title for marker in large_scale_role_markers
    ):
        return _mapping(
            bucket="large_scale_builder",
            attention="background",
            cost=1,
            collision="large-scale-builder",
            canonical="large-scale-builder-track",
            confidence="high",
            notes="Long-horizon project delivery capability; fed by current roles without becoming a focus bet.",
        )

    if title.startswith("build operating capability:") or "role baseline" in title:
        return _mapping(
            bucket="capability_backlog",
            attention="background",
            cost=1,
            collision="role-capability",
            canonical="role-capability-baselines",
            confidence="high",
            notes="Generated capability baseline; activate through a selected project or role path.",
        )

    if any(marker in title for marker in ("fusion", "space", "post-scarcity", "mega-project", "civilization-scale")):
        return _mapping(
            bucket="frontier",
            attention="option",
            cost=0,
            collision="frontier",
            canonical="frontier-systems",
            confidence="high",
            notes="Long-horizon frontier option; no current execution allocation.",
        )

    if status == "paused":
        return _mapping(
            bucket="someday_curiosity",
            attention="option",
            cost=0,
            collision="paused",
            canonical=f"paused:{normalize_title(row.get('title'))}",
            confidence="provisional",
            notes="Paused goal retained for history; no current attention allocation.",
        )

    if status == "planning":
        return _mapping(
            bucket="option_portfolio",
            attention="option",
            cost=0,
            collision="planned",
            canonical=f"planned:{normalize_title(row.get('title'))}",
            confidence="provisional",
            notes="Planning row retained as an option until a project or evidence gate activates it.",
        )

    if category in {"life", "health"}:
        return _mapping(
            bucket="infrastructure",
            attention="maintenance",
            cost=2,
            collision="life-maintenance",
            canonical="life-maintenance",
            confidence="provisional",
            notes="General life/health goal mapped to supporting infrastructure pending review.",
        )

    if category == "tech":
        return _mapping(
            bucket="capability_backlog",
            attention="explore",
            cost=2,
            collision="technical-learning",
            canonical=f"tech:{normalize_title(row.get('title'))}",
            confidence="review",
            notes="Unmatched technical goal; review against the current active technical lane.",
        )

    return _mapping(
        bucket="someday_curiosity",
        attention="background",
        cost=1,
        collision="unclassified",
        canonical=f"goal:{normalize_title(row.get('title'))}",
        confidence="review",
        notes="Conservative default; assign a portfolio bucket after reviewing the goal's intended outcome.",
    )


def load_goals(store: Any) -> list[dict[str, Any]]:
    """The goals of the goals cell, by id, with the columns the classifier reads."""
    keys = ("id", "title", "description", "type", "category", "status", "priority", "progress_percent", "due_date")
    return [{key: row.get(key) for key in keys} for row in sorted(store.list("goals"), key=lambda r: r["id"])]


def current_portfolio(goals: list[dict[str, Any]], mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replaces the ``goal_portfolio_current`` view: each mapping joined to its goal (inner join, by goal id)."""
    by_id = {goal["id"]: goal for goal in goals}
    rows = []
    for mapping in sorted(mappings, key=lambda m: m["goal_id"]):
        goal = by_id.get(mapping["goal_id"])
        if goal is None:
            continue
        rows.append(
            {
                "goal_id": goal["id"],
                "title": goal["title"],
                "description": goal.get("description"),
                "type": goal.get("type"),
                "category": goal.get("category"),
                "status": goal["status"],
                "priority": goal.get("priority"),
                "progress_percent": goal.get("progress_percent"),
                "due_date": goal.get("due_date"),
                "portfolio_bucket": mapping["portfolio_bucket"],
                "attention_class": mapping["attention_class"],
                "attention_cost": mapping["attention_cost"],
                "collision_group": mapping["collision_group"],
                "canonical_goal": mapping["canonical_goal"],
                "canonical_goal_id": mapping.get("canonical_goal_id"),
                "current_season": mapping["current_season"],
                "mapping_confidence": mapping["mapping_confidence"],
                "mapping_source": mapping["mapping_source"],
                "mapping_notes": mapping.get("notes"),
                "mapping_updated_at": mapping.get("updated_at"),
            }
        )
    return rows


def attention_collision_summary(goals: list[dict[str, Any]], mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replaces the ``goal_attention_collision_summary`` view.

    Canonical families (distinct season/attention/cost/collision/canonical goal) among active or
    planning goals, counted and cost-summed per season, attention class and collision group.
    """
    status = {goal["id"]: goal["status"] for goal in goals}
    families = {
        (m["current_season"], m["attention_class"], m["attention_cost"], m["collision_group"], m["canonical_goal"])
        for m in mappings
        if status.get(m["goal_id"]) in {"active", "planning"}
    }
    groups: dict[tuple[str, str, str], list[int]] = {}
    for season, attention, cost, collision, _canonical in families:
        entry = groups.setdefault((season, attention, collision), [0, 0])
        entry[0] += 1
        entry[1] += cost or 0
    return [
        {"current_season": season, "attention_class": attention, "collision_group": collision,
         "canonical_goal_count": count, "attention_cost": cost}
        for (season, attention, collision), (count, cost) in sorted(groups.items())
    ]


def build_mappings(rows: list[dict[str, Any]], season: str) -> list[dict[str, Any]]:
    """Create mapping rows and choose a representative id per canonical family."""
    mapped: list[dict[str, Any]] = []
    representatives: dict[str, int] = {}
    for row in rows:
        result = classify_goal(row)
        canonical = result["canonical_goal"]
        representatives[canonical] = min(representatives.get(canonical, row["id"]), row["id"])
        mapped.append(
            {
                "goal_id": row["id"],
                "status": row["status"],
                "title": row["title"],
                "current_season": season,
                **result,
            }
        )
    for item in mapped:
        item["canonical_goal_id"] = representatives[item["canonical_goal"]]
    return mapped


def apply_mappings(store: Any, mappings: list[dict[str, Any]]) -> None:
    """Upsert only goal-portfolio-mappings (id = goal_id); never mutate goals or goal_tasks."""
    existing = {row["goal_id"]: row for row in store.list("goal-portfolio-mappings")}
    for item in mappings:
        fields = {key: item[key] for key in MAPPING_FIELDS}
        current = existing.get(item["goal_id"])
        if current is None:
            store.create("goal-portfolio-mappings", {"goal_id": item["goal_id"], **fields})
        elif any(current.get(key) != value for key, value in fields.items()):
            store.update("goal-portfolio-mappings", item["goal_id"], fields)


def _families(mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in mappings:
        grouped[item["canonical_goal"]].append(item)

    families = []
    for canonical, items in sorted(grouped.items()):
        active = [item for item in items if item["status"] in {"active", "planning"}]
        representative = items[0]
        families.append(
            {
                "canonical_goal": canonical,
                "goal_id": representative["canonical_goal_id"],
                "bucket": representative["portfolio_bucket"],
                "attention": representative["attention_class"],
                "cost": representative["attention_cost"],
                "collision": representative["collision_group"],
                "rows": len(items),
                "active_rows": len(active),
                "confidence": sorted({item["mapping_confidence"] for item in items}),
                "examples": sorted({item["title"] for item in items})[:3],
            }
        )
    return families


def summary(mappings: list[dict[str, Any]]) -> dict[str, Any]:
    families = _families(mappings)
    active_families = [family for family in families if family["active_rows"]]
    bucket_counts: dict[str, int] = defaultdict(int)
    attention_counts: dict[str, int] = defaultdict(int)
    collision_counts: dict[str, int] = defaultdict(int)
    for family in active_families:
        bucket_counts[family["bucket"]] += 1
        attention_counts[family["attention"]] += 1
        collision_counts[family["collision"]] += 1
    return {
        "mapped_rows": len(mappings),
        "canonical_families": len(families),
        "active_or_planning_families": len(active_families),
        "focus_families": attention_counts.get("focus", 0),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "attention_counts": dict(sorted(attention_counts.items())),
        "collision_counts": dict(sorted(collision_counts.items())),
        "review_rows": sum(1 for item in mappings if item["mapping_confidence"] == "review"),
    }


def render_report(mappings: list[dict[str, Any]], season: str) -> str:
    families = _families(mappings)
    active_families = [family for family in families if family["active_rows"]]
    data = summary(mappings)
    focus_families = [family for family in active_families if family["attention"] == "focus"]
    focus_names = ", ".join(f"`{family['canonical_goal']}`" for family in focus_families)
    if len(focus_families) <= 5:
        focus_decision = (
            f"คง Focus {len(focus_families)} canonical families ({focus_names or 'ไม่มี'}) "
            "ไว้ก่อน; ยังอยู่ในกรอบสูงสุด 5 families และไม่มีการเลื่อน goal ใหม่เข้า Focus"
        )
    else:
        focus_decision = (
            f"ต้องลด Focus จาก {len(focus_families)} canonical families ให้เหลือไม่เกิน 5 "
            "ก่อนเปิด lane ใหม่"
        )
    large_scale_families = [
        family for family in active_families if family["bucket"] == "large_scale_builder"
    ]
    large_scale_decision = (
        f"คง Large-Scale Builder Track เป็น {ATTENTION_LABELS[large_scale_families[0]['attention']]} "
        "และใช้ current Law/AI/security/finance/operations เป็น feed; ยังไม่มีเหตุผลให้เปิดเป็น Focus"
        if large_scale_families
        else "ยังไม่มี active/planning family ใน Large-Scale Builder Track ที่ต้อง promote"
    )
    lines = [
        "# Goal Portfolio and Attention Map",
        "",
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Season: `{season}`",
        "> Mapping-only view. Existing goals, tasks, history, and statuses were not merged or deleted.",
        "",
        "## Summary",
        "",
        f"- Mapped goal rows: **{data['mapped_rows']}**",
        f"- Canonical goal families: **{data['canonical_families']}**",
        f"- Active/planning families: **{data['active_or_planning_families']}**",
        f"- Focus families: **{data['focus_families']}**",
        f"- Rows requiring review: **{data['review_rows']}**",
        "",
        "## Current-season decision",
        "",
        f"- **Focus:** {focus_decision}",
        f"- **Large-Scale Builder:** {large_scale_decision}.",
        "- **Explore/Option:** AI/ML, Infra/DevOps, Frontier, and other future lanes remain non-focus allocations until a project, market signal, responsibility, or evidence gate exists.",
        "- **Change applied:** ไม่มีการ merge, delete, archive, status change หรือ goal-task rewrite; รอบนี้เป็นการยืนยัน allocation เดิมจาก evidence ปัจจุบัน.",
        "",
        "## Active/planning families",
        "",
        "| Canonical goal | Bucket | Attention | Cost | Collision | Rows | Mapping |",
        "|---|---|---:|---:|---|---:|---|",
    ]
    for family in active_families:
        examples = "; ".join(family["examples"])
        confidence = ", ".join(family["confidence"])
        lines.append(
            f"| `{family['canonical_goal']}`<br>{examples} | "
            f"{BUCKET_LABELS[family['bucket']]} | {ATTENTION_LABELS[family['attention']]} | "
            f"{family['cost']} | `{family['collision']}` | {family['rows']} | {confidence} |"
        )

    lines.extend(
        [
            "",
            "## Attention collision view",
            "",
            "This counts canonical families, not duplicate database rows.",
            "",
            "| Collision group | Canonical families | Attention classes | Cost |",
            "|---|---:|---|---:|",
        ]
    )
    collision_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for family in active_families:
        collision_groups[family["collision"]].append(family)
    for collision, group in sorted(collision_groups.items()):
        lines.append(
            f"| `{collision}` | {len(group)} | "
            f"{', '.join(sorted({ATTENTION_LABELS[item['attention']] for item in group}))} | "
            f"{sum(item['cost'] for item in group)} |"
        )

    review_families = [family for family in families if "review" in family["confidence"]]
    lines.extend(["", "## Review queue", ""])
    if review_families:
        lines.append("These mappings use the conservative default and need a later human review:")
        lines.append("")
        for family in review_families[:80]:
            lines.append(f"- `{family['canonical_goal']}` — {family['examples'][0]}")
        if len(review_families) > 80:
            lines.append(f"- … plus {len(review_families) - 80} more families")
    else:
        lines.append("- ไม่มีรายการที่ต้อง review จาก rule ชุดนี้")

    lines.extend(
        [
            "",
            "## Contract",
            "",
            "- `status` stays live in the goals cell.",
            "- `portfolio_bucket` describes the role in the life portfolio.",
            "- `attention_class` and `attention_cost` describe current-season load.",
            "- `collision_group` and `canonical_goal` prevent duplicate rows from looking like separate deep-focus bets.",
            "- No merge, delete, archive, or goal-task rewrite is performed by this mapping.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Upsert mapping rows only; never mutate goals")
    parser.add_argument(
        "--report",
        nargs="?",
        const=str(REPORTS_DIR / "GOAL-PORTFOLIO-MAP.md"),
        help="Write a Markdown report (default path when flag has no value)",
    )
    parser.add_argument("--json", action="store_true", help="Print a machine-readable summary")
    parser.add_argument("--season", help="Override the current season label, e.g. 2026-Q3")
    args = parser.parse_args()

    season = args.season or current_season()
    store = open_cell_store("goals", AUTOMATION_ID)
    rows = load_goals(store)
    mappings = build_mappings(rows, season)
    if args.apply:
        apply_mappings(store, mappings)
    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = PROJECT_ROOT / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(mappings, season), encoding="utf-8")
        print(f"Wrote mapping report: {report_path}")

    payload = summary(mappings)
    payload.update({"season": season, "applied": args.apply, "report": args.report})
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"Mapped {payload['mapped_rows']} goal rows into "
            f"{payload['canonical_families']} canonical families; "
            f"focus families: {payload['focus_families']}; "
            f"review rows: {payload['review_rows']}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CellStoreError, ClientConfigError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(1)

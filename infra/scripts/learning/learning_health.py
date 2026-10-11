#!/usr/bin/env python3
"""Report learning activity separately from explicit evidence.

The existing ``skills.progress_percent`` value is useful as an activity or
self-assessment signal, but it is not proof of mastery. This read-only report
adds the missing evidence view without rewriting historical progress.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from cell_store import CellStoreError, open_cell_store  # noqa: E402
from task_store import ClientConfigError  # noqa: E402


AUTO_ENRICHMENT_TITLE = "Activity enrichment ("


def _log_totals(entries: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    totals: dict[Any, dict[str, Any]] = {}
    for entry in entries:
        item = totals.setdefault(entry["skill_id"], {
            "progress_log_count": 0, "auto_enrichment_logs": 0, "evidence_logs": 0,
            "demonstrated_evidence_logs": 0, "latest_evidence_at": None,
        })
        item["progress_log_count"] += 1
        if str(entry.get("title") or "").startswith(AUTO_ENRICHMENT_TITLE):
            item["auto_enrichment_logs"] += 1
        if str(entry.get("evidence_url") or "").strip():
            item["evidence_logs"] += 1
            if entry.get("activity_type") in ("practice", "project", "teaching"):
                item["demonstrated_evidence_logs"] += 1
            logged = entry.get("logged_at")
            if logged and (item["latest_evidence_at"] is None or str(logged) > str(item["latest_evidence_at"])):
                item["latest_evidence_at"] = logged
    return totals


def _skill_rows() -> list[dict[str, Any]]:
    cell = open_cell_store("roles-skills", "reporting-reader")
    logs = _log_totals(cell.list("progress-log"))
    skills = cell.list("skills")
    skills.sort(key=lambda sk: (-(sk["progress_percent"] or 0), sk["name"] or ""))
    empty = {
        "progress_log_count": 0, "auto_enrichment_logs": 0, "evidence_logs": 0,
        "demonstrated_evidence_logs": 0, "latest_evidence_at": None,
    }
    rows = [
        {
            **{key: sk.get(key) for key in ("id", "name", "slug", "progress_percent", "status", "hours_invested")},
            **{key: value for key, value in (logs.get(sk["id"]) or empty).items() if key != "skill_id"},
        }
        for sk in skills
    ]

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in (
            "progress_log_count",
            "auto_enrichment_logs",
            "evidence_logs",
            "demonstrated_evidence_logs",
        ):
            item[key] = int(item[key] or 0)
        item["evidence_state"] = (
            "demonstrated"
            if item["demonstrated_evidence_logs"] > 0
            else "practice"
            if item["evidence_logs"] > 0
            else "none"
        )
        item["proficiency_evidence_ready"] = bool(
            int(item["progress_percent"] or 0) >= 80
            and item["demonstrated_evidence_logs"] > 0
        )
        result.append(item)
    return result


def build_report() -> dict[str, Any]:
    skills = _skill_rows()
    total = len(skills)
    evidence_covered = sum(1 for skill in skills if skill["evidence_logs"] > 0)
    demonstrated = sum(
        1 for skill in skills if skill["demonstrated_evidence_logs"] > 0
    )
    high_activity_without_evidence = sum(
        1
        for skill in skills
        if int(skill["progress_percent"] or 0) >= 80
        and skill["demonstrated_evidence_logs"] == 0
    )
    progress_logs = sum(skill["progress_log_count"] for skill in skills)
    auto_logs = sum(skill["auto_enrichment_logs"] for skill in skills)

    return {
        "generated_on": date.today().isoformat(),
        "interpretation": {
            "activity_progress": "skills.progress_percent and status are activity/self-assessment signals",
            "explicit_evidence": "progress-log evidence_url is the evidence gate",
            "proficiency_evidence_ready": "activity progress >= 80% plus practice/project/teaching evidence; this is not a mastery claim",
        },
        "summary": {
            "skills": total,
            "activity_progress_avg": round(
                sum(int(skill["progress_percent"] or 0) for skill in skills) / total,
                1,
            )
            if total
            else 0.0,
            "skills_with_explicit_evidence": evidence_covered,
            "skills_with_demonstrated_evidence": demonstrated,
            "evidence_coverage_percent": round(evidence_covered / total * 100, 1)
            if total
            else 0.0,
            "high_activity_without_demonstrated_evidence": high_activity_without_evidence,
            "progress_logs": progress_logs,
            "auto_enrichment_logs": auto_logs,
            "auto_enrichment_log_percent": round(auto_logs / progress_logs * 100, 1)
            if progress_logs
            else 0.0,
        },
        "skills": skills,
    }


def print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("Learning Health")
    print(f"  Activity progress average: {summary['activity_progress_avg']}%")
    print(
        "  Explicit evidence coverage: "
        f"{summary['skills_with_explicit_evidence']}/{summary['skills']} "
        f"({summary['evidence_coverage_percent']}%)"
    )
    print(
        "  Demonstrated evidence: "
        f"{summary['skills_with_demonstrated_evidence']}/{summary['skills']} skills"
    )
    print(
        "  High activity without demonstrated evidence: "
        f"{summary['high_activity_without_demonstrated_evidence']} skills"
    )
    print(
        "  Auto-enrichment share of progress logs: "
        f"{summary['auto_enrichment_logs']}/{summary['progress_logs']} "
        f"({summary['auto_enrichment_log_percent']}%)"
    )

    gaps = [
        skill
        for skill in report["skills"]
        if int(skill["progress_percent"] or 0) >= 80
        and skill["demonstrated_evidence_logs"] == 0
    ]
    if gaps:
        print("\nEvidence gap queue (high activity, no demonstrated evidence):")
        for skill in gaps[:20]:
            print(
                f"  - [{skill['id']}] {skill['name']}: "
                f"activity={skill['progress_percent']}%, "
                f"evidence={skill['evidence_state']}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        report = build_report()
    except (OSError, CellStoreError, ClientConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

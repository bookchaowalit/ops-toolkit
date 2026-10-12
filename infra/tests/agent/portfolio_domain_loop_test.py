#!/usr/bin/env python3
"""Offline contract tests for the cross-domain Book Dev adapter loop."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "systems", "architecture-docs checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "workflows"))

from portfolio_domain_loop import (  # noqa: E402
    build_all_report,
    build_report,
    list_domains,
    load_profile,
    load_workflow_registry,
    queue_execution,
    recommend_tools,
    task_matches_profile,
)


def main() -> int:
    registry = load_workflow_registry()
    domains = list_domains(registry)
    assert len(domains) >= 40
    science = next(item for item in domains if item["slug"] == "book-science")
    assert science["adapter"] == "composed"
    assert science["capability_packs"] == ["research", "analytics", "knowledge"]
    profile = load_profile("book-ai", registry)
    assert profile["adapter"] == "composed"
    assert profile["name"] == "AI/ML"
    assert profile["path"] == "projects/product/rnd/book-ai"
    assert profile["workflow_id"] == "question-evidence-evaluate+build-verify-release+measure-reconcile-decide"

    assert task_matches_profile({"title": "unrelated", "project": "book-ai"}, profile)
    assert task_matches_profile({"title": "[Book AI] experiment", "project": None}, profile)
    assert not task_matches_profile({"title": "unrelated", "project": "book-sales"}, profile)

    free_registry = json.loads(
        (ARCHITECTURE_ROOT / "systems" / "cloud-platform" / "free-resource-index.json").read_text(encoding="utf-8")
    )
    tools, missing = recommend_tools(profile, free_registry, max_tools=3)
    assert not missing
    assert len(tools) == 3
    assert all(item["access_model"] != "trial-credit" for item in tools)
    for domain in domains:
        composed = load_profile(domain["slug"], registry)
        resolved, unresolved = recommend_tools(composed, free_registry, max_tools=20)
        assert composed["workflow_stages"], domain["slug"]
        assert not unresolved, (domain["slug"], unresolved)
        assert resolved, domain["slug"]

    portfolio = build_all_report(
        [
            {
                "domain": {"slug": "book-ai", "name": "AI/ML", "group": "rnd", "workspace": profile["path"], "capability_packs": profile["capability_packs"], "workflow_id": profile["workflow_id"]},
                "workspace": {"state": "clean"},
                "summary": {"candidate_count": 1, "tool_recommendation_count": 3, "blocked_task_count": 0},
            }
        ]
    )
    assert portfolio["contract"] == "solo-empire.book-dev-domain-loop.v2-all"
    assert portfolio["summary"]["domain_count"] == 1
    assert portfolio["domains"][0]["capability_packs"] == profile["capability_packs"]
    assert portfolio["policy"]["external_saas_provisioning"] is False

    workspace = {
        "path": profile["path"],
        "state": "clean",
        "readme": True,
        "file_count": 10,
        "git_status": [],
    }
    report = build_report(
        profile,
        [
            {"id": 1, "title": "AI task", "status": "todo", "priority": 2, "project": "book-ai"},
            {"id": 2, "title": "blocked", "status": "blocked", "priority": 2, "project": "book-ai"},
        ],
        workspace,
        tools,
        missing_tool_ids=missing,
        generated_at="2026-08-30T00:00:00+00:00",
    )
    assert report["contract"] == "solo-empire.book-dev-domain-loop.v2"
    assert report["summary"]["candidate_count"] == 1
    assert report["summary"]["blocked_task_count"] == 1
    assert report["policy"]["external_saas_provisioning"] is False
    assert report["workflow"]["stages"][:3] == ["question", "source", "experiment"]
    assert "official terms" in report["next_action"]

    with tempfile.TemporaryDirectory() as temp_dir:
        execution_dir = Path(temp_dir) / "execution"
        ticket = queue_execution(report, execution_dir, write=True)
        assert ticket["status"] == "queued"
        assert ticket["contract"] == "solo-empire.book-dev-domain-loop-execution.v2"
        assert (execution_dir / "current.json").is_file()
        assert (execution_dir / "current.md").is_file()
        assert (execution_dir / "state.json").is_file()
        assert len(list((execution_dir / "history").glob("*.json"))) == 1

        again = queue_execution(report, execution_dir, write=True)
        assert again["status"] == "skipped_existing_open"

    print("Portfolio domain loop tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

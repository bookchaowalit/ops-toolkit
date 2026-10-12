#!/usr/bin/env python3
"""Offline checks for the free-resource and Awesome-list discovery index."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "discovery"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists, requires  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "systems" / "cloud-platform", "architecture-docs checkout (SOLO_ARCHITECTURE_ROOT)")

from free_resource import (  # noqa: E402
    FREE_ACCESS_MODELS,
    audit_upstream,
    extract_github_repo,
    filter_resources,
    github_references,
    load_registry,
    validate_registry,
)


EXPECTED_CATEGORIES = {
    "ai",
    "api",
    "auth",
    "automation",
    "business",
    "ci-cd",
    "data",
    "database",
    "design",
    "email",
    "hosting",
    "learning",
    "observability",
    "payments",
    "security",
    "storage",
}


def test_registry_is_valid_and_broad() -> None:
    registry = load_registry()
    assert validate_registry(registry) == []
    assert len(registry["resources"]) >= 50
    assert len(registry["awesome_sources"]) >= 10
    categories = {resource["category"] for resource in registry["resources"]}
    assert EXPECTED_CATEGORIES <= categories


def test_free_only_excludes_trial_credit() -> None:
    registry = load_registry()
    all_resources = filter_resources(registry["resources"])
    free_resources = filter_resources(registry["resources"], free_only=True)
    trial_resources = [
        resource
        for resource in all_resources
        if resource["access_model"] == "trial-credit"
    ]
    assert trial_resources
    assert len(free_resources) < len(all_resources)
    assert all(resource["access_model"] in FREE_ACCESS_MODELS for resource in free_resources)
    assert not {resource["id"] for resource in trial_resources} & {
        resource["id"] for resource in free_resources
    }


def test_search_and_recommend_match_category_use_case_and_tags() -> None:
    registry = load_registry()
    hosting = filter_resources(registry["resources"], query="hosting")
    assert "cloudflare-pages-workers" in {resource["id"] for resource in hosting}

    sql = filter_resources(registry["resources"], query="sql", free_only=True)
    assert {"sqlbolt", "duckdb", "dbt-core"} <= {
        resource["id"] for resource in sql
    }

    api_sources = [
        source
        for source in registry["awesome_sources"]
        if "api" in source["categories"]
    ]
    assert {"public-api-lists", "free-for-dev"} <= {
        source["id"] for source in api_sources
    }


def test_review_required_resources_are_hidden_unless_requested() -> None:
    registry = load_registry()
    visible = filter_resources(registry["resources"], category="storage")
    all_storage = filter_resources(
        registry["resources"], category="storage", include_inactive=True
    )
    assert "minio" not in {resource["id"] for resource in visible}
    assert "seaweedfs" in {resource["id"] for resource in visible}
    assert "minio" in {resource["id"] for resource in all_storage}


def test_github_url_parser_supports_repository_and_file_urls() -> None:
    assert extract_github_repo("https://github.com/org/project") == "org/project"
    assert (
        extract_github_repo("https://github.com/org/project/blob/main/README.md")
        == "org/project"
    )
    assert extract_github_repo("https://example.com/org/project") is None


def test_upstream_audit_reports_archive_and_freshness_without_network() -> None:
    registry = {
        "updated": "2026-08-24",
        "upstream_reviewed": "2026-08-16",
        "awesome_sources": [
            {
                "id": "source",
                "name": "Source",
                "url": "https://github.com/org/source",
                "focus": "test",
                "categories": ["learning"],
                "source_type": "github-awesome-list",
            }
        ],
        "resources": [
            {
                "id": "resource",
                "official_url": "https://example.com/resource",
                "access_url": "https://github.com/org/resource",
            }
        ],
    }

    def fake_fetch(repo: str) -> dict[str, object]:
        if repo == "org/source":
            return {
                "ok": True,
                "full_name": repo,
                "archived": False,
                "pushed_at": "2026-08-20T00:00:00Z",
            }
        return {
            "ok": True,
            "full_name": repo,
            "archived": True,
            "pushed_at": "2026-08-10T00:00:00Z",
        }

    report = audit_upstream(
        registry,
        fetcher=fake_fetch,
        checked_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert report["unique_repositories"] == 2
    assert report["sources"]["upstream_newer_than_reviewed"] == 1
    assert report["resources"]["archived"] == 1
    assert {finding["status"] for finding in report["findings"]} == {
        "upstream-newer",
        "archived",
    }


def test_github_reference_collection_deduplicates_repository_fields() -> None:
    registry = load_registry()
    references = github_references(registry)
    keys = {(item["kind"], item["id"], item["repo"]) for item in references}
    assert len(keys) == len(references)
    assert any(item["repo"] == "minio/minio" for item in references)
    assert any(item["repo"] == "seaweedfs/seaweedfs" for item in references)


if __name__ == "__main__":
    for name in dir():
        if name.startswith("test_"):
            globals()[name]()
    print("OK: free resource discovery tests passed")

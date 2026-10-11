#!/usr/bin/env python3
"""Search the free-first service and Awesome-list discovery index.

The registry is deliberately local and reviewable. It never creates accounts,
calls providers, provisions infrastructure, or treats a GitHub list as proof
that a service is free. Official access links and re-check notes travel with
each entry so an operator can make the final decision before use.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import PROJECT_ROOT, ARCHITECTURE_ROOT  # noqa: E402


REGISTRY_PATH = ARCHITECTURE_ROOT / "systems" / "cloud-platform" / "free-resource-index.json"
VALID_ACCESS_MODELS = {"free-forever", "free-quota", "trial-credit", "self-hosted-oss"}
FREE_ACCESS_MODELS = {"free-forever", "free-quota", "self-hosted-oss"}
VALID_RESOURCE_STATUSES = {"active", "review-required"}
GITHUB_API_URL = "https://api.github.com/repos/{}"
REQUIRED_SOURCE_FIELDS = ("id", "name", "url", "focus", "categories", "source_type")
REQUIRED_RESOURCE_FIELDS = (
    "id",
    "name",
    "provider",
    "category",
    "use_cases",
    "access_model",
    "requires_account",
    "official_url",
    "access_url",
    "free_notes",
    "limits",
    "source_ids",
    "tags",
)


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read free resource registry {path}: {exc}") from exc
    errors = validate_registry(data)
    if errors:
        raise ValueError("invalid free resource registry:\n" + "\n".join(errors))
    return data


def validate_registry(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root must be an object"]

    if not isinstance(data.get("version"), int) or isinstance(data.get("version"), bool):
        errors.append("version must be an integer")

    sources = data.get("awesome_sources")
    if not isinstance(sources, list) or not sources:
        errors.append("awesome_sources must be a non-empty list")
        sources = []
    resources = data.get("resources")
    if not isinstance(resources, list) or not resources:
        errors.append("resources must be a non-empty list")
        resources = []

    source_ids: set[str] = set()
    for index, source in enumerate(sources, 1):
        prefix = f"source {index}"
        if not isinstance(source, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field in REQUIRED_SOURCE_FIELDS:
            value = source.get(field)
            if field == "categories":
                if not isinstance(value, list) or not value:
                    errors.append(f"{prefix}: categories must be a non-empty list")
            elif not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}: {field} must be a non-empty string")
        source_id = source.get("id")
        if isinstance(source_id, str) and source_id:
            if source_id in source_ids:
                errors.append(f"{prefix}: duplicate id {source_id!r}")
            source_ids.add(source_id)
        if isinstance(source.get("url"), str) and not source["url"].startswith("https://"):
            errors.append(f"{prefix}: url must use https")

    resource_ids: set[str] = set()
    for index, resource in enumerate(resources, 1):
        prefix = f"resource {index}"
        if not isinstance(resource, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field in REQUIRED_RESOURCE_FIELDS:
            if field not in resource:
                errors.append(f"{prefix}: missing {field}")
        resource_id = resource.get("id")
        if isinstance(resource_id, str) and resource_id:
            if resource_id in resource_ids:
                errors.append(f"{prefix}: duplicate id {resource_id!r}")
            resource_ids.add(resource_id)

        if resource.get("access_model") not in VALID_ACCESS_MODELS:
            errors.append(
                f"{prefix}: access_model must be one of {sorted(VALID_ACCESS_MODELS)}"
            )
        if not isinstance(resource.get("requires_account"), bool):
            errors.append(f"{prefix}: requires_account must be a boolean")
        for field in ("use_cases", "source_ids", "tags"):
            value = resource.get(field)
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                errors.append(f"{prefix}: {field} must be a list of non-empty strings")
        for field in ("official_url", "access_url"):
            value = resource.get(field)
            if not isinstance(value, str) or not value.startswith("https://"):
                errors.append(f"{prefix}: {field} must be an https URL")
        for field in ("name", "provider", "category", "free_notes", "limits"):
            value = resource.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}: {field} must be a non-empty string")
        if resource.get("status", "active") not in VALID_RESOURCE_STATUSES:
            errors.append(
                f"{prefix}: status must be one of {sorted(VALID_RESOURCE_STATUSES)}"
            )
        for source_id in resource.get("source_ids", []):
            if source_id not in source_ids:
                errors.append(f"{prefix}: unknown source_id {source_id!r}")

    return errors


def source_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {source["id"]: source for source in registry["awesome_sources"]}


def filter_resources(
    resources: Iterable[dict[str, Any]],
    *,
    query: str | None = None,
    category: str | None = None,
    provider: str | None = None,
    access_model: str | None = None,
    source_id: str | None = None,
    free_only: bool = False,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    normalized_query = query.lower().strip() if query else None
    result: list[dict[str, Any]] = []
    for resource in resources:
        if not include_inactive and resource.get("status", "active") != "active":
            continue
        if category and resource["category"] != category:
            continue
        if provider and resource["provider"] != provider:
            continue
        if access_model and resource["access_model"] != access_model:
            continue
        if source_id and source_id not in resource["source_ids"]:
            continue
        if free_only and resource["access_model"] not in FREE_ACCESS_MODELS:
            continue
        if normalized_query:
            searchable = json.dumps(resource, ensure_ascii=False).lower()
            if normalized_query not in searchable:
                continue
        result.append(resource)
    return sorted(result, key=lambda item: (item["category"], item["name"]))


def render_resources(resources: list[dict[str, Any]], sources: dict[str, dict[str, Any]]) -> str:
    if not resources:
        return "ไม่พบ resource ที่ตรงกับเงื่อนไข"
    lines: list[str] = []
    for resource in resources:
        account = "ต้องมีบัญชี" if resource["requires_account"] else "ไม่ต้องมีบัญชี"
        lines.extend(
            [
                f"[{resource['category']}] {resource['name']} — {resource['provider']}",
                f"Access: {resource['access_model']} | {account}",
                f"Use: {', '.join(resource['use_cases'])}",
                f"Official: {resource['official_url']}",
                f"Access/Pricing: {resource['access_url']}",
                f"Status: {resource.get('status', 'active')}",
                f"Free note: {resource['free_notes']}",
                f"Limits: {resource['limits']}",
                "Awesome: "
                + ", ".join(sources[source_id]["url"] for source_id in resource["source_ids"]),
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def render_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "ไม่พบ Awesome source ที่ตรงกับเงื่อนไข"
    return "\n".join(
        f"{source['id']}\t{source['name']}\t{', '.join(source['categories'])}\t{source['url']}"
        for source in sources
    )


def extract_github_repo(url: str) -> str | None:
    """Return ``owner/repo`` for a GitHub URL, including file URLs."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repository = parts[:2]
    repository = repository.removesuffix(".git")
    if not owner or not repository:
        return None
    return f"{owner}/{repository}"


def github_references(registry: dict[str, Any]) -> list[dict[str, str]]:
    """Collect unique catalog references that point at GitHub repositories."""

    references: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_reference(kind: str, item_id: str, url: str, field: str) -> None:
        repo = extract_github_repo(url)
        if not repo:
            return
        key = (kind, item_id, repo)
        if key in seen:
            return
        seen.add(key)
        references.append(
            {"kind": kind, "id": item_id, "field": field, "repo": repo, "url": url}
        )

    for source in registry["awesome_sources"]:
        add_reference("source", source["id"], source["url"], "url")
    for resource in registry["resources"]:
        add_reference("resource", resource["id"], resource["access_url"], "access_url")
    return references


def fetch_github_repository(
    repo: str,
    *,
    timeout: float = 10.0,
    token: str | None = None,
) -> dict[str, Any]:
    """Fetch public repository metadata without exposing credentials."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "solo-empire-free-resource-audit/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(GITHUB_API_URL.format(repo), headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code in {403, 429}:
            return {"ok": False, "error": "GitHub API rate limit or access denied"}
        return {"ok": False, "error": f"HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError):
        return {"ok": False, "error": "network error"}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid GitHub response"}

    if not isinstance(payload, dict) or not isinstance(payload.get("full_name"), str):
        return {"ok": False, "error": "invalid GitHub response"}
    return {
        "ok": True,
        "full_name": payload["full_name"],
        "archived": bool(payload.get("archived", False)),
        "default_branch": payload.get("default_branch"),
        "pushed_at": payload.get("pushed_at"),
        "updated_at": payload.get("updated_at"),
    }


def _is_newer_than_baseline(pushed_at: Any, baseline: Any) -> bool:
    if not isinstance(pushed_at, str) or not pushed_at:
        return False
    if not isinstance(baseline, str) or not baseline:
        return False
    try:
        pushed_date = datetime.fromisoformat(pushed_at.replace("Z", "+00:00")).date()
        registry_date = date.fromisoformat(baseline)
    except ValueError:
        return False
    return pushed_date > registry_date


RepositoryFetcher = Callable[[str], dict[str, Any]]


def audit_upstream(
    registry: dict[str, Any],
    *,
    timeout: float = 10.0,
    token: str | None = None,
    fetcher: RepositoryFetcher | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Audit GitHub references without modifying the local registry."""

    references = github_references(registry)
    upstream_baseline = registry.get("upstream_reviewed") or registry.get("updated")
    fetch = fetcher or (
        lambda repo: fetch_github_repository(repo, timeout=timeout, token=token)
    )
    repos: dict[str, dict[str, Any]] = {}
    for reference in references:
        if reference["repo"] not in repos:
            result = dict(fetch(reference["repo"]))
            result["repo"] = reference["repo"]
            result["upstream_newer_than_reviewed"] = _is_newer_than_baseline(
                result.get("pushed_at"), upstream_baseline
            )
            repos[reference["repo"]] = result

    def summarize(kind: str) -> dict[str, int]:
        items = [reference for reference in references if reference["kind"] == kind]
        results = [repos[reference["repo"]] for reference in items]
        return {
            "total": len(items),
            "reachable": sum(1 for result in results if result.get("ok")),
            "archived": sum(1 for result in results if result.get("archived")),
            "errors": sum(1 for result in results if not result.get("ok")),
            "upstream_newer_than_reviewed": sum(
                1 for result in results if result.get("upstream_newer_than_reviewed")
            ),
        }

    findings: list[dict[str, Any]] = []
    for reference in references:
        result = repos[reference["repo"]]
        if not result.get("ok"):
            findings.append(
                {
                    **reference,
                    "status": "error",
                    "error": result.get("error", "unknown error"),
                }
            )
        elif result.get("archived"):
            findings.append({**reference, "status": "archived"})
        elif result.get("upstream_newer_than_reviewed"):
            findings.append({**reference, "status": "upstream-newer"})

    timestamp = checked_at or datetime.now(timezone.utc)
    return {
        "checked_at": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "registry_updated": registry.get("updated"),
        "upstream_reviewed": upstream_baseline,
        "unique_repositories": len(repos),
        "sources": summarize("source"),
        "resources": summarize("resource"),
        "findings": findings,
        "repositories": repos,
    }


def render_audit(report: dict[str, Any]) -> str:
    lines = [
        f"ตรวจ GitHub upstream ณ {report['checked_at']}",
        f"Registry updated: {report.get('registry_updated') or 'unknown'} | "
        f"Upstream reviewed: {report.get('upstream_reviewed') or 'unknown'}",
        f"Unique repositories: {report['unique_repositories']}",
    ]
    for label, key in (("Sources", "sources"), ("Resources", "resources")):
        summary = report[key]
        lines.append(
            f"{label}: {summary['total']} refs | reachable {summary['reachable']} | "
            f"archived {summary['archived']} | errors {summary['errors']} | "
            f"upstream newer than reviewed {summary['upstream_newer_than_reviewed']}"
        )
    if report["findings"]:
        lines.append("Findings:")
        for finding in report["findings"]:
            detail = finding.get("error", finding["status"])
            lines.append(
                f"- [{finding['status']}] {finding['kind']}:{finding['id']} "
                f"({finding['repo']}) — {detail}"
            )
    else:
        lines.append("Findings: none")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    check = subparsers.add_parser("check", help="validate the resource registry")
    check.add_argument("--registry", help="optional registry path for tests")
    check.set_defaults(handler=command_check)

    listing = subparsers.add_parser("list", help="list resources")
    add_filters(listing)
    listing.set_defaults(handler=command_list)

    search = subparsers.add_parser("search", help="search resources by name, use case, tag, or note")
    search.add_argument("query")
    add_filters(search)
    search.set_defaults(handler=command_search)

    recommend = subparsers.add_parser("recommend", help="find resources for a use case")
    recommend.add_argument("use_case", help="e.g. hosting, api, sql, auth, email")
    add_filters(recommend)
    recommend.set_defaults(handler=command_search)

    awesome = subparsers.add_parser("awesome", help="list Awesome GitHub discovery sources")
    awesome.add_argument("--category", help="filter sources by covered category")
    awesome.add_argument("--json", action="store_true", help="emit JSON")
    awesome.add_argument("--registry", help="optional registry path for tests")
    awesome.set_defaults(handler=command_awesome)

    audit = subparsers.add_parser(
        "audit-upstream",
        help="audit GitHub repository reachability, archive state, and freshness",
    )
    audit.add_argument("--json", action="store_true", help="emit JSON")
    audit.add_argument("--registry", help="optional registry path for tests")
    audit.add_argument("--timeout", type=float, default=10.0)
    audit.add_argument(
        "--strict",
        action="store_true",
        help="return non-zero when a reference is archived, unreachable, or newer than the review baseline",
    )
    audit.set_defaults(handler=command_audit_upstream)
    return parser


def add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--category", help="filter by category")
    parser.add_argument("--provider", help="filter by provider")
    parser.add_argument("--access-model", choices=sorted(VALID_ACCESS_MODELS))
    parser.add_argument("--source", dest="source_id", help="filter by Awesome source id")
    parser.add_argument("--free-only", action="store_true", help="exclude trial-credit entries")
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="include resources marked review-required",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--registry", help="optional registry path for tests")


def command_check(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry) if args.registry else REGISTRY_PATH)
    categories = {resource["category"] for resource in registry["resources"]}
    print(
        f"OK: validated {len(registry['resources'])} resources, "
        f"{len(categories)} categories, {len(registry['awesome_sources'])} discovery sources"
    )
    return 0


def selected_resources(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry = load_registry(Path(args.registry) if args.registry else REGISTRY_PATH)
    resources = filter_resources(
        registry["resources"],
        query=getattr(args, "query", None) or getattr(args, "use_case", None),
        category=args.category,
        provider=args.provider,
        access_model=args.access_model,
        source_id=args.source_id,
        free_only=args.free_only,
        include_inactive=args.include_inactive,
    )
    return registry, resources


def command_list(args: argparse.Namespace) -> int:
    registry, resources = selected_resources(args)
    if args.json:
        print(json.dumps({"resources": resources}, ensure_ascii=False, indent=2))
    else:
        print(render_resources(resources, source_map(registry)))
    return 0


def command_search(args: argparse.Namespace) -> int:
    registry, resources = selected_resources(args)
    if args.json:
        print(json.dumps({"query": args.query if hasattr(args, "query") else args.use_case, "resources": resources}, ensure_ascii=False, indent=2))
    else:
        print(render_resources(resources, source_map(registry)))
    return 0


def command_awesome(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry) if args.registry else REGISTRY_PATH)
    sources = registry["awesome_sources"]
    if args.category:
        sources = [source for source in sources if args.category in source["categories"]]
    if args.json:
        print(json.dumps({"sources": sources}, ensure_ascii=False, indent=2))
    else:
        print(render_sources(sources))
    return 0


def command_audit_upstream(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry) if args.registry else REGISTRY_PATH)
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")
    report = audit_upstream(registry, timeout=args.timeout, token=token)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_audit(report))
    if args.strict and any(
        report[group]["archived"]
        or report[group]["errors"]
        or report[group]["upstream_newer_than_reviewed"]
        for group in ("sources", "resources")
    ):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run an approval-gated handoff loop for any registered Book-* domain.

The workflow is provider-neutral and read-only by default. It uses the
canonical workspace registry, local SQLite task data, and the local
free-resource catalog. It never provisions a SaaS account, sends a message,
changes a task, or edits a domain workspace.

Examples:
    python3 portfolio_domain_loop.py --list
    python3 portfolio_domain_loop.py --all --json
    python3 portfolio_domain_loop.py --domain book-ai --json
    python3 portfolio_domain_loop.py --domain book-sales --write --queue-execution --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

WORKFLOW_DIR = Path(__file__).resolve().parent
UTILS_DIR = WORKFLOW_DIR.parent / "utils"
DISCOVERY_DIR = WORKFLOW_DIR.parent / "discovery"
for _path in (UTILS_DIR, DISCOVERY_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import yaml  # noqa: E402

from _env import PROJECT_ROOT, ARCHITECTURE_ROOT  # noqa: E402
from free_resource import load_registry  # noqa: E402
from task_store import open_task_store  # noqa: E402
from workspace_paths import WORKSPACE_REGISTRY, workspace_path, workspace_record  # noqa: E402

AUTOMATION_ID = "portfolio-reader"


CONTRACT = "solo-empire.book-dev-domain-loop.v2"
EXECUTION_CONTRACT = "solo-empire.book-dev-domain-loop-execution.v2"
REGISTRY_PATH = ARCHITECTURE_ROOT / "systems" / "architecture" / "book-dev-domain-workflows.yaml"
FREE_RESOURCE_REGISTRY = ARCHITECTURE_ROOT / "systems" / "cloud-platform" / "free-resource-index.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "runtime" / "agent-loop" / "domains"
RUNNABLE_STATUSES = {"todo", "in_progress", "waiting"}
MAX_CONTEXT_FILES = 8
MAX_STATUS_LINES = 20
MAX_FILE_SCAN = 5000


def _clean(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:limit].rstrip()


def _path_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_string_list(value: Any, field: str, context: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{context}: {field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def load_workflow_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    """Load and validate the human-editable domain adapter registry."""

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read domain workflow registry {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 2:
        raise ValueError("domain workflow registry must be a version 2 mapping")
    packs = data.get("capability_packs")
    defaults = data.get("group_defaults")
    overrides = data.get("domain_overrides")
    if not isinstance(packs, dict) or not packs:
        raise ValueError("domain workflow registry must contain capability_packs")
    if not isinstance(defaults, dict) or not defaults:
        raise ValueError("domain workflow registry must contain group_defaults")
    if not isinstance(overrides, dict):
        raise ValueError("domain workflow registry domain_overrides must be a mapping")

    normalized_packs: dict[str, dict[str, Any]] = {}
    for pack_id, pack in packs.items():
        context = f"capability pack {pack_id}"
        if not isinstance(pack_id, str) or not pack_id.strip() or not isinstance(pack, dict):
            raise ValueError(f"{context} must be a mapping with a non-empty id")
        name = pack.get("name")
        workflow_id = pack.get("workflow_id")
        objective = pack.get("objective")
        if not all(isinstance(value, str) and value.strip() for value in (name, workflow_id, objective)):
            raise ValueError(f"{context}: name, workflow_id, and objective are required strings")
        stages = _as_string_list(pack.get("stages", []), "stages", context)
        evidence = _as_string_list(pack.get("evidence", []), "evidence", context)
        tools = pack.get("tools", [])
        if not isinstance(tools, list):
            raise ValueError(f"{context}: tools must be a list")
        normalized_tools: list[dict[str, str]] = []
        seen_tool_ids: set[str] = set()
        for tool_index, tool in enumerate(tools, 1):
            tool_context = f"{context} tool {tool_index}"
            if not isinstance(tool, dict):
                raise ValueError(f"{tool_context} must be a mapping")
            tool_id, purpose = tool.get("id"), tool.get("purpose")
            if not isinstance(tool_id, str) or not tool_id.strip() or not isinstance(purpose, str) or not purpose.strip():
                raise ValueError(f"{tool_context}: id and purpose are required strings")
            tool_id = tool_id.strip()
            if tool_id in seen_tool_ids:
                raise ValueError(f"{context}: duplicate tool id {tool_id!r}")
            seen_tool_ids.add(tool_id)
            normalized_tools.append({"id": tool_id, "purpose": _clean(purpose, 180)})
        normalized_packs[pack_id.strip()] = {
            "id": pack_id.strip(), "name": name.strip(), "workflow_id": workflow_id.strip(),
            "objective": _clean(objective, 240), "stages": stages, "evidence": evidence,
            "tools": normalized_tools,
        }

    normalized_defaults: dict[str, list[str]] = {}
    for group, pack_ids in defaults.items():
        if not isinstance(group, str) or not group.strip():
            raise ValueError("group_defaults keys must be non-empty strings")
        values = _as_string_list(pack_ids, "group_defaults", f"group {group}")
        unknown = [pack_id for pack_id in values if pack_id not in normalized_packs]
        if unknown:
            raise ValueError(f"group {group}: unknown capability packs: {', '.join(unknown)}")
        normalized_defaults[group.strip()] = values

    normalized_overrides: dict[str, dict[str, Any]] = {}
    for slug, override in overrides.items():
        context = f"domain override {slug}"
        if not isinstance(slug, str) or not slug.strip() or not isinstance(override, dict):
            raise ValueError(f"{context} must be a mapping with a non-empty slug")
        pack_ids = _as_string_list(override.get("packs", []), "packs", context)
        unknown = [pack_id for pack_id in pack_ids if pack_id not in normalized_packs]
        if unknown:
            raise ValueError(f"{context}: unknown capability packs: {', '.join(unknown)}")
        normalized_overrides[slug.strip()] = {
            "packs": pack_ids,
            "task_projects": _as_string_list(override.get("task_projects", []), "task_projects", context),
            "task_project_prefixes": _as_string_list(
                override.get("task_project_prefixes", []), "task_project_prefixes", context
            ),
            "title_prefixes": _as_string_list(override.get("title_prefixes", []), "title_prefixes", context),
        }
    return {
        "version": 2,
        "contract": data.get("contract", CONTRACT),
        "capability_packs": normalized_packs,
        "group_defaults": normalized_defaults,
        "domain_overrides": normalized_overrides,
    }


def _profile_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return registry.get("domain_overrides", {})


def list_domains(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """List every active workspace and its composed capability packs."""

    registry = registry or load_workflow_registry()
    result = []
    for record in WORKSPACE_REGISTRY:
        if record["status"] != "active":
            continue
        profile = load_profile(record["slug"], registry)
        result.append(
            {
                "slug": record["slug"], "name": record["name"], "bu": record["bu"],
                "group": record["group"], "path": record["path"], "adapter": profile["adapter"],
                "capability_packs": profile["capability_packs"], "workflow_id": profile["workflow_id"],
            }
        )
    return result


def load_profile(slug: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compose a domain profile from workspace group defaults and small overrides."""

    slug = (slug or "").strip()
    if not slug:
        raise ValueError("domain slug must not be empty")
    record = workspace_record(slug)
    if record["status"] != "active":
        raise ValueError(f"workspace {slug!r} is registered as planned, not active")
    registry = registry or load_workflow_registry()
    override = _profile_map(registry).get(slug, {})
    pack_ids = override.get("packs") or registry["group_defaults"].get(record["group"], ["knowledge"])
    packs = [registry["capability_packs"][pack_id] for pack_id in pack_ids]
    stages: list[str] = []
    evidence: list[str] = []
    tools: list[dict[str, str]] = []
    seen_tools: set[str] = set()
    for pack in packs:
        for stage in pack["stages"]:
            if stage not in stages:
                stages.append(stage)
        for item in pack["evidence"]:
            if item not in evidence:
                evidence.append(item)
        for tool in pack["tools"]:
            if tool["id"] not in seen_tools:
                tools.append(dict(tool))
                seen_tools.add(tool["id"])
    profile = {
        "slug": slug, "name": record["name"], "task_projects": [slug],
        "task_project_prefixes": [], "title_prefixes": [f"[{record['name']}]"],
        "capability_packs": [pack["id"] for pack in packs], "pack_names": [pack["name"] for pack in packs],
        "workflow_id": "+".join(pack["workflow_id"] for pack in packs),
        "workflow_objective": " / ".join(pack["objective"] for pack in packs),
        "workflow_stages": stages, "evidence_patterns": evidence, "tools": tools,
        "adapter": "composed",
    }
    for key in ("task_projects", "task_project_prefixes", "title_prefixes"):
        if key in override and override[key]:
            profile[key] = list(override[key])
    profile.update(
        {
            "bu": record["bu"],
            "group": record["group"],
            "path": record["path"],
            "status": record["status"],
        }
    )
    return profile


def task_matches_profile(task: dict[str, Any], profile: dict[str, Any]) -> bool:
    title = str(task.get("title") or "").casefold()
    project = str(task.get("project") or "").casefold()
    exact_projects = {value.casefold() for value in profile.get("task_projects", [])}
    project_prefixes = [value.casefold() for value in profile.get("task_project_prefixes", [])]
    title_prefixes = [value.casefold() for value in profile.get("title_prefixes", [])]
    return (
        project in exact_projects
        or any(project.startswith(prefix) for prefix in project_prefixes)
        or any(title.startswith(prefix) for prefix in title_prefixes)
    )


def load_domain_tasks(profile: dict[str, Any]) -> list[dict[str, Any]]:
    store = open_task_store(None, AUTOMATION_ID)
    status_rank = {"in_progress": 0, "todo": 1, "waiting": 2, "blocked": 3}
    rows = [
        task for task in store.list(include_archived=True)
        if task["status"] not in ("done", "cancelled")
    ]
    rows.sort(key=lambda task: (
        status_rank.get(task["status"], 4),
        task["priority"] is not None, task["priority"] if task["priority"] is not None else 0,
        task["due_date"] is None, task["due_date"] or "",
        task["id"],
    ))
    fields = ("id", "title", "description", "status", "priority", "project", "due_date", "updated_at", "parent_task_id")
    return [
        {field: task[field] for field in fields}
        for task in rows if task_matches_profile(task, profile)
    ]


def _run_git_status(project_root: Path, relative_path: str) -> tuple[str, list[str], str | None]:
    try:
        result = subprocess.run(
            [
                "git", "-C", str(project_root), "status", "--porcelain=v1",
                "--untracked-files=normal", "--", relative_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unreadable", [], _clean(exc, 180)
    if result.returncode:
        return "unreadable", [], _clean(result.stderr, 180) or "git_status_failed"
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return ("dirty" if lines else "clean"), lines[:MAX_STATUS_LINES], None


def inspect_workspace(workspace_root: Path, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Collect bounded evidence without entering or modifying a workspace."""

    resolved_root = workspace_root.resolve()
    resolved_project_root = project_root.resolve()
    if resolved_project_root not in resolved_root.parents:
        raise ValueError("domain workspace must stay under the repository projects/ boundary")
    if not resolved_root.is_dir():
        return {
            "path": _path_label(workspace_root), "state": "missing", "exists": False,
            "readme": False, "file_count": 0, "top_level_directories": [],
            "git_status": [], "error_class": "workspace_missing",
        }

    relative = resolved_root.relative_to(resolved_project_root).as_posix()
    if not relative.startswith("projects/"):
        raise ValueError("domain workspace must stay under projects/")
    state, status_lines, error = _run_git_status(project_root, relative)
    file_count = 0
    scan_truncated = False
    for path in resolved_root.rglob("*"):
        if any(part in {".git", "node_modules", ".next", "dist", "build", "coverage"} for part in path.parts):
            continue
        if path.is_file():
            file_count += 1
            if file_count >= MAX_FILE_SCAN:
                scan_truncated = True
                break
    return {
        "path": _path_label(resolved_root), "state": state, "exists": True,
        "readme": (resolved_root / "README.md").is_file(), "file_count": file_count,
        "file_scan_truncated": scan_truncated,
        "top_level_directories": sorted(
            path.name for path in resolved_root.iterdir()
            if path.is_dir() and path.name != ".git"
        )[:40],
        "git_status": status_lines, "error_class": error,
    }


def recommend_tools(
    profile: dict[str, Any], free_registry: dict[str, Any], *, max_tools: int = 5
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve curated free/open resources without making network calls."""

    resources = {item["id"]: item for item in free_registry.get("resources", [])}
    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    for tool in profile.get("tools", [])[:max_tools]:
        resource = resources.get(tool["id"])
        if resource is None or resource.get("access_model") == "trial-credit":
            missing.append(tool["id"])
            continue
        selected.append(
            {
                "id": resource["id"], "name": resource["name"],
                "category": resource["category"], "access_model": resource["access_model"],
                "requires_account": resource["requires_account"],
                "official_url": resource["official_url"], "access_url": resource["access_url"],
                "purpose": tool["purpose"], "free_notes": resource["free_notes"],
                "limits": resource["limits"],
            }
        )
    return selected, missing


def _context_files(profile: dict[str, Any], workspace: dict[str, Any]) -> list[str]:
    candidates = [f"{workspace['path']}/README.md"]
    for evidence in profile.get("evidence_patterns", []):
        path = PROJECT_ROOT / workspace["path"] / evidence
        if path.is_file():
            candidates.append(f"{workspace['path']}/{evidence}")
        elif path.is_dir():
            candidates.append(f"{workspace['path']}/{evidence}/")
    candidates.extend(
        [
            "projects/workspaces.yaml",
            "docs/WORKSPACE-LOCATION-MAP.md",
            "systems/cloud-platform/free-resource-index.json",
        ]
    )
    return [
        path for path in candidates
        if (PROJECT_ROOT / path.rstrip("/")).is_file() or (PROJECT_ROOT / path.rstrip("/")).is_dir()
    ][:MAX_CONTEXT_FILES]


def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"), "title": _clean(task.get("title")),
        "description": _clean(task.get("description"), 600), "status": task.get("status"),
        "priority": task.get("priority"), "project": _clean(task.get("project"), 120),
        "due_date": task.get("due_date"), "updated_at": task.get("updated_at"),
    }


def build_candidate(
    task: dict[str, Any], profile: dict[str, Any], workspace: dict[str, Any], tools: list[dict[str, Any]]
) -> dict[str, Any]:
    task_payload = _task_payload(task)
    return {
        "task": task_payload, "domain": profile["slug"],
        "target": {"kind": "workspace", "path": workspace["path"], "state": workspace["state"]},
        "workspace": workspace,
        "recommendation": (
            "Inspect the domain workspace, then implement one bounded task inside it. "
            "Use shared infra only through an explicitly approved cross-domain task."
        ),
        "context_files": _context_files(profile, workspace),
        "recommended_tools": [tool["id"] for tool in tools],
        "verification": [
            "Capture the exact domain-specific verification command and output.",
            "Check the final diff against the task acceptance evidence.",
            "Record blockers or missing external access instead of guessing.",
        ],
        "approval_gate": [
            "No external message, SaaS provisioning, spending, publish, delete, or production change without owner approval.",
            "No task status update, commit, push, merge, or deployment from the discovery workflow.",
        ],
        "agent_prompt": (
            f"ทำงานเฉพาะ task #{task_payload['id']} ใน domain {profile['slug']} "
            f"ภายใต้ {workspace['path']} อ่าน context files ก่อนแก้ไข "
            "ตรวจสถานะอีกครั้ง รักษางานที่ไม่เกี่ยวข้อง รัน verification ที่เหมาะสม "
            "และหยุดก่อน action ที่ต้องอนุมัติ"
        ),
        "source": "open_task",
    }


def build_report(
    profile: dict[str, Any], tasks: list[dict[str, Any]], workspace: dict[str, Any],
    tools: list[dict[str, Any]], *, missing_tool_ids: Iterable[str] = (),
    generated_at: str | None = None, max_tasks: int = 5,
) -> dict[str, Any]:
    missing = list(missing_tool_ids)
    runnable = [task for task in tasks if task.get("status") in RUNNABLE_STATUSES]
    candidates = [build_candidate(task, profile, workspace, tools) for task in runnable[:max_tasks]]
    return {
        "contract": CONTRACT, "generated_at": generated_at or _now_iso(),
        "domain": {
            "slug": profile["slug"], "name": profile["name"], "bu": profile["bu"],
            "group": profile["group"], "adapter": profile["adapter"], "workspace": profile["path"],
            "capability_packs": profile["capability_packs"], "workflow_id": profile["workflow_id"],
        },
        "source": {
            "task_store": "tasks cell API", "workspace_registry": "projects/workspaces.yaml",
            "free_resource_registry": "systems/cloud-platform/free-resource-index.json",
            "task_selection": "explicit project or title-prefix adapter match",
        },
        "summary": {
            "open_task_count": len(tasks), "runnable_task_count": len(runnable),
            "blocked_task_count": sum(1 for task in tasks if task.get("status") == "blocked"),
            "candidate_count": len(candidates), "tool_recommendation_count": len(tools),
            "missing_tool_id_count": len(missing),
        },
        "workspace": workspace, "tool_recommendations": tools, "missing_tool_ids": missing,
        "candidates": candidates,
        "workflow": {
            "id": profile["workflow_id"],
            "objective": profile["workflow_objective"],
            "stages": profile["workflow_stages"],
            "capability_packs": profile["capability_packs"],
            "moves": [
                "domain_selection", "bounded_task_discovery", "workspace_evidence",
                "free_tool_recommendation", "optional_execution_ticket",
                "human_approved_execution", "independent_verification",
            ],
            "ai_provider_call": False, "external_write": False,
        },
        "policy": {
            "read_only_discovery": True, "workspace_writes_by_workflow": False,
            "task_database_writes": False, "external_saas_provisioning": False,
            "trial_credit_resources_excluded": True, "automatic_status_updates": False,
            "human_approval_required_for_irreversible_actions": True, "max_tasks_per_run": max_tasks,
        },
        "next_action": (
            f"อ่าน candidate แรกของ {profile['slug']} แล้วทำงานเฉพาะใน {profile['path']} "
            "พร้อมตรวจ official terms ของ tool ก่อนเปิดใช้"
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary, domain = report["summary"], report["domain"]
    lines = [
        f"# Book Dev Domain Loop — {domain['slug']}", "",
        "> Handoff นี้สร้างจาก task database, workspace registry และ free-resource registry แบบ local",
        "> ไม่อนุญาตให้ workflow แก้ไฟล์, เปลี่ยน task, provision SaaS, ส่งข้อความ หรือ deploy อัตโนมัติ",
        "", f"Generated: `{report['generated_at']}`  ", f"Contract: `{report['contract']}`  ",
        f"Workspace: `{domain['workspace']}` (`{domain['adapter']}` adapter)", "",
        "## Workflow lane",
        "",
        f"- Workflow: `{report['workflow']['id']}`",
        f"- Capability packs: {', '.join(f'`{item}`' for item in report['workflow']['capability_packs'])}",
        f"- Stages: {' → '.join(report['workflow']['stages'])}",
        f"- Objective: {report['workflow']['objective']}",
        "",
        "## สรุป", "", f"- Open tasks: **{summary['open_task_count']}** ({summary['blocked_task_count']} blocked)",
        f"- Candidates: **{summary['candidate_count']}**",
        f"- OSS/free recommendations: **{summary['tool_recommendation_count']}**", "",
        "## Workspace evidence", "", f"- State: `{report['workspace']['state']}`",
        f"- README: `{report['workspace']['readme']}`", f"- Files observed: `{report['workspace']['file_count']}`",
        f"- Git status lines: `{len(report['workspace'].get('git_status') or [])}`", "",
        "## OSS / free-tier recommendations", "",
        "รายการนี้เป็นข้อเสนอจาก registry ภายใน ไม่ใช่การยืนยันราคา/โควตาปัจจุบัน; ตรวจ official terms ก่อนใช้",
    ]
    if report["tool_recommendations"]:
        for tool in report["tool_recommendations"]:
            account = "ต้องมีบัญชี" if tool["requires_account"] else "ไม่ต้องมีบัญชี"
            lines.extend(
                [
                    f"- **{tool['name']}** (`{tool['access_model']}`, {account}) — {tool['purpose']}",
                    f"  Official: {tool['official_url']} | Access: {tool['access_url']}",
                    f"  ข้อจำกัด: {tool['limits']}",
                ]
            )
    else:
        lines.append("- ไม่มีรายการ tool ที่ resolve ได้จาก free-resource registry")
    lines.extend(["", "## Candidate queue", ""])
    if not report["candidates"]:
        lines.append("_ไม่พบ runnable task ที่ match กับ domain adapter นี้_\n")
    else:
        for index, candidate in enumerate(report["candidates"], 1):
            task = candidate["task"]
            lines.extend(
                [
                    f"### {index}. #{task['id']} P{task['priority']} — {task['title']}", "",
                    f"- Status: `{task['status']}`", f"- Target: `{candidate['target']['path']}` (`{candidate['target']['state']}`)",
                    f"- Recommendation: {candidate['recommendation']}",
                    f"- Context: {', '.join(f'`{item}`' for item in candidate['context_files']) or 'none found'}", "",
                ]
            )
    lines.extend(
        [
            "## Safety", "", "1. ทำทีละ candidate และอยู่ใน workspace ที่ระบุ",
            "2. อ่าน context และตรวจ dirty work ก่อนแก้ไข",
            "3. หยุดก่อนส่งข้อความ, ใช้ credential, เปิด SaaS, ใช้เงินจริง, publish, deploy, commit หรือ push",
            "", f"Next action: {report['next_action']}", "",
        ]
    )
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path, state_path = output_dir / "latest.json", output_dir / "latest.md", output_dir / "state.json"
    report["outputs"] = {"json": _path_label(json_path), "markdown": _path_label(markdown_path), "state": _path_label(state_path)}
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    json_path.write_text(payload, encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "contract": f"{CONTRACT}-state", "last_run_at": report["generated_at"],
                "latest_json": report["outputs"]["json"], "latest_markdown": report["outputs"]["markdown"],
                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            }, ensure_ascii=False, indent=2
        ) + "\n", encoding="utf-8"
    )
    return report["outputs"]


def build_all_report(reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact portfolio index from per-domain read-only reports."""

    items = []
    for report in reports:
        domain, summary = report["domain"], report["summary"]
        items.append(
            {
                "slug": domain["slug"], "name": domain["name"], "group": domain["group"],
                "workspace": domain["workspace"], "capability_packs": domain["capability_packs"],
                "workflow_id": domain["workflow_id"], "workspace_state": report["workspace"]["state"],
                "candidate_count": summary["candidate_count"],
                "tool_recommendation_count": summary["tool_recommendation_count"],
                "blocked_task_count": summary["blocked_task_count"],
            }
        )
    items.sort(key=lambda item: item["slug"])
    return {
        "contract": f"{CONTRACT}-all",
        "generated_at": _now_iso(),
        "summary": {
            "domain_count": len(items),
            "candidate_count": sum(item["candidate_count"] for item in items),
            "tool_recommendation_count": sum(item["tool_recommendation_count"] for item in items),
            "blocked_task_count": sum(item["blocked_task_count"] for item in items),
            "clean_workspace_count": sum(item["workspace_state"] == "clean" for item in items),
            "dirty_workspace_count": sum(item["workspace_state"] == "dirty" for item in items),
        },
        "domains": items,
        "policy": {
            "read_only_discovery": True,
            "external_saas_provisioning": False,
            "execution_ticket_mode": "one domain at a time; use --domain with --queue-execution",
        },
        "next_action": "เลือก domain เดียวเพื่อ queue execution ticket หลัง review portfolio index",
    }


def render_all_markdown(index: dict[str, Any]) -> str:
    summary = index["summary"]
    lines = [
        "# Book Dev Domain Portfolio Index", "",
        "> ภาพรวมของ active Book-* workspaces ทั้งหมดจาก composed domain adapters",
        "> การรัน `--all` ไม่ queue execution ticket และไม่ทำ external write", "",
        f"Generated: `{index['generated_at']}`  ", f"Contract: `{index['contract']}`", "",
        "## Summary", "",
        f"- Domains: **{summary['domain_count']}**",
        f"- Candidates: **{summary['candidate_count']}**",
        f"- Tool recommendations: **{summary['tool_recommendation_count']}**",
        f"- Workspaces: **{summary['clean_workspace_count']} clean / {summary['dirty_workspace_count']} dirty**", "",
        "## Domains", "", "| Domain | Packs | Candidates | Tools | Workspace |", "|---|---|---:|---:|---|",
    ]
    for item in index["domains"]:
        lines.append(
            f"| `{item['slug']}` | {', '.join(f'`{pack}`' for pack in item['capability_packs'])} "
            f"| {item['candidate_count']} | {item['tool_recommendation_count']} "
            f"| `{item['workspace']}` ({item['workspace_state']}) |"
        )
    lines.extend(["", f"Next action: {index['next_action']}", ""])
    return "\n".join(lines)


def write_all_index(index: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = output_dir / "index.json", output_dir / "index.md"
    index["outputs"] = {"json": _path_label(json_path), "markdown": _path_label(markdown_path)}
    json_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_all_markdown(index), encoding="utf-8")
    return index["outputs"]


def _candidate_fingerprint(candidate: dict[str, Any]) -> str:
    payload = {"domain": candidate.get("domain"), "task": candidate.get("task"), "target": candidate.get("target"), "recommendation": candidate.get("recommendation")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _render_execution_markdown(ticket: dict[str, Any]) -> str:
    candidate = ticket.get("selected") or {}
    task, target = candidate.get("task") or {}, candidate.get("target") or {}
    lines = [
        f"# Domain Execution Ticket — {ticket.get('domain')}", "",
        "> Ticket นี้ให้สิทธิ์ตรวจสอบและ implement งานที่ขอบเขตเท่านั้น",
        "> ไม่ให้สิทธิ์ commit, push, deploy, publish, provision SaaS หรือส่งข้อความภายนอก", "",
        f"Generated: `{ticket.get('generated_at')}`  ", f"Contract: `{ticket.get('contract')}`  ",
        f"Status: `{ticket.get('status')}`", "",
    ]
    if not candidate:
        lines.append("_ไม่พบ candidate ที่ปลอดภัยสำหรับ queue_\n")
        return "\n".join(lines)
    lines.extend(
        [
            "## Selected candidate", "", f"- Task: `#{task.get('id')}` — {task.get('title')}",
            f"- Domain: `{candidate.get('domain')}`", f"- Workspace: `{target.get('path')}` (`{target.get('state')}`)",
            f"- Context: {', '.join(f'`{item}`' for item in candidate.get('context_files') or []) or 'none found'}", "",
            "## Agent prompt", "", "```text", candidate.get("agent_prompt") or "", "```", "",
            "## Stop conditions", "",
        ]
    )
    lines.extend(f"- {item}" for item in ticket.get("stop_conditions") or [])
    lines.extend(["", f"Next action: {ticket.get('next_action')}", ""])
    return "\n".join(lines)


def queue_execution(report: dict[str, Any], execution_dir: Path, *, write: bool = True, force: bool = False) -> dict[str, Any]:
    candidates = report.get("candidates") or []
    candidate = candidates[0] if candidates else None
    current_path = execution_dir / "current.json"
    previous: dict[str, Any] | None = None
    if current_path.is_file():
        try:
            loaded = json.loads(current_path.read_text(encoding="utf-8"))
            previous = loaded if isinstance(loaded, dict) else None
        except (OSError, json.JSONDecodeError):
            previous = None
    if candidate is None:
        ticket = {
            "contract": EXECUTION_CONTRACT, "domain": (report.get("domain") or {}).get("slug"),
            "generated_at": _now_iso(), "status": "skipped_no_candidate", "selected": None,
            "policy": {"max_candidates": 1, "automatic_external_writes": False},
            "next_action": "รอ task ที่ match domain adapter หรือเพิ่ม adapter mapping ที่ตรวจสอบแล้ว",
        }
    else:
        fingerprint = _candidate_fingerprint(candidate)
        if not force and previous and previous.get("status") in {"queued", "in_progress"} and previous.get("fingerprint") == fingerprint:
            ticket = dict(previous)
            ticket["status"], ticket["generated_at"] = "skipped_existing_open", _now_iso()
        else:
            ticket = {
                "contract": EXECUTION_CONTRACT, "domain": (report.get("domain") or {}).get("slug"),
                "generated_at": _now_iso(), "status": "queued", "fingerprint": fingerprint,
                "selected": candidate,
                "policy": {
                    "max_candidates": 1, "automatic_external_writes": False,
                    "task_database_writes": False, "human_approval_required_for_irreversible_actions": True,
                },
                "stop_conditions": [
                    "ทำงานเพียง candidate เดียว", "หยุดเมื่อ verification เสร็จหรือมี blocker",
                    "หยุดก่อน external write, credential use, spending, publish, deploy, commit หรือ push",
                ],
                "next_action": f"อ่าน ticket และทำงานใน {candidate['target']['path']} เท่านั้น",
            }
    if not write:
        return ticket
    execution_dir.mkdir(parents=True, exist_ok=True)
    history_dir = execution_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path, state_path = execution_dir / "current.json", execution_dir / "current.md", execution_dir / "state.json"
    payload = json.dumps(ticket, ensure_ascii=False, indent=2) + "\n"
    json_path.write_text(payload, encoding="utf-8")
    markdown_path.write_text(_render_execution_markdown(ticket), encoding="utf-8")
    history_path = history_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    history_path.write_text(payload, encoding="utf-8")
    ticket["outputs"] = {
        "json": _path_label(json_path), "markdown": _path_label(markdown_path),
        "state": _path_label(state_path), "history": _path_label(history_path),
    }
    json_path.write_text(json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "contract": f"{EXECUTION_CONTRACT}-state", "status": ticket.get("status"),
                "domain": ticket.get("domain"), "fingerprint": ticket.get("fingerprint"),
                "current_json": ticket["outputs"]["json"], "current_markdown": ticket["outputs"]["markdown"],
            }, ensure_ascii=False, indent=2
        ) + "\n", encoding="utf-8"
    )
    return ticket


def run_loop(
    *, domain: str, output_dir: Path | None = None,
    config_path: Path = REGISTRY_PATH, free_resource_path: Path = FREE_RESOURCE_REGISTRY,
    max_tasks: int = 5, max_tools: int = 5, write: bool = False,
    queue_execution_ticket: bool = False, force_execution: bool = False,
) -> dict[str, Any]:
    if max_tasks < 1 or max_tasks > 20:
        raise ValueError("max_tasks must be between 1 and 20")
    if max_tools < 1 or max_tools > 20:
        raise ValueError("max_tools must be between 1 and 20")
    registry = load_workflow_registry(config_path)
    profile = load_profile(domain, registry)
    workspace = inspect_workspace(workspace_path(domain))
    tools, missing_tool_ids = recommend_tools(profile, load_registry(free_resource_path), max_tools=max_tools)
    tasks = load_domain_tasks(profile)
    report = build_report(profile, tasks, workspace, tools, missing_tool_ids=missing_tool_ids, max_tasks=max_tasks)
    if write:
        target_dir = output_dir or (DEFAULT_OUTPUT_ROOT / domain)
        report["outputs"] = write_outputs(report, target_dir)
        if queue_execution_ticket:
            ticket = queue_execution(report, target_dir / "execution", write=True, force=force_execution)
            report["execution"] = {
                "status": ticket.get("status"), "outputs": ticket.get("outputs"),
                "task_id": ((ticket.get("selected") or {}).get("task") or {}).get("id"),
            }
    elif queue_execution_ticket:
        raise ValueError("--queue-execution requires --write")
    return report


def run_all(
    *, output_dir: Path | None = None,
    config_path: Path = REGISTRY_PATH, free_resource_path: Path = FREE_RESOURCE_REGISTRY,
    max_tasks: int = 5, max_tools: int = 5, write: bool = False,
) -> dict[str, Any]:
    """Run every active domain without queuing execution tickets."""

    if output_dir is not None and not write:
        raise ValueError("--output-dir with --all requires --write")
    registry = load_workflow_registry(config_path)
    reports = []
    target_root = output_dir or DEFAULT_OUTPUT_ROOT
    for item in list_domains(registry):
        reports.append(
            run_loop(
                domain=item["slug"],
                output_dir=(target_root / item["slug"]) if write else None,
                config_path=config_path, free_resource_path=free_resource_path,
                max_tasks=max_tasks, max_tools=max_tools, write=write,
            )
        )
    index = build_all_report(reports)
    if write:
        index["outputs"] = write_all_index(index, target_root)
    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Book Dev domain adapter loop")
    parser.add_argument("--domain", help="registered workspace slug, for example book-ai")
    parser.add_argument("--list", action="store_true", help="list active domains and adapter status")
    parser.add_argument("--all", dest="run_all", action="store_true", help="run a read-only portfolio scan for every active domain")
    parser.add_argument("--write", action="store_true", help="write the local handoff packet")
    parser.add_argument("--queue-execution", action="store_true", help="queue one candidate execution ticket")
    parser.add_argument("--force-execution", action="store_true", help="replace an open ticket")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--max-tasks", type=int, default=5)
    parser.add_argument("--max-tools", type=int, default=5)
    parser.add_argument("--config", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--free-resource-registry", type=Path, default=FREE_RESOURCE_REGISTRY)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        registry = load_workflow_registry(args.config)
        if sum(bool(value) for value in (args.list, args.run_all, args.domain)) > 1:
            raise ValueError("use only one of --list, --all, or --domain")
        if args.list:
            payload: Any = {"contract": CONTRACT, "domains": list_domains(registry)}
        elif args.run_all:
            payload = run_all(
                output_dir=args.output_dir,
                config_path=args.config, free_resource_path=args.free_resource_registry,
                max_tasks=args.max_tasks, max_tools=args.max_tools, write=args.write,
            )
        else:
            if not args.domain:
                raise ValueError("--domain is required unless --list is used")
            payload = run_loop(
                domain=args.domain, output_dir=args.output_dir,
                config_path=args.config, free_resource_path=args.free_resource_registry,
                max_tasks=args.max_tasks, max_tools=args.max_tools, write=args.write,
                queue_execution_ticket=args.queue_execution, force_execution=args.force_execution,
            )
    except (OSError, KeyError, ValueError, yaml.YAMLError) as exc:
        print(json.dumps({"contract": CONTRACT, "status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.list:
        print("Book Dev domain adapters:")
        for item in payload["domains"]:
            print(f"- {item['slug']}: {item['name']} [{item['adapter']}] -> {item['path']}")
    elif args.run_all:
        summary = payload["summary"]
        print(
            f"Book Dev domain portfolio: {summary['domain_count']} domain(s) | "
            f"{summary['candidate_count']} candidate(s) | "
            f"{summary['tool_recommendation_count']} free/OSS recommendation(s)"
        )
        if args.write:
            print(json.dumps(payload.get("outputs", {}), ensure_ascii=False))
    else:
        summary = payload["summary"]
        print(
            f"Book Dev domain loop: {payload['domain']['slug']} | "
            f"{summary['candidate_count']} candidate(s) | "
            f"{summary['tool_recommendation_count']} free/OSS tool(s)"
        )
        if args.write:
            print(json.dumps(payload.get("outputs", {}), ensure_ascii=False))
        if payload.get("execution"):
            print(json.dumps(payload["execution"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

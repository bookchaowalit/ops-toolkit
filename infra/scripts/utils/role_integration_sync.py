#!/usr/bin/env python3
"""Sync atomic-role learning links and reviewable task-routing suggestions."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

from _env import PROJECT_ROOT, repo_file, ARCHITECTURE_ROOT, display_path  # noqa: E402
from cell_store import CellStoreError, open_cell_store  # noqa: E402
from task_store import ClientConfigError, open_task_store  # noqa: E402


ROLE_CATALOG = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-CATALOG.yaml"
ROLE_LEARNING = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-LEARNING.yaml"
ROLE_ROUTING = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-TASK-ROUTING.yaml"
ROLE_PATHS = ARCHITECTURE_ROOT / "docs" / "registries" / "ROLE-PATHS.yaml"
# These values are stable projection identifiers stored in the roles-skills cell. They are
# deliberately independent from the current filesystem location of the YAML
# sources so a category move does not orphan existing links.
ROLE_SKILL_SOURCE = "docs/ROLE-LEARNING.yaml"
TASK_SKILL_SOURCE = "docs/ROLE-LEARNING.yaml"
TASK_ROLE_SOURCE = "docs/ROLE-TASK-ROUTING.yaml"
ROLE_PATH_SOURCE = "docs/ROLE-PATHS.yaml"

AUTOMATION = "role-router"
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}

# Aliases keep task-to-learning matching useful without pretending that every
# task title contains the canonical skill slug.
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "business-strategy-skill": ("business strategy", "strategy"),
    "unit-economics": ("unit economics", "finance", "accounting"),
    "software-architecture": ("software architecture", "architecture"),
    "data-engineering": ("data engineering", "data lake", "data pipeline"),
    "data-analytics": ("data analytics", "analytics", "analysis"),
    "machine-learning": ("machine learning", "ai / ml", "ai/ml"),
    "nlp-llm": ("nlp", "llm"),
    "ai-agents": ("ai agents", "agent"),
    "seo-content": ("seo", "marketing"),
    "social-media": ("social media", "social"),
    "consultative-selling": ("consultative selling", "sales"),
    "technical-writing": ("technical writing", "documentation", "writing"),
    "ui-ux": ("ui/ux", "ux", "user experience", "design"),
}


def normalize(value: object) -> str:
    text = str(value or "").lower().replace("/", " ")
    return re.sub(r"\s+", " ", text).strip()


def load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing registry: {display_path(path)}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {display_path(path)}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"registry must be a mapping: {display_path(path)}")
    return data


def validate_registries() -> tuple[
    dict, dict, dict, dict, dict[str, dict], dict[str, dict]
]:
    catalog = load_yaml(ROLE_CATALOG)
    learning = load_yaml(ROLE_LEARNING)
    routing = load_yaml(ROLE_ROUTING)
    paths = load_yaml(ROLE_PATHS)

    catalog_rows = catalog.get("roles", [])
    learning_rows = learning.get("roles", [])
    routing_rows = routing.get("rules", [])
    catalog_by_id = {
        row.get("id"): row for row in catalog_rows if isinstance(row, dict) and row.get("id")
    }
    learning_by_id = {
        row.get("role_id"): row for row in learning_rows if isinstance(row, dict) and row.get("role_id")
    }
    routing_by_id = {
        row.get("role_id"): row for row in routing_rows if isinstance(row, dict) and row.get("role_id")
    }
    role_ids = set(catalog_by_id)

    if set(learning_by_id) != role_ids:
        missing = sorted(role_ids - set(learning_by_id))
        extra = sorted(set(learning_by_id) - role_ids)
        raise ValueError(f"learning role IDs do not match catalog; missing={missing}, extra={extra}")
    if set(routing_by_id) != role_ids:
        missing = sorted(role_ids - set(routing_by_id))
        extra = sorted(set(routing_by_id) - role_ids)
        raise ValueError(f"routing role IDs do not match catalog; missing={missing}, extra={extra}")

    path_rows = paths.get("roles", [])
    path_by_id = {
        row.get("role_id"): row for row in path_rows if isinstance(row, dict) and row.get("role_id")
    }
    if set(path_by_id) != role_ids:
        missing = sorted(role_ids - set(path_by_id))
        extra = sorted(set(path_by_id) - role_ids)
        raise ValueError(f"path role IDs do not match catalog; missing={missing}, extra={extra}")

    phase_ids = [row.get("id") for row in paths.get("goal_model", {}).get("phases", [])]
    if phase_ids != ["orient", "learn", "practice", "evidence", "operate"]:
        raise ValueError("ROLE-PATHS.yaml must declare the five ordered path phases")
    for role_id, row in path_by_id.items():
        if not str(row.get("practice_action", "")).strip():
            raise ValueError(f"{role_id}: practice_action must be non-empty")
        if not str(row.get("success_signal", "")).strip():
            raise ValueError(f"{role_id}: success_signal must be non-empty")
        artifacts = row.get("evidence_artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError(f"{role_id}: evidence_artifacts must be non-empty")
        for artifact in artifacts:
            artifact_path = repo_file(str(artifact))
            if not artifact_path.is_file():
                raise ValueError(f"{role_id}: missing evidence artifact: {artifact}")

    default_paths = learning.get("default_learning_paths", [])
    if not isinstance(default_paths, list) or not default_paths:
        raise ValueError("ROLE-LEARNING.yaml must declare default_learning_paths")
    for path_value in default_paths:
        path = repo_file(str(path_value))
        if not path.is_file():
            raise ValueError(f"missing default learning path: {path_value}")

    for role_id, row in learning_by_id.items():
        skill_slugs = row.get("skill_slugs")
        if not isinstance(skill_slugs, list) or not skill_slugs:
            raise ValueError(f"{role_id}: skill_slugs must be non-empty")
        for path_value in row.get("learning_paths", []):
            path = repo_file(str(path_value))
            if not path.is_file():
                raise ValueError(f"{role_id}: missing learning path: {path_value}")

    for role_id, row in routing_by_id.items():
        terms = row.get("terms")
        if not isinstance(terms, list) or not terms:
            raise ValueError(f"{role_id}: routing terms must be non-empty")

    return catalog, learning, routing, paths, catalog_by_id, learning_by_id


def task_text(row: dict) -> str:
    tags = row["tags"]
    if isinstance(tags, list):  # task store records carry tags as a list
        tags = json.dumps(tags) if tags else ""
    fields = [row["title"], row["description"], row["category"], row["project"], tags]
    return normalize(" ".join(str(value or "") for value in fields))


def skill_terms(slug: str, name: str) -> tuple[str, ...]:
    terms = {normalize(slug.replace("-", " ")), normalize(name)}
    terms.update(SKILL_ALIASES.get(slug, ()))
    return tuple(sorted((term for term in terms if len(term) >= 4), key=len, reverse=True))


def matching_skills(task: dict, skills: dict[str, dict]) -> list[tuple[str, str]]:
    text = task_text(task)
    matches: list[tuple[str, str]] = []
    for slug, skill in skills.items():
        for term in skill_terms(slug, skill["name"]):
            if term in text:
                matches.append((slug, f"matched skill term: {term}"))
                break
    return matches


def matching_roles(task: dict, routing_by_id: dict[str, dict]) -> dict[str, tuple[str, str]]:
    text = task_text(task)
    matched: dict[str, tuple[str, str]] = {}
    for role_id, rule in routing_by_id.items():
        terms = [normalize(term) for term in rule.get("terms", [])]
        hits = [term for term in terms if term and term in text]
        if not hits:
            continue
        confidence = "high" if len(hits) >= 2 else "medium"
        matched[role_id] = (confidence, "matched routing terms: " + ", ".join(hits[:4]))
    return matched


def sync_cell_rows(cell, role_skill_rows: list[tuple], role_path_rows: list[tuple]) -> None:
    """Make the generated role-skill links and path steps in the cell equal the registry projection."""
    skill_fields = ("role_id", "skill_id", "learning_path", "link_type", "source_registry")
    wanted_links = {(row[0], row[1]): dict(zip(skill_fields, row)) for row in role_skill_rows}
    existing_links = {
        (row["role_id"], row["skill_id"]): row
        for row in cell.list("role-skill-links")
        if row.get("source_registry") == ROLE_SKILL_SOURCE
    }
    for key, row in existing_links.items():
        if key not in wanted_links:
            cell.delete("role-skill-links", row["id"])
    for key, fields in wanted_links.items():
        current = existing_links.get(key)
        if current is None:
            cell.create("role-skill-links", fields)
        elif any(current.get(name) != value for name, value in fields.items()):
            cell.update("role-skill-links", current["id"], fields)

    step_fields = ("role_id", "step_key", "phase_id", "title", "action", "completion_rule",
                   "skill_id", "target_progress", "evidence_path", "source_registry")
    wanted_steps = {(row[0], row[1]): dict(zip(step_fields, row)) for row in role_path_rows}
    existing_steps = {
        (row["role_id"], row["step_key"]): row
        for row in cell.list("role-path-steps")
        if row.get("source_registry") == ROLE_PATH_SOURCE
    }
    for key, row in existing_steps.items():
        if key not in wanted_steps:
            cell.delete("role-path-steps", row["id"])
    for key, fields in wanted_steps.items():
        current = existing_steps.get(key)
        if current is None:
            cell.create("role-path-steps", fields)
        elif any(current.get(name) != value for name, value in fields.items()):
            cell.update("role-path-steps", current["id"], fields)


def sync(args: argparse.Namespace) -> int:
    _, learning, routing, paths, catalog_by_id, learning_by_id = validate_registries()
    roles_skills = open_cell_store("roles-skills", AUTOMATION)

    skills = {
        row["slug"]: {"id": row["id"], "name": row["name"]}
        for row in roles_skills.list("skills")
    }
    for role_id, row in learning_by_id.items():
        for slug in row["skill_slugs"]:
            if slug not in skills:
                raise ValueError(f"{role_id}: unknown skill slug: {slug}")

    default_paths = learning["default_learning_paths"]
    path_defaults = paths["defaults"]
    phase_rows = {
        row["id"]: row
        for row in paths["goal_model"]["phases"]
        if isinstance(row, dict) and row.get("id")
    }
    role_skill_rows: list[tuple[str, int, str, str, str]] = []
    for role_id, row in learning_by_id.items():
        learning_path_rows = row.get("learning_paths") or default_paths
        path_value = str(learning_path_rows[0])
        for index, slug in enumerate(row["skill_slugs"]):
            role_skill_rows.append(
                (
                    role_id,
                    skills[slug]["id"],
                    path_value,
                    "core" if index == 0 else "supporting",
                    ROLE_SKILL_SOURCE,
                )
            )

    path_by_id = {
        row["role_id"]: row
        for row in paths["roles"]
        if isinstance(row, dict) and row.get("role_id")
    }
    role_path_rows: list[
        tuple[str, str, str, str, str, str, int | None, int | None, str, str]
    ] = []
    for role_id, path_row in path_by_id.items():
        role_name = catalog_by_id[role_id]["name"]
        artifacts = "; ".join(str(value) for value in path_row["evidence_artifacts"])
        orient = phase_rows["orient"]
        role_path_rows.append(
            (
                role_id,
                "orient",
                "orient",
                f"กำหนดเป้าหมาย {role_name}",
                str(orient["action"]),
                str(orient["exit_gate"]),
                None,
                None,
                artifacts,
                ROLE_PATH_SOURCE,
            )
        )
        for index, slug in enumerate(learning_by_id[role_id]["skill_slugs"], start=1):
            skill = skills[slug]
            target = (
                int(path_defaults["core_skill_target_percent"])
                if index == 1
                else int(path_defaults["supporting_skill_target_percent"])
            )
            learning_path_rows = learning_by_id[role_id].get("learning_paths") or default_paths
            learning_path = str(learning_path_rows[0])
            learn = phase_rows["learn"]
            role_path_rows.append(
                (
                    role_id,
                    f"learn-{index}",
                    "learn",
                    f"เรียน {skill['name']}",
                    f"{learn['action']} จาก {learning_path}",
                    f"{learn['exit_gate']} ({target}%)",
                    skill["id"],
                    target,
                    learning_path,
                    ROLE_PATH_SOURCE,
                )
            )
        practice = phase_rows["practice"]
        role_path_rows.append(
            (
                role_id,
                "practice",
                "practice",
                f"ฝึกงานจริง: {role_name}",
                str(path_row["practice_action"]),
                str(practice["exit_gate"]),
                None,
                None,
                artifacts,
                ROLE_PATH_SOURCE,
            )
        )
        evidence = phase_rows["evidence"]
        role_path_rows.append(
            (
                role_id,
                "evidence",
                "evidence",
                f"เก็บหลักฐาน: {role_name}",
                str(evidence["action"]),
                f"{evidence['exit_gate']} ({path_row['success_signal']})",
                None,
                None,
                artifacts,
                ROLE_PATH_SOURCE,
            )
        )
        operate = phase_rows["operate"]
        role_path_rows.append(
            (
                role_id,
                "operate",
                "operate",
                f"ทำซ้ำและ review: {role_name}",
                str(operate["action"]),
                str(operate["exit_gate"]),
                None,
                None,
                artifacts,
                ROLE_PATH_SOURCE,
            )
        )

    store = open_task_store(None, AUTOMATION)
    tasks = store.list(include_archived=True)
    role_by_skill: dict[int, list[str]] = defaultdict(list)
    for row in role_skill_rows:
        role_by_skill[row[1]].append(row[0])

    task_skill_rows: list[tuple[int, int, str, str, str]] = []
    role_suggestions: dict[tuple[int, str], tuple[str, str]] = {}
    routing_by_id = {
        row["role_id"]: row
        for row in routing.get("rules", [])
        if isinstance(row, dict) and row.get("role_id")
    }
    task_status_by_id = {task["id"]: task["status"] for task in tasks}
    for task in tasks:
        for slug, reason in matching_skills(task, skills):
            skill_id = skills[slug]["id"]
            task_skill_rows.append((task["id"], skill_id, "suggested", reason, TASK_SKILL_SOURCE))
            for role_id in role_by_skill.get(skill_id, []):
                role_suggestions[(task["id"], role_id)] = (
                    "low",
                    f"linked through learning skill: {slug}",
                )

        for role_id, (confidence, reason) in matching_roles(task, routing_by_id).items():
            key = (task["id"], role_id)
            previous = role_suggestions.get(key)
            if not previous or CONFIDENCE_RANK[confidence] > CONFIDENCE_RANK[previous[0]]:
                role_suggestions[key] = (confidence, reason)
            elif previous:
                role_suggestions[key] = (previous[0], previous[1] + "; " + reason)

    filtered_suggestions = {
        key: value
        for key, value in role_suggestions.items()
        if args.include_history or task_status_by_id[key[0]] in {"todo", "in_progress", "blocked"}
    }
    role_suggestion_rows = [
        (task_id, role_id, confidence, reason, "suggested", TASK_ROLE_SOURCE)
        for (task_id, role_id), (confidence, reason) in sorted(filtered_suggestions.items())
    ]

    print("=" * 70)
    print("Solo Empire — Atomic Role Integration Sync")
    print("=" * 70)
    print(f"  roles: {len(catalog_by_id)}")
    print(f"  role-skill links: {len(role_skill_rows)}")
    print(f"  tasks scanned: {len(tasks)}")
    print(f"  task-skill links: {len(task_skill_rows)}")
    print(f"  task-role suggestions: {len(role_suggestion_rows)}")
    print(f"  role path steps: {len(role_path_rows)}")
    print(f"  suggestion scope: {'all task history' if args.include_history else 'active tasks only'}")

    if args.dry_run:
        print("Result: DRY RUN (no database changes)")
        return 0

    try:
        sync_cell_rows(roles_skills, role_skill_rows, role_path_rows)
        existing_skill_links = {
            (link["task"], link["target_id"]): link
            for link in store.links(target_type="skill")
            if link["attributes"].get("source_registry") == TASK_SKILL_SOURCE
        }
        for task_id, skill_id, link_type, reason, source_registry in task_skill_rows:
            # An existing link keeps its link_type (suggested/accepted/dismissed); only the reason refreshes.
            attributes = {"match_reason": reason, "source_registry": source_registry}
            if (task_id, skill_id) not in existing_skill_links:
                attributes["link_type"] = link_type
            store.link(task_id, "skill", skill_id, attributes, update=True)
        generated_task_skill_keys = {(row[0], row[1]) for row in task_skill_rows}
        for key, link in existing_skill_links.items():
            if link["attributes"].get("link_type") == "suggested" and key not in generated_task_skill_keys:
                # A generated suggestion that no longer matches is withdrawn; reviewed links stay.
                store.unlink(key[0], "skill", key[1], source_registry=TASK_SKILL_SOURCE)
        for task_id, role_id, confidence, reason, _review_status, source_registry in role_suggestion_rows:
            # review_status is not passed, so an existing accepted/dismissed review is kept.
            store.suggest_role(task_id, role_id, source_registry, confidence, reason)
        generated_suggestion_keys = {(row[0], row[1]) for row in role_suggestion_rows}
        for row in store.role_suggestions(source_registry=TASK_ROLE_SOURCE, review_status="suggested"):
            if (row["task"], row["role_id"]) not in generated_suggestion_keys:
                store.withdraw_role(row["task"], row["role_id"], TASK_ROLE_SOURCE, only_status="suggested")
    except CellStoreError as exc:
        raise ValueError(str(exc)) from exc

    print("Result: OK — role learning links and reviewable task suggestions synced")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="include done/cancelled tasks in task-role suggestions",
    )
    parser.add_argument("--dry-run", action="store_true", help="report links without writing the database")
    args = parser.parse_args()
    try:
        return sync(args)
    except (OSError, ValueError, ClientConfigError) as exc:
        print(f"Result: FAIL — {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

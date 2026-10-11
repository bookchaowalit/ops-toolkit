#!/usr/bin/env python3
"""Project existing life, venture, freelance, and role data into portfolio contexts.

Contexts answer "where does this belong?" and personal roles answer "which
personal identity is exercised?". Neither replaces the professional role
catalog. The projection is intentionally idempotent and never deletes or
rewrites existing goals, projects, tasks, or role links.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, ARCHITECTURE_ROOT, REPORTS_DIR, display_path
from cell_store import CellStoreError, open_cell_store
from role_portfolio_seed import _DRY_RUN as _ROLE_SEED_DRY_RUN, _DryRunCell, _DryRunTaskStore
from task_store import ClientConfigError, open_task_store


CONTEXT_REGISTRY = ARCHITECTURE_ROOT / "docs" / "registries" / "PORTFOLIO-CONTEXTS.yaml"
PERSONAL_ROLE_REGISTRY = ARCHITECTURE_ROOT / "docs" / "registries" / "PERSONAL-ROLE-CATALOG.yaml"
WORKSPACE_REGISTRY = KNOWLEDGE_ROOT / "projects" / "workspaces.yaml"
REPORT_PATH = REPORTS_DIR / "PORTFOLIO-CONTEXTS.md"
# Keep these SQLite projection identifiers stable across filesystem/category
# moves. They are provenance keys, not paths used to load the registries.
AUTOMATION_ID = "portfolio-seeder"
SOURCE = "docs/PORTFOLIO-CONTEXTS.yaml#context-projection-v1"
PERSONAL_ROLE_SOURCE = "docs/PERSONAL-ROLE-CATALOG.yaml#personal-role-projection-v1"
PERSONAL_ROLE_BASELINE_SOURCE = "docs/PERSONAL-ROLE-CATALOG.yaml#personal-role-baseline-v1"


_DRY_RUN = {"on": False}
_CONTEXTS_BY_SLUG: dict[str, dict] = {}  # filled once per seed() run

# link resource -> (cell, natural key); every link resource is unique on its key
_LINKS = {
    "goal-project-links": ("goals", ("goal_id", "project_id")),
    "goal-task-project-links": ("goals", ("goal_task_id", "project_id")),
    "goal-context-links": ("goals", ("goal_id", "context_id")),
    "project-context-links": ("projects", ("project_id", "context_id")),
    "work-item-goal-task-links": ("projects", ("work_item_id", "goal_task_id")),
    "personal-role-context-links": ("roles-skills", ("personal_role_id", "context_id")),
    "personal-role-goal-links": ("roles-skills", ("personal_role_id", "goal_id")),
    "personal-role-project-links": ("roles-skills", ("personal_role_id", "project_id")),
    "personal-role-task-links": ("roles-skills", ("personal_role_id", "task_id")),
}


def _cell(name: str):
    store = open_cell_store(name, AUTOMATION_ID)
    return _DryRunCell(store) if _DRY_RUN["on"] else store


def _task_store():
    store = open_task_store(None, AUTOMATION_ID)
    return _DryRunTaskStore(store) if _DRY_RUN["on"] else store


class LinkIndex:
    """Creates link rows once per natural key (the old INSERT OR IGNORE): each resource is listed once."""

    def __init__(self) -> None:
        self._known: dict[str, set[tuple]] = {}

    def _keys(self, plural: str) -> set[tuple]:
        if plural not in self._known:
            cell, keys = _LINKS[plural]
            self._known[plural] = {tuple(row[k] for k in keys) for row in _cell(cell).list(plural)}
        return self._known[plural]

    def create(self, plural: str, fields: dict) -> bool:
        cell, keys = _LINKS[plural]
        known = self._keys(plural)
        key = tuple(fields[k] for k in keys)
        if key in known:
            return False
        try:
            _cell(cell).create(plural, fields)
        except CellStoreError as exc:
            if exc.status != 409:
                raise
            known.add(key)
            return False
        known.add(key)
        return True

    def forget(self, plural: str, key: tuple) -> None:
        self._keys(plural).discard(key)


def _project_ids() -> dict[str, int]:
    return {row["slug"]: row["id"] for row in _cell("projects").list("projects")}


def _present(fields: dict) -> dict:
    return {k: v for k, v in fields.items() if v is not None}


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"registry must be a mapping: {display_path(path)}")
    return value


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if normalized:
        return normalized[:72]
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"value-{digest}"


def context_slug_for_legacy(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"legacy-task-project-{digest}"


def ensure_context(values: dict,
    parent_id: int | None = None,
) -> int:
    contexts = _cell("projects")
    if values["slug"] not in _CONTEXTS_BY_SLUG:  # list again only on a miss, not once per context
        _CONTEXTS_BY_SLUG.update({row["slug"]: row for row in contexts.list("portfolio-contexts")})
    existing = _CONTEXTS_BY_SLUG.get(values["slug"])
    if existing:
        if existing.get("source") != "manual":
            contexts.update("portfolio-contexts", existing["id"], {
                "name": values["name"], "context_type": values["context_type"],
                "scope": values.get("scope", "personal"), "parent_context_id": parent_id,
                "description": values.get("description"), "source_path": values.get("source_path"),
                "status": values.get("status", "active"),
            })
        return int(existing["id"])
    row = contexts.create("portfolio-contexts", _present({
        "slug": values["slug"], "name": values["name"], "context_type": values["context_type"],
        "scope": values.get("scope", "personal"), "parent_context_id": parent_id,
        "description": values.get("description"), "source_path": values.get("source_path"),
        "source": SOURCE, "status": values.get("status", "active"),
    }))
    _CONTEXTS_BY_SLUG[values["slug"]] = row
    return int(row["id"])


def ensure_personal_role(values: dict) -> int:
    roles = _cell("roles-skills")
    existing = next((row for row in roles.list("personal-roles") if row["slug"] == values["id"]), None)
    source_path = "; ".join(str(path) for path in values.get("source_paths", []))
    if existing:
        if existing.get("source") != "manual":
            roles.update("personal-roles", existing["id"], {
                "name": values["name"], "category": values["category"], "description": values.get("summary"),
                "source_path": source_path, "status": values.get("status", "active"),
            })
        return int(existing["id"])
    row = roles.create("personal-roles", _present({
        "slug": values["id"], "name": values["name"], "category": values["category"],
        "description": values.get("summary"), "source_path": source_path, "source": PERSONAL_ROLE_SOURCE,
        "status": values.get("status", "active"),
    }))
    return int(row["id"])


def personal_goal_category(role: dict) -> str:
    return {
        "health": "health",
        "learning": "life",
        "culture": "life",
        "mind": "life",
        "creative": "creative",
        "finance": "financial",
        "entrepreneurship": "business",
        "lifestyle": "life",
        "life-operations": "life",
    }.get(str(role.get("category", "")), "life")


def personal_project_candidates(role: dict) -> list[str]:
    context_projects = {
        "personal-health": "book-health",
        "personal-sports": "book-sports",
        "personal-travel": "book-travel",
        "personal-learning": "book-learning",
        "personal-languages": "book-languages",
        "personal-history": "book-history",
        "personal-psychology": "book-psychology",
        "personal-philosophy": "book-philosophy",
        "personal-religion": "book-religion",
        "personal-medicine": "book-medicine",
        "personal-creative": "book-creative",
        "personal-hobbies": "book-media",
        "hobby-music": "book-music",
        "hobby-games": "book-games",
        "hobby-cooking": "book-cooking",
        "personal-finance": "book-finance",
        "personal-diary": "solo-empire",
        "solo-empire-internal": "solo-empire",
        "startup-portfolio": "personal-business",
        "freelance-portfolio": "personal-business",
        "client-engagements": "client-work",
        "personal-life": "book-life",
    }
    candidates = [
        context_projects[context]
        for context in role.get("contexts", [])
        if context in context_projects
    ]
    if role.get("category") == "entrepreneurship":
        candidates.append("personal-business")
    candidates.append("solo-empire")
    return list(dict.fromkeys(candidates))


def ensure_personal_role_baselines(role_registry: dict,
    role_ids: dict[str, int],
    context_ids: dict[str, int],
    links: LinkIndex | None = None,
) -> dict[str, int]:
    """Give every personal role a non-destructive Goal and canonical Task baseline."""
    counts = defaultdict(int)
    links = links or LinkIndex()
    goals_cell, projects_cell, roles_cell = _cell("goals"), _cell("projects"), _cell("roles-skills")
    project_ids = _project_ids()
    projects_by_id = {row["id"]: row for row in projects_cell.list("projects")}
    for role in role_registry.get("roles", []):
        role_id = role_ids[role["id"]]
        context_slugs = [
            slug for slug in role.get("contexts", []) if slug in context_ids
        ]
        if not context_slugs:
            continue

        linked_goals = sorted(
            link["goal_id"] for link in roles_cell.list("personal-role-goal-links", personal_role_id=role_id)
            if goals_cell.get("goals", link["goal_id"]) is not None
        )
        if linked_goals:
            goal_id = int(linked_goals[0])
        else:
            primary_context = context_slugs[0]
            goal = goals_cell.create("goals", {
                "title": f"Build personal practice: {role['name']}",
                "description": (
                    f"Develop the personal identity of {role['name']} through a small, "
                    f"repeatable, evidence-backed practice. {role.get('summary', '')} "
                    "This Goal can progress continuously alongside every other Goal."
                ),
                "type": "yearly",
                "category": personal_goal_category(role),
                "status": "planning",
                "priority": 4,
                "start_date": datetime.now().date().isoformat(),
                "progress_percent": 0,
                "key_results": json.dumps(
                    [
                        "Define a sustainable practice",
                        "Complete one bounded evidence-producing activity",
                        "Review the next improvement step",
                    ],
                    ensure_ascii=False,
                ),
                "notes": "Generated baseline for a personal role with no linked Goal.",
                "domain": f"personal-role:{primary_context}",
            })
            goal_id = int(goal["id"])
            counts["personal_role_goals_created"] += 1
            links.create("personal-role-goal-links", {
                "personal_role_id": role_id, "goal_id": goal_id, "link_type": "primary",
                "source": PERSONAL_ROLE_BASELINE_SOURCE,
            })

        goal_links = [
            link for link in goals_cell.list("goal-project-links", goal_id=goal_id)
            if link["project_id"] in projects_by_id
        ]
        if goal_links:
            first = min(goal_links, key=lambda link: (link["relation_type"] != "primary", link["project_id"]))
            project_id = int(first["project_id"])
            project_slug = str(projects_by_id[project_id]["slug"])
        else:
            project_slug = next(
                (slug for slug in personal_project_candidates(role) if slug in project_ids),
                "solo-empire",
            )
            project_id = project_ids.get(project_slug)
            if project_id is None:
                continue
            links.create("goal-project-links", {
                "goal_id": goal_id, "project_id": project_id, "relation_type": "delivery", "weight": 1,
                "source": PERSONAL_ROLE_BASELINE_SOURCE,
            })

        task_id = None
        has_role_task = roles_cell.list("personal-role-task-links", personal_role_id=role_id)
        if not has_role_task:
            now = datetime.now().isoformat(timespec="seconds")
            task_title = f"Personal role baseline: {role['name']}"
            store = _task_store()
            goal_task_ids = {
                int(link["task"])
                for link in store.links(target_type="goal", target_id=goal_id)
            }
            task_row = next(
                (
                    task for task in store.list(include_archived=True)
                    if task["title"] == task_title and int(task["id"]) in goal_task_ids
                ),
                None,
            )
            if task_row:
                task_id = int(task_row["id"])
            else:
                task_id = int(store.create(
                    {
                        "title": task_title,
                        "description": (
                            f"Start a bounded practice for {role['name']}: define the "
                            "first action, complete it, and capture evidence."
                        ),
                        "status": "todo",
                        "priority": 4,
                        "project": project_slug,
                        "tags": json.dumps(
                            [
                                "personal-role-baseline",
                                f"personal-role:{role['id']}",
                                f"goal:{goal_id}",
                            ],
                            ensure_ascii=False,
                        ),
                        "category": "learning",
                        "notes": f"source={PERSONAL_ROLE_BASELINE_SOURCE}; role={role['id']}; goal={goal_id}",
                        "estimated_minutes": 60,
                    },
                    legacy={"sync_status": "local", "created_at": now, "updated_at": now},
                ))
                store.link(
                    task_id, "goal", goal_id,
                    {"link_type": "primary", "source": PERSONAL_ROLE_BASELINE_SOURCE}, update=False,
                )
                counts["personal_role_tasks_created"] += 1

        counts["personal_role_goal_links_created"] += int(links.create("personal-role-goal-links", {
            "personal_role_id": role_id, "goal_id": goal_id, "link_type": "primary",
            "source": PERSONAL_ROLE_BASELINE_SOURCE,
        }))
        counts["personal_role_project_links_created"] += int(links.create("personal-role-project-links", {
            "personal_role_id": role_id, "project_id": project_id, "link_type": "primary",
            "source": PERSONAL_ROLE_BASELINE_SOURCE,
        }))
        if task_id is not None:
            counts["personal_role_task_links_created"] += int(links.create("personal-role-task-links", {
                "personal_role_id": role_id, "task_id": task_id, "link_type": "primary",
                "source": PERSONAL_ROLE_BASELINE_SOURCE,
            }))
    return dict(counts)


def context_value(
    slug: str,
    name: str,
    context_type: str,
    scope: str = "personal",
    source_path: str | None = None,
    description: str | None = None,
    status: str = "active",
) -> dict:
    return {
        "slug": slug,
        "name": name,
        "context_type": context_type,
        "scope": scope,
        "source_path": source_path,
        "description": description,
        "status": status,
    }


def ensure_workspace_project(workspace: dict) -> int:
    """Create a project node for a registered workspace when one is missing."""
    slug = str(workspace["slug"])
    existing = _project_ids().get(slug)
    if existing is not None:
        return int(existing)
    bu = str(workspace.get("bu", ""))
    category = "product" if bu == "product" else "internal" if bu in {"marketing", "operations", "money"} else "portfolio"
    status = "active" if workspace.get("status") == "active" else "planning"
    row = _cell("projects").create("projects", {
        "name": str(workspace.get("name", slug)),
        "slug": slug,
        "description": f"Workspace-backed project node for {slug}; context and role links are stored separately.",
        "category": category,
        "type": "workspace",
        "status": status,
        "priority": 3,
        "phase": "context-mapped",
        "domain": slug,
        "notes": str(workspace.get("path", "")),
    })
    return int(row["id"])


def ensure_personal_business_project() -> int:
    existing = _project_ids().get("personal-business")
    if existing is not None:
        return int(existing)
    row = _cell("projects").create("projects", {
        "name": "Personal Business Portfolio",
        "slug": "personal-business",
        "description": "Ongoing personally owned business and income work; not a professional role.",
        "category": "portfolio",
        "type": "portfolio-lane",
        "status": "active",
        "priority": 2,
        "phase": "context-mapped",
        "notes": "Generated to give business goals a stable Project node while preserving their personal ownership.",
    })
    return int(row["id"])


def seed_contexts() -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    registry = load_yaml(CONTEXT_REGISTRY)
    _CONTEXTS_BY_SLUG.clear()  # each run starts from what the cell holds now
    context_ids: dict[str, int] = {}
    workspace_contexts: dict[str, int] = {}
    venture_contexts: dict[str, int] = {}
    engagement_contexts: dict[str, int] = {}

    explicit = registry.get("explicit_contexts", [])
    for row in explicit:
        context_ids[row["slug"]] = ensure_context(row)

    # Ensure the parent roots used by discovered workspaces exist even if a
    # future registry intentionally removes their explicit documentation row.
    roots = {
        "personal-hobbies": context_value(
            "personal-hobbies", "Personal Hobbies", "hobby", source_path="projects/product/content-media"
        ),
    }
    for slug, row in roots.items():
        context_ids[slug] = ensure_context(row)

    workspaces = load_yaml(WORKSPACE_REGISTRY).get("workspaces", [])
    for workspace in workspaces:
        slug = str(workspace["slug"])
        ensure_workspace_project(workspace)
        bu = str(workspace.get("bu", ""))
        group = str(workspace.get("group", ""))
        special_parents = {
            "book-health": "personal-health",
            "book-sports": "personal-sports",
            "book-travel": "personal-travel",
            "book-learning": "personal-learning",
            "book-languages": "personal-languages",
            "book-history": "personal-history",
            "book-psychology": "personal-psychology",
            "book-philosophy": "personal-philosophy",
            "book-religion": "personal-religion",
            "book-medicine": "personal-medicine",
            "book-cooking": "hobby-cooking",
            "book-games": "hobby-games",
            "book-music": "hobby-music",
            "book-creative": "personal-creative",
            "book-finance": "personal-finance",
        }
        if slug in special_parents:
            parent_slug = special_parents[slug]
            context_type = "hobby" if parent_slug.startswith("hobby-") else "learning" if parent_slug in {
                "personal-learning", "personal-languages", "personal-history", "personal-psychology",
                "personal-philosophy", "personal-medicine",
            } else "personal"
        elif bu == "life":
            parent_slug = "personal-life"
            context_type = "learning" if group in {"learning", "culture", "mind-spirit"} else "personal"
        elif bu == "product" and group == "content-media":
            parent_slug = "personal-hobbies"
            context_type = "hobby"
        elif bu == "product":
            parent_slug = "startup-portfolio"
            context_type = "venture"
        elif bu in {"marketing", "operations", "money"}:
            parent_slug = "personal-business"
            context_type = "business_system"
        else:
            parent_slug = "solo-empire-internal"
            context_type = "workspace"
        parent_id = context_ids[parent_slug]
        context_slug = f"workspace-{slug}"
        context_ids[context_slug] = ensure_context(context_value(
                context_slug,
                str(workspace.get("name", slug)),
                context_type,
                source_path=str(workspace.get("path", "")),
                status="active" if workspace.get("status") == "active" else "planned",
                description=f"Workspace context for {slug}; this is not a professional role.",
            ),
            parent_id,
        )
        workspace_contexts[slug] = context_ids[context_slug]

    ensure_personal_business_project()

    # Some historical goals use a book-* domain that predates the workspace
    # registry (for example book-law and book-education). Keep those goals in
    # the same model by creating a transparent, source-derived context/project
    # node instead of leaving them unlinked.
    known_workspace_domains = set(workspace_contexts)
    goal_domains = sorted({
        str(goal["domain"]) for goal in _cell("goals").list("goals")
        if goal.get("domain") and str(goal["domain"]).startswith("book-")
    })
    for domain in goal_domains:
        if domain in known_workspace_domains:
            continue
        learning_domain = domain in {"book-law", "book-education"} or any(
            term in domain for term in ("learning", "language", "history", "science", "research")
        )
        parent_slug = "personal-learning" if learning_domain else "personal-life"
        context_slug = f"workspace-{slugify(domain)}"
        context_ids[context_slug] = ensure_context(context_value(
                context_slug,
                domain.replace("book-", "").replace("-", " ").title(),
                "learning" if learning_domain else "workspace",
                source_path=f"goals.domain:{domain}",
                description="Historical goal domain projected into the portfolio context model.",
            ),
            context_ids[parent_slug],
        )
        workspace_contexts[domain] = context_ids[context_slug]
        ensure_workspace_project({
                "slug": domain,
                "name": domain.replace("book-", "").replace("-", " ").title(),
                "bu": "life" if learning_domain else "operations",
                "path": f"goals/domain/{domain}",
                "status": "active",
            },
        )

    ventures_root = PROJECT_ROOT / "ventures"
    if ventures_root.is_dir():
        for path in sorted(ventures_root.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            if path.name in {"README.md"}:
                continue
            slug = path.name
            is_freelance = slug.startswith("freelance-")
            parent_slug = "freelance-portfolio" if is_freelance else "startup-portfolio"
            context_slug = f"venture-{slugify(slug)}"
            venture_contexts[slug] = ensure_context(context_value(
                    context_slug,
                    slug.replace("-", " ").title(),
                    "freelance" if is_freelance else "venture",
                    source_path=str(display_path(path)),
                    description="Personally owned venture context; it is not a role.",
                ),
                context_ids[parent_slug],
            )

    # Client and internal engagement checkouts live in Book Dev repositories; the
    # registry keeps the work type, client, and slug the context slugs use.
    registry_path = ARCHITECTURE_ROOT / "docs" / "registries" / "ENGAGEMENT-REGISTRY.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {}
    for record in (registry or {}).get("engagements") or []:
        if record.get("source_kind") == "owned-product-baseline":
            continue
        kind, owner, name = record["work_type"], record["client"], record["slug"]
        engagement_slug = f"engagement-{slugify(kind)}-{slugify(owner)}-{slugify(name)}"
        if kind == "internal":
            parent_slug = "solo-empire-internal"
            context_type = "internal"
            scope = "personal"
        elif kind == "freelance":
            parent_slug = "freelance-portfolio"
            context_type = "client_engagement"
            scope = "client"
        else:
            parent_slug = "client-engagements"
            context_type = "client_engagement"
            scope = "client"
        context_id = ensure_context(context_value(
                engagement_slug,
                f"{kind.title()} Engagement: {name}",
                context_type,
                scope=scope,
                source_path=str(record["source_path"]).rstrip("/"),
                description="A real work engagement; not a role and not a generic project category.",
            ),
            context_ids[parent_slug],
        )
        context_ids[engagement_slug] = context_id
        engagement_contexts[name] = context_id
        engagement_contexts[f"engagements/{name}"] = context_id
    return context_ids, workspace_contexts, venture_contexts | engagement_contexts


def ensure_legacy_context(context_ids: dict[str, int],
    project_value: str,
) -> int:
    known = {
        "💼 Income": ("personal-business", "Personal Income Work", "freelance"),
        "🧠 Life": ("personal-life", "Personal Life Work", "personal"),
        "📖 Growth": ("personal-learning", "Personal Growth and Learning", "learning"),
        "🏋️ Body": ("personal-health", "Personal Body and Health", "personal"),
        "🎨 Creative": ("personal-creative", "Personal Creative Work", "hobby"),
        "🌐 Generalist Paths": ("personal-learning", "Generalist Learning", "learning"),
        "Inbox": ("personal-life", "Personal Inbox", "personal"),
    }
    if project_value in known:
        return context_ids[known[project_value][0]]
    slug = context_slug_for_legacy(project_value)
    if slug not in context_ids:
        parent_slug = "startup-portfolio" if project_value not in {"DCS", "CuteERP"} else "personal-business"
        context_ids[slug] = ensure_context(context_value(
                slug,
                f"Legacy task project: {project_value}",
                "workspace",
                source_path=f"tasks.project:{project_value}",
                description="Imported from the existing task project label; review and refine when the context becomes a formal venture or hobby.",
            ),
            context_ids[parent_slug],
        )
    return context_ids[slug]


def project_contexts(context_ids: dict[str, int],
    workspace_contexts: dict[str, int],
    venture_contexts: dict[str, int],
    engagement_contexts: dict[str, int],
) -> dict[int, list[int]]:
    mapping: dict[int, list[int]] = defaultdict(list)
    for row in _cell("projects").list("projects"):
        values: list[int] = []
        slug = str(row["slug"] or "")
        domain = str(row["domain"] or "")
        if slug == "solo-empire":
            values.append(context_ids["solo-empire-internal"])
        if slug == "personal-business":
            values.append(context_ids["personal-business"])
        if slug == "client-work" or row["category"] == "client":
            values.append(context_ids["client-engagements"])
        if slug in venture_contexts:
            values.append(venture_contexts[slug])
        if domain in workspace_contexts:
            values.append(workspace_contexts[domain])
        if slug in workspace_contexts:
            values.append(workspace_contexts[slug])
        if not values:
            if row["category"] in {"product", "experiment", "portfolio", "open_source"}:
                values.append(context_ids["startup-portfolio"])
            else:
                values.append(context_ids["solo-empire-internal"])
        deduped = list(dict.fromkeys(values))
        mapping[row["id"]] = deduped
    return mapping


def goal_contexts(context_ids: dict[str, int],
    workspace_contexts: dict[str, int],
) -> dict[int, list[int]]:
    mapping: dict[int, list[int]] = defaultdict(list)
    for row in _cell("goals").list("goals"):
        values: list[int] = []
        title = str(row["title"] or "").lower()
        domain = str(row["domain"] or "")
        category = str(row["category"] or "")
        if domain.startswith("role:") or "operating capability" in title:
            values.append(context_ids["professional-role-capability"])
        elif domain.startswith("personal-role:"):
            personal_context = domain.split(":", 1)[1]
            values.append(
                context_ids.get(personal_context, context_ids["personal-life"])
            )
        elif domain in workspace_contexts:
            values.append(workspace_contexts[domain])
        elif category == "health":
            values.append(context_ids["personal-health"])
        elif category == "life":
            values.append(context_ids["personal-life"])
        elif category == "financial":
            values.append(context_ids["personal-finance"])
        elif category == "creative":
            values.append(context_ids["personal-creative"])
        elif category == "business":
            values.append(context_ids["personal-business"])
        elif category == "tech":
            values.append(context_ids["startup-portfolio"])
        else:
            values.append(context_ids["personal-life"])
        if any(term in title for term in ("learn", "เรียน", "study", "certification", "ภาษา", "language")):
            values.append(context_ids["personal-learning"])
        if any(term in title for term in ("startup", "venture", "product", "service", "niche", "audience")):
            values.append(context_ids["startup-portfolio"])
        mapping[row["id"]] = list(dict.fromkeys(values))
    return mapping


def task_contexts(context_ids: dict[str, int],
    workspace_contexts: dict[str, int],
    project_context_map: dict[int, list[int]],
    goal_context_map: dict[int, list[int]],
) -> dict[int, list[int]]:
    project_ids = _project_ids()
    mapping: dict[int, list[int]] = defaultdict(list)
    store = _task_store()
    goals_by_task: dict[int, list[int]] = defaultdict(list)
    for link in store.links(target_type="goal"):
        goals_by_task[link["task"]].append(link["target_id"])
    for row in store.list(include_archived=True):
        values: list[int] = []
        project_value = str(row["project"] or "").strip()
        if project_value in project_ids:
            values.extend(project_context_map.get(project_ids[project_value], []))
        if project_value in workspace_contexts:
            values.append(workspace_contexts[project_value])
        if project_value.startswith("engagements/"):
            matching = [
                context_id for key, context_id in context_ids.items()
                if key.startswith("engagement-") and project_value.split("/")[-1] in key
            ]
            values.extend(matching)
        if project_value and not values:
            values.append(ensure_legacy_context(context_ids, project_value))
        for goal_id in goals_by_task.get(row["id"], []):
            values.extend(goal_context_map.get(goal_id, []))
        if not values:
            category = str(row["category"] or "")
            if category == "learning":
                values.append(context_ids["personal-learning"])
            elif category in {"income", "finance"}:
                values.append(context_ids["personal-business"])
            elif category == "health":
                values.append(context_ids["personal-health"])
            elif category == "creative":
                values.append(context_ids["personal-creative"])
            else:
                values.append(context_ids["personal-life"])
        mapping[row["id"]] = list(dict.fromkeys(values))
    return mapping


def ensure_all_task_project_links() -> int:
    project_ids = _project_ids()
    created = 0
    store = _task_store()
    for row in [task for task in store.list(include_archived=True) if task["project"]]:
        project_id = project_ids.get(str(row["project"]))
        if project_id is None:
            continue
        created += store.link(
            row["id"], "project", project_id,
            {"relation_type": "delivery", "weight": 1, "source": SOURCE}, update=False,
        ) == "created"
    return created


def default_project_slug(goal: dict) -> str:
    domain = str(goal["domain"] or "")
    category = str(goal["category"] or "")
    title = str(goal["title"] or "").lower()
    if domain.startswith("role:"):
        return "solo-empire"
    if domain.startswith("book-"):
        return domain
    if category == "health":
        return "book-health"
    if category == "life":
        return "book-life"
    if category == "financial":
        return "book-finance"
    if category == "creative":
        return "book-creative"
    if category == "business":
        return "personal-business"
    if category == "tech":
        if any(term in title for term in ("infra", "devops", "platform", "cloud")):
            return "book-dev"
        return "book-ai"
    return "personal-business"


def ensure_goal_project_links(goal_map: dict[int, list[int]],
    links: LinkIndex | None = None,
) -> tuple[int, int]:
    """Ensure every existing goal has a stable Project node when possible."""
    links = links or LinkIndex()
    project_ids = _project_ids()
    goals_cell = _cell("goals")
    created = 0
    goal_task_project_created = 0
    store = _task_store()
    projects_by_task: dict[int, list[int]] = defaultdict(list)
    for link in store.links(target_type="project"):
        projects_by_task[link["task"]].append(link["target_id"])
    tasks_by_goal: dict[int, list[int]] = defaultdict(list)
    for link in store.links(target_type="goal"):
        tasks_by_goal[link["target_id"]].append(link["task"])
    existing_by_goal: dict[int, set[int]] = defaultdict(set)
    for linked in goals_cell.list("goal-project-links"):
        existing_by_goal[linked["goal_id"]].add(linked["project_id"])
    goal_tasks_by_goal: dict[int, list[int]] = defaultdict(list)
    for goal_task in goals_cell.list("goal-tasks"):
        goal_tasks_by_goal[goal_task["goal_id"]].append(goal_task["id"])
    for row in goals_cell.list("goals"):
        existing = set(existing_by_goal.get(row["id"], ()))
        # Preserve the concrete task-derived relationship first.
        task_projects = dict.fromkeys(
            project_id
            for task_id in tasks_by_goal.get(row["id"], [])
            for project_id in projects_by_task.get(task_id, [])
        )
        for linked_project_id in task_projects:
            if linked_project_id in existing:
                continue
            created += links.create("goal-project-links", {
                "goal_id": row["id"], "project_id": linked_project_id, "relation_type": "delivery",
                "weight": 1, "source": SOURCE,
            })
            existing.add(linked_project_id)

        project_id = project_ids.get(default_project_slug(row))
        if project_id is not None and project_id not in existing:
            created += links.create("goal-project-links", {
                "goal_id": row["id"], "project_id": project_id,
                "relation_type": "supporting" if existing else "delivery", "weight": 1, "source": SOURCE,
            })
            existing.add(project_id)

        primary_project_id = next(iter(existing), project_id)
        if primary_project_id is None:
            continue
        for goal_task_id in goal_tasks_by_goal.get(row["id"], []):
            goal_task_project_created += links.create("goal-task-project-links", {
                "goal_task_id": goal_task_id, "project_id": primary_project_id, "relation_type": "delivery",
                "weight": 1, "source": SOURCE,
            })
    return created, goal_task_project_created


def goal_task_progress(status: str, explicit: int | None) -> int:
    if status == "done":
        return 100
    if explicit is not None and int(explicit or 0) > 0:
        return max(0, min(100, int(explicit)))
    return {"in_progress": 50, "blocked": 0}.get(status, 0)


def ensure_goal_task_work_items(links: LinkIndex | None = None) -> int:
    """Represent legacy goal_tasks as Story children without duplicating task rows."""
    links = links or LinkIndex()
    goals_cell, projects_cell = _cell("goals"), _cell("projects")
    goal_tasks = {row["id"]: row for row in goals_cell.list("goal-tasks")}
    epics_by_scope: dict[tuple[int, int], list[int]] = defaultdict(list)
    for epic in sorted(projects_cell.list("work-items", item_type="epic"), key=lambda e: e["id"]):
        epics_by_scope[(epic["goal_id"], epic["project_id"])].append(epic["id"])
    stories = {row["id"]: row for row in projects_cell.list("work-items", item_type="story")}
    stories_by_goal_task: dict[tuple[int, int], list[int]] = defaultdict(list)
    for link in projects_cell.list("work-item-goal-task-links"):
        story = stories.get(link["work_item_id"])
        if story is not None:
            stories_by_goal_task[(link["goal_task_id"], story["project_id"])].append(story["id"])
    rows = []
    for gtp in goals_cell.list("goal-task-project-links"):
        goal_task = goal_tasks.get(gtp["goal_task_id"])
        if goal_task is None:
            continue
        for epic_id in epics_by_scope.get((goal_task["goal_id"], gtp["project_id"]), []):
            rows.append((goal_task["goal_id"], goal_task["id"], gtp["project_id"], epic_id, goal_task))
    rows.sort(key=lambda r: r[:4])
    created = 0
    for goal_id, goal_task_id, project_id, epic_id, gt in rows:
        progress = goal_task_progress(gt["status"], gt["progress_percent"])
        existing = sorted(stories_by_goal_task.get((goal_task_id, project_id), []))
        if existing:
            story_id = existing[0]
            story = stories[story_id]
            if story["status"] != gt["status"] or story["progress_percent"] != progress:
                projects_cell.update("work-items", story_id, {"status": gt["status"], "progress_percent": progress})
        else:
            story = projects_cell.create("work-items", {
                "item_type": "story",
                "title": f"Story: {gt['title']}",
                "description": gt["description"] or "Legacy goal task projected into the continuous hierarchy.",
                "goal_id": goal_id,
                "project_id": project_id,
                "parent_item_id": epic_id,
                "status": gt["status"],
                "progress_percent": progress,
                "weight": 1,
                "source_key": f"{SOURCE}:goal-story:{goal_task_id}:{goal_id}:{project_id}",
                "source": SOURCE,
            })
            story_id = int(story["id"])
            stories[story_id] = story
            stories_by_goal_task[(goal_task_id, project_id)].append(story_id)
            created += 1
        links.create("work-item-goal-task-links", {
            "work_item_id": story_id, "goal_task_id": goal_task_id, "relation_type": "implements", "weight": 1,
        })
    return created


def apply_links(context_ids: dict[str, int],
    workspace_contexts: dict[str, int],
    venture_contexts: dict[str, int],
    engagement_contexts: dict[str, int],
) -> dict[str, int]:
    counts = defaultdict(int)
    links = LinkIndex()
    role_registry = load_yaml(PERSONAL_ROLE_REGISTRY)
    role_ids: dict[str, int] = {}
    for role in role_registry.get("roles", []):
        role_ids[role["id"]] = ensure_personal_role(role)
    baseline_counts = ensure_personal_role_baselines(role_registry, role_ids, context_ids, links
    )
    for key, value in baseline_counts.items():
        counts[key] += value

    counts["task_project_links_created"] = ensure_all_task_project_links()
    project_map = project_contexts(context_ids, workspace_contexts, venture_contexts, engagement_contexts)
    goal_map = goal_contexts(context_ids, workspace_contexts)
    goal_project_created, goal_task_project_created = ensure_goal_project_links(goal_map, links)
    counts["goal_project_links_created"] = goal_project_created
    counts["goal_task_project_links_created"] = goal_task_project_created
    task_map = task_contexts(context_ids, workspace_contexts, project_map, goal_map)

    for goal_id, ids in goal_map.items():
        for index, context_id in enumerate(ids):
            counts["goal_context_links_created"] += links.create("goal-context-links", {
                "goal_id": goal_id, "context_id": context_id,
                "relation_type": "primary" if index == 0 else "supporting", "weight": 1, "source": SOURCE,
            })
    for project_id, ids in project_map.items():
        for index, context_id in enumerate(ids):
            counts["project_context_links_created"] += links.create("project-context-links", {
                "project_id": project_id, "context_id": context_id,
                "relation_type": "primary" if index == 0 else "supporting", "weight": 1, "source": SOURCE,
            })
    link_store = _task_store()
    for task_id, ids in task_map.items():
        for index, context_id in enumerate(ids):
            counts["task_context_links_created"] += link_store.link(
                task_id, "context", context_id,
                {"relation_type": "primary" if index == 0 else "supporting", "weight": 1, "source": SOURCE},
                update=False,
            ) == "created"

    desired_role_contexts: set[tuple[int, int]] = set()
    for role in role_registry.get("roles", []):
        for context_slug in role.get("contexts", []):
            context_id = context_ids.get(context_slug)
            if context_id is not None:
                desired_role_contexts.add((role_ids[role["id"]], context_id))

    roles_cell = _cell("roles-skills")

    def prune_generated_links(plural: str, id_columns: tuple[str, ...], desired: set[tuple[int, int]]) -> None:
        for row in roles_cell.list(plural):
            if row.get("source") != PERSONAL_ROLE_SOURCE:
                continue
            key = tuple(row[column] for column in id_columns)
            if key not in desired:
                roles_cell.delete(plural, row["id"])
                links.forget(plural, key)

    prune_generated_links(
        "personal-role-context-links", ("personal_role_id", "context_id"), desired_role_contexts
    )
    for personal_role_id, context_id in desired_role_contexts:
        counts["personal_role_context_links_created"] += links.create("personal-role-context-links", {
            "personal_role_id": personal_role_id, "context_id": context_id, "relation_type": "belongs_to",
            "source": PERSONAL_ROLE_SOURCE,
        })

    role_contexts = defaultdict(set)
    for role_id, context_id in desired_role_contexts:
        role_contexts[role_id].add(context_id)
    context_children: dict[int, set[int]] = defaultdict(set)
    all_contexts = _cell("projects").list("portfolio-contexts")
    for context in all_contexts:
        if context["parent_context_id"] is not None:
            context_children[context["parent_context_id"]].add(context["id"])

    def descendants(context_id: int) -> set[int]:
        result = {context_id}
        pending = [context_id]
        while pending:
            current = pending.pop()
            for child_id in context_children.get(current, set()):
                if child_id in result:
                    continue
                result.add(child_id)
                pending.append(child_id)
        return result

    goal_context_sets = {key: set(value) for key, value in goal_map.items()}
    project_context_sets = {key: set(value) for key, value in project_map.items()}
    task_context_sets = {key: set(value) for key, value in task_map.items()}
    desired_role_goals: set[tuple[int, int]] = set()
    desired_role_projects: set[tuple[int, int]] = set()
    desired_role_tasks: set[tuple[int, int]] = set()
    for role_id, contexts in role_contexts.items():
        role_scope_contexts = set().union(*(descendants(context_id) for context_id in contexts))
        for goal_id, linked_contexts in goal_context_sets.items():
            if not role_scope_contexts.intersection(linked_contexts):
                continue
            desired_role_goals.add((role_id, goal_id))
        for project_id, linked_contexts in project_context_sets.items():
            if not role_scope_contexts.intersection(linked_contexts):
                continue
            desired_role_projects.add((role_id, project_id))
        for task_id, linked_contexts in task_context_sets.items():
            if not role_scope_contexts.intersection(linked_contexts):
                continue
            desired_role_tasks.add((role_id, task_id))

    prune_generated_links("personal-role-goal-links", ("personal_role_id", "goal_id"), desired_role_goals)
    prune_generated_links("personal-role-project-links", ("personal_role_id", "project_id"), desired_role_projects)
    prune_generated_links("personal-role-task-links", ("personal_role_id", "task_id"), desired_role_tasks)
    for role_id, goal_id in desired_role_goals:
        counts["personal_role_goal_links_created"] += links.create("personal-role-goal-links", {
            "personal_role_id": role_id, "goal_id": goal_id, "link_type": "primary", "source": PERSONAL_ROLE_SOURCE,
        })
    for role_id, project_id in desired_role_projects:
        counts["personal_role_project_links_created"] += links.create("personal-role-project-links", {
            "personal_role_id": role_id, "project_id": project_id, "link_type": "primary",
            "source": PERSONAL_ROLE_SOURCE,
        })
    for role_id, task_id in desired_role_tasks:
        counts["personal_role_task_links_created"] += links.create("personal-role-task-links", {
            "personal_role_id": role_id, "task_id": task_id, "link_type": "primary", "source": PERSONAL_ROLE_SOURCE,
        })
    # Reuse the canonical role hierarchy builder for all task-goal/project
    # combinations, then add the legacy goal_tasks bridge without duplicating
    # rows in tasks.
    from role_portfolio_seed import ensure_work_hierarchy

    all_project_ids = _project_ids()
    hierarchy_counts = ensure_work_hierarchy(all_project_ids)
    for key, value in hierarchy_counts.items():
        counts[f"hierarchy_{key}"] += value
    counts["goal_task_stories_created"] = ensure_goal_task_work_items(links)
    counts["contexts"] = len(_cell("projects").list("portfolio-contexts"))
    counts["personal_roles"] = len(roles_cell.list("personal-roles"))
    counts["goals"] = len(_cell("goals").list("goals"))
    counts["projects"] = len(_cell("projects").list("projects"))
    counts["tasks"] = len(_task_store().list(include_archived=True))
    return dict(counts)


def markdown_value(value: object) -> str:
    return str(value or "—").replace("|", "\\|").replace("\n", " ")


def write_report() -> None:
    lines = [
        "---",
        "title: Portfolio Contexts and Personal Roles",
        "tags: [dashboard, portfolio, personal, freelance, startup, hobby, contexts]",
        "---",
        "",
        "# Portfolio Contexts and Personal Roles",
        "",
        "> Context is where the work or life goal belongs. Role is the capability or personal identity exercised inside that context.",
        "> Freelance, startup, hobby, and client engagement are contexts — they are not professional roles.",
        "",
        "## Contexts",
        "",
        "| Context | Type | Scope | Parent | Goals | Projects | Tasks | Progress | Source |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    contexts = _cell("projects").list("portfolio-contexts")
    contexts_by_id = {row["id"]: row for row in contexts}
    goals_by_id = {row["id"]: row for row in _cell("goals").list("goals")}
    goal_links: dict[int, set[int]] = defaultdict(set)
    for link in _cell("goals").list("goal-context-links"):
        goal_links[link["context_id"]].add(link["goal_id"])
    project_links: dict[int, set[int]] = defaultdict(set)
    for link in _cell("projects").list("project-context-links"):
        project_links[link["context_id"]].add(link["project_id"])
    rows = []
    for context in sorted(contexts, key=lambda c: (c["context_type"], c["slug"])):
        progress = [goals_by_id[g]["progress_percent"] for g in goal_links.get(context["id"], ())
                    if g in goals_by_id and goals_by_id[g]["progress_percent"] is not None]
        parent = contexts_by_id.get(context["parent_context_id"])
        rows.append({
            "context_id": context["id"], "slug": context["slug"], "context_type": context["context_type"],
            "scope": context["scope"], "parent_name": parent["name"] if parent else None,
            "goal_count": len(goal_links.get(context["id"], ())),
            "project_count": len(project_links.get(context["id"], ())),
            "avg_progress": float(int(sum(progress) / len(progress) + 0.5)) if progress else None,  # ROUND() gave a float
            "source_path": context.get("source_path"),
        })
    context_tasks: dict[int, set[int]] = defaultdict(set)
    for link in _task_store().links(target_type="context"):
        context_tasks[link["target_id"]].add(link["task"])
    for row in rows:
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} | {}% | `{}` |".format(
                markdown_value(row["slug"]), markdown_value(row["context_type"]),
                markdown_value(row["scope"]), markdown_value(row["parent_name"]),
                row["goal_count"], row["project_count"], len(context_tasks.get(row["context_id"], ())),
                row["avg_progress"] if row["avg_progress"] is not None else 0,
                markdown_value(row["source_path"]),
            )
        )
    lines.extend([
        "",
        "## Personal Roles",
        "",
        "| Personal role | Category | Contexts | Goals | Projects | Tasks |",
        "|---|---|---|---:|---:|---:|",
    ])
    roles_cell = _cell("roles-skills")
    role_context_links = roles_cell.list("personal-role-context-links")
    role_goal_links = roles_cell.list("personal-role-goal-links")
    role_project_links = roles_cell.list("personal-role-project-links")
    role_task_links = roles_cell.list("personal-role-task-links")
    role_rows = []
    for role in sorted(roles_cell.list("personal-roles"), key=lambda r: (r["category"], r["slug"])):
        context_slugs = sorted({
            contexts_by_id[link["context_id"]]["slug"] for link in role_context_links
            if link["personal_role_id"] == role["id"] and link["context_id"] in contexts_by_id
        })
        role_rows.append({
            "slug": role["slug"], "name": role["name"], "category": role["category"],
            "contexts": ",".join(context_slugs) or None,
            "goal_count": len({l["goal_id"] for l in role_goal_links if l["personal_role_id"] == role["id"]}),
            "project_count": len({l["project_id"] for l in role_project_links if l["personal_role_id"] == role["id"]}),
            "task_count": len({l["task_id"] for l in role_task_links if l["personal_role_id"] == role["id"]}),
        })
    for row in role_rows:
        lines.append(
            "| `{}` ({}) | {} | {} | {} | {} | {} |".format(
                markdown_value(row["slug"]), markdown_value(row["name"]),
                markdown_value(row["category"]), markdown_value(row["contexts"]),
                row["goal_count"], row["project_count"], row["task_count"],
            )
        )
    lines.extend([
        "",
        "## Operating Rules",
        "",
        "1. A professional role and a personal role are separate dimensions.",
        "2. Freelance and startup describe the work context; a client engagement is a concrete delivery context.",
        "3. A hobby can have a personal role, a context, a goal, and normal project/task progress.",
        "4. A Goal may belong to multiple contexts and may advance continuously with other Goals.",
        "5. Existing data remains the source of truth; this report is a projection of links, not a replacement for it.",
        "",
    ])
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def seed(dry_run: bool = False) -> dict[str, int]:
    _DRY_RUN["on"] = _ROLE_SEED_DRY_RUN["on"] = dry_run
    try:
        context_ids, workspace_contexts, venture_contexts = seed_contexts()
        counts = apply_links(context_ids, workspace_contexts, venture_contexts, {})
        if not dry_run:
            write_report()
        return counts
    finally:
        _DRY_RUN["on"] = _ROLE_SEED_DRY_RUN["on"] = False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        counts = seed(dry_run=args.dry_run)
    except (CellStoreError, ClientConfigError) as exc:
        print(f"Portfolio context projection unavailable: {exc}")
        return 1
    mode = " (dry-run)" if args.dry_run else ""
    print(f"Portfolio context projection{mode}")
    for key in (
        "contexts", "personal_roles", "goals", "projects", "tasks",
        "task_project_links_created", "goal_project_links_created",
        "goal_task_project_links_created", "goal_task_stories_created",
        "goal_context_links_created", "project_context_links_created",
        "task_context_links_created", "personal_role_context_links_created",
        "personal_role_goal_links_created", "personal_role_project_links_created",
        "personal_role_task_links_created",
    ):
        print(f"  {key}: {counts.get(key, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

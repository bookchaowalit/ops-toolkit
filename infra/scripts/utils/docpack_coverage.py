#!/usr/bin/env python3
"""Check per-system document packs against the portfolio docpack standard.

Reads registries/docpack-coverage.yaml from the registry folder (SOLO_REGISTRY_DIR), resolves every
required doc type for each system's tier (front matter `doc_type:` or the
registry `doc_type_map` for legacy packs), validates front matter, and reports
whether built PDFs are present and newer than their Markdown sources.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT, REGISTRY_DIR, display_path, registry_path, repo_file, repo_rel  # noqa: E402
REGISTRY_PATH = Path("repository-catalog/registries/docpack-coverage.yaml")
REQUIRED_FIELDS = ("title", "doc_id", "version", "status")
VERDICTS = ("invest", "showcase", "template", "merge", "archive")
CARDS_INDEX_PATH = Path("repository-catalog/portfolio/PRODUCT-CARDS.md")
FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _under(root: Path, relative: str | Path) -> Path:
    """Resolve a registry-relative path. The default root (solo-ops) maps legacy hub-layout paths to the
    repositories that own them (clones next to solo-ops, SOLO_REGISTRY_DIR); any other root is used verbatim."""
    return repo_file(relative) if root == PROJECT_ROOT else root / relative


def _name(root: Path, path: Path) -> str:
    return display_path(path) if root == PROJECT_ROOT else path.relative_to(root).as_posix()


def load_registry(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    data = yaml.safe_load(_under(root, REGISTRY_PATH).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("systems"), list):
        raise ValueError(f"{REGISTRY_PATH}: expected a mapping with a systems list")
    return data


def front_matter(path: Path) -> dict[str, str]:
    match = FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta


def pdf_state(pack: Path, md_files: list[Path]) -> dict[str, Any]:
    pdf_dir = pack / "pdf"
    pdfs = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.is_dir() else []
    if not pdfs:
        return {"built": False, "count": 0, "stale": []}
    newest_pdf = max(p.stat().st_mtime for p in pdfs)
    stale = [p.name for p in md_files if p.stat().st_mtime > newest_pdf]
    return {"built": True, "count": len(pdfs), "stale": stale}


def discover_repos(registry: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Expand repo_discovery roots into system rows (one per nested git repository)."""
    spec = registry.get("repo_discovery") or {}
    overrides = spec.get("tier_overrides") or {}
    excluded = tuple(spec.get("exclude", []))
    # A repository whose DOCPACK is already registered as a system is covered there.
    registered = {system.get("pack") for system in registry.get("systems", [])}
    rows: list[dict[str, Any]] = []
    for base in spec.get("roots", []):
        base_path = _under(root, base)
        if not base_path.is_dir():
            continue
        for git_dir in sorted(base_path.glob("*/*/**/.git")):
            repo = git_dir.parent
            rel = repo.relative_to(base_path).as_posix()
            if rel.startswith(excluded) or _name(root, repo / "DOCPACK") in registered:
                continue
            rows.append({
                "id": rel,
                "name": repo.name,
                "kind": "personal-repo",
                "tier": overrides.get(rel, spec.get("default_tier", "L0")),
                "pack": _name(root, repo / "DOCPACK"),
                "always_required": list(spec.get("always_required", [])),
            })
    return rows


def check_system(system: dict[str, Any], registry: dict[str, Any], root: Path) -> dict[str, Any]:
    tier = system.get("tier")
    required = list(registry.get("tiers", {}).get(tier, []))
    required += [t for t in system.get("always_required", []) if t not in required]
    pack = _under(root, system["pack"])
    result: dict[str, Any] = {
        "id": system["id"],
        "kind": system.get("kind"),
        "tier": tier,
        "pack": system["pack"],
        "errors": [],
        "missing": [],
        "found": {},
    }
    if not required:
        result["errors"].append(f"unknown tier {tier!r}")
    nested = Path(system["pack"]).parts[:4] == ("projects", "product", "engineering", "book-dev")
    if not pack.is_dir() and nested and os.environ.get("SOLO_EMPIRE_CI") == "1":
        # CI checks out only this repository; Book Dev and client repositories are absent there.
        result["complete"] = True
        result["skipped"] = "checkout absent in CI"
        result["pdf"] = {"built": False, "count": 0, "stale": []}
        return result
    if not pack.is_dir():
        result["missing"] = required
        result["errors"].append("pack directory does not exist")
        result["complete"] = False
        result["pdf"] = {"built": False, "count": 0, "stale": []}
        return result

    known_types = set(registry.get("doc_types", {}))
    by_type: dict[str, str] = {}
    md_files = sorted(pack.glob("*.md"))
    for md in md_files:
        meta = front_matter(md)
        doc_type = meta.get("doc_type")
        if not doc_type:
            continue
        if doc_type not in known_types:
            result["errors"].append(f"{md.name}: unknown doc_type {doc_type!r}")
            continue
        by_type.setdefault(doc_type, md.name)
        if doc_type == "product-card":
            result["verdict"] = meta.get("verdict")
            if meta.get("verdict") not in VERDICTS:
                result["errors"].append(f"{md.name}: verdict must be one of {', '.join(VERDICTS)}")
        missing_fields = [f for f in REQUIRED_FIELDS if not meta.get(f)]
        if missing_fields:
            result["errors"].append(f"{md.name}: front matter missing {', '.join(missing_fields)}")
    for doc_type, rel in (system.get("doc_type_map") or {}).items():
        if (pack / rel).is_file():
            by_type.setdefault(doc_type, rel)
        else:
            result["errors"].append(f"doc_type_map {doc_type}: {rel} not found")

    result["found"] = {t: by_type[t] for t in required if t in by_type}
    result["missing"] = [t for t in required if t not in by_type]
    if system.get("pdf_builder") != "legacy" and pack.is_dir() and not (pack / "docpack.yaml").is_file():
        result["errors"].append("docpack.yaml is missing")
    result["pdf"] = pdf_state(pack, md_files)
    result["complete"] = not result["missing"] and not result["errors"]
    return result


def run(root: Path = PROJECT_ROOT, only: str | None = None, scope: str = "all") -> dict[str, Any]:
    registry = load_registry(root)
    candidates: list[dict[str, Any]] = []
    if scope in ("all", "systems"):
        candidates += registry["systems"]
    if scope in ("all", "repos"):
        candidates += discover_repos(registry, root)
    systems = [s for s in candidates if only in (None, s.get("id"))]
    if only and not systems:
        raise ValueError(f"unknown system id: {only}")
    results = [check_system(s, registry, root) for s in systems]
    return {
        "registry": REGISTRY_PATH.as_posix(),
        "systems": results,
        "complete": sum(1 for r in results if r["complete"]),
        "total": len(results),
        "ok": all(r["complete"] for r in results),
    }


def write_cards_index(report: dict[str, Any], root: Path = PROJECT_ROOT) -> Path:
    """Write the portfolio Product Card index (verdict per personal repository)."""
    repos = [r for r in report["systems"] if "product-card" in r["found"] or r.get("kind") == "personal-repo"]
    counts = {v: sum(1 for r in repos if r.get("verdict") == v) for v in VERDICTS}
    lines = [
        "---",
        "title: Product Card Index",
        "status: generated",
        "description: Verdict per personal Book Dev repository, generated by task docpack:check -- --write-cards-index.",
        "---",
        "",
        "# Product Card Index",
        "",
        "Generated from each repository's `DOCPACK/00-product-card.md`; edit the cards, not this file.",
        "",
        "| Verdict | Repositories |",
        "|---|---|",
        *[f"| {v} | {counts[v]} |" for v in VERDICTS],
        f"| missing card | {sum(1 for r in repos if 'product-card' not in r['found'])} |",
        "",
        "| Repository | Tier | Verdict | Docs complete |",
        "|---|---|---|---|",
    ]
    for r in sorted(repos, key=lambda x: (VERDICTS.index(x["verdict"]) if x.get("verdict") in VERDICTS else 9, x["id"])):
        lines.append(f"| `{r['id']}` | {r['tier']} | {r.get('verdict') or '—'} | {'yes' if r['complete'] else 'no'} |")
    out = _under(root, CARDS_INDEX_PATH)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    parser.add_argument("--system", help="check one system id")
    parser.add_argument("--require-pdf", action="store_true", help="fail when PDFs are missing or stale")
    parser.add_argument("--scope", choices=("all", "systems", "repos"), default="all", help="registry systems, discovered personal repos, or both")
    parser.add_argument("--write-cards-index", action="store_true", help=f"write {CARDS_INDEX_PATH}")
    parser.add_argument("--summary", action="store_true", help="print only failing systems and totals")
    args = parser.parse_args(argv)

    if REGISTRY_DIR is None or not registry_path("registries/docpack-coverage.yaml").is_file():
        print("SKIP: docpack_coverage: set SOLO_REGISTRY_DIR to the repository-catalog folder of the registry repository")
        return 0
    report = run(only=args.system, scope=args.scope)
    if args.write_cards_index:
        print(f"wrote {display_path(write_cards_index(report))}")
    if args.require_pdf:
        for r in report["systems"]:
            if not r["pdf"]["built"] or r["pdf"]["stale"]:
                r["complete"] = False
        report["complete"] = sum(1 for r in report["systems"] if r["complete"])
        report["ok"] = report["complete"] == report["total"]

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in report["systems"]:
            if args.summary and r["complete"]:
                continue
            mark = "OK " if r["complete"] else "GAP"
            pdf = r["pdf"]
            pdf_note = f"pdf {pdf['count']}" + (f" stale {len(pdf['stale'])}" if pdf["stale"] else "") if pdf["built"] else "pdf -"
            found = len(r["found"])
            need = found + len(r["missing"])
            verdict = f"  verdict {r['verdict']}" if r.get("verdict") else ""
            print(f"{mark} {r['id']:<32} {r['tier']}  docs {found}/{need}  {pdf_note}{verdict}")
            if r["missing"] and not args.summary:
                print(f"      missing: {', '.join(r['missing'])}")
            for err in r["errors"]:
                print(f"      error: {err}")
        print(f"\n{report['complete']}/{report['total']} systems complete")
    if args.write_cards_index:
        return 0
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

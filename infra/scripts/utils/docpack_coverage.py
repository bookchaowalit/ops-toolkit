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


def pdf_state(
    pack: Path, md_files: list[Path], *, legacy: bool = False, mapped: set[Path] | None = None,
) -> dict[str, Any]:
    pdf_dir = pack / "pdf"
    pdfs = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.is_dir() else []
    state: dict[str, Any] = {
        "built": bool(pdfs), "count": len(pdfs), "stale": [], "missing": [], "skipped": {},
    }
    for md in md_files:
        rel = md.relative_to(pack).as_posix()
        meta = front_matter(md)
        doc_id = meta.get("doc_id", "").strip("\"'")
        if not doc_id:
            # The standard builder skips metadata-free Markdown. No stem-only
            # legacy naming contract has been verified, so do not invent one.
            state["skipped"][rel] = "no doc_id; not a verified PDF source"
            continue
        if not legacy and not re.match(r"^[0-9]{2}-", md.name) and md not in (mapped or set()):
            state["skipped"][rel] = "outside the builder's numbered Markdown sources"
            continue
        version = meta.get("version", "").strip("\"'").split()
        version_text = version[0].removeprefix("v") if version else "0.1"
        stem = re.sub(r"^[0-9]{2}-", "", md.stem)
        if legacy:
            # EV3's 02-PRD-v0.2.md (front matter 0.4) produces PRD_v0.4.pdf.
            stem = re.sub(r"-v[0-9]+(?:\.[0-9]+)*$", "", stem)
        pdf = pdf_dir / f"{doc_id}_{stem}_v{version_text}.pdf"
        if not pdf.is_file():
            state["missing"].append(rel)
        elif md.stat().st_mtime > pdf.stat().st_mtime:
            state["stale"].append(rel)
    return state


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


def override_errors(
    registry: dict[str, Any], root: Path, rows: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Reject unused overrides without guessing a repository from its basename."""
    spec = registry.get("repo_discovery") or {}
    roots = spec.get("roots", [])
    discovered = {row["id"] for row in rows}
    registered = {system.get("pack") for system in registry["systems"]}
    errors: list[str] = []
    warnings: list[str] = []
    for key in spec.get("tier_overrides") or {}:
        if key in discovered or any(f"{base.rstrip('/')}/{key}/DOCPACK" in registered for base in roots):
            continue
        message = f"tier_overrides {key!r}: no discovered repository or registered pack path"
        if roots and os.environ.get("SOLO_EMPIRE_CI") == "1" and not any(
            (root / base / key / ".git").exists() for base in roots
        ):
            warnings.append(f"{message}; checkout absent in CI, validation skipped")
        else:
            errors.append(message)
    return errors, warnings


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
    checkout_present = nested and any(
        (parent / ".git").exists() for parent in pack.parents if parent != root and root in parent.parents
    )
    if not pack.is_dir() and nested and not checkout_present and os.environ.get("SOLO_EMPIRE_CI") == "1":
        # CI checks out only this repository; Book Dev and client repositories are absent there.
        result["complete"] = True
        result["skipped"] = "checkout absent in CI"
        result["pdf"] = {"built": False, "count": 0, "stale": [], "missing": [], "skipped": {}}
        return result
    if not pack.is_dir():
        result["missing"] = required
        result["errors"].append("pack directory does not exist")
        result["complete"] = False
        result["pdf"] = {"built": False, "count": 0, "stale": [], "missing": [], "skipped": {}}
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
    # Required mapped sources (including nested runbooks/README.md) need their
    # own PDFs too; unrelated nested Markdown is outside the builder's scope.
    mapped = {pack / by_type[t] for t in required if t in by_type}
    result["pdf"] = pdf_state(
        pack, sorted(set(md_files) | mapped), legacy=system.get("pdf_builder") == "legacy", mapped=mapped,
    )
    result["complete"] = not result["missing"] and not result["errors"]
    return result


def run(root: Path = PROJECT_ROOT, only: str | None = None, scope: str = "all") -> dict[str, Any]:
    registry = load_registry(root)
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    if scope in ("all", "systems"):
        candidates += registry["systems"]
    if scope in ("all", "repos"):
        rows = discover_repos(registry, root)
        errors, warnings = override_errors(registry, root, rows)
        candidates += rows
    systems = [s for s in candidates if only in (None, s.get("id"))]
    if only and not systems:
        raise ValueError(f"unknown system id: {only}")
    results = [check_system(s, registry, root) for s in systems]
    warnings += [f"{r['id']}: {r['skipped']}" for r in results if r.get("skipped")]
    return {
        "registry": REGISTRY_PATH.as_posix(),
        "systems": results,
        "complete": sum(1 for r in results if r["complete"]),
        "total": len(results),
        "ok": not errors and all(r["complete"] for r in results),
        "errors": errors,
        "warnings": warnings,
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
            if r.get("skipped"):
                continue
            if not r["pdf"]["built"] or r["pdf"]["missing"] or r["pdf"]["stale"]:
                r["complete"] = False
        report["complete"] = sum(1 for r in report["systems"] if r["complete"])
        report["ok"] = not report["errors"] and report["complete"] == report["total"]

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for err in report["errors"]:
            print(f"error: {err}")
        for warning in report["warnings"]:
            print(f"warning: {warning}")
        for r in report["systems"]:
            if args.summary and r["complete"]:
                continue
            mark = "OK " if r["complete"] else "GAP"
            pdf = r["pdf"]
            pdf_note = f"pdf {pdf['count']}" + (f" stale {len(pdf['stale'])}" if pdf["stale"] else "") if pdf["built"] else "pdf -"
            if pdf["missing"]:
                pdf_note += f" missing {len(pdf['missing'])}"
            found = len(r["found"])
            need = found + len(r["missing"])
            verdict = f"  verdict {r['verdict']}" if r.get("verdict") else ""
            print(f"{mark} {r['id']:<32} {r['tier']}  docs {found}/{need}  {pdf_note}{verdict}")
            if r["missing"] and not args.summary:
                print(f"      missing: {', '.join(r['missing'])}")
            for err in r["errors"]:
                print(f"      error: {err}")
            if not args.summary:
                for key in ("missing", "stale"):
                    if pdf[key]:
                        print(f"      pdf {key}: {', '.join(pdf[key])}")
                for source, reason in pdf["skipped"].items():
                    print(f"      pdf skipped: {source}: {reason}")
        print(f"\n{report['complete']}/{report['total']} systems complete")
    if args.write_cards_index:
        return 0
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Media and generated-data lifecycle pipeline.

The default operation is read-only: scan data/generated and data/runtime,
hash files, find exact duplicates, and write a retention plan. Files outside
those generated/runtime boundaries (backups, raw input, indexes, logs, and DBs)
are intentionally never touched by this script.

Usage:
    python3 media_lifecycle.py --plan --save
    python3 media_lifecycle.py --plan --save --json
    python3 media_lifecycle.py --quarantine --apply
    python3 media_lifecycle.py --purge --apply --quarantine-days 30
    python3 media_lifecycle.py --catalog --save
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

try:
    from .paths import GENERATED_DIR, PROJECT_ROOT, RUNTIME_DIR
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from paths import GENERATED_DIR, PROJECT_ROOT, RUNTIME_DIR


MANIFEST_DIR = RUNTIME_DIR / "cleanup"
QUARANTINE_DIR = MANIFEST_DIR / "quarantine"
INVENTORY_PATH = MANIFEST_DIR / "media-inventory.json"
PLAN_PATH = MANIFEST_DIR / "media-retention-plan.json"
QUARANTINE_MANIFEST_PATH = MANIFEST_DIR / "quarantine-manifest.jsonl"
CATALOG_PATH = MANIFEST_DIR / "media-catalog.html"
AGENT_LOOP_DIR = RUNTIME_DIR / "agent-loop"

# These roots are generated/runtime boundaries only. Never add data/raw or
# data/backups here: those have independent source/backup retention policies.
SCAN_ROOTS = ((GENERATED_DIR, "generated"), (RUNTIME_DIR, "runtime"))

IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".webm"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
GENERATED_TEXT_EXTENSIONS = {
    ".csv", ".html", ".json", ".md", ".txt", ".xml", ".yaml", ".yml"
}

DEFAULT_POLICIES = {
    "runtime_days": 14,
    "generated_media_days": 30,
    "quarantine_days": 30,
}


def iso_utc(timestamp: float | None = None) -> str:
    value = datetime.fromtimestamp(timestamp or datetime.now().timestamp(), tz=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def file_kind(path: Path, root_kind: str) -> str:
    if root_kind == "runtime":
        return "runtime_snapshot"
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in GENERATED_TEXT_EXTENSIONS:
        return "generated_text"
    return "generated_other"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_paths() -> set[str]:
    """Return root-repository tracked paths; nested repositories are excluded."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--full-name"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def tracked_text() -> list[tuple[str, str]]:
    """Load small text files from the root repository for conservative ref checks."""
    result: list[tuple[str, str]] = []
    for name in sorted(tracked_paths()):
        path = PROJECT_ROOT / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        if path.suffix.lower() not in GENERATED_TEXT_EXTENSIONS | {".js", ".py", ".sh", ".ts"}:
            continue
        try:
            result.append((name, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return result


def is_referenced(relative: str, text_files: list[tuple[str, str]]) -> tuple[bool, str | None]:
    """Check exact repository path references without guessing from generic names."""
    basename = Path(relative).name
    for source, text in text_files:
        if relative in text:
            return True, source
        # A local markdown/HTML link commonly stores only the filename. Only
        # use basename matching for generated reports and require a path-like
        # media filename to avoid protecting unrelated files with generic names.
        if relative.startswith("data/generated/reports/") and basename in text:
            return True, source
    return False, None


def iter_files() -> Iterable[tuple[Path, str]]:
    """Yield files, excluding agent-loop which is handled as run directories."""
    for root, root_kind in SCAN_ROOTS:
        if not root.exists():
            continue
        for base, directories, filenames in os.walk(root):
            base_path = Path(base)
            # Prune large runtime snapshots and this script's own manifests
            # before os.walk descends into them.
            directories[:] = [
                name for name in directories
                if (base_path / name) != AGENT_LOOP_DIR
                and (base_path / name) != MANIFEST_DIR
            ]
            for name in filenames:
                path = base_path / name
                if path.is_symlink():
                    continue
                yield path, root_kind


def snapshot_summary(directory: Path) -> tuple[int, int, float]:
    """Summarize a large agent run without hashing every cache asset."""
    file_count = 0
    total_size = 0
    newest_mtime = directory.stat().st_mtime
    for base, _dirs, names in os.walk(directory):
        for name in names:
            path = Path(base) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            file_count += 1
            total_size += stat.st_size
            newest_mtime = max(newest_mtime, stat.st_mtime)
    return file_count, total_size, newest_mtime


def scan_agent_loop_snapshots() -> list[dict]:
    """Create one inventory record per run directory, not one per cache file."""
    if not AGENT_LOOP_DIR.is_dir():
        return []
    records = []
    for directory in sorted(AGENT_LOOP_DIR.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        file_count, total_size, newest_mtime = snapshot_summary(directory)
        records.append({
            "path": relative_path(directory),
            "root": "runtime",
            "kind": "runtime_snapshot",
            "entry_type": "directory",
            "extension": "",
            "size": total_size,
            "file_count": file_count,
            "mtime": iso_utc(directory.stat().st_mtime),
            "newest_file_mtime": iso_utc(newest_mtime),
            "sha256": None,
            "tracked": False,
            "referenced": False,
            "reference_source": None,
        })
    return records


def scan_agent_loop_root_files() -> list[dict]:
    """Include stray files placed directly in agent-loop beside run directories."""
    if not AGENT_LOOP_DIR.is_dir():
        return []
    records = []
    for path in AGENT_LOOP_DIR.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        stat = path.stat()
        records.append({
            "path": relative_path(path),
            "root": "runtime",
            "kind": "runtime_snapshot",
            "entry_type": "file",
            "extension": path.suffix.lower(),
            "size": stat.st_size,
            "mtime": iso_utc(stat.st_mtime),
            "sha256": sha256_file(path),
            "tracked": False,
            "referenced": False,
            "reference_source": None,
        })
    return records


def scan() -> list[dict]:
    tracked = tracked_paths()
    text_files = tracked_text()
    records: list[dict] = []
    for path, root_kind in iter_files():
        rel = relative_path(path)
        stat = path.stat()
        referenced, source = is_referenced(rel, text_files)
        records.append({
            "path": rel,
            "root": root_kind,
            "kind": file_kind(path, root_kind),
            "entry_type": "file",
            "extension": path.suffix.lower(),
            "size": stat.st_size,
            "mtime": iso_utc(stat.st_mtime),
            "sha256": sha256_file(path),
            "tracked": rel in tracked,
            "referenced": referenced,
            "reference_source": source,
        })

    records.extend(scan_agent_loop_snapshots())
    records.extend(scan_agent_loop_root_files())

    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if record.get("sha256"):
            groups[record["sha256"]].append(record)
    for digest, group in groups.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: (item["mtime"], item["path"]), reverse=True)
        group_id = digest[:16]
        for index, record in enumerate(ordered):
            record["duplicate_group"] = group_id
            record["duplicate_rank"] = index + 1

    return sorted(records, key=lambda item: item["path"])


def load_or_scan() -> list[dict]:
    if INVENTORY_PATH.is_file():
        try:
            payload = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
            if payload.get("schema_version") == 1:
                return payload.get("files", [])
        except (OSError, json.JSONDecodeError):
            pass
    return scan()


def build_plan(records: list[dict], now: datetime | None = None, policies: dict | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    policy = {**DEFAULT_POLICIES, **(policies or {})}
    candidates: list[dict] = []
    protected = 0
    for record in records:
        reason = None
        if record.get("tracked"):
            protected += 1
            continue
        if record.get("referenced"):
            protected += 1
            continue
        mtime_key = "newest_file_mtime" if record.get("entry_type") == "directory" else "mtime"
        mtime = datetime.fromisoformat(record[mtime_key].replace("Z", "+00:00"))
        age_days = max(0, (now - mtime).total_seconds() / 86400)
        if record["root"] == "runtime" and age_days >= policy["runtime_days"]:
            reason = f"runtime snapshot older than {policy['runtime_days']} days"
        elif (
            record["root"] == "generated"
            and record["kind"] in {"image", "video", "audio"}
            and age_days >= policy["generated_media_days"]
        ):
            reason = f"unreferenced generated media older than {policy['generated_media_days']} days"
        if reason:
            candidate = dict(record)
            candidate["age_days"] = round(age_days, 2)
            candidate["reason"] = reason
            candidates.append(candidate)

    duplicate_groups: dict[str, int] = defaultdict(int)
    for record in records:
        if record.get("duplicate_group"):
            duplicate_groups[record["duplicate_group"]] += 1
    return {
        "schema_version": 1,
        "generated_at": iso_utc(),
        "policy": policy,
        "summary": {
            "files_scanned": len(records),
            "protected_files": protected,
            "duplicate_groups": len(duplicate_groups),
            "candidates": len(candidates),
            "candidate_bytes": sum(item["size"] for item in candidates),
        },
        "candidates": candidates,
    }


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_inventory(records: list[dict]) -> None:
    save_json(INVENTORY_PATH, {
        "schema_version": 1,
        "generated_at": iso_utc(),
        "scan_roots": [relative_path(root) for root, _ in SCAN_ROOTS],
        "excluded_boundaries": ["data/backups", "data/raw", "data/indexes", "data/logs", "data/runtime/cleanup"],
        "files": records,
    })


def quarantine(plan: dict) -> list[dict]:
    moved: list[dict] = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination_root = QUARANTINE_DIR / stamp
    for candidate in plan.get("candidates", []):
        source = PROJECT_ROOT / candidate["path"]
        if candidate.get("entry_type") == "directory":
            if not source.is_dir() or source.is_symlink():
                continue
            file_count, total_size, _newest = snapshot_summary(source)
            if file_count != candidate.get("file_count") or total_size != candidate["size"]:
                continue
        elif not source.is_file() or source.is_symlink():
            continue
        if candidate.get("entry_type") == "file" and sha256_file(source) != candidate["sha256"]:
            continue
        destination = destination_root / candidate["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved.append({
            "quarantined_at": iso_utc(),
            "original_path": candidate["path"],
            "quarantine_path": relative_path(destination),
            "sha256": candidate["sha256"],
            "size": candidate["size"],
        })
    if moved:
        QUARANTINE_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with QUARANTINE_MANIFEST_PATH.open("a", encoding="utf-8") as handle:
            for item in moved:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return moved


def purge(days: int) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed: list[str] = []
    if not QUARANTINE_DIR.exists():
        return removed
    for path in QUARANTINE_DIR.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff:
            removed.append(relative_path(path))
            path.unlink()
    # Remove empty quarantine directories, deepest first.
    for directory in sorted((p for p in QUARANTINE_DIR.rglob("*") if p.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed


def build_catalog(records: list[dict]) -> None:
    media = [r for r in records if r["kind"] in {"image", "video", "audio"}]
    rows = []
    for record in media:
        label = html.escape(record["path"])
        size_mb = record["size"] / (1024 * 1024)
        preview = ""
        if record["kind"] == "image":
            preview = f'<img loading="lazy" src="../../../{html.escape(record["path"])}" alt="{label}">'
        rows.append(
            f'<article><div class="preview">{preview}</div><code>{label}</code>'
            f'<small>{record["kind"]} · {size_mb:.2f} MB · {record["mtime"]}</small></article>'
        )
    body = "\n".join(rows) or "<p>No generated/runtime media found.</p>"
    document = f"""<!doctype html>
<meta charset="utf-8">
<title>Solo Empire media catalog</title>
<style>body{{font:14px system-ui;margin:2rem;background:#f6f7f9}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}}article{{background:white;padding:1rem;border-radius:8px;overflow:hidden}}img{{max-width:100%;max-height:220px;object-fit:contain}}code,small{{display:block;overflow-wrap:anywhere;margin-top:.5rem}}small{{color:#666}}</style>
<h1>Solo Empire media catalog</h1>
<p>Generated from the lifecycle inventory. This catalog does not copy or delete source files.</p>
<main>{body}</main>
"""
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(document, encoding="utf-8")


def print_summary(payload: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    if payload.get("mode") == "catalog":
        print("🗂️ Media catalog")
        print(f"   Media files: {payload.get('media_files', 0)}")
        print(f"   Catalog: {payload.get('catalog', relative_path(CATALOG_PATH))}")
        return
    if payload.get("mode") == "scan":
        print("🔎 Media inventory")
        print(f"   Scanned: {payload.get('files_scanned', 0)} files")
        print(f"   Inventory: {payload.get('inventory', relative_path(INVENTORY_PATH))}")
        return
    summary = payload.get("summary", {})
    print("🧹 Media lifecycle plan")
    print(f"   Scanned: {summary.get('files_scanned', 0)} files")
    print(f"   Protected: {summary.get('protected_files', 0)} files")
    print(f"   Duplicate groups: {summary.get('duplicate_groups', 0)}")
    print(f"   Candidates: {summary.get('candidates', 0)} files / {summary.get('candidate_bytes', 0) / (1024 * 1024):.2f} MB")
    print(f"   Inventory: {relative_path(INVENTORY_PATH)}")
    print(f"   Plan: {relative_path(PLAN_PATH)}")
    if payload.get("candidates"):
        for candidate in payload["candidates"][:20]:
            print(f"   - {candidate['path']} ({candidate['reason']})")
        if len(payload["candidates"]) > 20:
            print(f"   - ... and {len(payload['candidates']) - 20} more")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan, deduplicate, quarantine, and retain generated/runtime data safely")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true", help="Scan and save an inventory")
    mode.add_argument("--plan", action="store_true", help="Build a retention plan; this is the default")
    mode.add_argument("--quarantine", action="store_true", help="Move approved candidates to quarantine")
    mode.add_argument("--purge", action="store_true", help="Permanently remove old quarantine files")
    mode.add_argument("--catalog", action="store_true", help="Build an HTML catalog of media")
    parser.add_argument("--save", action="store_true", help="Save inventory/plan/catalog output")
    parser.add_argument("--apply", action="store_true", help="Required for quarantine or permanent purge")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary")
    parser.add_argument("--runtime-days", type=int, default=DEFAULT_POLICIES["runtime_days"])
    parser.add_argument("--generated-media-days", type=int, default=DEFAULT_POLICIES["generated_media_days"])
    parser.add_argument("--quarantine-days", type=int, default=DEFAULT_POLICIES["quarantine_days"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.purge:
        if not args.apply:
            print("❌ Refusing permanent purge without --apply", file=sys.stderr)
            return 2
        removed = purge(args.quarantine_days)
        payload = {"mode": "purge", "removed": removed, "removed_count": len(removed)}
        print_summary(payload, args.json)
        return 0

    records = scan() if args.scan or args.plan or args.quarantine or args.catalog or not args.__dict__ else load_or_scan()
    if args.scan:
        save_inventory(records)
        payload = {"mode": "scan", "files_scanned": len(records), "inventory": relative_path(INVENTORY_PATH)}
        print_summary(payload, args.json)
        return 0

    if args.catalog:
        build_catalog(records)
        payload = {"mode": "catalog", "media_files": sum(r["kind"] in {"image", "video", "audio"} for r in records), "catalog": relative_path(CATALOG_PATH)}
        if args.save:
            save_inventory(records)
        print_summary(payload, args.json)
        return 0

    plan = build_plan(records, policies={
        "runtime_days": args.runtime_days,
        "generated_media_days": args.generated_media_days,
        "quarantine_days": args.quarantine_days,
    })
    if args.save or args.quarantine:
        save_inventory(records)
        save_json(PLAN_PATH, plan)
    if args.quarantine:
        if not args.apply:
            print("❌ Refusing quarantine move without --apply", file=sys.stderr)
            print_summary(plan, args.json)
            return 2
        moved = quarantine(plan)
        plan["quarantine"] = {"moved": len(moved), "paths": moved}
        save_json(PLAN_PATH, plan)
        if args.json:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        else:
            print(f"✅ Moved {len(moved)} candidate(s) to quarantine")
        return 0
    print_summary(plan, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

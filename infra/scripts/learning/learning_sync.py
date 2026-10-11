#!/usr/bin/env python3
"""Synchronize learning-note coverage and reviewed Q&A candidates.

This workflow is intentionally deterministic. It detects new or changed
learning notes, validates quiz source links, captures an explicitly sanitized
Q&A candidate, and promotes an approved candidate into a canonical learning
note. It never reads or copies raw AI conversation logs and never invents
quiz answers or distractors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, repo_file, repo_rel  # noqa: E402
from quiz import discover_quizzes, load_quiz, validate_quiz  # noqa: E402


LEARNING_DIR = KNOWLEDGE_ROOT / "learning"
QA_INBOX = LEARNING_DIR / "qa-inbox"
CANDIDATE_DIR = QA_INBOX / "candidates"
STATE_PATH = QA_INBOX / "SYNC-STATE.json"
SCHEMA_VERSION = 1
EXCLUDED_TRACKS = {"qa-inbox"}
VALID_CANDIDATE_STATUSES = {"candidate", "reviewed", "promoted", "rejected"}
SECRET_RE = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9]{16,}|"
    r"(?:gh[pousr]|xox[baprs])[-_][A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{16,}|"
    r"(?:password|passwd|token|secret)\s*[:=]\s*[^\s,;]+"
    r")",
    re.IGNORECASE,
)
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return repo_rel(resolved)
    except ValueError:
        return resolved.as_posix()


def inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_tracks() -> list[Path]:
    return sorted(
        path
        for path in LEARNING_DIR.iterdir()
        if path.is_dir() and path.name not in EXCLUDED_TRACKS and (path / "README.md").is_file()
    )


def discover_learning_notes() -> list[Path]:
    notes: list[Path] = []
    if not LEARNING_DIR.is_dir():
        return notes
    for path in sorted(LEARNING_DIR.rglob("*.md")):
        if not path.is_file() or path.name == "README.md":
            continue
        relative = path.relative_to(LEARNING_DIR)
        if relative.parts and relative.parts[0] in EXCLUDED_TRACKS:
            continue
        notes.append(path)
    return notes


def discover_sync_paths(source_index: dict[str, list[str]]) -> list[Path]:
    """Return learning files whose changes should enter the review queue."""
    paths = set(discover_learning_notes())
    paths.update(track / "README.md" for track in discover_tracks())
    for source in source_index:
        source_path = repo_file(source).resolve()
        if inside(source_path, LEARNING_DIR) and source_path.is_file():
            paths.add(source_path)
    return sorted(paths)


def read_frontmatter(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"cannot read {relative_path(path)}: {exc}"
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, f"{relative_path(path)}: missing YAML frontmatter"
    try:
        value = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        return {}, f"{relative_path(path)}: invalid YAML frontmatter: {exc}"
    if not isinstance(value, dict):
        return {}, f"{relative_path(path)}: frontmatter must be a mapping"
    return value, None


def frontmatter_text(metadata: dict[str, Any]) -> str:
    dumped = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{dumped}\n---\n\n"


def secret_found(value: str) -> bool:
    return bool(SECRET_RE.search(value))


def load_state() -> tuple[dict[str, Any] | None, str | None]:
    if not STATE_PATH.is_file():
        return None, None
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"invalid sync state {relative_path(STATE_PATH)}: {exc}"
    if not isinstance(value, dict):
        return None, f"sync state {relative_path(STATE_PATH)} must be an object"
    return value, None


def quiz_inventory() -> tuple[dict[str, list[str]], list[str], list[str]]:
    source_index: dict[str, list[str]] = {}
    errors: list[str] = []
    quiz_files: list[str] = []
    for path in discover_quizzes():
        quiz_files.append(relative_path(path))
        try:
            data = load_quiz(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        errors.extend(validate_quiz(data, path))
        for question in data.get("questions", []):
            if not isinstance(question, dict) or not isinstance(question.get("source"), str):
                continue
            source = question["source"].split("#", 1)[0]
            source_index.setdefault(source, []).append(
                f"{relative_path(path)}:{question.get('id', 'unknown')}"
            )
    return source_index, errors, quiz_files


def validate_candidate(path: Path, tracks: dict[str, Path]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    metadata, frontmatter_error = read_frontmatter(path)
    label = relative_path(path)
    if frontmatter_error:
        return [frontmatter_error], []

    if metadata.get("type") != "qa-candidate":
        errors.append(f"{label}: type must be qa-candidate")
    status = metadata.get("status")
    if status not in VALID_CANDIDATE_STATUSES:
        errors.append(f"{label}: status must be one of {sorted(VALID_CANDIDATE_STATUSES)}")

    track = metadata.get("track")
    if not isinstance(track, str) or track not in tracks:
        errors.append(f"{label}: track must name an existing learning track")

    source = metadata.get("source")
    source_path: Path | None = None
    if not isinstance(source, str) or not source.startswith("learning/"):
        errors.append(f"{label}: source must be a path under learning/")
    else:
        source_path = repo_file(source.split("#", 1)[0]).resolve()
        if not inside(source_path, LEARNING_DIR) or not source_path.is_file():
            errors.append(f"{label}: source does not point to an existing learning file")
        elif isinstance(track, str):
            try:
                source_track = source_path.relative_to(LEARNING_DIR).parts[0]
            except ValueError:
                source_track = ""
            if source_track != track:
                errors.append(f"{label}: source track {source_track!r} does not match {track!r}")

    for field in ("question", "answer"):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            errors.append(f"{label}: {field} must be a non-empty string")

    source_hash = metadata.get("source_hash")
    if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        errors.append(f"{label}: source_hash must be a SHA-256 hex string")
    elif source_path and source_path.is_file() and source_hash != sha256(source_path):
        warnings.append(f"{label}: source changed since candidate capture")

    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"{label}: cannot read candidate body: {exc}")
        body = ""
    if secret_found(body):
        errors.append(f"{label}: possible secret-shaped value found; sanitize before capture")

    canonical_path = metadata.get("canonical_path")
    if status == "promoted":
        if not isinstance(canonical_path, str) or not repo_file(canonical_path).is_file():
            errors.append(f"{label}: promoted candidate must point to an existing canonical_path")
    return errors, warnings


def build_report() -> tuple[dict[str, Any], list[str]]:
    tracks = discover_tracks()
    track_names = [path.name for path in tracks]
    track_map = {path.name: path for path in tracks}
    source_index, quiz_errors, quiz_files = quiz_inventory()
    notes = discover_learning_notes()
    sync_paths = discover_sync_paths(source_index)
    source_hashes = {relative_path(path): sha256(path) for path in sync_paths}
    state, state_error = load_state()
    errors = list(quiz_errors)
    if state_error:
        errors.append(state_error)

    old_hashes = state.get("source_hashes", {}) if isinstance(state, dict) else {}
    if not isinstance(old_hashes, dict):
        errors.append("sync state source_hashes must be an object")
        old_hashes = {}
    changed = sorted(path for path, digest in source_hashes.items() if old_hashes.get(path) != digest)
    removed = sorted(path for path in old_hashes if path not in source_hashes)
    note_paths = {relative_path(path) for path in notes}
    uncovered = sorted(path for path in note_paths if path not in source_index)

    candidate_errors: list[str] = []
    candidate_warnings: list[str] = []
    candidates: list[str] = []
    if CANDIDATE_DIR.is_dir():
        for path in sorted(CANDIDATE_DIR.glob("*.md")):
            candidates.append(relative_path(path))
            item_errors, item_warnings = validate_candidate(path, track_map)
            candidate_errors.extend(item_errors)
            candidate_warnings.extend(item_warnings)
    errors.extend(candidate_errors)
    report = {
        "version": SCHEMA_VERSION,
        "tracks": track_names,
        "learning_notes": len(notes),
        "tracked_sources": len(source_hashes),
        "quiz_files": quiz_files,
        "quiz_sources": sorted(source_index),
        "uncovered_notes": uncovered,
        "new_or_changed_notes": changed,
        "removed_notes": removed,
        "candidates": candidates,
        "candidate_warnings": candidate_warnings,
        "state_path": relative_path(STATE_PATH),
        "state_initialized": state is not None,
    }
    return report, errors


def write_state(report: dict[str, Any]) -> None:
    QA_INBOX.mkdir(parents=True, exist_ok=True)
    source_index, _errors, _quiz_files = quiz_inventory()
    source_hashes = {
        relative_path(path): sha256(path)
        for path in discover_sync_paths(source_index)
    }
    state = {
        "version": SCHEMA_VERSION,
        "generated_on": date.today().isoformat(),
        "source_hashes": dict(sorted(source_hashes.items())),
        "quiz_files": report["quiz_files"],
        "quiz_sources": report["quiz_sources"],
        "tracks": report["tracks"],
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_report(report: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("Learning Sync")
    print(f"  tracks: {len(report['tracks'])}")
    print(f"  learning notes: {report['learning_notes']}")
    print(f"  tracked sources: {report['tracked_sources']}")
    print(f"  quiz files: {len(report['quiz_files'])}")
    print(f"  uncovered notes: {len(report['uncovered_notes'])}")
    print(f"  new/changed notes: {len(report['new_or_changed_notes'])}")
    print(f"  Q&A candidates: {len(report['candidates'])}")
    if report["uncovered_notes"]:
        print("  Review queue — notes without quiz coverage:")
        for path in report["uncovered_notes"]:
            print(f"    - {path}")
    if report["new_or_changed_notes"]:
        print("  Review queue — new or changed notes:")
        for path in report["new_or_changed_notes"]:
            print(f"    - {path}")
    if report["removed_notes"]:
        print("  Removed notes from sync state:")
        for path in report["removed_notes"]:
            print(f"    - {path}")
    for warning in report["candidate_warnings"]:
        print(f"  ⚠️  {warning}")
    if not report["state_initialized"]:
        print(f"  ℹ️  state not initialized; run --propose --write-state to create {report['state_path']}")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72]


def candidate_body(metadata: dict[str, Any], *, promoted: bool = False) -> str:
    status_line = "Promoted to canonical learning note." if promoted else "Awaiting review."
    return (
        frontmatter_text(metadata)
        + "# Q&A Candidate\n\n"
        + "## Question\n\n"
        + str(metadata["question"]).strip()
        + "\n\n## Proposed answer\n\n"
        + str(metadata["answer"]).strip()
        + "\n\n## Evidence\n\n"
        + f"- Source: `{metadata['source']}`\n"
        + "\n## Review gate\n\n"
        + f"- {status_line}\n"
        + "- Verify the source, remove private data, and add a curated quiz item separately.\n"
    )


def canonical_body(metadata: dict[str, Any]) -> str:
    lines = [
        frontmatter_text(metadata).rstrip(),
        "",
        f"# {metadata['title']}",
        "",
        "## Question",
        "",
        str(metadata["question"]).strip(),
        "",
        "## Answer",
        "",
        str(metadata["answer"]).strip(),
        "",
        "## Evidence",
        "",
        f"- Source: `{metadata['source']}`",
    ]
    if metadata.get("conversation_ref"):
        lines.append(f"- Conversation reference: `{metadata['conversation_ref']}`")
    lines.extend(
        [
            "",
            "## Review status",
            "",
            "This Q&A was explicitly promoted after source and privacy review.",
            "Quiz questions remain curated separately in `quiz/<track>.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def capture(args: argparse.Namespace) -> int:
    tracks = {path.name: path for path in discover_tracks()}
    if args.track not in tracks:
        print(f"FAIL: unknown learning track: {args.track}", file=sys.stderr)
        return 1
    source = args.source.split("#", 1)[0]
    source_path = repo_file(source).resolve()
    if not source.startswith("learning/") or not inside(source_path, LEARNING_DIR) or not source_path.is_file():
        print("FAIL: --source must point to an existing file under learning/", file=sys.stderr)
        return 1
    source_track = source_path.relative_to(LEARNING_DIR).parts[0]
    if source_track != args.track:
        print(f"FAIL: source belongs to track {source_track}, not {args.track}", file=sys.stderr)
        return 1
    combined = f"{args.question}\n{args.answer}\n{args.conversation_ref or ''}"
    if secret_found(combined):
        print("FAIL: possible secret-shaped value found; sanitize the Q&A before capture", file=sys.stderr)
        return 1

    slug = slugify(args.slug or args.question)
    if not slug:
        slug = "qa-" + hashlib.sha256(args.question.encode("utf-8")).hexdigest()[:12]
    destination = CANDIDATE_DIR / f"{slug}.md"
    if destination.exists():
        print(f"FAIL: candidate already exists: {relative_path(destination)}", file=sys.stderr)
        return 1
    today = date.today().isoformat()
    metadata: dict[str, Any] = {
        "title": f"Q&A Candidate — {args.track} — {slug}",
        "type": "qa-candidate",
        "status": "candidate",
        "track": args.track,
        "source": source,
        "source_hash": sha256(source_path),
        "question": args.question.strip(),
        "answer": args.answer.strip(),
        "created": today,
        "updated": today,
        "tags": [tag.strip() for tag in args.tags.split(",") if tag.strip()],
    }
    if args.conversation_ref:
        metadata["conversation_ref"] = args.conversation_ref.strip()
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    destination.write_text(candidate_body(metadata), encoding="utf-8")
    print(f"Captured sanitized Q&A candidate: {relative_path(destination)}")
    print("Next: review it, then run --promote with the candidate path.")
    return 0


def resolve_candidate(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = repo_file(path)
    return path.resolve()


def promote(args: argparse.Namespace) -> int:
    candidate = resolve_candidate(args.promote)
    if not inside(candidate, CANDIDATE_DIR) or candidate.suffix.lower() != ".md" or not candidate.is_file():
        print("FAIL: --promote must point to a candidate Markdown file under learning/qa-inbox/candidates/", file=sys.stderr)
        return 1
    tracks = {path.name: path for path in discover_tracks()}
    errors, warnings = validate_candidate(candidate, tracks)
    metadata, frontmatter_error = read_frontmatter(candidate)
    if frontmatter_error:
        errors.append(frontmatter_error)
    if metadata.get("status") == "promoted":
        print(f"Already promoted: {relative_path(candidate)}")
        return 0
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"WARN: {warning}")

    track = str(metadata["track"])
    canonical_dir = tracks[track] / "qa"
    canonical_path = (canonical_dir / candidate.stem).with_suffix(".md")
    if canonical_path.exists():
        print(f"FAIL: canonical Q&A already exists: {relative_path(canonical_path)}", file=sys.stderr)
        return 1
    today = date.today().isoformat()
    canonical_metadata: dict[str, Any] = {
        "title": metadata["title"].replace("Q&A Candidate", "Learning Q&A"),
        "type": "learning-qa",
        "status": "current",
        "track": track,
        "source": metadata["source"],
        "source_hash": metadata["source_hash"],
        "created": metadata.get("created", today),
        "updated": today,
        "question": metadata["question"],
        "answer": metadata["answer"],
    }
    if metadata.get("conversation_ref"):
        canonical_metadata["conversation_ref"] = metadata["conversation_ref"]
    canonical_dir.mkdir(parents=True, exist_ok=True)
    canonical_path.write_text(canonical_body(canonical_metadata), encoding="utf-8")

    metadata["status"] = "promoted"
    metadata["canonical_path"] = relative_path(canonical_path)
    metadata["updated"] = today
    candidate.write_text(candidate_body(metadata, promoted=True), encoding="utf-8")
    print(f"Promoted Q&A: {relative_path(canonical_path)}")
    print("Quiz remains a curated artifact; add a reviewed question to quiz/<track>.json separately.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="validate learning, quiz, and Q&A candidate references")
    modes.add_argument("--propose", action="store_true", help="report new/changed or uncovered learning notes")
    modes.add_argument("--capture", action="store_true", help="write an explicitly sanitized Q&A candidate")
    modes.add_argument("--promote", metavar="PATH", help="promote a reviewed candidate into learning/<track>/qa/")
    parser.add_argument("--write-state", action="store_true", help="write the current source-hash baseline (with --check/--propose)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable report")
    parser.add_argument("--track", help="learning track for --capture")
    parser.add_argument("--source", help="learning source path for --capture")
    parser.add_argument("--question", help="sanitized reusable question for --capture")
    parser.add_argument("--answer", help="sanitized evidence-backed answer for --capture")
    parser.add_argument("--conversation-ref", default="", help="metadata-only conversation reference; never raw chat")
    parser.add_argument("--tags", default="", help="comma-separated tags for --capture")
    parser.add_argument("--slug", default="", help="optional ASCII candidate slug")
    args = parser.parse_args(argv)

    if args.capture:
        missing = [name for name in ("track", "source", "question", "answer") if not getattr(args, name)]
        if missing:
            parser.error(f"--capture requires: {', '.join('--' + name.replace('_', '-') for name in missing)}")
        return capture(args)
    if args.promote:
        if args.write_state or args.json:
            parser.error("--promote does not support --write-state or --json")
        return promote(args)

    report, errors = build_report()
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if args.write_state:
        write_state(report)
        report["state_initialized"] = True
        report["state_written"] = relative_path(STATE_PATH)
    print_report(report, as_json=args.json)
    if not args.json:
        print("Result: OK")
    return 0


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("learning_sync.py")
    raise SystemExit(main())

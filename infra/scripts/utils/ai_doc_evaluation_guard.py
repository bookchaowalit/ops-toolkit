#!/usr/bin/env python3
"""Validate the AI Doc Assistant canonical evaluation pack."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, repo_file, display_path, skip_without_architecture  # noqa: E402


CASE_DIR = KNOWLEDGE_ROOT / "roles" / "pilots" / "ai-doc-assistant"
EVALUATION_SET = CASE_DIR / "evaluation-set.md"
RUN_RECORD = CASE_DIR / "evaluation-run-record.md"
CHECKLIST = CASE_DIR / "execution-gate-checklist.md"
README = CASE_DIR / "README.md"
_DOC_ASSISTANT = repo_file("projects/product/engineering/book-dev/github/bookchaowalit/private/ai-doc-assistant")
FIXTURES = (
    _DOC_ASSISTANT / "sample-documents" / "company-policy.txt",
    _DOC_ASSISTANT / "sample-documents" / "thai-legal-contract.txt",
)
PORTABLE_CI = os.environ.get("SOLO_EMPIRE_CI") == "1"

EXPECTED_IDS = tuple(
    [f"EVAL-TH-{index:02d}" for index in range(1, 9)]
    + [f"EVAL-EN-{index:02d}" for index in range(1, 9)]
    + [f"EVAL-NE-{index:02d}" for index in range(1, 3)]
    + [f"EVAL-INJ-{index:02d}" for index in range(1, 3)]
    + [f"EVAL-OPS-{index:02d}" for index in range(1, 3)]
)
ID_PATTERN = re.compile(r"^\|\s*(EVAL-(?:TH|EN|NE|INJ|OPS)-\d{2})\s*\|", re.MULTILINE)


def relative(path: Path) -> str:
    return display_path(path)


def extract_ids(path: Path) -> list[str]:
    return ID_PATTERN.findall(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check() -> int:
    skip_without_architecture("ai_doc_evaluation_guard.py")  # standalone: no solo-architecture checkout -> SKIP, not FAIL
    print("=" * 70)
    print("Solo Empire — AI Doc Assistant Evaluation Guard")
    print("=" * 70)
    errors: list[str] = []

    for path in (EVALUATION_SET, RUN_RECORD, CHECKLIST, README):
        if not path.is_file():
            errors.append(f"missing required file: {relative(path)}")

    missing_fixtures = [fixture for fixture in FIXTURES if not fixture.is_file()]
    if not PORTABLE_CI:
        for fixture in missing_fixtures:
            errors.append(f"missing required file: {relative(fixture)}")

    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1

    evaluation_ids = extract_ids(EVALUATION_SET)
    run_ids = extract_ids(RUN_RECORD)
    if evaluation_ids != list(EXPECTED_IDS):
        errors.append(
            "evaluation-set IDs must contain the canonical 22 IDs in order: "
            + ", ".join(EXPECTED_IDS)
        )
    if run_ids != list(EXPECTED_IDS):
        errors.append("evaluation-run-record IDs must match evaluation-set IDs in order")

    evaluation_text = EVALUATION_SET.read_text(encoding="utf-8")
    run_text = RUN_RECORD.read_text(encoding="utf-8")
    checklist_text = CHECKLIST.read_text(encoding="utf-8")
    readme_text = README.read_text(encoding="utf-8")
    required_text = (
        (EVALUATION_SET, "| Case count | 22:"),
        (EVALUATION_SET, "ready-for-review — not executed with provider"),
        (RUN_RECORD, "## Threshold reconciliation"),
        (RUN_RECORD, "All 22 cases have a result and reviewer"),
        (CHECKLIST, "evaluation-run-record.md"),
        (README, "evaluation-run-record.md"),
    )
    for path, marker in required_text:
        text = {
            EVALUATION_SET: evaluation_text,
            RUN_RECORD: run_text,
            CHECKLIST: checklist_text,
            README: readme_text,
        }[path]
        if marker not in text:
            errors.append(f"{relative(path)} missing marker: {marker}")

    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1

    print(f"  ✅ canonical evaluation IDs: {len(EXPECTED_IDS)}")
    print("  ✅ evaluation set and run record IDs match")
    print("  ✅ execution gate, run record, and case index are linked")
    print("  ✅ provider execution remains explicitly unclaimed")
    for fixture in FIXTURES:
        if fixture.is_file():
            print(f"  ✅ fixture: {relative(fixture)} sha256={sha256(fixture)}")
        else:
            print(f"  ⚠️ fixture deferred in portable CI: {relative(fixture)}")
    print("Result: OK")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("ai_doc_evaluation_guard.py")
    raise SystemExit(check())

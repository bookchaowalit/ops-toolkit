#!/usr/bin/env python3
"""Offline checks for the deterministic learning sync workflow."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import KNOWLEDGE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(KNOWLEDGE_ROOT / "learning", "knowledge-base checkout (SOLO_KNOWLEDGE_ROOT)")
RUNNER = ROOT / "infra" / "scripts" / "learning" / "learning_sync.py"
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "learning"))
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
import learning_sync as sync  # noqa: E402


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    check = run("--check")
    if check.returncode != 0 or "Result: OK" not in check.stdout:
        print("FAIL: learning sync check did not pass")
        print(check.stdout)
        print(check.stderr)
        return 1

    proposal = run("--propose", "--json")
    if proposal.returncode != 0:
        print("FAIL: learning sync proposal exited non-zero")
        print(proposal.stdout)
        print(proposal.stderr)
        return 1
    try:
        report = json.loads(proposal.stdout)
    except json.JSONDecodeError as exc:
        print(f"FAIL: learning sync proposal was not JSON: {exc}")
        return 1
    required_tracks = {"ai-agents", "business-systems", "platform-engineering", "psychology", "systems-networking"}
    if not required_tracks.issubset(set(report.get("tracks", []))):
        print("FAIL: learning sync report is missing expected tracks")
        return 1
    if not report.get("quiz_files"):
        print("FAIL: learning sync report found no quiz files")
        return 1

    secret = run(
        "--capture",
        "--track",
        "business-systems",
        "--source",
        "learning/business-systems/erpnext-business-logic-path.md",
        "--question",
        "What is the password=should-not-be-stored?",
        "--answer",
        "Reject this candidate",
    )
    if secret.returncode == 0 or "secret-shaped" not in secret.stderr:
        print("FAIL: learning sync did not reject secret-shaped candidate content")
        print(secret.stdout)
        print(secret.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="learning-sync-test-") as temporary:
        temporary_root = Path(temporary)
        original_inbox = sync.QA_INBOX
        original_candidates = sync.CANDIDATE_DIR
        original_discover_tracks = sync.discover_tracks
        fake_track = temporary_root / "business-systems"
        fake_track.mkdir()
        (fake_track / "README.md").write_text("# test track\n", encoding="utf-8")
        sync.QA_INBOX = temporary_root / "qa-inbox"
        sync.CANDIDATE_DIR = sync.QA_INBOX / "candidates"
        sync.discover_tracks = lambda: [fake_track]
        try:
            capture_args = argparse.Namespace(
                track="business-systems",
                source="learning/business-systems/erpnext-business-logic-path.md",
                question="Which evidence gate comes before invoicing?",
                answer="Delivery acceptance or the agreed milestone must pass first.",
                conversation_ref="conversation-summary:test",
                tags="business-rules,review",
                slug="invoice-evidence",
            )
            if sync.capture(capture_args) != 0:
                print("FAIL: learning sync capture did not create a candidate")
                return 1
            candidate = sync.CANDIDATE_DIR / "invoice-evidence.md"
            promote_args = argparse.Namespace(promote=str(candidate))
            if sync.promote(promote_args) != 0:
                print("FAIL: learning sync promote did not create a canonical note")
                return 1
            canonical = fake_track / "qa" / "invoice-evidence.md"
            if not canonical.is_file():
                print("FAIL: promoted canonical learning note is missing")
                return 1
        finally:
            sync.QA_INBOX = original_inbox
            sync.CANDIDATE_DIR = original_candidates
            sync.discover_tracks = original_discover_tracks

    print("OK: learning sync workflow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

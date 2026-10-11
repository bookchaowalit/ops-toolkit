#!/usr/bin/env python3
"""Offline tests for the central Science and R&D lifecycle contract."""

from __future__ import annotations

import csv
import contextlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "systems/research-and-development", "solo-architecture checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
from _env import repo_file, skip_without_knowledge  # noqa: E402

skip_without_knowledge("rnd_pipeline_test.py")  # needs solo-knowledge; skipped in CI when absent

from rnd_pipeline_guard import CONTRACT_PATH, TRACKER_PATH, load_contract, validate  # noqa: E402


class RndPipelineTests(unittest.TestCase):
    @contextlib.contextmanager
    def _without_external_checkout(self):
        owner = repo_file("projects/product/engineering/book-dev/github/bookchaowalit/private/ai-doc-assistant")
        original = {name: getattr(Path, name) for name in ("exists", "is_dir", "is_file")}
        with contextlib.ExitStack() as stack:
            for name, method in original.items():
                def probe(path, method=method):
                    return False if path.is_relative_to(owner) else method(path)
                stack.enter_context(patch.object(Path, name, probe))
            yield

    def test_absent_declared_external_evidence_is_unverified(self):
        warnings = []
        with self._without_external_checkout(), patch.dict(os.environ, {"SOLO_EMPIRE_CI": "1"}):
            self.assertEqual(validate(warnings=warnings), [])
        self.assertEqual(len(warnings), 5)
        self.assertTrue(all("unverified external evidence" in warning for warning in warnings))

    def test_required_external_checkout_must_be_present(self):
        with self._without_external_checkout(), patch.dict(os.environ, {"SOLO_EMPIRE_CI": "1"}):
            errors = validate(require_checkouts=True)
        self.assertTrue(any("checkout not present" in error for error in errors))

    def test_local_missing_external_checkout_is_strict_by_default(self):
        with self._without_external_checkout(), patch.dict(os.environ, {"SOLO_EMPIRE_CI": "0"}):
            errors = validate()
        self.assertTrue(any("checkout not present" in error for error in errors))

    def test_external_evidence_provenance_cannot_be_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = load_contract()
            contract["external_evidence"] = [{
                "repository": "bookchaowalit/ai-doc-assistant",
                "checkout_root": "projects/product/engineering/book-dev/github/bookchaowalit/private/ai-doc-assistant",
                "commit": "invalid-pin", "files": [{"path": "main.py", "blob": "1" * 40}],
            }]
            path = Path(directory) / "contract.yaml"
            path.write_text(yaml.safe_dump(contract), encoding="utf-8")
            self.assertTrue(any("external evidence" in error for error in validate(path)))

    def test_external_evidence_typo_is_not_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "initiatives.csv"
            path.write_text(TRACKER_PATH.read_text(encoding="utf-8").replace(
                "ai-doc-assistant/main.py", "ai-doc-assistant/main-typo.py"), encoding="utf-8")
            with self._without_external_checkout():
                errors = validate(tracker_path=path)
        self.assertTrue(any("main-typo.py" in error and "path does not exist" in error for error in errors))
    def test_checked_in_contract_and_tracker_pass(self):
        self.assertEqual(validate(), [])
        contract = load_contract()
        self.assertEqual(contract["contract"], "solo-empire.rnd-lifecycle.v1")
        self.assertEqual(contract["kind"], "research-and-development-lifecycle")
        self.assertEqual(len(contract["stages"]), 8)
        self.assertEqual(
            {binding["id"] for binding in contract["workspace_bindings"]},
            {"book-ai", "book-science", "book-research", "book-math", "book-social-science"},
        )
        self.assertTrue(CONTRACT_PATH.is_file())
        self.assertTrue(TRACKER_PATH.is_file())

    def test_tracker_has_end_to_end_records(self):
        with TRACKER_PATH.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(
            {row["initiative_id"] for row in rows},
            {"RND-0001", "RND-0002", "RND-0003", "RND-0004", "RND-0005"},
        )
        baseline = next(row for row in rows if row["initiative_id"] == "RND-0001")
        self.assertEqual(baseline["stage"], "dissemination")
        self.assertEqual(baseline["status"], "completed")
        pilot = next(row for row in rows if row["initiative_id"] == "RND-0002")
        self.assertEqual(pilot["workspace_path"], "projects/product/rnd/book-research")
        self.assertEqual(pilot["stage"], "dissemination")
        self.assertEqual(pilot["status"], "active")
        applied = next(row for row in rows if row["initiative_id"] == "RND-0003")
        self.assertEqual(applied["workspace_path"], "projects/product/rnd/book-ai")
        self.assertEqual(applied["decision"], "iterate")
        self.assertEqual(applied["status"], "active")
        fixture = next(row for row in rows if row["initiative_id"] == "RND-0004")
        self.assertEqual(fixture["workspace_path"], "projects/product/rnd/book-ai")
        self.assertEqual(fixture["stage"], "analysis")
        self.assertEqual(fixture["decision"], "iterate")
        self.assertEqual(fixture["status"], "active")
        runtime = next(row for row in rows if row["initiative_id"] == "RND-0005")
        self.assertEqual(runtime["workspace_path"], "projects/product/rnd/book-ai")
        self.assertEqual(runtime["stage"], "analysis")
        self.assertEqual(runtime["decision"], "iterate")
        self.assertEqual(runtime["status"], "active")

    def test_missing_evidence_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_tracker = Path(directory) / "initiatives.csv"
            shutil.copyfile(TRACKER_PATH, temp_tracker)
            text = temp_tracker.read_text(encoding="utf-8").replace(
                "systems/research-and-development/README.md",
                "systems/research-and-development/does-not-exist.md",
            )
            temp_tracker.write_text(text, encoding="utf-8")
            errors = validate(tracker_path=temp_tracker)
            self.assertTrue(any("does-not-exist.md" in error for error in errors))

    def test_duplicate_identifier_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_tracker = Path(directory) / "initiatives.csv"
            shutil.copyfile(TRACKER_PATH, temp_tracker)
            with temp_tracker.open("a", newline="", encoding="utf-8") as handle:
                handle.write(temp_tracker.read_text(encoding="utf-8").splitlines()[1] + "\n")
            errors = validate(tracker_path=temp_tracker)
            self.assertTrue(any("duplicate initiative_id" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

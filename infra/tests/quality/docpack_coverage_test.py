#!/usr/bin/env python3
"""Regression tests for the portfolio docpack coverage checker."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "utils"))

import docpack_coverage  # noqa: E402

ORIGINAL_RUN = docpack_coverage.run

REGISTRY = """
doc_types:
  overview: {number: '00'}
  prd: {number: '01'}
tiers:
  L1: [overview, prd]
systems:
  - {id: good, tier: L1, pack: packs/good}
  - {id: legacy, tier: L1, pack: packs/legacy, pdf_builder: legacy, doc_type_map: {overview: README.md, prd: spec.md}}
  - {id: gap, tier: L1, pack: packs/gap}
  - {id: absent, tier: L1, pack: packs/absent}
"""


def doc(doc_type: str, doc_id: str = "X-DOC-00") -> str:
    return f"---\ntitle: X\ndoc_id: {doc_id}\ndoc_type: {doc_type}\nversion: 0.1\nstatus: draft\n---\n# X\n"


class DocpackCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(os.environ, {"SOLO_EMPIRE_CI": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        reg = self.root / docpack_coverage.REGISTRY_PATH
        reg.parent.mkdir(parents=True)
        reg.write_text(REGISTRY, encoding="utf-8")
        good = self.root / "packs/good"
        good.mkdir(parents=True)
        (good / "docpack.yaml").write_text("system_id: good\n", encoding="utf-8")
        (good / "00-overview.md").write_text(doc("overview"), encoding="utf-8")
        (good / "01-prd.md").write_text(doc("prd", "X-DOC-01"), encoding="utf-8")
        legacy = self.root / "packs/legacy"
        legacy.mkdir(parents=True)
        (legacy / "README.md").write_text("# readme\n", encoding="utf-8")
        (legacy / "spec.md").write_text("# spec\n", encoding="utf-8")
        gap = self.root / "packs/gap"
        gap.mkdir(parents=True)
        (gap / "00-overview.md").write_text(doc("overview").replace("doc_id: X-DOC-00\n", ""), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def by_id(self) -> dict:
        return {r["id"]: r for r in docpack_coverage.run(self.root)["systems"]}

    def test_complete_pack_and_legacy_map(self) -> None:
        results = self.by_id()
        self.assertTrue(results["good"]["complete"])
        self.assertEqual(results["good"]["found"], {"overview": "00-overview.md", "prd": "01-prd.md"})
        self.assertTrue(results["legacy"]["complete"])

    def test_gaps_are_reported(self) -> None:
        gap = self.by_id()["gap"]
        self.assertFalse(gap["complete"])
        self.assertEqual(gap["missing"], ["prd"])
        self.assertTrue(any("doc_id" in e for e in gap["errors"]))
        self.assertTrue(any("docpack.yaml" in e for e in gap["errors"]))
        self.assertEqual(self.by_id()["absent"]["missing"], ["overview", "prd"])

    def make_pdf(self, name: str, mtime: int = 200, pack: str = "good") -> Path:
        pdf = self.root / "packs" / pack / "pdf" / name
        pdf.parent.mkdir(exist_ok=True)
        pdf.write_bytes(b"%PDF")
        os.utime(pdf, (mtime, mtime))
        return pdf

    def save_registry(self, registry: dict) -> None:
        (self.root / docpack_coverage.REGISTRY_PATH).write_text(
            yaml.safe_dump(registry), encoding="utf-8")

    def cli(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        # main() skips unless the registry folder exists; point it at the fixture so the
        # result does not depend on the caller's SOLO_REGISTRY_DIR.
        catalog = self.root / "repository-catalog"
        with patch.object(docpack_coverage, "run", side_effect=lambda **kw: ORIGINAL_RUN(self.root, **kw)), \
                patch.object(docpack_coverage, "REGISTRY_DIR", catalog), \
                patch.object(docpack_coverage, "registry_path", lambda relative: catalog / relative):
            with contextlib.redirect_stdout(output):
                status = docpack_coverage.main(list(args))
        return status, output.getvalue()

    def test_pdf_staleness(self) -> None:
        self.make_pdf("X-DOC-00_overview_v0.1.pdf", mtime=1)
        state = self.by_id()["good"]["pdf"]
        self.assertTrue(state["built"])
        self.assertEqual(state["stale"], ["00-overview.md"])
        self.assertEqual(state["missing"], ["01-prd.md"])

    def test_fresh_sibling_cannot_hide_stale_pdf(self) -> None:
        pack = self.root / "packs/good"
        for md in pack.glob("*.md"):
            os.utime(md, (100, 100))
        self.make_pdf("X-DOC-00_overview_v0.1.pdf", mtime=1)
        self.make_pdf("X-DOC-01_prd_v0.1.pdf")
        self.assertEqual(self.by_id()["good"]["pdf"]["stale"], ["00-overview.md"])
        status, output = self.cli("--system", "good", "--require-pdf", "--json")
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(output)["ok"])

    def test_missing_pdf_and_version_mismatch(self) -> None:
        self.make_pdf("X-DOC-01_prd_v0.1.pdf")
        for md in (self.root / "packs/good").glob("*.md"):
            os.utime(md, (100, 100))
        for name in (None, "X-DOC-00_overview_v0.10.pdf", "X-DOC-00_overview_v0.0.pdf"):
            with self.subTest(name=name):
                if name:
                    self.make_pdf(name)
                state = self.by_id()["good"]["pdf"]
                self.assertEqual(state["missing"], ["00-overview.md"])
                self.assertEqual(state["stale"], [])
                status, output = self.cli("--system", "good", "--require-pdf", "--json")
                self.assertEqual(status, 1)
                self.assertFalse(json.loads(output)["ok"])

    def test_actual_filename_normalization_and_non_sources(self) -> None:
        pack = self.root / "packs/good"
        (pack / "00-overview.md").write_text(
            doc("overview", '\"X-DOC-00\"').replace("version: 0.1", "version: 'v0.1'"), encoding="utf-8")
        (pack / "README.md").write_text(doc("overview", "IGNORED"), encoding="utf-8")
        (pack / "02-not-a-source.md").write_text("# notes\n", encoding="utf-8")
        self.make_pdf("X-DOC-00_overview_v0.1.pdf")
        self.make_pdf("X-DOC-01_prd_v0.1.pdf")
        for md in pack.glob("*.md"):
            os.utime(md, (100, 100))
        state = self.by_id()["good"]["pdf"]
        self.assertEqual(state["missing"], [])
        self.assertEqual(state["stale"], [])
        self.assertIn("README.md", state["skipped"])
        self.assertIn("02-not-a-source.md", state["skipped"])

    def test_legacy_version_suffix_and_mapped_runbook(self) -> None:
        registry = docpack_coverage.load_registry(self.root)
        registry["systems"][1]["doc_type_map"] = {"overview": "02-PRD-v0.2.md", "prd": "runbooks/README.md"}
        self.save_registry(registry)
        pack = self.root / "packs/legacy"
        source = pack / "02-PRD-v0.2.md"
        source.write_text(doc("overview", "EV3-DOC-02").replace("version: 0.1", "version: 0.4"), encoding="utf-8")
        runbook = pack / "runbooks/README.md"
        runbook.parent.mkdir()
        runbook.write_text(doc("prd", "EV3-RB-00"), encoding="utf-8")
        os.utime(source, (100, 100))
        os.utime(runbook, (100, 100))
        self.make_pdf("EV3-DOC-02_PRD_v0.4.pdf", pack="legacy")
        state = self.by_id()["legacy"]["pdf"]
        self.assertEqual(state["stale"], [])
        self.assertEqual(state["missing"], ["runbooks/README.md"])
        self.make_pdf("EV3-RB-00_README_v0.1.pdf", mtime=1, pack="legacy")
        self.assertEqual(self.by_id()["legacy"]["pdf"]["stale"], ["runbooks/README.md"])

    def test_legacy_without_doc_id_is_explicitly_skipped(self) -> None:
        state = self.by_id()["legacy"]["pdf"]
        self.assertEqual(state["missing"], [])
        self.assertIn("README.md", state["skipped"])
        self.assertIn("spec.md", state["skipped"])

    def test_unmatched_override_fails_without_fuzzy_remapping(self) -> None:
        registry = docpack_coverage.load_registry(self.root)
        registry["systems"] = []
        registry["tiers"]["L0"] = ["overview", "prd"]
        registry["repo_discovery"] = {"roots": ["nested"], "default_tier": "L0",
                                      "tier_overrides": {"org/old/app": "L1"}}
        (self.root / "nested/org/new/app/.git").mkdir(parents=True)
        pack = self.root / "nested/org/new/app/DOCPACK"
        pack.mkdir()
        (pack / "docpack.yaml").write_text("system_id: app\n", encoding="utf-8")
        (pack / "00-overview.md").write_text(doc("overview"), encoding="utf-8")
        (pack / "01-prd.md").write_text(doc("prd", "X-DOC-01"), encoding="utf-8")
        self.save_registry(registry)
        report = docpack_coverage.run(self.root, scope="repos")
        self.assertTrue(report["systems"][0]["complete"])
        self.assertEqual(report["systems"][0]["tier"], "L0")
        self.assertFalse(report["ok"])
        self.assertIn("org/old/app", report["errors"][0])
        for args in (("--scope", "repos"), ("--scope", "repos", "--require-pdf", "--json")):
            status, output = self.cli(*args)
            self.assertEqual(status, 1)
            self.assertIn("org/old/app", output)
        self.assertTrue(docpack_coverage.run(self.root, scope="systems")["ok"])
        self.assertEqual(docpack_coverage.run(self.root, scope="systems")["errors"], [])

    def test_registered_override_exemption_uses_exact_pack_path(self) -> None:
        registry = docpack_coverage.load_registry(self.root)
        registry["systems"] = [{"id": "registered", "tier": "L1", "pack": "nested/org/apps/app/DOCPACK"}]
        registry["repo_discovery"] = {"roots": ["nested"], "tier_overrides": {"org/apps/app": "L1"}}
        (self.root / "nested/org/apps/app/.git").mkdir(parents=True)
        self.save_registry(registry)
        report = docpack_coverage.run(self.root, scope="repos")
        self.assertTrue(report["ok"])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["errors"], [])
        registry["systems"][0]["pack"] = "elsewhere/org/apps/app/DOCPACK"
        registry["repo_discovery"]["tier_overrides"] = {"org/old/app": "L1"}
        self.save_registry(registry)
        self.assertFalse(docpack_coverage.run(self.root, scope="repos")["ok"])

    def test_absent_checkout_override_warns_only_in_ci(self) -> None:
        registry = docpack_coverage.load_registry(self.root)
        registry["systems"] = []
        registry["repo_discovery"] = {"roots": ["nested"], "tier_overrides": {"org/apps/absent": "L1"}}
        self.save_registry(registry)
        for root_exists in (False, True):
            with self.subTest(root_exists=root_exists):
                if root_exists:
                    (self.root / "nested").mkdir()
                with patch.dict(os.environ, {"SOLO_EMPIRE_CI": "1"}):
                    report = docpack_coverage.run(self.root, scope="repos")
                    self.assertTrue(report["ok"])
                    self.assertEqual(report["errors"], [])
                    self.assertIn("org/apps/absent", report["warnings"][0])
                    status, output = self.cli("--scope", "repos", "--require-pdf", "--json")
                    self.assertEqual(status, 0)
                    self.assertTrue(json.loads(output)["ok"])
                with patch.dict(os.environ, {"SOLO_EMPIRE_CI": "0"}):
                    self.assertFalse(docpack_coverage.run(self.root, scope="repos")["ok"])

    def test_ci_skipped_system_does_not_fail_require_pdf(self) -> None:
        registry = docpack_coverage.load_registry(self.root)
        registry["systems"] = [{"id": "ci", "tier": "L1",
                                "pack": "projects/product/engineering/book-dev/github/org/apps/ci/DOCPACK"}]
        self.save_registry(registry)
        with patch.dict(os.environ, {"SOLO_EMPIRE_CI": "1"}):
            status, output = self.cli("--require-pdf", "--json")
            report = json.loads(output)
            self.assertEqual(status, 0)
            self.assertTrue(report["ok"])
            self.assertEqual(report["systems"][0]["skipped"], "checkout absent in CI")

    def test_ci_existing_checkout_with_missing_pack_fails(self) -> None:
        registry = docpack_coverage.load_registry(self.root)
        repo = "projects/product/engineering/book-dev/github/org/apps/ci"
        registry["systems"] = [{"id": "ci", "tier": "L1", "pack": f"{repo}/DOCPACK"}]
        (self.root / repo / ".git").mkdir(parents=True)
        self.save_registry(registry)
        with patch.dict(os.environ, {"SOLO_EMPIRE_CI": "1"}):
            status, output = self.cli("--require-pdf", "--json")
            report = json.loads(output)
            self.assertEqual(status, 1)
            self.assertFalse(report["ok"])
            self.assertNotIn("skipped", report["systems"][0])

    def test_require_pdf_preserves_override_error_without_rows(self) -> None:
        registry = docpack_coverage.load_registry(self.root)
        registry["systems"] = []
        registry["repo_discovery"] = {"roots": ["nested"], "tier_overrides": {"org/apps/missing": "L1"}}
        self.save_registry(registry)
        with patch.dict(os.environ, {"SOLO_EMPIRE_CI": "0"}):
            status, output = self.cli("--scope", "repos", "--require-pdf", "--json")
            report = json.loads(output)
            self.assertEqual(status, 1)
            self.assertEqual(report["total"], 0)
            self.assertFalse(report["ok"])
            self.assertIn("org/apps/missing", report["errors"][0])

    def test_no_pdfs_reports_each_missing_source(self) -> None:
        state = self.by_id()["good"]["pdf"]
        self.assertFalse(state["built"])
        self.assertEqual(state["count"], 0)
        self.assertEqual(state["stale"], [])
        self.assertEqual(state["missing"], ["00-overview.md", "01-prd.md"])

    def test_standard_builder_preserves_version_suffix_in_stem(self) -> None:
        pack = self.root / "packs/good"
        source = pack / "02-PRD-v0.2.md"
        source.write_text(doc("prd", "X-DOC-02"), encoding="utf-8")
        os.utime(source, (100, 100))
        self.make_pdf("X-DOC-02_PRD-v0.2_v0.1.pdf")
        state = docpack_coverage.pdf_state(pack, [source])
        self.assertEqual(state["missing"], [])
        self.assertEqual(state["stale"], [])

    def test_repo_discovery_requires_card_with_verdict(self) -> None:
        reg = self.root / docpack_coverage.REGISTRY_PATH
        reg.write_text(REGISTRY.replace("tiers:\n", "tiers:\n  L0: [product-card]\n").replace(
            "doc_types:\n", "doc_types:\n  product-card: {number: '00'}\n") +
            "repo_discovery:\n  roots: [nested]\n  default_tier: L0\n  always_required: [product-card]\n"
            "  tier_overrides: {org/apps/promoted: L1}\n", encoding="utf-8")
        for name in ("carded", "bare", "promoted"):
            (self.root / f"nested/org/apps/{name}/.git").mkdir(parents=True)
        card = self.root / "nested/org/apps/carded/DOCPACK"
        card.mkdir()
        (card / "docpack.yaml").write_text("system_id: carded\n", encoding="utf-8")
        (card / "00-product-card.md").write_text(doc("product-card").replace("status: draft", "status: draft\nverdict: showcase"), encoding="utf-8")
        report = docpack_coverage.run(self.root, scope="repos")
        rows = {r["id"]: r for r in report["systems"]}
        self.assertTrue(rows["org/apps/carded"]["complete"])
        self.assertEqual(rows["org/apps/carded"]["verdict"], "showcase")
        self.assertEqual(rows["org/apps/bare"]["missing"], ["product-card"])
        self.assertEqual(rows["org/apps/promoted"]["missing"], ["overview", "prd", "product-card"])
        (card / "00-product-card.md").write_text(doc("product-card").replace("status: draft", "status: draft\nverdict: maybe"), encoding="utf-8")
        self.assertFalse(docpack_coverage.run(self.root, scope="repos", only="org/apps/carded")["systems"][0]["complete"])

    def test_unknown_system_rejected(self) -> None:
        with self.assertRaises(ValueError):
            docpack_coverage.run(self.root, only="nope")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Regression tests for the portfolio docpack coverage checker."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "utils"))

import docpack_coverage  # noqa: E402

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

    def test_pdf_staleness(self) -> None:
        import os
        pdf_dir = self.root / "packs/good/pdf"
        pdf_dir.mkdir()
        pdf = pdf_dir / "X-DOC-00_overview_v0.1.pdf"
        pdf.write_bytes(b"%PDF")
        os.utime(pdf, (1, 1))
        state = self.by_id()["good"]["pdf"]
        self.assertTrue(state["built"])
        self.assertEqual(sorted(state["stale"]), ["00-overview.md", "01-prd.md"])

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

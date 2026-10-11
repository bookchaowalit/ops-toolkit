#!/usr/bin/env python3
"""Contract tests for the shared building-block services catalog."""

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "platform" / "building_block_services.py"
SPEC = importlib.util.spec_from_file_location("building_block_services", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
bbs = importlib.util.module_from_spec(SPEC)
sys.modules["building_block_services"] = bbs
SPEC.loader.exec_module(bbs)


class BuildingBlockCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = bbs.load_catalog(check=False)
        cls.refs = bbs.known_provider_refs()

    def errors_for(self, mutate) -> list[str]:
        catalog = copy.deepcopy(self.catalog)
        mutate(catalog)
        return bbs.validate(catalog, self.refs)

    def service(self, catalog, service_id):
        return next(s for s in catalog["services"] if s["id"] == service_id)

    def test_repository_catalog_is_valid(self):
        self.assertEqual(bbs.validate(self.catalog, self.refs), [])

    def test_covers_every_horizontal_category(self):
        self.assertEqual(
            {c["id"] for c in self.catalog["categories"]},
            {
                "identity", "kyc", "compliance", "payments", "communication", "media", "data", "search",
                "location", "analytics", "observability", "security", "ai", "commerce", "saas", "trust",
                "devinfra",
            },
        )
        self.assertGreaterEqual(len(self.catalog["services"]), 150)

    def test_regulated_capabilities_are_bought_and_gated(self):
        services = {s["id"]: s for s in bbs.resolve(self.catalog)}
        for service_id in ("ekyc", "liveness", "sanctions-screening", "pep-screening", "wallet", "ndid"):
            self.assertEqual(services[service_id]["decision"], "buy", service_id)
            self.assertTrue(services[service_id]["approval"], service_id)

    def test_existing_central_boundaries_are_recorded(self):
        services = {s["id"]: s for s in bbs.resolve(self.catalog)}
        self.assertEqual(services["llm"]["status"], "planned")  # generation runs in bookchaowalit/solo-ai-gateway
        self.assertEqual(services["line-messaging"]["impl"], [])  # infra/api retired 2026-10-11 (archived in solo-platform-api)

    def test_build_ceiling_is_enforced(self):
        def make_ekyc_build(catalog):
            self.service(catalog, "ekyc")["decision"] = "build"

        self.assertTrue(any("build ceiling" in e for e in self.errors_for(make_ekyc_build)))

    def test_difficulty_five_must_be_bought(self):
        def make_hybrid(catalog):
            self.service(catalog, "ddos-protection")["decision"] = "hybrid"

        self.assertTrue(any("must be bought" in e for e in self.errors_for(make_hybrid)))

    def test_regulated_data_requires_approval(self):
        def drop_approval(catalog):
            self.service(catalog, "liveness")["approval"] = False

        self.assertTrue(any("requires approval" in e for e in self.errors_for(drop_approval)))

    def test_apps_may_not_embed_provider_secrets(self):
        def allow_secrets(catalog):
            catalog["policy"]["apps_embed_provider_secrets"] = True

        self.assertTrue(any("apps_embed_provider_secrets" in e for e in self.errors_for(allow_secrets)))

    def test_unknown_provider_ref_and_missing_impl_are_rejected(self):
        if self.refs is None:
            self.skipTest("bookchaowalit/solo-integrations checkout absent (SOLO_INTEGRATIONS_ROOT)")
        def break_refs(catalog):
            self.service(catalog, "email")["refs"] = ["not-a-provider"]
            self.service(catalog, "llm")["impl"] = ["infra/api/missing.ts"]

        errors = self.errors_for(break_refs)
        self.assertTrue(any("not-a-provider" in e for e in errors))
        self.assertTrue(any("missing.ts" in e for e in errors))

    def test_duplicate_ids_and_unknown_fields_are_rejected(self):
        def duplicate(catalog):
            catalog["services"].append(dict(self.service(catalog, "email"), secret_key="x"))

        errors = self.errors_for(duplicate)
        self.assertTrue(any("duplicate service id: email" in e for e in errors))
        self.assertTrue(any("unknown fields" in e for e in errors))

    def test_summary_and_filters(self):
        summary = bbs.summarize(self.catalog)
        self.assertEqual(summary["total_services"], len(self.catalog["services"]))
        self.assertIn("llm", summary["adopt_now"])
        kyc = bbs.filter_services(self.catalog, category="kyc")
        self.assertTrue(kyc and all(s["category"] == "kyc" for s in kyc))
        self.assertEqual(bbs.main(["check"]), 0)


if __name__ == "__main__":
    unittest.main()

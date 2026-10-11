#!/usr/bin/env python3
"""Offline checks for the social identity isolation guard."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra/scripts/utils"))

from identity_isolation_guard import audit  # noqa: E402


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class IdentityIsolationGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_public_brand_passes(self):
        write(self.root, "accounts/x/brand/demo/account.yaml",
              "identity_layer: faceless-brand\ntrust_zone: z2-separated\nlogin_ref: vault://brands/demo\n")
        self.assertEqual(audit(self.root, legacy=set()), ([], []))

    def test_isolated_zone_is_rejected(self):
        write(self.root, "accounts/x/brand/demo/account.yaml", "trust_zone: z3-isolated\n")
        errors, _ = audit(self.root, legacy=set())
        self.assertEqual(len(errors), 1)
        self.assertIn("must not be stored in Git", errors[0])

    def test_anonymous_folder_is_rejected(self):
        write(self.root, "accounts/reddit/anonymous/p/account.yaml", "identity_layer: faceless-brand\n")
        errors, _ = audit(self.root, legacy=set())
        self.assertEqual(len(errors), 1)

    def test_legacy_record_is_warning(self):
        relative = "accounts/x/anonymous/p/account.yaml"
        write(self.root, relative, "identity_layer: anonymous\n")
        errors, warnings = audit(self.root, legacy={relative})
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)

    def test_email_and_secret_fields_are_rejected(self):
        write(self.root, "accounts/x/brand/demo/account.yaml",
              "trust_zone: z1-public\nnotes: login is demo@example.com\npassword: hunter2\n")
        errors, _ = audit(self.root, legacy=set())
        self.assertTrue(any("email address" in item for item in errors))
        self.assertTrue(any("password" in item for item in errors))

    def test_unknown_zone_is_rejected(self):
        write(self.root, "accounts/x/brand/demo/account.yaml", "trust_zone: z9\n")
        errors, _ = audit(self.root, legacy=set())
        self.assertIn("unknown trust_zone", errors[0])

    def test_repository_accounts_have_no_new_violations(self):
        errors, _ = audit(ROOT)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

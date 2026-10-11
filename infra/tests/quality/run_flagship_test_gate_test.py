#!/usr/bin/env python3
"""Tests for the Tier A flagship nested test gate."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

INFRA_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = INFRA_ROOT / "scripts" / "quality" / "run_flagship_test_gate.py"
TARGETS = INFRA_ROOT / "scripts" / "quality" / "flagship_test_targets.yaml"


def load_module():
    spec = importlib.util.spec_from_file_location("run_flagship_test_gate", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FlagshipTestGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def test_targets_file_loads_and_has_tier_a_suites(self) -> None:
        data = self.mod.load_targets(TARGETS)
        ids = {t["id"] for t in data["targets"]}
        self.assertIn("bookreading-frontend", ids)
        self.assertIn("booktrading-backend", ids)
        self.assertIn("portfolio-frontend-data-products", ids)
        self.assertIn("localcrm-backend", ids)
        runnable = [t for t in data["targets"] if t.get("kind") in {"npm", "go", "python"}]
        self.assertGreaterEqual(len(runnable), 5)

    def test_list_mode_exits_zero(self) -> None:
        code = self.mod.main(["--list"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

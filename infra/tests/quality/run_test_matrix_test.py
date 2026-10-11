#!/usr/bin/env python3
"""Tests for the infra test-matrix runner discovery rules."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

INFRA_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = INFRA_ROOT / "scripts" / "quality" / "run_test_matrix.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("run_test_matrix", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RunTestMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def test_discovers_known_modules(self) -> None:
        modules = self.runner.discover_modules()
        rels = {self.runner.rel_test_path(p) for p in modules}
        self.assertIn("quality/smoke_test.py", rels)
        self.assertIn("security/security_test.py", rels)
        self.assertNotIn("fixtures", " ".join(rels))

    def test_default_skips_benchmark_and_optional_job_e2e(self) -> None:
        self.assertIsNotNone(
            self.runner.should_skip(
                "api/api_performance_benchmark.py",
                include_optional=False,
                include_benchmarks=False,
            )
        )
        self.assertIsNotNone(
            self.runner.should_skip(
                "data_lake/job_data_e2e_test.py",
                include_optional=False,
                include_benchmarks=False,
            )
        )
        self.assertIsNone(
            self.runner.should_skip(
                "quality/smoke_test.py",
                include_optional=False,
                include_benchmarks=False,
            )
        )

    def test_only_filter_selects_group(self) -> None:
        modules = self.runner.discover_modules()
        selected = self.runner.filter_modules(modules, ["security"])
        rels = [self.runner.rel_test_path(p) for p in selected]
        self.assertTrue(rels)
        self.assertTrue(all(r.startswith("security/") for r in rels))


if __name__ == "__main__":
    unittest.main()

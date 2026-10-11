#!/usr/bin/env python3
"""Portable catalog validation still rejects invalid paths and missing root files."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra/scripts/utils"))
import production_tools_catalog_guard as guard  # noqa: E402


class ProductionToolsPortabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="production-tools-portability-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.owner = "projects/product/engineering/book-dev/github/example/book-tool"
        (self.root / "docs").mkdir()
        (self.root / "docs/evidence.md").write_text("Recorded gate evidence")
        self.tool = {
            "id": "example-tool", "name": "Example", "owning_repo": self.owner,
            "cli_entry_point": self.owner + "/src/cli.py", "cli_command": "example render",
            "input_schema": self.owner + "/schemas/request.json", "outputs": ["PDF"],
            "runtimes": [], "gate_status": "experimental", "evidence": ["docs/evidence.md"],
        }

    def errors(self, *, tool=None, ci="1", required=False, known=True):
        errors, warnings = [], []
        with patch.object(guard, "ROOT", self.root), patch.dict("os.environ", {"SOLO_EMPIRE_CI": ci}):
            guard.check_tool(self.tool if tool is None else tool, 0, errors,
                             known_repos={self.owner} if known else set(),
                             require_checkouts=required, warnings=warnings)
        return errors, warnings

    def test_only_registered_absent_checkout_is_portable(self):
        errors, warnings = self.errors()
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("SKIP", warnings[0])
        self.assertTrue(self.errors(known=False)[0])

    def test_local_and_explicit_required_modes_remain_strict(self):
        self.assertTrue(self.errors(ci="0")[0])
        self.assertTrue(self.errors(required=True)[0])

    def test_present_checkout_missing_entry_still_fails_in_ci(self):
        (self.root / self.owner).mkdir(parents=True)
        errors, warnings = self.errors()
        self.assertTrue(any("cli_entry_point" in error for error in errors))
        self.assertEqual(warnings, [])

    def symlink(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("host does not permit symlink creation")

    def test_checkout_file_is_not_an_absent_checkout(self):
        path = self.root / self.owner
        path.parent.mkdir(parents=True)
        path.write_text("wrong type")
        self.assertTrue(self.errors()[0])

    def test_broken_symlink_is_not_an_absent_checkout(self):
        path = self.root / self.owner
        path.parent.mkdir(parents=True)
        self.symlink(path, self.root / "missing-target")
        self.assertTrue(self.errors()[0])

    def test_root_schema_and_evidence_are_never_skipped(self):
        for field, value in (("input_schema", "schemas/root.json"), ("evidence", ["docs/missing.md"])):
            tool = copy.deepcopy(self.tool)
            tool[field] = value
            self.assertTrue(any(field in error for error in self.errors(tool=tool)[0]))

    def test_root_owned_tool_entry_is_not_optional(self):
        tool = copy.deepcopy(self.tool)
        tool.update(owning_repo=".", cli_entry_point="services/missing.py", input_schema="typed request")
        self.assertTrue(any("cli_entry_point" in error for error in self.errors(tool=tool)[0]))

    def test_invalid_paths_cannot_use_missing_checkout_exception(self):
        for field in ("owning_repo", "cli_entry_point", "input_schema", "evidence"):
            for invalid in ("../outside", "/absolute", self.owner + "/../escape", "C:\\outside", self.owner + "//child", self.owner + "/bad\nfile"):
                with self.subTest(field=field, invalid=invalid):
                    tool = copy.deepcopy(self.tool)
                    tool[field] = [invalid] if field == "evidence" else invalid
                    self.assertTrue(self.errors(tool=tool)[0])

    def test_entry_must_belong_to_declared_owner(self):
        tool = copy.deepcopy(self.tool)
        tool["cli_entry_point"] = "docs/evidence.md"
        self.assertTrue(any("belong" in error for error in self.errors(tool=tool)[0]))

    def test_symlink_escape_is_rejected(self):
        self.symlink(self.root / "docs/outside", self.root.parent)
        tool = copy.deepcopy(self.tool)
        tool["evidence"] = ["docs/outside/other.md"]
        self.assertTrue(any("contained" in error for error in self.errors(tool=tool)[0]))

    def test_cli_symlink_to_sibling_repository_is_rejected(self):
        repo = self.root / self.owner
        repo.mkdir(parents=True)
        self.symlink(repo / "linked", self.root / "docs")
        tool = copy.deepcopy(self.tool)
        tool.update(cli_entry_point=self.owner + "/linked/evidence.md", input_schema="typed request")
        self.assertTrue(any("belong" in error for error in self.errors(tool=tool)[0]))

    def test_cli_symlink_within_owner_is_valid(self):
        repo = self.root / self.owner
        (repo / "src").mkdir(parents=True)
        (repo / "src/cli.py").write_text("pass")
        self.symlink(repo / "linked", repo / "src")
        tool = copy.deepcopy(self.tool)
        tool.update(cli_entry_point=self.owner + "/linked/cli.py", input_schema="typed request")
        self.assertEqual(self.errors(tool=tool)[0], [])

    def test_repository_refs_map_onto_registered_checkouts(self):
        repositories = {"book-tool": {"path": self.owner}, "book-production-platform": {"path": "catalog-repo"}}
        tool = dict(self.tool, owning_repo="bookchaowalit/book-tool", cli_entry_point="src/cli.py",
                    input_schema="schemas/request.json", evidence=["docs/plan.md#gate"])
        errors: list[str] = []
        local = guard.localize_tool(tool, repositories, errors)
        self.assertEqual(errors, [])
        self.assertEqual(local["owning_repo"], self.owner)
        self.assertEqual(local["cli_entry_point"], self.owner + "/src/cli.py")
        self.assertEqual(local["input_schema"], self.owner + "/schemas/request.json")
        self.assertEqual(local["evidence"], ["catalog-repo/docs/plan.md#gate"])
        self.assertEqual(tool["owning_repo"], "bookchaowalit/book-tool")  # the catalog itself is untouched
        guard.localize_tool(dict(tool, owning_repo="bookchaowalit/unknown"), repositories, errors)
        self.assertTrue(any("not a runtime-boundaries repository" in error for error in errors))

    def test_legacy_hub_relative_tool_passes_through(self):
        self.assertIs(guard.localize_tool(self.tool, {}, []), self.tool)


if __name__ == "__main__":
    unittest.main()

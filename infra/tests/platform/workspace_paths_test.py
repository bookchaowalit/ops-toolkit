#!/usr/bin/env python3
"""Tests for the canonical domain workspace path registry."""

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import KNOWLEDGE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(KNOWLEDGE_ROOT / "projects/workspaces.yaml", "solo-knowledge checkout (SOLO_KNOWLEDGE_ROOT)")
sys.path.insert(0, str(REPO_ROOT / "infra" / "scripts" / "utils"))

from _env import KNOWLEDGE_ROOT  # noqa: E402
from workspace_paths import (  # noqa: E402
    EXTERNAL_WORKSPACE_PATHS,
    REGISTRY_PATH,
    WORKSPACE_REGISTRY,
    WORKSPACE_LAYOUT,
    load_workspace_registry,
    workspace_data_path,
    workspace_path,
    workspace_record,
    workspace_tools_path,
)


class WorkspacePathTests(unittest.TestCase):
    def test_registry_has_active_and_planned_entries(self):
        self.assertTrue(REGISTRY_PATH.is_file())
        self.assertEqual(len(WORKSPACE_REGISTRY), 45)
        self.assertEqual(len(WORKSPACE_LAYOUT), 40)
        self.assertEqual(
            sum(entry["status"] == "planned" for entry in WORKSPACE_REGISTRY),
            5,
        )

    def test_registry_matches_first_party_workspace_directories(self):
        actual = {
            path.name: path
            for path in (KNOWLEDGE_ROOT / "projects").glob("*/*/book-*")
            if path.is_dir()
        }
        registered = {
            slug: workspace_path(slug)
            for slug in WORKSPACE_LAYOUT
            if workspace_record(slug)["path"] not in EXTERNAL_WORKSPACE_PATHS
        }
        self.assertEqual(registered, actual)

    def test_planned_workspace_is_registered_without_being_runtime_active(self):
        record = workspace_record("book-engineering")
        self.assertEqual(record["status"], "planned")
        with self.assertRaisesRegex(ValueError, "planned"):
            workspace_path("book-engineering")

    def test_every_registered_workspace_resolves_inside_projects(self):
        for slug in WORKSPACE_LAYOUT:
            if workspace_record(slug)["path"] in EXTERNAL_WORKSPACE_PATHS:
                continue  # local-only Book Dev checkout lives in solo-empire, not here
            path = workspace_path(slug)
            self.assertEqual(path, path.resolve())
            self.assertTrue(path.is_relative_to(KNOWLEDGE_ROOT / "projects"))
            self.assertTrue(path.is_dir(), slug)

    def test_moved_learning_workspace_uses_life_bu(self):
        self.assertEqual(
            workspace_path("book-learning"),
            KNOWLEDGE_ROOT / "projects" / "life" / "learning" / "book-learning",
        )

    def test_workspace_subpaths_are_canonical(self):
        self.assertEqual(
            workspace_tools_path("book-finance"),
            KNOWLEDGE_ROOT / "projects" / "money" / "finance" / "book-finance" / "tools",
        )
        self.assertEqual(
            workspace_data_path("book-health", "workouts.csv"),
            KNOWLEDGE_ROOT / "projects" / "life" / "wellness" / "book-health" / "data" / "workouts.csv",
        )

    def test_unknown_workspace_slug_fails_loudly(self):
        with self.assertRaisesRegex(KeyError, "book-unknown"):
            workspace_path("book-unknown")


class WorkspaceRegistryValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry = Path(self.temporary.name) / "workspaces.yaml"
        self.record = {
            "slug": "book-example",
            "name": "Example",
            "bu": "product",
            "group": "rnd",
            "path": "projects/product/rnd/book-example",
            "status": "active",
        }

    def load(self, *records):
        self.registry.write_text(
            yaml.safe_dump({"version": 1, "workspaces": list(records)}),
            encoding="utf-8",
        )
        return load_workspace_registry(self.registry)

    def test_reusable_loader_preserves_optional_scope_without_requiring_directories(self):
        self.assertEqual(self.load(self.record), (self.record,))
        scoped = {**self.record, "scope": "Tools | methods\nand applications"}
        self.assertEqual(self.load(scoped), (scoped,))
        self.assertFalse((self.registry.parent / self.record["path"]).exists())

    def test_scope_must_be_nonempty_text_when_present(self):
        for scope in (None, "", "  ", 123):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, "scope"):
                self.load({**self.record, "scope": scope})

    def test_path_must_match_business_unit_group_and_slug(self):
        for field, value in (("bu", "life"), ("group", "learning"), ("slug", "book-other")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "path"):
                self.load({**self.record, field: value})

    def test_paths_reject_noncanonical_syntax_and_traversal(self):
        paths = (
            "/projects/product/rnd/book-example",
            "./projects/product/rnd/book-example",
            "projects/product//rnd/book-example",
            "projects/product/./rnd/book-example",
            "projects/product/rnd/../book-example",
            "projects/product/rnd/book-example/",
            "projects/product/rnd/extra/book-example",
            "projects/product/book-example",
            "projects\\product\\rnd\\book-example",
            " projects/product/rnd/book-example",
            "projects/product/rnd/book-example\n",
        )
        for path in paths:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "path"):
                self.load({**self.record, "path": path})

    def test_duplicate_paths_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate workspace path"):
            self.load(self.record, dict(self.record))

    def test_duplicate_slugs_at_different_canonical_paths_are_rejected(self):
        other = {**self.record, "bu": "life", "path": "projects/life/rnd/book-example"}
        with self.assertRaisesRegex(ValueError, "Duplicate workspace slug"):
            self.load(self.record, other)

    def test_existing_required_metadata_and_status_validation_remain(self):
        with self.assertRaisesRegex(ValueError, "missing fields.*name"):
            self.load({**self.record, "name": ""})
        with self.assertRaisesRegex(ValueError, "Invalid workspace status"):
            self.load({**self.record, "status": "unknown"})


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for the local mobile release gate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts"))

from utils.mobile_release_gate import (  # noqa: E402
    audit_app,
    build_audit_report,
    build_check_commands,
    run_emulator_smoke,
)


class MobileReleaseGateTests(unittest.TestCase):
    def _write(self, root: Path, relative: str, content: str = "") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _package(self, root: Path, value: dict) -> None:
        self._write(root, "package.json", json.dumps(value))

    def test_missing_mobile_root_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = build_audit_report(Path(temp_dir) / "missing")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["summary"]["total"], 0)
        self.assertEqual(report["network"], "not-used")
        self.assertEqual(report["credentials"], "not-read")

    def test_expo_scaffold_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "sample-expo"
            app.mkdir()
            self._package(
                app,
                {
                    "dependencies": {"expo": "latest"},
                    "scripts": {"lint": "expo lint"},
                },
            )
            self._write(
                app,
                "app.json",
                json.dumps(
                    {
                        "expo": {
                            "icon": "./assets/icon.png",
                            "android": {"package": "com.example.sample"},
                        }
                    }
                ),
            )
            self._write(app, "src/App.tsx", "export const App = () => null;\n")
            self._write(
                app,
                ".github/workflows/build.yml",
                "steps:\n  - run: npm run lint || true\n",
            )
            report = audit_app(app)
        self.assertEqual(report["framework"], "expo")
        self.assertEqual(report["status"], "blocked")
        self.assertIn("CI workflow suppresses command failures", report["blockers"])
        self.assertIn("Expo eas.json is missing", report["blockers"])
        self.assertTrue(any("missing Expo asset" in item for item in report["blockers"]))
        self.assertIn("no automated test source found", report["blockers"])

    def test_complete_android_fixture_is_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "sample-android"
            app.mkdir()
            self._write(app, "build.gradle.kts", "plugins {}\n")
            self._write(app, "gradlew", "#!/bin/sh\n")
            self._write(app, "gradle.lockfile", "# locked\n")
            self._write(app, "app/src/main/AndroidManifest.xml", "<manifest />\n")
            self._write(app, "app/src/main/java/MainActivity.kt", "class MainActivity\n")
            self._write(app, "app/src/main/java/Helper.kt", "class Helper\n")
            self._write(app, "app/src/test/java/HelperTest.kt", "class HelperTest\n")
            self._write(
                app,
                ".github/workflows/build.yml",
                "steps:\n  - run: ./gradlew testDebugUnitTest assembleDebug\n",
            )
            report = audit_app(app)
        self.assertEqual(report["framework"], "android-native")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["blockers"], [])

    def test_inventory_summarizes_frameworks_and_blockers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expo = root / "expo-app"
            expo.mkdir()
            self._package(expo, {"dependencies": {"expo": "latest"}})
            self._write(expo, "app.json", json.dumps({"expo": {"android": {"package": "x.y"}}}))
            swift = root / "swift-app"
            swift.mkdir()
            self._write(swift, "Package.swift", "// swift-tools-version: 5.9\n")
            report = build_audit_report(root)
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["ready"], 0)
        self.assertEqual(report["summary"]["frameworks"], {"expo": 1, "swift": 1})

    def test_check_plan_never_installs_dependencies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "vite-app"
            app.mkdir()
            self._package(
                app,
                {
                    "devDependencies": {"vite": "latest"},
                    "scripts": {"build": "vite build", "lint": "eslint .", "test": "vitest"},
                },
            )
            self._write(app, "src/App.tsx", "export const App = () => null;\n")
            self._write(app, "src/main.tsx", "export {};\n")
            self._write(app, "src/App.test.tsx", "test('app', () => {});\n")
            self._write(app, "tsconfig.json", "{}\n")
            self._write(app, "package-lock.json", "{}\n")
            self._write(app, ".github/workflows/build.yml", "steps:\n  - run: npm test\n")
            audit = audit_app(app)
            commands = build_check_commands(app, audit)
        flattened = " ".join(" ".join(command or []) for _, command in commands)
        self.assertNotIn("npm install", flattened)
        self.assertNotIn("npm ci", flattened)
        self.assertEqual([name for name, _ in commands], ["lint", "typecheck", "unit-test", "build"])

    @mock.patch("utils.mobile_release_gate._resolve_tool", return_value=None)
    def test_emulator_mode_fails_before_start_when_tools_are_missing(self, _which):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_emulator_smoke(
                avd="missing-avd",
                apk=Path(temp_dir) / "missing.apk",
                package_id="com.example.missing",
                flow=Path(temp_dir) / "missing.yaml",
                boot_timeout=1,
                test_timeout=1,
            )
        self.assertEqual(report["status"], "blocked")
        self.assertIn("missing tool: emulator", report["blockers"])
        self.assertIn("missing tool: adb", report["blockers"])
        self.assertIn("missing tool: maestro", report["blockers"])


if __name__ == "__main__":
    unittest.main()

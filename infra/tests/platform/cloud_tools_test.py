"""Offline tests for registry-only cloud CLI diagnostics."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "platform"))
from cloud_providers import load_registry, validate_registry
from cloud_tools import build_tools_doctor


class CloudToolsTest(unittest.TestCase):
    def test_default_never_executes_cli(self):
        runner = Mock()
        report = build_tools_doctor(which=lambda _: "C:\\Tools\\cli.exe", process_runner=runner)
        self.assertEqual(report["summary"]["installed"], 17)
        runner.assert_not_called()

    def test_version_output_is_allowlisted_and_not_logged(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "railway 4.12.3 private-data", "private error"))
        report = build_tools_doctor(
            which=lambda name: "C:\\Tools\\railway.cmd" if name == "railway" else None,
            check_versions=True,
            process_runner=runner,
        )
        railway = next(item for item in report["tools"] if item["provider_id"] == "railway")
        self.assertEqual(railway["version"], "4.12.3")
        self.assertEqual(railway["version_status"], "ok")
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("C:\\Tools", json.dumps(report))
        self.assertEqual(runner.call_args.args[0], ["C:\\Tools\\railway.cmd", "--version"])
        self.assertEqual(runner.call_args.kwargs["timeout"], 8)

    def test_failure_timeout_and_unrecognized_output(self):
        for result, expected in (
            (subprocess.CompletedProcess([], 1, "private", "private"), "cli_version_failed"),
            (subprocess.TimeoutExpired("gh", 8, output="private"), "cli_version_timeout"),
            (OSError("private"), "cli_version_unavailable"),
        ):
            with self.subTest(expected=expected):
                runner = Mock(side_effect=result) if isinstance(result, BaseException) else Mock(return_value=result)
                report = build_tools_doctor(
                    which=lambda name: "gh.exe" if name == "gh" else None,
                    check_versions=True, process_runner=runner,
                )
                item = next(tool for tool in report["tools"] if tool["provider_id"] == "github")
                self.assertEqual(item["error_class"], expected)
                self.assertNotIn("private", json.dumps(report))
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "unknown", ""))
        report = build_tools_doctor(which=lambda _: "cli", check_versions=True, process_runner=runner)
        self.assertTrue(all(tool["version_status"] == "unrecognized" for tool in report["tools"]))

    def test_colored_version_with_build_suffix_is_sanitized(self):
        runner = Mock(return_value=subprocess.CompletedProcess(
            [], 0, "\x1b[32mrunpodctl v2.14.0-dd55bcf\x1b[0m", "",
        ))
        report = build_tools_doctor(
            which=lambda name: "runpodctl.exe" if name == "runpodctl" else None,
            check_versions=True, process_runner=runner,
        )
        item = next(tool for tool in report["tools"] if tool["provider_id"] == "runpod")
        self.assertEqual(item["version"], "2.14.0")
        self.assertEqual(item["version_status"], "ok")
        self.assertNotIn("dd55bcf", json.dumps(report))

    def test_heroku_timeout_is_bounded_and_analytics_are_disabled(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "heroku/11.10.1", ""))
        build_tools_doctor(
            which=lambda name: "heroku.cmd" if name == "heroku" else None,
            check_versions=True, process_runner=runner,
        )
        self.assertEqual(runner.call_args.kwargs["timeout"], 40)
        self.assertEqual(runner.call_args.kwargs["env"]["HEROKU_SKIP_ANALYTICS"], "1")
        registry = load_registry()
        registry["providers"][0]["cli_version_timeout_seconds"] = 1000
        self.assertTrue(any("1 to 60" in error for error in validate_registry(registry)))

    def test_registry_rejects_mutation_in_version_command(self):
        registry = load_registry()
        registry["providers"][0]["cli_version_command"] = ["wrangler", "deploy"]
        self.assertTrue(any("version-only" in error for error in validate_registry(registry)))
        runner = Mock()
        report = build_tools_doctor(
            registry=registry, which=lambda name: "wrangler" if name == "wrangler" else None,
            check_versions=True, process_runner=runner,
        )
        self.assertEqual(report["tools"][0]["error_class"], "invalid_cli_version_command")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Offline tests for the hosted AI gateway client token provisioning helper.

No Vercel or network call is made; VERCEL_BIN points at a local fake CLI.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "scripts" / "security" / "provision_ai_gateway_client.py"

FAKE_VERCEL = """
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(" ".join(sys.argv[2:]) + "\\n" + sys.stdin.read(), encoding="utf-8")
sys.exit(int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0)
"""


def run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


class ProvisionAiGatewayClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_out_file_prints_only_hash_entry(self) -> None:
        out = self.dir / "secrets" / "ev3-web.token"
        result = run(["--subject", "ev3-web", "--out-file", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        token = out.read_text(encoding="utf-8")
        self.assertGreaterEqual(len(token), 43)
        entry = json.loads(result.stdout)
        self.assertEqual(entry, {
            "subject": "ev3-web",
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "scopes": ["ai:generate", "api:read"],
        })
        self.assertNotIn(token, result.stdout + result.stderr)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)

    def test_refuses_overwrite_and_emits_nothing(self) -> None:
        out = self.dir / "existing.token"
        out.write_text("keep", encoding="utf-8")
        result = run(["--subject", "ev3-web", "--out-file", str(out)])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(out.read_text(encoding="utf-8"), "keep")

    def test_vercel_receives_token_on_stdin_not_argv(self) -> None:
        fake = self.dir / "fake_vercel.py"
        fake.write_text(FAKE_VERCEL, encoding="utf-8")
        capture = self.dir / "capture.txt"
        vercel_bin = f"{Path(sys.executable).as_posix()} {fake.as_posix()} {capture.as_posix()}"
        result = run(
            ["--subject", "ev3-web", "--scopes", "ai:generate", "--vercel-env", "EV3_AI_GATEWAY_TOKEN", "--expires-at", "2027-03-31T00:00:00Z"],
            env={"VERCEL_BIN": vercel_bin},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        argv_line, token = capture.read_text(encoding="utf-8").split("\n", 1)
        self.assertEqual(argv_line, "env add EV3_AI_GATEWAY_TOKEN production")
        entry = json.loads(result.stdout)
        self.assertEqual(entry["token_hash"], hashlib.sha256(token.encode("utf-8")).hexdigest())
        self.assertEqual(entry["scopes"], ["ai:generate"])
        self.assertEqual(entry["expires_at"], "2027-03-31T00:00:00Z")
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_failed_vercel_add_emits_no_entry(self) -> None:
        fake = self.dir / "fake_vercel.py"
        fake.write_text(FAKE_VERCEL, encoding="utf-8")
        capture = self.dir / "capture.txt"
        # argv[2] == "3" makes the fake CLI exit with status 3.
        vercel_bin = f"{Path(sys.executable).as_posix()} {fake.as_posix()} {capture.as_posix()}"
        result = run(
            ["--subject", "ev3-web", "--vercel-env", "EV3_AI_GATEWAY_TOKEN"],
            env={"VERCEL_BIN": vercel_bin.replace(capture.as_posix(), capture.as_posix() + " 3")},
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("exited with 3", result.stderr)

    def test_rejects_wildcard_scope_and_missing_destination(self) -> None:
        self.assertEqual(run(["--subject", "ev3-web", "--scopes", "*", "--out-file", str(self.dir / "t")]).returncode, 2)
        self.assertEqual(run(["--subject", "ev3-web"]).returncode, 2)
        self.assertEqual(run(["--subject", "bad subject", "--out-file", str(self.dir / "t")]).returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

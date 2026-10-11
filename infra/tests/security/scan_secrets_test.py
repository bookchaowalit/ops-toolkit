#!/usr/bin/env python3
"""Offline tests for the builtin credential-shaped literal scanner.

Fake tokens are assembled at runtime so this file never contains a
credential-shaped literal itself.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "security"))

import scan_secrets  # noqa: E402

FAKE_AWS = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
FAKE_GH = "gh" + "p_" + "a" * 36
FAKE_ANT = "sk-" + "ant-" + "api03-" + "x" * 30
FAKE_GOOGLE = "AI" + "za" + "B" * 35
FAKE_KEY = "-----BEGIN " + "RSA PRIVATE KEY-----"


def make_repo(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


class ScanSecretsTest(unittest.TestCase):
    def test_detects_token_shapes_without_printing_values(self) -> None:
        root = make_repo({
            "a.py": f'KEY = "{FAKE_AWS}"\n',
            "docs/b.md": f"token {FAKE_GH}\nok line\n{FAKE_ANT}\n",
            "c.txt": f"{FAKE_GOOGLE}\n{FAKE_KEY}\n",
            "untracked.txt": "",
        })
        (root / "untracked.txt").unlink()
        (root / "ignored.txt").write_text(FAKE_GH, encoding="utf-8")
        findings = scan_secrets.builtin_scan(root)
        self.assertEqual(sorted(findings), sorted([
            "a.py:1: aws-access-key-id",
            "docs/b.md:1: github-token",
            "docs/b.md:3: anthropic-api-key",
            "c.txt:1: google-api-key",
            "c.txt:2: private-key-block",
        ]))
        out = io.StringIO()
        with redirect_stdout(out):
            code = scan_secrets.main(["--engine", "builtin", "--root", str(root)])
        self.assertEqual(code, 1)
        for value in (FAKE_AWS, FAKE_GH, FAKE_ANT, FAKE_GOOGLE):
            self.assertNotIn(value, out.getvalue())

    def test_ignores_slugs_short_prefixes_and_allowlisted_lines(self) -> None:
        root = make_repo({
            "slug.md": "dusk-to-dawn-ant-roadmap and task-ant-colony\nsk-ant- prefix only\nghp_ short\n",
            "allowed.txt": f"ALLOWME {FAKE_GH}\n",
            ".gitleaks.toml": "[allowlist]\nregexes = ['''ALLOWME''']\n",
        })
        self.assertEqual(scan_secrets.builtin_scan(root), [])
        out = io.StringIO()
        with redirect_stdout(out):
            code = scan_secrets.main(["--engine", "builtin", "--root", str(root)])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

"""Tests for the non-printing Infisical secret writer."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

from infisical_secrets import store_secret  # noqa: E402


def test_secret_value_is_not_in_cli_arguments_and_temp_file_is_removed():
    captured = {}

    def fake_run(command, **kwargs):
        file_path = Path(command[command.index("--file") + 1])
        captured["content"] = file_path.read_text()
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    with patch("infisical_secrets.shutil.which", return_value="/usr/bin/infisical"), \
         patch("infisical_secrets.subprocess.run", side_effect=fake_run):
        store_secret("GOOGLE_REFRESH_TOKEN", "refresh-token-test")

    assert captured["content"] == "GOOGLE_REFRESH_TOKEN=refresh-token-test\n"
    assert "refresh-token-test" not in " ".join(captured["command"])
    temp_path = Path(captured["command"][captured["command"].index("--file") + 1])
    assert not temp_path.exists()


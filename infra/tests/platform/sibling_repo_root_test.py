#!/usr/bin/env python3
"""Sibling checkouts resolve across the hub's public/private visibility folders."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
import _env  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        owner = Path(temp_dir) / "github" / "owner"
        public, private = owner / "public", owner / "private"
        for path in (public / "platform-ops-cli", public / "shared", private / "shared", private / "secret-docs"):
            path.mkdir(parents=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith("SOLO_")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(_env, "SIBLINGS_PARENT", public):
            assert _env.sibling_repo_root("secret-docs") == private / "secret-docs"
            # The same-visibility sibling wins when both exist.
            assert _env.sibling_repo_root("shared") == public / "shared"
            # Missing everywhere: keep the documented ../<repo> default.
            assert _env.sibling_repo_root("absent") == public / "absent"
            os.environ["SOLO_ARCHITECTURE_ROOT"] = str(owner / "elsewhere")
            assert _env.sibling_repo_root("architecture-docs") == owner / "elsewhere"
        flat = Path(temp_dir) / "flat"
        (flat / "other").mkdir(parents=True)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(_env, "SIBLINGS_PARENT", flat):
            assert _env.sibling_repo_root("missing") == flat / "missing"
    print("sibling repo root tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

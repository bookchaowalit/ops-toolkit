"""Locations of sibling repositories for tests; absent siblings make tests skip, never fail.

Resolution mirrors scripts/utils/_env.py: SOLO_<NAME>_ROOT, else ../<repo> next to the
solo-ops checkout. solo-ops has no path back to the registry hub.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

OPS_ROOT = Path(__file__).resolve().parents[3]


def sibling(repo: str) -> Path:
    short = repo[len("solo-"):] if repo.startswith("solo-") else repo
    override = os.environ.get(f"SOLO_{short.upper().replace('-', '_')}_ROOT")
    return Path(override).expanduser() if override else OPS_ROOT.parent / repo


ARCHITECTURE_ROOT = sibling("solo-architecture")
KNOWLEDGE_ROOT = sibling("solo-knowledge")
SKILLS_ROOT = sibling("solo-skills")


def exit_unless_exists(path: Path, label: str) -> None:
    """For script-style tests: print a SKIP line and exit 0 when `path` is missing."""
    if not Path(path).exists():
        print(f"SKIP: {label} not found at {path}")
        raise SystemExit(0)


def requires(path: Path, label: str):
    """unittest decorator: skip the test (class or method) when the sibling path is missing."""
    import unittest

    return unittest.skipUnless(Path(path).exists(), f"{label} not found at {path}")

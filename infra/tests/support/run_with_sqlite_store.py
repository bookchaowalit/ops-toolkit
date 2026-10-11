#!/usr/bin/env python3
"""Run a script as __main__ with every store replaced by its SQLite test double (see sqlite_doubles.py).

Usage: run_with_sqlite_store.py <script.py> [args...]
For tests that exercise a script through a subprocess; the script otherwise talks only to the live APIs.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sqlite_doubles  # noqa: E402,F401  (patches the stores on import)

script = Path(sys.argv[1]).resolve()
sys.argv = [str(script), *sys.argv[2:]]
sys.path.insert(0, str(script.parent))
runpy.run_path(str(script), run_name="__main__")

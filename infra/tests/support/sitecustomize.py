"""Test-only: put this directory on PYTHONPATH and every Python child process (a subprocess of a subprocess
included) talks to the SQLite test double instead of a kit cell API, over the database named by
SOLO_EMPIRE_DB_PATH. A process without SOLO_EMPIRE_DB_PATH is left untouched. With
SOLO_EMPIRE_TEST_DOUBLES=1 every store (tasks, decisions, habits, notifications, cells) uses its double
(sqlite_doubles.py), for runtimes such as the dashboard that start Python scripts.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

_DB = os.environ.get("SOLO_EMPIRE_DB_PATH")
if _DB and os.environ.get("SOLO_EMPIRE_TEST_DOUBLES") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sqlite_doubles  # noqa: E402,F401  (patches every store on import)
elif _DB:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sqlite_cell_store  # noqa: E402  (also isolates the cell API client switches)

    import cell_store  # noqa: E402

    _opener = []

    def _lazy_opener(cell, *args, **kwargs):
        if not _opener:
            _opener.append(sqlite_cell_store.sqlite_cell_opener(sqlite3.connect(_DB)))
        return _opener[0](cell, *args, **kwargs)

    cell_store.open_cell_store = _lazy_opener

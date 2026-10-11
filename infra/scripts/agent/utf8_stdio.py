#!/usr/bin/env python3
"""Keep direct agent CLI output UTF-8 across supported host consoles."""

from __future__ import annotations

import sys
from typing import Any


def _reconfigure(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        # A detached or already-closed console should not change the CLI's
        # JSON/error contract while it is shutting down.
        return


def configure_utf8_stdio() -> None:
    """Configure text output streams without requiring PYTHONIOENCODING."""
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)

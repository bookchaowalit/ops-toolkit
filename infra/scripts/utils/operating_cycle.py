#!/usr/bin/env python3
"""Canonical entry point for the weekly planning cycle.

The implementation remains import-compatible with the original ``ai_cycle``
module so existing local automation can transition without duplicating logic.
"""

from ai_cycle import main


if __name__ == "__main__":
    raise SystemExit(main())

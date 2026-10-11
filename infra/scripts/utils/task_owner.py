"""The canonical user owner of every task.

Task assignments in the Tasks API reference the owner by this stable id and name. It replaces the
single seed row of the retired local ``collaborators`` table.
"""

from __future__ import annotations

CANONICAL_OWNER = {"id": "1", "name": "Owner"}


def canonical_owner() -> dict[str, str]:
    """A copy of the canonical owner (``id`` and ``name``)."""
    return dict(CANONICAL_OWNER)

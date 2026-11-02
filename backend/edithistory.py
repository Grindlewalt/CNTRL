"""The edit history (nov2; docs/nov2-contract.md): every batch of agent writes the
host applies to a project becomes a commit on a hidden ref, so nothing an agent wrote
or overwrote is lost, and a file or a whole turn can be put back. Not the operator's
git: main, HEAD, the index and the files are untouched (gitsafe.snapshot), and
refs/jav3/* is never served by /git or pushed.

An orchestrator item's copy (a work key) records onto its own branch instead
(worktrees.Work.branch): that branch IS its history.
"""
from __future__ import annotations

from .worktrees import HISTORY_REF

__all__ = ["HISTORY_REF", "after_apply"]


async def after_apply(key: str, op_id: str | None, res: dict) -> str | None:
    """Called by workspace_xfer.apply_guest_writes once a batch has landed (`res` is its
    result). Returns the commit made, or None. Never raises: history must not fail a
    turn. Implemented by agent A."""
    return None

"""The edit history (nov2; docs/nov2-contract.md): every batch of agent writes the
host applies to a project becomes a commit on a hidden ref, so nothing an agent wrote
or overwrote is lost, and a file or a whole turn can be put back. Not the operator's
git: main, HEAD, the index and the files are untouched (gitsafe.snapshot), and
refs/jav3/* is never served by /git or pushed.

An orchestrator item's copy (a work key) records onto its own branch instead
(worktrees.Work.branch): that branch IS its history.
"""
from __future__ import annotations

import asyncio
import logging

from . import gitsafe, runtime, worktrees
from .worktrees import HISTORY_REF

__all__ = ["HISTORY_REF", "after_apply", "parse_message"]

log = logging.getLogger("cntrl.edithistory")
TIMEOUT = 20.0


def _conversation_id(op_id):
    cid = runtime.conversation_id.get()
    if cid is None and op_id:
        try:
            from .vm import broker
            env = broker.get_turn(op_id)
            cid = env.conversation_id if env else None
        except Exception:
            cid = None
    return cid


def build_message(cid, op_id, applied, deleted, note: str = "") -> str:
    paths = list(applied) + list(deleted)
    head = ", ".join(paths[:3]) + (", ..." if len(paths) > 3 else "")
    first = f"#{cid if cid is not None else '-'} · {len(paths)} files · {head}"
    if note:
        first = f"{note} · {first}"
    lines = [first, ""]
    if cid is not None:
        lines.append(f"Conversation: {cid}")
    if op_id:
        lines.append(f"Op: {op_id}")
    if applied:
        lines.append("Applied: " + ", ".join(applied))
    if deleted:
        lines.append("Deleted: " + ", ".join(deleted))
    return "\n".join(lines) + "\n"


def parse_message(msg: str) -> dict:
    out = {"conversation_id": None, "op": None, "applied": [], "deleted": [],
           "line": msg.split("\n", 1)[0]}
    for ln in msg.split("\n")[1:]:
        k, _, v = ln.partition(": ")
        if k == "Conversation" and v.strip().isdigit():
            out["conversation_id"] = int(v)
        elif k == "Op":
            out["op"] = v.strip()
        elif k in ("Applied", "Deleted"):
            out[k.lower()] = [p for p in v.split(", ") if p]
    return out


async def _run(key, op_id, res, note="") -> str | None:
    w = worktrees.resolve(key)
    if not w.git_dir.exists():
        return None
    msg = build_message(_conversation_id(op_id), op_id, res.get("applied") or [],
                        res.get("deleted") or [], note)
    if w.branch:
        base = await gitsafe.rev(w.git_dir, f"refs/jav3/base/{w.job}")
        return await gitsafe.snapshot(w.git_dir, w.dir, f"refs/heads/{w.branch}", msg,
                                      start=base or "HEAD")
    return await gitsafe.snapshot(w.git_dir, w.dir, HISTORY_REF, msg)


async def after_apply(key: str, op_id: str | None, res: dict, note: str = "") -> str | None:
    """Called by workspace_xfer.apply_guest_writes once a batch has landed (`res` is its
    result). Returns the commit made, or None. Never raises."""
    try:
        return await asyncio.wait_for(_run(key, op_id, res or {}, note), TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("edit history for %s skipped: %r", key, e)
        return None

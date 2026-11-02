"""Operator API for the edit history (backend/edithistory.py): list what agents wrote,
show an entry's diff, put a file or a whole entry back. Cookie login only (require_user):
a device token cannot reach it."""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import edithistory, gitsafe, taintpaths, worktrees
from .auth import require_user
from .config import settings
from .fsutil import safe_join
from .writes import PROTECTED

router = APIRouter(prefix="/api/projects", tags=["history"],
                   dependencies=[Depends(require_user)])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$", re.I)
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
DIFF_CAP = 200_000
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbd89b8"


class RestoreBody(BaseModel):
    path: str | None = None
    force: bool = False


def _project(slug: str):
    if not SLUG_RE.match(slug or "") or ".." in slug or worktrees.is_work_key(slug):
        raise HTTPException(400, "bad project name")
    d = settings.projects_dir / slug
    g = d / ".git"
    if not g.exists():
        raise HTTPException(404, "no such project (or it has no git directory)")
    return d, g


async def _entry(g, sha: str) -> str:
    if not SHA_RE.match(sha or ""):
        raise HTTPException(400, "bad sha")
    full = await gitsafe.rev(g, sha)
    if not full:
        raise HTTPException(404, "no such entry")
    rc, _, _ = await gitsafe.git(g, "merge-base", "--is-ancestor", full,
                                 worktrees.HISTORY_REF)
    if rc != 0:
        raise HTTPException(404, "no such entry in this project's history")
    return full


async def _blob(g, spec: str) -> bytes | None:
    proc = await asyncio.create_subprocess_exec(
        "git", *gitsafe.HARDEN, f"--git-dir={g}", "cat-file", "blob", spec,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), 30)
    return out if proc.returncode == 0 else None


async def _touched(g, sha: str) -> tuple[str, list[str]]:
    """(parent or empty tree, the paths the entry changed)."""
    parent = await gitsafe.rev(g, f"{sha}^")
    base = parent or EMPTY_TREE
    _, out, _ = await gitsafe.git(g, "diff-tree", "-r", "--name-only", "-z",
                                  "--no-renames", base, sha, check=True)
    return base, [p for p in out.split("\0") if p]


def _guard(d, rel: str):
    top = rel.split("/", 1)[0]
    if top in PROTECTED or top.casefold() == ".context.json":
        raise HTTPException(400, f"cannot restore into {top}")
    try:
        return safe_join(d, rel)
    except Exception:
        raise HTTPException(400, "path escapes the project")


@router.get("/{slug}/history")
async def history(slug: str, limit: int = 50, path: str | None = None):
    d, g = _project(slug)
    limit = max(1, min(int(limit), 500))
    if await gitsafe.rev(g, worktrees.HISTORY_REF) is None:
        return {"entries": []}
    args = ["log", worktrees.HISTORY_REF, f"-n{limit}", "--grep=^Applied: ", "--grep=^Deleted: ",
            "--format=%H%x1f%ct%x1f%B%x1e"]  # the base commit under the first entry is HEAD, not an entry
    if path:
        args += ["--", path]
    _, out, _ = await gitsafe.git(g, *args, check=True)
    entries = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, when, msg = rec.split("\x1f", 2)
        m = edithistory.parse_message(msg)
        entries.append({"sha": sha, "when": int(when),
                        "conversation_id": m["conversation_id"],
                        "files": {"applied": m["applied"], "deleted": m["deleted"]},
                        "message": m["line"]})
    return {"entries": entries}


@router.get("/{slug}/history/{sha}")
async def history_diff(slug: str, sha: str):
    d, g = _project(slug)
    full = await _entry(g, sha)
    base, _ = await _touched(g, full)
    _, out, _ = await gitsafe.git(g, "diff", "--no-color", "--no-ext-diff", base, full,
                                  check=True)
    capped = len(out) > DIFF_CAP
    return {"sha": full, "diff": out[:DIFF_CAP], "truncated": capped}


@router.post("/{slug}/history/{sha}/restore")
async def history_restore(slug: str, sha: str, body: RestoreBody | None = None):
    body = body or RestoreBody()
    d, g = _project(slug)
    full = await _entry(g, sha)
    base, touched = await _touched(g, full)
    if body.path is not None:
        if body.path not in touched:
            raise HTTPException(400, "that entry did not touch that path")
        targets = [body.path]
    else:
        targets = touched
    plan = []          # (rel, dest, old bytes or None)
    changed = []
    for rel in targets:
        dest = _guard(d, rel)
        mine = await _blob(g, f"{full}:{rel}")
        cur = dest.read_bytes() if dest.is_file() else None
        if cur != mine:
            changed.append(rel)
        plan.append((rel, dest, await _blob(g, f"{base}:{rel}")))
    if changed and not body.force:
        raise HTTPException(409, {"error": "changed since that entry", "changed": changed})
    applied, deleted = [], []
    for rel, dest, old in plan:
        if old is None:
            if dest.is_file() or dest.is_symlink():
                dest.unlink()
            deleted.append(rel)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(old)
            applied.append(rel)
    taintpaths.record(slug, applied + deleted, False)   # an operator restore is clean
    new = await edithistory.after_apply(
        slug, None, {"applied": applied, "deleted": deleted}, note=f"restore of {full[:8]}")
    return {"restored": applied, "deleted": deleted, "entry": new,
            "forced": changed if body.force else []}

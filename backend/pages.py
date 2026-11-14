"""Published pages: a file or folder of static documents at its own link.

An agent asks with the `publish` tool; the request waits in the Queue until the
operator approves it (or publishes at once in yolo, permissions.auto_approve).
Approval copies a SNAPSHOT of the files into <data_dir>/pages/<id>/ and nothing
else: the pages server (pages_server.py) never reads a project folder, so a
later edit in the project changes nothing that is live until it is published
again.

What is refused, in every mode: a symlink or anything resolving outside the
project, anything that is not a regular file, a tree over the size or count
caps, and a file holding the VALUE of a stored secret (the same scan the write
chokepoint runs; the reason names the secret, never the value).

Files the agent wrote this turn sit in the box's write buffer until the turn
ends or a 60-second checkpoint; request() and approve() both pull that buffer
home first (gitgate.flush_guest_writes, the way the git tools do), so a file
written a moment ago is in the snapshot.

Events: page_publish_requested (settled by the decision), page_published,
page_rejected, page_unpublished, page_refused.
"""
import asyncio
import json
import logging
import os
import secrets as _tokens
import shutil
from pathlib import Path

from . import permissions, secrets as secrets_mod, security, worktrees
from .config import settings
from .db import get_db

log = logging.getLogger(__name__)

MAX_BYTES = 50 * 1024 * 1024
MAX_FILES = 1000
MAX_FILE_BYTES = 25 * 1024 * 1024
NAME_MAX = 120


class PageError(Exception):
    """The request cannot be made or decided; the message is for the agent/operator."""


def root() -> Path:
    return settings.data_dir / "pages"


def page_dir(pid: str) -> Path:
    return root() / pid


def base_url() -> str:
    """Where the operator's browser reaches the pages server."""
    if settings.pages_base_url.strip():
        return settings.pages_base_url.strip().rstrip("/")
    try:
        from .lan import lan_ips
        ips = lan_ips()
    except Exception:  # noqa: BLE001 — links fall back to loopback
        ips = []
    return f"http://{ips[0] if ips else '127.0.0.1'}:{settings.pages_port}"


def url_of(pid: str) -> str:
    return f"{base_url()}/p/{pid}/"


# --- collecting the files -------------------------------------------------------

def _walk(src: Path):
    # os.walk does not follow links, but a linked folder shows up as a dirname:
    # list it as a file instead so collect() refuses it.
    for dirpath, dirnames, filenames in os.walk(src):
        for d in [d for d in dirnames if (Path(dirpath) / d).is_symlink()]:
            dirnames.remove(d)
            filenames.append(d)
        yield dirpath, filenames


def collect(base: Path, rel: str) -> list[tuple[str, Path, int]]:
    """[(path inside the page, source file, size)] for `rel` under `base`. A file
    publishes alone under its own name; a folder publishes its contents. Raises
    PageError for any rule broken."""
    rel = (rel or "").strip().replace("\\", "/").strip("/")
    if rel in ("", "."):
        src = base
    else:
        if any(part in ("..", "") for part in rel.split("/")):
            raise PageError("the path must stay inside the project")
        src = base / rel
    real_base = base.resolve()
    if src.is_symlink() or (src.exists() and not src.resolve().is_relative_to(real_base)):
        raise PageError("symlinks and paths outside the project are not published")
    if not src.exists():
        raise PageError(f"no such file or folder in the project: {rel or '.'}")
    out: list[tuple[str, Path, int]] = []
    if src.is_file():
        out.append((src.name, src, src.stat().st_size))
    else:
        for dirpath, filenames in _walk(src):
            for fn in filenames:
                f = Path(dirpath) / fn
                shown = f.relative_to(src).as_posix()
                if f.is_symlink():
                    raise PageError(f"{shown} is a symlink; symlinks are not published")
                if not f.is_file():
                    raise PageError(f"{shown} is not a regular file")
                out.append((shown, f, f.stat().st_size))
                if len(out) > MAX_FILES:
                    raise PageError(f"more than {MAX_FILES} files")
    if not out:
        raise PageError("there is nothing to publish there (no files)")
    total = 0
    for shown, _f, size in out:
        if size > MAX_FILE_BYTES:
            raise PageError(f"{shown} is over {MAX_FILE_BYTES // 2**20} MB")
        total += size
    if total > MAX_BYTES:
        raise PageError(f"the files total over {MAX_BYTES // 2**20} MB")
    return out


def _source_dir(row: dict) -> Path:
    d = worktrees.resolve(row.get("source") or row["project"]).dir
    return d if d.is_dir() else worktrees.resolve(row["project"]).dir


async def _flush(key: str) -> None:
    """Bring this turn's writes from the box to the host (a no-op outside a
    brokered guest tool call)."""
    try:
        from .gitgate import flush_guest_writes
        await flush_guest_writes(key)
    except Exception as e:  # noqa: BLE001 — publish from what the host has
        log.warning("could not flush the guest's writes for %s: %s", key, e)


# --- rows -----------------------------------------------------------------------

def _view(r) -> dict:
    return {"id": r["id"], "project": r["project"], "name": r["name"], "path": r["path"],
            "files": json.loads(r["files"] or "[]"), "bytes": r["bytes"],
            "status": r["status"], "url": url_of(r["id"]) if r["status"] == "live" else None,
            "created_at": r["created_at"], "decided_at": r["decided_at"],
            "conversation_id": r["conversation_id"],
            # the agent's two-sentence summary and the call that filed it (reqsummary.py)
            "agent_summary": _col(r, "agent_summary"), "call_id": _col(r, "call_id")}


def _col(r, name: str):
    """A column a row from before its migration may lack."""
    try:
        return r[name]
    except (IndexError, KeyError):
        return None


async def _row(db, pid: str):
    async with db.execute("SELECT * FROM pages WHERE id = ?", (pid,)) as cur:
        return await cur.fetchone()


async def _pending_or_live(pid: str, want: str) -> dict:
    db = await get_db()
    try:
        r = await _row(db, pid)
    finally:
        await db.close()
    if not r:
        raise PageError("no such request" if want == "pending" else "no such page")
    if r["status"] != want:
        raise PageError(f"it is {r['status']}, not {want}")
    return dict(r)


async def list_pages(status: str) -> list[dict]:
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM pages WHERE status = ? "
                              "ORDER BY created_at DESC, rowid DESC", (status,)) as cur:
            return [_view(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def get(pid: str) -> dict | None:
    db = await get_db()
    try:
        r = await _row(db, pid)
        return _view(r) if r else None
    finally:
        await db.close()


# --- request / decide ------------------------------------------------------------

async def request(files_key: str, path: str, name: str,
                  conversation_id: int | None = None) -> dict:
    """File a publish request for `path` in the project (or work copy) `files_key`.
    Returns the page view: pending, or live at once when yolo approves it (or
    refused, if the approval found a secret). Raises PageError when the path
    cannot be published at all."""
    if not settings.pages_enabled:
        raise PageError("publishing is turned off (pages_enabled)")
    name = " ".join((name or "").split())[:NAME_MAX]
    if not name:
        raise PageError("give the page a name")
    work = worktrees.resolve(files_key)
    await _flush(files_key)
    files = collect(work.dir, path)         # refuse early, with the reason
    pid = _tokens.token_urlsafe(12)
    total = sum(s for _r, _f, s in files)
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO pages (id, project, name, path, files, bytes, status, "
            "conversation_id, source) VALUES (?,?,?,?,?,?,'pending',?,?)",
            (pid, work.slug, name, (path or "").strip() or ".",
             json.dumps([{"path": r, "size": s} for r, _f, s in files]), total,
             conversation_id, files_key))
        await db.commit()
        await security.raise_event(
            db, kind="page_publish_requested", severity="warn", project=work.slug,
            summary=f"publish '{name}' ({len(files)} files, {total} bytes)",
            conversation_id=conversation_id,
            detail={"id": pid, "project": work.slug, "name": name, "path": path,
                    "bytes": total, "files": len(files)})
    finally:
        await db.close()
    if await permissions.auto_approve(
            "publish", project=work.slug, summary=f"publish '{name}'",
            detail={"id": pid, "name": name}):
        try:
            await approve(pid, decided_by="yolo")
        except PageError:
            pass            # refused: the row says so, the caller reads it back
    return await get(pid)


async def approve(pid: str, decided_by: str = "operator") -> dict:
    row = await _pending_or_live(pid, "pending")
    await _flush(row.get("source") or row["project"])
    snap = page_dir(pid)
    try:
        files = collect(_source_dir(row), row["path"])
        leaks = []
        for rel, f, _s in files:
            leaks += [f"{n} (in {rel})" for n in secrets_mod.find_in_bytes(f.read_bytes())]
        if leaks:
            raise PageError("a file holds the value of the stored secret "
                            + ", ".join(leaks) + "; remove it and publish again")
        snap.mkdir(parents=True)
        meta, total = [], 0
        for rel, f, _s in files:
            dest = snap / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
            size = dest.stat().st_size
            meta.append({"path": rel, "size": size})
            total += size
        if total > MAX_BYTES:
            raise PageError(f"the files total over {MAX_BYTES // 2**20} MB")
    except (PageError, OSError) as e:
        shutil.rmtree(snap, ignore_errors=True)
        why = str(e) if isinstance(e, PageError) else f"could not copy the files: {e}"
        await _finish(pid, row, "refused", decided_by, "page_refused", "warn", why)
        raise PageError(why) from e
    db = await get_db()
    try:
        await db.execute("UPDATE pages SET files=?, bytes=? WHERE id=?",
                         (json.dumps(meta), total, pid))
        await db.commit()
    finally:
        await db.close()
    await _finish(pid, row, "live", decided_by, "page_published", "info",
                  f"published '{row['name']}'")
    return await get(pid)


async def _finish(pid: str, row: dict, status: str, by: str, kind: str, sev: str,
                  summary: str) -> None:
    """Record the decision, settle the request's alert and raise the outcome."""
    db = await get_db()
    try:
        await db.execute("UPDATE pages SET status=?, decided_at=datetime('now'), "
                         "decided_by=? WHERE id=?", (status, by, pid))
        await db.commit()
        await security.settle(db, "page_publish_requested", "id", pid)
        await security.raise_event(
            db, kind=kind, severity=sev, project=row["project"], summary=summary[:300],
            conversation_id=row.get("conversation_id"),
            detail={"id": pid, "project": row["project"], "name": row["name"], "by": by,
                    "reason": summary if status == "refused" else None})
    finally:
        await db.close()


async def reject(pid: str, decided_by: str = "operator") -> None:
    row = await _pending_or_live(pid, "pending")
    await _finish(pid, row, "rejected", decided_by, "page_rejected", "info",
                  f"rejected '{row['name']}'")


async def unpublish(pid: str, decided_by: str = "operator") -> None:
    row = await _pending_or_live(pid, "live")
    await asyncio.to_thread(shutil.rmtree, page_dir(pid), True)
    await _finish(pid, row, "unpublished", decided_by, "page_unpublished", "info",
                  f"unpublished '{row['name']}'")

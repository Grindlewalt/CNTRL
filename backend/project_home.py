"""The project home page (frontend/src/pages/ProjectHome.jsx): one read of what
a project holds, and the operator's edits to the parts of it that ride every
prompt.

  GET    /api/projects/{slug}/home           name, chats (newest activity first),
                                             instructions, context files,
                                             memory counts, schedules
  PUT    /api/projects/{slug}/instructions   save instructions.md
  POST   /api/projects/{slug}/context-files  upload one document into context/
                                             and load it into every prompt
  DELETE /api/projects/{slug}/context-files  stop loading a file (an upload in
                                             context/ is deleted too)
  GET    /api/projects/{slug}/memory         the journal (project.md) and the
                                             memory notes that mention the project

Nothing here is a new mechanism:

  * Instructions and uploads are ordinary project files, written through the
    write chokepoint (writes.apply_write: path refusals, the secret-value
    refusal, the advisory diff gate, 0644) and deleted through apply_delete.
  * "Loaded into every prompt" is the always-loaded list (alwaysloaded.py): the
    operator's ticks in .context.json, changed only on this cookie session and
    recorded as an info security event. memory._active_project_blocks inlines
    project.md and then the list in order until project_context_budget_tokens is
    spent; a file past the budget is named for the model to read on demand.
    instructions.md is kept first on the list, so it is the last to be cut.
  * Being on that list is also what holds a write by a turn that read untrusted
    content for the operator's approval, instead of letting it land.

Memory notes are global (memory/notes/); a note has no project field. The
memory read lists the notes whose name, description or text mention the
project's name or slug, and says so.
"""
import asyncio
import hashlib
import re
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from . import alwaysloaded, journalhold, memory, taintpaths, writes
from .auth import require_user
from .config import settings
from .db import get_db

router = APIRouter(prefix="/api/projects/{slug}", tags=["project-home"],
                   dependencies=[Depends(require_user)])

INSTRUCTIONS = "instructions.md"
CONTEXT_DIR = "context"
# the always-loaded list's own cap on a file it will hold for approval; an upload
# bigger than this could never be reviewed there, so it is refused here too
MAX_TEXT_BYTES = alwaysloaded.MAX_HELD_BYTES
MAX_PDF_BYTES = 20_000_000
CHATS_MAX = 100
NOTE_SCAN_BYTES = 200_000


async def _project(slug: str) -> dict:
    """The live, visible project row, else 404."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT id, slug, name FROM projects WHERE slug = ? "
            "AND deleted_at IS NULL AND is_hidden = 0", (slug,)) as cur:
            row = await cur.fetchone()
    finally:
        await db.close()
    if row is None:
        raise HTTPException(status_code=404, detail="no such project")
    return dict(row)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(slug: str, rel: str) -> str | None:
    """A project file's text; None when it is missing or not text."""
    try:
        return (settings.projects_dir / slug / rel).read_text()
    except (OSError, UnicodeDecodeError, ValueError):
        return None


async def _set_list(slug: str, chosen: list[str]) -> None:
    """Store the always-loaded ticks and record the change, as PUT /context does."""
    added, removed = alwaysloaded.set_selection(slug, chosen)
    await alwaysloaded.record_change(slug, added, removed)


def _project_md_tokens(slug: str) -> int:
    """project.md as memory._active_project_blocks puts it in the prompt."""
    text, withheld = memory.strip_unverified(memory.read_project_md(slug))
    if not text:
        return 0
    block = f"# Active project (loaded into central context): {slug}\n\n{text}"
    return memory.estimate_tokens(block) + (40 if withheld else 0)


def _loaded(slug: str) -> dict:
    """The always-loaded list as the prompt will see it: each ticked file, its
    token estimate, and whether it fits in the budget (the same walk as
    memory._active_project_blocks: project.md first, then the list in order)."""
    budget = settings.project_context_budget_tokens
    used = _project_md_tokens(slug)
    md_tokens = used
    tainted = set(taintpaths.paths(slug))
    rows = []
    for rel in alwaysloaded.selection(slug):
        if rel == alwaysloaded.PROJECT_MD:
            continue
        path = settings.projects_dir / slug / rel
        text = _read(slug, rel)
        tokens = memory.estimate_tokens(text) if text is not None else 0
        inlined = text is not None and used + tokens <= budget
        if inlined:
            used += tokens
        rows.append({"path": rel, "tokens": tokens, "inlined": inlined,
                     "missing": not path.is_file(),
                     "binary": path.is_file() and text is None,
                     "uploaded": rel.startswith(f"{CONTEXT_DIR}/"),
                     "tainted": rel in tainted})
    return {"rows": rows, "budget": budget, "used": used, "project_md_tokens": md_tokens}


def _instructions(slug: str, loaded: dict) -> dict:
    path = settings.projects_dir / slug / INSTRUCTIONS
    text = _read(slug, INSTRUCTIONS)
    row = next((r for r in loaded["rows"] if r["path"] == INSTRUCTIONS), None)
    held = any(h.get("path") == INSTRUCTIONS for h in alwaysloaded.list_held(slug))
    return {"path": INSTRUCTIONS, "text": text or "",
            # binds a save to the text the operator opened ('' = no file yet)
            "sha256": _sha(path.read_bytes()) if path.is_file() else "",
            "loaded": row is not None, "inlined": bool(row and row["inlined"]),
            "tokens": memory.estimate_tokens(text) if text else 0,
            "tainted": INSTRUCTIONS in set(taintpaths.paths(slug)), "held": held}


def _context(loaded: dict) -> dict:
    return {"files": [r for r in loaded["rows"] if r["path"] != INSTRUCTIONS],
            "budget": loaded["budget"], "used": loaded["used"],
            "project_md_tokens": loaded["project_md_tokens"]}


def _mentions(slug: str, name: str) -> list[dict]:
    """Memory notes whose name, description or text mention the project's slug
    or name (whole words, any case)."""
    words = {w for w in (slug, name, slug.replace("-", " ")) if w and len(w) >= 3}
    if not words:
        return []
    pat = re.compile("|".join(rf"(?<!\w){re.escape(w)}(?!\w)" for w in
                              sorted(words, key=len, reverse=True)), re.I)
    nd = memory.notes_dir()
    out = []
    if not nd.is_dir():
        return out
    for p in sorted(nd.glob("*.md")):
        try:
            text = p.read_text()[:NOTE_SCAN_BYTES]
        except (OSError, UnicodeDecodeError):
            continue
        meta, body = memory.parse_note(text)
        desc = memory.note_description(meta, body)
        if not (pat.search(p.stem) or pat.search(desc or "") or pat.search(body)):
            continue
        out.append({"name": p.stem, "description": desc,
                    "pending": not memory.note_trusted(meta)})
    return out


def _held(slug: str) -> int:
    return len(alwaysloaded.list_held(slug)) + len(journalhold.list_held(slug))


@router.get("/home")
async def home(slug: str):
    proj = await _project(slug)
    from .chat import _active_turns
    db = await get_db()
    try:
        active = await memory.get_active_project(db)
        async with db.execute(
            "SELECT c.id, c.summary, c.started_at, c.starred, c.agent_slug, "
            "(SELECT MAX(m.created_at) FROM messages m WHERE m.conversation_id = c.id) "
            "AS last_at FROM conversations c WHERE c.project_id = ? "
            "AND (c.kind = 'chat' OR c.kind IS NULL) AND c.ephemeral = 0 "
            "ORDER BY COALESCE(last_at, c.started_at) DESC, c.id DESC LIMIT ?",
            (proj["id"], CHATS_MAX)) as cur:
            chats = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT id, name, kind, agent_slug, task, cadence_kind, daily_at, "
            "interval_minutes, enabled, pending_approval, next_run, last_run "
            "FROM schedules WHERE project_slug = ? AND deleted_at IS NULL "
            "ORDER BY enabled DESC, next_run", (slug,)) as cur:
            schedules = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    for c in chats:
        c["starred"] = bool(c["starred"])
        c["running"] = c["id"] in _active_turns
    loaded = _loaded(slug)
    return {"slug": slug, "name": proj["name"], "loaded": active == slug,
            "project_md": memory.read_project_md(slug),
            "chats": chats,
            "instructions": _instructions(slug, loaded),
            "context": _context(loaded),
            "memory": {"notes": len(_mentions(slug, proj["name"])), "held": _held(slug)},
            "schedules": schedules}


class Instructions(BaseModel):
    content: str
    # sha256 of the instructions the operator opened ('' = there were none);
    # omitted skips the check
    base_sha256: str | None = None


def _write_error(e: Exception, what: str) -> HTTPException:
    if isinstance(e, writes.SecretLeakError):
        return HTTPException(
            status_code=422,
            detail=f"{what} would contain the value of secret(s) {', '.join(e.names)}; "
                   "not saved")
    return HTTPException(status_code=400, detail=str(e))


@router.put("/instructions")
async def put_instructions(slug: str, body: Instructions):
    """Save the project's instructions as instructions.md through the write
    chokepoint and keep it first on the always-loaded list. Empty text deletes
    the file and takes it off the list."""
    await _project(slug)
    path = settings.projects_dir / slug / INSTRUCTIONS
    current = path.read_bytes() if path.is_file() else None
    if body.base_sha256 is not None and body.base_sha256 != (_sha(current) if current else ""):
        raise HTTPException(status_code=409,
                            detail="the instructions changed since you opened them; "
                                   "reopen to see the new text")
    text = body.content.replace("\r\n", "\n")
    others = [f for f in alwaysloaded.selection(slug) if f != INSTRUCTIONS]
    if not text.strip():
        if current is not None:
            try:
                await writes.apply_delete(slug, INSTRUCTIONS)
            except (ValueError, FileNotFoundError) as e:
                raise _write_error(e, "the instructions") from None
        await _set_list(slug, others)
    else:
        data = (text.rstrip("\n") + "\n").encode()
        if len(data) > MAX_TEXT_BYTES:
            raise HTTPException(status_code=413, detail="instructions are limited to 2 MB")
        try:
            await writes.apply_write(slug, INSTRUCTIONS, data)
        except ValueError as e:
            raise _write_error(e, "the instructions") from None
        # the operator wrote every word: the file is vouched for
        taintpaths.record(slug, [INSTRUCTIONS], False)
        await _set_list(slug, [INSTRUCTIONS, *others])
    return {"ok": True, "instructions": _instructions(slug, _loaded(slug))}


_UNSAFE = re.compile(r"[^A-Za-z0-9._ ()-]+")


def _clean_name(filename: str | None) -> str:
    """An upload's base name, kept to plain characters, never a dotfile."""
    name = PurePosixPath(str(filename or "").replace("\\", "/")).name
    name = _UNSAFE.sub("-", name).strip(" .-")[:120]
    return name or "document"


def _free(slug: str, rel: str) -> str:
    """rel, or 'name (2).ext' and so on when a file is already there."""
    base = settings.projects_dir / slug
    if not (base / rel).exists():
        return rel
    p = PurePosixPath(rel)
    for n in range(2, 1000):
        cand = str(p.with_name(f"{p.stem} ({n}){p.suffix}"))
        if not (base / cand).exists():
            return cand
    raise HTTPException(status_code=409, detail="too many files with that name")


def _pdf_text(raw: bytes) -> str:
    from . import pdftext
    try:
        doc = pdftext._parse(raw)
    except pdftext.PdfError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    pages = [(i, t) for i, t in enumerate(doc.get("pages") or [], 1) if t and t.strip()]
    if not pages:
        raise HTTPException(status_code=422,
                            detail="this PDF has no text layer (scanned pages), "
                                   "so there is nothing to load")
    return "\n\n".join(f"[page {i}]\n{t.strip()}" for i, t in pages) + "\n"


@router.post("/context-files")
async def add_context_file(slug: str, file: UploadFile):
    """Put one document into context/ and add it to the always-loaded list. Text
    files land as they are; a PDF lands as its extracted text (<name>.pdf.txt),
    because the prompt can only carry text."""
    await _project(slug)
    raw = await file.read(MAX_PDF_BYTES + 1)
    name = _clean_name(file.filename)
    from . import pdftext
    if pdftext.looks_like_pdf(file.content_type or "", raw[:8], name):
        if len(raw) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="a PDF is limited to 20 MB")
        text = await asyncio.to_thread(_pdf_text, raw)
        name = f"{name}.txt"
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=415,
                detail="only text files and PDFs can be loaded into every prompt") from None
        if "\x00" in text:
            raise HTTPException(
                status_code=415,
                detail="only text files and PDFs can be loaded into every prompt")
    data = text.encode()
    if len(data) > MAX_TEXT_BYTES:
        raise HTTPException(status_code=413, detail="a context file is limited to 2 MB of text")
    rel = _free(slug, f"{CONTEXT_DIR}/{name}")
    try:
        await writes.apply_write(slug, rel, data)
    except ValueError as e:
        raise _write_error(e, name) from None
    sel = alwaysloaded.selection(slug)
    await _set_list(slug, [*sel, rel])
    return {"ok": True, "path": rel, "context": _context(_loaded(slug))}


@router.delete("/context-files")
async def remove_context_file(slug: str, path: str):
    """Take a file off the always-loaded list. An upload (a file in context/) is
    deleted through the write chokepoint as well; any other file stays in the
    project, just no longer loaded."""
    await _project(slug)
    if path == alwaysloaded.PROJECT_MD:
        raise HTTPException(status_code=400, detail="project.md is always loaded")
    if path == INSTRUCTIONS:
        raise HTTPException(status_code=400,
                            detail="clear the instructions to stop loading them")
    sel = alwaysloaded.selection(slug)
    if path not in sel:
        raise HTTPException(status_code=404, detail="that file is not loaded")
    deleted = False
    if path.startswith(f"{CONTEXT_DIR}/") and writes.resolve(slug, path) is not None:
        try:
            await writes.apply_delete(slug, path)
        except (ValueError, FileNotFoundError) as e:
            raise _write_error(e, path) from None
        deleted = True
    await _set_list(slug, [f for f in sel if f != path])
    return {"ok": True, "path": path, "deleted": deleted, "context": _context(_loaded(slug))}


@router.get("/memory")
async def project_memory(slug: str):
    """The journal (project.md, which rides every prompt of the project) and the
    memory notes that mention the project. Notes are global: what CNTRL keeps
    about the operator and every project, approved on the Memory page."""
    proj = await _project(slug)
    journal = memory.read_project_md(slug)
    _, withheld = memory.strip_unverified(journal)
    return {"journal": journal, "journal_tokens": _project_md_tokens(slug),
            "withheld": withheld, "notes": _mentions(slug, proj["name"]),
            "held": _held(slug)}

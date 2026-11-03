"""Journal entries held as [unverified], and the operator's decision on them.

A journal line written by a turn that had read untrusted content is tagged
[unverified] in project.md, and prompt assembly leaves tagged lines out
(memory.strip_unverified): project.md rides every prompt, so an injected line
must not. Until now the operator removed the tag by editing the file. Here each
tagged line is a held item in the Queue instead:

  * approve removes the tag from that one line and nothing else in the file;
    the entry rides prompts from then on.
  * drop removes the line.
  * the item is the line itself, found again by a hash of its text (people edit
    project.md too). A line that was edited or is gone is a LineChanged, and
    nothing is written: the operator sees the list again instead of a guess.

Only the operator's routes call approve / drop (memory_api.py, cookie session).
The defence is unchanged: a tagged line stays out of the prompt until the
operator, not the agent, acts on it. Writing a tagged line raises a
`journal_held` security event; deciding it, or removing the tag by hand, settles
that event (the list sweeps, so a hand edit clears the item too).
"""
import hashlib
import json
import re

from . import runtime
from .config import settings
from .memory import UNVERIFIED_MARK, _UNVERIFIED_LINE, flat_line, read_project_md

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$", re.I)
_TAG = re.compile(r"^([ \t]*-[ \t]+\d{4}-\d{2}-\d{2})[ \t]+\[unverified\]")
_PARTS = re.compile(r"^\s*-\s+(\d{4}-\d{2}-\d{2})\s+\[unverified\]:?\s*(.*)$")
TEXT_MAX = 400                  # chars of a line shown in the Queue


class LineChanged(Exception):
    """The line is not in project.md as it was (edited, already decided, gone)."""


def line_id(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def _lines(slug: str) -> list[str]:
    return [ln for ln in read_project_md(slug).split("\n") if _UNVERIFIED_LINE.match(ln)]


def held_in(slug: str) -> list[dict]:
    """The tagged lines of one project, in file order, a repeat of identical text once."""
    out, seen = [], set()
    for ln in _lines(slug):
        iid = line_id(ln)
        if iid in seen:
            continue
        seen.add(iid)
        m = _PARTS.match(ln)
        out.append({"project": slug, "id": iid, "date": m.group(1) if m else "",
                    "text": flat_line(m.group(2) if m else ln, TEXT_MAX)})
    return out


def list_held(slug: str | None = None) -> list[dict]:
    """Held journal lines of one project, or of every project."""
    if slug:
        return held_in(slug) if _SLUG.match(slug) else []
    root = settings.projects_dir
    out = []
    for d in (sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else ()):
        if _SLUG.match(d.name):
            out += held_in(d.name)
    return out


def _rewrite(slug: str, iid: str, keep) -> str:
    """project.md with the first line whose hash is `iid` replaced by
    keep(line) (None drops it). Every other byte is untouched."""
    lines = read_project_md(slug).split("\n")
    for i, ln in enumerate(lines):
        if _UNVERIFIED_LINE.match(ln) and line_id(ln) == iid:
            new = keep(ln)
            return "\n".join(lines[:i] + ([new] if new is not None else []) + lines[i + 1:])
    raise LineChanged(iid)


async def _decide(slug: str, iid: str, keep, kind: str, summary: str, by_operator: bool) -> None:
    from . import writes
    from .db import get_db
    from .memory import refresh_all_projects
    if not _SLUG.match(slug or ""):
        raise LineChanged(iid)
    text = _rewrite(slug, iid, keep)
    # project.md rides every prompt: through the same chokepoint as any write
    await writes.apply_write(slug, "project.md", text.encode())
    db = await get_db()
    try:
        await refresh_all_projects(db)
    finally:
        await db.close()
    await _settle(iid)
    await _audit(kind, "info", slug, summary, {"project": slug, "id": iid, "by": "operator"},
                 by_operator=by_operator)


async def approve(slug: str, iid: str, *, by_operator: bool = False) -> None:
    """Remove the tag from the line: it rides prompts from the next turn."""
    await _decide(slug, iid, lambda ln: _TAG.sub(r"\1", ln, count=1), "journal_approved",
                  f"journal entry in '{slug}' approved: it rides prompts now", by_operator)


async def drop(slug: str, iid: str, *, by_operator: bool = False) -> None:
    """Remove the line from project.md."""
    await _decide(slug, iid, lambda ln: None, "journal_dropped",
                  f"journal entry in '{slug}' dropped", by_operator)


async def hold_event(slug: str, entry_line: str) -> None:
    """The Queue item for a line just written tagged. Best-effort, like the
    write itself: the line stays out of the prompt whether or not this lands."""
    iid = line_id(entry_line)
    await _audit("journal_held", "warn", slug,
                 f"journal entry in '{slug}' held {UNVERIFIED_MARK} for your approval: "
                 "written after untrusted content",
                 {"project": slug, "id": iid, "text": flat_line(entry_line, TEXT_MAX)},
                 cause=f"journal_held:{slug}:{iid}")


async def sweep() -> int:
    """Settle the open journal_held events whose line is no longer tagged (the
    operator removed the tag or the line by hand). Returns how many."""
    try:
        from . import security
        from .db import get_db
        db = await get_db()
        try:
            async with db.execute("SELECT detail FROM security_events WHERE "
                                  "acknowledged=0 AND kind='journal_held'") as cur:
                rows = await cur.fetchall()
            n = 0
            for r in rows:
                try:
                    d = json.loads(r["detail"] or "{}")
                except ValueError:
                    continue
                slug, iid = d.get("project"), d.get("id")
                if not iid or iid in {h["id"] for h in list_held(slug)}:
                    continue
                n += await security.settle(db, "journal_held", "id", iid)
            return n
        finally:
            await db.close()
    except Exception:  # noqa: BLE001
        return 0


async def _settle(iid: str) -> None:
    try:
        from . import security
        from .db import get_db
        db = await get_db()
        try:
            await security.settle(db, "journal_held", "id", iid)
        finally:
            await db.close()
    except Exception:  # noqa: BLE001
        pass


async def _audit(kind: str, severity: str, slug: str, summary: str, detail: dict,
                 cause: str | None = None, *, by_operator: bool = False) -> None:
    try:
        from . import security
        from .db import get_db
        db = await get_db()
        try:
            await security.raise_event(
                db, kind=kind, severity=severity, project=slug, summary=summary,
                detail={**detail, "conversation_id": runtime.conversation_id.get()},
                cause=cause, actor=security.OPERATOR if by_operator else None)
        finally:
            await db.close()
    except Exception:  # noqa: BLE001
        pass

"""GET /api/search/messages: deep search over chat message text.

The sidebar's quick search only matches titles it already has. This reads the
text of every message, so it is slower: it has a time limit and pages. It
lists exactly the chats /api/conversations lists (real chats, not agent job
nodes and not incognito ones) and the same actors may call it, so a chat
token sees nothing it could not already open.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import require_actor
from .db import get_db

router = APIRouter(prefix="/api", tags=["search"], dependencies=[Depends(require_actor)])

TIME_LIMIT_S = 4.0


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def snippet(content: str, q: str, width: int = 70) -> str:
    """The matching line, trimmed around the match."""
    low, ql = content.lower(), q.lower()
    i = low.find(ql)
    if i < 0:
        return content.strip().splitlines()[0][:width * 2] if content.strip() else ""
    start = low.rfind("\n", 0, i) + 1
    end = low.find("\n", i)
    line = content[start:end if end >= 0 else len(content)]
    at = i - start
    a = max(0, at - width)
    b = min(len(line), at + len(q) + width)
    return ("…" if a else "") + line[a:b].strip() + ("…" if b < len(line) else "")


@router.get("/search/messages")
async def search_messages(q: str = Query(..., min_length=2, max_length=200),
                          limit: int = Query(20, ge=1, le=50),
                          offset: int = Query(0, ge=0)):
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(status_code=400, detail="type at least two characters")
    db = await get_db()
    try:
        sql = (
            "SELECT m.id AS message_id, m.conversation_id, m.role, m.content, "
            "c.summary, p.slug AS project_slug FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "LEFT JOIN projects p ON p.id = c.project_id "
            "WHERE (c.kind = 'chat' OR c.kind IS NULL) AND c.ephemeral = 0 "
            "AND m.role IN ('user', 'assistant') AND m.content LIKE ? ESCAPE '\\' "
            "ORDER BY m.id DESC LIMIT ? OFFSET ?")

        async def run():
            async with db.execute(sql, (_like(q), limit + 1, offset)) as cur:
                return await cur.fetchall()

        task = asyncio.ensure_future(run())
        done, _ = await asyncio.wait({task}, timeout=TIME_LIMIT_S)
        if not done:
            try:
                db.interrupt()
            except Exception:  # noqa: BLE001
                pass
            task.cancel()
            return {"results": [], "more": False, "timed_out": True, "offset": offset}
        rows = task.result()
    finally:
        await db.close()
    more = len(rows) > limit
    return {"results": [{
        "message_id": r["message_id"], "conversation_id": r["conversation_id"],
        "role": r["role"], "title": r["summary"], "project_slug": r["project_slug"],
        "snippet": snippet(r["content"], q)} for r in rows[:limit]],
        "more": more, "timed_out": False, "offset": offset}

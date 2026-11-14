"""GET /api/security/glance: what is waiting on the operator, in one cheap call.

The terminal client's sidebar polls this every ~15 s, so everything here is a
COUNT over the same tables the queue endpoints read (nov1 contract, section 3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from . import egress, security
from .auth import require_user
from .db import get_db

router = APIRouter(prefix="/api/security", tags=["security"],
                   dependencies=[Depends(require_user)])


async def _count(db, sql: str, args: tuple = ()) -> int:
    async with db.execute(sql, args) as cur:
        row = await cur.fetchone()
    return int(row[0] or 0)


@router.get("/glance")
async def glance(project: str | None = None):
    db = await get_db()
    try:
        scope, args = ("", ()) if not project else (" AND project_slug = ?", (project,))
        waiting = {
            "egress": len(await egress.list_pending(db, project)),
            # a package an environment proposed is answered with that environment
            "packages": await _count(
                db, "SELECT COUNT(*) FROM package_catalogue WHERE status = 'pending' "
                    "AND environment_id IS NULL" + scope, args),
            "environments": await _count(
                db, "SELECT COUNT(*) FROM environment_requests WHERE status = 'pending'"
                    + scope, args),
            "skills": await _count(
                db, "SELECT COUNT(*) FROM skill_requests WHERE status IN ('pending', 'fetched')"
                    + scope, args),
            "git": await _count(
                db, "SELECT COUNT(*) FROM git_requests WHERE status = 'pending'" + scope, args),
            "alerts": len([e for e in await security.list_events(
                db, queue_only=True, limit=500)
                if not project or e.get("project_slug") in (project, None)]),
        }
        where, eargs = "e.created_at > datetime('now', '-1 day')", ()
        if project:
            where += " AND e.project_slug = ?"
            eargs = (project,)
        async with db.execute(
                "SELECT COUNT(DISTINCT CASE WHEN e.verdict IN "
                "('allow','auto_allow','approved','reviewer_approved') THEN e.host END) "
                "AS allowed, "
                "COUNT(DISTINCT CASE WHEN e.verdict IN ('deny','auto_deny','cut') "
                "AND (e.verdict = 'cut' OR NOT EXISTS ("
                "SELECT 1 FROM egress_events a WHERE a.host = e.host AND a.id > e.id "
                "AND a.project_slug IS e.project_slug "
                "AND a.verdict IN ('approved','reviewer_approved'))) "
                f"THEN e.host END) AS denied FROM egress_events e WHERE {where}", eargs) as cur:
            net = dict(await cur.fetchone())
        return {"waiting": waiting, "total": sum(waiting.values()),
                "net24h": {"allowed": int(net["allowed"] or 0),
                           "denied": int(net["denied"] or 0)}}
    finally:
        await db.close()


# ---- Inspect: the chat step that filed a request -------------------------------
# A queue row's Inspect opens the chat AT the call that filed it (/c/<id>?step=<n>,
# where n is the tool_calls row). The request keeps the model's call id
# (reqsummary.store); a row from before that, or one whose call id the turn
# never recorded, falls back to the first call of the filing tool in that chat
# at or after the request was filed.

# queue kind -> (table, {row kind -> tool}, the section a merged call shows as)
_REQUESTS = {
    "git": ("git_requests", {"commit": "git_commit_request", "push": "git_push_request",
                             "remote": "git_remote_request"}, "git"),
    "package": ("package_catalogue", {"": "package_request"}, "project"),
    "environment": ("environment_requests", {"": "environment_request"}, "project"),
    "skill": ("skill_requests", {"": "skill_request"}, "skills"),
    "service": ("services", {"": "service_request"}, "project"),
    "page": ("pages", {"": "publish"}, "project"),
}


@router.get("/request_step")
async def request_step(kind: str, id: str):
    """{conversation_id, step}: where in which chat a request was filed. Either
    may be null (filed over HTTP, by the orchestrator, or the chat is gone)."""
    none = {"conversation_id": None, "step": None}
    spec = _REQUESTS.get(kind)
    if spec is None:
        return none
    table, tools, section = spec
    db = await get_db()
    try:
        async with db.execute(f"SELECT * FROM {table} WHERE id = ?", (id,)) as cur:
            row = await cur.fetchone()
        row = dict(row) if row is not None else {}
        cid = row.get("conversation_id")
        if not cid:
            return none
        step = None
        if row.get("call_id"):
            async with db.execute(
                    "SELECT id FROM tool_calls WHERE conversation_id = ? AND call_id = ? "
                    "ORDER BY id LIMIT 1", (cid, str(row["call_id"]))) as cur:
                r = await cur.fetchone()
            step = r["id"] if r else None
        tool = tools.get(row.get("kind") or "") or tools.get("")
        if step is None and tool:
            async with db.execute(
                    "SELECT id FROM tool_calls WHERE conversation_id = ? AND tool IN (?, ?) "
                    "AND created_at >= datetime(?, '-5 seconds') ORDER BY id LIMIT 1",
                    (cid, tool, section, row.get("created_at") or "1970-01-01")) as cur:
                r = await cur.fetchone()
            step = r["id"] if r else None
        return {"conversation_id": cid, "step": step}
    finally:
        await db.close()

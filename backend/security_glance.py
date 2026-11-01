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

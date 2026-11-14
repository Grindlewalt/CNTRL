"""environment_request: file a pending request for a new image variant
(host-side, brokered). Validation and storage live in backend/environments.py."""
import asyncio

from backend import environments, permissions, reqsummary, runtime
from backend.agent.tools.toolctx import active_slug
from backend.config import settings
from backend.db import get_db
from backend.packages import PackageError


async def run(name: str = "", packages: list | None = None, reason: str = "",
              min_mem_mb: int | None = None, use: str = "", summary: str = "", **kw) -> str:
    if not settings.vm_boxes_enabled:
        return "error: environment requests need boxes (vm_boxes_enabled is off)."
    try:
        summary = reqsummary.check(summary)
    except reqsummary.SummaryError as e:
        return reqsummary.refusal(e)
    from_variant = kw.get("from") or kw.get("from_variant") or "main"
    slug = await active_slug()
    db = await get_db()
    try:
        row = await environments.file_request(
            db, project=slug, conversation_id=runtime.conversation_id.get(),
            name=name, from_variant=from_variant, packages=packages or [],
            min_mem_mb=min_mem_mb, reason=reason, use=use or None)
        await reqsummary.store(db, "environment_requests", row["id"], summary)
    except PackageError as e:
        return f"error: {e}"
    finally:
        await db.close()
    if await permissions.auto_approve(
            "environment_request", project=slug,
            summary=f"image `{row['name']}` for '{slug}'",
            detail={"environment_id": row["id"], "name": row["name"],
                    "kind": row["kind"]}):
        if row["kind"] == "use":
            done = await environments.yolo_approve(row["id"])
            if done is not None and done["status"] == "approved":
                return (f"environment request #{row['id']} approved unasked (yolo): "
                        f"this project's next box boot uses image `{row['name']}`.")
        else:
            # resolve, approve and build in the background; the agent is not held
            asyncio.get_running_loop().create_task(environments.yolo_approve(row["id"]))
            return (f"environment request #{row['id']} filed: image `{row['name']}` — "
                    "approved unasked (yolo) once its packages resolve, then built; "
                    "this project's boxes boot on it on a later box boot, not this turn.")
    if row["kind"] == "use":
        return (f"environment request #{row['id']} filed: move this project onto the "
                f"existing image `{row['name']}` (its own box) — pending. Nothing is "
                "built; if the operator approves, the project's next box boot uses "
                "that image (not this turn). Carry on without it, or ask_user to "
                "tell the operator it is waiting.")
    n = len(row["packages"])
    return (f"environment request #{row['id']} filed: image `{row['name']}` from "
            f"`{row['from_variant']}` with {n} package{'s' if n != 1 else ''} — "
            "pending. Nothing is built until the operator approves it; this "
            "project's boxes then boot on it (a later box boot, not this turn).")

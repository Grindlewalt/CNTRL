"""skill_request: file a skill install request (host-side, brokered).

Validation and the table live in backend/skillrequests.py. Nothing is fetched
here: the operator's Fetch button is the only thing that contacts the source."""
from backend import permissions, runtime, skillrequests
from backend.agent.tools.toolctx import active_slug
from backend.db import get_db


async def run(source: str = "", reason: str = "", name: str | None = None) -> str:
    slug = await active_slug()
    db = await get_db()
    yolo_err = None
    try:
        row = await skillrequests.file_request(
            db, source=source, name=name, reason=reason, project=slug,
            conversation_id=runtime.conversation_id.get())
        if await permissions.auto_approve(
                "skill_request", project=slug, summary=f"skill {row['source']}",
                detail={"request_id": row["id"], "source": row["source"]}):
            try:
                row = await skillrequests.yolo_approve(db, row["id"])
            except Exception as e:  # noqa: BLE001 — it stays pending for the operator
                yolo_err = str(e)
            else:
                return (f"skill request #{row['id']} approved unasked (yolo): "
                        f"{row['source']} is installed"
                        f"{'' if row['granted'] else ' but not granted (' + (row['error'] or '') + ')'}"
                        "; it appears in your tools in a later turn.")
    except skillrequests.SkillRequestError as e:
        return f"error: {e}"
    finally:
        await db.close()
    if yolo_err:
        return (f"skill request #{row['id']} filed: {row['source']}; the unasked "
                f"approval (yolo) failed: {yolo_err}. It waits for the operator.")
    return (f"skill request #{row['id']} filed: {row['source']} — pending. The operator "
            "fetches and reads it before anything is installed; if approved it appears "
            "in your tools as an installed skill in a later turn.")

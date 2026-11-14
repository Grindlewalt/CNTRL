"""publish: ask to publish a file or folder as a static page (host-side, brokered).

Everything lives in backend/pages.py: the snapshot, the secret scan, the Queue
request. In yolo the request is approved on the spot and the result carries the link."""
from backend import pages, reqsummary, runtime
from backend.agent.tools.toolctx import require_files
from backend.db import get_db


async def run(path: str, name: str, summary: str = "") -> str:
    try:
        summary = reqsummary.check(summary)
    except reqsummary.SummaryError as e:
        return reqsummary.refusal(e)
    key = await require_files()
    try:
        page = await pages.request(key, path, name, runtime.conversation_id.get())
    except pages.PageError as e:
        return f"error: {e}"
    await reqsummary.store_new("pages", page["id"], summary)
    if page["status"] == "live":
        return f"published '{page['name']}': {page['url']} (anyone with the link can open it)."
    if page["status"] == "refused":
        return f"error: publishing refused: {await _reason(page['id'])}"
    return (f"publish request filed for '{page['name']}' ({len(page['files'])} files, "
            f"{page['bytes']} bytes). It waits in the Queue; nothing is online until the "
            "operator approves it.")


async def _reason(pid: str) -> str:
    """Why an approval refused, from its page_refused event."""
    db = await get_db()
    try:
        async with db.execute(
                "SELECT summary FROM security_events WHERE kind='page_refused' "
                "AND json_extract(detail,'$.id')=? ORDER BY id DESC LIMIT 1", (pid,)) as cur:
            r = await cur.fetchone()
    finally:
        await db.close()
    return r["summary"] if r else "see the Queue"

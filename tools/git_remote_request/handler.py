from backend.agent.tools.toolctx import require_project
from backend.gitgate import create_remote_request, ensure_repo, get_remote, yolo_decide


async def run(url: str) -> str:
    try:
        slug = await require_project()
    except LookupError as e:
        return f"error: {e}"
    await ensure_repo(slug)
    try:
        row = await create_remote_request(slug, url)
    except ValueError as e:
        return f"error: {e}"
    current = await get_remote(slug)
    note = (f" This replaces the currently connected remote ({current})."
            if current else "")
    current_note = note
    done, err = await yolo_decide(row)
    if done["status"] == "approved":
        return (f"remote request #{row['id']} for {row['message']} approved unasked "
                "(yolo): connected, existing commits pushed"
                + (f"; {done['error']}" if done.get("error") else "") + "." + current_note)
    if err:
        note += f" The unasked approval (yolo) failed: {err}. It waits for the operator."
    return (f"remote request #{row['id']} filed for {row['message']} — status "
            f"pending.{note} Nothing connects or pushes until the operator "
            "approves it in the Git panel; approval also pushes existing commits.")

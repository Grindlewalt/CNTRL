from backend.agent.tools.toolctx import require_project
from backend import reqsummary
from backend.gitgate import create_request, ensure_repo, yolo_decide


async def run(message: str, summary: str = "", paths: list[str] | None = None) -> str:
    try:
        summary = reqsummary.check(summary)
    except reqsummary.SummaryError as e:
        return reqsummary.refusal(e)
    try:
        slug = await require_project()
    except LookupError as e:
        return f"error: {e}"
    await ensure_repo(slug)
    try:
        row = await create_request(slug, message, paths)
    except ValueError as e:
        return f"error: {e}"
    await reqsummary.store_new("git_requests", row["id"], summary)
    scope = f" (paths: {', '.join(paths)})" if paths else ""
    replaced = row.get("replaced") or []
    if replaced:
        scope += "; replaces pending " + ", ".join(
            f"request #{r['id']} ({r['message'][:60]!r})" for r in replaced)
    note = "".join(f"\nnote: {n}" for n in row.get("harness_notice") or [])
    done, err = await yolo_decide(row)
    if done["status"] == "approved":
        return (f"commit request #{row['id']} filed{scope} and approved unasked (yolo): "
                f"committed {(done.get('commit_sha') or '')[:10]}"
                + (f"; {done['error']}" if done.get("error") else "") + "." + note)
    if err:
        note += f"\nthe unasked approval (yolo) failed: {err}. It waits for the operator."
    return (f"commit request #{row['id']} filed{scope} — status pending. "
            "Nothing is committed or pushed until the operator approves it "
            "in the dashboard." + note)

"""gitea_issue: open an issue on the current project's repo on the host's Gitea.

Host-side and brokered, like git_push_request: the bot's token is read per call
in backend/gitea.py and never enters the box. The permission gate (GATED_TOOLS
in backend/permissions.py) runs before this in ask and auto mode."""
from backend.agent.tools.toolctx import active_slug


async def run(title: str, body: str = "") -> str:
    from backend import gitea, runtime
    from backend.agent import budget
    from backend.vm import broker
    slug = await active_slug()
    if not slug:
        return ("error: no project is loaded, so there is no repo to open the issue on. "
                "Load the project first (project section, action load).")
    if not gitea.enabled():
        return ("error: Gitea isn't set up on this CNTRL, so there is nowhere to open "
                "issues. Nothing was filed. Keep the issue in your report file and tell "
                "the operator.")
    try:
        issue = await gitea.open_issue(
            slug, title, body, conversation_id=runtime.conversation_id.get(),
            tainted=broker.op_tainted(budget.active_op_id.get() or ""))
    except ValueError as e:
        return f"error: {e}"
    except gitea.GiteaOff:
        return ("error: Gitea isn't set up on this CNTRL. Nothing was filed. Keep the "
                "issue in your report file.")
    except gitea.GiteaUnreachable as e:
        return (f"error: {gitea.scrub(str(e))}. Nothing was filed. Keep the issue in your "
                "report file and tell the operator Gitea is down; don't retry in a loop.")
    except (gitea.GiteaError, RuntimeError) as e:
        return (f"error: could not open the issue: {gitea.scrub(str(e))}. Nothing was "
                "filed; retrying with the same arguments will fail the same way.")
    return (f"issue #{issue['number']} opened on {slug}'s Gitea repo: {issue['url']}. "
            "It is filed; don't open it again.")

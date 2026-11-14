"""The agent's summary on a request the operator decides (Security's Queue).

Every tool that files a queue item (git commit, push and remote requests,
package, service, environment, skill and publish requests) takes a required
`summary`: at most two sentences, written for the operator, saying what the
request does and why. The Queue shows it first; the rest is behind Inspect.

`check` runs BEFORE anything is filed, so a refused summary files nothing and
the model can fix it and call again. `store` runs after the row exists and
also keeps the model's call id, so Inspect can open the chat at that step
(GET /api/security/request_step).

The summary is the agent's text: the UI renders it as plain text, marked as
the agent's words."""
from __future__ import annotations

import re

SUMMARY_MAX = 300           # characters, after whitespace is collapsed
SENTENCES_MAX = 2

# the tables a request lives in; nothing else is ever written by `store`
TABLES = ("git_requests", "package_catalogue", "environment_requests",
          "skill_requests", "services", "pages")

# a period that ends an abbreviation, not a sentence ("e.g. the parser")
_ABBREV = re.compile(r"\b(?:e\.g|i\.e|etc|vs|approx|incl|cf|no|mr|mrs|ms|dr|st)\.(?=\s)",
                     re.IGNORECASE)
# a sentence end: . ! ? (or a run of them) followed by a space or the end.
# "app.py" and "1.2" are not ends: the mark is followed by a letter or digit.
_END = re.compile(r"[.!?]+(?=\s|$)")

_HOW = ("Write one or two plain sentences for the operator: what this request "
        "does and why. Then call the tool again with the same arguments and "
        "the new summary. Nothing was filed.")


class SummaryError(ValueError):
    """A summary the operator would not get: missing, too long, too many sentences."""


def sentences(text: str) -> int:
    """How many sentences `text` has, counting sentence ends. Text with no end
    mark at all is one sentence."""
    t = _ABBREV.sub("", " ".join((text or "").split()))
    return max(1, len(_END.findall(t))) if t else 0


def check(summary) -> str:
    """The summary on one line, or SummaryError with what to do instead."""
    if summary is not None and not isinstance(summary, str):
        raise SummaryError(f"summary must be text, not {type(summary).__name__}. {_HOW}")
    text = " ".join((summary or "").split())
    if not text:
        raise SummaryError(f"summary is required. {_HOW}")
    n = sentences(text)
    if n > SENTENCES_MAX:
        raise SummaryError(
            f"summary has {n} sentences; the operator reads at most {SENTENCES_MAX}. "
            "Put the details in the other arguments (reason, message, description). "
            + _HOW)
    if len(text) > SUMMARY_MAX:
        raise SummaryError(
            f"summary is {len(text)} characters; keep it under {SUMMARY_MAX}. "
            "Put the details in the other arguments. " + _HOW)
    return text


def refusal(e: SummaryError) -> str:
    """The tool result for a refused summary."""
    return f"error: {e}"


def _call_id():
    """The model's id for the call filing this request: the broker's copy for a
    guest turn, else the host loop's (as security._ambient reads it)."""
    from . import runtime
    call = runtime.tool_call_id.get()
    if call is None:
        from .agent.tools import registry
        call = registry.call_id.get()
    return call


async def store(db, table: str, request_id, summary: str) -> None:
    """Keep the summary (already checked) and this call's id on the request row.
    A failure is swallowed: the request is filed, and the Queue falls back to
    the request's own reason."""
    if table not in TABLES or request_id is None:
        return
    call_id = _call_id()
    try:
        await db.execute(
            f"UPDATE {table} SET agent_summary = ?, call_id = ? WHERE id = ?",
            (summary, str(call_id)[:80] if call_id else None, request_id))
        await db.commit()
    except Exception:  # noqa: BLE001 — the summary is decoration on a filed request
        pass


async def store_new(table: str, request_id, summary: str) -> None:
    """`store` on its own connection (for handlers that hold none)."""
    from .db import get_db
    db = await get_db()
    try:
        await store(db, table, request_id, summary)
    finally:
        await db.close()

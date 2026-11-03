"""call_log: this conversation's recent tool calls as one line each. Host side
(reads the DB). Result bodies never leave: only a size, or an error's first line."""
import json

from backend import runtime
from backend.db import get_db

DEFAULT_LIMIT = 30
MAX_LIMIT = 200
ARGS_CUT = 120
STORED_CAP = 10_000      # the sink in loop.py stores at most this much of a result


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(v), hi))
    except (TypeError, ValueError):
        return default


def _one_line(s: str, cut: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= cut else s[:cut - 1] + "…"


def outcome(result: str | None) -> str:
    r = (result or "").strip()
    if r[:5].lower() == "error":
        first = r.splitlines()[0][5:].lstrip(": ")
        return "error: " + _one_line(first, 160)
    n = len(result or "")
    return f"ok, {n}{'+' if n >= STORED_CAP else ''} chars"


def format_row(row) -> str:
    try:
        args = json.dumps(json.loads(row["args"]), ensure_ascii=False)
    except (TypeError, ValueError):
        args = str(row["args"])
    return (f"{row['created_at']} {row['tool']} {_one_line(args, ARGS_CUT)} "
            f"-> {outcome(row['result'])}")


async def run(limit: int = DEFAULT_LIMIT, turns: int | None = None) -> str:
    cid = runtime.conversation_id.get()
    if not cid:
        return "error: no conversation to read a call log for."
    limit = _int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    sql = "SELECT id, tool, args, result, created_at FROM tool_calls WHERE conversation_id = ?"
    params: list = [cid]
    db = await get_db()
    try:
        if turns is not None:
            n = _int(turns, 1, 1, 50)
            async with db.execute(
                    "SELECT created_at FROM messages WHERE conversation_id = ? AND role = 'user' "
                    "ORDER BY id DESC LIMIT 1 OFFSET ?", (cid, n - 1)) as cur:
                m = await cur.fetchone()
            if m:
                sql += " AND created_at >= ?"
                params.append(m["created_at"])
        async with db.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)) as cur:
            rows = list(await cur.fetchall())
    finally:
        await db.close()
    if not rows:
        return "no tool calls logged for this conversation yet."
    rows.reverse()
    return f"{len(rows)} call(s), newest last:\n" + "\n".join(format_row(r) for r in rows)

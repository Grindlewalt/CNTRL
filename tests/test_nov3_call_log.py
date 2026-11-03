"""call_log: this conversation's tool calls, one line each, never result bodies."""
import importlib.util
import json

from backend import runtime
from backend.config import settings
from backend.db import get_db, init_db


def _load():
    spec = importlib.util.spec_from_file_location(
        "t_call_log", settings.base_dir / "tools" / "call_log" / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cl = _load()


async def _seed(rows):
    await init_db()
    db = await get_db()
    try:
        for _ in range(2):
            await db.execute("INSERT INTO conversations (title) VALUES ('t')")
        for conv, tool, args, result in rows:
            await db.execute("INSERT INTO tool_calls (conversation_id, tool, args, result) "
                             "VALUES (?,?,?,?)", (conv, tool, json.dumps(args), result))
        await db.commit()
    finally:
        await db.close()


async def test_lines_sizes_errors_and_no_bodies(tmp_env):
    await _seed([(1, "web_search", {"q": "x" * 300}, "SECRET BODY " * 10),
                 (1, "read_file", {"path": "a"}, "error: no such file\nmore"),
                 (2, "run_code", {"c": 1}, "other conversation")])
    runtime.conversation_id.set(1)
    out = await cl.run()
    assert "2 call(s)" in out and "other conversation" not in out and "run_code" not in out
    assert "SECRET BODY" not in out
    lines = out.splitlines()
    assert "web_search" in lines[1] and "ok, 120 chars" in lines[1] and "…" in lines[1]
    assert lines[2].endswith("error: no such file") and "more" not in lines[2]


async def test_limit_keeps_newest_last(tmp_env):
    await _seed([(1, f"t{i}", {}, "r") for i in range(5)])
    runtime.conversation_id.set(1)
    out = await cl.run(limit=2)
    assert "t3" in out and "t4" in out and "t2" not in out
    assert out.index("t3") < out.index("t4")


async def test_turns_and_empty(tmp_env):
    await _seed([])
    runtime.conversation_id.set(1)
    assert "no tool calls" in await cl.run()
    db = await get_db()
    try:
        await db.execute("INSERT INTO tool_calls (conversation_id, tool, args, result, created_at)"
                         " VALUES (1,'old','{}','r','2026-01-01 00:00:00')")
        await db.execute("INSERT INTO messages (conversation_id, role, content, created_at)"
                         " VALUES (1,'user','hi','2026-01-02 00:00:00')")
        await db.execute("INSERT INTO tool_calls (conversation_id, tool, args, result, created_at)"
                         " VALUES (1,'new','{}','r','2026-01-02 00:00:01')")
        await db.commit()
    finally:
        await db.close()
    out = await cl.run(turns=1)
    assert "new" in out and "old" not in out


def test_it_is_a_system_action_not_a_top_level_tool():
    text = (settings.base_dir / "tools" / "call_log" / "TOOL.md").read_text()
    assert "section: system" in text and "action: call_log" in text

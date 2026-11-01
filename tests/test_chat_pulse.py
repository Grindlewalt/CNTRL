"""GET /api/chat/{cid}/pulse: in-memory phase of a running turn."""
import asyncio

import httpx
import pytest

from backend import chat as chat_mod
from backend.auth import hash_password
from backend.db import get_db, init_db
from backend.main import app


@pytest.fixture
async def client(tmp_env):
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                         ("operator", hash_password("hunter2")))
        await db.commit()
    finally:
        await db.close()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        await c.post("/api/auth/login",
                     json={"username": "operator", "password": "hunter2"})
        yield c


async def test_unknown_chat_is_not_running(client):
    r = await client.get("/api/chat/99999/pulse")
    assert r.status_code == 200
    assert r.json() == {"running": False, "phase": None, "tool": None,
                        "phase_s": None, "quiet_s": None}


async def test_phases_track_and_clear(client):
    task = asyncio.create_task(asyncio.sleep(30))
    chat_mod._active_turns[7] = task
    try:
        chat_mod._pulse_note(7, "start")
        j = (await client.get("/api/chat/7/pulse")).json()
        assert j["running"] and j["phase"] == "starting" and j["tool"] is None
        chat_mod._pulse_note(7, "think")
        assert (await client.get("/api/chat/7/pulse")).json()["phase"] == "model"
        chat_mod._pulse_note(7, "tool", "web_read")
        j = (await client.get("/api/chat/7/pulse")).json()
        assert j["phase"] == "tool" and j["tool"] == "web_read"
        assert j["phase_s"] >= 0 and j["quiet_s"] >= 0
        chat_mod._pulse_note(7, "tool_result")
        assert (await client.get("/api/chat/7/pulse")).json()["tool"] is None
    finally:
        task.cancel()
        chat_mod._active_turns.pop(7, None)
        chat_mod._pulse.pop(7, None)
    assert (await client.get("/api/chat/7/pulse")).json()["running"] is False

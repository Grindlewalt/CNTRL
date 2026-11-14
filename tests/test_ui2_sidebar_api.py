"""UI v2 sidebar: project pins, names that can't start with a number, and the
deep message search."""
import httpx
import pytest

from backend.auth import hash_password
from backend.db import get_db, init_db, open_conversation
from backend.main import app
from backend.memory import ensure_memory_seeds


@pytest.fixture
async def client(tmp_env):
    await init_db()
    ensure_memory_seeds()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                         ("operator", hash_password("hunter2")))
        await db.commit()
    finally:
        await db.close()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": "operator", "password": "hunter2"})
        yield c


async def _msg(cid, role, content):
    db = await get_db()
    try:
        await db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
                         (cid, role, content))
        await db.commit()
    finally:
        await db.close()


async def test_name_cannot_start_with_a_number(client):
    r = await client.post("/api/projects", json={"name": "9lives"})
    assert r.status_code == 400 and "number" in r.json()["detail"]
    assert (await client.post("/api/projects", json={"name": "Lives9"})).status_code == 200
    r = await client.put("/api/projects/lives9/name", json={"name": " 2nd try"})
    assert r.status_code == 400
    assert (await client.put("/api/projects/lives9/name", json={"name": "Nine"})).status_code == 200


async def test_pin_round_trip_shows_in_sidebar(client):
    await client.post("/api/projects", json={"name": "Alpha"})
    await client.post("/api/projects", json={"name": "Beta"})
    assert (await client.put("/api/projects/beta/pin", json={"pinned": True})).status_code == 200
    pins = {p["slug"]: p["pinned_at"] for p in (await client.get("/api/sidebar")).json()["projects"]}
    assert pins["beta"] and pins["alpha"] is None
    await client.put("/api/projects/beta/pin", json={"pinned": False})
    listed = (await client.get("/api/projects")).json()["projects"]
    assert all(p["pinned_at"] is None for p in listed)
    assert (await client.put("/api/projects/nope/pin", json={"pinned": True})).status_code == 404


async def test_message_search(client):
    db = await get_db()
    try:
        a = await open_conversation(db, project=None, title="First")
        b = await open_conversation(db, project=None, title="Second", kind="head")
        e = await open_conversation(db, project=None, title="Hidden")
        await db.execute("UPDATE conversations SET ephemeral = 1 WHERE id = ?", (e,))
        await db.commit()
    finally:
        await db.close()
    await _msg(a, "user", "intro\nthe Quarterly report is due 100% soon\nbye")
    await _msg(b, "user", "quarterly in a job node")
    await _msg(e, "user", "quarterly incognito")
    r = (await client.get("/api/search/messages", params={"q": "quarterly"})).json()
    assert [x["conversation_id"] for x in r["results"]] == [a]
    assert r["results"][0]["snippet"] == "the Quarterly report is due 100% soon"
    assert r["results"][0]["message_id"] and not r["more"]
    # wildcard characters are literal
    assert (await client.get("/api/search/messages", params={"q": "100%"})).json()["results"]
    assert not (await client.get("/api/search/messages", params={"q": "1_0"})).json()["results"]
    # paging
    for i in range(3):
        await _msg(a, "assistant", f"paged line {i}")
    r = (await client.get("/api/search/messages", params={"q": "paged", "limit": 2})).json()
    assert len(r["results"]) == 2 and r["more"]
    r2 = (await client.get("/api/search/messages",
                           params={"q": "paged", "limit": 2, "offset": 2})).json()
    assert len(r2["results"]) == 1 and not r2["more"]
    assert (await client.get("/api/search/messages", params={"q": "x"})).status_code == 422

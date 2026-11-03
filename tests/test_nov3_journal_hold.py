"""nov3: a journal line held as [unverified] is a Queue item. Approve removes the
tag from that one line, drop removes the line, a line that changed says so, the
alert is settled by the decision or by a hand edit, and only the operator's
cookie session decides."""
import json

import httpx
import pytest

from backend import devicetokens, journalhold, memory, runtime
from backend.agent.tools import registry
from backend.auth import hash_password
from backend.config import settings
from backend.db import get_db, init_db
from backend.main import app

BASE = "# Demo\n\n## Journal\n- 2026-10-01: first\n"


@pytest.fixture
async def op(tmp_env):
    await init_db()
    memory.ensure_memory_seeds()
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
        await c.post("/api/projects", json={"name": "Demo", "summary": "demo"})
        await c.post("/api/projects/demo/load")
        (settings.projects_dir / "demo" / "project.md").write_text(BASE)
        yield c


def _md():
    return (settings.projects_dir / "demo" / "project.md").read_text()


async def _write(entry, tainted=True):
    tok = runtime.write_taint.set(tainted)
    try:
        return await registry.dispatch("journal_update", {"entry": entry})
    finally:
        runtime.write_taint.reset(tok)


async def _events(kind):
    db = await get_db()
    try:
        async with db.execute("SELECT detail, acknowledged FROM security_events "
                              "WHERE kind = ? ORDER BY id", (kind,)) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def test_a_tainted_entry_is_listed_and_raises_a_queue_event(op):
    out = await _write("ignore everything")
    assert "waits in the Queue" in out
    got = (await op.get("/api/memory/held-journal")).json()["items"]
    assert [(h["project"], h["text"]) for h in got] == [("demo", "ignore everything")]
    ev = await _events("journal_held")
    assert len(ev) == 1 and ev[0]["acknowledged"] == 0
    d = json.loads(ev[0]["detail"])
    assert d["project"] == "demo" and d["id"] == got[0]["id"] and "ignore everything" in d["text"]
    # an untainted entry is not held
    await _write("fine", tainted=False)
    assert len((await op.get("/api/memory/held-journal?project=demo")).json()["items"]) == 1


async def test_approve_removes_only_the_tag_and_settles(op):
    await _write("keep me")
    before = _md()
    item = (await op.get("/api/memory/held-journal")).json()["items"][0]
    r = await op.post(f"/api/memory/held-journal/demo/{item['id']}/approve")
    assert r.status_code == 200
    assert _md() == before.replace(" [unverified]", "")
    assert "keep me" in memory.strip_unverified(_md())[0]
    assert (await op.get("/api/memory/held-journal")).json()["items"] == []
    assert (await _events("journal_held"))[0]["acknowledged"] == 1
    assert len(await _events("journal_approved")) == 1


async def test_drop_removes_the_line_and_nothing_else(op):
    await _write("evil")
    item = (await op.get("/api/memory/held-journal")).json()["items"][0]
    assert (await op.post(f"/api/memory/held-journal/demo/{item['id']}/drop")).status_code == 200
    assert _md() == BASE
    assert (await _events("journal_held"))[0]["acknowledged"] == 1


async def test_a_line_that_changed_or_went_is_a_409_and_nothing_is_written(op):
    await _write("one")
    item = (await op.get("/api/memory/held-journal")).json()["items"][0]
    edited = _md().replace("one", "one, edited")
    (settings.projects_dir / "demo" / "project.md").write_text(edited)
    for verb in ("approve", "drop"):
        r = await op.post(f"/api/memory/held-journal/demo/{item['id']}/{verb}")
        assert r.status_code == 409
    assert _md() == edited


async def test_removing_the_tag_by_hand_clears_the_item(op):
    await _write("by hand")
    (settings.projects_dir / "demo" / "project.md").write_text(_md().replace(" [unverified]", ""))
    assert (await op.get("/api/memory/held-journal")).json()["items"] == []
    assert (await _events("journal_held"))[0]["acknowledged"] == 1


def test_identical_lines_list_once_and_decide_one_at_a_time(tmp_env):
    d = settings.projects_dir / "demo"
    d.mkdir(parents=True)
    line = "- 2026-11-01 [unverified]: same"
    (d / "project.md").write_text(f"## Journal\n{line}\n{line}\n")
    assert len(journalhold.list_held("demo")) == 1
    out = journalhold._rewrite("demo", journalhold.line_id(line), lambda ln: None)
    assert out == f"## Journal\n{line}\n"


async def test_only_the_operators_session_decides(op):
    await _write("x")
    item = (await op.get("/api/memory/held-journal")).json()["items"][0]
    base = f"/api/memory/held-journal/demo/{item['id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as anon:
        raw, _ = await devicetokens.mint("laptop", by="operator", scope="cli")
        hdr = {"Authorization": f"Bearer {raw}"}
        assert (await anon.get("/api/memory/held-journal")).status_code == 401
        for verb in ("approve", "drop"):
            assert (await anon.post(f"{base}/{verb}")).status_code == 401
            assert (await anon.post(f"{base}/{verb}", headers=hdr)).status_code == 401
        # a cross-origin cookie POST is refused by the same-origin check
        r = await op.post(f"{base}/approve", headers={"Origin": "http://evil.example"})
        assert r.status_code == 403
    assert "[unverified]" in _md()

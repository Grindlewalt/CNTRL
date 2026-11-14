"""The agent's summary on every request the operator decides (backend/reqsummary.py):
required, at most two sentences, checked before anything is filed, stored on the
row with the call that filed it, returned by the queue APIs, and the way back to
that call for Inspect (GET /api/security/request_step)."""
import httpx
import pytest

from backend import reqsummary, runtime
from backend.agent.tools import registry
from backend.auth import hash_password
from backend.db import get_db, init_db
from backend.main import app
from backend.memory import ensure_memory_seeds


# ---- the check -------------------------------------------------------------------

def test_one_or_two_sentences_pass_and_come_back_on_one_line():
    assert reqsummary.check("Commits the new loader.") == "Commits the new loader."
    two = "Adds the CSV loader.\n  The tests need it to read fixtures."
    assert reqsummary.check(two) == "Adds the CSV loader. The tests need it to read fixtures."
    # no end mark at all is one sentence
    assert reqsummary.check("Install ffmpeg for the video export") == "Install ffmpeg for the video export"


def test_file_names_versions_and_abbreviations_are_not_sentence_ends():
    s = "Pins requests 2.32.3 for app.py, e.g. the retry helper. It must persist across runs."
    assert reqsummary.sentences(s) == 2
    assert reqsummary.check(s) == s


@pytest.mark.parametrize("bad, phrase", [
    ("", "summary is required"),
    ("   \n ", "summary is required"),
    (None, "summary is required"),
    ("One. Two! Three?", "has 3 sentences"),
    ("x" * 301, "301 characters"),
])
def test_a_summary_the_operator_would_not_get_is_refused_with_what_to_write(bad, phrase):
    with pytest.raises(reqsummary.SummaryError) as e:
        reqsummary.check(bad)
    msg = str(e.value)
    assert phrase in msg
    assert "one or two plain sentences" in msg and "Nothing was filed" in msg


def test_a_non_string_summary_is_refused():
    with pytest.raises(reqsummary.SummaryError, match="must be text"):
        reqsummary.check(["a", "b"])


# ---- through the tools -------------------------------------------------------------

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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": "operator", "password": "hunter2"})
        await c.post("/api/projects", json={"name": "Demo", "summary": "demo"})
        await c.post("/api/projects/demo/load")
        yield c


async def _requests(client):
    return (await client.get("/api/projects/demo/git/requests")).json()["requests"]


async def test_three_sentences_files_nothing(client):
    from backend.config import settings
    (settings.projects_dir / "demo" / "code" / "x.py").write_text("print(1)\n")
    out = await registry.dispatch("git_commit_request", {
        "message": "Add x", "summary": "Adds x. It prints one. Nothing else."})
    assert out.startswith("error: summary has 3 sentences")
    assert await _requests(client) == []
    out = await registry.dispatch("git_commit_request", {"message": "Add x"})
    assert out.startswith("error: summary is required")
    assert await _requests(client) == []


async def test_the_summary_and_the_step_ride_the_request(client):
    from backend.config import settings
    (settings.projects_dir / "demo" / "code" / "x.py").write_text("print(1)\n")
    db = await get_db()
    try:
        await db.execute("INSERT INTO conversations (id, summary) VALUES (7, 'fix the loader')")
        # an earlier call in the same chat, then the one that files
        await db.execute("INSERT INTO tool_calls (conversation_id, tool, args, call_id) "
                         "VALUES (7, 'read_file', '{}', 'call_a')")
        await db.commit()
    finally:
        await db.close()
    ctok = runtime.conversation_id.set(7)
    ktok = runtime.tool_call_id.set("call_b")
    try:
        out = await registry.dispatch("git_commit_request", {
            "message": "Add x", "summary": "Adds x.py, which prints one.  It is the first step."})
    finally:
        runtime.tool_call_id.reset(ktok)
        runtime.conversation_id.reset(ctok)
    assert "pending" in out
    [req] = await _requests(client)
    assert req["agent_summary"] == "Adds x.py, which prints one. It is the first step."
    assert req["call_id"] == "call_b" and req["conversation_id"] == 7
    # the loop records the call after it returns
    db = await get_db()
    try:
        cur = await db.execute("INSERT INTO tool_calls (conversation_id, tool, args, call_id) "
                               "VALUES (7, 'git', '{}', 'call_b')")
        step = cur.lastrowid
        await db.commit()
    finally:
        await db.close()
    r = (await client.get(f"/api/security/request_step?kind=git&id={req['id']}")).json()
    assert r == {"conversation_id": 7, "step": step}


async def test_a_row_without_a_call_id_finds_the_filing_call_by_tool_and_time(client):
    db = await get_db()
    try:
        await db.execute("INSERT INTO conversations (id, summary) VALUES (9, 'x')")
        await db.execute("INSERT INTO tool_calls (conversation_id, tool, args) "
                         "VALUES (9, 'read_file', '{}')")
        cur = await db.execute("INSERT INTO git_requests (project_slug, message, conversation_id) "
                               "VALUES ('demo', 'old one', 9)")
        rid = cur.lastrowid
        cur = await db.execute("INSERT INTO tool_calls (conversation_id, tool, args) "
                               "VALUES (9, 'git_commit_request', '{}')")
        step = cur.lastrowid
        await db.commit()
    finally:
        await db.close()
    r = (await client.get(f"/api/security/request_step?kind=git&id={rid}")).json()
    assert r == {"conversation_id": 9, "step": step}


async def test_inspect_diff_shows_new_files_whole(client):
    from backend.config import settings
    (settings.projects_dir / "demo" / "code" / "new.py").write_text("print('hi')\n")
    (settings.projects_dir / "demo" / "odd name.txt").write_text("x\n")
    plain = (await client.get("/api/projects/demo/git/diff")).json()["diff"]
    assert "untracked files" in plain and "+print('hi')" not in plain
    full = (await client.get("/api/projects/demo/git/diff?new_files=true")).json()["diff"]
    assert "+++ b/code/new.py" in full and "+print('hi')" in full
    # a name git quotes stays a name
    assert "untracked files" in full and "odd name.txt" in full
    one = (await client.get("/api/projects/demo/git/diff?new_files=true&path=code")).json()["diff"]
    assert "+print('hi')" in one and "odd name" not in one


async def test_request_step_knows_nothing_it_was_not_asked_about(client):
    assert (await client.get("/api/security/request_step?kind=nope&id=1")).json() == {
        "conversation_id": None, "step": None}
    assert (await client.get("/api/security/request_step?kind=page&id=missing")).json() == {
        "conversation_id": None, "step": None}


async def test_request_step_needs_a_login(tmp_env):
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/security/request_step?kind=git&id=1")
    assert r.status_code == 401

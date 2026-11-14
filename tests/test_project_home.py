"""The web app's project home page (backend/project_home.py): one read of a
project's chats, instructions, context files, memory and schedules; the
instructions and uploads go through the write chokepoint and onto the
always-loaded list, so they ride every prompt of the project."""
import httpx
import pytest

from backend import alwaysloaded, memory, writes
from backend.auth import hash_password
from backend.config import settings
from backend.db import get_db, init_db
from backend.main import app
from backend.memory import assemble_system_prompt


async def _sql(q, args=()):
    db = await get_db()
    try:
        cur = await db.execute(q, args)
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def _events(kind):
    db = await get_db()
    try:
        async with db.execute("SELECT summary FROM security_events WHERE kind = ? ORDER BY id",
                              (kind,)) as cur:
            return [r["summary"] for r in await cur.fetchall()]
    finally:
        await db.close()


async def _prompt(slug="demo"):
    db = await get_db()
    try:
        return await assemble_system_prompt(db, active=slug)
    finally:
        await db.close()


@pytest.fixture
async def client(tmp_env):
    await init_db()
    memory.ensure_memory_seeds()
    await _sql("INSERT INTO users (username, password_hash) VALUES (?, ?)",
               ("operator", hash_password("hunter2")))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": "operator", "password": "hunter2"})
        assert (await c.post("/api/projects", json={"name": "Demo", "summary": "demo"})).status_code == 200
        yield c


async def test_needs_the_operator_session(tmp_env):
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/projects/demo/home")).status_code == 401
        r = await c.put("/api/projects/demo/instructions", json={"content": "x"})
        assert r.status_code in (401, 403)


async def test_unknown_project_is_404(client):
    assert (await client.get("/api/projects/nope/home")).status_code == 404
    assert (await client.put("/api/projects/nope/instructions",
                             json={"content": "x"})).status_code == 404


async def test_home_lists_this_projects_chats_newest_activity_first(client):
    pid = (await client.get("/api/projects/demo")).json()["id"]
    await client.post("/api/projects", json={"name": "Other", "summary": "x"})
    other = (await client.get("/api/projects/other")).json()["id"]
    old = await _sql("INSERT INTO conversations (project_id, summary, started_at) "
                     "VALUES (?, 'old but busy', '2026-10-01 09:00:00')", (pid,))
    new = await _sql("INSERT INTO conversations (project_id, summary, started_at) "
                     "VALUES (?, 'newer, quiet', '2026-10-05 09:00:00')", (pid,))
    await _sql("INSERT INTO conversations (project_id, summary) VALUES (?, 'elsewhere')", (other,))
    await _sql("INSERT INTO conversations (project_id, summary, ephemeral) "
               "VALUES (?, 'temporary', 1)", (pid,))
    await _sql("INSERT INTO conversations (project_id, summary, kind) "
               "VALUES (?, 'a job node', 'subagent')", (pid,))
    await _sql("INSERT INTO messages (conversation_id, role, content, created_at) "
               "VALUES (?, 'user', 'hi', '2026-10-09 10:00:00')", (old,))
    r = (await client.get("/api/projects/demo/home")).json()
    assert r["name"] == "Demo" and r["slug"] == "demo"
    assert [c["id"] for c in r["chats"]] == [old, new]
    assert r["chats"][0]["last_at"] == "2026-10-09 10:00:00"
    assert r["chats"][1]["last_at"] is None and r["chats"][0]["running"] is False


async def test_home_lists_this_projects_schedules(client):
    await client.post("/api/schedules", json={"name": "nightly", "task": "report",
                                              "project_slug": "demo"})
    await client.post("/api/schedules", json={"name": "unscoped", "task": "x"})
    r = (await client.get("/api/projects/demo/home")).json()
    assert [s["name"] for s in r["schedules"]] == ["nightly"]


async def test_instructions_are_saved_first_on_the_list_and_ride_every_prompt(client):
    d = settings.projects_dir / "demo"
    (d / "notes.md").write_text("notes\n")
    await client.put("/api/projects/demo/context", json={"files": ["notes.md"]})
    home = (await client.get("/api/projects/demo/home")).json()
    assert home["instructions"]["text"] == "" and home["instructions"]["sha256"] == ""
    r = await client.put("/api/projects/demo/instructions",
                         json={"content": "Answer in French.", "base_sha256": ""})
    assert r.status_code == 200, r.text
    ins = r.json()["instructions"]
    assert ins["loaded"] and ins["inlined"] and ins["text"] == "Answer in French.\n"
    assert (d / "instructions.md").read_text() == "Answer in French.\n"
    assert oct((d / "instructions.md").stat().st_mode & 0o777) == "0o644"
    assert alwaysloaded.selection("demo") == ["instructions.md", "notes.md"]
    assert any("now loaded: instructions.md" in s for s in await _events("always_loaded_changed"))
    assert "Answer in French." in await _prompt()
    # the context list does not repeat the instructions
    home = (await client.get("/api/projects/demo/home")).json()
    assert [f["path"] for f in home["context"]["files"]] == ["notes.md"]


async def test_instructions_changed_since_opened_is_409(client):
    await client.put("/api/projects/demo/instructions", json={"content": "one"})
    r = await client.put("/api/projects/demo/instructions",
                         json={"content": "two", "base_sha256": "0" * 64})
    assert r.status_code == 409
    assert (settings.projects_dir / "demo" / "instructions.md").read_text() == "one\n"


async def test_instructions_holding_a_secret_value_are_refused(client, monkeypatch):
    monkeypatch.setattr(writes.secrets_mod, "find_in_bytes",
                        lambda b: ["API_KEY"] if b"sk-live-123" in b else [])
    r = await client.put("/api/projects/demo/instructions", json={"content": "key sk-live-123"})
    assert r.status_code == 422 and "API_KEY" in r.json()["detail"]
    assert not (settings.projects_dir / "demo" / "instructions.md").exists()
    assert "instructions.md" not in alwaysloaded.selection("demo")


async def test_empty_instructions_delete_the_file_and_leave_the_list(client):
    await client.put("/api/projects/demo/instructions", json={"content": "be brief"})
    r = await client.put("/api/projects/demo/instructions", json={"content": "  \n"})
    assert r.status_code == 200 and r.json()["instructions"]["loaded"] is False
    assert not (settings.projects_dir / "demo" / "instructions.md").exists()
    assert alwaysloaded.selection("demo") == []


async def test_upload_lands_in_context_and_is_loaded(client):
    r = await client.post("/api/projects/demo/context-files",
                          files={"file": ("style guide.md", b"Use short sentences.\n", "text/markdown")})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "context/style guide.md"
    files = r.json()["context"]["files"]
    assert files[0]["path"] == "context/style guide.md" and files[0]["uploaded"]
    assert files[0]["inlined"] and files[0]["tokens"] > 0
    assert alwaysloaded.selection("demo") == ["context/style guide.md"]
    assert "Use short sentences." in await _prompt()
    # the same name again does not overwrite
    r = await client.post("/api/projects/demo/context-files",
                          files={"file": ("style guide.md", b"second\n", "text/markdown")})
    assert r.json()["path"] == "context/style guide (2).md"


async def test_upload_names_cannot_escape_or_hide(client):
    r = await client.post("/api/projects/demo/context-files",
                          files={"file": ("../../.context.json", b"[]", "text/plain")})
    assert r.status_code == 200 and r.json()["path"] == "context/context.json"
    assert alwaysloaded.selection("demo") == ["context/context.json"]


async def test_binary_uploads_are_refused(client):
    r = await client.post("/api/projects/demo/context-files",
                          files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n\x00\xff\xfe", "image/png")})
    assert r.status_code == 415
    assert not (settings.projects_dir / "demo" / "context").exists()


async def test_a_pdf_without_text_is_refused(client):
    pypdf = pytest.importorskip("pypdf")
    import io
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    r = await client.post("/api/projects/demo/context-files",
                          files={"file": ("scan.pdf", buf.getvalue(), "application/pdf")})
    assert r.status_code == 422 and "no text" in r.json()["detail"]


async def test_remove_deletes_an_upload_but_only_unloads_other_files(client):
    d = settings.projects_dir / "demo"
    (d / "README.md").write_text("readme\n")
    await client.post("/api/projects/demo/context-files",
                      files={"file": ("a.txt", b"alpha\n", "text/plain")})
    await client.put("/api/projects/demo/context",
                     json={"files": [*alwaysloaded.selection("demo"), "README.md"]})
    r = await client.delete("/api/projects/demo/context-files", params={"path": "context/a.txt"})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert not (d / "context" / "a.txt").exists()
    r = await client.delete("/api/projects/demo/context-files", params={"path": "README.md"})
    assert r.json()["deleted"] is False and (d / "README.md").exists()
    assert alwaysloaded.selection("demo") == []
    assert (await client.delete("/api/projects/demo/context-files",
                                params={"path": "project.md"})).status_code == 400
    assert (await client.delete("/api/projects/demo/context-files",
                                params={"path": "README.md"})).status_code == 404


async def test_over_budget_files_are_marked_not_inlined(client, monkeypatch):
    monkeypatch.setattr(settings, "project_context_budget_tokens", 200)
    await client.post("/api/projects/demo/context-files",
                      files={"file": ("big.txt", b"word " * 400, "text/plain")})
    files = (await client.get("/api/projects/demo/home")).json()["context"]["files"]
    assert files[0]["inlined"] is False


async def test_memory_lists_the_journal_and_notes_that_mention_the_project(client):
    nd = memory.notes_dir()
    nd.mkdir(parents=True, exist_ok=True)
    (nd / "demo-deploys.md").write_text("---\ndescription: how Demo ships\n---\nUse the Pi.\n")
    (nd / "unrelated.md").write_text("---\ndescription: coffee\n---\nDemolition is loud.\n")
    (nd / "pending.md").write_text("---\nsource: agent\napproved: false\n---\nThe demo uses SQLite.\n")
    r = (await client.get("/api/projects/demo/memory")).json()
    assert "## Summary" in r["journal"] and r["journal_tokens"] > 0
    names = {n["name"]: n for n in r["notes"]}
    assert set(names) == {"demo-deploys", "pending"}
    assert names["pending"]["pending"] is True and names["demo-deploys"]["pending"] is False
    home = (await client.get("/api/projects/demo/home")).json()
    assert home["memory"] == {"notes": 2, "held": 0}

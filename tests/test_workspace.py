import httpx
import pytest

from backend.auth import hash_password
from backend.config import settings
from backend.db import get_db, init_db
from backend.main import app
from backend.memory import ensure_memory_seeds
from tests.platform_caps import needs_rlimit_as


@pytest.fixture
async def client(tmp_env):
    await init_db()
    ensure_memory_seeds()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("operator", hash_password("hunter2")),
        )
        await db.commit()
    finally:
        await db.close()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login",
                     json={"username": "operator", "password": "hunter2"})
        await c.post("/api/projects", json={"name": "Demo", "summary": "demo"})
        yield c


async def test_file_roundtrip_and_traversal_guard(client):
    r = await client.put("/api/projects/demo/file",
                         json={"path": "notes/idea.md", "content": "# hi"})
    assert r.status_code == 200
    r = await client.get("/api/projects/demo/file", params={"path": "notes/idea.md"})
    assert r.json()["content"] == "# hi"
    r = await client.get("/api/projects/demo/files")
    paths = [f["path"] for f in r.json()["files"]]
    assert "notes/idea.md" in paths and "project.md" in paths

    r = await client.get("/api/projects/demo/file", params={"path": "../../secrets"})
    assert r.status_code == 400
    r = await client.put("/api/projects/demo/file",
                         json={"path": "../evil.md", "content": "x"})
    assert r.status_code == 400


@needs_rlimit_as
async def test_run_scratch_code_with_artifact(client):
    r = await client.post("/api/projects/demo/run", json={
        "code": "print(2**10)\nopen('out.txt','w').write('artifact')\n"})
    assert r.status_code == 200
    body = r.json()
    assert body["exit_code"] == 0
    assert "1024" in body["stdout"]
    assert "code/out.txt" in body["artifacts"]

    r = await client.post("/api/projects/demo/run", json={"code": "raise ValueError('boom')"})
    body = r.json()
    assert body["exit_code"] != 0 and "boom" in body["stderr"]


async def test_todos(client):
    r = await client.post("/api/projects/demo/todos",
                          json={"action": "add", "text": "write physics sim"})
    assert r.json()["todos"] == [{"done": False, "text": "write physics sim"}]
    r = await client.post("/api/projects/demo/todos", json={"action": "toggle", "index": 0})
    assert r.json()["todos"][0]["done"] is True
    # persisted as a plain markdown checklist the agent can read
    # (the agent's own hidden file, not the project's todo.md)
    assert "- [x] write physics sim" in (settings.projects_dir / "demo" / ".todo.md").read_text()
    r = await client.post("/api/projects/demo/todos", json={"action": "delete", "index": 0})
    assert r.json()["todos"] == []


async def test_memory_api(client):
    r = await client.get("/api/memory")
    names = {f["path"]: f for f in r.json()["files"]}
    assert "soul.md" in names
    assert names["all-projects.md"]["auto_generated"] is True

    r = await client.put("/api/memory/file",
                         json={"path": "notes/facts.md", "content": "remember this"})
    assert r.status_code == 200
    r = await client.get("/api/memory/file", params={"path": "notes/facts.md"})
    assert r.json()["content"] == "remember this"
    r = await client.get("/api/memory/file", params={"path": "../../etc/passwd"})
    assert r.status_code == 400


async def test_gui_save_goes_through_the_write_chokepoint(client):
    """The operator's PUT /file lands through writes.apply_write: a file holding
    a real secret value is refused (and nothing is written), a protected path is
    refused, and a clean save lands with no exec bit."""
    from backend import secrets

    secrets.save({"DEMO_KEY": "sk-live-abcdef123456"})
    r = await client.put("/api/projects/demo/file",
                         json={"path": "notes/leak.md", "content": "key: sk-live-abcdef123456"})
    assert r.status_code == 400
    assert "secret" in r.json()["detail"]
    assert not (settings.projects_dir / "demo" / "notes" / "leak.md").exists()

    r = await client.put("/api/projects/demo/file",
                         json={"path": ".context.json", "content": "{}"})
    assert r.status_code == 400

    r = await client.put("/api/projects/demo/file",
                         json={"path": "code/tool.py", "content": "print(1)\n"})
    assert r.status_code == 200
    assert "flags" in r.json()
    mode = (settings.projects_dir / "demo" / "code" / "tool.py").stat().st_mode & 0o777
    assert mode == 0o644


async def test_zip_has_the_listed_files_and_needs_login(client):
    import io
    import zipfile

    await client.put("/api/projects/demo/file", json={"path": "notes/a.md", "content": "# a"})
    await client.put("/api/projects/demo/file", json={"path": "code/b.py", "content": "print()"})
    (settings.projects_dir / "demo" / ".hidden").write_text("no")
    r = await client.get("/api/projects/demo/zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="demo.zip"' in r.headers["content-disposition"]
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    listed = {f["path"] for f in (await client.get("/api/projects/demo/files")).json()["files"]}
    assert names == listed
    assert {"notes/a.md", "code/b.py", "project.md"} <= names
    assert ".hidden" not in names

    assert (await client.get("/api/projects/nope/zip")).status_code == 404
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
        assert (await anon.get("/api/projects/demo/zip")).status_code == 401

"""GET /api/security/glance: counts of what waits on the operator (nov1 contract s3)."""
import httpx
import pytest

from backend import db as db_mod
from backend import security
from backend.auth import hash_password
from backend.main import app


@pytest.fixture
async def client(tmp_env):
    await db_mod.init_db()
    security._pings.clear()
    conn = await db_mod.get_db()
    try:
        await conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                           ("operator", hash_password("hunter2")))
        await conn.commit()
    finally:
        await conn.close()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": "operator", "password": "hunter2"})
        yield c


async def test_requires_login(tmp_env):
    await db_mod.init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/security/glance")).status_code in (401, 403)


async def test_empty_is_all_zero(client):
    r = await client.get("/api/security/glance")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 0
    assert set(j["waiting"]) == {"egress", "packages", "environments", "skills", "git", "alerts"}
    assert j["net24h"] == {"allowed": 0, "denied": 0}


async def test_counts_and_project_filter(client):
    db = await db_mod.get_db()
    try:
        await db.execute("INSERT INTO egress_pending(project_slug, host, status) "
                         "VALUES ('a', 'x.com', 'pending')")
        await db.execute("INSERT INTO egress_pending(project_slug, host, status) "
                         "VALUES ('b', 'y.com', 'pending')")
        await db.execute("INSERT INTO git_requests(project_slug, message) VALUES ('a', 'm')")
        await db.execute("INSERT INTO skill_requests(project_slug, reason, source) "
                         "VALUES ('a', 'r', 'https://x/y')")
        await db.execute("INSERT INTO egress_events(project_slug, host, verdict) "
                         "VALUES ('a', 'ok.com', 'allow')")
        await db.commit()
    finally:
        await db.close()
    j = (await client.get("/api/security/glance")).json()
    assert j["waiting"]["egress"] == 2 and j["waiting"]["git"] == 1
    assert j["waiting"]["skills"] == 1
    assert j["total"] == sum(j["waiting"].values())
    assert j["net24h"]["allowed"] == 1
    a = (await client.get("/api/security/glance?project=a")).json()
    assert a["waiting"]["egress"] == 1
    b = (await client.get("/api/security/glance?project=b")).json()
    assert b["waiting"]["git"] == 0 and b["waiting"]["egress"] == 1

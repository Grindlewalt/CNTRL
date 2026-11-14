"""Approval notification center — aggregates the three pending stores.

Read-only aggregation; tested against an authed client with an empty DB (no
pending anything) plus one seeded git request to prove it surfaces."""
import httpx
import pytest

from backend.auth import hash_password
from backend.db import get_db, init_db
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login",
                     json={"username": "operator", "password": "hunter2"})
        yield c


async def test_notifications_empty(client):
    r = await client.get("/api/notifications")
    assert r.status_code == 200
    body = r.json()
    zero = {k: 0 for k in ("asks", "git", "sites", "services", "packages", "environments",
                           "skills", "journal", "pages", "alerts")}
    assert body == {"count": 0, "waiting": zero,
                    "git": [], "schedules": [], "desk_shell": [], "asks": [],
                    "memory_pending": 0, "alerts": 0, "egress_pending": 0, "critical": 0, "records": 0,
                    "level": "approvals", "ping_count": 0,
                    "dnd": {"on": False, "since": None, "until": None,
                            "break_critical": True}}


async def test_notifications_surfaces_pending_git(client):
    # seed a project + a pending git request
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO projects (slug, name, path) VALUES ('proj', 'Proj', 'proj')")
        await db.execute(
            "INSERT INTO git_requests (project_slug, message, status) "
            "VALUES ('proj', 'commit the thing', 'pending')")
        await db.commit()
    finally:
        await db.close()
    body = (await client.get("/api/notifications")).json()
    assert body["count"] == 1
    assert body["git"] and body["git"][0]["project"] == "proj"
    assert body["git"][0]["message"] == "commit the thing"


async def test_the_badge_counts_what_the_waiting_list_shows(client):
    # one row of every kind the Security page's "Waiting for you" list shows
    # (SecurityDash.jsx QueueList), two info alerts it folds away, and two loud
    # alerts in one card (one row): the badge is the list's row count
    from backend import egress, security
    from backend.config import settings
    db = await get_db()
    try:
        await db.execute("INSERT INTO projects (slug, name, path) VALUES ('proj', 'Proj', 'proj')")
        await db.execute("INSERT INTO git_requests (project_slug, message, status) "
                         "VALUES ('proj', 'commit', 'pending')")
        await db.execute("INSERT INTO services (project_slug, name, command, placement) "
                         "VALUES ('proj', 'web', '[\"python\"]', 'per_project')")
        await db.execute("INSERT INTO package_catalogue (manager, package, status, project_slug) "
                         "VALUES ('pip', 'httpx', 'pending', 'proj')")
        await db.execute("INSERT INTO environment_requests (project_slug, name, reason) "
                         "VALUES ('proj', 'data', 'pandas')")
        await db.execute("INSERT INTO skill_requests (project_slug, source, reason, status) "
                         "VALUES ('proj', 'https://example.dev/a/b', 'r', 'fetched')")
        await db.execute("INSERT INTO pages (id, project, name, path, status) "
                         "VALUES ('pg1', 'proj', 'site', 'site', 'pending')")
        await db.commit()
        await egress.note_denied(db, "proj", "cdn.example.dev")
        for i in range(2):
            await security.raise_event(db, kind="browser_session", summary=f"s{i}",
                                       severity="info", project="proj")
        for i in range(2):
            await security.raise_event(db, kind="unexpected_process", summary=f"u{i}",
                                       severity="warn", project="proj")
    finally:
        await db.close()
    d = settings.projects_dir / "proj"
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.md").write_text("# Proj\n\n- 2026-11-13 [unverified]: a held line\n")
    body = (await client.get("/api/notifications")).json()
    assert body["waiting"] == {"asks": 0, "git": 1, "sites": 1, "services": 1, "packages": 1,
                               "environments": 1, "skills": 1, "journal": 1, "pages": 1,
                               "alerts": 1}
    assert body["count"] == 9


async def test_notifications_requires_auth():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/notifications")).status_code == 401

"""nov4: published pages. Request -> approve -> a snapshot served at its link with
the sandbox headers; reject, unpublish, yolo, refusals (symlink, caps, a secret's
value), the operator routes' auth, and the pages server's rules (GET/HEAD only,
identical 404s, fixed content types, no cookies)."""
import json

import httpx
import pytest

from backend import devicetokens, pages, pages_server, permissions, secrets as secrets_mod
from backend.auth import hash_password
from backend.config import settings
from backend.db import get_db, init_db
from backend.main import app


@pytest.fixture
async def env(tmp_env, monkeypatch):
    await init_db()
    monkeypatch.setattr(settings, "pages_base_url", "http://pages.test")
    d = settings.projects_dir / "demo"
    d.mkdir(parents=True)
    (d / "site").mkdir()
    (d / "site" / "index.html").write_text("<h1>hi</h1>")
    (d / "site" / "app.js").write_text("1")
    (d / "site" / "data.bin").write_bytes(b"\x00\x01")
    (d / "brief.pdf").write_bytes(b"%PDF-1.4 x" * 100)
    return d


@pytest.fixture
async def srv(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=pages_server.app),
                                 base_url="http://pages") as c:
        yield c


async def _events(kind):
    db = await get_db()
    try:
        async with db.execute("SELECT detail, acknowledged FROM security_events "
                              "WHERE kind = ? ORDER BY id", (kind,)) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def test_request_approve_serves_with_headers(env, srv):
    p = await pages.request("demo", "site", "Demo site", 7)
    assert p["status"] == "pending" and p["url"] is None
    assert [e["acknowledged"] for e in await _events("page_publish_requested")] == [0]
    assert (await srv.get(f"/p/{p['id']}/")).status_code == 404     # nothing live yet
    live = await pages.approve(p["id"])
    assert live["status"] == "live" and live["url"] == f"http://pages.test/p/{p['id']}/"
    assert [e["acknowledged"] for e in await _events("page_publish_requested")] == [1]
    assert len(await _events("page_published")) == 1
    r = await srv.get(f"/p/{p['id']}/")
    assert r.status_code == 200 and r.text == "<h1>hi</h1>"
    assert r.headers["content-type"].startswith("text/html")
    assert "sandbox allow-scripts" in r.headers["content-security-policy"]
    assert "allow-same-origin" not in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert "set-cookie" not in r.headers
    # a later edit in the project is not live (a snapshot)
    (env / "site" / "index.html").write_text("changed")
    assert (await srv.get(f"/p/{p['id']}/index.html")).text == "<h1>hi</h1>"
    assert [x["id"] for x in await pages.list_pages("live")] == [p["id"]]


async def test_single_file_and_types(env, srv):
    p = await pages.approve((await pages.request("demo", "brief.pdf", "Brief"))["id"])
    r = await srv.get(f"/p/{p['id']}/brief.pdf")
    assert r.headers["content-type"] == "application/pdf"
    rg = await srv.get(f"/p/{p['id']}/brief.pdf", headers={"Range": "bytes=0-3"})
    assert rg.status_code == 206 and rg.content == b"%PDF"
    q = await pages.approve((await pages.request("demo", "site", "Site"))["id"])
    assert (await srv.get(f"/p/{q['id']}/app.js")).headers["content-type"].startswith("text/javascript")
    b = await srv.get(f"/p/{q['id']}/data.bin")
    assert b.headers["content-type"] == "application/octet-stream"
    assert "attachment" in b.headers["content-disposition"]


async def test_listing_without_index(env, srv):
    (env / "plain").mkdir()
    (env / "plain" / "a<b>.txt").write_text("x")
    p = await pages.approve((await pages.request("demo", "plain", "Plain"))["id"])
    r = await srv.get(f"/p/{p['id']}/")
    assert r.status_code == 200 and "a&lt;b&gt;.txt" in r.text and "<b>" not in r.text
    assert (await srv.get(f"/p/{p['id']}/a<b>.txt")).headers["content-type"].startswith("text/plain")


async def test_reject_and_unpublish(env, srv):
    a = await pages.request("demo", "site", "A")
    await pages.reject(a["id"])
    assert (await pages.get(a["id"]))["status"] == "rejected"
    assert not pages.page_dir(a["id"]).exists()
    assert len(await _events("page_rejected")) == 1
    b = await pages.approve((await pages.request("demo", "site", "B"))["id"])
    assert (await srv.get(f"/p/{b['id']}/")).status_code == 200
    await pages.unpublish(b["id"])
    assert (await srv.get(f"/p/{b['id']}/")).status_code == 404
    assert (await pages.get(b["id"]))["status"] == "unpublished"
    assert len(await _events("page_unpublished")) == 1
    with pytest.raises(pages.PageError):
        await pages.approve(a["id"])


async def test_yolo_publishes_at_once(env, srv, monkeypatch):
    async def yes(*a, **k):
        return True
    monkeypatch.setattr(permissions, "auto_approve", yes)
    p = await pages.request("demo", "site", "Now")
    assert p["status"] == "live"
    db = await get_db()
    try:
        async with db.execute("SELECT decided_by FROM pages WHERE id=?", (p["id"],)) as cur:
            assert (await cur.fetchone())["decided_by"] == "yolo"
    finally:
        await db.close()
    assert (await srv.get(f"/p/{p['id']}/")).status_code == 200


async def test_this_turn_files_are_flushed_first(env, monkeypatch):
    calls = []

    async def flush(key):
        calls.append(key)
        (env / "late.txt").write_text("written this turn")   # what the pull lands
    import backend.gitgate as gitgate
    monkeypatch.setattr(gitgate, "flush_guest_writes", flush)
    p = await pages.request("demo", "late.txt", "Late")
    await pages.approve(p["id"])
    assert calls == ["demo", "demo"]
    assert (pages.page_dir(p["id"]) / "late.txt").read_text() == "written this turn"


async def test_refusals(env, monkeypatch):
    (env / "evil").mkdir()
    (env / "evil" / "ok.txt").write_text("x")
    (env / "evil" / "link").symlink_to("/etc/hosts")
    with pytest.raises(pages.PageError, match="symlink"):
        await pages.request("demo", "evil", "E")
    with pytest.raises(pages.PageError, match="inside the project"):
        await pages.request("demo", "../x", "E")
    with pytest.raises(pages.PageError, match="no such"):
        await pages.request("demo", "nope", "E")
    (env / "dirlink").symlink_to(env / "site")
    with pytest.raises(pages.PageError, match="symlink"):
        await pages.request("demo", "dirlink", "E")
    monkeypatch.setattr(pages, "MAX_FILE_BYTES", 5)
    with pytest.raises(pages.PageError, match="over"):
        await pages.request("demo", "brief.pdf", "E")
    monkeypatch.setattr(pages, "MAX_FILE_BYTES", 10**9)
    monkeypatch.setattr(pages, "MAX_FILES", 2)
    with pytest.raises(pages.PageError, match="more than"):
        await pages.request("demo", "site", "E")
    monkeypatch.setattr(pages, "MAX_FILES", 1000)
    monkeypatch.setattr(pages, "MAX_BYTES", 10)
    with pytest.raises(pages.PageError, match="total over"):
        await pages.request("demo", "site", "E")


async def test_secret_value_refused_by_name(env, monkeypatch):
    monkeypatch.setattr(secrets_mod, "load", lambda: {"API_TOKEN": "sup3rs3cretvalue"})
    (env / "leak.html").write_text("<p>key sup3rs3cretvalue</p>")
    p = await pages.request("demo", "leak.html", "Leak")
    with pytest.raises(pages.PageError) as ei:
        await pages.approve(p["id"])
    assert "API_TOKEN" in str(ei.value) and "sup3rs3cretvalue" not in str(ei.value)
    assert (await pages.get(p["id"]))["status"] == "refused"
    assert not pages.page_dir(p["id"]).exists()
    ev = await _events("page_refused")
    assert len(ev) == 1 and "sup3rs3cretvalue" not in json.dumps(ev)


async def test_server_rules(env, srv):
    p = await pages.approve((await pages.request("demo", "site", "S"))["id"])
    pid = p["id"]
    for m in ("post", "put", "delete", "patch"):
        r = await srv.request(m.upper(), f"/p/{pid}/")
        assert r.status_code == 405 and "content-security-policy" in r.headers
    assert (await srv.head(f"/p/{pid}/")).status_code == 200
    miss_id = await srv.get("/p/unknownunknown1/index.html")
    miss_file = await srv.get(f"/p/{pid}/nope.html")
    assert miss_id.status_code == miss_file.status_code == 404
    assert miss_id.text == miss_file.text
    assert miss_id.headers["content-security-policy"] == miss_file.headers["content-security-policy"]
    assert "x-robots-tag" in miss_file.headers
    for bad in (f"/p/{pid}/../../jarvis.db", f"/p/{pid}/%2e%2e/%2e%2e/x", f"/p/{pid}/..%2f..%2fx"):
        r = await srv.get(bad)
        assert r.status_code in (404, 307) and "root" not in r.text
    assert (await srv.get("/")).status_code == 404
    assert (await srv.get(f"/p/{pid}")).status_code == 307
    plain = await srv.get(f"/p/{pid}/")
    withc = await srv.get(f"/p/{pid}/", headers={"Cookie": "jarvis_session=abc"})
    assert withc.text == plain.text and withc.status_code == plain.status_code
    assert "set-cookie" not in withc.headers


# --- operator routes -----------------------------------------------------------------

@pytest.fixture
async def op(env):
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


async def test_routes_flow(op, srv):
    p = await pages.request("demo", "site", "Via API", 3)
    got = (await op.get("/api/pages/requests")).json()["items"]
    assert [g["id"] for g in got] == [p["id"]] and got[0]["status"] == "pending"
    assert set(got[0]) == {"id", "project", "name", "path", "files", "bytes", "status", "url",
                           "created_at", "decided_at", "conversation_id"}
    assert (await op.get("/api/pages")).json()["items"] == []
    r = await op.post(f"/api/pages/requests/{p['id']}/approve")
    assert r.json() == {"ok": True, "url": f"http://pages.test/p/{p['id']}/"}
    assert [g["id"] for g in (await op.get("/api/pages")).json()["items"]] == [p["id"]]
    assert (await op.post(f"/api/pages/requests/{p['id']}/approve")).status_code == 400
    assert (await op.post(f"/api/pages/{p['id']}/unpublish")).json() == {"ok": True}
    q = await pages.request("demo", "site", "No")
    assert (await op.post(f"/api/pages/requests/{q['id']}/reject")).json() == {"ok": True}


async def test_routes_are_guarded(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as anon:
        for m, u in (("get", "/api/pages"), ("get", "/api/pages/requests"),
                     ("post", "/api/pages/requests/x/approve"),
                     ("post", "/api/pages/requests/x/reject"), ("post", "/api/pages/x/unpublish")):
            assert (await getattr(anon, m)(u)).status_code == 401, u
        db = await get_db()
        try:
            tok = await devicetokens.mint("phone", by="t")
        finally:
            await db.close()
        secret = tok["token"] if isinstance(tok, dict) else tok
        for u in ("/api/pages", "/api/pages/requests"):
            r = await anon.get(u, headers={"Authorization": f"Bearer {secret}"})
            assert r.status_code == 401


async def test_cross_origin_cookie_post_is_403(op):
    r = await op.post("/api/pages/x/unpublish", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


async def test_a_listing_never_turns_a_file_name_into_a_script_link(env, srv):
    (env / "odd").mkdir()
    (env / "odd" / "javascript:alert(1)").write_text("x")
    p = await pages.approve((await pages.request("demo", "odd", "Odd"))["id"])
    r = await srv.get(f"/p/{p['id']}/")
    assert 'href="javascript:' not in r.text and 'href="./javascript%3Aalert%281%29"' in r.text
    sub = await srv.get(f"/p/{p['id']}", follow_redirects=False)       # no trailing slash
    assert sub.status_code == 307 and sub.headers["location"] == f"/p/{p['id']}/"

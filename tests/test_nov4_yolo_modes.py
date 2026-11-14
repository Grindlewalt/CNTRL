"""nov4: yolo split in two (backend/permissions.py). `safe-yolo` is the old yolo
(the legacy stored/sent value "yolo" means it, forever); `yolo-full` is the new
yolo where nothing waits: every approval path a turn can reach approves the
request as it is made (decided_by "yolo") and records one yolo_auto_approved
event. Per path: FULL approves at once, safe-yolo / legacy yolo / no
conversation (a scheduled run) wait as before. Network, git, VMs: all mocked."""
import contextlib
import json

import httpx
import pytest

from backend import egress, permissions, runtime
from backend.auth import hash_password
from backend.db import get_db, init_db
from backend.main import app


@pytest.fixture
async def db(tmp_env):
    await init_db()
    conn = await get_db()
    yield conn
    await conn.close()


async def _conv(db, mode=None, parent=None) -> int:
    cur = await db.execute(
        "INSERT INTO conversations (summary, permission_mode, parent_conversation_id) "
        "VALUES ('t', ?, ?)", (mode, parent))
    await db.commit()
    return cur.lastrowid


@contextlib.contextmanager
def in_conv(cid):
    tok = runtime.conversation_id.set(cid)
    try:
        yield
    finally:
        runtime.conversation_id.reset(tok)


async def _events(db, kind="yolo_auto_approved") -> list[dict]:
    async with db.execute("SELECT * FROM security_events WHERE kind = ?", (kind,)) as cur:
        return [dict(r) for r in await cur.fetchall()]


# --- modes -----------------------------------------------------------------------

def test_mode_names():
    assert permissions.MODES == ("safe-yolo", "auto", "ask", "yolo-full")
    assert permissions.DEFAULT_MODE == "safe-yolo"
    assert permissions.FULL == "yolo-full"


async def test_legacy_yolo_is_safe_yolo_everywhere(db):
    cid = await _conv(db)
    await permissions.set_mode(db, cid, "yolo")
    await db.commit()
    async with db.execute("SELECT permission_mode FROM conversations WHERE id = ?",
                          (cid,)) as cur:
        assert (await cur.fetchone())[0] == "safe-yolo"
    # a row stored as "yolo" by an older version reads as safe-yolo, never FULL
    old = await _conv(db, "yolo")
    assert await permissions.get_mode(old) == "safe-yolo"
    assert not await permissions.never_asks(old)
    assert await permissions.get_mode(None) == "safe-yolo"
    assert await permissions.get_mode(await _conv(db)) == "safe-yolo"


async def test_set_mode_rejects_junk_and_takes_full(db):
    cid = await _conv(db)
    for bad in ("nope", "", "YOLO-FULL", None):
        with pytest.raises(ValueError):
            await permissions.set_mode(db, cid, bad)
    await permissions.set_mode(db, cid, "yolo-full")
    await db.commit()
    assert await permissions.never_asks(cid)


async def test_gate_lets_both_yolos_run(db):
    for mode in ("safe-yolo", "yolo", "yolo-full"):
        cid = await _conv(db, mode)
        with in_conv(cid):
            assert await permissions.gate("run_code", {"command": "rm -rf x"}) is None


async def test_children_inherit_full_and_no_conversation_never_asks(db):
    root = await _conv(db, "yolo-full")
    kid = await _conv(db, None, root)
    grandkid = await _conv(db, None, kid)
    assert await permissions.get_mode(grandkid) == "yolo-full"
    assert await permissions.auto_approve("package_request", cid=grandkid)
    # a child's own mode wins
    own = await _conv(db, "ask", root)
    assert not await permissions.auto_approve("package_request", cid=own)
    # a scheduled run has no conversation: never auto-approves
    with in_conv(None):
        assert not await permissions.auto_approve("package_request")
    assert not await permissions.never_asks(None)


async def test_auto_approve_records_one_event(db):
    cid = await _conv(db, "yolo-full")
    assert await permissions.auto_approve("service_request", cid=cid, project="p",
                                          summary="service x", detail={"service_id": 3})
    evs = await _events(db)
    assert len(evs) == 1
    assert evs[0]["project_slug"] == "p"
    assert json.loads(evs[0]["detail"])["request"] == "service_request"


@pytest.fixture
async def client(tmp_env):
    await init_db()
    c0 = await get_db()
    try:
        await c0.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                         ("operator", hash_password("hunter2")))
        await c0.commit()
    finally:
        await c0.close()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": "operator", "password": "hunter2"})
        yield c


async def test_api_accepts_legacy_yolo_as_safe_yolo(client):
    c0 = await get_db()
    try:
        cid = await _conv(c0)
    finally:
        await c0.close()
    r = await client.put(f"/api/chat/{cid}/permission_mode", json={"mode": "yolo"})
    assert r.status_code == 200 and r.json()["mode"] == "safe-yolo"
    got = (await client.get(f"/api/chat/{cid}/permission_mode")).json()
    assert got["mode"] == "safe-yolo"
    r = await client.put(f"/api/chat/{cid}/permission_mode", json={"mode": "yolo-full"})
    assert r.json()["mode"] == "yolo-full"
    assert (await client.put(f"/api/chat/{cid}/permission_mode",
                             json={"mode": "bogus"})).status_code == 422


async def test_old_client_new_chat_with_yolo_gets_safe_yolo(client, monkeypatch):
    """An installed terminal client sends permission_mode "yolo" for every new chat."""
    from backend import chat as chat_mod

    async def turn(cid, *a, **kw):
        yield {"type": "final", "content": "ok"}
    monkeypatch.setattr(chat_mod, "guest_turn", turn)
    r = await client.post("/api/chat", json={"message": "hi", "confirm_peak": True,
                                             "permission_mode": "yolo"})
    assert r.status_code == 200
    c0 = await get_db()
    try:
        async with c0.execute("SELECT id, permission_mode FROM conversations "
                              "ORDER BY id DESC LIMIT 1") as cur:
            cid, stored = tuple(await cur.fetchone())
        assert stored == "safe-yolo"
        assert not await permissions.never_asks(cid)
    finally:
        await c0.close()


# --- package_request ---------------------------------------------------------------

async def test_package_request_full_approves_and_safe_waits(db, monkeypatch):
    from backend import packages
    from tools.package_request import handler
    monkeypatch.setattr("backend.config.settings.vm_boxes_enabled", True)
    row = {"id": 7, "manager": "pip", "package": "rich", "version_req": None,
           "target_variant": "main"}

    async def fake_file(db_, **kw):
        return row
    approved = []

    async def fake_yolo(pid):
        approved.append(pid)
    monkeypatch.setattr(packages, "file_request", fake_file)
    monkeypatch.setattr(packages, "yolo_approve", fake_yolo)
    monkeypatch.setattr(handler, "active_slug", lambda: _aw("proj"))

    kicks = []
    from backend.vm import images
    monkeypatch.setattr(images.builder, "kick_resolve", lambda: kicks.append(1))

    full = await _conv(db, "yolo-full")
    with in_conv(full):
        out = await handler.run(summary="Test request.", manager="pip", package="rich", reason="r")
    import asyncio
    await asyncio.sleep(0)
    assert "approved unasked (yolo)" in out and approved == [7]
    assert len(await _events(db)) == 1 and kicks == []

    safe = await _conv(db, "yolo")
    with in_conv(safe):
        out = await handler.run(summary="Test request.", manager="pip", package="rich", reason="r")
    assert "pending" in out and approved == [7] and kicks == [1]
    assert len(await _events(db)) == 1


async def _aw(v):
    return v


async def test_packages_yolo_approve_resolves_approves_and_builds(db, monkeypatch):
    from backend import packages
    from backend.vm import images
    monkeypatch.setattr("backend.config.settings.vm_boxes_enabled", True)
    row = await packages.file_request(
        db, manager="pip", package="rich", version=None, install_command=None,
        reason="fmt", project=None, conversation_id=None, source="operator",
        target_variant="main")
    resolved, built = [], []

    async def fake_resolve():
        await packages.set_resolution(db, row["id"], resolved_version="13.7.0",
                                      integrity="sha256:abc")
        resolved.append(1)

    async def fake_exists(db_, v):
        return True

    async def fake_build(v):
        built.append(v)
    monkeypatch.setattr(images.builder, "resolve_pending", fake_resolve)
    monkeypatch.setattr(images, "variant_exists", fake_exists)
    monkeypatch.setattr(images.builder, "build", fake_build)
    out = await packages.yolo_approve(row["id"])
    got = await packages.get(db, row["id"])
    assert resolved == [1] and built == ["main"]
    assert got["status"] == "approved" and got["decided_by"] == "yolo"
    assert out["status"] == "approved"


async def test_packages_yolo_approve_unresolvable_stays_pending(db, monkeypatch):
    from backend import packages
    from backend.vm import images
    row = await packages.file_request(
        db, manager="pip", package="nope", version=None, install_command=None,
        reason="x", project=None, conversation_id=None, source="operator",
        target_variant="main")

    async def fake_resolve():
        return {"resolved": 0}
    monkeypatch.setattr(images.builder, "resolve_pending", fake_resolve)
    await packages.yolo_approve(row["id"])
    assert (await packages.get(db, row["id"]))["status"] == "pending"


# --- service_request ---------------------------------------------------------------

async def test_service_request_full_approves_with_requested_ports(db, monkeypatch):
    from backend.vm import services
    from tools.service_request import handler
    row = {"id": 4, "name": "web", "supersedes_id": None, "placement": "per_project",
           "artifact_sha256": "a" * 64,
           "ports": [{"port": 8080, "expose": "loopback"}, {"port": 9, "expose": "none"}]}

    async def fake_file(slug, args, conversation_id=None, **kw):
        return row
    calls = []

    async def fake_approve(sid, *, placement, expose_ports, by="operator", **kw):
        calls.append((sid, placement, expose_ports, by))
        return {**row, "status": "approved"}

    async def noflush(slug):
        return None
    monkeypatch.setattr(services, "file_request", fake_file)
    monkeypatch.setattr(services, "approve", fake_approve)
    monkeypatch.setattr(handler, "require_project", lambda: _aw("proj"))
    monkeypatch.setattr(handler, "flush_guest_writes", noflush)

    full = await _conv(db, "yolo-full")
    with in_conv(full):
        out = await handler.run(summary="Test request.", name="web", command=["x"], reason="r")
    assert "approved unasked (yolo)" in out
    assert calls == [(4, "per_project", [{"port": 8080, "bind": "loopback"}], "yolo")]
    assert len(await _events(db)) == 1

    safe = await _conv(db, "safe-yolo")
    with in_conv(safe):
        out = await handler.run(summary="Test request.", name="web", command=["x"], reason="r")
    assert "Nothing runs until the operator approves" in out and len(calls) == 1


# --- environment_request -----------------------------------------------------------

async def test_environment_request_full_vs_safe(db, monkeypatch):
    import asyncio
    from backend import environments
    from tools.environment_request import handler
    monkeypatch.setattr("backend.config.settings.vm_boxes_enabled", True)
    row = {"id": 2, "name": "pyenv", "kind": "build", "from_variant": "main",
           "packages": [{}]}

    async def fake_file(db_, **kw):
        return row
    done = []

    async def fake_yolo(eid):
        done.append(eid)
        return {**row, "status": "approved"}
    monkeypatch.setattr(environments, "file_request", fake_file)
    monkeypatch.setattr(environments, "yolo_approve", fake_yolo)
    monkeypatch.setattr(handler, "active_slug", lambda: _aw("proj"))
    full = await _conv(db, "yolo-full")
    with in_conv(full):
        out = await handler.run(summary="Test request.", name="pyenv", packages=["pip rich"], reason="r")
    await asyncio.sleep(0)
    assert "approved unasked (yolo)" in out and done == [2]
    safe = await _conv(db, "yolo")
    with in_conv(safe):
        out = await handler.run(summary="Test request.", name="pyenv", packages=["pip rich"], reason="r")
    assert "pending" in out and done == [2]
    assert len(await _events(db)) == 1


async def test_environment_use_kind_is_approved_inline(db, monkeypatch):
    from backend import environments
    from tools.environment_request import handler
    monkeypatch.setattr("backend.config.settings.vm_boxes_enabled", True)
    row = {"id": 3, "name": "img", "kind": "use", "from_variant": "main", "packages": []}

    async def fake_file(db_, **kw):
        return row

    async def fake_yolo(eid):
        return {**row, "status": "approved"}
    monkeypatch.setattr(environments, "file_request", fake_file)
    monkeypatch.setattr(environments, "yolo_approve", fake_yolo)
    monkeypatch.setattr(handler, "active_slug", lambda: _aw("proj"))
    with in_conv(await _conv(db, "yolo-full")):
        out = await handler.run(summary="Test request.", use="img", reason="r")
    assert "approved unasked (yolo)" in out


# --- skill_request -----------------------------------------------------------------

async def test_skill_request_full_installs_safe_waits(db, monkeypatch):
    from backend import skillrequests
    from tools.skill_request import handler
    row = {"id": 5, "source": "github.com/a/b", "granted": 1, "error": None}

    async def fake_file(db_, **kw):
        return row
    calls = []

    async def fake_yolo(db_, rid):
        calls.append(rid)
        return row
    monkeypatch.setattr(skillrequests, "file_request", fake_file)
    monkeypatch.setattr(skillrequests, "yolo_approve", fake_yolo)
    monkeypatch.setattr(handler, "active_slug", lambda: _aw("proj"))
    with in_conv(await _conv(db, "yolo-full")):
        out = await handler.run(summary="Test request.", source="github.com/a/b", reason="r")
    assert "approved unasked (yolo)" in out and calls == [5]
    with in_conv(await _conv(db, "yolo")):
        out = await handler.run(summary="Test request.", source="github.com/a/b", reason="r")
    assert "pending" in out and calls == [5]


async def test_skillrequests_yolo_approve_fetches_then_approves_as_yolo(db, monkeypatch):
    from backend import skillrequests
    seen = {}

    async def fake_fetch(db_, rid):
        return {"skill_sha256": "abc"}

    async def fake_approve(db_, rid, *, skill_sha256, decided_by, by_operator):
        seen.update(rid=rid, sha=skill_sha256, by=decided_by, op=by_operator)
        return {"id": rid}
    monkeypatch.setattr(skillrequests, "fetch", fake_fetch)
    monkeypatch.setattr(skillrequests, "approve", fake_approve)
    await skillrequests.yolo_approve(db, 9)
    assert seen == {"rid": 9, "sha": "abc", "by": "yolo", "op": False}


# --- egress hosts ------------------------------------------------------------------

async def test_egress_queued_host_full_approves_others_wait(db):
    await egress.note_denied(db, "proj", "api.example.com")
    await egress.note_denied(db, "proj", "other.example.com")
    full = await _conv(db, "yolo-full")
    safe = await _conv(db, "yolo")
    assert not await egress.yolo_approve_queued(db, "proj", "api.example.com", safe)
    assert not await egress.yolo_approve_queued(db, "proj", "api.example.com", None)
    assert not await egress.yolo_approve_queued(db, egress.GENERAL, "x.example.com", full)
    assert await _events(db) == []
    assert await egress.yolo_approve_queued(db, "proj", "api.example.com", full)
    assert len(await _events(db)) == 1
    async with db.execute("SELECT host, status FROM egress_pending ORDER BY host") as cur:
        got = {r["host"]: r["status"] for r in await cur.fetchall()}
    assert got == {"api.example.com": "approved", "other.example.com": "pending"}
    verdict, _ = await egress.decide(db, "proj", "api.example.com")
    assert verdict == "allow"
    # already decided: no second event
    assert not await egress.yolo_approve_queued(db, "proj", "api.example.com", full)
    assert len(await _events(db)) == 1


# --- git ---------------------------------------------------------------------------

async def test_git_requests_full_approves_safe_waits(db, monkeypatch):
    from backend import gitgate
    approved = []

    async def fake_approve(rid, slug=None):
        approved.append((rid, slug))
        return {"id": rid, "status": "approved", "project_slug": slug, "kind": "commit"}
    monkeypatch.setattr(gitgate, "approve_request", fake_approve)
    full = await _conv(db, "yolo-full")
    safe = await _conv(db, "yolo")
    for kind in ("commit", "remote", "push", "merge"):
        row = {"id": 1, "kind": kind, "project_slug": "p", "message": "m",
               "status": "pending", "conversation_id": full}
        got, err = await gitgate.yolo_decide(row)
        assert got["status"] == "approved" and err is None
    assert len(approved) == 4 and len(await _events(db)) == 4
    for cid in (safe, None):
        row = {"id": 2, "kind": "push", "project_slug": "p", "message": "m",
               "status": "pending", "conversation_id": cid}
        got, err = await gitgate.yolo_decide(row)
        assert got["status"] == "pending" and err is None
    assert len(approved) == 4


async def test_git_yolo_decide_failure_stays_pending(db, monkeypatch):
    from backend import gitgate
    cur = await db.execute("INSERT INTO git_requests (project_slug, message, conversation_id) "
                           "VALUES ('p', 'm', 1)")
    await db.commit()
    rid = cur.lastrowid
    full = await _conv(db, "yolo-full")

    async def boom(rid_, slug=None):
        raise RuntimeError("push refused")
    monkeypatch.setattr(gitgate, "approve_request", boom)
    got, err = await gitgate.yolo_decide({"id": rid, "kind": "commit", "project_slug": "p",
                                          "message": "m", "conversation_id": full})
    assert got["status"] == "pending" and "push refused" in err


async def test_commit_handler_tells_the_agent(db, monkeypatch):
    from tools.git_commit_request import handler
    row = {"id": 8, "message": "m", "replaced": [], "harness_notice": []}

    async def fake_create(slug, message, paths=None):
        return row

    async def fake_decide(r):
        return {**r, "status": "approved", "commit_sha": "c0ffee1234567", "error": None}, None

    async def noop(slug):
        return None
    monkeypatch.setattr(handler, "require_project", lambda: _aw("proj"))
    monkeypatch.setattr(handler, "ensure_repo", noop)
    monkeypatch.setattr(handler, "create_request", fake_create)
    monkeypatch.setattr(handler, "yolo_decide", fake_decide)
    out = await handler.run("m", summary="Test request.")
    assert "approved unasked (yolo)" in out and "c0ffee1234" in out


# --- held memory / journal / always-loaded -----------------------------------------

async def test_journal_hold_full_releases_safe_holds(db, tmp_env, monkeypatch):
    from backend import journalhold, memory
    from backend.runtime import write_taint
    from tools.journal_update import handler
    md = memory.project_md_path("proj")
    md.parent.mkdir(parents=True)
    md.write_text("# proj\n")
    monkeypatch.setattr(handler, "require_project", lambda: _aw("proj"))
    held = []

    async def fake_hold(slug, line):
        held.append(line)
    monkeypatch.setattr(journalhold, "hold_event", fake_hold)
    tok = write_taint.set(True)
    try:
        with in_conv(await _conv(db, "yolo")):
            out = await handler.run("first thing")
        assert "waits in the Queue" in out and len(held) == 1
        assert journalhold.held_in("proj")
        with in_conv(await _conv(db, "yolo-full")):
            out = await handler.run("second thing")
        assert "approved unasked (yolo)" in out and len(held) == 1
        texts = [h["text"] for h in journalhold.held_in("proj")]
        assert not any("second thing" in t for t in texts)
        assert "second thing" in memory.read_project_md("proj")
    finally:
        write_taint.reset(tok)
    assert len(await _events(db)) == 1


async def test_memory_write_full_approves_note_and_proposal(db, tmp_env):
    from backend import memory
    from backend.runtime import write_taint
    from tools.memory_write import handler
    notes = memory.notes_dir()
    notes.mkdir(parents=True, exist_ok=True)
    tok = write_taint.set(True)
    try:
        with in_conv(await _conv(db, "yolo")):
            out = await handler.run("a-note", "hello", mode="replace")
        assert "PENDING" in out
        meta, _ = memory.parse_note((notes / "a-note.md").read_text())
        assert not memory.note_trusted(meta)
        with in_conv(await _conv(db, "yolo-full")):
            out = await handler.run("b-note", "hello", mode="replace")
            assert "approved unasked (yolo)" in out
            meta, _ = memory.parse_note((notes / "b-note.md").read_text())
            assert memory.note_trusted(meta) and "taint" not in meta
            # b-note is binding now: a further write is a proposal, approved on the spot
            out = await handler.run("b-note", "changed", mode="replace")
            assert "approved unasked (yolo)" in out
        assert "changed" in (notes / "b-note.md").read_text()
        assert memory.read_proposal("b-note", notes) is None
    finally:
        write_taint.reset(tok)
    assert len(await _events(db)) == 2


async def test_always_loaded_tainted_write_lands_in_full(db, tmp_env, monkeypatch):
    from backend import alwaysloaded, taintpaths, writes
    proj = tmp_env / "projects" / "proj"
    proj.mkdir(parents=True)
    (proj / "project.md").write_text("old\n")
    monkeypatch.setattr(alwaysloaded, "is_loaded", lambda slug, rel: rel == "project.md")
    held = []

    async def fake_hold(slug, rel, content):
        held.append(rel)
        return {}
    monkeypatch.setattr(alwaysloaded, "hold", fake_hold)
    with in_conv(await _conv(db, "yolo")):
        _, was_held = await writes.apply_write_gated("proj", "project.md", b"new1\n",
                                                     tainted=True)
    assert was_held and held == ["project.md"]
    assert (proj / "project.md").read_text() == "old\n"
    with in_conv(await _conv(db, "yolo-full")):
        _, was_held = await writes.apply_write_gated("proj", "project.md", b"new2\n",
                                                     tainted=True)
    assert not was_held and held == ["project.md"]
    assert (proj / "project.md").read_text() == "new2\n"
    assert "project.md" not in taintpaths.paths("proj")
    assert len(await _events(db)) == 1


async def test_a_device_token_cannot_turn_yolo_on(client):
    # chat only: a device approves nothing, so it must not reach the mode that approves
    # everything, by PUT or with a new chat's first message
    conn = await get_db()
    try:
        cid = await _conv(conn)
    finally:
        await conn.close()
    from backend import devicetokens
    raw, _ = await devicetokens.mint("phone", by="operator")
    head = {"Authorization": f"Bearer {raw}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as anon:
        r = await anon.put(f"/api/chat/{cid}/permission_mode", json={"mode": "yolo-full"},
                           headers=head)
        assert r.status_code == 403 and "password login" in r.text
        assert await permissions.get_mode(cid) == "safe-yolo"
        r = await anon.post("/api/chat", json={"message": "hi", "permission_mode": "yolo-full"},
                            headers=head)
        assert r.status_code == 403
        r = await anon.put(f"/api/chat/{cid}/permission_mode", json={"mode": "auto"}, headers=head)
        assert r.status_code == 200 and await permissions.get_mode(cid) == "auto"
    r = await client.put(f"/api/chat/{cid}/permission_mode", json={"mode": "yolo-full"})
    assert r.status_code == 200 and await permissions.get_mode(cid) == permissions.FULL

"""nov4 in the terminal client: a publish request is a PAGE row in the /security Queue
(y publishes, n rejects, each after a confirm), /pages lists the live pages and
unpublishes one after a confirm, and a server without the routes shows nothing."""
import httpx
import pytest

from cli_fake import FakeServer, load_client

jav3 = load_client("jav3cli_pages")
FULL = jav3.SESSION_PREFIX + "jwt"
REQ = {"id": "rq1", "project": "demo", "name": "report", "path": "out/", "bytes": 3000,
       "files": [{"path": "index.html", "size": 2000}, {"path": "a.png", "size": 1000}],
       "status": "pending", "url": None, "created_at": "2026-11-04T10:00:00Z"}
LIVE = {"id": "pg1", "project": "demo", "name": "site", "path": "site/", "bytes": 10,
        "files": [], "status": "live", "url": "http://h:8090/p/pg1/",
        "created_at": "2026-11-04T09:00:00Z"}


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))


class PagesServer(FakeServer):
    def __init__(self, requests=(REQ,), live=(LIVE,), routes=True):
        super().__init__(full=True)
        self.requests, self.live, self.routes = list(requests), list(live), routes
        self.writes: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/security/runs":
            return httpx.Response(200, json={"runs": [], "totals": {}})
        if path.startswith("/api/pages"):
            if not self.routes:
                return httpx.Response(404, json={"detail": "nope"})
            if method == "GET":
                return httpx.Response(200, json={
                    "items": self.requests if path.endswith("/requests") else self.live})
            self.writes.append((method, path))
            return httpx.Response(200, json={"ok": True, "url": "http://h:8090/p/new/"})
        return super().handle(request)


async def _until(pilot, cond, tries=80):
    for _ in range(tries):
        if cond():
            return True
        await pilot.pause(0.05)
    return cond()


async def _open(pilot, app):
    app.dispatch("/security")
    assert await _until(pilot, lambda: type(app.top).__name__ == "SecurityPage")
    scr = app.top
    assert await _until(pilot, lambda: scr.loaded["queue"])
    await pilot.pause(0.1)
    return scr


@pytest.mark.parametrize("key,verb", [("y", "approve"), ("n", "reject")])
async def test_queue_row_decides_after_a_confirm(key, verb):
    srv = PagesServer()
    app = jav3.build_tui("http://h:1", FULL, transport=srv.transport())
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        scr = await _open(pilot, app)
        assert await _until(pilot, lambda: [e["type"] for e in scr.entries["queue"]] == ["page"])
        row = scr.row_markup(scr.entries["queue"][0])
        assert "PAGE" in row and "report" in row and "demo" in row
        detail = scr.detail_markup(scr.entries["queue"][0])
        assert "index.html" in detail and "a.png" in detail
        await pilot.press(key)
        assert await _until(pilot, lambda: type(app.top).__name__ == "Confirm")
        await pilot.press("n")                              # cancelled: nothing sent
        assert await _until(pilot, lambda: type(app.top).__name__ != "Confirm")
        assert srv.writes == []
        await pilot.press(key)
        assert await _until(pilot, lambda: type(app.top).__name__ == "Confirm")
        await pilot.press("y")
        assert await _until(pilot, lambda: srv.writes)
        assert srv.writes == [("POST", f"/api/pages/requests/rq1/{verb}")]


async def test_server_without_the_routes_shows_nothing():
    srv = PagesServer(routes=False)
    app = jav3.build_tui("http://h:1", FULL, transport=srv.transport())
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        scr = await _open(pilot, app)
        assert scr.entries["queue"] == []
        assert not any("pages" in e for e in scr.errors["queue"])
        app.dispatch("/pages")
        await pilot.pause(0.5)
        assert type(app.top).__name__ != "Picker"


async def test_pages_lists_live_pages_and_unpublishes_after_a_confirm():
    srv = PagesServer()
    app = jav3.build_tui("http://h:1", FULL, transport=srv.transport())
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        app.dispatch("/pages")
        assert await _until(pilot, lambda: type(app.top).__name__ == "Picker")
        assert [r[1] for r in app.top.rows] == ["site"]
        assert "http://h:8090/p/pg1/" in app.top.rows[0][2]
        await pilot.press("u")
        assert await _until(pilot, lambda: type(app.top).__name__ == "Confirm")
        await pilot.press("n")                              # cancelled: nothing sent
        assert await _until(pilot, lambda: type(app.top).__name__ == "Picker")
        assert srv.writes == []
        await pilot.press("u")
        assert await _until(pilot, lambda: type(app.top).__name__ == "Confirm")
        await pilot.press("y")
        assert await _until(pilot, lambda: srv.writes)
        assert srv.writes == [("POST", "/api/pages/pg1/unpublish")]

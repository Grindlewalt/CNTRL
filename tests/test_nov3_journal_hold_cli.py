"""nov3 in the terminal client: a journal line held as [unverified] is a row in the
/security Queue, y approves it and n drops it (each after a confirm), and a server
without the route simply has no such rows."""
import httpx
import pytest

from cli_fake import FakeServer, load_client

jav3 = load_client("jav3cli_journal")
FULL = jav3.SESSION_PREFIX + "jwt"
HELD = {"project": "demo", "id": "ab12cd34ef567890", "date": "2026-11-03",
        "text": "the build needs [b]node 22[/]"}


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))


class JournalServer(FakeServer):
    def __init__(self, items=(HELD,)):
        super().__init__(full=True)
        self.items = list(items) if items is not None else None
        self.writes: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/security/runs":
            return httpx.Response(200, json={"runs": [], "totals": {}})
        if path == "/api/memory/held-journal":
            if self.items is None:
                return httpx.Response(404, json={"detail": "nope"})
            return httpx.Response(200, json={"items": self.items})
        if path.startswith("/api/memory/held-journal/") and method == "POST":
            self.writes.append((method, path))
            self.items = []
            return httpx.Response(200, json={"ok": True})
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


@pytest.mark.parametrize("key,verb", [("y", "approve"), ("n", "drop")])
async def test_y_approves_and_n_drops_after_a_confirm(key, verb):
    srv = JournalServer()
    app = jav3.build_tui("http://h:1", FULL, transport=srv.transport())
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        scr = await _open(pilot, app)
        assert await _until(pilot, lambda: [e["type"] for e in scr.entries["queue"]] == ["journal"])
        row = scr.row_markup(scr.entries["queue"][0])
        assert "JOURNAL" in row and "demo" in row and "\\[b]node 22" in row
        assert "[b]node 22" in scr.detail_markup(scr.entries["queue"][0]).replace("\\[", "[")
        await pilot.press(key)
        assert await _until(pilot, lambda: type(app.top).__name__ == "Confirm")
        await pilot.press("n")                              # cancelled: nothing sent
        assert await _until(pilot, lambda: type(app.top).__name__ != "Confirm")
        assert srv.writes == []
        await pilot.press(key)
        assert await _until(pilot, lambda: type(app.top).__name__ == "Confirm")
        await pilot.press("y")
        assert await _until(pilot, lambda: srv.writes)
        assert srv.writes == [("POST", f"/api/memory/held-journal/demo/{HELD['id']}/{verb}")]


async def test_an_older_server_has_no_journal_rows():
    srv = JournalServer(items=None)
    app = jav3.build_tui("http://h:1", FULL, transport=srv.transport())
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        scr = await _open(pilot, app)
        assert [e for e in scr.entries["queue"] if e["type"] == "journal"] == []

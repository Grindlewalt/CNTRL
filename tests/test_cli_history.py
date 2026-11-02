"""/history and /undo (nov2): the edit history from the terminal client."""
import httpx
import pytest

from cli_fake import FakeServer, load_client, top, wait_for

jav3 = load_client("jav3cli_history")

ENTRIES = [
    {"sha": "a" * 40, "when": 1760000000, "conversation_id": 7,
     "files": {"applied": ["x.py"], "deleted": ["old.txt"]}, "message": "#7"},
    {"sha": "b" * 40, "when": 1759990000, "conversation_id": 3,
     "files": {"applied": ["y.py"], "deleted": []}, "message": "#3"},
]


class HistServer(FakeServer):
    def __init__(self) -> None:
        super().__init__(projects=["demo"], full=True)
        self.restores = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/api/projects/demo/history":
            return httpx.Response(200, json={"entries": ENTRIES})
        if p.endswith("/restore"):
            self.restores.append((p, request.content))
            return httpx.Response(200, json={"restored": ["x.py"], "deleted": []})
        return super().handle(request)


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def notes(app) -> str:
    return "\n".join(str(w.render()) for w in app.query("Note"))


def _app(srv, token=None):
    return jav3.build_tui("http://h:1", token or jav3.SESSION_PREFIX + "jwt",
                          transport=srv.transport())


async def test_history_lists_entries():
    srv = HistServer()
    app = _app(srv)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.focused_chat.project = "demo"
        app.dispatch("/history")
        assert await wait_for(lambda: "edit history of demo" in notes(app))
        out = notes(app)
        assert "aaaaaaaa" in out and "#7" in out and "old.txt (deleted)" in out


async def test_undo_posts_the_restore_after_confirm():
    srv = HistServer()
    app = _app(srv)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.focused_chat.project = "demo"
        app.cid = 7
        app.dispatch("/undo")
        assert await wait_for(lambda: top(app) == "Confirm")
        assert "x.py" in str(app.screen.detail)
        await pilot.press("y")
        assert await wait_for(lambda: srv.restores)
        assert srv.restores[0][0] == "/api/projects/demo/history/" + "a" * 40 + "/restore"
        assert await wait_for(lambda: "undone: 1 restored" in notes(app))


async def test_chat_only_login_is_told_it_needs_full_access():
    srv = HistServer()
    app = _app(srv, "jvd_x")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.focused_chat.project = "demo"
        app.dispatch("/undo")
        assert await wait_for(lambda: "needs full access" in notes(app))
        assert srv.restores == []

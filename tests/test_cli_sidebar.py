"""The terminal client's sidebar (nov1, S): sections that appear and hide by data, the
this-turn numbers the status row shows, the x that hides it, ctrl+x v opening one Security
tab, and the foot following the connection's state."""
import httpx
import pytest

from cli_fake import FakeServer, load_client, wait_for

jav3 = load_client("jav3cli_sidebar")

GIT = ("branch: main\nlast commit: a1b2c3d x\nchanges:\n M index.html\nA  GAME.md\n?? smoke.js")
GLANCE = {"waiting": {"egress": 1, "packages": 0, "environments": 0, "skills": 0, "git": 0,
                      "alerts": 0}, "total": 1, "net24h": {"allowed": 2, "denied": 0}}
BOXES = {"enabled": True, "boxes": [
    {"id": "p-demo", "kind": "project", "project": "demo", "projects": ["demo"],
     "state": "running", "uptime_s": 360, "image": {"variant": "main", "version": 4}}]}


class SbServer(FakeServer):
    """The base fake plus the sidebar's sources; `gone` names the ones this 'older server'
    does not have (404)."""

    def __init__(self) -> None:
        super().__init__()
        self.gone: set = set()
        self.agents: list = []
        self.info = {"title": "t", "files": [], "local": None, "cost_usd": 0.06,
                     "input_tokens": 1_000_000, "output_tokens": 200_000,
                     "context": {"used": 40, "window": 1000}}

    def handle(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        hit = {"/api/security/glance": ("glance", GLANCE),
               "/api/vm/boxes": ("box", BOXES),
               "/api/chat/agents": ("agents", {"nodes": self.agents})}.get(p)
        if p.endswith("/git/status"):
            hit = ("git", {"status": GIT})
        elif p.startswith("/api/conversations/") and p.endswith("/info"):
            hit = ("info", self.info)
        if hit:
            if hit[0] in self.gone:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json=hit[1])
        return super().handle(request)


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))


def text(app, wid: str) -> str:
    return str(app.query_one("#" + wid).render())


async def start(srv=None, size=(130, 40)):
    srv = srv or SbServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    return srv, app


async def show(pilot, app, project="demo", cid=5):
    await pilot.pause(0.3)
    app.focused_chat.project, app.focused_chat.cid = project, cid
    app.sidebar_pref = True
    app.refresh_chrome()
    await pilot.pause(0.1)


async def polled(pilot, app, *kinds):
    ok = await wait_for(lambda: (app._sidebar_tick() or True)
                        and all(app.sb.get(k) is not None for k in kinds), tries=60)
    assert ok, {k: app.sb.get(k) for k in kinds}
    app._sidebar()
    await pilot.pause(0.1)


async def test_sections_show_their_data_and_hide_without_it():
    srv, app = await start()
    async with app.run_test(size=(130, 40)) as pilot:
        await show(pilot, app)
        assert not app.query_one("#sb-agents").display      # nothing polled yet / no agents
        await polled(pilot, app, "git", "glance", "box")
        git = text(app, "sb-files")
        assert "3 changed" in git and "index.html" in git and "smoke.js" in git
        assert "1 host waiting" in text(app, "sb-sec") and "ctrl+x v" in text(app, "sb-sec")
        assert "main" in text(app, "sb-box") and "up 6m" in text(app, "sb-box")
        assert not app.query_one("#sb-agents").display and not app.query_one("#sb-todo").display
        assert "ctrl+b" in text(app, "sb-title")
        # agents appear when this chat has a subtree, with the tree's own words
        srv.agents = [
            {"id": 5, "parent_id": None, "title": "me", "status": "running", "running": True},
            {"id": 6, "parent_id": 5, "title": "physics", "agent_slug": "tuner",
             "status": "running", "running": True},
            {"id": 7, "parent_id": 5, "title": "track", "status": "done", "running": False}]
        app.sb["at"]["agents"] = -1e9
        await polled(pilot, app, "agents")
        ag = text(app, "sb-agents")
        assert "1 running" in ag and "1 done" in ag and "tuner" in ag
        app.todos = [(True, "research"), (False, "write")]
        app._sidebar()
        assert "1/2" in text(app, "sb-todo")


async def test_a_missing_endpoint_hides_its_section_quietly():
    srv = SbServer()
    srv.gone = {"glance", "git", "box"}
    srv, app = await start(srv)
    async with app.run_test(size=(130, 40)) as pilot:
        await show(pilot, app)
        for _ in range(10):
            app._sidebar_tick()
            await pilot.pause(0.1)
        assert {"glance", "git", "box"} <= app.sb["gone"]
        assert not app.query_one("#sb-sec").display and not app.query_one("#sb-box").display
        assert not app.query_one("#sb-files").display        # no git, no per-chat files either


async def test_git_falls_back_to_the_chats_own_modified_files():
    srv = SbServer()
    srv.gone = {"git"}
    srv.info["files"] = [{"path": "src/a.py", "writes": 2}]
    srv, app = await start(srv)
    async with app.run_test(size=(130, 40)) as pilot:
        await show(pilot, app)
        app.info = srv.info
        for _ in range(8):
            app._sidebar_tick()
            await pilot.pause(0.1)
        assert "Modified Files" in text(app, "sb-files") and "a.py" in text(app, "sb-files")


async def test_this_turn_shows_the_numbers_the_status_row_shows():
    srv, app = await start()
    async with app.run_test(size=(130, 40)) as pilot:
        await show(pilot, app)
        assert "This turn" not in text(app, "sb-context")          # no turn yet
        t = jav3.TurnState()
        t.ntools, t.cost, t.tin, t.tout = 22, 0.04, 914_800, 56_800
        app.info = srv.info
        app.turn, app.busy = t, True
        app._refresh_status()
        app._sidebar()
        side, row = text(app, "sb-context"), text(app, "status-left")
        stats = t.stats()
        assert len(stats) >= 3
        for bit in stats[1:]:                          # elapsed ticks; tools and cost don't
            assert bit in side and bit in row, (bit, side, row)
        assert "$0.04" in side and "↑ 914.8k" in side and "ctx" in side and "$0.06" in side
        app.turn, app.busy = None, False
        app.last_turn = t
        app._sidebar()
        assert "22 tools" in text(app, "sb-context")             # kept after the turn ends


async def test_the_x_hides_the_sidebar_like_ctrl_b():
    srv, app = await start()
    async with app.run_test(size=(130, 40)) as pilot:
        await show(pilot, app)
        assert app.query_one("#sidebar").display and app.sidebar_shown()
        app.sidebar_click("title", 5, 0, 38)                    # not on the x: nothing
        assert app.sidebar_shown()
        app.sidebar_click("title", 30, 0, 38)                   # on "x ctrl+b"
        await pilot.pause(0.1)
        assert not app.sidebar_shown() and not app.query_one("#sidebar").display


async def test_ctrl_x_v_opens_one_security_tab_and_jumps_back_to_it():
    srv, app = await start()
    async with app.run_test(size=(130, 40)) as pilot:
        await show(pilot, app)
        assert len(app.ptabs) == 1
        await pilot.press("ctrl+x", "v")
        assert await wait_for(lambda: len(app.ptabs) == 2 and app.ptab_i == 1)
        assert await wait_for(lambda: type(app.page) is jav3.SecurityPage if hasattr(
            jav3, "SecurityPage") else app.paged)
        await pilot.press("ctrl+x", "1")                         # back to the chat
        assert app.ptab_i == 0
        await pilot.press("ctrl+x", "v")                         # jumps, no third tab
        assert await wait_for(lambda: app.ptab_i == 1)
        assert len(app.ptabs) == 2


@pytest.mark.parametrize("state, word, mark", [
    ("live", "live (beat", "$success"), ("quiet", "quiet", "$warning"),
    ("reconnecting", "reconnecting: wifi is down", "$error"),
    ("down", "down: server is off", "$error")])
async def test_the_foot_follows_the_link(state, word, mark):
    srv, app = await start()
    async with app.run_test(size=(130, 40)) as pilot:
        await show(pilot, app)
        app.link.beat()
        app.link.state = state
        app.link.why = {"reconnecting": "wifi is down", "down": "server is off"}.get(state, "")
        foot = app._sb_foot()
        assert word in foot and f"[{mark}]" in foot and "h:1" in foot
        app._sidebar()
        assert "full access" in text(app, "sb-foot") or "chat only" in text(app, "sb-foot")


def test_git_status_text_parses():
    got = jav3.parse_git_changes(GIT)
    assert got == [("M", "index.html"), ("A", "GAME.md"), ("?", "smoke.js")]
    assert jav3.parse_git_changes("branch: m\nlast commit: x\nchanges:\nclean") == []

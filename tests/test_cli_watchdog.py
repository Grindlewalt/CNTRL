"""Never silently frozen (clients/cntrl/cntrl): whose fault a failure is, a dead stream
that times out into the reattach loop, the quiet-turn lines, and the pulse that settles
a turn the server finished without telling us."""
import time

import httpx
import pytest

from cli_fake import FakeServer, load_client, open_chat, wait_for

jav3 = load_client("jav3cli_watchdog")
REAL_PROBE = jav3.probe_route
BASE = "http://10.0.0.82:8000"


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(jav3, "RECONNECT_STEPS", (0.05,), raising=False)
    monkeypatch.setattr(jav3, "probe_route", lambda h, p: None)


# --- the classifier ---------------------------------------------------------------------------

def test_no_route_is_the_clients_fault():
    where, text = jav3.classify_net_error(httpx.ConnectError("x"), BASE, route="route")
    assert (where, text) == ("client", "your network is down (no route to 10.0.0.82)")
    assert jav3.classify_net_error(httpx.ConnectError("x"), BASE, route="dns")[0] == "client"
    # a route exists but the host on it does not answer: the server's machine, not your network
    where, text = jav3.classify_net_error(httpx.ConnectError("[Errno 65] No route to host"),
                                          BASE, route="ok")
    assert where == "server" and "no answer" in text
    assert jav3.classify_net_error(
        httpx.ConnectError("[Errno 51] Network is unreachable"), BASE)[0] == "client"


def test_refused_is_the_servers():
    where, text = jav3.classify_net_error(
        httpx.ConnectError("All connection attempts failed"), BASE)
    assert where == "server"
    assert text == ("10.0.0.82 refuses connections on :8000 — the jav3 server is down "
                    "or restarting")


def test_timeouts_and_resets():
    assert "no answer from 10.0.0.82" in jav3.classify_net_error(
        httpx.ConnectTimeout("t"), BASE)[1]
    assert "no heartbeat from the server for 60 s" in jav3.classify_net_error(
        httpx.ReadTimeout("t"), BASE)[1]
    assert "closed the connection" in jav3.classify_net_error(
        httpx.RemoteProtocolError("p"), BASE)[1]


def test_statuses_and_server_messages():
    assert jav3.classify_status(502) == (
        "server", "the proxy answers but the jav3 server behind it doesn't (502)")
    assert jav3.classify_status(500, "boom") == ("server", "server error: boom")
    assert jav3.classify_message("model API 429: slow down") == (
        "provider", "the model provider failed: model API 429: slow down")
    assert jav3.classify_message("kaboom") == ("server", "server error: kaboom")


def test_the_udp_probe_asks_for_a_route_without_traffic(monkeypatch):
    import errno
    import socket as sk

    class S:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, addr):
            raise OSError(errno.ENETUNREACH, "Network is unreachable")

    monkeypatch.setattr(sk, "socket", S)
    assert REAL_PROBE("127.0.0.1", 8000) == "route"


# --- quiet turns ------------------------------------------------------------------------------

def test_quiet_states():
    t = jav3.TurnState()
    link = jav3.Link()
    assert t.quiet_line(link) is None
    t.last_event = time.monotonic() - 35
    assert t.quiet_line(link) == ("wait", "waiting for the model · 35s")
    t.last_event = time.monotonic() - 130
    link.beat()
    lvl, text = t.quiet_line(link)
    assert lvl == "warn" and text.startswith("! nothing for 2m 10s · server up (beat ")
    assert text.endswith("esc stops")
    t.pulse = {"running": True, "phase": "model", "phase_s": 130}
    assert "server: waiting on the model 2m 10s" in t.quiet_line(link)[1]
    t.pulse = {"running": True, "phase": "tool", "tool": "bash", "phase_s": 9}
    assert "server: running bash 9s" in t.quiet_line(link)[1]
    t.running["x"] = object()               # a running tool shows its own time
    assert t.quiet_line(link) is None


def test_link_goes_quiet_with_a_stream_open_and_live_on_the_next_byte():
    link = jav3.Link()
    link.beat()
    link.last_byte -= 50
    link.watch(0)
    assert link.state == "live"
    link.watch(1)
    assert link.state == "quiet"
    link.beat()
    assert link.state == "live"


# --- on screen --------------------------------------------------------------------------------

def notes(app) -> list[str]:
    return [str(n.render()) for n in app.query("Note")]


async def test_a_dead_stream_times_out_and_the_status_row_says_why():
    srv = FakeServer()
    app = jav3.build_tui(BASE, "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 30)) as pilot:
        await open_chat(pilot, app, srv)
        assert await wait_for(lambda: app.turn is not None and app.turn.cid == 4)
        whys = []
        real = app.link.lost
        app.link.lost = lambda w, why, n=1: (whys.append((w, why)), real(w, why, n))
        srv.down = True
        srv.feed.drop(httpx.ReadTimeout("no bytes"))
        assert await wait_for(lambda: app.reconnecting)
        assert whys[0] == ("server", "no heartbeat from the server for 60 s; the connection "
                                     "went dead")
        row = " ".join(p[0] for p in app._status_parts())
        assert row.startswith("✗ lost connection: ")
        assert "retrying (try" in row and "esc gives up" in row
        srv.back(4, "go", reply="done")
        assert await wait_for(lambda: not app.busy)


class PulseServer(FakeServer):
    pulse = {"running": False, "phase": None, "tool": None, "phase_s": None, "quiet_s": None}

    def handle(self, request):
        if request.url.path.endswith("/pulse"):
            return httpx.Response(200, json=self.pulse)
        return super().handle(request)


async def test_pulse_not_running_and_transcript_finished_settles_the_turn():
    srv = PulseServer()
    app = jav3.build_tui(BASE, "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 30)) as pilot:
        await open_chat(pilot, app, srv)
        assert await wait_for(lambda: app.turn is not None and app.turn.stream_open and app.turn.cid == 4)
        srv.conv_messages[4] = {"messages": [
            {"id": 1, "role": "user", "content": "go", "created_at": "t0"},
            {"id": 2, "role": "assistant", "content": "all done", "created_at": "t1",
             "model": "m", "activity": []}], "running": False, "pending_activity": [],
            "agent_slug": None}
        app.turn.last_event -= 60
        assert await wait_for(lambda: not app.busy, tries=120)
        assert any(w.source == "all done" for w in app.log_view.children
                   if type(w).__name__ == "Reply")


async def test_pulse_says_idle_but_the_transcript_still_runs_so_we_keep_waiting():
    srv = PulseServer()
    app = jav3.build_tui(BASE, "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 30)) as pilot:
        await open_chat(pilot, app, srv)
        assert await wait_for(lambda: app.turn is not None and app.turn.stream_open and app.turn.cid == 4)
        srv.conv_messages[4] = {"messages": [], "running": True, "pending_activity": [],
                                "agent_slug": None}
        app.turn.last_event -= 60
        await pilot.pause(1.0)
        assert app.busy and app.turn.pulse is None
        srv.feed.put({"type": "final", "content": "ok", "conversation_id": 4})
        srv.feed.close()
        assert await wait_for(lambda: not app.busy)

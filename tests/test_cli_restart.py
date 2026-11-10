"""/restart, and yes to "Restart cntrl now?" after /update: the client keeps what is open
in restart.json (0600), leaves the terminal and starts the file on disk again in the same
process; the new one puts back the tabs and tiles, each tile's chat (re-attached when its
turn still runs), draft, settings, attachments and page, the notifications and the /local
grants. A restart waits for what it would break: a message the server has not taken
yet, a command running on this computer."""
import json
import socket
import stat
import sys
import time

import pytest

from cli_fake import FakeServer, load_client, send, wait_for

jav3 = load_client("jav3cli_restart")
BASE = "http://h:1"


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))


def make(srv, **kw):
    return jav3.build_tui(BASE, "jvd_x", transport=srv.transport(), **kw)


async def started(pilot, app):
    assert await wait_for(lambda: app._layout_ready, tries=200)
    await pilot.pause(0.3)


async def test_a_restart_puts_back_tabs_tiles_drafts_settings_pages_and_notices():
    srv = FakeServer()
    srv.back(4, "first", "one")
    srv.back(5, "second", running=True)
    app = make(srv)
    async with app.run_test(size=(140, 40)) as pilot:
        await started(pilot, app)
        await app.open_conversation(4)
        app.chat.model = "deepseek/deepseek-flash"
        app.editor.text = "half a thought"
        app.editor.move_cursor((0, 4))
        app.dispatch("/new-panel")
        assert await wait_for(lambda: len(app.chats) == 2)
        await pilot.pause(0.2)
        c2 = app.chats[2]
        c2.project, c2.project_mode, c2.perm_mode = "p1", "pin", "ask"
        c2.editor.text = "unsent\nsecond line"
        c2.attachments.append("I ran `ls` on my machine")
        await pilot.press("ctrl+x", "t")
        assert await wait_for(lambda: len(app.ptabs) == 2 and app.chat.panel is not None)
        await pilot.pause(0.2)
        await app.open_conversation(5)                  # still running: attached
        assert await wait_for(lambda: app.chat.busy and len(srv.streams.get(5, [])) == 1)
        await app.open_page("agents")
        app.push_notice("agent #9 finished", ref=("cid", 9))
        app.push_notice(jav3.UPDATE_NOTICE, toast=False, ref=("update",))
        state = app._restart_state("update")
        jav3.save_restart_state(state)
    path = jav3._restart_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    kept = jav3.take_restart_state(BASE)
    assert kept == json.loads(json.dumps(state)) and not path.exists()
    assert jav3.take_restart_state(BASE) is None                    # once

    app2 = make(srv, restored=kept)
    async with app2.run_test(size=(140, 40)) as pilot:
        await started(pilot, app2)
        await pilot.pause(0.3)
        assert len(app2.ptabs) == 2 and app2.ptab_i == 1
        one, two = app2.ptabs[0].chats, app2.ptabs[1].chats
        assert sorted(one) == [1, 2] and app2.ptabs[0].focus_no == 2
        assert one[1].cid == 4 and one[1].model == "deepseek/deepseek-flash"
        assert one[1].editor.text == "half a thought" and one[1].editor.cursor_location == (0, 4)
        assert one[2].cid is None and (one[2].project, one[2].perm_mode) == ("p1", "ask")
        assert one[2].editor.text == "unsent\nsecond line"
        assert one[2].attachments == ["I ran `ls` on my machine"]
        assert two[1].cid == 5 and two[1].busy and len(srv.streams[5]) == 2   # re-attached
        assert type(two[1].panel.host.current).__name__ == "AgentsPage"
        assert app2.focused_chat is two[1]
        texts = [n[2] for n in app2.notices]
        assert "agent #9 finished" in texts and jav3.UPDATE_NOTICE not in texts
        mine = next(n for n in app2.notices if n[2] == "agent #9 finished")
        assert mine.ref == ("cid", 9) and mine.unread and app2.unread >= 1


async def test_restart_keeps_what_is_open_and_leaves_for_cmd_tui_to_start_it_again():
    srv = FakeServer()
    app = make(srv)
    async with app.run_test(size=(120, 40)) as pilot:
        await started(pilot, app)
        await send(pilot, app, "/restart")
        assert await wait_for(lambda: app.return_value == jav3.RESTART)
    state = jav3.take_restart_state(BASE)
    assert state["why"] == "restart" and state["tiles"] == [{"1": state["tiles"][0]["1"]}]


async def test_a_restart_waits_for_the_server_to_take_a_message_on_its_way():
    srv = FakeServer()
    app = make(srv)
    async with app.run_test(size=(120, 40)) as pilot:
        await started(pilot, app)
        await send(pilot, app, "hello")
        assert await wait_for(lambda: srv.posts)
        app.run_worker(app.restart(), group="cmd")
        await pilot.pause(0.5)
        assert app.return_value is None            # no `start` yet: no chat to come back to
        srv.feed.put({"type": "start", "conversation_id": 7})
        assert await wait_for(lambda: app.return_value == jav3.RESTART)
    state = jav3.take_restart_state(BASE)
    assert state["layout"]["tabs"][0]["cids"] == {"1": 7}


async def test_a_restart_waits_for_a_command_running_here_but_not_for_a_dialog():
    srv = FakeServer()
    app = make(srv)
    async with app.run_test(size=(120, 40)) as pilot:
        await started(pilot, app)
        app.local_calls = 1
        app.run_worker(app.restart(), group="cmd")
        await pilot.pause(0.5)
        assert app.return_value is None and not jav3._restart_path().exists()
        app.local_asking = 1       # it only waits on its approval: the server asks again
        assert await wait_for(lambda: app.return_value == jav3.RESTART)


async def test_no_restart_while_the_server_does_not_answer():
    srv = FakeServer()
    app = make(srv)
    async with app.run_test(size=(120, 40)) as pilot:
        await started(pilot, app)
        srv.down = True
        await send(pilot, app, "/restart")
        await pilot.pause(0.5)
        assert app.return_value is None and not jav3._restart_path().exists()
        assert any("not restarting" in str(n.render()) for n in app.query("Note"))


async def test_a_temporary_chat_is_said_to_start_empty_and_n_keeps_the_client():
    srv = FakeServer()
    app = make(srv)
    async with app.run_test(size=(120, 40)) as pilot:
        await started(pilot, app)
        app.chat.ephemeral, app.chat.last_user = True, "a secret question"
        await send(pilot, app, "/restart")
        assert await wait_for(lambda: type(app.screen).__name__ == "Confirm")
        assert "temporary chat starts empty again" in str(
            app.screen.query("Static")[1].render())
        await pilot.press("n")
        await pilot.pause(0.3)
        assert app.return_value is None and not jav3._restart_path().exists()


def test_a_kept_state_is_good_once_for_its_own_server_and_a_short_while():
    jav3.save_restart_state({"v": 1, "at": time.time(), "base": BASE})
    assert jav3.take_restart_state("http://other:1") is None
    assert not jav3._restart_path().exists()
    jav3.save_restart_state({"v": 1, "at": time.time() - 600, "base": BASE})
    assert jav3.take_restart_state(BASE) is None
    jav3._restart_path().write_text("{not json")
    assert jav3.take_restart_state(BASE) is None
    assert jav3.take_restart_state(BASE) is None              # nothing there at all


def test_the_restart_runs_the_file_on_disk_again_told_to_pick_up_the_state(tmp_path):
    seen = []
    p = tmp_path / "cntrl"
    jav3.restart_client("10.0.0.82:8000", path=p, execv=lambda exe, argv: seen.append((exe, argv)))
    assert seen == [(sys.executable, [sys.executable, str(p), "--restored",
                                      "--server", "10.0.0.82:8000"])]
    args = jav3.parse_args(["--restored", "--server", "10.0.0.82:8000"])
    assert args.restored and args.cmd is None and args.prompt == []
    assert not jav3.parse_args([]).restored

    def boom(*a):
        raise OSError("no such file")
    jav3.save_restart_state({"v": 1, "at": time.time(), "base": BASE})
    with pytest.raises(jav3.CliError, match="run cntrl"):
        jav3.restart_client(None, path=p, execv=boom)
    assert not jav3._restart_path().exists()


def test_local_grants_carry_over_for_the_same_directory_only(tmp_path):
    ex = jav3.LocalExecutor(tmp_path)
    ex.always = {"shell", "write"}
    ex.read_ok = [(tmp_path.parent.resolve(), True)]
    g = json.loads(json.dumps(ex.grants()))
    again = jav3.LocalExecutor(tmp_path)
    again.restore_grants(g)
    assert again.always == {"shell", "write"} and again.read_ok == ex.read_ok
    elsewhere = jav3.LocalExecutor(tmp_path / "other")
    elsewhere.restore_grants(g)
    assert elsewhere.always == set() and elsewhere.read_ok == []
    g["always"].append("everything")
    g["read_ok"].append(["relative/dir", True])
    junk = jav3.LocalExecutor(tmp_path)
    junk.restore_grants(g)
    assert junk.always == {"shell", "write"} and len(junk.read_ok) == 1


async def test_a_restarted_local_chat_keeps_its_always_answers(tmp_path):
    srv = FakeServer()
    srv.local_info = {"hostname": socket.gethostname(), "cwd": str(tmp_path)}
    ex = jav3.LocalExecutor(tmp_path)
    ex.always = {"shell"}
    state = {"v": 1, "at": time.time(), "base": BASE, "local": {"7": ex.grants()},
             "local_cids": [7]}
    app = make(srv, restored=state)
    asked = []
    async with app.run_test(size=(120, 40)) as pilot:
        await started(pilot, app)

        async def approve(kind, summary):
            asked.append(kind)
            return "no"
        app.local_approve = approve
        ok, out = await app._run_local(
            {"name": "local_shell", "args": {"command": "echo carried"}}, 7)
    assert ok and "carried" in out and asked == []
    assert 7 in app.local_cids


async def test_restarted_without_a_login_the_tiles_and_drafts_still_come_back():
    srv = FakeServer()
    app = jav3.build_tui(BASE, None, transport=srv.transport())        # not logged in
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.5)
        app.dispatch("/new-panel")
        assert await wait_for(lambda: len(app.chats) == 2)
        await pilot.pause(0.2)
        app.chats[1].editor.text = "left"
        app.chats[2].editor.text = "right"
        state = json.loads(json.dumps(app._restart_state("restart")))
    app2 = jav3.build_tui(BASE, None, transport=srv.transport(), restored=state)
    async with app2.run_test(size=(140, 40)) as pilot:
        assert await wait_for(lambda: app2._layout_ready, tries=200)
        await pilot.pause(0.3)
        assert [c.editor.text for _, c in sorted(app2.chats.items())] == ["left", "right"]

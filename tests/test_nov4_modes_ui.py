"""The four permission modes in the terminal client (nov4): safe-yolo is the default,
shift+tab never reaches yolo, /permissions yolo needs a yes and sends yolo-full, and a
server's old `yolo` shows as safe-yolo."""
import pytest

from cli_fake import FakeServer, load_client, open_chat, send, top, wait_for

jav3 = load_client("jav3cli_nov4_modes")


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_server_values_are_shown_by_their_new_names():
    app = jav3.build_tui("http://h:1", "jvd_x", transport=FakeServer().transport())
    assert app._perm_shown("yolo") == "safe-yolo"
    assert app._perm_shown("yolo-full") == "yolo"
    assert app._perm_shown("safe-yolo") == "safe-yolo"
    assert app._perm_shown("banana") is None
    assert app._perm_wire("yolo") == "yolo-full" and app._perm_wire("ask") == "ask"


async def test_shift_tab_never_reaches_yolo_from_any_mode():
    srv = FakeServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        assert app.perm_mode == "safe-yolo"
        for start in ("safe-yolo", "auto", "ask", "yolo"):
            app.perm_mode = start
            for _ in range(4):
                await pilot.press("shift+tab")
                assert app.perm_mode in ("safe-yolo", "auto", "ask")
        assert not any(p["mode"] == "yolo-full" for p in srv.perm_puts)


async def test_permissions_yolo_asks_first_and_sends_yolo_full_on_yes():
    srv = FakeServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(100, 30)) as pilot:
        await open_chat(pilot, app, srv)
        await send(pilot, app, "/permissions yolo")
        assert await wait_for(lambda: top(app) == "Confirm")
        assert "Nothing will ask" in app.screen.question
        await pilot.press("y")
        assert await wait_for(lambda: app.perm_mode == "yolo")
        assert await wait_for(lambda: srv.perm_puts == [{"mode": "yolo-full"}])
        assert "$error" in app._perm_label()


async def test_permissions_yolo_then_no_leaves_the_mode():
    srv = FakeServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(100, 30)) as pilot:
        await open_chat(pilot, app, srv)
        await send(pilot, app, "/permissions yolo")
        assert await wait_for(lambda: top(app) == "Confirm")
        await pilot.press("n")
        await pilot.pause(0.3)
        assert app.perm_mode == "safe-yolo" and srv.perm_puts == []


async def test_a_server_yolo_shows_as_safe_yolo():
    srv = FakeServer()                        # its GET answers the old "yolo"
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(100, 30)) as pilot:
        await open_chat(pilot, app, srv)
        app.perm_mode = "ask"
        await app._load_perm_mode(app.cid)
        assert app.perm_mode == "safe-yolo"


async def test_a_new_chat_sends_safe_yolo():
    srv = FakeServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(100, 30)) as pilot:
        await open_chat(pilot, app, srv)
        assert srv.posts[0]["permission_mode"] == "safe-yolo"


async def test_a_chosen_yolo_sends_yolo_full_with_the_first_message():
    srv = FakeServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        app.perm_mode = "yolo"
        await open_chat(pilot, app, srv)
        assert srv.posts[0]["permission_mode"] == "yolo-full"


async def test_enter_on_the_yolo_question_says_no():
    # the most permissive mode is never one reflexive keypress away: enter is no
    srv = FakeServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await send(pilot, app, "/permissions yolo")
        assert await wait_for(lambda: type(app.screen).__name__ == "Confirm")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app.chat.perm_mode == "safe-yolo"

"""Tabs and tiles in the terminal client. Tiles are the split engine (at most four a tab):
ctrl+t splits the focused one with a new chat, ctrl+w closes it (the last tile closes the tab,
the last tab becomes one new chat). Tabs sit on the top row: ctrl+x t / w / 1-9 / [ ] and a
click on a tab, its x or +. Each tab keeps its own tiles running, and the layout is kept in
tui.json (a layout saved before tabs loads as one tab). The first half is pure; the second
drives the Textual app. The client file is loaded from tests/cli_fake.py."""
import json

import pytest

from cli_fake import FakeServer, load_client, send, wait_for

jav3 = load_client("jav3cli_tabs")


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))


# -- the model (pure) ------------------------------------------------------------------------

def test_a_layout_round_trips_with_its_tabs_focus_zoom_and_chats():
    two = jav3.PanelSplit("row", [1, jav3.PanelSplit("col", [2, 3], [0.25, 0.75])], [0.4, 0.6])
    tabs = [{"tree": two, "focus": 3, "zoomed": True, "cids": {1: 7, 3: 9}},
            {"tree": 1, "focus": 1, "zoomed": False, "cids": {}}]
    raw = json.loads(json.dumps(jav3.layout_to_json(tabs, 1)))      # through a file
    back, current = jav3.layout_from_json(raw)
    assert current == 1 and len(back) == 2
    assert back[0]["tree"] == two and back[0]["tree"].sizes == pytest.approx([0.4, 0.6])
    assert back[0]["tree"].children[1].sizes == pytest.approx([0.25, 0.75])
    assert back[0]["focus"] == 3 and back[0]["zoomed"] and back[0]["cids"] == {1: 7, 3: 9}
    assert back[1]["tree"] == 1 and not back[1]["zoomed"]


def test_a_layout_saved_before_tabs_loads_as_one_tab():
    old = {"dir": "row", "children": [1, 2], "sizes": [0.5, 0.5]}
    back, current = jav3.layout_from_json(old)
    assert current == 0 and len(back) == 1
    assert back[0]["tree"] == jav3.PanelSplit("row", [1, 2]) and back[0]["focus"] == 1
    back, _ = jav3.layout_from_json(3)
    assert len(back) == 1 and back[0]["tree"] == 3


@pytest.mark.parametrize("junk", [None, "x", [], {}, {"tabs": []}, {"tabs": [5, None, "a"]},
                                  {"tabs": [{"tree": {"dir": "diag", "children": [1]}}]},
                                  {"tabs": [{"tree": {"dir": "row", "children": [1, 1]}}]},
                                  {"tabs": [{"tree": {"dir": "row", "children": [1, 2, 3, 4, 5]}}]}])
def test_a_layout_that_is_not_one_gives_nothing_and_never_raises(junk):
    assert jav3.layout_from_json(junk) == ([], 0)


def test_the_tab_bar_names_the_tabs_and_has_a_hit_for_each_part():
    markup, hits = jav3.tab_bar_layout(["chat", "/vms", "/agents"], 1, 80)
    assert "1 chat" in markup and "2 /vms" in markup and "3 /agents" in markup
    kinds = [(k, i) for _, _, k, i in hits]
    assert kinds == [("tab", 0), ("close", 0), ("tab", 1), ("close", 1), ("tab", 2),
                     ("close", 2), ("new", 3)]
    spans = [(a, b) for a, b, _, _ in hits]
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))   # no overlap
    assert all(b - a >= 1 for a, b in spans)


def test_a_crowded_tab_bar_shortens_names_then_scrolls_behind_arrows():
    names = ["a long chat title about something"] * 6
    markup, hits = jav3.tab_bar_layout(names, 0, 80)
    assert "…" in markup and not any(k in ("left", "right") for _, _, k, _ in hits)
    assert max(b for _, b, _, _ in hits) <= 80
    names = ["chat"] * 9
    markup, hits = jav3.tab_bar_layout(names, 7, 40)
    shown = [i for _, _, k, i in hits if k == "tab"]
    assert 7 in shown and len(shown) < 9
    assert any(k == "left" for _, _, k, _ in hits) and max(b for _, b, _, _ in hits) <= 40


# -- the app ---------------------------------------------------------------------------------

def make(srv=None, **kw):
    srv = srv or FakeServer()
    return srv, jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport(), **kw)


async def tiles(pilot, app, want):
    app.action_new_tile()
    assert await wait_for(lambda: len(app.chats) == want and app.panel_area.size.width)
    await pilot.pause(0.2)


async def settle(pilot, app, tabs, tiles_=None):
    assert await wait_for(lambda: len(app.ptabs) == tabs), (len(app.ptabs), tabs)
    if tiles_ is not None:
        assert await wait_for(lambda: len(app.chats) == tiles_), (len(app.chats), tiles_)
    await pilot.pause(0.2)


async def test_ctrl_t_splits_the_focused_tile_and_ctrl_w_closes_it():
    srv, app = make()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("ctrl+t")
        assert await wait_for(lambda: len(app.chats) == 2)
        await pilot.press("ctrl+t")
        assert await wait_for(lambda: len(app.chats) == 3)
        assert app.focus_no == 3 and len(app.ptabs) == 1
        app.focus_panel(2)
        await pilot.press("ctrl+w")
        assert await wait_for(lambda: sorted(app.chats) == [1, 3])
        assert app.focus_no in (1, 3) and len(app.ptabs) == 1
        # a fourth is the most a tab holds
        await pilot.press("ctrl+t")
        await pilot.press("ctrl+t")
        assert await wait_for(lambda: len(app.chats) == 4)
        await pilot.press("ctrl+t")
        await pilot.pause(0.3)
        assert len(app.chats) == 4


async def test_ctrl_w_in_the_prompt_closes_the_tile_instead_of_deleting_a_word():
    srv, app = make()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await tiles(pilot, app, 2)
        closing = app.editor
        closing.text = "hello brave world"
        closing.move_cursor(closing.document.end)
        await pilot.press("ctrl+w")
        assert await wait_for(lambda: len(app.chats) == 1)
        assert closing.text == "hello brave world"   # nothing was deleted from the prompt
        # the other way to delete a word still works, in the tile that is left once
        # its prompt has the keyboard
        assert await wait_for(lambda: app.editor is not closing and app.editor.has_focus)
        app.editor.text = "hello brave world"
        app.editor.move_cursor(app.editor.document.end)
        await pilot.press("alt+backspace")
        assert app.editor.text == "hello brave "


async def test_one_tab_shows_its_bar_when_there_is_room_and_ctrl_x_t_makes_a_second():
    srv, app = make()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.3)
        assert app.query_one("#tabbar").display          # 36 rows: the + is on screen
        await tiles(pilot, app, 2)
        await pilot.press("ctrl+x", "t")
        await settle(pilot, app, 2, 1)
        bar = app.query_one("#tabbar")
        assert bar.display and app.ptab_i == 1 and sorted(app.chats) == [1]
        assert len(app.ptabs[0].chats) == 2          # tab 1 kept its two tiles
        assert not app.ptabs[0].chats[1].panel.display
        assert app.focused_chat.panel.display
        text = str(bar.render())
        assert "1 chat" in text and "2 chat" in text and "+" in text


async def test_ctrl_x_brackets_digits_and_w_switch_and_close_tabs():
    srv, app = make()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("ctrl+x", "t")
        await settle(pilot, app, 2)
        await pilot.press("ctrl+x", "t")
        await settle(pilot, app, 3)
        assert app.ptab_i == 2
        await pilot.press("ctrl+x", "[")
        assert app.ptab_i == 1
        await pilot.press("ctrl+x", "]")
        await pilot.press("ctrl+x", "]")                       # past the last: round to the first
        assert app.ptab_i == 0
        await pilot.press("ctrl+x", "3")
        assert app.ptab_i == 2
        await pilot.press("ctrl+x", "1")
        assert app.ptab_i == 0 and app.focused_chat.panel.display
        assert not app.ptabs[2].chats[1].panel.display
        await pilot.press("ctrl+x", "9")                       # no tab 9: stays
        assert app.ptab_i == 0
        gone = app.ptabs[1]
        await pilot.press("ctrl+x", "2")
        await pilot.press("ctrl+x", "w")
        await settle(pilot, app, 2)
        assert gone not in app.ptabs and app.ptab_i == 0 and not gone.chats[1].panel.is_attached
        # ctrl+x t and w are tabs now: themes and web are slash-only
        assert not app.commands["themes"].leader and not app.commands["web"].leader


async def test_ctrl_w_on_the_last_tile_closes_the_tab_and_the_last_tab_becomes_a_new_chat():
    srv, app = make()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("ctrl+x", "t")
        await settle(pilot, app, 2)
        await pilot.press("ctrl+w")                            # one tile in tab 2: the tab goes
        await settle(pilot, app, 1)
        assert len(app.ptabs) == 1
        first = app.chats[1]
        await pilot.press("ctrl+w")                            # the last tab: a new chat instead
        assert await wait_for(lambda: app.chats[1] is not first)
        assert len(app.ptabs) == 1 and app.chats[1].panel.is_attached
        assert not first.panel.is_attached


async def test_a_chat_in_tab_one_keeps_streaming_while_tab_two_shows():
    srv, app = make()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        await send(pilot, app, "a long job")
        assert await wait_for(lambda: len(srv.feeds) == 1)
        srv.feeds[0].put({"type": "start", "conversation_id": 4},
                         {"type": "token", "text": "working "})
        assert await wait_for(lambda: app.chats[1].cid == 4)
        chat = app.chats[1]
        await pilot.press("ctrl+x", "t")
        await settle(pilot, app, 2)
        assert app.chats[1] is not chat and chat.busy
        srv.feeds[0].put({"type": "token", "text": "and still going"})
        assert await wait_for(lambda: "still going" in
                              " ".join(r.source for r in chat.log.query("Reply")))
        assert not chat.panel.display and chat.busy
        # a dialog or the stop-all route still reaches it, and it is back when the tab is
        await pilot.press("ctrl+x", "1")
        assert app.focused_chat is chat and chat.panel.display
        srv.feeds[0].put({"type": "final", "content": "done", "conversation_id": 4})
        srv.feeds[0].close()
        assert await wait_for(lambda: not chat.busy)


async def test_a_click_on_a_tab_its_x_and_plus_switch_close_and_open():
    srv, app = make()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("ctrl+x", "t")
        await settle(pilot, app, 2)
        bar = app.query_one("#tabbar")
        at = {(k, i): (a + b) // 2 for a, b, k, i in bar.hits}
        await pilot.click("#tabbar", offset=(at[("tab", 0)], 0))
        await pilot.pause(0.2)
        assert app.ptab_i == 0
        await pilot.click("#tabbar", offset=(at[("new", 2)], 0))
        await settle(pilot, app, 3)
        assert app.ptab_i == 2
        bar = app.query_one("#tabbar")
        at = {(k, i): (a + b) // 2 for a, b, k, i in bar.hits}
        await pilot.click("#tabbar", offset=(at[("close", 1)], 0))
        await settle(pilot, app, 2)
        assert app.ptab_i == 1                                  # the one showing stays showing


async def test_the_layout_is_kept_in_tui_json_and_comes_back_with_its_chats():
    srv, app = make()
    srv.back(4, "first", "one")
    srv.back(5, "second", "two")
    cfg_file = None
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.5)
        await app.open_conversation(4)
        await tiles(pilot, app, 2)
        await app.open_conversation(5)
        await pilot.press("ctrl+x", "t")
        await settle(pilot, app, 2)
        app.focus_panel(1)
        await pilot.press("ctrl+x", "1")
        app.focus_panel(2)
        assert await wait_for(lambda: app._layout_ready)
        app.refresh_tabbar()
        cfg_file = app._settings_path()
        saved = json.loads(cfg_file.read_text())["layout"]
    assert saved["current"] == 0 and len(saved["tabs"]) == 2
    assert saved["tabs"][0]["cids"] == {"1": 4, "2": 5} and saved["tabs"][0]["focus"] == 2
    srv2, app2 = make(srv)
    async with app2.run_test(size=(140, 40)) as pilot:
        assert await wait_for(lambda: app2._layout_ready, tries=200)
        assert len(app2.ptabs) == 2
        await pilot.pause(0.5)
        assert app2.ptab_i == 0 and sorted(app2.chats) == [1, 2] and app2.focus_no == 2
        assert app2.chats[1].cid == 4 and app2.chats[2].cid == 5
        assert len(app2.ptabs[1].chats) == 1 and app2.ptabs[1].chats[1].cid is None


async def test_a_short_terminal_shows_a_lone_tabs_bar_only_with_a_second_tab():
    srv, app = make()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        assert not app.query_one("#tabbar").display      # 24 rows: every row counts
        await pilot.press("ctrl+x", "t")
        await settle(pilot, app, 2)
        assert app.query_one("#tabbar").display


async def test_the_clock_never_trips_on_a_tile_that_is_still_mounting():
    # on the Pi a saved 2-tab layout crashed the client at start: the tab being mounted
    # was already the focused one, and the status row asked its panel-less tile for its page
    srv, app = make()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.3)
        app.ptabs.append(jav3.PanelTab({1: jav3.ChatState(1)}))
        app.ptab_i = len(app.ptabs) - 1                 # as if shown before it mounted
        app._tick()                                     # must not raise
        assert app.page is None
        app.ptabs.pop()
        app.ptab_i = 0
        n = 0
        real = app._tick

        def counting():
            nonlocal n
            n += 1
            real()
        app._tick = counting
        await pilot.press("ctrl+x", "t")                # a new tab mounts under the clock
        assert await wait_for(lambda: len(app.ptabs) == 2 and app.ptab_i == 1)
        assert app.focused_chat.panel is not None and app.page is not None

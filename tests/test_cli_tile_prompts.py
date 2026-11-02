"""Every tile has its own prompt: its own draft, history position and popup, its own meta
row, and a send, a failed send and a setting change stay in the tile they belong to. A
new tile or tab copies the settings once, then they are separate."""
import pytest

from cli_fake import FakeServer, load_client, send, wait_for

jav3 = load_client("jav3cli_tile_prompts")


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))


def make(srv=None, **kw):
    srv = srv or FakeServer()
    return srv, jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport(), **kw)


async def split(pilot, app, line="/new-panel", want=2):
    app.dispatch(line)
    assert await wait_for(lambda: len(app.chats) == want and app.panel_area.size.width), line
    await pilot.pause(0.2)


async def test_each_tile_keeps_its_own_draft():
    srv, app = make()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await split(pilot, app)
        e1, e2 = app.chats[1].editor, app.chats[2].editor
        assert e1 is not e2 and app.editor is e2 and e2.has_focus
        e2.text = "second"
        app.focus_panel(1)
        await pilot.pause(0.1)
        assert app.editor is e1 and e1.has_focus and e1.text == ""
        e1.text = "first"
        app.focus_panel(2)
        await pilot.pause(0.1)
        assert (e1.text, e2.text) == ("first", "second") and e2.has_focus


async def test_enter_in_tile_two_posts_to_tile_two_only():
    srv, app = make()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await split(pilot, app)
        app.chats[1].editor.text = "draft one"
        await send(pilot, app, "hello from two")
        assert await wait_for(lambda: len(srv.posts) >= 1)
        assert srv.posts[0]["message"] == "hello from two"
        assert app.chats[1].editor.text == "draft one" and app.chats[2].editor.text == ""
        assert app.chats[1].cid is None


async def test_a_setting_in_one_tile_does_not_change_the_other():
    srv, app = make()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await split(pilot, app)
        m1, p1 = app.chats[1].model, app.chats[1].perm_mode
        app.chats[2].model = "some/model"
        app.cycle_permission()                      # tile 2 has the focus
        assert app.chats[2].perm_mode != p1
        assert app.chats[1].model == m1 and app.chats[1].perm_mode == p1


async def test_a_new_tile_and_a_new_tab_copy_once_and_start_empty():
    srv, app = make(project="p1", model="m/one")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.chat.perm_mode = "ask"
        app.editor.text = "unsent"
        app.chat.attachments.append("x")
        await split(pilot, app)
        c2 = app.chats[2]
        assert (c2.project, c2.model, c2.perm_mode) == ("p1", "m/one", "ask")
        assert c2.editor.text == "" and c2.attachments == []
        c2.perm_mode = "yolo"
        assert app.chats[1].perm_mode == "ask" and app.chats[1].editor.text == "unsent"
        chat = await app.new_tab()
        assert chat.editor.text == "" and chat.attachments == [] and chat.model == "m/one"
        assert chat.perm_mode == "yolo"             # copied from the tile that was focused
        chat.perm_mode = "ask"
        assert c2.perm_mode == "yolo"


async def test_a_failed_send_lands_back_in_its_own_tile():
    srv, app = make()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await split(pilot, app)
        srv.down = True
        await send(pilot, app, "lost words")
        app.focus_panel(1)
        assert await wait_for(lambda: "lost words" in app.chats[2].editor.text)
        assert app.chats[1].editor.text == ""


async def test_clicking_an_unfocused_prompt_focuses_its_tile():
    srv, app = make()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await split(pilot, app)
        assert app.focus_no == 2
        e1 = app.chats[1].editor
        await pilot.click(e1)
        await pilot.pause(0.1)
        assert app.focus_no == 1 and e1.has_focus
        assert app.chats[2].prompt_box.has_class("-idle")
        assert not app.chats[1].prompt_box.has_class("-idle")


async def test_a_page_in_one_tile_leaves_the_others_meta_alone():
    srv, app = make()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await split(pilot, app)
        app.dispatch("/security")
        assert await wait_for(lambda: app.chats[2].prompt_block.has_class("paged"))
        await pilot.pause(0.2)
        assert not app.chats[2].meta_row.display
        assert app.chats[1].meta_row.display
        assert not app.chats[1].prompt_block.has_class("paged")

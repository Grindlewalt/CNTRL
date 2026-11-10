"""/pull and /helper (nov1): the commands that bring a project's code here, and a turn
that carries CNTRL's own manual."""
import httpx
import pytest

from cli_fake import FakeServer, finish, load_client, send, wait_for

jav3 = load_client("jav3cli_helper")

PULL = {"project": "demo", "clone": "git clone http://h:1/git/demo",
        "pull": "cd demo && git pull", "auth": "git asks for your CNTRL username and password",
        "uncommitted": 2, "last_commit": "a1b2c3d add the loop",
        "note": "only committed work is served"}


class HelpServer(FakeServer):
    def __init__(self, pull=PULL, status=200) -> None:
        super().__init__()
        self.pull, self.status, self.pull_params = pull, status, None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/helper/pull":
            self.pull_params = dict(request.url.params)
            return httpx.Response(self.status, json=self.pull if self.status == 200
                                  else {"detail": "no"})
        return super().handle(request)


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def copied(monkeypatch):
    got = []
    monkeypatch.setattr(jav3, "copy_to_clipboard", lambda text, osc52=None: got.append(text) or "test")
    return got


def notes(app) -> str:
    return "\n".join(str(w.render()) for w in app.query("Note"))


async def test_pull_prints_the_commands_and_copies_the_clone(copied):
    srv = HelpServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.focused_chat.project = "demo"
        await send(pilot, app, "/pull")
        assert await wait_for(lambda: "git clone" in notes(app))
        out = notes(app)
        assert "git clone http://h:1/git/demo" in out and "cd demo && git pull" in out
        assert "2 files not committed yet" in out and "a1b2c3d" in out
        assert srv.pull_params == {"project": "demo", "base": "http://h:1"}
        assert copied == ["git clone http://h:1/git/demo"]


async def test_pull_without_the_endpoint_builds_the_commands_from_the_address(copied):
    srv = HelpServer(status=403)          # a chat-only login, or an older server (404)
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await send(pilot, app, "/pull other")
        assert await wait_for(lambda: "git clone" in notes(app))
        assert "git clone http://h:1/git/other" in notes(app)
        assert "not committed" not in notes(app)
        assert copied == ["git clone http://h:1/git/other"]


async def test_pull_in_a_checkout_copies_the_pull(copied, tmp_path):
    (tmp_path / "demo" / ".git").mkdir(parents=True)
    srv = HelpServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await send(pilot, app, "/pull demo")
        assert await wait_for(lambda: "git clone" in notes(app))
        assert copied == ["cd demo && git pull"]


async def test_pull_says_when_there_is_no_project(copied):
    srv = HelpServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.focused_chat.project_mode = "none"
        await send(pilot, app, "/pull")
        assert await wait_for(lambda: "no project" in notes(app))
        assert copied == []


async def test_helper_marks_only_the_next_turn():
    srv = HelpServer()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await send(pilot, app, "/helper how do I get my code?")
        assert await wait_for(lambda: srv.posts)
        assert srv.posts[0]["helper"] is True
        assert srv.posts[0]["message"] == "how do I get my code?"
        srv.feed.put({"type": "start", "conversation_id": 4})
        await finish(srv, app)
        await send(pilot, app, "thanks")
        assert await wait_for(lambda: len(srv.posts) == 2)
        assert "helper" not in srv.posts[1]
        srv.feed.put({"type": "start", "conversation_id": 4})
        await finish(srv, app)


async def test_a_turn_that_wrote_files_points_at_pull():
    app = jav3.build_tui("http://h:1", "jvd_x", transport=HelpServer().transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.focused_chat.project = "demo"
        turn = jav3.TurnState()
        turn.done = True
        turn.tools = [{"name": "write_file", "args": {}, "ok": True, "result": ""}]
        assert "/pull" in app._footer_markup(turn)
        turn.tools = [{"name": "read_file", "args": {}, "ok": True, "result": ""}]
        assert "/pull" not in app._footer_markup(turn)


async def test_transcript_copy_puts_the_whole_chat_on_the_clipboard(copied, tmp_path):
    # 2026-11-03: /copy has only the last reply; /transcript copy is the whole chat as
    # /export writes it, and /transcript save is /export
    srv = HelpServer()
    srv.conv_messages[7] = {"messages": [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer",
         "activity": [{"name": "read_file", "args": {"path": "a.py"}, "ok": True}]},
        {"role": "system", "content": "never copied"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"}]}
    app = jav3.build_tui("http://h:1", "jvd_x", transport=srv.transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        with pytest.raises(jav3.CliError, match="no chat"):
            await app.c_transcript("copy")
        app.focused_chat.cid = 7
        await send(pilot, app, "/transcript copy")
        assert await wait_for(lambda: copied)
        text = copied[0]
        for part in ("# CNTRL chat #7", "first question", "first answer", "a.py",
                     "second question", "second answer"):
            assert part in text, part
        assert "never copied" not in text
        assert text == jav3.transcript_markdown("CNTRL chat #7", [
            m for m in srv.conv_messages[7]["messages"] if m["role"] != "system"])
        await app.c_transcript("")                             # bare: copy too
        assert len(copied) == 2 and copied[1] == text
        await app.c_transcript("save")
        assert "second answer" in (tmp_path / "jav3-7.md").read_text()
        with pytest.raises(jav3.CliError, match="/transcript copy"):
            await app.c_transcript("print")
        app.editor.text = "/transcript "
        await pilot.pause(0.1)
        assert [v for _, v in app.popup_items] == ["copy", "save"]

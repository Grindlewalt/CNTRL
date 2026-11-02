"""nov2 in the terminal client: /pull prints the PR's lines, agent rows show the branch,
and a merge request has a queue payload reader."""
import json

import httpx
import pytest

from cli_fake import FakeServer, load_client, send, wait_for

jav3 = load_client("jav3cli_merge")

PULL = {"project": "demo", "clone": "git clone http://h:1/git/demo",
        "pull": "cd demo && git pull", "uncommitted": 0, "last_commit": "a1b2c3d x",
        "note": "",
        "pr": {"branch": "agent/orch-7f3a2c", "request_id": 4,
               "clone": "git clone -b agent/orch-7f3a2c http://h:1/git/demo demo-pr7f3a2c",
               "fetch": "git fetch origin agent/orch-7f3a2c && git switch agent/orch-7f3a2c"}}


class PullServer(FakeServer):
    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/helper/pull":
            return httpx.Response(200, json=PULL)
        return super().handle(request)


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def notes(app) -> str:
    return "\n".join(str(w.render()) for w in app.query("Note"))


async def test_pull_prints_the_pr_lines_and_copies_the_pr_clone(monkeypatch):
    got = []
    monkeypatch.setattr(jav3, "copy_to_clipboard",
                        lambda text, osc52=None: got.append(text) or "test")
    app = jav3.build_tui("http://h:1", "jvd_x", transport=PullServer().transport())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.focused_chat.project = "demo"
        await send(pilot, app, "/pull")
        assert await wait_for(lambda: "a PR waits for you" in notes(app))
        out = notes(app)
        assert "git clone -b agent/orch-7f3a2c" in out and "request #4" in out
        assert got == [PULL["pr"]["clone"]]


def test_branch_meta_on_agent_rows():
    assert jav3._branch_meta({"branch": "agent/7f3a2c-i1", "work_state": "done",
                              "files": 4}) == ["agent/7f3a2c-i1", "✓ done · 4 files"]
    assert jav3._branch_meta({"pr": {"state": "filed", "files": 11}}) == ["PR filed · 11 files"]
    assert jav3._branch_meta({"title": "plain"}) == []


def test_merge_payload_reads_the_json_text():
    assert jav3._merge_payload({"payload": json.dumps({"branch": "b"})}) == {"branch": "b"}
    assert jav3._merge_payload({"payload": "{broken"}) == {}
    assert jav3._merge_payload({}) == {}

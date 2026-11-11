"""gitea_issue (tools/gitea_issue, backend/gitea.open_issue): an issue on the
current project's repo on the host's Gitea, opened as the bot, recorded as a
security event, gated like the other write-type calls, never with a token or a
secret value in it."""
import json
import re

import httpx

from backend import gitea, permissions
from backend.agent.tools import registry
from backend.config import settings
from backend.db import get_db
from tests import test_gitea as tg

ADMIN_TOK, BOT_TOK = tg.ADMIN_TOK, tg.BOT_TOK
client, fake = tg.client, tg.fake          # the fixtures: a logged-in app, a fake Gitea


def _issues(monkeypatch, fake):
    """Fake Gitea plus the issues endpoint; returns the issues it was sent."""
    got = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/v1")
        if req.method == "POST" and re.fullmatch(r"/repos/operator/[^/]+/issues", path):
            tok = req.headers.get("authorization", "").removeprefix("token ")
            got.append({"token": tok, **json.loads(req.content)})
            return httpx.Response(201, json={"number": len(got)})
        return fake(req)
    monkeypatch.setattr(gitea, "_transport", httpx.MockTransport(handler))
    return got


async def _events(kind):
    db = await get_db()
    try:
        async with db.execute("SELECT summary, detail, project_slug FROM security_events "
                              "WHERE kind = ?", (kind,)) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def test_opens_an_issue_as_the_bot(client, fake, monkeypatch):
    sent = _issues(monkeypatch, fake)
    out = await registry.dispatch("gitea_issue", {"title": "Undo says nothing\nsecond line",
                                                  "body": "Ran /undo; no message."})
    assert out.startswith("issue #1 opened on demo's Gitea repo")
    assert "http://gitbox.lan:3000/operator/demo/issues/1" in out
    assert len(sent) == 1 and sent[0]["token"] == BOT_TOK         # the bot, never the operator
    assert sent[0]["title"] == "Undo says nothing"                 # one line
    assert sent[0]["body"].startswith("Ran /undo; no message.\n\n---\nOpened by the CNTRL agent")
    assert "demo" in fake.repos                                    # the repo is made if missing
    ev = await _events("gitea_issue_opened")
    assert len(ev) == 1 and ev[0]["project_slug"] == "demo"
    assert json.loads(ev[0]["detail"])["number"] == 1
    for t in (out, json.dumps(ev)):
        assert ADMIN_TOK not in t and BOT_TOK not in t


async def test_a_tainted_turn_says_so_in_the_issue(client, fake, monkeypatch):
    from backend.vm import broker
    sent = _issues(monkeypatch, fake)
    monkeypatch.setattr(broker, "op_tainted", lambda op: True)
    await registry.dispatch("gitea_issue", {"title": "t"})
    assert "read outside content" in sent[0]["body"]


async def test_a_secret_value_is_refused(client, fake, monkeypatch):
    from backend import secrets as secrets_mod
    sent = _issues(monkeypatch, fake)
    monkeypatch.setattr(secrets_mod, "load", lambda: {"OPENAI_KEY": "sk-live-123456789"})
    out = await registry.dispatch("gitea_issue", {"title": "key", "body": "it is sk-live-123456789"})
    assert out.startswith("error:") and "OPENAI_KEY" in out and "sk-live" not in out
    assert sent == [] and await _events("gitea_issue_opened") == []


async def test_bad_input_files_nothing(client, fake, monkeypatch):
    sent = _issues(monkeypatch, fake)
    assert (await registry.dispatch("gitea_issue", {"title": "  "})).startswith("error:")
    long = await registry.dispatch("gitea_issue", {"title": "t", "body": "x" * 20_001})
    assert "report file" in long and sent == []


async def test_without_gitea_nothing_is_filed(client, monkeypatch):
    monkeypatch.setattr(settings, "gitea_enabled", False)
    out = await registry.dispatch("gitea_issue", {"title": "t"})
    assert out.startswith("error: Gitea isn't set up") and "report file" in out


def test_offered_only_with_gitea_and_gated_like_a_write(monkeypatch):
    e = next(e for e in registry.load_registry() if e["name"] == "gitea_issue")
    assert e["section"] == "git" and e["action"] == "issue"
    monkeypatch.setattr(settings, "gitea_enabled", False)
    assert registry._requirements_met(e) is False
    monkeypatch.setattr(settings, "gitea_enabled", True)
    assert registry._requirements_met(e) is True
    # ask mode asks, auto mode judges; there is no "always allow" for it
    assert "gitea_issue" in permissions.GATED_TOOLS
    assert permissions.rule_prefix("gitea_issue", {"title": "t"}) is None

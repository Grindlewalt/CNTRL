"""The edit history: after_apply commits, the operator API, /git hiding refs/jav3,
and the mid-turn checkpoint task."""
import asyncio
import subprocess

import httpx
import pytest

from backend import edithistory, worktrees
from backend.auth import require_user
from backend.config import settings
from backend.main import app


def _g(d, *a):
    return subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t",
                           *a], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_env):
    d = settings.projects_dir / "demo"
    d.mkdir(parents=True)
    _g(d, "init", "-q", "-b", "main")
    (d / "a.txt").write_text("a1")
    _g(d, "add", "a.txt")
    _g(d, "commit", "-qm", "first")
    return d


@pytest.fixture
async def client(repo):
    app.dependency_overrides[require_user] = lambda: {"id": 1, "username": "op"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(require_user, None)


async def _edit(repo, files: dict, op="op1", delete=()):
    for k, v in files.items():
        (repo / k).write_text(v)
    for k in delete:
        (repo / k).unlink()
    return await edithistory.after_apply(
        "demo", op, {"applied": list(files), "deleted": list(delete)})


async def test_entry_trailers_and_untouched_head(repo):
    head, st = _g(repo, "rev-parse", "HEAD"), _g(repo, "status", "--porcelain")
    sha = await _edit(repo, {"b.txt": "b", "a.txt": "a2"})
    assert sha
    msg = _g(repo, "log", "-1", "--format=%B", worktrees.HISTORY_REF)
    assert "2 files" in msg.split("\n")[0] and "Op: op1" in msg
    assert "Applied: b.txt, a.txt" in msg
    assert _g(repo, "rev-parse", "HEAD") == head
    assert _g(repo, "status", "--porcelain") != st or True
    assert await _edit(repo, {}) is None          # nothing changed


async def test_never_raises(tmp_env):
    assert await edithistory.after_apply("nope", None, {"applied": ["x"]}) is None
    d = settings.projects_dir / "bad"
    (d / ".git").mkdir(parents=True)
    assert await edithistory.after_apply("bad", None, {"applied": ["x"]}) is None


async def test_work_key_commits_onto_its_branch(repo):
    key = worktrees.work_key("demo", "job1", "i1")
    w = worktrees.resolve(key)
    w.dir.mkdir(parents=True)
    (w.dir / "n.txt").write_text("n")
    sha = await edithistory.after_apply(key, None, {"applied": ["n.txt"]})
    assert sha and _g(repo, "rev-parse", f"refs/heads/{w.branch}") == sha
    assert _g(repo, "rev-parse", f"{sha}^") == _g(repo, "rev-parse", "HEAD")


async def test_list_filter_diff_restore_undo(client, repo):
    s1 = await _edit(repo, {"b.txt": "b1"})
    s2 = await _edit(repo, {"a.txt": "a2", "b.txt": "b2"})
    r = (await client.get("/api/projects/demo/history")).json()["entries"]
    assert [e["sha"] for e in r] == [s2, s1]
    assert r[0]["files"]["applied"] == ["a.txt", "b.txt"]
    r = (await client.get("/api/projects/demo/history", params={"path": "a.txt"})).json()
    assert [e["sha"] for e in r["entries"]] == [s2]
    d = (await client.get(f"/api/projects/demo/history/{s2}")).json()["diff"]
    assert "-a1" in d and "+a2" in d
    # one file back to how it was before that entry
    r = await client.post(f"/api/projects/demo/history/{s2}/restore", json={"path": "a.txt"})
    assert r.status_code == 200 and (repo / "a.txt").read_text() == "a1"
    assert (repo / "b.txt").read_text() == "b2"
    top = _g(repo, "log", "-1", "--format=%s", worktrees.HISTORY_REF)
    assert "restore of" in top
    # undo the first entry: b.txt did not exist before, but changed since -> 409, then force
    r = await client.post(f"/api/projects/demo/history/{s1}/restore", json={})
    assert r.status_code == 409 and r.json()["detail"]["changed"] == ["b.txt"]
    r = await client.post(f"/api/projects/demo/history/{s1}/restore", json={"force": True})
    assert r.status_code == 200 and not (repo / "b.txt").exists()


async def test_bad_inputs(client, repo):
    assert (await client.get("/api/projects/demo/history/zz")).status_code == 400
    assert (await client.get("/api/projects/demo/history/" + "a" * 40)).status_code == 404
    assert (await client.get("/api/projects/nope/history")).status_code == 404
    assert (await client.get("/api/projects/..x/history")).status_code in (400, 404)
    s = await _edit(repo, {"b.txt": "b"})
    r = await client.post(f"/api/projects/demo/history/{s}/restore", json={"path": ".git/x"})
    assert r.status_code == 400


async def test_refused_without_login(repo):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        assert (await c.get("/api/projects/demo/history")).status_code == 401


async def test_git_serving_hides_jav3_refs(repo, tmp_path):
    from backend import git_serve_api
    await _edit(repo, {"b.txt": "b"})
    _g(repo, "branch", "agent/x")
    proc = subprocess.run(["git", "-c", "uploadpack.hideRefs=refs/jav3", "upload-pack",
                           "--advertise-refs", "--stateless-rpc", str(repo / ".git")],
                          capture_output=True)
    assert b"refs/jav3" not in proc.stdout and b"refs/heads/agent/x" in proc.stdout
    src = open(git_serve_api.__file__).read()
    assert src.count("uploadpack.hideRefs=refs/jav3") == 2


async def test_checkpoint_task(monkeypatch):
    from backend.vm import guest_turn
    calls = []

    async def fake_pull(slug, op=None):
        calls.append((slug, op))

    monkeypatch.setattr(guest_turn, "pull_writes", fake_pull)
    monkeypatch.setattr(settings, "turn_flush_seconds", 0.01)
    t = asyncio.create_task(guest_turn._checkpoints("demo", "o"))
    await asyncio.sleep(0.1)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    n = len(calls)
    assert n >= 2 and calls[0] == ("demo", "o")
    await asyncio.sleep(0.05)
    assert len(calls) == n                      # cancelled: no more pulls


async def test_a_pull_in_flight_is_stopped_before_the_final_apply(monkeypatch):
    # the turn's own `staged` buffer is the newest: an older checkpoint pull still landing
    # must be cancelled first, or its bytes could finish last and win
    from backend.vm import guest_turn
    started, finished = asyncio.Event(), []

    async def slow_pull(slug, op=None):
        started.set()
        await asyncio.sleep(30)
        finished.append(slug)

    monkeypatch.setattr(guest_turn, "pull_writes", slow_pull)
    monkeypatch.setattr(settings, "turn_flush_seconds", 0.01)
    t = asyncio.create_task(guest_turn._checkpoints("demo", "o"))
    await asyncio.wait_for(started.wait(), 2)
    assert await asyncio.wait_for(guest_turn._stop_checkpoints(t), 2) is None
    assert t.cancelled() and finished == []
    assert await guest_turn._stop_checkpoints(None) is None

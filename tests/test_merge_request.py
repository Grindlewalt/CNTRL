"""Orchestrator PR (nov2): kind 'merge' requests on real temp repos."""
import json
import subprocess

import pytest

from backend import gitgate, worktrees
from backend.config import settings
from backend.db import get_db, init_db

JOB = "7f3a2c"
BRANCH = f"agent/orch-{JOB}"
ITEM = f"agent/{JOB}-i1"


def _g(d, *a):
    return subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t",
                           *a], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
async def proj(tmp_env):
    await init_db()
    d = settings.projects_dir / "demo"
    d.mkdir(parents=True)
    (d / "project.md").write_text("demo")
    _g(d, "init", "-q", "-b", "main")
    (d / "a.txt").write_text("a\n")
    (d / "b.txt").write_text("b\n")
    _g(d, "add", "-A")
    _g(d, "commit", "-qm", "first")
    db = await get_db()
    try:
        await db.execute("INSERT INTO projects (slug, name) VALUES ('demo', 'Demo')")
        await db.commit()
    except Exception:                                   # noqa: BLE001
        pass
    finally:
        await db.close()
    return d


def _branch(d, base, files, deletes=(), name=BRANCH):
    """Commit `files` on `name` starting at `base`, without touching the dir."""
    _g(d, "branch", "-f", name, base)
    wt = d.parent / "wt-tmp"
    _g(d, "worktree", "add", "-q", "--force", str(wt), name)
    for k, v in files.items():
        (wt / k).parent.mkdir(parents=True, exist_ok=True)
        (wt / k).write_text(v)
    for k in deletes:
        (wt / k).unlink()
    _g(wt, "add", "-A")
    _g(wt, "commit", "-qm", "work")
    _g(d, "worktree", "remove", "--force", str(wt))
    return _g(d, "rev-parse", name)


async def _file(d, base, job=JOB, **kw):
    return await gitgate.create_merge_request(
        "demo", job_id=job, branch=kw.get("branch", BRANCH), base_sha=base,
        parts=[{"item": 1, "title": "physics", "branch": ITEM, "sha": base, "files": 2,
                "state": "done"}], summary="Add c, change a\n\ndetails")


@pytest.fixture
def removed(monkeypatch):
    calls = []

    async def rm(key, **kw):
        calls.append(key)
        raise NotImplementedError          # B's stub: approval must survive it
    monkeypatch.setattr(worktrees, "remove", rm)
    return calls


@pytest.fixture
def hist(monkeypatch):
    calls = []

    async def aa(key, op, res):
        calls.append((key, res))
    from backend import edithistory
    monkeypatch.setattr(edithistory, "after_apply", aa)
    return calls


async def test_create_validates_and_one_per_job(proj):
    base = _g(proj, "rev-parse", "HEAD")
    _branch(proj, base, {"c.txt": "c\n"})
    with pytest.raises(ValueError):
        await gitgate.create_merge_request("demo", job_id="1", branch="agent/x", base_sha=base,
                                           parts=[], summary="s")
    with pytest.raises(ValueError):
        await gitgate.create_merge_request("demo", job_id="1", branch=BRANCH, base_sha="zz",
                                           parts=[], summary="s")
    row = await _file(proj, base)
    assert row["kind"] == "merge" and row["status"] == "pending"
    assert json.loads(row["payload"])["parts"][0]["title"] == "physics"
    with pytest.raises(ValueError):
        await _file(proj, base)


async def test_approve_clean_with_dirty_base(proj, removed, hist):
    (proj / "a.txt").write_text("dirty\n")                    # uncommitted at job start
    base = await __import__("backend.gitsafe", fromlist=["x"]).snapshot(
        proj / ".git", proj, "refs/jav3/base/" + JOB, "base")
    _branch(proj, base, {"c.txt": "c\n", "a.txt": "dirty2\n"}, deletes=["b.txt"])
    row = await _file(proj, base)
    assert json.loads(row["payload"])["base_dirty"] == ["a.txt"]
    (proj / "a.txt").write_text("dirty\n")
    out = await gitgate.approve_request(row["id"], "demo")
    assert out["status"] == "approved"
    assert len(_g(proj, "rev-list", "--parents", "-1", "HEAD").split()) == 3   # two parents
    assert (proj / "c.txt").read_text() == "c\n" and (proj / "a.txt").read_text() == "dirty2\n"
    assert not (proj / "b.txt").exists()
    assert _g(proj, "status", "--porcelain") == "?? .gitignore"
    assert hist and "c.txt" in hist[0][1]["applied"] and hist[0][1]["deleted"] == ["b.txt"]
    assert any(k.endswith(f"--{JOB}-merge") for k in removed)
    assert _g(proj, "branch", "--list", ITEM) == ""
    d = await gitgate.merge_detail("demo", row["id"])
    assert d["gone"]


async def test_approve_when_main_moved(proj, removed):
    base = _g(proj, "rev-parse", "HEAD")
    _branch(proj, base, {"c.txt": "c\n"})
    row = await _file(proj, base)
    (proj / "b.txt").write_text("b2\n")
    _g(proj, "commit", "-qam", "main moved")
    out = await gitgate.approve_request(row["id"], "demo")
    assert out["status"] == "approved"
    assert (proj / "c.txt").exists() and (proj / "b.txt").read_text() == "b2\n"


async def test_main_moved_conflict_stays_pending(proj, removed):
    base = _g(proj, "rev-parse", "HEAD")
    _branch(proj, base, {"a.txt": "pr\n"})
    row = await _file(proj, base)
    (proj / "a.txt").write_text("main\n")
    _g(proj, "commit", "-qam", "main edits a")
    head = _g(proj, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="a.txt"):
        await gitgate.approve_request(row["id"], "demo")
    assert _g(proj, "rev-parse", "HEAD") == head
    db = await get_db()
    try:
        r = await gitgate._fetch_request(db, row["id"])
    finally:
        await db.close()
    assert r["status"] == "pending" and "a.txt" in r["error"]


async def test_refuses_when_dir_changed_on_pr_path(proj, removed):
    base = _g(proj, "rev-parse", "HEAD")
    _branch(proj, base, {"a.txt": "pr\n", "c.txt": "c\n"})
    row = await _file(proj, base)
    (proj / "a.txt").write_text("mine\n")
    (proj / "z.txt").write_text("z\n")
    with pytest.raises(ValueError, match="a.txt"):
        await gitgate.approve_request(row["id"], "demo")
    assert (proj / "a.txt").read_text() == "mine\n" and not (proj / "c.txt").exists()
    # a path only the dir changed stays uncommitted after approving once a.txt is put back
    (proj / "a.txt").write_text("a\n")
    out = await gitgate.approve_request(row["id"], "demo")
    assert out["status"] == "approved"
    assert _g(proj, "status", "--porcelain") == "?? .gitignore\n?? z.txt"


async def test_reject_leaves_project_and_cleans(proj, removed):
    base = _g(proj, "rev-parse", "HEAD")
    _branch(proj, base, {"c.txt": "c\n"})
    row = await _file(proj, base)
    out = await gitgate.reject_request(row["id"], "demo")
    assert out["status"] == "rejected" and not (proj / "c.txt").exists()
    assert removed and _g(proj, "branch", "--list", BRANCH) == ""


async def test_detail_summary(proj):
    (proj / "a.txt").write_text("dirty\n")
    base = await __import__("backend.gitsafe", fromlist=["x"]).snapshot(
        proj / ".git", proj, "refs/jav3/base/" + JOB, "base")
    _branch(proj, base, {"a.txt": "dirty2\n", "c.txt": "c\n"})
    row = await _file(proj, base)
    d = await gitgate.merge_detail("demo", row["id"])
    assert d["base_dirty"] == ["a.txt"] and "c.txt" in d["diffstat"]
    assert d["parts"][0]["title"] == "physics" and "+c" in d["diff"]


async def test_gitea_on_mirrors_and_closes(proj, removed, monkeypatch):
    from backend import gitea
    seen = []
    monkeypatch.setattr(gitea, "enabled", lambda: True)

    async def mirror(slug, branch, sha, summary):
        seen.append(("open", branch))
        return 5

    async def close(slug, pl, *, merged):
        seen.append(("close", pl.get("pr_number"), merged))

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(gitea, "mirror_merge_request", mirror)
    monkeypatch.setattr(gitea, "close_merge_mirror", close)
    for n in ("reconcile", "sync_main", "ensure_remote_repo", "push_main"):
        monkeypatch.setattr(gitea, n, noop)
    base = _g(proj, "rev-parse", "HEAD")
    _branch(proj, base, {"c.txt": "c\n"})
    row = await _file(proj, base)
    assert row["pr_number"] == 5
    await gitgate.reject_request(row["id"], "demo")
    assert seen == [("open", BRANCH), ("close", 5, False)]

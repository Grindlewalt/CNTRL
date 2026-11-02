"""nov2: integration of item branches into the job's PR branch."""
import subprocess

import pytest

from backend import agents_run, gitgate, plan as plan_mod, plan_branches, worktrees
from backend.config import settings
from backend.db import init_db


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
async def proj(tmp_env):
    await init_db()
    d = settings.projects_dir / "demo"
    d.mkdir(parents=True)
    (d / "project.md").write_text("# demo\n")
    (d / "a.txt").write_text("one\ntwo\nthree\n")
    await gitgate.ensure_repo("demo")
    sh(d, "git", "add", "-A")
    sh(d, "git", "commit", "-qm", "init")
    return d


async def _setup(proj, edits):
    p = plan_mod.empty_plan(title="T", dump="d")
    p["isolation"] = "branches"
    p["status"] = "done"
    await plan_branches.ensure_base("demo", p, "JOB")
    for n, files in enumerate(edits.values(), 1):
        it = plan_mod.new_item(p, title=f"item {n}")
        it["status"] = "done"
        p["items"].append(it)
        w = await worktrees.create("demo", "JOB", it["id"], p["base"]["sha"])
        for rel, text in files.items():
            (w.dir / rel).write_text(text)
        it["work"] = await plan_branches.finish_item("demo", "JOB", w.key)
        await plan_branches.mark(p, it)
    await plan_mod.save("demo", p)
    return p


def _fake_mr(monkeypatch):
    calls = []

    async def mr(slug, **kw):
        calls.append(kw)
        return {"id": 7}
    monkeypatch.setattr(gitgate, "create_merge_request", mr)
    return calls


async def test_two_items_different_files_merge_clean(proj, monkeypatch):
    calls = _fake_mr(monkeypatch)
    await _setup(proj, {"a": {"x.txt": "x\n"}, "b": {"y.txt": "y\n"}})
    await plan_branches.integrate("demo", "run1", 1, plan_mod)
    p = plan_mod.load("demo")
    assert p["merge"]["state"] == "filed" and p["merge"]["request_id"] == 7
    out = sh(proj, "git", "ls-tree", "-r", "--name-only", p["merge"]["sha"])
    assert "x.txt" in out and "y.txt" in out
    assert not (proj / "x.txt").exists()          # the project is untouched
    assert len(calls[0]["parts"]) == 2


async def test_conflict_resolved_by_resolver(proj, monkeypatch):
    _fake_mr(monkeypatch)
    await _setup(proj, {"a": {"a.txt": "one\nAAA\nthree\n"},
                        "b": {"a.txt": "one\nBBB\nthree\n"}})

    async def fake(agent, task, active=None, **kw):
        w = worktrees.resolve(kw["work_key"])
        assert "<<<<<<<" in (w.dir / "a.txt").read_text()
        (w.dir / "a.txt").write_text("one\nAAA BBB\nthree\n")
        return {"final": "done"}
    monkeypatch.setattr(agents_run, "_run_headless", fake)
    await plan_branches.integrate("demo", "run1", 1, plan_mod)
    p = plan_mod.load("demo")
    assert p["merge"]["state"] == "filed"
    tip = p["merge"]["sha"]
    assert sh(proj, "git", "show", f"{tip}:a.txt") == "one\nAAA BBB\nthree\n"
    assert len(sh(proj, "git", "rev-list", "--parents", "-n1", tip).split()) == 3
    assert [i["work"]["state"] for i in p["items"]] == ["merged", "resolved"]


async def test_resolver_fails_pr_filed_without_it(proj, monkeypatch):
    calls = _fake_mr(monkeypatch)
    await _setup(proj, {"a": {"a.txt": "one\nAAA\nthree\n"},
                        "b": {"a.txt": "one\nBBB\nthree\n"}})

    async def fake(agent, task, active=None, **kw):
        return {"final": "gave up"}          # leaves the markers in place
    monkeypatch.setattr(agents_run, "_run_headless", fake)
    await plan_branches.integrate("demo", "run1", 1, plan_mod)
    p = plan_mod.load("demo")
    assert p["merge"]["state"] == "conflict"
    assert p["items"][1]["work"]["state"] == "conflict"
    assert "AAA" in sh(proj, "git", "show", f"{p['merge']['sha']}:a.txt")
    assert calls and "i2" in calls[0]["summary"]

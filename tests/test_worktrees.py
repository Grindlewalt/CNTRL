import asyncio
import subprocess

import pytest

from backend import worktrees, gitsafe
from backend.config import settings


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    d = settings.projects_dir / "demo"
    d.mkdir(parents=True)
    sh(d, "git", "init", "-q", "-b", "main")
    sh(d, "git", "config", "user.email", "a@b")
    sh(d, "git", "config", "user.name", "a")
    (d / "a.txt").write_text("one\n")
    from backend import gitgate
    asyncio.run(gitgate.ensure_repo("demo"))
    sh(d, "git", "add", "-A")
    sh(d, "git", "commit", "-qm", "init")
    return d


def test_base_clean_is_head(proj):
    head = sh(proj, "git", "rev-parse", "HEAD").strip()
    assert asyncio.run(worktrees.base_snapshot("demo", "j1")) == head
    assert worktrees.last_dirty[worktrees.job_tag("j1")] == 0


def test_base_dirty_and_create_remove(proj):
    head = sh(proj, "git", "rev-parse", "HEAD").strip()
    (proj / "a.txt").write_text("two\n")
    (proj / "new.txt").write_text("n\n")

    async def go():
        base = await worktrees.base_snapshot("demo", "j2")
        assert base != head
        assert worktrees.last_dirty[worktrees.job_tag("j2")] == 2
        w = await worktrees.create("demo", "j2", "i1", base)
        assert (w.dir / "a.txt").read_text() == "two\n"
        assert (w.dir / "new.txt").exists() and not (w.dir / ".git").exists()
        (w.dir / "mine.txt").write_text("x")
        w2 = await worktrees.create("demo", "j2", "i1", base)   # retry: reuses
        assert (w2.dir / "mine.txt").exists()
        assert await gitsafe.rev(w.git_dir, f"refs/heads/{w.branch}") == base
        await worktrees.remove(w.key)
        assert not w.dir.exists()
        assert await gitsafe.rev(w.git_dir, f"refs/heads/{w.branch}") is None
    asyncio.run(go())
    assert (proj / "a.txt").read_text() == "two\n"


def test_reap(proj):
    async def go():
        base = await worktrees.base_snapshot("demo", "j3")
        w = await worktrees.create("demo", "j3", "i1", base)
        gone = await worktrees.reap()
        assert w.key in gone and not w.dir.exists()
    asyncio.run(go())

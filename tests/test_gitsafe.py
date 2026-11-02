"""gitsafe.snapshot and the work keys (nov2 contract): a snapshot commits the files onto
a ref without touching HEAD, the index or the files; work keys never collide with a
project slug."""
import subprocess

import pytest

from backend import gitsafe, projects, worktrees
from backend.config import settings


def _g(d, *a):
    return subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t",
                           *a], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "p")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    d = tmp_path / "p" / "demo"
    d.mkdir(parents=True)
    _g(d, "init", "-q", "-b", "main")
    (d / "a.txt").write_text("a")
    (d / ".todo.md").write_text("- [ ] x")
    _g(d, "add", "a.txt")
    _g(d, "commit", "-qm", "first")
    return d


async def test_snapshot_commits_onto_a_ref_and_touches_nothing_else(repo):
    head, status = _g(repo, "rev-parse", "HEAD"), _g(repo, "status", "--porcelain")
    (repo / "b.txt").write_text("b")
    (repo / "a.txt").write_text("a2")
    git_dir = repo / ".git"
    sha = await gitsafe.snapshot(git_dir, repo, worktrees.HISTORY_REF, "turn 1")
    assert sha and _g(repo, "rev-parse", "HEAD") == head
    assert status == "?? .todo.md"
    assert _g(repo, "status", "--porcelain").split("\n") == [  # index and files as they were
        "M a.txt", "?? .todo.md", "?? b.txt"]
    assert _g(repo, "show", f"{sha}:a.txt") == "a2" and _g(repo, "show", f"{sha}:b.txt") == "b"
    names = _g(repo, "ls-tree", "-r", "--name-only", sha).split()
    assert ".todo.md" not in names                          # harness files never ride
    assert _g(repo, "rev-parse", f"{sha}^") == head          # first one sits on HEAD
    assert await gitsafe.snapshot(git_dir, repo, worktrees.HISTORY_REF, "same") is None
    (repo / "b.txt").unlink()
    two = await gitsafe.snapshot(git_dir, repo, worktrees.HISTORY_REF, "turn 2")
    assert _g(repo, "rev-parse", f"{two}^") == sha
    assert "b.txt" not in _g(repo, "ls-tree", "-r", "--name-only", two)
    assert _g(repo, "log", "-1", "--format=%an %s", two) == "Jav3 turn 2"
    # the hidden ref is not a branch
    assert "jav3" not in _g(repo, "branch", "-a")


async def test_snapshot_of_another_dir_with_extra_parent(repo, tmp_path):
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "a.txt").write_text("from the copy")
    head = _g(repo, "rev-parse", "HEAD")
    sha = await gitsafe.snapshot(repo / ".git", copy, "refs/heads/agent/abc123-i1", "item",
                                 start=head)
    assert _g(repo, "show", f"{sha}:a.txt") == "from the copy"
    other = await gitsafe.snapshot(repo / ".git", repo, "refs/heads/agent/abc123-i2", "x",
                                   start=head, parents=(sha,))
    assert _g(repo, "rev-list", "--parents", "-1", other).split()[1:] == [head, sha]
    assert not (copy / ".git").exists()


async def test_hooks_never_run(repo):
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(exist_ok=True)
    marker = repo.parent / "ran"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    (repo / "c.txt").write_text("c")
    await gitsafe.snapshot(repo / ".git", repo, worktrees.HISTORY_REF, "t")
    assert not marker.exists()


def test_work_keys(repo):
    k = worktrees.work_key("polycrash-v2", "job-17", "i3")
    assert worktrees.is_work_key(k) and k.startswith("polycrash-v2--")
    w = worktrees.resolve(k)
    assert w.slug == "polycrash-v2" and w.part == "i3" and w.branch.startswith("agent/")
    assert w.dir == settings.data_dir / "worktrees" / k
    assert w.git_dir == settings.projects_dir / "polycrash-v2" / ".git"
    m = worktrees.resolve(worktrees.work_key("demo", 5, "merge"))
    assert m.branch == f"agent/orch-{worktrees.job_tag(5)}"
    plain = worktrees.resolve("demo")
    assert plain.slug == "demo" and plain.branch is None and plain.dir == repo
    for name in ("Polycrash -- v2", "a--b", "x---i1", "--demo--"):
        assert not worktrees.is_work_key(projects.slugify(name))
    assert worktrees.project_of(k) == "polycrash-v2" and worktrees.project_of("demo") == "demo"
    with pytest.raises(ValueError):
        worktrees.work_key("demo", 1, "x9")

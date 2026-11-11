"""dev-agent-setup (backend/devagent.py): the dev install's project of CNTRL's
own code, its instructions and its skill. Idempotent, local git only, and
--refresh moves the copy forward only from a clean tree."""
import re
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from backend import devagent, gitgate
from backend.agent.tools.registry import load_registry
from backend.config import settings
from backend.db import get_db

ROOT = Path(__file__).resolve().parent.parent


def _git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(cwd, path, text, msg):
    (Path(cwd) / path).parent.mkdir(parents=True, exist_ok=True)
    (Path(cwd) / path).write_text(text)
    _git(cwd, "add", "-A")
    _git(cwd, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", msg)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def checkout(tmp_path):
    """A stand-in for the install's checkout: main checked out, a dev branch
    one commit ahead, and the repo's real .gitignore."""
    co = tmp_path / "checkout"
    co.mkdir()
    _git(co, "init", "-q", "-b", "main")
    (co / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    _commit(co, "a.txt", "one\n", "main")
    _git(co, "checkout", "-q", "-b", "dev")
    _commit(co, "dev.txt", "dev\n", "dev work")
    _git(co, "checkout", "-q", "main")
    return co


@pytest.fixture
def no_net(monkeypatch):
    real = socket.socket.connect

    def connect(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError(f"network use: {addr}")
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "connect", connect)


def _proj() -> Path:
    return settings.projects_dir / devagent.SLUG


async def _rows():
    db = await get_db()
    try:
        async with db.execute("SELECT slug, name, github_remote FROM projects") as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def test_creates_the_project_from_the_dev_branch(tmp_env, checkout, no_net, capsys):
    assert await devagent.setup(checkout=checkout) == 0
    out = capsys.readouterr().out
    assert "created from" in out and "(dev," in out
    assert await _rows() == [{"slug": "dev-agent", "name": "dev-agent", "github_remote": None}]
    p = _proj()
    assert (p / "dev.txt").read_text() == "dev\n"            # the dev branch, not main
    assert _git(p, "rev-parse", "HEAD") == _git(checkout, "rev-parse", "dev")
    assert _git(p, "branch", "--show-current") == "main"
    assert _git(p, "remote") == ""                            # nothing points at the checkout
    assert _git(p, "status", "--porcelain") == ""             # clean: refresh can fast-forward
    assert _git(p, "config", "user.name") == "CNTRL"
    md = (p / "project.md").read_text()
    assert md.startswith("# dev-agent\n") and "cntrl-try-everything" in md
    assert "reports/YYYY-MM-DD-<topic>.md" in md and "{created}" not in md
    assert "dev-agent" in (settings.memory_dir / "all-projects.md").read_text()
    # the skill, installed and offered
    skill = settings.skills_dir / devagent.SKILL / "SKILL.md"
    assert skill.read_text() == (ROOT / "scripts/dev-agent/skills" / devagent.SKILL
                                 / "SKILL.md").read_text()
    e = next(e for e in load_registry() if e["name"] == devagent.SKILL)
    assert e["kind"] == "skill" and e.get("enabled") is True
    assert "boxes are off" in out and "Gitea is not set up" in out


async def test_running_it_again_changes_nothing(tmp_env, checkout, capsys):
    await devagent.setup(checkout=checkout)
    head = _git(_proj(), "rev-parse", "HEAD")
    md = (_proj() / "project.md").read_text() + "- later: a journal line\n"
    (_proj() / "project.md").write_text(md)
    capsys.readouterr()
    assert await devagent.setup(checkout=checkout) == 0
    out = capsys.readouterr().out
    assert "already there" in out and "already installed" in out
    assert len(await _rows()) == 1
    assert _git(_proj(), "rev-parse", "HEAD") == head
    assert (_proj() / "project.md").read_text() == md          # the journal is kept


async def test_an_edited_skill_is_kept_and_refresh_puts_it_back(tmp_env, checkout, capsys):
    await devagent.setup(checkout=checkout)
    skill = settings.skills_dir / devagent.SKILL / "SKILL.md"
    skill.write_text(skill.read_text() + "\nmine\n")
    await devagent.setup(checkout=checkout)
    assert skill.read_text().endswith("mine\n")
    assert "kept your edited copy" in capsys.readouterr().out
    await devagent.setup(refresh_copy=True, checkout=checkout)
    assert not skill.read_text().endswith("mine\n")


async def test_refresh_fast_forwards_a_clean_copy(tmp_env, checkout, capsys):
    await devagent.setup(checkout=checkout)
    _git(checkout, "checkout", "-q", "dev")
    new = _commit(checkout, "b.txt", "two\n", "more dev work")
    capsys.readouterr()
    assert await devagent.setup(refresh_copy=True, checkout=checkout) == 0
    assert "fast-forwarded" in capsys.readouterr().out
    assert _git(_proj(), "rev-parse", "HEAD") == new
    assert (_proj() / "b.txt").read_text() == "two\n"
    assert await devagent.setup(refresh_copy=True, checkout=checkout) == 0
    assert "already up to date" in capsys.readouterr().out


async def test_refresh_says_why_not_on_a_dirty_tree(tmp_env, checkout, capsys):
    await devagent.setup(checkout=checkout)
    head = _git(_proj(), "rev-parse", "HEAD")
    (_proj() / "reports").mkdir()
    (_proj() / "reports" / "draft.md").write_text("wip\n")
    _commit(checkout, "c.txt", "x\n", "main moves")    # main is what is checked out...
    _git(checkout, "checkout", "-q", "dev")
    _commit(checkout, "d.txt", "x\n", "dev moves")
    capsys.readouterr()
    assert await devagent.setup(refresh_copy=True, checkout=checkout) == 1
    out = capsys.readouterr().out
    assert "not refreshed" in out and "reports/" in out and "uncommitted" in out
    assert _git(_proj(), "rev-parse", "HEAD") == head


async def test_refresh_merges_when_the_project_has_commits_of_its_own(tmp_env, checkout,
                                                                      capsys):
    await devagent.setup(checkout=checkout)
    _commit(_proj(), "reports/2026-11-10-x.md", "report\n", "a report")
    _git(checkout, "checkout", "-q", "dev")
    new = _commit(checkout, "b.txt", "two\n", "more dev work")
    capsys.readouterr()
    assert await devagent.setup(refresh_copy=True, checkout=checkout) == 0
    assert "merged" in capsys.readouterr().out
    p = _proj()
    assert (p / "b.txt").exists() and (p / "reports/2026-11-10-x.md").exists()
    assert _git(p, "merge-base", "--is-ancestor", new, "HEAD") == ""
    assert _git(p, "status", "--porcelain") == ""


async def test_a_conflicting_refresh_changes_nothing(tmp_env, checkout, capsys):
    await devagent.setup(checkout=checkout)
    head = _commit(_proj(), "a.txt", "project's\n", "edit a")
    _git(checkout, "checkout", "-q", "dev")
    _commit(checkout, "a.txt", "checkout's\n", "edit a too")
    capsys.readouterr()
    assert await devagent.setup(refresh_copy=True, checkout=checkout) == 1
    out = capsys.readouterr().out
    assert "conflict" in out and "a.txt" in out
    assert _git(_proj(), "rev-parse", "HEAD") == head
    assert _git(_proj(), "status", "--porcelain") == ""


async def test_a_checkout_without_dev_follows_its_branch(tmp_env, tmp_path, capsys):
    co = tmp_path / "plain"
    co.mkdir()
    _git(co, "init", "-q", "-b", "main")
    sha = _commit(co, "a.txt", "one\n", "only main")      # no .gitignore of ours at all
    assert await devagent.setup(checkout=co) == 0
    assert "(main," in capsys.readouterr().out
    p = _proj()
    # the baseline commit a new project gets: its .gitignore block and project.md
    assert _git(p, "rev-parse", "HEAD~1") == sha
    assert _git(p, "status", "--porcelain") == ""


async def test_local_git_only(tmp_env, checkout):
    env = devagent._git_env()
    assert env["GIT_ALLOW_PROTOCOL"] == "file" and env["GIT_TERMINAL_PROMPT"] == "0"
    rc, out = await devagent._git(checkout, "ls-remote", "https://example.invalid/x.git")
    assert rc != 0 and "not allowed" in out


async def test_a_trashed_project_is_not_brought_back(tmp_env, checkout, capsys):
    await devagent.setup(checkout=checkout)
    db = await get_db()
    try:
        await db.execute("UPDATE projects SET deleted_at = CURRENT_TIMESTAMP")
        await db.commit()
    finally:
        await db.close()
    assert await devagent.setup(checkout=checkout) == 1
    assert "in the trash" in capsys.readouterr().out


async def test_a_stray_directory_is_not_adopted(tmp_env, checkout, capsys):
    _proj().mkdir(parents=True)
    (_proj() / "keep.txt").write_text("mine")
    assert await devagent.setup(checkout=checkout) == 1
    assert "move it aside" in capsys.readouterr().out
    assert (_proj() / "keep.txt").read_text() == "mine" and await _rows() == []


async def test_with_boxes_on_it_gets_a_box_of_its_own(tmp_env, checkout, monkeypatch, capsys):
    from backend.vm import placement
    monkeypatch.setattr(settings, "vm_boxes_enabled", True)
    monkeypatch.setattr(settings, "docker_enabled", False)
    await devagent.setup(checkout=checkout)
    assert "its own VM p-dev-agent" in capsys.readouterr().out
    eff = await placement.effective(devagent.SLUG)
    assert eff["mode"] == "own" and eff["source"] == "project"
    # an operator's later choice is kept on a re-run
    db = await get_db()
    try:
        await placement.put(db, devagent.SLUG, {"mode": "shared"}, by_operator=True)
    finally:
        await db.close()
    await devagent.setup(checkout=checkout)
    assert "the shared box (kept)" in capsys.readouterr().out


def test_the_cli_refuses_unknown_flags(capsys):
    assert devagent.run(["--bogus"]) == 2
    assert "dev-agent-setup [--refresh]" in capsys.readouterr().err


def test_the_skill_has_the_shape_of_the_shipped_ones():
    def front(p):
        return yaml.safe_load(re.match(r"^---\s*\n(.*?)\n---", p.read_text(), re.S)[1])
    ours = front(ROOT / "scripts/dev-agent/skills" / devagent.SKILL / "SKILL.md")
    theirs = front(ROOT / "skills/organize-project/SKILL.md")
    assert list(ours) == list(theirs)
    assert list(ours["parameters"]) == list(theirs["parameters"])
    assert ours["name"] == devagent.SKILL and ours["enabled"] is True
    # outside the repo's skills/: a plain install never seeds it
    assert not (ROOT / "skills" / devagent.SKILL).exists()


def test_the_repos_gitignore_carries_the_managed_block(tmp_env):
    """Without it, every dev-agent copy starts dirty and --refresh never
    fast-forwards. A change to gitgate.GITIGNORE must be made here too."""
    d = settings.projects_dir / "probe"
    d.mkdir(parents=True)
    (d / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    assert gitgate.ensure_gitignore("probe") is False

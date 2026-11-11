"""dev-agent-setup: the dev install's own project (docs/DEV-INSTALL.md).

    python -m backend.cli dev-agent-setup [--refresh]

`scripts/install.sh --dev` runs it after first-run setup, Gitea and Docker.
Idempotent: every step looks before it acts and says what it found.

1. Project `dev-agent`, whose files are CNTRL's own code: a host-side copy of
   this install's checkout (settings.base_dir), fetched with git from a local
   path only (GIT_ALLOW_PROTOCOL=file, so no network). It follows the
   checkout's `dev` branch when there is one, else what the checkout has
   checked out, and its local branch is `main` like every other project's. It
   is registered by projects.register_cloned, the path a GitHub import takes.
2. Its project.md is the shipped instructions (scripts/dev-agent/project.md).
   An existing one is kept: the agent's journal grows in it.
3. The skill `cntrl-try-everything` (scripts/dev-agent/skills/), copied into
   the skills dir with `enabled: true`. Skills are not per project in CNTRL:
   enabled means offered in every chat of this instance. The skill lives
   outside the repo's skills/ on purpose, so a plain install never gets it.
4. With Gitea set up, the project's repo there (where its issues go).
5. With boxes on (vm_boxes_enabled), the project runs in a box of its own,
   unless its "Runs in" was already chosen. Boxes and egress are never
   switched on here.

--refresh also fast-forwards the project's copy from the checkout, only when
the project's tree is clean. When the project has commits of its own (the
agent's approved commits) the checkout's new commits are merged in instead;
a merge that conflicts is undone and the files are named.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from .config import settings

SLUG = NAME = "dev-agent"
SKILL = "cntrl-try-everything"
SUMMARY = ("CNTRL's own code, copied from this install's checkout, so an agent can "
           "use CNTRL from the inside and report what to improve.")
USAGE = "usage: python -m backend.cli dev-agent-setup [--refresh]"
GIT_TIMEOUT = 300


class SetupError(Exception):
    """A step that cannot go on, with what to do about it."""


def shipped_dir() -> Path:
    return settings.base_dir / "scripts" / "dev-agent"


def _say(kind: str, text: str) -> None:
    print(f"  {kind:<5} {text}")


# --- git, local only ----------------------------------------------------------

def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file")
    return env


async def _git(cwd: Path, *args: str) -> tuple[int, str]:
    """git -C <cwd> <args>, local transports only. (rc, stdout or stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(cwd), *args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=_git_env())
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, f"git {args[0]} timed out after {GIT_TIMEOUT}s"
    text = out.decode(errors="replace").strip()
    if proc.returncode != 0:
        text = err.decode(errors="replace").strip() or text
    return proc.returncode, text


async def source_ref(checkout: Path) -> tuple[str, str]:
    """(ref to fetch, what to call it): the checkout's dev branch when it has
    one (its own, else origin's), else the branch it has checked out."""
    rc, _ = await _git(checkout, "rev-parse", "--git-dir")
    if rc != 0:
        raise SetupError(f"{checkout} is not a git checkout, so there is nothing to copy")
    for ref, label in (("refs/heads/dev", "dev"), ("refs/remotes/origin/dev", "origin/dev")):
        if (await _git(checkout, "show-ref", "--verify", "--quiet", ref))[0] == 0:
            return ref, label
    rc, branch = await _git(checkout, "symbolic-ref", "--short", "-q", "HEAD")
    if rc == 0 and branch:
        return f"refs/heads/{branch}", branch
    return "HEAD", "the checked-out commit"


async def _fetch(project: Path, checkout: Path, ref: str) -> str:
    rc, out = await _git(project, "fetch", "-q", "--no-tags", "--", str(checkout), ref)
    if rc != 0:
        raise SetupError(f"could not fetch {ref} from {checkout}: {out}")
    rc, sha = await _git(project, "rev-parse", "FETCH_HEAD")
    if rc != 0:
        raise SetupError(f"could not read what was fetched: {sha}")
    return sha


async def _ancestor(project: Path, a: str, b: str) -> bool:
    return (await _git(project, "merge-base", "--is-ancestor", a, b))[0] == 0


async def _dirty(project: Path) -> list[str]:
    rc, out = await _git(project, "status", "--porcelain")
    if rc != 0:
        raise SetupError(f"git status failed in {project}: {out}")
    return [ln[3:] for ln in out.splitlines() if ln.strip()]


# --- the steps ----------------------------------------------------------------

async def _row(db) -> dict | None:
    async with db.execute("SELECT slug, deleted_at FROM projects WHERE slug = ?",
                          (SLUG,)) as cur:
        r = await cur.fetchone()
    return dict(r) if r else None


def _project_md() -> str:
    text = (shipped_dir() / "project.md").read_text()
    return text.replace("{created}", date.today().isoformat())


async def ensure_project(db, checkout: Path) -> str:
    """The project, created from the checkout if it is missing. Returns what
    happened, for the summary."""
    from . import gitgate, projects
    project = settings.projects_dir / SLUG
    row = await _row(db)
    if row and row["deleted_at"]:
        raise SetupError(f"project {SLUG} is in the trash: restore it (Projects page, or "
                         f"POST /api/projects/{SLUG}/restore) or purge it, then run this again")
    if row:
        if not (project / ".git").is_dir():
            raise SetupError(f"project {SLUG} has no git repo at {project}; move what is "
                             "there aside, purge the project, then run this again")
        return f"project {SLUG}: already there ({project})"
    if project.exists():
        raise SetupError(f"{project} exists but is not a project: move it aside, then "
                         "run this again")
    ref, label = await source_ref(checkout)
    project.mkdir(parents=True)
    try:
        for args in (("init", "-q"), ("symbolic-ref", "HEAD", "refs/heads/main")):
            rc, out = await _git(project, *args)
            if rc != 0:
                raise SetupError(f"git {args[0]} failed: {out}")
        sha = await _fetch(project, checkout, ref)
        rc, out = await _git(project, "reset", "-q", "--hard", sha)
        if rc != 0:
            raise SetupError(f"could not check out {sha[:12]}: {out}")
    except BaseException:
        shutil.rmtree(project, ignore_errors=True)
        raise
    (project / "project.md").write_text(_project_md())
    await projects.register_cloned(db, SLUG, NAME, summary=SUMMARY)
    await gitgate.ensure_repo(SLUG)
    if await _dirty(project):
        # a checkout without CNTRL's block in its .gitignore (or without the
        # rule that ignores root *.md): the same baseline commit a new project gets
        await gitgate.run_git(SLUG, "add", "-A", check=True)
        await gitgate.run_git(SLUG, "commit", "-q", "-m", "dev-agent project created",
                              check=True)
    return f"project {SLUG}: created from {checkout} ({label}, {sha[:12]})"


async def refresh(checkout: Path) -> tuple[bool, str]:
    """Bring the project's copy up to the checkout. (done, what happened)."""
    project = settings.projects_dir / SLUG
    from . import gitea
    if gitea.enabled():
        try:
            err = await gitea.sync_main(SLUG)       # a PR merged in Gitea comes home first
        except (gitea.GiteaError, gitea.GiteaOff, RuntimeError) as e:
            err = gitea.scrub(str(e))
        if err:
            return False, f"not refreshed: {err}"
    dirty = await _dirty(project)
    if dirty:
        shown = ", ".join(dirty[:5]) + (f" and {len(dirty) - 5} more" if len(dirty) > 5 else "")
        return False, (f"not refreshed: the project has {len(dirty)} uncommitted "
                       f"change(s) ({shown}). Approve or reject the agent's commit "
                       "request, or put the files back (/undo, /history), then run "
                       "--refresh again.")
    ref, label = await source_ref(checkout)
    sha = await _fetch(project, checkout, ref)
    if await _ancestor(project, sha, "HEAD"):
        return True, f"already up to date with the checkout's {label} ({sha[:12]})"
    if await _ancestor(project, "HEAD", sha):
        rc, out = await _git(project, "merge", "-q", "--ff-only", sha)
        if rc != 0:
            return False, f"not refreshed: fast-forward failed: {out}"
        return True, f"fast-forwarded to the checkout's {label} ({sha[:12]})"
    rc, out = await _git(project, "merge", "-q", "--no-ff", "--no-edit", "-m",
                         f"Merge CNTRL {label} from the install's checkout", sha)
    if rc != 0:
        _, conflicts = await _git(project, "diff", "--name-only", "--diff-filter=U")
        await _git(project, "merge", "--abort")
        names = ", ".join(conflicts.split()) or out
        return False, (f"not refreshed: the project's own commits conflict with the "
                       f"checkout's {label} in {names}. Nothing was changed.")
    return True, (f"merged the checkout's {label} ({sha[:12]}): the project has commits "
                  "of its own, so a fast-forward was not possible")


def ensure_skill(replace: bool) -> str:
    """The shipped skill in the skills dir (written if missing; with --refresh
    an edited copy is replaced by the shipped one)."""
    from .agent.tools.registry import compile_registry
    src = shipped_dir() / "skills" / SKILL / "SKILL.md"
    dest = settings.skills_dir / SKILL / "SKILL.md"
    new = src.read_text()
    if dest.is_file() and dest.read_text() == new:
        return f"skill {SKILL}: already installed and enabled"
    if dest.is_file() and not replace:
        return (f"skill {SKILL}: kept your edited copy ({dest}); --refresh puts back "
                "the shipped one")
    verb = "replaced with the shipped copy" if dest.is_file() else "installed and enabled"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(new)
    compile_registry()
    return f"skill {SKILL}: {verb}"


async def ensure_gitea() -> tuple[str, str]:
    from . import gitea
    if not gitea.enabled():
        return "note", ("Gitea is not set up here, so issues have nowhere to go and the "
                        "agent keeps them in its reports. Later: python -m backend.cli "
                        "gitea-setup, then run this again")
    try:
        await gitea.ensure_repo(SLUG)
    except (gitea.GiteaError, gitea.GiteaOff, RuntimeError) as e:
        return "warn", f"Gitea repo not ready: {gitea.scrub(str(e))}"
    return "ok", f"Gitea repo {gitea.web_url(SLUG)} (issues go here)"


async def ensure_box(db) -> tuple[str, str]:
    from .vm import boxes, lifecycle, placement
    if not boxes.enabled():
        return "note", ("boxes are off (vm_boxes_enabled), so dev-agent runs in the shared "
                        "VM like every project; this step does not turn them on")
    current = await placement.get_setting(db, SLUG)
    if current and current["mode"] != "profile":
        return "ok", f"runs in {placement.describe(await placement.effective(SLUG))} (kept)"
    body = {"mode": "own", "runtime": "docker" if lifecycle.docker_only_host() else None}
    try:
        out = await placement.put(db, SLUG, body, actor="dev-agent-setup", by_operator=True)
    except placement.PlacementError as e:
        return "warn", f"could not give dev-agent a box of its own: {e}"
    extra = "".join(f"; {w}" for w in out["warnings"])
    return "ok", f"runs in {placement.describe(out['effective'])}{extra}"


async def setup(refresh_copy: bool = False, checkout: Path | None = None) -> int:
    from .db import get_db, init_db
    checkout = Path(checkout or settings.base_dir)
    await init_db()
    print(f"== dev-agent for the checkout at {checkout}")
    rc = 0
    db = await get_db()
    try:
        try:
            _say("ok", await ensure_project(db, checkout))
        except SetupError as e:
            _say("error", str(e))
            return 1
        if refresh_copy:
            try:
                done, what = await refresh(checkout)
            except SetupError as e:
                done, what = False, f"not refreshed: {e}"
            _say("ok" if done else "warn", what)
            rc = 0 if done else 1
        _say("ok", ensure_skill(replace=refresh_copy))
        _say(*await ensure_gitea())
        _say(*await ensure_box(db))
    finally:
        await db.close()
    print("next: open the dev-agent project and ask it to run the "
          f"{SKILL} skill (docs/DEV-INSTALL.md)")
    return rc


def run(argv: list[str]) -> int:
    args = list(argv)
    if any(a not in ("--refresh",) for a in args):
        print(USAGE, file=sys.stderr)
        return 2
    return asyncio.run(setup(refresh_copy="--refresh" in args))

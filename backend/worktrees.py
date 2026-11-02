"""Per-agent copies of a project for an orchestrator job (nov2; docs/nov2-contract.md).

Each plan item of a job that runs "on branches" works in its own copy of the project,
under a WORK KEY instead of the project slug:

    <slug>--<job6>-<part>      part = i<N> (an item) | merge (the integration copy)

`--` never comes out of projects.slugify (it collapses runs to one `-`), so a work key
can't be a project's slug, and it passes every slug check in the code ([a-z0-9-]).
Policy (egress, secrets, taint, the box, the projects row, names) stays the PROJECT's;
only the files and the guest's workspace dir follow the key. The copy is a plain
directory with no `.git` in it: commits go through gitsafe with the project's own git
dir given explicitly, onto agent/<job6>-<part> (agent/orch-<job6> for the merge copy).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .config import settings

KEY_RE = re.compile(r"^(?P<slug>[a-z0-9](?:[a-z0-9]|-(?!-))*)--(?P<job>[a-z0-9]{6})"
                    r"-(?P<part>i\d{1,3}|merge)$")
HISTORY_REF = "refs/jav3/history"


@dataclass(frozen=True)
class Work:
    key: str                  # what the workspace machinery is keyed by
    slug: str                 # the project: policy, the box, the DB row
    dir: Path                 # where the files are on the host
    git_dir: Path             # the project's .git
    branch: str | None        # agent/<job6>-<part>; None for the project itself
    job: str | None = None    # job6
    part: str | None = None


def job_tag(job_id: str | int) -> str:
    """Six lowercase hex characters that name a job in keys and branches."""
    return hashlib.sha1(str(job_id).encode()).hexdigest()[:6]


def work_key(slug: str, job_id: str | int, part: str) -> str:
    key = f"{slug}--{job_tag(job_id)}-{part}"
    if not KEY_RE.match(key):
        raise ValueError(f"not a work key: {key!r}")
    return key


def is_work_key(key: str | None) -> bool:
    return bool(key and KEY_RE.match(key))


def root() -> Path:
    return settings.data_dir / "worktrees"


def branch_for(job: str, part: str) -> str:
    return f"agent/orch-{job}" if part == "merge" else f"agent/{job}-{part}"


def resolve(key: str) -> Work:
    """A slug or a work key -> where its files are and which project it belongs to."""
    m = KEY_RE.match(key or "")
    if not m:
        d = settings.projects_dir / key
        return Work(key, key, d, d / ".git", None)
    slug, job, part = m["slug"], m["job"], m["part"]
    return Work(key, slug, root() / key, settings.projects_dir / slug / ".git",
                branch_for(job, part), job, part)


def project_of(key: str | None) -> str | None:
    """The project a key belongs to (a slug is its own)."""
    if not key:
        return key
    m = KEY_RE.match(key)
    return m["slug"] if m else key


# --- agent B (docs/nov2-contract.md) ---------------------------------------------------

BASE_PREFIX = "refs/jav3/base/"
last_dirty: dict[str, int] = {}     # job6 -> files uncommitted at the job's start


def _pdir(slug: str) -> Path:
    return settings.projects_dir / slug


async def base_snapshot(slug: str, job_id: str | int) -> str:
    """The job's base: a commit of the project dir as it is now (uncommitted work
    included) on top of HEAD, kept at refs/jav3/base/<job6>; HEAD itself if clean.
    The number of uncommitted files is left in `last_dirty[job6]`."""
    from . import gitsafe, gitgate
    await gitgate.ensure_repo(slug)
    gd, wt = _pdir(slug) / ".git", _pdir(slug)
    tag = job_tag(job_id)
    ref = BASE_PREFIX + tag
    head = await gitsafe.rev(gd, "HEAD")
    sha = await gitsafe.rev(gd, ref)
    if not sha:
        sha = await gitsafe.snapshot(gd, wt, ref, "jav3 job base", start="HEAD")
        if sha is None:
            sha = head
            if head:
                await gitsafe.git(gd, "update-ref", ref, head, check=True)
    if head and sha and sha != head:
        _, o, _ = await gitsafe.git(gd, "diff-tree", "-r", "--name-only", head, sha)
        last_dirty[tag] = len([x for x in o.split("\n") if x])
    else:
        last_dirty[tag] = 0
    return sha


async def _materialize(git_dir: Path, dest: Path, tree_ish: str) -> None:
    import io
    import tarfile
    from . import gitsafe
    proc = await asyncio.create_subprocess_exec(
        "git", *gitsafe.HARDEN, f"--git-dir={git_dir}", "archive", "--format=tar", tree_ish,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=gitsafe._env(None))
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise gitsafe.GitError(f"git archive failed: {err.decode()[:300]}")
    dest.mkdir(parents=True, exist_ok=True)
    root_ = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(out)) as tf:
        for m in tf.getmembers():
            if not (m.isfile() or m.isdir()):
                continue
            target = (dest / m.name).resolve()
            if root_ != target and root_ not in target.parents:
                continue
            tf.extract(m, dest)


async def create(slug: str, job_id: str | int, part: str, base: str) -> Work:
    """Materialize the copy for `part` at `base` (agent/<job6>-<part> created there),
    or return the existing one (a retry of the same item reuses its copy)."""
    from . import gitsafe
    w = resolve(work_key(slug, job_id, part))
    if not w.dir.exists() or not any(w.dir.iterdir()):
        await _materialize(w.git_dir, w.dir, base)
    if not await gitsafe.rev(w.git_dir, f"refs/heads/{w.branch}"):
        await gitsafe.git(w.git_dir, "update-ref", f"refs/heads/{w.branch}", base,
                          check=True)
    return w


async def remove(key: str, *, delete_branch: bool = True) -> None:
    import shutil
    from . import gitsafe
    if not is_work_key(key):
        return
    w = resolve(key)
    if w.dir.exists():
        await asyncio.to_thread(shutil.rmtree, w.dir, True)
    if delete_branch and w.branch and w.git_dir.exists():
        await gitsafe.git(w.git_dir, "update-ref", "-d", f"refs/heads/{w.branch}")


def _live_jobs() -> set[str]:
    """job6 tags of jobs still running or awaiting a merge request."""
    tags: set[str] = set()
    try:
        from . import plan as _plan
        for p in settings.projects_dir.iterdir():
            pl = _plan.load(p.name) if p.is_dir() else None
            if not pl:
                continue
            # a branches job is live until its PR is decided (approve/reject removes the
            # copies); a plan that was only stopped or paused can still be resumed
            if pl.get("isolation") == "branches" and pl.get("work_job") and \
                    (pl.get("merge") or {}).get("state") not in ("approved", "rejected"):
                tags.add(job_tag(pl["work_job"]))
    except Exception:
        pass
    return tags


async def reap() -> list[str]:
    """At startup and hourly: copies with no live or pending job. Returns removed keys."""
    out: list[str] = []
    r = root()
    if not r.is_dir():
        return out
    live = _live_jobs()
    for d in sorted(r.iterdir()):
        m = KEY_RE.match(d.name)
        if m and m["job"] not in live:
            await remove(d.name)
            out.append(d.name)
    return out


# --- merging (plan.py's integration step) ---------------------------------------------

async def merge_tree(git_dir: Path, a: str, b: str) -> tuple[str, list[str]]:
    """`git merge-tree --write-tree a b` -> (tree, conflicted paths). A clean merge has
    no paths. A conflicted tree holds the conflict markers in the files. The merge base
    is found from history, so both must descend from the job's base."""
    from . import gitsafe
    rc, out, err = await gitsafe.git(git_dir, "merge-tree", "--write-tree", "--name-only",
                                     a, b)
    lines = out.split("\n")
    tree = lines[0].strip()
    if rc not in (0, 1) or len(tree) < 40:
        raise gitsafe.GitError(f"git merge-tree failed: {(err or out).strip()[:300]}")
    paths: list[str] = []
    if rc == 1:
        for ln in lines[1:]:
            if not ln.strip():
                break
            paths.append(ln.strip())
    return tree, paths


async def commit_tree(git_dir: Path, tree: str, parents: list[str], message: str) -> str:
    from . import gitsafe
    args = ["commit-tree", tree]
    for p in parents:
        args += ["-p", p]
    _, sha, _ = await gitsafe.git(git_dir, *args, env=gitsafe._who(),
                                  stdin=message.encode(), check=True)
    return sha.strip()


async def is_ancestor(git_dir: Path, anc: str, desc: str) -> bool:
    from . import gitsafe
    rc, _, _ = await gitsafe.git(git_dir, "merge-base", "--is-ancestor", anc, desc)
    return rc == 0


async def changed_files(git_dir: Path, a: str, b: str) -> list[str]:
    from . import gitsafe
    _, out, _ = await gitsafe.git(git_dir, "diff-tree", "-r", "--name-only", "--no-renames",
                                  a, b)
    return [x for x in out.split("\n") if x]


async def set_ref(git_dir: Path, ref: str, sha: str) -> None:
    from . import gitsafe
    await gitsafe.git(git_dir, "update-ref", ref, sha, check=True)


async def reset_copy(key: str, tree_ish: str) -> None:
    """Replace the copy's files with `tree_ish` (a commit or tree)."""
    import shutil
    w = resolve(key)
    if w.dir.exists():
        await asyncio.to_thread(shutil.rmtree, w.dir, True)
    await _materialize(w.git_dir, w.dir, tree_ish)

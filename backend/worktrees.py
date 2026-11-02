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


# --- implemented by agent B (docs/nov2-contract.md) ------------------------------------

async def base_snapshot(slug: str, job_id: str | int) -> str:
    """The job's base: a commit of the project dir as it is now (uncommitted work
    included) on top of HEAD, kept at refs/jav3/base/<job6>; HEAD itself if clean."""
    raise NotImplementedError


async def create(slug: str, job_id: str | int, part: str, base: str) -> Work:
    """Materialize the copy for `part` at `base` (agent/<job6>-<part> created there),
    or return the existing one (a retry of the same item reuses its copy)."""
    raise NotImplementedError


async def remove(key: str, *, delete_branch: bool = True) -> None:
    raise NotImplementedError


async def reap() -> list[str]:
    """At startup and hourly: copies with no live or pending job. Returns removed keys."""
    raise NotImplementedError

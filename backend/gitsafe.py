"""Host-side git on directories an agent writes to (nov2).

Two users: the edit history (edithistory.py: every batch of agent writes the host
applies becomes a commit on a hidden ref) and the per-agent branches of an
orchestrator job (worktrees.py: each agent's own copy of the project, committed
onto agent/<job>-<item>). In both the work tree holds agent-written bytes and the
git dir is the host's, so:

- the git dir and the work tree are always given explicitly (`--git-dir`,
  `--work-tree`): nothing is discovered from a file the agent could have written;
- hooks, fsmonitor, the system and global config are off: no program runs because
  of what is in the tree;
- every commit is built in a throwaway index, so the project's real index, HEAD,
  main and the files themselves are never touched by a snapshot.
"""
from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

from .config import settings

GIT_TIMEOUT = 60
HARDEN = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
          "-c", "core.untrackedCache=false", "-c", "gc.auto=0",
          "-c", "commit.gpgSign=false", "-c", "protocol.allow=never")
ZERO = "0" * 40


class GitError(RuntimeError):
    pass


def _env(extra: dict[str, str] | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"})
    if extra:
        env.update(extra)
    return env


async def git(git_dir: Path, *args: str, work_tree: Path | None = None,
              index: Path | None = None, env: dict[str, str] | None = None,
              check: bool = False, stdin: bytes | None = None,
              timeout: int = GIT_TIMEOUT) -> tuple[int, str, str]:
    """git --git-dir=<git_dir> [--work-tree=<work_tree>] <args>, hardened (see above).
    `index`: GIT_INDEX_FILE for this call. Never a shell."""
    cmd = ["git", *HARDEN, f"--git-dir={git_dir}"]
    if work_tree is not None:
        cmd.append(f"--work-tree={work_tree}")
    extra = dict(env or {})
    if index is not None:
        extra["GIT_INDEX_FILE"] = str(index)
    proc = await asyncio.create_subprocess_exec(
        *cmd, *args, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_env(extra),
        cwd=str(work_tree or git_dir))
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise GitError(f"git {args[0] if args else ''} timed out after {timeout}s")
    o, e = out.decode(errors="replace"), err.decode(errors="replace")
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {(e or o).strip()[:400]}")
    return proc.returncode, o, e


async def rev(git_dir: Path, name: str) -> str | None:
    """The commit `name` points at, or None."""
    rc, out, _ = await git(git_dir, "rev-parse", "--verify", "-q", f"{name}^{{commit}}")
    return out.strip() if rc == 0 and out.strip() else None


def _who() -> dict[str, str]:
    return {"GIT_AUTHOR_NAME": "Jav3", "GIT_AUTHOR_EMAIL": settings.git_author_email,
            "GIT_COMMITTER_NAME": "Jav3", "GIT_COMMITTER_EMAIL": settings.git_author_email}


# one snapshot at a time per (git dir, ref): the compare-and-swap below would catch a
# race, but two turns finishing together should both land, not one of them fail
_locks: dict[tuple[str, str], asyncio.Lock] = {}


async def tree_of(git_dir: Path, work_tree: Path, start: str | None) -> str:
    """The tree the work tree's files make now (honouring .gitignore and the git dir's
    info/exclude, harness files kept as `start` has them), built in a throwaway index."""
    from .gitgate import HARNESS_PATHSPECS
    idx = git_dir / f"jav3-snap-{secrets.token_hex(4)}.index"
    try:
        if start:
            await git(git_dir, "read-tree", start, index=idx, check=True)
        else:
            await git(git_dir, "read-tree", "--empty", index=idx, check=True)
        await git(git_dir, "add", "-A", work_tree=work_tree, index=idx, check=True)
        # the harness's own files (.todo.md, .plan.json...) never ride a snapshot
        rc, out, _ = await git(git_dir, "ls-files", "-z", "--", *HARNESS_PATHSPECS,
                               work_tree=work_tree, index=idx)
        tracked = [p for p in out.split("\0") if p] if rc == 0 else []
        if tracked:
            await git(git_dir, "rm", "-q", "--cached", "--ignore-unmatch", "--", *tracked,
                      work_tree=work_tree, index=idx, check=True)
            if start:
                rc, out, _ = await git(git_dir, "ls-tree", "-r", "-z", "--name-only", start,
                                       "--", *tracked)
                back = [p for p in out.split("\0") if p] if rc == 0 else []
                if back:
                    await git(git_dir, "reset", "-q", start, "--", *back,
                              work_tree=work_tree, index=idx, check=True)
        _, tree, _ = await git(git_dir, "write-tree", index=idx, check=True)
        return tree.strip()
    finally:
        idx.unlink(missing_ok=True)


async def snapshot(git_dir: Path, work_tree: Path, ref: str, message: str, *,
                   start: str | None = "HEAD", parents: tuple[str, ...] = ()) -> str | None:
    """Commit what `work_tree` holds now onto `ref` (a full ref name). The first parent
    is the ref's tip, or `start` when the ref does not exist yet (None/missing = a root
    commit); `parents` are extra parents (a merge). Returns the new commit, or None when
    the files are what the tip already has (and no extra parent asks for a commit).
    Main, HEAD, the real index and the files are untouched."""
    key = (str(git_dir), ref)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        tip = await rev(git_dir, ref)
        first = tip or (await rev(git_dir, start) if start else None)
        tree = await tree_of(git_dir, work_tree, first)
        if first and not parents:
            _, old, _ = await git(git_dir, "rev-parse", f"{first}^{{tree}}", check=True)
            if old.strip() == tree:
                return None
        args = ["commit-tree", tree]
        for p in ((first,) if first else ()) + tuple(parents):
            args += ["-p", p]
        _, sha, _ = await git(git_dir, *args, env=_who(), stdin=message.encode(), check=True)
        sha = sha.strip()
        await git(git_dir, "update-ref", "-m", "jav3 snapshot", ref, sha, tip or ZERO,
                  check=True)
        return sha

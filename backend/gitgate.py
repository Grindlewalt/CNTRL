"""Git gate: the agent can only *request* a commit; the host commits (and
pushes, when a remote is configured) after operator approval via the API.

The repo is the project dir itself. Staging metadata and runtime files are
kept out via a host-written .gitignore, and the agent can never write into
.git/ (writes + workspace endpoints refuse the path).
"""
import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .config import settings
from .db import get_db

log = logging.getLogger("jav3.gitgate")

GIT_TIMEOUT = 30
NET_TIMEOUT = 120       # push / fetch / ls-remote cross the internet on a Pi
CLONE_TIMEOUT = 300

# The harness's own files at the project root: never part of a commit or a push,
# whatever the project's .gitignore says (the agent can replace the host-written
# one). `.todo.md` is the todo_update list (`.todo-archive.md` its pruned items),
# `.plan.json` the plan checklist, `runs/<job>/*.md` the rollups an orchestrator
# or research run writes. Used for .gitignore, for .git/info/exclude (which
# `git add -A` honours and the agent cannot write: the add in approve_request and
# gitea's snapshot rely on that one), and for unstage_harness below.
RUNTIME_FILES = (".staging", ".workspace.json", ".context.json", ".todo.md",
                 ".todo-archive.md", ".plan.json", "data")
ROLLUP_GLOB = "runs/*/*.md"     # narrow on purpose: a project's own runs/ data stays committable
_EXCLUDE_PATTERNS = tuple(f"/{n}" for n in RUNTIME_FILES) + (f"/{ROLLUP_GLOB}",)
# what a commit must never stage even when tracked, as pathspecs (data/ is kept
# out of new work above but is not policed once tracked: a project may hold real
# data there on purpose)
HARNESS_PATHSPECS = (".staging", ".workspace.json", ".context.json", ".todo.md",
                     ".todo-archive.md", ".plan.json", f":(glob){ROLLUP_GLOB}")
# The managed block of a project's .gitignore. Everything between the markers is
# rewritten by ensure_gitignore, so a project's own rules go outside it.
MANAGED_BEGIN = ("# >>> Jav3 harness files: managed by Jav3 (rewritten on load); "
                 "your own rules go outside this block")
MANAGED_END = "# <<< Jav3 harness files"
GITIGNORE = "\n".join([
    MANAGED_BEGIN, ".staging/", ".workspace.json", ".context.json", ".todo.md",
    ".todo-archive.md", "/.plan.json", f"/{ROLLUP_GLOB}", "data/", MANAGED_END]) + "\n"
_EXCLUDE = "# Jav3 runtime files (written by the host)\n" + "".join(
    f"{p}\n" for p in _EXCLUDE_PATTERNS)


def _project_dir(slug: str) -> Path:
    return settings.projects_dir / slug


async def run_git(slug: str, *args: str, check: bool = False,
                  extra_env: dict[str, str] | None = None,
                  timeout: int = GIT_TIMEOUT) -> tuple[int, str, str]:
    """git -C <project dir> <args>. Never a shell; env stripped of GIT_* surprises."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra_env:
        env.update(extra_env)
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(_project_dir(slug)), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"git {' '.join(args)} timed out after {timeout}s")
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {_scrub((err or out).strip())}")
    return proc.returncode, out, err


async def flush_guest_writes(slug: str) -> None:
    """Bring this turn's file writes to the host before a git tool reads them.

    A turn that runs in a guest VM buffers write_file/edit_file there (.staging)
    and hands the buffer back only after its final message. Until then the host
    repo cannot see the files: git_status said "clean", git_commit_request said
    "nothing to commit", and git_push_request snapshotted a tree WITHOUT the
    agent's new files (a PR holding one host-side journal line, 2026-09-29).
    The guest's `pull` returns the buffer without clearing it and applying it
    again is idempotent, so this is safe mid-turn and again at turn end. A no-op
    outside a brokered guest tool call (HTTP, host-run turns, tests)."""
    from .agent import budget as budget_mod
    from .vm import broker
    op = budget_mod.active_op_id.get()
    if not op or broker.get_turn(op) is None:
        return
    from .vm import guest_turn
    try:
        await asyncio.wait_for(guest_turn.pull_writes(slug), timeout=60)
    except Exception as e:  # noqa: BLE001 — the git tool still answers, from what the host has
        log.warning("could not pull the guest's writes for %s: %s", slug, e)


def ensure_gitignore(slug: str) -> bool:
    """Make sure the project's .gitignore carries the current managed block of
    harness files; True if the file was written. Idempotent: no block yet means
    it is added at the end (nothing of the project's own rules is touched), an
    old block is replaced in place, and a current one is left alone, so calling
    it on every load costs one small read. Never raises: a project whose
    directory or .gitignore cannot be used just goes without."""
    path = _project_dir(slug) / ".gitignore"
    try:
        if not path.parent.is_dir():
            return False
        have = path.read_text() if path.exists() else ""
        lines = have.splitlines()
        begin = next((i for i, l in enumerate(lines)
                      if l.startswith("# >>> Jav3 harness files")), None)
        end = next((i for i, l in enumerate(lines)
                    if begin is not None and i > begin
                    and l.startswith("# <<< Jav3 harness files")), None)
        block = GITIGNORE.splitlines()
        if begin is not None and end is not None:
            new = lines[:begin] + block + lines[end + 1:]
        else:
            # none, or a damaged one (an end marker lost): add a whole block at
            # the end; a half block left behind only repeats lines harmlessly
            new = lines + ([""] if lines and lines[-1].strip() else []) + block
        text = "\n".join(new) + "\n"
        if text == have:
            return False
        path.write_text(text)
        return True
    except (OSError, UnicodeDecodeError) as e:
        log.warning("could not update %s: %s", path, e)
        return False


async def tracked_harness(slug: str, extra_env: dict[str, str] | None = None) -> list[str]:
    """Harness files git already tracks (an old commit took them in): they
    are no longer ignored, so every `git add -A` stages their changes."""
    rc, out, _ = await run_git(slug, "ls-files", "-z", "--", *HARNESS_PATHSPECS,
                               extra_env=extra_env)
    return [p for p in out.split("\0") if p] if rc == 0 else []


async def unstage_harness(slug: str, extra_env: dict[str, str] | None = None) -> list[str]:
    """After a `git add`: put the harness files back to what HEAD has, so a file
    that is tracked anyway (the history is the operator's to rewrite, not ours)
    does not ride into the commit. Returns the files it put back."""
    tracked = await tracked_harness(slug, extra_env)
    if tracked:
        await run_git(slug, "reset", "-q", "--", *tracked, extra_env=extra_env)
    return tracked


async def harness_notice(slug: str) -> list[str]:
    """What to tell the agent about harness files that are tracked: one line per
    file, and each file only ONCE per project (remembered in .git, which no
    agent tool can write)."""
    tracked = await tracked_harness(slug)
    if not tracked:
        return []
    told_path = _project_dir(slug) / ".git" / "jav3-harness-told"
    try:
        told = set(told_path.read_text().split("\n")) if told_path.exists() else set()
        fresh = [t for t in tracked if t not in told]
        if fresh:
            told_path.write_text("\n".join(sorted(told | set(fresh))) + "\n")
    except OSError:
        fresh = tracked
    return [f"{t} is tracked; run `git rm --cached {t}` to stop committing it "
            "(Jav3 leaves it out of every commit meanwhile)" for t in fresh]


# per-slug init lock: git_status/git_diff are read_only-flagged, so the loop
# may run them concurrently — two first-touches must not race `git init`
_repo_locks: dict[str, asyncio.Lock] = {}


async def ensure_repo(slug: str) -> None:
    lock = _repo_locks.setdefault(slug, asyncio.Lock())
    async with lock:
        d = _project_dir(slug)
        if not (d / ".git").exists():
            await run_git(slug, "init", "-q", check=True)
            await run_git(slug, "config", "user.name", "Jav3", check=True)
            await run_git(slug, "config", "user.email", settings.git_author_email,
                          check=True)
        ensure_gitignore(slug)
        # an `rm`/rewrite of .gitignore must not let the runtime files into a
        # commit: info/exclude lives inside .git, which no agent tool can write
        exclude = d / ".git" / "info" / "exclude"
        try:
            if not exclude.exists() or exclude.read_text() != _EXCLUDE:
                exclude.parent.mkdir(exist_ok=True)
                exclude.write_text(_EXCLUDE)
        except OSError as e:
            log.warning("could not write %s: %s", exclude, e)


# --- GitHub remote plumbing --------------------------------------------------
# The remote URL is stored CLEAN in projects.github_remote and .git/config
# ("origin"). Auth rides only in per-invocation env (GIT_CONFIG_* ->
# http.extraheader), so the token never appears in argv, .git/config, the DB,
# or persisted error text; _scrub is belt-and-braces on every surfaced string.
# All of this is operator-only surface — no agent tool can set a remote,
# push, or pull.

def github_token() -> str | None:
    from . import secrets as secrets_store
    return (secrets_store.load().get("GITHUB_TOKEN")
            or os.environ.get("JARVIS_GITHUB_TOKEN") or None)


def valid_remote(url: str) -> bool:
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    return (u.scheme == "https" and u.hostname == "github.com"
            and not u.username and not u.password
            and len([p for p in u.path.split("/") if p]) >= 2)


def _scrub(text: str) -> str:
    tok = github_token()
    return text.replace(tok, "***") if tok and tok in text else text


def _auth_env(url: str | None) -> dict[str, str]:
    tok = github_token()
    if not tok or not url or urlsplit(url).hostname != "github.com":
        return {}
    b64 = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {b64}"}


async def get_remote(slug: str) -> str | None:
    db = await get_db()
    try:
        async with db.execute("SELECT github_remote FROM projects WHERE slug = ?",
                              (slug,)) as cur:
            row = await cur.fetchone()
        return row["github_remote"] if row else None
    finally:
        await db.close()


async def _ensure_origin(slug: str, url: str) -> None:
    """Self-heal: origin mirrors the stored URL (covers rows set before the
    remote API existed, or hand-edited in the DB)."""
    rc, out, _ = await run_git(slug, "remote", "get-url", "origin")
    if rc != 0:
        await run_git(slug, "remote", "add", "origin", url, check=True)
    elif out.strip() != url:
        await run_git(slug, "remote", "set-url", "origin", url, check=True)


async def set_remote(slug: str, url: str | None) -> None:
    """Persist the remote (DB) and mirror it onto the repo's origin so plain
    fetch/merge refs (origin/<branch>) exist. None disconnects."""
    await ensure_repo(slug)
    if url:
        await _ensure_origin(slug, url)
    else:
        rc, _, _ = await run_git(slug, "remote", "get-url", "origin")
        if rc == 0:
            await run_git(slug, "remote", "remove", "origin")
    db = await get_db()
    try:
        await db.execute("UPDATE projects SET github_remote = ? WHERE slug = ?",
                         (url, slug))
        await db.commit()
    finally:
        await db.close()


async def verify_remote(slug: str, url: str) -> None:
    """ls-remote reachability/auth check; raises RuntimeError (scrubbed) if not."""
    await ensure_repo(slug)
    rc, out, err = await run_git(slug, "ls-remote", "--heads", "--", url,
                                 extra_env=_auth_env(url), timeout=NET_TIMEOUT)
    if rc != 0:
        raise RuntimeError(_scrub((err or out).strip()))


async def current_branch(slug: str) -> str:
    rc, out, _ = await run_git(slug, "symbolic-ref", "--short", "-q", "HEAD")
    return out.strip() if rc == 0 and out.strip() else "main"


async def push_to_remote(slug: str) -> str:
    url = await get_remote(slug)
    if not url:
        raise ValueError("no remote connected for this project")
    await _ensure_origin(slug, url)
    branch = await current_branch(slug)
    rc, out, err = await run_git(slug, "push", "origin", f"HEAD:refs/heads/{branch}",
                                 extra_env=_auth_env(url), timeout=NET_TIMEOUT)
    if rc != 0:
        raise RuntimeError(_scrub((err or out).strip()))
    return _scrub((err or out).strip() or "pushed")   # git narrates on stderr


async def pull_from_remote(slug: str) -> str:
    """fetch + ff-only merge. Refuses on a dirty tree — never merge over the
    agent's uncommitted work."""
    url = await get_remote(slug)
    if not url:
        raise ValueError("no remote connected for this project")
    _, porcelain, _ = await run_git(slug, "status", "--porcelain")
    if porcelain.strip():
        raise ValueError("working tree has uncommitted changes — commit "
                         "(approve a request) or discard them before pulling")
    await _ensure_origin(slug, url)
    branch = await current_branch(slug)
    await run_git(slug, "fetch", "origin", extra_env=_auth_env(url),
                  timeout=NET_TIMEOUT, check=True)
    rc, out, err = await run_git(slug, "merge", "--ff-only",
                                 f"origin/{branch}")
    if rc != 0:
        raise RuntimeError(_scrub((err or out).strip()))
    return _scrub((out or err).strip() or "up to date")


async def ahead_behind(slug: str, fetch: bool = False) -> dict | None:
    """{'ahead': n, 'behind': m} vs origin/<branch>, or None if no remote ref
    is known yet. fetch=True refreshes origin first (network)."""
    url = await get_remote(slug)
    if not url:
        return None
    if fetch:
        await _ensure_origin(slug, url)
        await run_git(slug, "fetch", "origin", extra_env=_auth_env(url),
                      timeout=NET_TIMEOUT, check=True)
    branch = await current_branch(slug)
    rc, out, _ = await run_git(slug, "rev-list", "--left-right", "--count",
                               f"HEAD...origin/{branch}")
    if rc != 0:
        return None
    ahead, behind = (int(x) for x in out.split())
    return {"ahead": ahead, "behind": behind}


async def clone_repo(url: str, dest: Path) -> None:
    """git clone into a fresh dir; auth via env, so the token never lands in
    the clone's .git/config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(_auth_env(url))
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--", url, str(dest),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(),
                                                timeout=CLONE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"git clone timed out after {CLONE_TIMEOUT}s")
    if proc.returncode != 0:
        raise RuntimeError(_scrub(
            (stderr.decode(errors="replace") or stdout.decode(errors="replace")).strip()))


async def status_text(slug: str) -> str:
    rc, out, _ = await run_git(slug, "symbolic-ref", "--short", "-q", "HEAD")
    branch = out.strip() if rc == 0 else "detached"
    rc, last, _ = await run_git(slug, "log", "-1", "--oneline")
    last = last.strip() if rc == 0 else "no commits yet"
    _, porcelain, _ = await run_git(slug, "status", "--porcelain", "-uall")
    changes = porcelain.strip() or "clean"
    return f"branch: {branch}\nlast commit: {last}\nchanges:\n{changes}"


async def diff_text(slug: str, path: str | None = None, max_chars: int = 20000) -> str:
    rc, _, _ = await run_git(slug, "rev-parse", "--verify", "-q", "HEAD")
    args = ["diff", "--no-color"] + (["HEAD"] if rc == 0 else [])
    if path:
        args += ["--", path]
    _, diff, _ = await run_git(slug, *args)
    _, porcelain, _ = await run_git(slug, "status", "--porcelain", "-uall")
    untracked = [l[3:] for l in porcelain.splitlines() if l.startswith("??")]
    if path:
        untracked = [u for u in untracked
                     if u == path or u.startswith(path.rstrip("/") + "/")]
    parts = []
    if diff.strip():
        parts.append(diff.rstrip())
    if untracked:
        parts.append("untracked files (no diff until committed):\n"
                     + "\n".join(untracked))
    text = "\n".join(parts) or "no changes"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text


async def _fetch_request(db, rid: int) -> dict:
    async with db.execute("SELECT * FROM git_requests WHERE id = ?", (rid,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise KeyError(f"no git request #{rid}")
    return dict(row)


def _requesting_turn() -> int | None:
    """The conversation whose turn filed a request (the broker restores it for
    a guest tool call); None over HTTP. Recorded so the agents tree can show
    which node is waiting on this approval."""
    from . import runtime
    return runtime.conversation_id.get()


_BRANCH_RE = re.compile(r"agent/orch-([0-9a-f]{6})")
_HEX40 = re.compile(r"[0-9a-f]{40}")
DETAIL_CAP = 40000


def _gd(slug: str) -> Path:
    return _project_dir(slug) / ".git"


async def _names(slug: str, a: str, b: str) -> dict[str, str]:
    """path -> status letter between two trees/commits."""
    from . import gitsafe
    _, out, _ = await gitsafe.git(_gd(slug), "diff-tree", "-r", "-z", "--name-status",
                                  "--no-renames", a, b, check=True)
    parts = out.split("\0")
    return {parts[i + 1]: parts[i] for i in range(0, len(parts) - 1, 2) if parts[i]}


async def yolo_decide(row: dict) -> tuple[dict, str | None]:
    """FULL mode: approve a request just filed (commit, remote, push or merge) as it
    is made. -> (the row as it now stands, an error text or None). When the
    conversation is not in yolo-full this does nothing and the row stays pending;
    when the approval itself fails (a push refused, Gitea down) the row stays
    pending too and the error says why. The row's own conversation decides: a
    request filed with none (HTTP, a scheduled run) never auto-approves."""
    from . import permissions
    cid = row.get("conversation_id")
    if cid is None or not await permissions.auto_approve(
            f"git_{row.get('kind') or 'commit'}", cid=cid, project=row["project_slug"],
            summary=f"git {row.get('kind') or 'commit'} request #{row['id']} in "
                    f"'{row['project_slug']}': {(row.get('message') or '')[:120]}",
            detail={"git_request_id": row["id"], "kind": row.get("kind") or "commit"}):
        return row, None
    try:
        return await approve_request(row["id"], row["project_slug"]), None
    except Exception as e:  # noqa: BLE001 — stays pending for the operator
        db = await get_db()
        try:
            return await _fetch_request(db, row["id"]), str(e)[:300]
        finally:
            await db.close()


async def create_merge_request(slug: str, *, job_id: str, branch: str, base_sha: str,
                               parts: list[dict], summary: str,
                               conversation_id: int | None = None) -> dict:
    """An orchestrator job's one PR (nov2, docs/nov2-contract.md): `branch`
    (agent/orch-<job6>) into main, approved in the same queue as commits. `base_sha` is
    the job's base (worktrees.base_snapshot); `parts` one dict per item:
    {item, title, branch, sha, files, state}. Returns the request row."""
    from . import gitsafe
    if not _BRANCH_RE.fullmatch(branch or ""):
        raise ValueError(f"not an orchestrator branch: {branch!r}")
    if not _HEX40.fullmatch(base_sha or ""):
        raise ValueError("bad base commit id")
    if not isinstance(parts, list):
        raise ValueError("parts must be a list")
    clean = []
    for p in parts:
        if not isinstance(p, dict) or not str(p.get("title", "")).strip():
            raise ValueError("each part needs a title")
        if p.get("sha") and not _HEX40.fullmatch(str(p["sha"])):
            raise ValueError("bad part commit id")
        if p.get("branch") and not re.fullmatch(r"agent/[0-9a-f]{6}-i\d{1,3}", str(p["branch"])):
            raise ValueError(f"bad part branch {p.get('branch')!r}")
        clean.append({"item": p.get("item"), "title": str(p["title"]).strip()[:200],
                      "branch": p.get("branch"), "sha": p.get("sha"),
                      "files": int(p.get("files") or 0), "state": p.get("state")})
    if not (summary or "").strip():
        raise ValueError("summary must not be empty")
    await ensure_repo(slug)
    gd = _gd(slug)
    tip = await gitsafe.rev(gd, f"refs/heads/{branch}")
    if not tip:
        raise ValueError(f"branch {branch} does not exist in {slug}")
    # uncommitted files the job started with: the base is a snapshot commit off main
    dirty: list[str] = []
    rc, _, _ = await gitsafe.git(gd, "merge-base", "--is-ancestor", base_sha, "HEAD")
    if rc != 0:
        try:
            dirty = sorted(await _names(slug, f"{base_sha}^", base_sha))
        except Exception:                           # noqa: BLE001
            dirty = []
    # what approval puts on main: everything from main as the job found it (the base's
    # parent when the base is a snapshot of uncommitted work) to the PR tip
    start = f"{base_sha}^" if dirty else base_sha
    try:
        total = len(await _names(slug, start, tip))
    except Exception:                               # noqa: BLE001
        total = None
    payload = {"job_id": str(job_id), "branch": branch, "base_sha": base_sha,
               "parts": clean, "sha": tip, "base_dirty": dirty, "files_total": total}
    db = await get_db()
    try:
        async with db.execute(
                "SELECT id FROM git_requests WHERE project_slug = ? AND kind = 'merge' "
                "AND status = 'pending' AND json_extract(payload, '$.job_id') = ?",
                (slug, str(job_id))) as cur:
            if await cur.fetchone():
                raise ValueError(f"job {job_id} already has a pending merge request")
        cur = await db.execute(
            "INSERT INTO git_requests (project_slug, kind, message, payload, branch, "
            "commit_sha, conversation_id) VALUES (?, 'merge', ?, ?, ?, ?, ?)",
            (slug, summary.strip(), json.dumps(payload), branch, tip,
             conversation_id if conversation_id is not None else _requesting_turn()))
        await db.commit()
        rid = cur.lastrowid
        row = await _fetch_request(db, rid)
    finally:
        await db.close()
    from . import gitea
    if gitea.enabled():
        try:
            number = await gitea.mirror_merge_request(slug, branch, tip, summary.strip())
            if number:
                payload["pr_number"] = number
                db = await get_db()
                try:
                    await db.execute("UPDATE git_requests SET payload = ?, pr_number = ?, "
                                     "pr_url = ? WHERE id = ?",
                                     (json.dumps(payload), number,
                                      f"{gitea.web_url(slug)}/pulls/{number}", rid))
                    await db.commit()
                    row = await _fetch_request(db, rid)
                finally:
                    await db.close()
        except Exception as e:                      # noqa: BLE001
            log.warning("Gitea mirror of merge request #%s failed: %s", rid, e)
    row, err = await yolo_decide(row)
    if err:
        log.warning("yolo approval of merge request #%s failed: %s", rid, err)
    return row


def _payload(row: dict) -> dict:
    try:
        return json.loads(row.get("payload") or "{}")
    except ValueError:
        return {}


async def merge_detail(slug: str, rid: int) -> dict:
    """What the operator reads before approving: parts, the PR's diffstat against its
    base, what was uncommitted at job start that the PR also commits, and a capped
    plain-text diff."""
    from . import gitsafe
    db = await get_db()
    try:
        row = await _own_request(db, rid, slug)
    finally:
        await db.close()
    if row.get("kind") != "merge":
        raise ValueError(f"request #{rid} is not a merge request")
    pl = _payload(row)
    gd = _gd(slug)
    out = {"id": rid, "status": row["status"], "branch": pl.get("branch"),
           "message": row["message"], "parts": pl.get("parts", []),
           "files": [], "diffstat": "", "diff": "", "base_dirty": [], "error": row.get("error")}
    ref, base = f"refs/heads/{pl.get('branch')}", pl.get("base_sha")
    if not (base and await gitsafe.rev(gd, ref)):
        out["gone"] = True              # decided and cleaned up
        return out
    # everything approval commits: from main as the job found it, so files that were
    # uncommitted when the job started (base_dirty) are in the list and the diff too
    start = f"{base}^" if pl.get("base_dirty") else base
    names = await _names(slug, start, ref)
    out["files"] = [f"{st} {p}" for p, st in sorted(names.items())]
    out["base_dirty"] = list(pl.get("base_dirty", []))
    _, stat, _ = await gitsafe.git(gd, "diff", "--stat", "--no-color", start, ref)
    out["diffstat"] = stat.strip()[-4000:]
    _, diff, _ = await gitsafe.git(gd, "diff", "--no-color", "--no-ext-diff", start, ref)
    out["diff"] = diff[:DETAIL_CAP] + ("\n... (diff cut)" if len(diff) > DETAIL_CAP else "")
    return out


def _job_keys(slug: str, pl: dict) -> list[str]:
    job6 = _BRANCH_RE.fullmatch(pl.get("branch", "")).group(1)
    keys = [f"{slug}--{job6}-merge"]
    for p in pl.get("parts", []):
        m = re.fullmatch(r"agent/[0-9a-f]{6}-(i\d{1,3})", p.get("branch") or "")
        if m:
            keys.append(f"{slug}--{job6}-{m.group(1)}")
    return keys


async def _cleanup_merge(slug: str, pl: dict) -> None:
    """Remove the job's copies, base ref and branches. Failures log, never raise."""
    from . import gitsafe, worktrees
    job6 = _BRANCH_RE.fullmatch(pl.get("branch", "")).group(1)
    for key in _job_keys(slug, pl):
        try:
            await worktrees.remove(key)
        except Exception as e:                      # noqa: BLE001
            log.warning("could not remove work copy %s: %s", key, e)
    gd = _gd(slug)
    try:
        await gitsafe.git(gd, "update-ref", "-d", f"refs/jav3/base/{job6}")
        for b in [pl.get("branch")] + [p.get("branch") for p in pl.get("parts", [])]:
            if b and re.fullmatch(r"agent/[a-z0-9-]+", b):
                await gitsafe.git(gd, "branch", "-D", "-q", b)
    except Exception as e:                          # noqa: BLE001
        log.warning("merge cleanup of %s in %s: %s", job6, slug, e)


async def _read_blob(gd: Path, sha: str) -> bytes:
    from . import gitsafe
    proc = await asyncio.create_subprocess_exec(
        "git", *gitsafe.HARDEN, f"--git-dir={gd}", "cat-file", "blob", sha,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=gitsafe._env(None))
    out, err = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"cannot read blob {sha}: {err.decode(errors='replace')[:200]}")
    return out


async def _record_error(db, rid: int, msg: str) -> None:
    await db.execute("UPDATE git_requests SET error = ? WHERE id = ?", (msg, rid))
    await db.commit()


async def _refuse(db, rid: int, msg: str):
    await _record_error(db, rid, msg)
    raise ValueError(msg)


async def _approve_merge(db, rid: int, row: dict) -> dict:
    from . import gitea, gitsafe, writes
    from .fsutil import safe_join
    slug = row["project_slug"]
    pl = _payload(row)
    branch, base = pl.get("branch", ""), pl.get("base_sha", "")
    if not _BRANCH_RE.fullmatch(branch) or not _HEX40.fullmatch(base):
        raise ValueError("this merge request is malformed")
    await ensure_repo(slug)
    gd, project = _gd(slug), _project_dir(slug)
    if gitea.enabled():
        try:
            await gitea.reconcile(slug)
            await gitea.sync_main(slug)
        except (RuntimeError, ValueError):
            pass
    M = await gitsafe.rev(gd, "HEAD")
    P = await gitsafe.rev(gd, f"refs/heads/{branch}")
    if not P:
        await _refuse(db, rid, "the PR branch is gone")
    if not M:
        await _refuse(db, rid, "the project has no commits yet")
    base_tree = (await gitsafe.git(gd, "rev-parse", f"{base}^{{tree}}", check=True))[1].strip()
    # the base's first parent is main as the job saw it (the base itself when clean)
    rc, _, _ = await gitsafe.git(gd, "merge-base", "--is-ancestor", base, M)
    h0 = base
    if rc != 0:
        h0 = (await gitsafe.git(gd, "rev-parse", f"{base}^", check=True))[1].strip()
    # 1. main moved: merge it in (no files touched)
    if M == h0:
        T = (await gitsafe.git(gd, "rev-parse", f"{P}^{{tree}}", check=True))[1].strip()
    else:
        rc, out, _ = await gitsafe.git(gd, "merge-tree", "--write-tree", "--name-only", M, P)
        lines = out.splitlines()
        if rc != 0:
            files = []
            for l in lines[1:]:
                if not l.strip():
                    break
                files.append(l.strip())
            await _refuse(db, rid, "main has moved since the job started and conflicts with "
                          "the PR in: " + (", ".join(files[:20]) or "(unknown)"))
        T = lines[0].strip()
    # what lands in the project dir: base -> T
    raw = (await gitsafe.git(gd, "diff-tree", "-r", "-z", "--raw", "--no-renames",
                             "--no-abbrev", base_tree, T, check=True))[1]
    toks = raw.split("\0")
    entries = []     # (status, new mode, new sha, path)
    i = 0
    while i + 1 < len(toks) and toks[i].startswith(":"):
        _om, nm, _osha, nsha, st = toks[i][1:].split()[:5]
        entries.append((st[0], nm, nsha, toks[i + 1]))
        i += 2
    paths = [e[3] for e in entries]
    for rel in paths:
        top = rel.split("/", 1)[0]
        if top in writes.PROTECTED or top.casefold() == ".context.json":
            await _refuse(db, rid, f"the PR changes {rel}, a protected path; it cannot be applied")
    # 2. the dir moved on the same paths: refuse, nothing is overwritten
    dir_tree = await gitsafe.tree_of(gd, project, base)
    moved = set(await _names(slug, base_tree, dir_tree)) if dir_tree != base_tree else set()
    clash = []
    for rel in sorted(moved & set(paths)):
        # the dir already holds exactly what T has (main moved there first): not a clash
        _, ds, _ = await gitsafe.git(gd, "rev-parse", "-q", "--verify", f"{dir_tree}:{rel}")
        _, ts, _ = await gitsafe.git(gd, "rev-parse", "-q", "--verify", f"{T}:{rel}")
        if ds.strip() != ts.strip():
            clash.append(rel)
    if clash:
        await _refuse(db, rid, "files in the project changed since the job started and the "
                      "PR changes them too: " + ", ".join(clash[:20]) +
                      ". Commit or move your edits, then approve again.")
    # 3. main = merge commit (CAS on M), then the files
    title = (row["message"].strip().splitlines() or ["orchestrator job"])[0][:120]
    _, new, _ = await gitsafe.git(
        gd, "commit-tree", T, "-p", M, "-p", P, "-m", f"Merge {branch}: {title}",
        env=gitsafe._who(), check=True)
    new = new.strip()
    rc, _, err = await gitsafe.git(gd, "update-ref", "-m", f"merge {branch}", "HEAD", new, M)
    if rc != 0:
        await _refuse(db, rid, f"main moved while approving; try again ({err.strip()[:120]})")
    applied, deleted, skipped, held = [], [], [], []
    # a file that rides every prompt (alwaysloaded.py) keeps its gate through a PR: a
    # change a turn that read untrusted content made is held for its own approval, as
    # it would have been written straight into the project; a PR never deletes one
    from . import alwaysloaded, taintpaths
    tainted = set(taintpaths.paths(slug))
    # yolo-full: nothing is held; a tainted always-loaded change lands with the PR
    from . import permissions
    yolo = await permissions.never_asks(row.get("conversation_id"))
    for key in _job_keys(slug, pl):
        tainted |= set(taintpaths.paths(key))
    for st, mode, sha, rel in entries:
        dest = safe_join(project, rel)
        loaded = alwaysloaded.is_loaded(slug, rel)
        if st == "D":
            if loaded:
                held.append(rel)
                continue
            if dest.is_file() or dest.is_symlink():
                dest.unlink()
            deleted.append(rel)
            continue
        if mode not in ("100644", "100755"):
            skipped.append(rel)
            continue
        data = await _read_blob(gd, sha)
        if loaded and rel in tainted and not yolo:
            try:
                await alwaysloaded.hold(slug, rel, data)
            except Exception as e:                  # noqa: BLE001 — too large: not written
                log.warning("PR change to always-loaded %s not held: %s", rel, e)
            held.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.jav3-merge")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o755 if mode == "100755" else 0o644)
        os.replace(tmp, dest)
        applied.append(rel)
    await gitsafe.git(gd, "reset", "-q", work_tree=project)     # index = HEAD, files stay
    error = None
    if skipped:
        error = "not written (symlinks/submodules): " + ", ".join(skipped[:10])
    if held:
        note = ("held for your approval (they ride every prompt): " + ", ".join(held[:10]))
        error = f"{error}; {note}" if error else note
    async with db.execute("SELECT github_remote FROM projects WHERE slug = ?", (slug,)) as cur:
        prow = await cur.fetchone()
    if prow and prow["github_remote"]:
        try:
            await push_to_remote(slug)
        except (RuntimeError, ValueError) as e:
            error = f"push failed: {e}"
    if gitea.enabled():
        try:
            await gitea.ensure_remote_repo(slug)
            gerr = await gitea.push_main(slug)
        except (RuntimeError, ValueError) as e:
            gerr = f"committed; main not pushed to Gitea: {gitea.scrub(str(e))}"
        if gerr:
            error = f"{error}; {gerr}" if error else gerr
    try:
        from . import edithistory
        await edithistory.after_apply(slug, None, {"applied": applied, "deleted": deleted},
                                      note=f"PR #{rid} approved")
    except Exception as e:                          # noqa: BLE001
        log.warning("edit history after merge: %s", e)
    await _cleanup_merge(slug, pl)
    if gitea.enabled():
        await gitea.close_merge_mirror(slug, pl, merged=True)
    await db.execute("UPDATE git_requests SET status = 'approved', commit_sha = ?, error = ?, "
                     "decided_at = datetime('now') WHERE id = ?", (new, error, rid))
    await db.commit()
    return await _fetch_request(db, rid)


async def create_request(slug: str, message: str, paths: list[str] | None = None) -> dict:
    if not message or not message.strip():
        raise ValueError("commit message must not be empty")
    await ensure_repo(slug)
    await flush_guest_writes(slug)      # this turn's write_file/edit_file, still in the VM
    _, porcelain, _ = await run_git(slug, "status", "--porcelain")
    tracked = await tracked_harness(slug)
    if tracked:
        # a tracked harness file shows as modified on every turn but is never
        # staged (unstage_harness): it is not something to commit
        porcelain = "\n".join(l for l in porcelain.splitlines() if l[3:].strip('"') not in tracked)
    if not porcelain.strip():
        raise ValueError("nothing to commit — the working tree is clean: every file in "
                         "the project already matches the last commit. Write or edit "
                         "a file first.")
    db = await get_db()
    try:
        # BUILD-08: a project has one pending commit request. Older ones named a
        # tree that no longer exists (one claimed a rename the commit could not
        # record, one with no paths would `git add -A` whatever is there at
        # approval), so the new request replaces them, and says so.
        async with db.execute(
                "SELECT id, message FROM git_requests WHERE project_slug = ? "
                "AND kind = 'commit' AND status = 'pending' ORDER BY id", (slug,)) as cur:
            old = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "INSERT INTO git_requests (project_slug, message, paths, conversation_id) "
            "VALUES (?, ?, ?, ?)",
            (slug, message.strip(), json.dumps(paths) if paths else None,
             _requesting_turn()))
        for o in old:
            await db.execute(
                "UPDATE git_requests SET status = 'rejected', error = ?, "
                "decided_at = datetime('now') WHERE id = ? AND status = 'pending'",
                (f"replaced by request #{cur.lastrowid}", o["id"]))
        await db.commit()
        row = await _fetch_request(db, cur.lastrowid)
        row["replaced"] = old
        row["harness_notice"] = await harness_notice(slug)
        return row
    finally:
        await db.close()


async def create_remote_request(slug: str, url: str) -> dict:
    """The agent's path to a remote: file a request; the operator's approval
    verifies, connects, and pushes. The agent itself never touches the remote."""
    url = (url or "").strip()
    if not valid_remote(url):
        raise ValueError("remote must look like https://github.com/<owner>/<repo>")
    db = await get_db()
    try:
        async with db.execute(
                "SELECT id FROM git_requests WHERE project_slug = ? AND "
                "kind = 'remote' AND status = 'pending'", (slug,)) as cur:
            dup = await cur.fetchone()
        if dup:
            raise ValueError(f"remote request #{dup['id']} is already pending "
                             "for this project — wait for the operator")
        cur = await db.execute(
            "INSERT INTO git_requests (project_slug, kind, message, conversation_id) "
            "VALUES (?, 'remote', ?, ?)", (slug, url, _requesting_turn()))
        await db.commit()
        return await _fetch_request(db, cur.lastrowid)
    finally:
        await db.close()


async def _approve_remote(db, rid: int, row: dict) -> dict:
    """Operator approved a remote-connect request: verify auth/reach, connect,
    and push any existing commits. A push failure records but the connect stands."""
    slug, url = row["project_slug"], row["message"]
    try:
        await verify_remote(slug, url)
    except RuntimeError as e:
        await db.execute("UPDATE git_requests SET error = ? WHERE id = ?",
                         (f"verify failed: {e}", rid))
        await db.commit()
        raise                       # stays pending — retryable after fixing token/URL
    await set_remote(slug, url)
    error = None
    rc, _, _ = await run_git(slug, "rev-parse", "--verify", "-q", "HEAD")
    if rc == 0:                     # commits exist -> put them up now
        try:
            await push_to_remote(slug)
        except (RuntimeError, ValueError) as e:
            error = f"connected, but push failed: {e}"
    await db.execute(
        "UPDATE git_requests SET status = 'approved', error = ?, "
        "decided_at = datetime('now') WHERE id = ?", (error, rid))
    await db.commit()
    return await _fetch_request(db, rid)


async def _own_request(db, rid: int, slug: str | None) -> dict:
    """The request, checked to belong to the project in the URL."""
    row = await _fetch_request(db, rid)
    if slug is not None and row["project_slug"] != slug:
        raise KeyError(f"no git request #{rid} in project {slug}")
    return row


async def approve_request(rid: int, slug: str | None = None) -> dict:
    db = await get_db()
    try:
        row = await _own_request(db, rid, slug)
        if row["status"] != "pending":
            raise ValueError(f"request #{rid} is {row['status']}, not pending")
        if row.get("kind") == "remote":
            return await _approve_remote(db, rid, row)
        if row.get("kind") == "push":
            from . import gitea
            return await gitea.approve_push(db, rid, row)
        if row.get("kind") == "merge":
            return await _approve_merge(db, rid, row)
        slug, message = row["project_slug"], row["message"]
        paths = json.loads(row["paths"]) if row["paths"] else None
        await ensure_repo(slug)
        from . import gitea
        if gitea.enabled():
            # take in anything merged in Gitea first, so the push after this
            # commit is a fast-forward (only HEAD + index move; files stay)
            try:
                await gitea.reconcile(slug)
                await gitea.sync_main(slug)
            except (RuntimeError, ValueError):
                pass
        if paths:
            await run_git(slug, "add", "--", *paths, check=True)
        else:
            await run_git(slug, "add", "-A", check=True)     # RUNTIME_FILES: info/exclude
        await unstage_harness(slug)         # ...which does not cover a harness file already tracked
        rc, _, _ = await run_git(slug, "diff", "--cached", "--quiet")
        rc_head, head, _ = await run_git(slug, "rev-parse", "--verify", "-q", "HEAD")
        if rc == 0 and rc_head == 0:
            # nothing left to commit: a pull request merged since (or an earlier
            # approval) already put these files into main. Close the request
            # instead of leaving it pending with a git error nobody can fix.
            await db.execute(
                "UPDATE git_requests SET status = 'approved', commit_sha = ?, error = ?, "
                "decided_at = datetime('now') WHERE id = ?",
                (head.strip(), "nothing new to commit: these changes are already in main", rid))
            await db.commit()
            return await _fetch_request(db, rid)
        try:
            await run_git(slug, "-c", "user.name=Jav3",
                          "-c", f"user.email={settings.git_author_email}",
                          "commit", "-m", message, check=True)
        except RuntimeError as e:
            # leave the request pending (retryable) but record what went wrong
            await db.execute("UPDATE git_requests SET error = ? WHERE id = ?",
                             (str(e), rid))
            await db.commit()
            raise
        _, sha, _ = await run_git(slug, "rev-parse", "HEAD", check=True)
        error = None
        async with db.execute("SELECT github_remote FROM projects WHERE slug = ?",
                              (slug,)) as cur:
            prow = await cur.fetchone()
        if prow and prow["github_remote"]:
            try:
                await push_to_remote(slug)
            except (RuntimeError, ValueError) as e:
                error = f"push failed: {e}"  # commit stands
        if gitea.enabled():
            # main mirrors to Gitea as the operator (protected against the bot)
            try:
                await gitea.ensure_remote_repo(slug)
                gerr = await gitea.push_main(slug)
            except (RuntimeError, ValueError) as e:
                gerr = f"committed; main not pushed to Gitea: {gitea.scrub(str(e))}"
            if gerr:
                error = f"{error}; {gerr}" if error else gerr
        await db.execute(
            "UPDATE git_requests SET status = 'approved', commit_sha = ?, error = ?, "
            "decided_at = datetime('now') WHERE id = ?", (sha.strip(), error, rid))
        await db.commit()
        return await _fetch_request(db, rid)
    finally:
        await db.close()


async def reject_request(rid: int, slug: str | None = None) -> dict:
    db = await get_db()
    try:
        row = await _own_request(db, rid, slug)
        if row["status"] != "pending":
            raise ValueError(f"request #{rid} is {row['status']}, not pending")
        if row.get("kind") == "push":
            from . import gitea
            return await gitea.reject_push(db, rid, row)
        if row.get("kind") == "merge":
            from . import gitea
            pl = _payload(row)
            await _cleanup_merge(row["project_slug"], pl)
            if gitea.enabled():
                await gitea.close_merge_mirror(row["project_slug"], pl, merged=False)
        await db.execute(
            "UPDATE git_requests SET status = 'rejected', decided_at = datetime('now') "
            "WHERE id = ?", (rid,))
        await db.commit()
        return await _fetch_request(db, rid)
    finally:
        await db.close()


async def list_requests(slug: str) -> list[dict]:
    from . import gitea
    await gitea.reconcile(slug)     # push rows follow merges/closes done in Gitea
    db = await get_db()
    try:
        async with db.execute(
                "SELECT * FROM git_requests WHERE project_slug = ? ORDER BY id DESC",
                (slug,)) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()

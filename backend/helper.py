"""The helper: a manual the operator can load into a chat, plus the exact
commands to pull the code the agent made.

`pull_info` answers "how do I get this project onto my machine"; the manual is
docs/SELF.md. A helper turn (ChatRequest.helper) gets both in that turn's
system prompt only."""
import asyncio
import contextvars
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import require_user
from .config import settings

router = APIRouter(prefix="/api/helper", tags=["helper"],
                   dependencies=[Depends(require_user)])

MANUAL = Path(__file__).resolve().parent.parent / "docs" / "SELF.md"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# the chat endpoint's base url for the request in flight, and conversation id ->
# base url for ONE helper turn (popped when that turn assembles its prompt)
request_base: contextvars.ContextVar = contextvars.ContextVar("helper_base", default="")
_pending: dict[int, str] = {}
MAX_MANUAL_CHARS = 24000
KEEP = ("getting your code", "logins and tokens", "terminal client")


def clean_base(base: str | None) -> str | None:
    """http(s)://host[:port] only (no path, query, credentials); else None."""
    if not base:
        return None
    try:
        u = urlsplit(base.strip())
        ok = (u.scheme in ("http", "https") and u.hostname and not u.username
              and u.path in ("", "/") and not u.query and not u.fragment)
        if not ok or not re.fullmatch(r"[A-Za-z0-9.:\-]+", u.hostname):
            return None
        port = f":{u.port}" if u.port else ""
        host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
        return f"{u.scheme}://{host}{port}"
    except ValueError:
        return None


def _git(repo: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _pending_pr(slug: str) -> dict | None:
    """The project's pending orchestrator PR (queue rows of kind merge), if any."""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True, timeout=2)
        try:
            r = con.execute("SELECT id, branch FROM git_requests WHERE project_slug = ? "
                            "AND kind = 'merge' AND status = 'pending' ORDER BY id DESC "
                            "LIMIT 1", (slug,)).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - /pull never fails on the queue's account
        return None
    return {"id": r[0], "branch": r[1]} if r and r[1] else None


def pull_info(slug: str | None, base: str) -> dict:
    base = clean_base(base) or ""
    out = {"project": slug, "clone": None, "pull": None,
           "auth": "git asks for your CNTRL username and password",
           "uncommitted": 0, "last_commit": None, "note": ""}
    if not slug or not _SLUG.match(slug):
        out["note"] = "no project is open in this chat, so there is nothing to pull"
        return out
    repo = settings.projects_dir / slug
    if not (repo / ".git").exists():
        out["note"] = f"no project named {slug}"
        return out
    if not base:
        out["note"] = "the server address is unknown; use http://<host>:<port>"
        base = "http://<host>:<port>"
    if not settings.git_serve_enabled:
        out["note"] = "git serving is turned off on this server (git_serve_enabled)"
    else:
        out["clone"] = f"git clone {base}/git/{slug}"
        out["pull"] = f"cd {slug} && git pull"
    # every untracked file, not one line per untracked folder: the same count the
    # project's git status (gitgate) and the terminal's sidebar show
    st = _git(repo, "status", "--porcelain", "--untracked-files=all")
    out["uncommitted"] = len([x for x in (st or "").splitlines() if x.strip()])
    log = _git(repo, "log", "-1", "--format=%h %s")
    out["last_commit"] = (log.strip() or None) if log else None
    if settings.gitea_enabled:
        # the same project on Gitea (approved commits push main there): what a
        # teammate with a Gitea account clones
        try:
            from . import gitea
            out["gitea_clone"] = f"git clone {gitea.public_url()}/{gitea.owner()}/{slug}.git"
        except Exception:  # noqa: BLE001 — the helper never fails on Gitea's account
            pass
    else:
        out["gitea"] = "not set up on this server (python -m backend.cli gitea-setup)"
    pr = _pending_pr(slug)
    if pr and settings.git_serve_enabled:
        job6 = pr["branch"].rsplit("-", 1)[-1]
        out["pr"] = {"branch": pr["branch"], "request_id": pr["id"],
                     "clone": f"git clone -b {pr['branch']} {base}/git/{slug} {slug}-pr{job6}",
                     "fetch": f"git fetch origin {pr['branch']} && git switch {pr['branch']}"}
    if out["uncommitted"]:
        out["note"] = (out["note"] + " " if out["note"] else "") + (
            "only committed work is served; the uncommitted files land once a "
            "commit request is approved")
    return out


def manual_text() -> str:
    try:
        return MANUAL.read_text()
    except OSError:
        return ""


def manual_for_prompt(text: str) -> str:
    """The whole manual, or when it is long the section list plus the sections
    on code, tokens and the terminal client."""
    if len(text) <= MAX_MANUAL_CHARS:
        return text
    parts = re.split(r"(?m)^(?=## )", text)
    names = [p.splitlines()[0][3:].strip() for p in parts if p.startswith("## ")]
    keep = [p for p in parts if p.startswith("## ")
            and p.splitlines()[0][3:].strip() in KEEP]
    return "Sections: " + ", ".join(names) + "\n\n" + "".join(keep)


def helper_block(slug: str | None, base: str | None) -> str:
    info = pull_info(slug, base or "")
    lines = [f"{k}: {v}" for k, v in info.items() if v not in (None, "")]
    return ("# The operator opened the helper\n"
            "For this turn the operator wants help with CNTRL itself: how to get "
            "their code, set up logins and tokens, use the terminal client. Answer "
            "from the manual below and give exact commands. You never create, see "
            "or rotate credentials; tell the operator the steps.\n\n"
            "## This chat's project\n" + "\n".join(lines) + "\n\n"
            "## Manual\n" + manual_for_prompt(manual_text()))


@router.get("/pull")
async def pull(request: Request, project: str, base: str | None = None):
    if base is not None and clean_base(base) is None:
        raise HTTPException(status_code=422, detail="base must be http(s)://host[:port]")
    # git runs in a thread: a slow repo must not stall every other request
    return await asyncio.to_thread(pull_info, project,
                                   clean_base(base) or str(request.base_url).rstrip("/"))


@router.get("/manual")
async def manual():
    return {"text": manual_text()}

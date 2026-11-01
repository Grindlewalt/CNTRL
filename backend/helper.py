"""The helper: a manual the operator can load into a chat, plus the exact
commands to pull the code the agent made.

`pull_info` answers "how do I get this project onto my machine"; the manual is
docs/SELF.md. A helper turn (ChatRequest.helper) gets both in that turn's
system prompt only."""
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


def pull_info(slug: str | None, base: str) -> dict:
    base = clean_base(base) or ""
    out = {"project": slug, "clone": None, "pull": None,
           "auth": "git asks for your Jav3 username and password",
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
    st = _git(repo, "status", "--porcelain")
    out["uncommitted"] = len([x for x in (st or "").splitlines() if x.strip()])
    log = _git(repo, "log", "-1", "--format=%h %s")
    out["last_commit"] = (log.strip() or None) if log else None
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
            "For this turn the operator wants help with Jav3 itself: how to get "
            "their code, set up logins and tokens, use the terminal client. Answer "
            "from the manual below and give exact commands. You never create, see "
            "or rotate credentials; tell the operator the steps.\n\n"
            "## This chat's project\n" + "\n".join(lines) + "\n\n"
            "## Manual\n" + manual_for_prompt(manual_text()))


@router.get("/pull")
async def pull(request: Request, project: str, base: str | None = None):
    if base is not None and clean_base(base) is None:
        raise HTTPException(status_code=422, detail="base must be http(s)://host[:port]")
    return pull_info(project, clean_base(base) or str(request.base_url).rstrip("/"))


@router.get("/manual")
async def manual():
    return {"text": manual_text()}

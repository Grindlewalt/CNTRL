"""Import an OpenClaw skill folder as a pinned, ungranted, untrusted snapshot.

Sources (all fetched HOST-side; the guest never fetches a skill):
  a local folder         /path/to/skill            (the folder holding SKILL.md)
  a git URL              https://host/owner/repo.git[#sub/dir]   shallow clone
  a GitHub link          https://github.com/owner/repo[/tree/<ref>/<dir>]  (also /blob/.../SKILL.md)
  an upload              a dropped folder or .zip (import_upload; recorded as upload:<name>)
  ClawHub                clawhub:<slug>[@version]  only with JARVIS_SKILL_IMPORT_CLAWHUB

The copy lands in skills/oc-<name>/ with a .pin.json (source, ref, sha256 of
every file, import time). What the importer never does: run an install hint,
keep an executable bit, follow a symlink, accept a binary, or update later —
a newer version is a deliberate re-import, which re-pins and revokes the grant.
The text is run through diffgate.scan as an ADVISORY tripwire (flags land as a
security event); the operator's review before granting is the real gate.

See backend/agent/tools/imported.py for what the registry does with the pin.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import diffgate, websec
from .agent.tools import imported
from .agent.tools.registry import _parse_md, compile_registry, load_registry
from .config import settings

MAX_FILES = 64
MAX_TOTAL_BYTES = 256 * 1024
MAX_DOWNLOAD_BYTES = 1024 * 1024
GIT_TIMEOUT = 120
_SUBDIR_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_CLAWHUB_RE = re.compile(r"^([a-z0-9][a-z0-9._-]{0,80}(?:/[a-z0-9][a-z0-9._-]{0,80})?)"
                         r"(?:@([0-9A-Za-z.+-]{1,40}))?$")


class SkillImportError(Exception):
    """Refused, with a reason the operator can act on."""


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def _allowed_hosts() -> set[str]:
    return {h.strip().lower() for h in settings.skill_import_allow_hosts.split(",")
            if h.strip()}


def _check_url(url: str) -> None:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host in _allowed_hosts() and p.scheme in ("http", "https"):
        return
    if p.scheme != "https":
        raise SkillImportError("git URLs must be https:// (or a host listed in "
                               "JARVIS_SKILL_IMPORT_ALLOW_HOSTS)")
    try:
        websec.is_safe_url(url)
    except websec.UnsafeURL as e:
        raise SkillImportError(f"refused: {e}") from None


def _git(args: list[str], cwd: Path | None = None) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(cwd or tempfile.gettempdir()),
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ALLOW_PROTOCOL": "http:https"}
    # no redirects (each hop would dodge the SSRF check), no symlinks checked
    # out, no submodules, no credential prompts, no hooks config from anywhere
    base = ["git", "-c", "http.followRedirects=false", "-c", "core.symlinks=false",
            "-c", "core.hooksPath=/dev/null", "-c", "credential.helper="]
    r = subprocess.run(base + args, cwd=cwd, env=env, capture_output=True,
                       text=True, timeout=GIT_TIMEOUT)
    if r.returncode != 0:
        raise SkillImportError(f"git {args[0]} failed: {r.stderr.strip()[-300:]}")
    return r.stdout.strip()


_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$", re.I)


def parse_github(link: str) -> tuple[str, str, str] | None:
    """A github.com link as (clone url, ref, subdir), or None when it is not
    one. Forms: owner/repo, owner/repo.git, .../tree/<ref>/<dir>, and
    .../blob/<ref>/<dir>/SKILL.md (the file's folder is the skill). The ref is
    the FIRST segment after tree/blob: a branch name with a slash is not
    guessed (use a tag, or the URL#subdir form with a clone URL)."""
    p = urlparse(link.strip())
    if p.scheme != "https" or (p.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    clone = f"https://github.com/{owner}/{repo}.git"
    if len(parts) == 2:
        return clone, "", ""
    if parts[2] not in ("tree", "blob") or len(parts) < 4:
        raise SkillImportError("that GitHub link points at something else; give the "
                               "repo or a folder of it (.../tree/<branch>/<folder>)")
    ref, rest = parts[3], parts[4:]
    if parts[2] == "blob":
        if not rest or rest[-1].lower() != "skill.md":
            raise SkillImportError("a GitHub file link must end in SKILL.md")
        rest = rest[:-1]
    return clone, ref, "/".join(rest)


def _clone(url: str, subdir: str, dest: Path, ref: str = "") -> str:
    """Shallow-clone url into dest (sparse to `subdir` when given, at the
    branch or tag `ref` when given); returns the commit id."""
    args = ["clone", "--depth", "1", "--no-tags", "--single-branch",
            "--no-recurse-submodules"]
    if ref:
        if _COMMIT_RE.match(ref):
            raise SkillImportError("a commit id cannot be shallow-cloned: link a "
                                   "branch or a tag instead")
        if not _REF_RE.match(ref) or ".." in ref:
            raise SkillImportError(f"bad branch or tag '{ref}'")
        args += ["--branch", ref]
    if subdir:
        args += ["--filter=blob:none", "--sparse"]
    _git(args + ["--", url, str(dest)])
    if subdir:
        _git(["sparse-checkout", "set", "--", subdir], cwd=dest)
    return _git(["rev-parse", "HEAD"], cwd=dest)


def _fetch_git(source: str, tmp: Path) -> tuple[Path, dict]:
    url, _, subdir = source.partition("#")
    subdir = subdir.strip("/")
    ref = ""
    gh = parse_github(url) if not subdir else None
    if gh:
        url, ref, subdir = gh
    if subdir and (not _SUBDIR_RE.match(subdir) or ".." in subdir.split("/")):
        raise SkillImportError(f"bad sub-directory '{subdir}'")
    _check_url(url)
    dest = tmp / "repo"
    commit = _clone(url, subdir, dest, ref) if ref else _clone(url, subdir, dest)
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return (dest / subdir if subdir else dest), {"source": source, "ref": commit}


def _safe_client() -> httpx.Client:
    def check(request: httpx.Request) -> None:
        try:
            websec.is_safe_url(str(request.url))
        except websec.UnsafeURL as e:
            raise SkillImportError(f"refused: {e}") from None
    return httpx.Client(timeout=30, follow_redirects=True,
                        event_hooks={"request": [check]})


def _extract_zip(data: bytes, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillImportError("ClawHub did not return a zip") from None
    total = 0
    for info in zf.infolist():
        name = info.filename
        parts = Path(name).parts
        if name.startswith("/") or ".." in parts or ":" in name:
            raise SkillImportError(f"unsafe path in archive: {name}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise SkillImportError(f"symlink in archive: {name}")
        if info.is_dir():
            continue
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise SkillImportError("archive too large")
        out = dest / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(zf.read(info))


def _fetch_clawhub(spec: str, tmp: Path) -> tuple[Path, dict]:
    if not settings.skill_import_clawhub:
        raise SkillImportError("ClawHub import is off (JARVIS_SKILL_IMPORT_CLAWHUB). "
                               "Prefer a vetted git repo or folder.")
    m = _CLAWHUB_RE.match(spec)
    if not m:
        raise SkillImportError(f"bad ClawHub slug '{spec}'")
    slug, version = m.group(1), m.group(2)
    base = settings.skill_import_clawhub_url.rstrip("/")
    with _safe_client() as c:
        r = c.get(f"{base}/api/v1/skills/{slug}")
        if r.status_code != 200:
            raise SkillImportError(f"ClawHub has no skill '{slug}' ({r.status_code})")
        info = r.json()
        mod = info.get("moderation") or {}
        if mod.get("isSuspicious") or mod.get("isMalwareBlocked") or \
                mod.get("verdict") not in (None, "clean"):
            raise SkillImportError(f"ClawHub flags '{slug}' as "
                                   f"{mod.get('verdict') or 'suspicious'} — refused")
        version = version or ((info.get("latestVersion") or {}).get("version"))
        params = {"slug": slug, **({"version": version} if version else {})}
        r = c.get(f"{base}/api/v1/download", params=params)
        if r.status_code != 200:
            raise SkillImportError(f"ClawHub download failed ({r.status_code})")
        if len(r.content) > MAX_DOWNLOAD_BYTES:
            raise SkillImportError("ClawHub download too large")
        if "json" in r.headers.get("content-type", ""):
            j = r.json()
            raise SkillImportError(
                "this ClawHub skill lives on GitHub — import it as "
                f"https://github.com/{j.get('repo', '?')}#{j.get('path', '')}")
    dest = tmp / "zip"
    _extract_zip(r.content, dest)
    if not (dest / "SKILL.md").is_file():
        tops = [p for p in dest.iterdir() if (p / "SKILL.md").is_file()]
        if len(tops) != 1:
            raise SkillImportError("archive has no SKILL.md")
        dest = tops[0]
    return dest, {"source": f"clawhub:{slug}", "ref": version or ""}


def _fetch(source: str, tmp: Path) -> tuple[Path, dict]:
    source = source.strip()
    if source.startswith("clawhub:"):
        return _fetch_clawhub(source[len("clawhub:"):], tmp)
    if re.match(r"^[a-z][a-z0-9+.-]*://", source, re.I) or source.startswith("git@"):
        if not source.lower().startswith(("https://", "http://")):
            raise SkillImportError("only https:// git URLs are accepted")
        return _fetch_git(source, tmp)
    path = Path(source).expanduser()
    if not path.is_dir():
        raise SkillImportError(f"no such folder: {source}")
    return path, {"source": str(path.resolve()), "ref": ""}


# ---------------------------------------------------------------------------
# Vet + vendor
# ---------------------------------------------------------------------------
def _collect(src: Path) -> dict[str, bytes]:
    """Every file under src as bytes, refusing anything a skill should not be."""
    files: dict[str, bytes] = {}
    total = 0
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src).as_posix()
        if rel == ".git" or rel.startswith(".git/") or rel == imported.PIN_FILE:
            continue
        if p.is_symlink():
            raise SkillImportError(f"symlink refused: {rel}")
        if p.is_dir():
            continue
        if not p.is_file():
            raise SkillImportError(f"not a regular file: {rel}")
        data = p.read_bytes()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise SkillImportError(f"not UTF-8 text: {rel}") from None
        total += len(data)
        files[rel] = data
        if len(files) > MAX_FILES:
            raise SkillImportError(f"more than {MAX_FILES} files")
        if total > MAX_TOTAL_BYTES:
            raise SkillImportError(f"larger than {MAX_TOTAL_BYTES // 1024} KiB")
    if "SKILL.md" not in files:
        raise SkillImportError("no SKILL.md at the top of the folder")
    return files


_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.S | re.M)


def scan(files: dict[str, bytes]) -> list[dict]:
    """diffgate over the bundle. Markdown is not a code type diffgate reads, so
    a .md file's fenced blocks are scanned as shell — that is what a skill's
    code blocks become when the model follows them."""
    flags: list[dict] = []
    for rel, data in files.items():
        text = data.decode("utf-8")
        if rel.lower().endswith(".md"):
            chunks = [(f"{rel}#block{i + 1}.sh", b) for i, b in enumerate(_FENCE.findall(text))]
        else:
            chunks = [(rel, text)]
        for path, chunk in chunks:
            for f in diffgate.scan("", chunk, path):
                flags.append({"file": path, "trigger": f["trigger"],
                              "detail": {k: v for k, v in f["detail"].items()
                                         if k != "lines"}})
    return flags


def _dir_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def vendor(src: Path, provenance: dict, name: str | None = None,
           replace: bool = False) -> dict:
    files = _collect(src)
    try:
        meta = _parse_md(src / "SKILL.md")
    except Exception:  # noqa: BLE001 — malformed YAML is the author's problem
        meta = None
    if meta is None:
        raise SkillImportError("SKILL.md needs frontmatter with name and description")
    reg_name = (name or str(meta["name"])).strip()
    if not imported.NAME_RE.match(reg_name):
        raise SkillImportError(f"'{reg_name}' is not a valid tool name; pass --name")
    slug = _dir_slug(reg_name)
    dest = settings.skills_dir / f"{imported.IMPORT_PREFIX}{slug}"
    if dest.exists() and not replace:
        raise SkillImportError(f"{dest.name} is already imported (re-import to update)")
    taken = {e["name"]: e for e in load_registry()
             if Path(e["source"]).parent != dest and not e.get("clash")}
    if reg_name in taken:
        raise SkillImportError(f"the name '{reg_name}' is taken by the "
                               f"{taken[reg_name]['kind']} '{reg_name}'; pass --name")

    flags = scan(files)
    if dest.exists():
        shutil.rmtree(dest)
    for rel, data in files.items():
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        out.write_bytes(data)
        out.chmod(0o644)                     # never an executable bit
    pin = {"origin": imported.ORIGIN, "name": reg_name,
           "source": provenance.get("source", ""), "ref": provenance.get("ref", ""),
           "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "files": {rel: imported.sha256_file(dest / rel) for rel in sorted(files)},
           "scan": flags}
    (dest / imported.PIN_FILE).write_text(json.dumps(pin, indent=2))
    imported.set_grant(dest, False)          # a (re-)import is never granted
    if flags:
        imported.alert("skill_import_flag", f"import:{dest.name}:{pin['imported_at']}",
                       f"Imported skill {dest.name}: {len(flags)} diff-gate flag(s) "
                       "— read it before granting",
                       {"skill": dest.name, "source": pin["source"],
                        "flags": flags[:20]})
    entries = compile_registry()
    e = next(x for x in entries if Path(x["source"]).parent == dest)
    return {"slug": dest.name, "name": reg_name, "source": pin["source"],
            "ref": pin["ref"], "files": len(files), "flags": flags,
            "requirements": e.get("requirements", []),
            "install_hints": e.get("install_hints", []),
            "blocked": e.get("blocked", "") or e.get("clash", "")}


def import_skill(source: str, name: str | None = None, replace: bool = False) -> dict:
    if not source or not source.strip():
        raise SkillImportError("give a folder, a git URL or clawhub:<slug>")
    with tempfile.TemporaryDirectory(prefix="jav3-skill-") as tmp:
        src, prov = _fetch(source, Path(tmp))
        if not src.is_dir():
            raise SkillImportError("that source has no such folder")
        return vendor(src, prov, name=name, replace=replace)


# ---------------------------------------------------------------------------
# Upload (a dropped folder or .zip from the operator's own computer)
# ---------------------------------------------------------------------------
def _upload_path(name: str) -> Path:
    """A relative path from an upload, or a refusal. Names come from the
    browser, so nothing in them is trusted."""
    n = name.replace("\\", "/")
    parts = n.split("/")
    if (not n or n.startswith("/") or ":" in n or "\x00" in n
            or any(x in ("", ".", "..") for x in parts[:-1]) or parts[-1] in ("", ".", "..")):
        raise SkillImportError(f"unsafe path in upload: {name[:80]}")
    return Path(*parts)


def _write_entries(entries: list[tuple[str, bytes]], dest: Path) -> None:
    if len(entries) > MAX_FILES:
        raise SkillImportError(f"more than {MAX_FILES} files")
    total = 0
    for name, data in entries:
        rel = _upload_path(name)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SkillImportError(f"larger than {MAX_TOTAL_BYTES // 1024} KiB")
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)


def _zip_entries(data: bytes) -> list[tuple[str, bytes]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillImportError("that is not a readable .zip") from None
    infos = [i for i in zf.infolist() if not i.is_dir()]
    out: list[tuple[str, bytes]] = []
    total = 0
    for info in zf.infolist():
        mode = (info.external_attr >> 16) & 0o170000
        if mode not in (0, 0o100000, 0o040000):
            raise SkillImportError(f"symlink or special entry in zip: {info.filename[:80]}")
    if len(infos) > MAX_FILES:
        raise SkillImportError(f"more than {MAX_FILES} files")
    for info in infos:
        _upload_path(info.filename)
        if info.filename.startswith("__MACOSX/") or info.filename.endswith(".DS_Store"):
            continue
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise SkillImportError(f"larger than {MAX_TOTAL_BYTES // 1024} KiB")
        with zf.open(info) as f:               # the header's size can lie: cap the read too
            body = f.read(MAX_TOTAL_BYTES + 1)
        if len(body) > MAX_TOTAL_BYTES:
            raise SkillImportError(f"larger than {MAX_TOTAL_BYTES // 1024} KiB")
        out.append((info.filename, body))
    return out


def import_upload(files: list[tuple[str, bytes]], name: str | None = None,
                  replace: bool = False) -> dict:
    """Import what the operator dropped: a single .zip, or the files of a
    folder as (relative path, bytes). SKILL.md may sit at the root or inside
    one top folder. The pin records upload:<folder or zip name>, never a path."""
    if not files:
        raise SkillImportError("nothing was uploaded")
    if len(files) == 1 and files[0][0].lower().endswith(".zip"):
        label = files[0][0].replace("\\", "/").rsplit("/", 1)[-1]
        entries = _zip_entries(files[0][1])
    else:
        entries = list(files)
        tops = {e[0].replace("\\", "/").split("/")[0] for e in entries if "/" in e[0]}
        label = tops.pop() if len(tops) == 1 and all("/" in e[0] for e in entries) else "files"
    label = re.sub(r"[^\w .@+-]", "_", label)[:80] or "files"
    with tempfile.TemporaryDirectory(prefix="jav3-skill-") as tmp:
        root = Path(tmp) / "up"
        root.mkdir()
        _write_entries(entries, root)
        src = root
        if not (root / "SKILL.md").is_file():
            tops = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                    and (p / "SKILL.md").is_file()]
            if len(tops) != 1:
                raise SkillImportError("no SKILL.md at the top of the upload")
            src = tops[0]
        return vendor(src, {"source": f"upload:{label}", "ref": ""}, name=name,
                      replace=replace)

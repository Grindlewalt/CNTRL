"""Environment requests: an agent proposes a whole new image variant for its
project (docs/agent-requests-contract.md, section 1).

The agent can only file a pending row: one environment_requests row plus one
package_catalogue row per package (carrying environment_id). Approval needs
the operator and a resolved dry-run for every package; it creates the variant,
approves the rows into it and moves ONLY this project onto it, through a copy
of its profile (or its own profile, updated in place, when it already has one
made for it). Other projects stay where they were."""
from __future__ import annotations

import re

from . import packages, profiles, security
from .packages import PackageError

MAX_PACKAGES = 30
MAX_PENDING_PER_PROJECT = 3
REASON_MAX = 1000
MEM_MIN, MEM_MAX = 256, 16384

_STRING_RE = re.compile(r"^(apt|pip|npm)[\s:]+(\S+)$")


def _split_version(manager: str, token: str) -> tuple[str, str | None]:
    if manager == "pip" and "==" in token:
        n, _, v = token.partition("==")
        return n, v
    if manager == "npm" and "@" in token[1:]:
        i = token.rindex("@")
        return token[:i], token[i + 1:]
    if manager == "apt" and "=" in token:
        n, _, v = token.partition("=")
        return n, v
    return token, None


def parse_packages(items) -> list[dict]:
    """Objects or forgiving strings -> [{manager, package, version}], each
    validated, duplicates collapsed. PackageError (safe to show the agent)."""
    if not isinstance(items, list) or not items:
        raise PackageError("packages must be a non-empty list")
    if len(items) > MAX_PACKAGES:
        raise PackageError(f"at most {MAX_PACKAGES} packages per environment")
    out: list[dict] = []
    seen: set[tuple] = set()
    for it in items:
        if isinstance(it, str):
            m = _STRING_RE.match(it.strip())
            if not m:
                raise PackageError(f"cannot read package {it!r}: use "
                                   "`apt name`, `pip name==1.2` or `npm name@1.2.3`")
            manager = m.group(1)
            name, ver = _split_version(manager, m.group(2))
        elif isinstance(it, dict):
            manager, name, ver = it.get("manager"), it.get("package"), it.get("version")
        else:
            raise PackageError("each package is an object or a string")
        name = packages.validate_name(manager, name)
        ver = packages.validate_version(manager, ver)
        key = (manager, name, ver or "")
        if key in seen:
            continue
        seen.add(key)
        out.append({"manager": manager, "package": name, "version": ver})
    return out


async def get(db, env_id: int) -> dict | None:
    async with db.execute("SELECT * FROM environment_requests WHERE id = ?",
                          (env_id,)) as cur:
        r = await cur.fetchone()
    return await _shape(db, dict(r)) if r is not None else None


async def list_rows(db, status: str | None = None,
                    project: str | None = None) -> list[dict]:
    q, where, args = "SELECT * FROM environment_requests", [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if project:
        where.append("project_slug = ?")
        args.append(project)
    if where:
        q += " WHERE " + " AND ".join(where)
    async with db.execute(q + " ORDER BY id DESC LIMIT 200", args) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return [await _shape(db, r) for r in rows]


async def _shape(db, r: dict) -> dict:
    async with db.execute("SELECT * FROM package_catalogue WHERE environment_id = ? "
                          "ORDER BY id", (r["id"],)) as cur:
        pk = [dict(x) for x in await cur.fetchall()]
    now = await profiles.for_slug(db, r["project_slug"])
    out = {k: r[k] for k in ("id", "project_slug", "conversation_id", "name",
                             "from_variant", "min_mem_mb", "reason", "status", "kind",
                             "created_at", "decided_by", "decided_at")}
    out["packages"] = [{k: p[k] for k in ("id", "manager", "package", "version_req",
                                          "resolved_version", "integrity",
                                          "canonical_command", "status")} for p in pk]
    out["resolved"] = (r["kind"] == "use"
                       or (bool(pk) and all(p["resolved_version"] for p in pk)))
    out["summary"] = _summary(r)
    out["profile_now"] = {"id": now["id"], "name": now["name"]}
    # what approving WOULD do; once decided, `profile` says what it did
    out["plan"] = await _plan(db, r, now) if r["status"] == "pending" else None
    out["profile"] = None
    out["build"] = None
    if r["status"] == "approved":
        if r["profile_id"] is not None:
            p = await profiles.get(db, r["profile_id"])
            if p is not None:
                out["profile"] = {"id": p["id"], "name": p["name"]}
        async with db.execute(
                "SELECT version, status, CASE WHEN status = 'failed' "
                "THEN substr(build_log, -300) END FROM image_versions WHERE variant = ? "
                "ORDER BY version DESC LIMIT 1", (r["name"],)) as cur:
            b = await cur.fetchone()
        out["build"] = ({"version": b[0], "status": b[1], "error": b[2]}
                        if b is not None else None)
    return out


def _summary(r: dict) -> str:
    """One line for the cards: what approving moves the project onto."""
    if r["kind"] != "use":
        return (f"Build image `{r['name']}` from `{r['from_variant']}` and move "
                f"{r['project_slug']} onto it (its own VM)")
    from .vm import boxes
    mem = boxes.mem_floor(r["name"])
    return (f"Move {r['project_slug']} onto image `{r['name']}` (its own VM"
            + (f", ≥{mem} MB)" if mem else ")"))


def _own_profile(cur: dict, slug: str, projects: list[str]) -> bool:
    return (not cur["is_default"] and projects == [slug]
            and cur["name"].startswith(f"{slug} · "))


async def _projects_on(db, profile_id: int) -> list[str]:
    for p in await profiles.list_all(db):
        if p["id"] == profile_id:
            return p["projects"]
    return []


async def _plan(db, r: dict, now: dict) -> str:
    slug, name = r["project_slug"], r["name"]
    sep = ("" if now["separate_box"] else
           f"; {slug} gets its own box (it is on the shared one now)")
    if _own_profile(now, slug, await _projects_on(db, now["id"])):
        how = f"{slug} boots on it (its own profile {now['name']} is updated)"
    else:
        how = f"{slug} boots on it (a copy of profile {now['name']})"
    if r["kind"] == "use":
        return f"no build: {how}{sep}; other projects unchanged"
    return (f"builds image `{name}` from `{r['from_variant']}`, then {how}{sep}; "
            "other projects unchanged")


async def file_request(db, *, project: str | None, conversation_id: int | None,
                       name: str, from_variant: str, packages: list,
                       min_mem_mb: int | None, reason: str,
                       use: str | None = None) -> dict:
    """Validate and store a pending environment request. Raises PackageError.
    `use` = an existing image to move onto (kind 'use': nothing is built or
    installed, so it is allowed with package requests off)."""
    from .vm import images
    from . import packages as pk
    if not project:
        raise PackageError("an environment request needs an active project")
    if use:
        return await _file_use(db, project, conversation_id, use, name,
                               from_variant, packages, min_mem_mb, reason)
    if await pk.project_allows_requests(db, project) is False:
        raise PackageError("this project's profile does not allow package requests; "
                           "ask the operator (ask_user) to turn on Packages for it, "
                           "or ask for an existing image with use=")
    try:
        name = images.check_variant_name(name)
        from_variant = images.check_variant_name(from_variant or "main")
    except images.RecipeError as e:
        raise PackageError(str(e))
    if name in images.builtin_recipes():
        raise PackageError(f"`{name}` is a builtin image name")
    if await images.variant_exists(db, name):
        raise PackageError(f"an image variant `{name}` already exists")
    async with db.execute("SELECT id, status FROM environment_requests WHERE name = ? "
                          "AND status IN ('pending', 'approved')", (name,)) as cur:
        dup = await cur.fetchone()
    if dup is not None:
        raise PackageError(f"`{name}` is already the name of environment request "
                           f"#{dup[0]} ({dup[1]})")
    if not await images.variant_exists(db, from_variant):
        raise PackageError(f"no image variant `{from_variant}` to build from")
    if min_mem_mb is not None:
        if (isinstance(min_mem_mb, bool) or not isinstance(min_mem_mb, int)
                or not MEM_MIN <= min_mem_mb <= MEM_MAX):
            raise PackageError(f"min_mem_mb must be an integer {MEM_MIN}..{MEM_MAX}")
    reason = (reason or "").strip()
    if not reason:
        raise PackageError("reason is required: say what the environment is for")
    if len(reason) > REASON_MAX:
        raise PackageError(f"reason is longer than {REASON_MAX} chars")
    pkgs = parse_packages(packages)
    async with db.execute("SELECT COUNT(*) FROM environment_requests WHERE "
                          "project_slug = ? AND status = 'pending'", (project,)) as cur:
        if (await cur.fetchone())[0] >= MAX_PENDING_PER_PROJECT:
            raise PackageError(f"this project already has {MAX_PENDING_PER_PROJECT} "
                               "pending environment requests; wait for the operator")
    cur = await db.execute(
        "INSERT INTO environment_requests(project_slug, conversation_id, name, "
        "from_variant, min_mem_mb, reason) VALUES (?,?,?,?,?,?)",
        (project, conversation_id, name, from_variant, min_mem_mb, reason))
    env_id = cur.lastrowid
    await db.commit()
    try:
        for p in pkgs:
            await pk.file_request(
                db, manager=p["manager"], package=p["package"], version=p["version"],
                install_command=None, reason=reason, project=project,
                conversation_id=conversation_id, source="agent",
                target_variant=name, environment_id=env_id)
    except PackageError:
        await db.execute("DELETE FROM package_catalogue WHERE environment_id = ?", (env_id,))
        await db.execute("DELETE FROM environment_requests WHERE id = ?", (env_id,))
        await db.commit()
        raise
    await security.raise_event(
        db, kind="environment_requested", severity="info", project=project,
        summary=f"requested image `{name}` from `{from_variant}` with "
                f"{len(pkgs)} package{'s' if len(pkgs) != 1 else ''}",
        detail={"environment_id": env_id, "name": name, "from_variant": from_variant,
                "min_mem_mb": min_mem_mb, "reason": reason, "packages": pkgs,
                "conversation_id": conversation_id})
    try:
        images.builder.kick_resolve()
    except Exception:  # noqa: BLE001 — resolution is retried from the review card
        pass
    return await get(db, env_id)


async def _file_use(db, project, conversation_id, use, name, from_variant,
                    pkgs, min_mem_mb, reason) -> dict:
    from .vm import images, placement
    if name or pkgs or min_mem_mb is not None or from_variant not in (None, "", "main"):
        raise PackageError("use= takes only use and reason: leave name, from, "
                           "packages and min_mem_mb out (or drop use= to build a new image)")
    try:
        use = images.check_variant_name(use)
    except images.RecipeError as e:
        raise PackageError(str(e))
    if not await images.variant_exists(db, use):
        raise PackageError(f"no image `{use}` exists; ask for a new one with "
                           "name, packages and reason instead")
    reason = (reason or "").strip()
    if not reason:
        raise PackageError("reason is required: say why this project needs that image")
    if len(reason) > REASON_MAX:
        raise PackageError(f"reason is longer than {REASON_MAX} chars")
    prof = await profiles.for_slug(db, project)
    eff = placement.resolve(await placement.get_setting(db, project), prof, project)
    if eff["mode"] == "own" and eff["image"] == use:
        raise PackageError(f"this project already runs on image `{use}`; nothing to "
                           "request")
    if eff["mode"] == "shared" and use == "main":
        raise PackageError("this project already runs on the default image `main` "
                           "(the shared box); nothing to request")
    async with db.execute("SELECT COUNT(*) FROM environment_requests WHERE "
                          "project_slug = ? AND status = 'pending'", (project,)) as cur:
        if (await cur.fetchone())[0] >= MAX_PENDING_PER_PROJECT:
            raise PackageError(f"this project already has {MAX_PENDING_PER_PROJECT} "
                               "pending environment requests; wait for the operator")
    cur = await db.execute(
        "INSERT INTO environment_requests(project_slug, conversation_id, name, "
        "from_variant, reason, kind) VALUES (?,?,?,?,?,'use')",
        (project, conversation_id, use, use, reason))
    env_id = cur.lastrowid
    await db.commit()
    await security.raise_event(
        db, kind="environment_requested", severity="info", project=project,
        summary=f"asked to move onto existing image `{use}`",
        detail={"environment_id": env_id, "name": use, "kind": "use",
                "reason": reason, "conversation_id": conversation_id})
    return await get(db, env_id)


async def _set_status(db, env_id: int, status: str, decided_by: str, **cols) -> None:
    sets = "".join(f", {k} = ?" for k in cols)
    await db.execute("UPDATE environment_requests SET status = ?, decided_by = ?, "
                     f"decided_at = datetime('now'){sets} WHERE id = ?",
                     (status, decided_by, *cols.values(), env_id))
    await db.commit()


async def _pending(db, env_id: int) -> dict:
    async with db.execute("SELECT * FROM environment_requests WHERE id = ?",
                          (env_id,)) as cur:
        r = await cur.fetchone()
    if r is None:
        raise LookupError("no such environment request")
    if r["status"] != "pending":
        raise PackageError(f"environment request #{env_id} is already {r['status']}")
    return dict(r)


async def approve(db, env_id: int, *, decided_by: str, by_operator: bool) -> dict:
    from .vm import images
    r = await _pending(db, env_id)
    async with db.execute("SELECT * FROM package_catalogue WHERE environment_id = ? "
                          "ORDER BY id", (env_id,)) as cur:
        rows = [dict(x) for x in await cur.fetchall()]
    use = r["kind"] == "use"
    name, slug = r["name"], r["project_slug"]
    if use:
        if not await images.variant_exists(db, name):
            raise PackageError(f"image `{name}` no longer exists; reject this request")
    else:
        waiting = [x["package"] for x in rows if not x["resolved_version"]]
        if waiting or not rows:
            raise PackageError("not resolved yet: the dry-run must pin every package "
                               f"before approval ({', '.join(waiting) or 'no packages'})")
        try:
            await images.create_variant(db, name, r["from_variant"], [],
                                        min_mem_mb=r["min_mem_mb"])
        except images.RecipeError as e:
            raise PackageError(str(e))
        for x in rows:
            await packages.approve(db, x["id"], target_variant=name,
                                   decided_by=decided_by, by_operator=by_operator,
                                   via_environment=True)
    cur = await profiles.for_slug(db, slug)
    copied_from = None
    # an image only applies to a project's OWN box (placement.resolve ignores
    # box_image on the shared one); the memory floor is applied at allocation
    # (boxes.mem_floor), so box_mem_mb needs no change here
    flip = not cur["separate_box"]
    if _own_profile(cur, slug, await _projects_on(db, cur["id"])):
        prof = await profiles.update(db, cur["id"], {**{k: cur[k] for k in profiles.FIELDS},
                                              "box_image": name, "separate_box": 1},
                                     actor=decided_by, by_operator=by_operator)
    else:
        copied_from = cur["id"]
        base = f"{slug} · {name}"
        pname, n = base, 1
        while await profiles.by_name(db, pname):
            n += 1
            pname = f"{base} ({n})"
        body = {k: cur[k] for k in profiles.FIELDS}
        body.update(name=pname, box_image=name, separate_box=1)
        prof = await profiles.create(db, body, actor=decided_by, by_operator=by_operator)
        await profiles.assign(db, slug, prof["id"], actor=decided_by,
                              by_operator=by_operator)
    await _set_status(db, env_id, "approved", decided_by, profile_id=prof["id"],
                      profile_copied_from=copied_from)
    await security.settle(db, "environment_requested", "environment_id", env_id)
    await security.raise_event(
        db, kind="environment_approved", severity="warn", project=slug,
        summary=(f"approved moving {slug} onto image `{name}`" if use else
                 f"approved image `{name}` from `{r['from_variant']}`")
                + f"; {slug} now runs in its own box on profile {prof['name']!r}",
        detail={"environment_id": env_id, "name": name, "kind": r["kind"],
                "separate_box_set": flip, "profile_id": prof["id"],
                "profile_copied_from": copied_from,
                "packages": [f"{x['manager']} {x['package']}" for x in rows]},
        actor=security.OPERATOR if by_operator else None)
    return await get(db, env_id)


async def reject(db, env_id: int, *, reason: str = "", decided_by: str,
                 by_operator: bool) -> dict:
    r = await _pending(db, env_id)
    async with db.execute("SELECT id FROM package_catalogue WHERE environment_id = ? "
                          "AND status = 'pending'", (env_id,)) as cur:
        ids = [x[0] for x in await cur.fetchall()]
    for pid in ids:
        await packages.reject(db, pid, reason=reason, decided_by=decided_by,
                              via_environment=True, quiet=True)
    await _set_status(db, env_id, "rejected", decided_by)
    await security.settle(db, "environment_requested", "environment_id", env_id)
    await security.raise_event(
        db, kind="environment_rejected", severity="info", project=r["project_slug"],
        summary=f"rejected image request `{r['name']}`",
        detail={"environment_id": env_id, "reason": (reason or "")[:500]},
        actor=security.OPERATOR if by_operator else None)
    return await get(db, env_id)


async def yolo_approve(env_id: int) -> dict | None:
    """FULL mode (permissions.auto_approve said yes): approve as decided_by="yolo".
    A build request first needs its packages resolved (one dry-run); one that
    cannot be resolved stays pending for the operator. A new image is then built
    in the background. Returns the row, or None when nothing could be decided."""
    from .config import settings
    from .db import get_db
    from .vm import images
    db = await get_db()
    try:
        row = await get(db, env_id)
        if row is None or row["status"] != "pending":
            return row
        if row["kind"] != "use" and any(not p["resolved_version"] for p in row["packages"]):
            await db.close()
            db = None
            try:
                await images.builder.resolve_pending()
            except Exception:  # noqa: BLE001 — stays pending, resolvable from the card
                return None
            db = await get_db()
        try:
            row = await approve(db, env_id, decided_by="yolo", by_operator=False)
        except (PackageError, images.RecipeError):
            return None
    finally:
        if db is not None:
            await db.close()
    if row.get("kind") != "use" and settings.vm_boxes_enabled \
            and not images.builder.lock.locked():
        try:
            await images.builder.build(row["name"])
        except Exception:  # noqa: BLE001 — the version row / security event carry the error
            pass
    return row

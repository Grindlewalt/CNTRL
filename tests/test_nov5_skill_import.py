"""Skill import by upload (zip / dropped folder) and by ordinary GitHub link."""
import io
import json
import stat
import zipfile

import httpx
import pytest

from backend import devicetokens, skillimport as si
from backend.auth import hash_password
from backend.config import settings
from backend.db import get_db, init_db
from backend.main import app

SKILL = "---\nname: demo-skill\ndescription: does a thing\n---\nBody.\n"


@pytest.fixture
async def env(tmp_env):
    await init_db()
    return tmp_env


def _zip(entries, attrs=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in entries.items():
            zi = zipfile.ZipInfo(n)
            if attrs and n in attrs:
                zi.external_attr = attrs[n] << 16
            z.writestr(zi, d)
    return b.getvalue()


def _leftovers():
    return sorted(p.name for p in settings.skills_dir.glob("oc-*")) if settings.skills_dir.exists() else []


def _pin():
    return json.loads((settings.skills_dir / "oc-demo-skill" / ".pin.json").read_text())


async def test_zip_with_top_folder_and_root(env):
    r = si.import_upload([("demo.zip", _zip({"demo/SKILL.md": SKILL, "demo/a.txt": "x"}))])
    assert r["files"] == 2 and r["source"] == "upload:demo.zip"
    assert _pin()["source"] == "upload:demo.zip"
    r = si.import_upload([("root.zip", _zip({"SKILL.md": SKILL}))], replace=True)
    assert r["source"] == "upload:root.zip"


async def test_folder_upload(env):
    r = si.import_upload([("demo/SKILL.md", SKILL.encode()), ("demo/ref/n.md", b"hi")])
    assert r["files"] == 2 and _pin()["source"] == "upload:demo"


@pytest.mark.parametrize("entries,attrs", [
    ({"../evil/SKILL.md": SKILL}, None),
    ({"/abs/SKILL.md": SKILL}, None),
    ({"SKILL.md": SKILL, "link": "SKILL.md"}, {"link": stat.S_IFLNK | 0o777}),
    ({"SKILL.md": SKILL, **{f"f{i}.txt": "x" for i in range(si.MAX_FILES)}}, None),
    ({"SKILL.md": SKILL, "big.txt": "x" * (si.MAX_TOTAL_BYTES + 1)}, None),
])
async def test_bad_zips_refused_and_leave_nothing(env, entries, attrs):
    with pytest.raises(si.SkillImportError):
        si.import_upload([("bad.zip", _zip(entries, attrs))])
    assert _leftovers() == []


async def test_bad_folder_paths_refused(env):
    for n in ("../SKILL.md", "/SKILL.md", "a/../../SKILL.md"):
        with pytest.raises(si.SkillImportError):
            si.import_upload([(n, SKILL.encode())])
    assert _leftovers() == []


async def test_github_forms():
    c = "https://github.com/o/r.git"
    assert si.parse_github("https://github.com/o/r") == (c, "", "")
    assert si.parse_github("https://github.com/o/r/") == (c, "", "")
    assert si.parse_github("https://github.com/o/r.git") == (c, "", "")
    assert si.parse_github("https://github.com/o/r/tree/main/skills/x/") == (c, "main", "skills/x")
    assert si.parse_github("https://github.com/o/r/blob/v1/skills/x/SKILL.md") == (c, "v1", "skills/x")
    assert si.parse_github("https://gitlab.com/o/r") is None
    with pytest.raises(si.SkillImportError):
        si.parse_github("https://github.com/o/r/issues/3")


def test_clone_uses_branch_and_refuses_commit(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(si, "_git", lambda a, cwd=None: calls.append(a) or "abc")
    si._clone("https://github.com/o/r.git", "", tmp_path / "d", "v1")
    assert calls[0][calls[0].index("--branch") + 1] == "v1"
    with pytest.raises(si.SkillImportError, match="branch or a tag"):
        si._clone("https://github.com/o/r.git", "", tmp_path / "d", "a" * 40)


def test_fetch_git_turns_a_tree_link_into_clone(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(si, "_check_url", lambda u: None)
    monkeypatch.setattr(si, "_clone", lambda u, s, d, r="": seen.update(u=u, s=s, r=r) or "deadbeef")
    _, prov = si._fetch_git("https://github.com/o/r/tree/main/skills/x", tmp_path)
    assert seen == {"u": "https://github.com/o/r.git", "s": "skills/x", "r": "main"}
    assert prov["ref"] == "deadbeef"


@pytest.fixture
async def op(env):
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                         ("operator", hash_password("hunter2")))
        await db.commit()
    finally:
        await db.close()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": "operator", "password": "hunter2"})
        yield c


async def test_route_imports_a_zip(op):
    r = await op.post("/api/skills/import/upload",
                      files=[("files", ("demo.zip", _zip({"SKILL.md": SKILL}), "application/zip"))])
    assert r.status_code == 200 and r.json()["source"] == "upload:demo.zip"


async def test_route_imports_a_folder_and_refuses_oversize(op):
    r = await op.post("/api/skills/import/upload",
                      files=[("files", ("demo/SKILL.md", SKILL, "text/plain"))])
    assert r.status_code == 200 and r.json()["source"] == "upload:demo"
    big = b"x" * (2 * 1024 * 1024 + 10)
    r = await op.post("/api/skills/import/upload", files=[("files", ("a.zip", big))])
    assert r.status_code == 413


async def test_route_is_guarded(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as anon:
        f = [("files", ("a.zip", b"x"))]
        assert (await anon.post("/api/skills/import/upload", files=f)).status_code == 401
        db = await get_db()
        try:
            tok = await devicetokens.mint("phone", by="t")
        finally:
            await db.close()
        secret = tok["token"] if isinstance(tok, dict) else tok
        r = await anon.post("/api/skills/import/upload", files=f,
                            headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 401


async def test_cross_origin_cookie_post_is_403(op):
    r = await op.post("/api/skills/import/upload", files=[("files", ("a.zip", b"x"))],
                      headers={"Origin": "http://evil.example"})
    assert r.status_code == 403

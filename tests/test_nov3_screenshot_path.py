"""screenshot path=: a project file captured with file://, no server; escapes
refused; taint only when the page reached the egress proxy. Chromium mocked."""
import asyncio
import contextvars
import importlib.util
import io
import os
import sys
import types

import pytest

from backend.agent import imageresult
from backend.config import settings

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location(
        "t_shot_path", settings.base_dir / "tools" / "screenshot" / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


shot = _load()


@pytest.fixture
def box(monkeypatch, tmp_path):
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    (proj / "project.md").write_text("# Demo\n")
    monkeypatch.setattr(settings, "in_guest", True, raising=False)
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(shot, "SHOT_DIR", str(tmp_path / "shots"))
    tc = types.ModuleType("backend.turnctx")
    tc.op_id = contextvars.ContextVar("op", default="op-1")
    monkeypatch.setitem(sys.modules, "backend.turnctx", tc)
    from backend.agent.tools import toolctx

    async def slug():
        return "demo"
    monkeypatch.setattr(toolctx, "active_slug", slug)
    notes = []

    async def taint(source):
        notes.append(source)
        return True
    monkeypatch.setattr(shot, "taint_note", taint)
    monkeypatch.setattr(shot.shutil, "which",
                        lambda n: "/usr/bin/chromium" if n == "chromium" else None)
    monkeypatch.setattr(shot, "proxy_conns", lambda h, p: set())
    calls = []

    async def fake_exec(argv, *, timeout, env=None, cwd=None, watch_oom=False):
        calls.append(argv)
        out = next(a.split("=", 1)[1] for a in argv if a.startswith("--screenshot="))
        buf = io.BytesIO()
        Image.new("RGB", (800, 600), (30, 90, 200)).save(buf, "PNG")
        with open(out, "wb") as f:
            f.write(buf.getvalue())
        return shot.Ran(0)
    monkeypatch.setattr(shot, "_exec", fake_exec)
    return types.SimpleNamespace(proj=proj, notes=notes, calls=calls, tmp=tmp_path)


def _shot(path):
    return asyncio.run(shot.run(mode="url", path=path))


def test_path_uses_file_url_and_no_taint(box):
    (box.proj / "brief.html").write_text("<h1>hi</h1>")
    out = _shot("brief.html")
    assert imageresult.split(out)[1] is not None
    assert box.calls[0][-1] == "file://" + os.path.realpath(box.proj / "brief.html")
    assert box.notes == [] and "tainted" not in out


def test_path_with_spaces_is_quoted(box):
    (box.proj / "my brief.html").write_text("x")
    _shot("my brief.html")
    assert " " not in box.calls[0][-1]


def test_dotdot_and_absolute_refused(box):
    (box.tmp / "secret.txt").write_text("s")
    for bad in ("../../secret.txt", str(box.tmp / "secret.txt"), "nope.html"):
        assert _shot(bad).startswith("error"), bad
    assert box.calls == []


def test_symlink_out_refused(box):
    (box.tmp / "secret.html").write_text("s")
    os.symlink(box.tmp / "secret.html", box.proj / "link.html")
    assert _shot("link.html").startswith("error")
    assert box.calls == []


def test_symlink_inside_is_fine(box):
    (box.proj / "a.html").write_text("x")
    os.symlink(box.proj / "a.html", box.proj / "b.html")
    assert imageresult.split(_shot("b.html"))[1] is not None


def test_remote_subresource_taints(box, monkeypatch):
    (box.proj / "p.html").write_text("<img src='https://x.example/a.png'>")
    seq = iter([set(), {("10.0.0.2", 4000)}])
    monkeypatch.setattr(shot, "proxy_conns", lambda h, p: next(seq))
    out = _shot("p.html")
    assert box.notes == ["screenshot:path"]
    assert "tainted" in out


def test_remote_withheld_when_taint_fails(box, monkeypatch):
    (box.proj / "p.html").write_text("x")
    seq = iter([set(), {("10.0.0.2", 4000)}])
    monkeypatch.setattr(shot, "proxy_conns", lambda h, p: next(seq))

    async def fail(source):
        return False
    monkeypatch.setattr(shot, "taint_note", fail)
    out = _shot("p.html")
    assert out.startswith("error") and imageresult.split(out)[1] is None



def test_an_orchestrator_item_screenshots_the_file_in_its_own_copy(box, monkeypatch):
    # an item on a branch writes in its copy, not the project's dir: path= must look there
    from backend.agent.tools import toolctx
    copy = box.tmp / "worktrees" / "demo--abc123-i1"
    copy.mkdir(parents=True)
    (copy / "brief.html").write_text("<h1>item</h1>")

    async def key():
        return "demo--abc123-i1"
    monkeypatch.setattr(toolctx, "require_files", key)
    monkeypatch.setattr(toolctx, "files_dir", lambda k: copy)
    out = _shot("brief.html")
    assert imageresult.split(out)[1] is not None, out
    assert box.calls[-1][-1] == "file://" + os.path.realpath(copy / "brief.html")
    assert "no such file" in _shot("../demo/project.md") or "inside" in _shot("../demo/project.md")

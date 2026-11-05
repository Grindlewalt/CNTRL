"""nov4: the `publish` tool (waits in the Queue, live with a link in yolo, refused
with the reason) and the install pieces (unit file, installer step, doctor stage)."""
from pathlib import Path

import pytest

from backend import doctor, pages, permissions, runtime, secrets as secrets_mod
from backend.agent.tools import registry
from backend.config import settings
from backend.db import init_db

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
async def proj(tmp_env, monkeypatch):
    await init_db()
    monkeypatch.setattr(settings, "pages_base_url", "http://pages.test")
    d = settings.projects_dir / "demo"
    d.mkdir(parents=True)
    (d / "project.md").write_text("# demo\n")
    (d / "page.html").write_text("<p>x</p>")
    return d


async def _publish(**kw):
    runtime.active_project.set("demo")
    return await registry.dispatch("publish", kw)


async def _yolo(monkeypatch):
    async def yes(*a, **k):
        return True
    monkeypatch.setattr(permissions, "auto_approve", yes)


async def test_waits_in_the_queue(proj):
    out = await _publish(path="page.html", name="P")
    assert "waits in the Queue" in out
    assert len(await pages.list_pages("pending")) == 1


async def test_yolo_gives_the_link(proj, monkeypatch):
    await _yolo(monkeypatch)
    out = await _publish(path="page.html", name="P")
    assert "http://pages.test/p/" in out and out.startswith("published")


async def test_errors_and_secret_refusal(proj, monkeypatch):
    assert (await _publish(path="missing", name="P")).startswith("error: no such")
    await _yolo(monkeypatch)
    monkeypatch.setattr(secrets_mod, "load", lambda: {"TOKEN_X": "abcdef123456"})
    (proj / "leak.txt").write_text("abcdef123456")
    out = await _publish(path="leak.txt", name="L")
    assert out.startswith("error: publishing refused") and "TOKEN_X" in out
    assert "abcdef123456" not in out


def test_unit_and_installer_step():
    unit = (ROOT / "scripts" / "jarvis-pages.service").read_text()
    assert "backend.pages_server" in unit and "--workers" not in unit
    inst = (ROOT / "scripts" / "install.sh").read_text()
    assert "jarvis-pages.service" in inst and "$UNIT-pages.service" in inst


def test_doctor_has_an_optional_pages_stage(monkeypatch):
    st = doctor.pages_stage()
    assert st["id"] == "pages" and st["optional"] is True
    monkeypatch.setattr(settings, "pages_enabled", False)
    assert doctor.pages_stage()["ok"] is None

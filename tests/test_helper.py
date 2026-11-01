"""The helper: pull commands for a project, the manual, and the per-turn block."""
import subprocess

import pytest
from fastapi import HTTPException

from backend import helper
from backend.chat import ChatRequest
from backend.config import settings


def _vcs(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t",
                    "-c", "user.email=t@t", *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "projects_dir", tmp_path)
    d = tmp_path / "demo"
    d.mkdir()
    _vcs(d, "init", "-q")
    (d / "a.txt").write_text("a")
    _vcs(d, "add", "-A")
    _vcs(d, "commit", "-qm", "first thing")
    (d / "b.txt").write_text("b")
    return d


def test_pull_info(repo):
    i = helper.pull_info("demo", "http://10.0.0.82:8000")
    assert i["clone"] == "git clone http://10.0.0.82:8000/git/demo"
    assert i["pull"] == "cd demo && git pull"
    assert i["uncommitted"] == 1
    assert i["last_commit"].endswith("first thing")
    assert "committed" in i["note"]


def test_pull_info_off_and_missing(repo, monkeypatch):
    monkeypatch.setattr(settings, "git_serve_enabled", False)
    i = helper.pull_info("demo", "http://h:1")
    assert i["clone"] is None and "turned off" in i["note"]
    assert "no project" in helper.pull_info("nope", "http://h:1")["note"]
    assert "no project" in helper.pull_info(None, "http://h:1")["note"]
    assert "no project" in helper.pull_info("../x", "http://h:1")["note"]


def test_base_validation():
    assert helper.clean_base("http://10.0.0.82:8000/") == "http://10.0.0.82:8000"
    assert helper.clean_base("https://a.example") == "https://a.example"
    for bad in ("ftp://h", "http://u:p@h", "http://h/path", "http://h?x=1",
                "javascript:alert(1)", "", "http://h:99999"):
        assert helper.clean_base(bad) is None


async def test_endpoints(repo):
    class Req:
        base_url = "http://box:8000/"
    got = await helper.pull(Req(), "demo")
    assert got["clone"].startswith("git clone http://box:8000/git/")
    with pytest.raises(HTTPException):
        await helper.pull(Req(), "demo", base="http://h/evil")
    assert "## getting your code" in (await helper.manual())["text"]


def test_helper_block_and_request_flag(repo):
    block = helper.helper_block("demo", "http://box:8000")
    assert "opened the helper" in block and "## getting your code" in block
    assert "git clone http://box:8000/git/demo" in block
    assert ChatRequest(message="x").helper is False
    assert ChatRequest(message="x", helper=True).helper is True


def test_long_manual_keeps_key_sections():
    names = ("architecture", "getting your code", "memory", "terminal client")
    text = "# t\n\n" + "".join(f"## {n}\nbody {n}\n" + "x" * 9000 + "\n" for n in names)
    out = helper.manual_for_prompt(text)
    assert "body getting your code" in out and "body terminal client" in out
    assert "body memory" not in out and out.startswith("Sections: architecture")


def test_pull_info_counts_every_untracked_file_and_names_gitea(repo, monkeypatch):
    # an untracked folder counts its files (the sidebar's git section does), not one line
    (repo / "src").mkdir()
    (repo / "src" / "x.js").write_text("x")
    (repo / "src" / "y.js").write_text("y")
    assert helper.pull_info("demo", "http://h:1")["uncommitted"] == 3
    monkeypatch.setattr(settings, "gitea_enabled", False)
    assert "not set up" in helper.pull_info("demo", "http://h:1")["gitea"]
    from backend import gitea
    monkeypatch.setattr(settings, "gitea_enabled", True)
    monkeypatch.setattr(gitea, "public_url", lambda: "http://10.0.0.82:3000")
    monkeypatch.setattr(gitea, "owner", lambda: "op")
    i = helper.pull_info("demo", "http://h:1")
    assert i["gitea_clone"] == "git clone http://10.0.0.82:3000/op/demo.git" and "gitea" not in i

"""nov2: orchestrator items on branches (work keys, copies, integration)."""
import io
import subprocess
import tarfile

import pytest

from backend import gitgate, gitsafe, worktrees
from backend.config import settings
from backend.db import init_db
from backend.vm import workspace_xfer


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout


def _tar(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in files.items():
            data = text.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
async def proj(tmp_env):
    await init_db()
    d = settings.projects_dir / "demo"
    d.mkdir(parents=True)
    (d / "project.md").write_text("# demo\n")
    (d / "a.txt").write_text("one\ntwo\nthree\n")
    await gitgate.ensure_repo("demo")
    sh(d, "git", "add", "-A")
    sh(d, "git", "commit", "-qm", "init")
    return d


async def test_key_writes_land_in_the_copy(proj):
    base = await worktrees.base_snapshot("demo", "J1")
    w = await worktrees.create("demo", "J1", "i1", base)
    res = await workspace_xfer.apply_guest_writes(w.key, _tar({"new.txt": "hi\n"}))
    assert "new.txt" in res["applied"]
    assert (w.dir / "new.txt").read_text() == "hi\n"
    assert not (proj / "new.txt").exists()


async def test_key_gates_still_refuse(proj, tmp_env):
    from backend import secrets as secrets_mod
    base = await worktrees.base_snapshot("demo", "J2")
    w = await worktrees.create("demo", "J2", "i1", base)
    res = await workspace_xfer.apply_guest_writes(
        w.key, _tar({".git/config": "x", "ok.txt": "fine\n"}))
    assert not (w.dir / ".git").exists()
    assert (w.dir / "ok.txt").exists()
    assert res["refused"] or res["failed"] or ".git/config" not in res["applied"]
    del secrets_mod


async def test_build_workspace_ships_the_copy(proj):
    base = await worktrees.base_snapshot("demo", "J3")
    w = await worktrees.create("demo", "J3", "i1", base)
    (w.dir / "only_here.txt").write_text("x")
    with tarfile.open(fileobj=io.BytesIO(workspace_xfer.build_merged_tar(w.key)),
                      mode="r:gz") as t:
        names = {m.name for m in t.getmembers()}
    assert "only_here.txt" in names and "a.txt" in names
    assert not (proj / "only_here.txt").exists()


async def test_gitsafe_import():
    assert gitsafe.snapshot

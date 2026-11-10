"""The rename to CNTRL keeps what exists working: JARVIS_ settings still count
(a CNTRL_ one wins), an install's old folders are used until the installer
moves them, /cli/jav3 serves what /cli/cntrl serves, /api/health names the
version, and the installer's upgrade moves an old install once."""
import os
import subprocess
from pathlib import Path

import httpx
import pytest

from backend import config
from backend.config import Settings

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.sh"


# --- settings ------------------------------------------------------------------------

def test_a_jarvis_setting_still_counts_and_a_cntrl_one_wins(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("CNTRL_", "JARVIS_")):
            monkeypatch.delenv(k)
    env = tmp_path / "env"
    env.write_text("JARVIS_LAN_PORT=8101\nJARVIS_PAGES_PORT=9001\nCNTRL_PAGES_PORT=9002\n")
    monkeypatch.setenv("JARVIS_JWT_TTL_HOURS", "9")
    monkeypatch.setenv("CNTRL_JWT_TTL_HOURS", "5")
    monkeypatch.setenv("JARVIS_PAGES_BIND", "10.0.0.9")
    s = Settings(_env_file=env)
    assert s.lan_port == 8101                 # old name in the env file
    assert s.pages_port == 9002               # both in the file: the new one
    assert s.jwt_ttl_hours == 5               # both in the environment: the new one
    assert s.pages_bind == "10.0.0.9"         # old name in the environment
    # the environment still beats the file, whatever the names
    monkeypatch.setenv("JARVIS_LAN_PORT", "8202")
    assert Settings(_env_file=env).lan_port == 8202


def test_env_get_reads_the_new_name_then_the_old(monkeypatch):
    monkeypatch.delenv("CNTRL_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_GITHUB_TOKEN", "old")
    assert config.env_get("GITHUB_TOKEN") == "old"
    monkeypatch.setenv("CNTRL_GITHUB_TOKEN", "new")
    assert config.env_get("GITHUB_TOKEN") == "new"
    monkeypatch.delenv("CNTRL_GITHUB_TOKEN")
    monkeypatch.delenv("JARVIS_GITHUB_TOKEN")
    assert config.env_get("GITHUB_TOKEN", "d") == "d"


def test_the_old_folders_are_used_when_only_they_hold_an_install(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    new_cfg, old_cfg = tmp_path / ".config/cntrl", tmp_path / ".config/jarvis"
    new_st, old_st = tmp_path / ".local/share/cntrl", tmp_path / ".local/share/jarvis"
    # a fresh machine: the new names
    assert config._default_config_dir() == new_cfg
    assert config._default_state_dir() == new_st
    # an install from before the rename: its folders, as they are
    old_cfg.mkdir(parents=True)
    (old_cfg / "env").write_text("JARVIS_LAN_PORT=8000\n")
    (old_st / "data").mkdir(parents=True)
    (old_st / "data" / "jarvis.db").write_bytes(b"")
    # the terminal client keeps its files in ~/.config/cntrl too (and an early build
    # put its venv in ~/.local/share/cntrl): neither is a server install
    new_cfg.mkdir(parents=True)
    (new_cfg / "credentials.json").write_text("{}")
    (new_st / "venv").mkdir(parents=True)
    assert config._default_config_dir() == old_cfg
    assert config._default_state_dir() == old_st
    # once the installer moved them, the new ones
    (new_cfg / "env").write_text("CNTRL_LAN_PORT=8000\n")
    (new_st / "data").mkdir()
    (new_st / "data" / "jarvis.db").write_bytes(b"")
    assert config._default_config_dir() == new_cfg
    assert config._default_state_dir() == new_st


def test_a_checkout_remembers_its_instance_under_either_name(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / ".jarvis-instance").write_text("JARVIS_INSTANCE=t\nJARVIS_CONFIG_DIR=/srv/old\n")
    assert config._instance_config_dir() == "/srv/old"
    (tmp_path / ".cntrl-instance").write_text("CNTRL_INSTANCE=t\nCNTRL_CONFIG_DIR=/srv/new\n")
    assert config._instance_config_dir() == "/srv/new"


# --- the server ----------------------------------------------------------------------

async def test_cli_cntrl_and_cli_jav3_serve_the_same_bytes_and_health_has_the_version(tmp_env):
    from backend.db import init_db
    from backend.main import app
    from backend.version import __version__
    await init_db()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cntrl.lan:8000") as anon:
        new, old = await anon.get("/cli/cntrl"), await anon.get("/cli/jav3")
        assert new.status_code == old.status_code == 200
        assert new.content == old.content and b"textual" in new.content
        h = (await anon.get("/api/health")).json()
        assert h == {"ok": True, "version": __version__}
        a, b = await anon.get("/cli/cntrl-browser.zip"), await anon.get("/cli/jav3-browser.zip")
        assert a.status_code == b.status_code == 200 and a.content == b.content


def test_the_doctor_names_the_version(monkeypatch):
    from backend import doctor
    from backend.version import __version__
    monkeypatch.setattr(doctor, "stages", lambda: [])
    assert doctor.summary([])["version"] == __version__


def test_gitea_setup_retires_the_unit_from_before_the_rename(tmp_path, monkeypatch):
    from backend import gitea_setup
    monkeypatch.setattr(gitea_setup.Path, "home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(gitea_setup, "_systemctl", lambda *a: calls.append(a) or 0)
    monkeypatch.setattr(gitea_setup, "_healthy", lambda: True)
    p = gitea_setup.paths()
    p["unit"].parent.mkdir(parents=True)
    old = p["unit"].with_name(gitea_setup.old_unit_name())
    old.write_text("[Unit]\n")
    assert p["unit"].name.startswith("cntrl-gitea-") and old.name.startswith("jav3-gitea-")
    gitea_setup.ensure_service(gitea_setup.Plan(dry=False), p)
    assert not old.exists() and p["unit"].is_file()
    assert ("disable", "--now", old.name) in calls and ("enable", p["unit"].name) in calls


# --- the installer's upgrade ---------------------------------------------------------

def _bash() -> str | None:
    # install.sh needs bash >= 4; macOS /bin/bash is 3.2
    import shutil
    for b in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash")):
        if b and os.path.exists(b):
            out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"],
                                 capture_output=True, text=True).stdout.strip()
            if out.isdigit() and int(out) >= 4:
                return b
    return None


BASH = _bash()
OLD_UNITS = ("jarvis.service", "jarvis-pages.service", "jarvis-backup.service",
             "jarvis-backup.timer")


def _old_install(home: Path) -> None:
    """What an install from before the rename leaves in a home directory, plus
    the new terminal client's credentials already in ~/.config/cntrl."""
    cfg = home / ".config/jarvis"
    cfg.mkdir(parents=True, mode=0o700)
    (cfg / "env").write_text("JARVIS_DEEPSEEK_API_KEY=sk-test\nJARVIS_MDNS=false\n"
                             "JARVIS_LAN_PORT=8000\nCNTRL_LAN_PORT=8000\n")
    (cfg / "env").chmod(0o600)
    (cfg / "secrets.json").write_text("{}")
    st = home / ".local/share/jarvis"
    (st / "data").mkdir(parents=True)
    (st / "data" / "jarvis.db").write_bytes(b"db")
    (st / "memory").mkdir()
    (st / "memory" / "note.md").write_text("a note")
    units = home / ".config/systemd/user"
    units.mkdir(parents=True)
    for u in OLD_UNITS:
        (units / u).write_text("[Unit]\n")
    (units / "jav3-gitea-3000.service").write_text(
        "[Unit]\nDescription=Gitea 1.24 for Jav3 (port 3000)\n"
        f"[Service]\nWorkingDirectory={st}/gitea\n")
    (units / "jarvis.service.d").mkdir()
    (units / "jarvis.service.d" / "mem.conf").write_text("[Service]\nMemoryMax=2G\n")
    (home / ".config/cntrl").mkdir()
    (home / ".config/cntrl" / "credentials.json").write_text("{}")


def _run_rename(home: Path):
    stub = home / "bin"
    stub.mkdir(exist_ok=True)
    log = home / "systemctl.log"
    (stub / "systemctl").write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    (stub / "systemctl").chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{stub}:/usr/bin:/bin:/usr/sbin:/sbin"}
    r = subprocess.run([BASH, "-u", str(SCRIPT), "--rename-only"], capture_output=True,
                       text=True, env=env, timeout=60)
    calls = log.read_text().splitlines() if log.exists() else []
    log.unlink(missing_ok=True)
    return r, calls


def _tree(home: Path) -> dict:
    return {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*"))
            if p.is_file() and p.name != "systemctl"}


@pytest.mark.skipif(BASH is None, reason="no bash >= 4")
def test_the_installer_moves_an_old_install_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _old_install(home)
    r, calls = _run_rename(home)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "unbound variable" not in out
    # the old units: stopped, disabled and gone; the new ones installed and enabled
    for u in OLD_UNITS:
        assert f"--user disable --now {u}" in calls
        assert not (home / ".config/systemd/user" / u).exists()
    units = home / ".config/systemd/user"
    for u in ("cntrl.service", "cntrl-pages.service", "cntrl-backup.service",
              "cntrl-backup.timer"):
        assert (units / u).is_file(), u
    assert f"WorkingDirectory={ROOT}" in (units / "cntrl.service").read_text()
    assert (units / "cntrl.service.d" / "mem.conf").is_file()      # the drop-in came along
    assert "--user enable cntrl.service" in calls and "--user restart cntrl" in calls
    # Gitea: the same unit under its new name, started again only once its
    # state has moved, and pointing at it
    assert not (units / "jav3-gitea-3000.service").exists()
    gitea = (units / "cntrl-gitea-3000.service").read_text()
    assert "for CNTRL (port 3000)" in gitea
    assert f"WorkingDirectory={home}/.local/share/cntrl/gitea" in gitea
    stop = calls.index("--user disable --now jav3-gitea-3000.service")
    start = calls.index("--user enable --now cntrl-gitea-3000.service")
    assert stop < start
    assert all(calls.index(f"--user disable --now {u}") < start for u in OLD_UNITS)
    # the config dir moved in beside the client's credentials, nothing overwritten
    cfg = home / ".config/cntrl"
    assert not (home / ".config/jarvis").exists()
    assert (cfg / "credentials.json").is_file() and (cfg / "secrets.json").is_file()
    env = (cfg / "env").read_text()
    assert "CNTRL_DEEPSEEK_API_KEY=sk-test" in env and "CNTRL_MDNS=false" in env
    assert "JARVIS_" not in env and env.count("CNTRL_LAN_PORT=") == 1
    assert "CNTRL_INSTANCE_NAME=jav3" in env                # paired computers still find it
    assert "JARVIS_DEEPSEEK_API_KEY=sk-test" in (cfg / "env.bak").read_text()
    assert oct((cfg / "env").stat().st_mode & 0o777) == "0o600"
    assert oct((cfg / "env.bak").stat().st_mode & 0o777) == "0o600"
    # the state dir moved; the old path is a symlink to it, so absolute paths
    # inside the state (Gitea's app.ini, a box disk's backing file) still resolve
    st = home / ".local/share/cntrl"
    old = home / ".local/share/jarvis"
    assert st.is_dir() and not st.is_symlink()
    assert old.is_symlink() and old.resolve() == st.resolve()
    assert (st / "data" / "jarvis.db").read_bytes() == b"db"
    assert (st / "memory" / "note.md").read_text() == "a note"

    # a second run: nothing to do, nothing touched
    before = _tree(home)
    r2, calls2 = _run_rename(home)
    assert r2.returncode == 0 and "nothing to do" in r2.stdout, r2.stdout + r2.stderr
    assert calls2 == [] and _tree(home) == before


@pytest.mark.skipif(BASH is None, reason="no bash >= 4")
def test_the_installer_never_moves_over_an_existing_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _old_install(home)
    (home / ".config/cntrl" / "env").write_text("CNTRL_LAN_PORT=8000\n")
    r, _ = _run_rename(home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "both hold a server config" in r.stdout
    assert (home / ".config/jarvis" / "env").read_text().startswith("JARVIS_DEEPSEEK")
    assert (home / ".config/cntrl" / "env").read_text().startswith("CNTRL_LAN_PORT")


@pytest.mark.skipif(BASH is None, reason="no bash >= 4")
def test_check_reports_an_old_install_instead_of_advising_a_fresh_one(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _old_install(home)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    r = subprocess.run([BASH, "-u", str(SCRIPT), "--check"], capture_output=True,
                       text=True, env=env, timeout=60)
    out = r.stdout
    assert "an install from before the rename to CNTRL (jarvis.service" in out
    assert "touch" not in out and "service not installed" not in out
    assert (home / ".config/jarvis" / "env").exists()          # --check moves nothing


def test_a_setting_setup_writes_replaces_its_old_twin(tmp_path, monkeypatch):
    from backend import gitea_setup
    env = tmp_path / "env"
    env.write_text("JARVIS_GITEA_PORT=3000\nOTHER=1\n")
    monkeypatch.setattr(gitea_setup, "ENV_FILE", env)
    gitea_setup.set_env(gitea_setup.Plan(dry=False), {"CNTRL_GITEA_PORT": "3001"})
    assert env.read_text().splitlines() == ["OTHER=1", "CNTRL_GITEA_PORT=3001"]

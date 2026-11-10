import pytest

from backend import agenttree, gui
from backend.config import settings


@pytest.fixture(autouse=True)
def clean_player():
    """Reset the in-page player between tests.

    `gui._player` is a process global on purpose — it describes a browser tab
    that exists right now, exactly like TARMAC's own playerState. That makes it
    leak across test files: a test that left a track loaded made a later test's
    `auto` routing pick the in-page player and never reach the music server.
    Harmless in production, where the 30s staleness check covers it, but it
    makes test outcomes depend on file order.
    """
    gui.player_report({"track": None, "paused": True, "position": 0,
                       "duration": None, "queue": 0, "volume": 100,
                       "started": False, "error": ""})
    yield


@pytest.fixture(autouse=True)
def no_node_naming(monkeypatch):
    """Spawned agents get a background naming call (agenttree.name_later). A
    test that scripts model.complete call by call would see that extra call
    consume one of its scripted replies, so it is off unless a test turns it
    back on."""
    monkeypatch.setattr(agenttree, "NAMING", False)


@pytest.fixture(autouse=True)
def fresh_gitea_checks():
    """gitea.ensure_remote_repo trusts a full check of a repo for 15 minutes, in the
    process: a test's fake Gitea must not inherit the previous test's checks."""
    from backend import gitea
    gitea.forget_ensured()
    yield


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """Point all durable-state paths at a temp dir so tests never touch real data."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "data" / "jarvis.db")
    monkeypatch.setattr(settings, "memory_dir", tmp_path / "memory")
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "skills_dir", tmp_path / "skills")
    monkeypatch.setattr(settings, "agents_dir", tmp_path / "agents")
    monkeypatch.setattr(settings, "vm_dir", tmp_path / "data" / "vm")
    monkeypatch.setattr(settings, "secrets_path", tmp_path / "secrets.json")
    return tmp_path


@pytest.fixture(scope="session", autouse=True)
def no_real_state(tmp_path_factory):
    """Every test, tmp_env or not, keeps durable state in a temp dir. Tests that
    skipped tmp_env wrote the developer's real install (the DB, registry.json,
    seeded skills, jwt_secret, once secrets.json). Session-wide, so tests that
    share state without tmp_env still share it; tmp_env still gives a test its
    own dir on top."""
    root = tmp_path_factory.mktemp("state")
    mp = pytest.MonkeyPatch()
    for name, path in (("state_dir", root), ("data_dir", root / "data"),
                       ("db_path", root / "data" / "jarvis.db"),
                       ("memory_dir", root / "memory"), ("projects_dir", root / "projects"),
                       ("skills_dir", root / "skills"), ("agents_dir", root / "agents"),
                       ("vm_dir", root / "data" / "vm"), ("gitea_dir", root / "gitea"),
                       ("secrets_path", root / "config" / "secrets.json"),
                       ("gitea_admin_token_path", root / "config" / "gitea-admin.token"),
                       ("gitea_bot_token_path", root / "config" / "gitea-bot.token")):
        mp.setattr(settings, name, path)
    yield root
    mp.undo()

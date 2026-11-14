"""scripts/install.sh: the read-only modes run to completion under `bash -u`
(set -u regressions die before printing a line), and the Arch commands it runs
or prints are never a partial upgrade."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.sh"


def _bash() -> str | None:
    # the script needs bash >= 4 (empty arrays under set -u); macOS /bin/bash is 3.2
    for b in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash")):
        if b and os.path.exists(b):
            out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"],
                                 capture_output=True, text=True).stdout.strip()
            if out.isdigit() and int(out) >= 4:
                return b
    return None


BASH = _bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no bash >= 4")


def run(tmp_path, *args):
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run([BASH, "-u", str(SCRIPT), *args], capture_output=True,
                          text=True, env=env, timeout=60)


def test_never_pacman_sy_without_u():
    for f in (SCRIPT, ROOT / "scripts" / "bootstrap.sh"):
        assert not re.search(r"pacman -Sy\b(?!u)", f.read_text()), f


@needs_bash
@pytest.mark.parametrize("args", [["--check"], ["--check", "--name", "x", "--port", "8780"]])
def test_check_runs_to_the_end(tmp_path, args):
    r = run(tmp_path, *args)
    out = r.stdout + r.stderr
    assert "unbound variable" not in out
    assert "preflight" in out
    assert r.returncode in (0, 1)


@needs_bash
def test_named_instance_uses_its_own_config_dir(tmp_path):
    out = run(tmp_path, "--check", "--name", "x").stdout
    assert ".config/cntrl-x/env" in out and "cntrl-x.service" in out


@needs_bash
def test_help(tmp_path):
    r = run(tmp_path, "--help")
    assert r.returncode == 0 and "--name" in r.stdout and "--port" in r.stdout


@needs_bash
@pytest.mark.parametrize("args,msg", [(["--check", "--name", "a/b"], "--name wants"),
                                      (["--check", "--port", "abc"], "--port wants")])
def test_bad_args_are_named(tmp_path, args, msg):
    r = run(tmp_path, *args)
    assert r.returncode != 0 and msg in r.stderr


@needs_bash
def test_foreign_config_dir_is_a_conflict(tmp_path):
    cfg = tmp_path / ".config" / "cntrl"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text("other: app\n")
    out = run(tmp_path, "--check").stdout
    assert "already holds files from something else" in out


@needs_bash
def test_the_terminal_clients_files_are_not_a_conflict(tmp_path):
    # the client keeps its credentials in ~/.config/cntrl too
    cfg = tmp_path / ".config" / "cntrl"
    cfg.mkdir(parents=True)
    (cfg / "credentials.json").write_text("{}")
    (cfg / "themes").mkdir()
    out = run(tmp_path, "--check").stdout
    assert "already holds files from something else" not in out


@needs_bash
def test_conflict_does_not_also_advise_creating_the_env(tmp_path):
    cfg = tmp_path / ".config" / "cntrl"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text("other: app\n")
    out = run(tmp_path, "--check").stdout
    assert "touch" not in out and "service not installed" not in out


def test_checkout_remembers_its_instance(tmp_path, monkeypatch):
    from backend import config
    (tmp_path / ".jarvis-instance").write_text(
        "JARVIS_INSTANCE=test\nJARVIS_CONFIG_DIR=/srv/cfg-test\n")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    assert config._instance_config_dir() == "/srv/cfg-test"
    (tmp_path / ".jarvis-instance").unlink()
    assert config._instance_config_dir() is None


def _load_module_harness(tmp_path, modinfo_out, modprobe_rc):
    """Run install.sh's load_module() alone, with modinfo/modprobe stubbed."""
    text = SCRIPT.read_text()
    start = text.index("load_module() {")
    fn = text[start:text.index("\n}\n", start) + 3]
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "modinfo").write_text(f"#!/bin/sh\necho '{modinfo_out}'\n")
    (stub / "modprobe").write_text(f"#!/bin/sh\nexit {modprobe_rc}\n")
    for f in stub.iterdir():
        f.chmod(0o755)
    conf = tmp_path / "m.conf"
    prog = ("set -euo pipefail\nok(){ echo ok \"$*\"; }\nbad(){ echo MISS \"$*\"; }\n"
            "CHANGES=() ROOT_FAILED=0\n" + fn +
            f"load_module vhost_vsock {conf}\n"
            'echo "failed=$ROOT_FAILED changes=${#CHANGES[@]}"\n')
    env = {"PATH": f"{stub}:/usr/bin:/bin"}
    out = subprocess.run([BASH, "-c", prog], capture_output=True, text=True,
                         env=env).stdout
    return out, conf


def _fn(name):
    text = SCRIPT.read_text()
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 3]


def _pacman_harness(tmp_path, installed, plan, missing):
    """missing_pkgs + pacman_install against a stub pacman that logs its calls.
    installed: names `pacman -Q` knows; plan: what `-Sp` would install;
    missing: what `-T` reports unsatisfied."""
    stub = tmp_path / "bin"
    stub.mkdir()
    log = tmp_path / "calls"
    (stub / "pacman").write_text(f"""#!/bin/sh
echo "$*" >> {log}
case "$1" in
  -T) for p in {' '.join(missing)}; do echo "$p"; done; [ -z "{' '.join(missing)}" ] || exit 127 ;;
  -Sp) for p in {' '.join(plan)}; do echo "$p"; done ;;
  -Q) for p in {' '.join(installed)}; do [ "$2" = "$p" ] && exit 0; done; exit 1 ;;
  -S) exit 0 ;;
esac
""")
    (stub / "pacman").chmod(0o755)
    prog = ("set -euo pipefail\nPKG=pacman\nPACKAGES=(python nodejs qemu-base)\n"
            "die(){ echo DIE \"$*\"; exit 1; }\n" + _fn("missing_pkgs") + _fn("pacman_install")
            + 'mapfile -t need < <(missing_pkgs)\necho "need=${need[*]}"\n'
            + '[ ${#need[@]} -eq 0 ] || pacman_install "${need[@]}"\n')
    env = {"PATH": f"{stub}:/usr/bin:/bin"}
    out = subprocess.run([BASH, "-c", prog], capture_output=True, text=True, env=env).stdout
    calls = log.read_text().splitlines() if log.exists() else []
    return out, calls


@needs_bash
def test_pacman_never_names_an_installed_package(tmp_path):
    out, calls = _pacman_harness(tmp_path, installed=["python", "nodejs"],
                                 plan=["qemu-base", "qemu-img"], missing=["qemu-base"])
    assert "need=qemu-base" in out
    assert "-S --noconfirm qemu-base" in calls
    assert not any(c.startswith("-S") and ("nodejs" in c or "python" in c) for c in calls)
    assert not any(c.startswith("-Sy") for c in calls)


@needs_bash
def test_pacman_refuses_when_it_would_upgrade_installed_deps(tmp_path):
    out, calls = _pacman_harness(tmp_path, installed=["python", "nodejs", "abseil-cpp"],
                                 plan=["qemu-base", "abseil-cpp"], missing=["qemu-base"])
    assert "DIE" in out and "abseil-cpp" in out and "pacman -Syu" in out
    assert not any(c.startswith("-S ") for c in calls)


@needs_bash
def test_pacman_nothing_missing_does_nothing(tmp_path):
    out, calls = _pacman_harness(tmp_path, installed=["python", "nodejs", "qemu-base"],
                                 plan=[], missing=[])
    assert "need=" in out and calls == ["-T python nodejs qemu-base"]


@needs_bash
def test_builtin_module_is_not_persisted(tmp_path):
    out, conf = _load_module_harness(tmp_path, "(builtin)", 0)
    assert "built into the kernel" in out and "failed=0 changes=0" in out
    assert not conf.exists()


@needs_bash
def test_failed_modprobe_is_not_ok_and_not_persisted(tmp_path):
    out, conf = _load_module_harness(tmp_path, "/lib/modules/x/vhost_vsock.ko", 1)
    assert "MISS" in out and not out.startswith("ok") and "failed=1" in out
    assert not conf.exists()


@needs_bash
def test_loaded_module_is_persisted_once(tmp_path):
    out, conf = _load_module_harness(tmp_path, "/lib/modules/x/vhost_vsock.ko", 0)
    assert "failed=0 changes=1" in out and conf.read_text() == "vhost_vsock\n"



@needs_bash
@pytest.mark.parametrize("args", [["--check", "--json"], ["--check", "--json", "--port", "8780"]])
def test_check_json_is_one_object_on_stdout(tmp_path, args):
    import json
    r = run(tmp_path, *args)
    d = json.loads(r.stdout)                     # nothing else on stdout
    for k in ("ready", "kvm", "docker", "blocked", "missing_root", "missing_user",
              "conflict", "missing_packages", "root_command", "problems", "warnings"):
        assert k in d, k
    assert d["port"] == (8780 if "8780" in args else 8000)
    from backend.version import __version__
    assert d["version"] == __version__ and d["unit"] == "cntrl"
    assert all(set(p) == {"message", "fix"} for p in d["problems"])
    assert "MISS" in r.stderr or d["ready"]     # the human report moved to stderr


@needs_bash
def test_json_without_check_is_refused(tmp_path):
    r = run(tmp_path, "--json")
    assert r.returncode == 64 and "--json needs --check" in r.stderr


def test_bootstrap_progress_goes_to_stderr():
    # `curl .../bootstrap.sh | sh -s -- --check --json` must leave stdout pure JSON
    txt = (ROOT / "scripts" / "bootstrap.sh").read_text()
    assert "say() { printf '== %s\\n' \"$*\" >&2; }" in txt


def _state_dir_of(tmp_path, *args):
    import json
    out = run(tmp_path, "--check", "--json", *args).stdout
    return json.loads(out[out.index("{"):])["state_dir"]


@needs_bash
def test_a_new_named_instance_gets_its_own_state_dir(tmp_path):
    """Before, --name x shared the default instance's state dir: one database for two
    servers. A new named instance gets ~/.local/share/cntrl-x."""
    assert _state_dir_of(tmp_path, "--name", "x") == str(tmp_path / ".local/share/cntrl-x")
    assert _state_dir_of(tmp_path) == str(tmp_path / ".local/share/cntrl")


@needs_bash
def test_an_existing_named_instance_keeps_the_state_dir_it_has_been_using(tmp_path):
    cfg = tmp_path / ".config/cntrl-x"
    cfg.mkdir(parents=True)
    (cfg / "env").write_text("CNTRL_LAN_PORT=8780\n")        # no state dir recorded
    assert _state_dir_of(tmp_path, "--name", "x") == str(tmp_path / ".local/share/cntrl")
    (cfg / "env").write_text(f"CNTRL_STATE_DIR={tmp_path}/own\n")
    assert _state_dir_of(tmp_path, "--name", "x") == str(tmp_path / "own")


# --- --dev (docs/DEV-INSTALL.md) ---------------------------------------------------

def _json_check(tmp_path, *args):
    import json
    return json.loads(run(tmp_path, "--check", "--json", *args).stdout)


@needs_bash
def test_a_named_dev_instance_gets_its_own_state_dir(tmp_path):
    d = _json_check(tmp_path, "--dev", "--name", "dev", "--port", "8781")
    assert d["dev"] is True and d["unit"] == "cntrl-dev" and d["port"] == 8781
    assert d["state_dir"] == str(tmp_path / ".local/share/cntrl-dev")
    assert d["config_dir"] == str(tmp_path / ".config/cntrl-dev")


@needs_bash
def test_a_new_named_instance_gets_its_own_state_with_or_without_dev(tmp_path):
    # since 96d08e7 every NEW named instance has its own state dir, not only --dev ones
    d = _json_check(tmp_path, "--name", "dev")
    assert d["dev"] is False and d["state_dir"] == str(tmp_path / ".local/share/cntrl-dev")
    assert _json_check(tmp_path, "--dev")["state_dir"] == str(tmp_path / ".local/share/cntrl")


@needs_bash
def test_help_names_dev(tmp_path):
    out = run(tmp_path, "--help").stdout
    assert "--dev" in out and "dev-agent" in out


def test_the_dev_agent_step_comes_after_setup_gitea_and_docker():
    body = _fn("user_phase")
    order = [body.index(s) for s in ("first_run_setup", "gitea_step", "docker_step",
                                     "dev_agent_step")]
    assert order == sorted(order)
    step = _fn("dev_agent_step")
    assert '[ "$DEV" = 1 ] || return 0' in step and "dev-agent-setup" in step


def _line_fn(name) -> str:
    text = SCRIPT.read_text()
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n", start) + 1]


def _named_ports(tmp_path, new_named, taken=()):
    """install.sh's named_ports() alone, with `ss` stubbed to report `taken`."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "ss").write_text("#!/bin/sh\nfor p in " + " ".join(taken) +
                             '; do case "$*" in *":$p"*) echo LISTEN; exit 0;; esac; done\n')
    (stub / "ss").chmod(0o755)
    cfg = tmp_path / "cfg"
    cfg.mkdir(exist_ok=True)
    (cfg / "env").touch()
    prog = ("set -euo pipefail\nhave(){ command -v \"$1\" >/dev/null 2>&1; }\n"
            "ok(){ echo ok \"$*\"; }\n"
            f"NEW_NAMED={new_named} PORT=8781 CFG_DIR='{cfg}'\n"
            + _line_fn("port_taken") + _fn("named_ports") + "named_ports\n")
    subprocess.run([BASH, "-c", prog], check=True, capture_output=True, text=True,
                   env={"PATH": f"{stub}:/usr/bin:/bin"})
    return (cfg / "env").read_text()


@needs_bash
def test_a_new_named_instance_takes_free_pages_and_gitea_ports(tmp_path):
    env = _named_ports(tmp_path, 1, taken=("8091",))
    assert env == "CNTRL_PAGES_PORT=8092\nCNTRL_GITEA_PORT=3001\n"
    assert _named_ports(tmp_path, 1) == env          # recorded once, kept on a re-run


@needs_bash
def test_ports_stay_default_for_the_default_or_an_existing_named_instance(tmp_path):
    assert _named_ports(tmp_path, 0) == ""


# --- bootstrap --dev -----------------------------------------------------------------

def _upstream(tmp_path) -> Path:
    """A repo with main and dev whose scripts/install.sh only says how it ran."""
    up = tmp_path / "upstream"
    (up / "scripts").mkdir(parents=True)
    (up / "scripts" / "install.sh").write_text(
        'echo "ARGS:$*"\necho "BRANCH:$(git -C "$(dirname "$0")/.." branch --show-current)"\n')
    g = ["git", "-C", str(up), "-c", "user.name=t", "-c", "user.email=t@x"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(up)], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "main"], check=True)
    subprocess.run(g + ["branch", "dev"], check=True)
    return up


def _bootstrap(tmp_path, up, *args):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "CNTRL_REPO": str(up)}
    return subprocess.run(["sh", str(ROOT / "scripts" / "bootstrap.sh"), *args],
                          capture_output=True, text=True, env=env, timeout=60,
                          stdin=subprocess.DEVNULL)


def _branch(d) -> str:
    return subprocess.run(["git", "-C", str(d), "branch", "--show-current"],
                          capture_output=True, text=True).stdout.strip()


def test_bootstrap_dev_clones_the_dev_branch_beside_a_stable_checkout(tmp_path):
    up = _upstream(tmp_path)
    r = _bootstrap(tmp_path, up, "--dev", "--name", "dev", "--port", "8781")
    assert r.returncode == 0, r.stderr
    assert "ARGS:--dev --name dev --port 8781" in r.stdout and "BRANCH:dev" in r.stdout
    assert _branch(tmp_path / "home" / "cntrl-dev") == "dev"
    assert not (tmp_path / "home" / "cntrl").exists()


def test_bootstrap_dev_moves_an_existing_dev_checkout_onto_dev(tmp_path):
    up = _upstream(tmp_path)
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "clone", "-q", str(up), str(tmp_path / "home" / "cntrl-dev")],
                   check=True)
    assert _branch(tmp_path / "home" / "cntrl-dev") == "main"
    r = _bootstrap(tmp_path, up, "--dev")
    assert r.returncode == 0, r.stderr
    assert "switching" in r.stderr and _branch(tmp_path / "home" / "cntrl-dev") == "dev"


def test_bootstrap_without_dev_is_as_before(tmp_path):
    up = _upstream(tmp_path)
    r = _bootstrap(tmp_path, up, "--no-image")
    assert r.returncode == 0, r.stderr
    assert "ARGS:--no-image" in r.stdout and "BRANCH:main" in r.stdout
    assert not (tmp_path / "home" / "cntrl-dev").exists()

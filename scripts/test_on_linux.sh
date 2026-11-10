#!/usr/bin/env bash
# Run the test suite on a Linux host, sealed off from anything live there.
#
#   scripts/test_on_linux.sh user@host                    # the whole suite
#   scripts/test_on_linux.sh user@host tests/test_x.py -x # pytest args pass through
#   DETACH=1 scripts/test_on_linux.sh user@host           # start it, print where the log is
#
# The host may run a live install of this server (the Pi does: ~/jarvis,
# ~/.config/jarvis, ~/.local/share/jarvis, the jarvis units, :8000, Gitea,
# its VMs). Nothing here touches it:
#   - the working tree goes to ~/cntrl-test (never the live checkout), without
#     .git or anything git ignores (node_modules, frontend/dist, .venv, data,
#     projects, notes); the venv is ~/cntrl-test/.venv, pinned by
#     tests/constraints.txt;
#   - each run gets a fresh dir ~/cntrl-test-runs/<stamp>. HOME, every XDG dir,
#     TMPDIR and the config and state dirs (JARVIS_* and CNTRL_*) point into it,
#     and `env -i` drops the rest of the login environment: no XDG_RUNTIME_DIR
#     or D-Bus address, so `systemctl --user` cannot reach the live services;
#   - its own network namespace (loopback only), so no test reaches a live port
#     or the internet, and its own PID namespace, so no kill reaches a live
#     process; a seccomp filter refuses AF_VSOCK sockets, which no namespace
#     covers (the live gateway and guests listen on vsock);
#   - sudo, systemctl and systemd-run on PATH are stand-ins that refuse and log
#     to <run>/refused.log (a box with passwordless sudo would otherwise let a
#     test reach nft or the system units; the seccomp filter's no_new_privs
#     leaves even /usr/bin/sudo powerless), and DOCKER_HOST names a socket that
#     does not exist (a user in the docker group would otherwise reach the
#     live daemon);
#   - pytest runs under nice and idle I/O priority, with a time limit.
# Run dirs are kept (pytest.log, refused.log); delete old ones by hand.
set -euo pipefail

HOST="${1:?usage: scripts/test_on_linux.sh user@host [pytest args]}"
shift
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST=cntrl-test
SSH=(ssh -o BatchMode=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=10)

# --- 1. the tree, as git would commit it -------------------------------------
excl="$(mktemp)"
trap 'rm -f "$excl"' EXIT
# always, git checkout or not
printf '%s\n' /.git /.venv node_modules /frontend/dist /data /projects/ /memory/ \
    /agents/ __pycache__ '*.pyc' .pytest_cache /.jarvis-instance /.cntrl-instance > "$excl"
# and everything the checkout's .gitignore leaves out (local notes, state)
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$ROOT" ls-files --others --ignored --exclude-standard --directory \
        | sed 's|^|/|' >> "$excl"
fi
echo "==> sync $ROOT -> $HOST:~/$DEST"
"${SSH[@]}" "$HOST" "mkdir -p ~/$DEST"
# --delete keeps the copy exact; excluded paths there (the venv) are left alone
rsync -a --delete --exclude-from="$excl" -e "${SSH[*]}" "$ROOT/" "$HOST:$DEST/"

# --- 2. venv, run dir, the suite -----------------------------------------------
args=""
[ $# -gt 0 ] && args="$(printf '%q ' "$@")"
"${SSH[@]}" "$HOST" "DEST=$DEST DETACH=${DETACH:-0} bash -s -- $args" <<'REMOTE'
set -euo pipefail
cd "$HOME/$DEST"
here="$(pwd -P)"
for live in "$HOME/jarvis" "$HOME/cntrl"; do
    if [ -d "$live" ] && [ "$(cd "$live" && pwd -P)" = "$here" ]; then
        echo "refusing: $here is a live checkout" >&2
        exit 1
    fi
done

if [ ! -x .venv/bin/python ]; then
    echo "==> venv"
    python3 -m venv .venv
fi
want="$(cat requirements.txt tests/constraints.txt | sha256sum | cut -d' ' -f1)"
if [ "$(cat .venv/.deps 2>/dev/null || true)" != "$want" ]; then
    echo "==> pip install (pinned by tests/constraints.txt)"
    .venv/bin/python -m pip install -q --disable-pip-version-check \
        -c tests/constraints.txt -r requirements.txt \
        pytest pytest-asyncio textual pexpect pyte pyyaml pillow pypdf
    echo "$want" > .venv/.deps
fi

# a test that wrote state into the checkout must not make the next run use it
# (backend/config.py falls back to <checkout>/data when it holds a DB)
rm -rf -- "$HOME/$DEST/data"

run="$HOME/cntrl-test-runs/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$run"/home/.config "$run"/home/.local/share "$run"/home/.local/state \
    "$run"/home/.cache "$run"/tmp "$run"/config "$run"/state "$run"/bin
for b in sudo systemctl systemd-run; do
    cat > "$run/bin/$b" <<EOF
#!/bin/sh
echo "$b \$*" >> "$run/refused.log"
echo "$b: refused by scripts/test_on_linux.sh (this run is sealed off from the host)" >&2
exit 1
EOF
    chmod +x "$run/bin/$b"
done
echo "==> run dir $run"

# Its own network and processes: unprivileged user + network + PID namespaces.
# The only interface is a fresh loopback, so a test reaches its own loopback
# servers and nothing else (not the live server on :8000, Gitea, ollama, the
# guests' tap, the internet); and it sees only its own processes, so a pkill or
# kill can never land on the live server or its VMs. The inner namespace maps
# us back to our own uid with no capabilities, so tests see an ordinary user.
# Hosts without unprivileged namespaces run without it (said below);
# SEAL_NET=0 turns it off.
seal=()
if [ "${SEAL_NET:-1}" = 1 ] &&
        unshare --user --map-root-user --net --pid --fork --mount-proc --kill-child true \
            2>/dev/null; then
    # sh stays PID 1 (no exec), so orphaned grandchildren are reaped as init
    # would, not left as zombies that still look alive to a test
    seal=(unshare --user --map-root-user --net --pid --fork --mount-proc --kill-child sh -c \
        'ip link set lo up && u=$1 g=$2 && shift 2 &&
         unshare --user --map-user="$u" --map-group="$g" -- "$@"' \
        sh "$(id -u)" "$(id -g)")
    echo "==> sealed: own network (loopback only) and process namespaces"
else
    echo "==> NOT sealed: no unprivileged namespaces here (or SEAL_NET=0)"
fi

# vsock is not namespaced: from this host a test could still dial the live
# gateway or a live guest (CID 3+, ports 5556-5558). A seccomp filter, kept by
# every child, makes socket(AF_VSOCK, ...) fail as on a host without vsock;
# no_new_privs, which it needs, also leaves sudo powerless.
cat > "$run/novsock.py" <<'PY'
"""Exec argv[1:] with socket(AF_VSOCK, ...) refused (EAFNOSUPPORT), inherited."""
import ctypes, os, platform, struct, sys
ARCH, NR = {"aarch64": (0xC00000B7, 198), "x86_64": (0xC000003E, 41)}[platform.machine()]
LD, JEQ, RET = 0x20, 0x15, 0x06                      # BPF_LD|W|ABS, BPF_JMP|JEQ|K, BPF_RET|K
ALLOW, DENY = 0x7FFF0000, 0x00050000 | 97            # SECCOMP_RET_ERRNO | EAFNOSUPPORT
prog = [(LD, 0, 0, 4), (JEQ, 1, 0, ARCH), (RET, 0, 0, DENY),       # foreign arch: refuse
        (LD, 0, 0, 0), (JEQ, 0, 3, NR),                            # not socket(): allow
        (LD, 0, 0, 16), (JEQ, 0, 1, 40),                           # domain == AF_VSOCK?
        (RET, 0, 0, DENY), (RET, 0, 0, ALLOW)]
code = ctypes.create_string_buffer(b"".join(struct.pack("HBBI", *i) for i in prog))
class Prog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]
fprog = Prog(len(prog), ctypes.addressof(code))
libc = ctypes.CDLL(None, use_errno=True)
for args in ((38, 1, 0, 0, 0), (22, 2, ctypes.byref(fprog), 0, 0)):  # NO_NEW_PRIVS, SECCOMP
    if libc.prctl(*args) != 0:
        sys.exit(f"novsock: prctl{args[:2]} failed: {os.strerror(ctypes.get_errno())}")
os.execvp(sys.argv[1], sys.argv[1:])
PY

cmd=(env -i
    PATH="$run/bin:$here/.venv/bin:/usr/local/bin:/usr/bin:/bin"
    HOME="$run/home" USER="$(id -un)" LOGNAME="$(id -un)" LANG=C.UTF-8
    TMPDIR="$run/tmp"
    XDG_CONFIG_HOME="$run/home/.config" XDG_DATA_HOME="$run/home/.local/share"
    XDG_STATE_HOME="$run/home/.local/state" XDG_CACHE_HOME="$run/home/.cache"
    JARVIS_CONFIG_DIR="$run/config" CNTRL_CONFIG_DIR="$run/config"
    JARVIS_STATE_DIR="$run/state" CNTRL_STATE_DIR="$run/state"
    DOCKER_HOST="unix://$run/no-docker.sock"
    PYTHONDONTWRITEBYTECODE=1
    nice -n 19 ionice -c 3 timeout 4h ${seal[@]+"${seal[@]}"}
    "$here/.venv/bin/python" "$run/novsock.py"
    "$here/.venv/bin/python" -m pytest -q -p no:cacheprovider -rs "$@")
# what ran, kept beside its log; then what tests left in the config and state
# dirs (on a developer's machine those are the real ones)
{ echo '#!/bin/bash'; echo "cd $(printf '%q' "$here")"; printf '%q ' "${cmd[@]}"; echo
  echo 'rc=$?'
  printf 'find %q %q -type f > %q\n' "$run/config" "$run/state" "$run/state-writes.txt"
  echo 'exit $rc'; } > "$run/run.sh"

if [ "$DETACH" = 1 ]; then
    nohup setsid bash -c 'bash "$1/run.sh" > "$1/pytest.log" 2>&1; echo "exit $?" >> "$1/pytest.log"' \
        _ "$run" < /dev/null > /dev/null 2>&1 &
    echo "==> started; follow it with: ssh <host> tail -f $run/pytest.log"
    exit 0
fi
set +e
bash "$run/run.sh" 2>&1 | tee "$run/pytest.log"
rc=${PIPESTATUS[0]}
set -e
[ -s "$run/refused.log" ] && { echo "==> refused calls:"; cat "$run/refused.log"; }
exit "$rc"
REMOTE

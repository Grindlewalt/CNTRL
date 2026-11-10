# CNTRL first ship (contract)

Base: branch `ship` at the commit that adds this file. Every agent starts with
`git reset --hard <that sha>` and touches ONLY the files it owns.

## What the operator decided (2026-11-10; do not re-ask)

- The product is renamed **CNTRL** ("Controlled: Network, Tools, Runtimes and Logs").
  Everything a person sees says CNTRL; **old names keep working**.
- License **Apache-2.0**.
- The web app is redesigned to **match the public website's look** (light cream / ink /
  green by default, warm charcoal dark, strong contrast, one accent, mono only for data,
  the pixel wordmark as the single flourish).
- Ship = a **tagged v0.1.0 GitHub release** with notes and a one-line install, a
  **clean-machine install proven**, **every test green on Linux**.

## The name map

| Thing | Old | New | Compatibility |
|---|---|---|---|
| Product name in UI, docs, messages | Jav3 / jav3 / Jarvis | CNTRL (prose), `cntrl` (identifiers) | — |
| Settings in the environment | `JARVIS_*` | `CNTRL_*` | `JARVIS_*` is still read when the `CNTRL_*` one is unset |
| Server config dir | `~/.config/jarvis` | `~/.config/cntrl` | used as is when only the old one exists; the installer moves it on upgrade |
| Server state dir | `~/.local/share/jarvis` | `~/.local/share/cntrl` | same rule |
| Default install dir | `~/jarvis` | `~/cntrl` | an existing checkout stays where it is |
| systemd user units | `jarvis.service`, `jarvis-pages`, `jarvis-backup.*`, `jarvis-image-rebuild.*` | `cntrl.service`, `cntrl-pages`, `cntrl-backup.*`, `cntrl-image-rebuild.*` | the installer stops and disables the old units and enables the new ones on upgrade; nothing else ever touches them |
| Terminal command | `jav3` | `cntrl` | `jav3` stays as a second launcher |
| Terminal client source | `clients/jav3cli/jav3`, `clients/jav3cli/install.sh` | `clients/cntrl/cntrl`, `clients/cntrl/install.sh` | — |
| Client served by the server | `/cli/jav3` | `/cli/cntrl` | `/cli/jav3` serves the same file for good (old clients update from it) |
| Client config dir | `~/.config/jav3` | `~/.config/cntrl` | moved by the new client on first start (credentials stay 0600) |
| Client install dir | `~/.local/share/jav3` | `~/.local/share/cntrl-cli` (apart from the server's state dir, 52ba5f6) | an old install keeps working and updates in place |
| GitHub repo | `Grindlewalt/Jav3` | `Grindlewalt/cntrl` | renamed at ship time by the orchestrator; write the new URL everywhere now |
| Version | none | `backend/version.py` `__version__ = "0.1.0"` | server: `/api/health` and the doctor show it; client: `cntrl --version` and `/status` |

**Unchanged on purpose** (internal, and changing them would log people out or break
data): the Python package `backend`, the session cookie `jarvis_token`, the device
token prefix `jvd_`, the DB file `jarvis.db`, the Gitea bot account `jav3-agent`,
security event kinds. Comments may keep historical names where they describe history.

## Ownership

| Agent | Owns | Must not touch |
|---|---|---|
| A1 rename, server | `backend/`, `scripts/` (installer, bootstrap, units: `git mv` the unit files), `guest/`, `vm/`, `tools/`, `skills/`, `installpage/`, `voicebox/`, `docs/` (not README), `SECURITY-RESIDUAL-RISK.md`, `pytest.ini`, `requirements.txt`, and every test file except A2's | `clients/`, `frontend/`, `README.md`, `NOTICE`, `LICENSE`, A2's tests |
| A2 rename, clients | `clients/` (`git mv clients/jav3cli clients/cntrl`, the desk and browser clients too), `scripts/tui_drive.py`, `tests/test_cli*.py`, `tests/cli_fake.py`, `tests/tui_*.py`, `tests/test_tui_snapshots.py`, `tests/tui_snapshots/` | everything else |
| B design foundation | `frontend/` | everything else |
| C tests on Linux | the failing tests listed in its brief, `scripts/test_on_linux.sh` (new), and the smallest fixes in code those tests show are wrong | broad edits in A1's files (coordinate by keeping fixes tiny) |
| D release files | `LICENSE`, `NOTICE`, `CHANGELOG.md` (and its `!CHANGELOG.md` line in `.gitignore`), `README.md` | everything else |

The server serving `/cli/cntrl` and `/cli/jav3` from `clients/cntrl/` is A1's change
(backend/devices_api.py `CLI_DIR`); the move of the files is A2's.

## Rules every agent keeps

- Tests never touch the network. Python: `~/.cache/jav3-test-venv/bin/python -m pytest -q
  -p no:cacheprovider <files>`; lint: the same python `-m pyflakes <files>`. Tests that
  touch the DB use the `tmp_env` fixture and `init_db()`.
- Keep tool calls and outputs small (a step silent for 600 s kills you). Match the
  surrounding code: comment density, naming, plain-English comments. Operator-facing text
  in plain sentence case, American spelling.
- Commit on your branch; report what changed, tests run with results, and what you
  could not verify.

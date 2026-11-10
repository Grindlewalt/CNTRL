# CNTRL

CNTRL is a server you run yourself that gives AI agents real work to do (code,
research, files, git, scheduled jobs) and keeps them contained while they do it.
The name is short for "controlled": network, tools, runtimes and logs, the four
things it keeps in hand.

The agent does its thinking and runs its code inside a disposable sandbox, a KVM virtual machine or a locked-down Docker container.
That sandbox holds nothing worth stealing: no API key, no secrets, no database.
Everything valuable stays on the host, and every time the agent wants to reach
outside its sandbox (call the model, read a web page, change a file, open a
network connection, push a commit) it has to ask the host, which checks the
request, applies your rules, and keeps a record.

This is version 0.1.0, the first public release. The project was called Jav3
until now; old names keep working (see [Upgrading from Jav3](#upgrading-from-jav3)).

## Why use it

**The agent can't leak what it never has.** In a typical agent setup the API
key, your credentials and a shell all sit with the process that acts on the
model's output. Then one prompt injection (a poisoned web page, a malicious
README, a bad dependency) is enough to read the key or send your files
somewhere. In CNTRL the model's output runs where none of that exists. An injection that gets code
execution lands in a VM with no key, no secrets, and by default no network at
all.

**You decide what crosses the line.** Things that change the world outside the
sandbox wait for you: a new network host, an extra package, a git commit or
push, a page to publish, a memory note the agent wants to keep, a background
service, a new schedule. You can tighten that (ask before every write or
command) or loosen it per chat. The sandbox and the host's checks hold in
every mode; the most permissive one only answers the approvals for you, and
logs each one.

**Everything is in one place.** One server holds your model providers and keys,
your projects (each a git repo), memory, agents, skills and schedules. Every
way in talks to that server: the web app, the terminal client, a paired
computer or browser. Approvals from every chat, agent and schedule land in one
Security queue. Model calls go through one gateway, so cost and token use are
metered per call across everything. Revoke a device once and it's locked out
everywhere, its running turns included.

## How it works

```
   web app        terminal (cntrl)      paired computer / browser
        \               |                    /
         \______________|___________________/
                        | HTTP on your network
 +----------------------v-------------------------------------- host --+
 |  approvals and the Security queue      model gateway (keys, cost)   |
 |  tool broker: web_read, search, memory, git, publish, ...           |
 |  write chokepoint -> projects (git)    secrets (by name)            |
 |  memory, agents, schedules, logs       egress proxy (when on)       |
 +--------------^--------------------------------------^---------------+
                | vsock (VM) or unix sockets (Docker)   | the only way out
 +--------------+---------------------- sandbox --------+---------------+
 |  the agent loop, code execution, a copy of the project              |
 |  no API key, no secrets, no database, no network unless you allow it |
 +----------------------------------------------------------------------+
```

What happens when you send a message:

1. The host builds the prompt (your memory notes, the project's context) and
   starts a turn in the sandbox.
2. The agent loop runs inside the sandbox. When it needs the model, it asks
   the host's gateway, which adds the key, makes the call, meters it and
   streams the answer back. The key never enters the sandbox.
3. Code and file tools run inside the sandbox, against its copy of the
   project. Tools that reach past it (reading the web, searching, memory, git,
   publishing) are requests to the host's broker, which applies the checks and
   approvals for each one.
4. File changes come back through one host chokepoint. It refuses path tricks
   and any write containing one of your stored secrets, runs diff checks that
   flag suspicious changes, and lands the rest in the project. Commits wait for
   you.
5. Anything that needs your answer shows up in the Security queue (web) or
   `/security` (terminal), from whichever chat or agent asked.

The host and the sandbox talk only over vsock (VMs) or two unix sockets
(Docker), and by default the sandbox has no network.

## What it does

- **Chats and projects.** Talk to an agent on its own or inside a project. A
  project is a git repository on the host; agent writes land in it straight
  away, every applied batch is kept on a hidden ref so `/history` and `/undo`
  can bring back an overwritten or deleted file, and commits wait for your
  approval. Committed work can be cloned read-only from the server
  (`git clone http://<host>:<port>/git/<slug>`), pushed to GitHub with a
  `GITHUB_TOKEN` secret, or mirrored to an optional local Gitea where agents
  open pull requests ([docs/gitea.md](docs/gitea.md)).
- **Any model.** Providers from the [models.dev](https://models.dev) catalogue:
  OpenAI-compatible APIs (DeepSeek, OpenAI, OpenRouter, Groq, Mistral, xAI and
  others, or a local server such as Ollama or LM Studio) plus native Anthropic
  and Google adapters. Keys stay on the host; tokens and cost are metered per
  call.
- **Permission modes per chat.**
  - `safe-yolo` is the default. Writes and commands in the sandbox run without
    asking; the approvals below still wait for you.
  - `auto` has a separate model call, which sees only the tool name and its
    arguments, judge each write or command. Anything it isn't sure is safe asks
    you.
  - `ask` asks before every write or command.
  - `yolo` asks nothing: requests are approved as they are made, and each one
    is logged as a security event. It can only be switched on from a password
    login, after a confirmation.
- **Approvals in one queue.** Network hosts, extra packages, new sandbox
  images, skill installs, background services, git commits, remotes and pushes,
  published pages, memory notes the agent wrote, and new schedules (which start
  paused).
- **Memory you control.** Plain files on the host. A note the agent writes stays
  out of its context until you approve it on the Memory page, and a note written
  during a turn that read outside content carries a taint warning.
- **Multi-agent work.** Agents defined as folders, sub-agents and throwaway
  copies, an orchestrator that works through a plan (optionally each item on
  its own branch, merged as one pull request you approve), a research pipeline,
  and scheduled runs.
- **Web reading.** `web_read` fetches pages on the host behind an SSRF guard
  and reads PDFs a slice at a time (a scanned page comes back as an image for
  models that take images). Web search uses a [SearXNG](https://docs.searxng.org)
  instance you run yourself; the installer doesn't set one up
  (`CNTRL_SEARXNG_URL`, default `http://localhost:8080`).
- **Published pages.** The agent can ask to publish a static page. Once you
  approve it, a separate read-only server (`cntrl-pages.service`, port 8090)
  serves it, sandboxed, to anyone with the link.
- **Two front ends.** A web app (Work, Agents, Security, VMs, Tools, Settings,
  Memory, Schedules, Git) and `cntrl`, a keyboard-first terminal client.
- **Computer and browser use**, off until you pair a device and grant it (see
  [Computer and browser use](#computer-and-browser-use)).
- **Backups** of the state directory with rclone, hourly once you set a remote
  (see [Backups](#backups)).
- **Voice**, experimental: a `/voice` page that needs a separate speech sidecar
  on a GPU machine, set up by hand ([docs/voice.md](docs/voice.md)).

## Requirements

- Linux with apt (Debian, Ubuntu) or pacman (Arch). The installer has a dnf
  branch for Fedora too, but no test covers it yet.
- x86_64 or arm64 (aarch64).
- For the VM sandbox: KVM (`/dev/kvm`, with virtualization switched on in the
  firmware) and the `vhost_vsock` module. Without KVM, Docker can run the turns
  of projects set to their own container; Docker you install yourself.
- About 4 GB of RAM. A Raspberry Pi 4 with 4 GB runs it (the shared VM takes
  768 MB by default).
- systemd user services, running as a normal user (not root).
- A model API key, or a local model server.

## Install

Run this as the user the server will run as:

```sh
curl -fsSL https://raw.githubusercontent.com/Grindlewalt/cntrl/main/scripts/bootstrap.sh | sh
```

The bootstrap installs git if it's missing, clones the repository to `~/cntrl`
(or updates an existing checkout there, or one at `~/jarvis` from before the
rename) and hands over to `scripts/install.sh`. You can steer it with
`CNTRL_DIR` (where to clone), `CNTRL_REPO` (the git URL), `CNTRL_REF` (a branch
or tag) and `CNTRL_ALLOW_ROOT=1` (install for root anyway); each also answers to
its old `JARVIS_` name. Installer flags pass straight through:
`curl … | sh -s -- --port 8780 --no-gitea`.

Or clone it yourself:

```sh
git clone https://github.com/Grindlewalt/cntrl.git ~/cntrl
cd ~/cntrl && bash scripts/install.sh
```

Everything that needs root happens in one step. When some of it is still
owed, the installer prints the single command to run,
`sudo bash scripts/install.sh --root-phase --user <you>`, and stops if the rest
of the install can't go on without it; run that command, then run the install
again. The root step installs only packages that are missing (QEMU and its UEFI
firmware, Node and npm, Python's venv and pip, git, curl, rsync, rclone, and a
tool that writes the cloud-init seed) and never upgrades one you already have.
It also loads `kvm` and `vhost_vsock`, adds you to the `kvm` group, enables
systemd linger, and lists what it changed and how to undo each change. If
virtualization is off in the firmware, someone has to turn it on at the
machine.

`bash scripts/install.sh --check` reports what's missing and changes nothing
(`--check --json` prints one JSON object; `--target user@host` checks another
machine over ssh).

The rest of the install, as your user:

1. creates `.venv` and installs the Python requirements;
2. builds the web app (`npm ci`);
3. writes the config and installs the systemd user units: `cntrl.service` (the
   app, on all interfaces, port 8000 by default), `cntrl-pages.service`
   (published pages, port 8090) and `cntrl-backup.timer`;
4. builds the guest image (downloads a Debian cloud image, about 10 minutes;
   skipped without a usable `/dev/kvm`);
5. runs first-run setup: in a terminal it asks for your login, model provider
   and default security profile; otherwise it prints a one-time `/setup` link to
   open in a browser;
6. sets up Gitea (skip with `--no-gitea`);
7. if Docker is installed and your user can use it, builds the Docker box image
   and switches Docker boxes on (skip with `--no-docker`);
8. starts the service and checks `/api/health`.

Flags: `--port <n>`, `--name <name>` (a second instance: `cntrl-<name>.service`,
`~/.config/cntrl-<name>`), `--state-dir <dir>`, `--config-dir <dir>`,
`--from <host:path>` (copy state from another install first; `--force`
overwrites local state), `--no-image`, `--no-build`, `--no-gitea`,
`--gitea-dry-run`, `--no-docker`, `--yes` (no questions) and `--rename-only`
(see [Upgrading from Jav3](#upgrading-from-jav3)). `bash scripts/install.sh
--help` lists them all.

A coding agent can do the install for you: paste the prompt at the end of
[docs/AGENT-INSTALL.md](docs/AGENT-INSTALL.md). That guide also covers second
instances, state directories, moving from another machine and putting the
server behind TLS.

After installing:

- Settings live in `~/.config/cntrl/env` (mode 0600) as `CNTRL_<NAME>=value`
  lines (see `backend/config.py`). Restart with `systemctl --user restart
  cntrl` after a change. Durable state (projects, memory, agents, skills, the
  database) lives in `~/.local/share/cntrl`.
- `~/cntrl/.venv/bin/python -m backend.cli doctor` tells you what's still
  missing.
- To upgrade: `cd ~/cntrl && git pull --ff-only && bash scripts/install.sh`.
  Every step checks before it acts, so running it again is safe.

## Sandboxes

- **The shared VM.** By default one KVM guest runs every chat and project. It
  isn't wiped between turns unless you set `CNTRL_VM_IDLE_SCRUB_SECONDS` (off by
  default), and it's rebuilt from the read-only image whenever it restarts.
- **Boxes of their own.** With `CNTRL_VM_BOXES_ENABLED=true` (off by default;
  the installer's Docker step turns it on when Docker works), a project whose
  security profile or "Runs in" setting asks for it gets its own VM or
  container, stopped after 10 minutes idle. With boxes off, every project runs
  in the shared VM whatever its setting says. Boxes also bring service boxes,
  package requests, image variants (such as `desktop`, with a screen and a
  browser) and the headless browser tool.
- **Docker.** One container per box, with no network interface (`--network
  none`), reaching the host only over two unix sockets
  ([docs/docker-runtime.md](docs/docker-runtime.md)). It's lighter than a VM
  and weaker: it shares the host kernel unless you configure gVisor
  (`CNTRL_DOCKER_OCI_RUNTIME=runsc`). On a host without KVM, chats outside a
  project can't run, and a project runs only when it's set to its own container.

## Network access for agents

Egress is off by default, and a fresh install leaves it off: the sandbox has no
network at all. Web search and `web_read` still work, because they run on the
host.

Turning it on is a manual step for now. Set `CNTRL_VM_EGRESS=1` in the env file.
The host also needs nftables, dnsmasq and tcpdump, and a sudoers rule that lets
the service user run `sudo -n bash <checkout>/vm/net/net_up.sh …` (and
`vm/net/net_box.sh` when boxes are on) without a password. The installer
doesn't do any of this.

With egress on, the sandbox's only way out is a proxy on the host. nftables
drops the LAN and anything that tries to go around the proxy, and DNS goes
through a logged host resolver. The proxy applies the project's security
profile: the default profile allows a short list of package registries and
GitHub hosts, and any other new host is refused and queued for your approval.
A separate model call, the triage reviewer ("Auto review", on by default, with
no tools and no network), goes through that queue every five minutes and can
approve hosts it judges routine; you can switch it off in Security. The proxy
meters bytes per host, fills in `{{secret:NAME}}` placeholders the project was
granted in plain-HTTP requests, and cuts off a host when the traffic looks like
exfiltration (volume, entropy, cadence). HTTPS is tunnelled, not inspected. A
project can also be given access to listed LAN addresses (off by default).

## Terminal client

On any computer that can reach the server (Python 3.11 or newer):

```sh
curl -fsSL http://<host>:<port>/cli/install.sh | sh
cntrl
```

It installs into `~/.local/share/cntrl-cli` (with a private venv for httpx and
textual unless the system Python already has both) and puts `cntrl` and `jav3`
launchers in `~/.local/bin` (`CNTRL_BIN` changes that). The first time, it asks
for the line from Settings > Access > Add computer, which gives it a chat-only
device token; `/login password` signs in with your password for full access
(security pages, secrets). It notices when the server has a newer client and
updates itself with `/update` (or `cntrl update`). `cntrl --version` prints its
version.

## Computer and browser use

Nothing here is on by default. To let an agent use a computer, install
`cntrl-desk` on it (from `http://<host>:<port>/cli/cntrl-desk`), pair it with
the Add computer line, and grant screen, input or shell in Settings. Shell also
needs `cntrl-desk allow-shell` typed at that computer's own keyboard. Be aware
that input amounts to a shell: with it granted, the agent can open a terminal
and type into it.

The Chromium extension (`http://<host>:<port>/cli/cntrl-browser.zip`, or
`clients/jav3-browser/`) pairs the same way and is granted per project. See
[docs/navigation.md](docs/navigation.md) and
[clients/jav3-browser/README.md](clients/jav3-browser/README.md).

## Backups

`cntrl-backup.timer` runs every hour once a remote is set (in Settings, or
`CNTRL_BACKUP_REMOTE` in the env file). It uploads memory, projects, agents,
skills and a snapshot of the database with rclone. Secrets go up only if you
turn that on, and only through an rclone crypt remote. Restore with
`python -m backend.cli restore`.

## Upgrading from Jav3

Update the old checkout and run its installer (`git pull --ff-only && bash
scripts/install.sh`), or run the bootstrap, which finds `~/jarvis` on its own.
The installer then:

- stops, disables and removes the old systemd user units (`jarvis.service`,
  `jarvis-pages.service`, `jarvis-backup.*`, `jarvis-image-rebuild.*`, or
  `jarvis-<name>-…` for a named instance), moves their drop-in directories to
  the new names, and installs and starts the `cntrl` ones. An image-rebuild
  timer you had installed comes back as `cntrl-image-rebuild.*`;
- renames Gitea's unit from `jav3-gitea-<port>.service` to
  `cntrl-gitea-<port>.service`, on the same data;
- moves `~/.config/jarvis` to `~/.config/cntrl` and `~/.local/share/jarvis` to
  `~/.local/share/cntrl` (when the install uses those default locations), but
  only when the new directory holds none of the server's files. Nothing is
  overwritten, and anything that couldn't be moved is named. The old state path
  stays behind as a symlink to the new one, so absolute paths inside the state
  keep working;
- rewrites `JARVIS_` lines in the env file as `CNTRL_` (the old file is kept as
  `env.bak`) and adds `CNTRL_INSTANCE_NAME=jav3`, so the server still answers
  as `jav3.local` on your network.

`bash scripts/install.sh --rename-only` does just this move, installs the new
units and restarts, without building anything. Running it a second time finds
nothing to do.

What keeps working:

- `JARVIS_*` settings are still read when the `CNTRL_*` one isn't set, and
  until the move the app runs from the old directories as they are.
- The `jav3` command, and `/cli/jav3` on the server, so old clients can update
  themselves.
- Logins and paired devices: the session cookie, the device token prefix, the
  database file (`jarvis.db`) and the Gitea bot account keep their names.
- The terminal client moves `~/.config/jav3` to `~/.config/cntrl` the first time
  it starts. If `~/.config/cntrl` is already there (the server's own config on
  the same machine), its files move in one by one beside the server's, a file
  already there wins, and you stay logged in. The computer-use client moves its
  own `desk*` files the same way.
- The computer-use client is now `cntrl-desk`. A saved `jav3-desk` and
  `/cli/jav3-desk` keep working, and its service keeps the name
  `jav3-desk.service` (`local.jav3.desk` on macOS), so reinstalling replaces
  the old one instead of running two.

## Security model

CNTRL treats the agent as untrusted. A prompt injection that reaches code
execution lands in a sandbox with no key, no secrets and no database. With
egress off it has no network; with egress on, its traffic goes through the
proxy described above. Your approvals, the write chokepoint, the diff checks
and the git gates are there in every permission mode; yolo only answers the
approvals for you.

What it doesn't protect against: anything you approve, or let the triage
reviewer or yolo approve; content the agent reads that steers it within what it
is allowed to do; and a flaw in the hypervisor, the host kernel or CNTRL's own
host code. [SECURITY-RESIDUAL-RISK.md](SECURITY-RESIDUAL-RISK.md) goes through
the remaining risks in detail. Read it before giving the agent a new
capability.

## Known limitations

- **Writes are live.** Agent file writes land in the project immediately. The
  diff checks are heuristics and only raise events. Look at `git diff` before
  you run project code you didn't write.
- **The Run panel runs on the host.** A project's Run panel runs one of the
  project's Python files on the host as the server's user, with resource limits
  and a timeout but no sandbox. Only you can start it, but the file may be one
  the agent wrote.
- **Exfiltration through an allowed host.** Anomaly detection limits how fast
  and in what shape something can leak to a host you allowed; it can't rule it
  out. HTTPS payloads are opaque to the proxy, secrets can't be filled into
  HTTPS requests from the sandbox, and DNS is a logged side channel.
- **Secret values aren't scrubbed everywhere.** `web_read` after a
  substitution, `/local` tool results and service logs replace secret values
  with placeholders; files the agent reads and `run_code` output reach the
  model as they are.
- **Captured model context.** The exact messages sent on each model call are
  stored on the host (on by default, kept 7 days, adjustable in Logs). They
  aren't scrubbed, they outlive a deleted chat, and they ride along in the
  unencrypted database backup.
- **No single audit log.** Security events, the egress log, the model call log,
  box history and the computer-use audit are separate views.
- **Plain HTTP.** The server speaks HTTP on your network, so logins, cookies and
  device tokens travel in the clear unless you put TLS in front of it.
- **Backups** upload the database (including password hashes) unencrypted.
- **Attribution on the shared VM.** When several projects run turns in the
  shared VM at the same time, their network traffic can't be told apart. Boxes
  of their own are attributed exactly.
- **Image updates.** The monthly image-rebuild timer
  (`scripts/cntrl-image-rebuild.*`) isn't installed by the installer.
- **Docker is weaker isolation** than a VM, and computer and browser use act
  with your own accounts and sessions on the sites and apps you grant.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --reload       # API on :8000
cd frontend && npm ci && npm run dev              # web app, proxying to :8000
```

Tests: `.venv/bin/python -m pytest -q` and `cd frontend && npm test`.
`scripts/test_on_linux.sh user@host` runs the suite on a Linux machine without
touching a live install there, and CI runs it on Ubuntu with Python 3.13.
Tests that need KVM, vsock or other host features skip themselves where those
are missing.

Further reading: [docs/SELF.md](docs/SELF.md) (the agent's own manual of the
system), [docs/docker-runtime.md](docs/docker-runtime.md),
[docs/desktop.md](docs/desktop.md), [docs/gitea.md](docs/gitea.md),
[docs/TUI-TESTING.md](docs/TUI-TESTING.md).

## Third-party

rclone (MIT) runs as a separate program for backups; the provider catalogue is
data from [models.dev](https://models.dev) (MIT); the web app ships Schibsted
Grotesk and Martian Mono (SIL OFL 1.1). See [NOTICE](NOTICE).

## License

Apache License 2.0. See [LICENSE](LICENSE).

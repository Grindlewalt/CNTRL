# CNTRL

CNTRL ("Controlled: Network, Tools, Runtimes and Logs") is a self-hosted server
that runs AI agents for one owner and keeps them contained. The agent's
reasoning loop runs in a disposable sandbox (a KVM virtual machine, or a
hardened Docker container) that holds no model API key, no database and no
secrets. The host keeps those, and the agent reaches the outside only through
it: model calls go through a host gateway, tools other than code execution run
on the host behind its approval gates, file changes reach your projects through
one host chokepoint, and network traffic, when you turn it on, goes through a
host proxy.

This is version 0.1.0, the first public release. Until this version the project
was called Jav3 (internally Jarvis); the old names keep working (see
[Upgrading from Jav3](#upgrading-from-jav3)).

## What it does

- **Sandboxed agent loop.** By default every chat and project runs in one
  shared KVM guest that the host reaches only over vsock. A project can instead
  run in a VM or a Docker container of its own (see
  [Sandboxes](#sandboxes)).
- **Model gateway.** Providers from the [models.dev](https://models.dev)
  catalogue: OpenAI-compatible APIs (DeepSeek, OpenAI, OpenRouter, Groq,
  Mistral, xAI and others, or a local server such as Ollama or LM Studio) plus
  native Anthropic and Google adapters. The key is stored on the host and never
  enters the sandbox; the gateway meters tokens and cost per call.
- **Permission modes per chat:** `safe-yolo` (the default: writes and commands
  run in the sandbox without asking; the requests listed below still wait for
  you), `auto` (a separate model call that sees only the tool name and
  arguments judges each write or command; risky ones ask you), `ask` (every
  write or command asks you) and `yolo` (nothing waits: every request is
  approved as it is made and logged as a security event). Yolo can be turned on
  only from a password login, after a confirmation.
- **Approvals.** These wait in the Security queue (web) or `/security`
  (terminal) until you answer: network hosts, extra packages, new sandbox
  images, skill installs, background services, git commits, remotes and
  pushes, publishing a page, memory notes the agent wrote, and new schedules
  (they start paused).
- **Projects as git repos.** File writes land in the project at once, through
  one host chokepoint that refuses path escapes and writes containing a stored
  secret value, and runs diff checks that raise security events but do not
  block. Commits wait for your approval. Every applied batch of agent writes is
  also kept on a hidden ref, so `/history` and `/undo` can bring back an
  overwritten or deleted file. Committed work can be cloned read-only over the
  server's own HTTP (`git clone http://<host>:<port>/git/<slug>`), pushed to
  a GitHub remote with a `GITHUB_TOKEN` secret, or mirrored to an optional
  local Gitea where agents open pull requests ([docs/gitea.md](docs/gitea.md)).
- **Memory.** Plain files on the host. A note the agent writes is pending (not
  in its context) until you approve it on the Memory page; a note written in a
  turn that read outside content carries a taint warning.
- **Multi-agent work.** Agents defined as folders, sub-agents and throwaway
  copies, an orchestrator that runs a plan of items (optionally each on its own
  branch, merged as one pull request you approve), a research pipeline, and
  scheduled runs.
- **Web reading.** `web_read` fetches pages host-side with an SSRF guard and
  reads PDFs a slice at a time (a scanned page comes back as an image for a
  model that takes images). Web
  search goes through a [SearXNG](https://docs.searxng.org) instance you run
  yourself; the installer does not install one (`CNTRL_SEARXNG_URL`, default
  `http://localhost:8080`).
- **Published pages.** The agent can ask to publish a static page; after you
  approve it, a separate read-only server (`cntrl-pages.service`, port 8090)
  serves it sandboxed to anyone who has the link.
- **Two front ends.** A web app (Work, Agents, Security, VMs, Tools, Settings,
  Memory, Schedules, Git) and a keyboard-first terminal client, `cntrl`.
- **Computer and browser use**, off until you pair a device and grant it (see
  [Computer and browser use](#computer-and-browser-use)).
- **Backups** of the state directory with rclone, hourly once you configure a
  remote ([Backups](#backups)).
- **Voice**, experimental: a `/voice` page that needs a separate speech
  sidecar on a GPU machine, set up by hand ([docs/voice.md](docs/voice.md)).

## Requirements

- Linux with apt (Debian, Ubuntu) or pacman (Arch). The installer also has a
  branch for dnf (Fedora), but no test covers it, and on arm64 it names the
  x86 firmware package.
- x86_64 or arm64 (aarch64); the installer refuses other architectures.
- For the default sandbox: KVM (`/dev/kvm`, CPU virtualization on in the
  firmware) and the `vhost_vsock` module. Without KVM, Docker can carry the
  agent turns of projects set to run in their own container; you install
  Docker yourself.
- About 4 GB of RAM. A Raspberry Pi 4 with 4 GB runs it (the shared VM takes
  768 MB by default).
- systemd user services, run as a normal user (not root).
- A model API key, or a local model server.

## Install

Run as the user the server will run as:

```sh
curl -fsSL https://raw.githubusercontent.com/Grindlewalt/cntrl/main/scripts/bootstrap.sh | sh
```

The bootstrap installs git if it is missing, clones the repository to
`~/cntrl` (or updates an existing checkout there, or one at `~/jarvis` from
before the rename) and runs `scripts/install.sh`. Environment variables:
`CNTRL_DIR` (where to clone), `CNTRL_REPO` (the git URL), `CNTRL_REF` (a branch
or tag), and `CNTRL_ALLOW_ROOT=1` to install for root anyway; each also
answers to its old `JARVIS_` name. Installer flags pass through:
`curl … | sh -s -- --port 8780 --no-gitea`.

Or by hand:

```sh
git clone https://github.com/Grindlewalt/cntrl.git ~/cntrl
cd ~/cntrl && bash scripts/install.sh
```

The few steps that need root are kept in one phase. `bash scripts/install.sh
--check` reports what is missing and changes nothing (`--check --json` prints
one JSON object; `--target user@host` checks another machine over ssh). When
root steps are owed, the installer prints the one command to run:
`sudo bash scripts/install.sh --root-phase --user <you>`. It installs only the
missing packages (QEMU and its UEFI firmware, Node and npm, Python's venv and
pip, git, curl, rsync, rclone, and a tool that writes the cloud-init seed),
never upgrading one that is already installed, loads `kvm` and
`vhost_vsock`, adds you to the `kvm` group and enables systemd linger, then
lists what it changed and how to revert each change. If virtualization is off
in the firmware, a person has to turn it on at the machine.

The unprivileged part then:

1. creates `.venv` and installs the Python requirements;
2. builds the web app (`npm ci`);
3. writes the config and installs the systemd user units: `cntrl.service` (the
   app, listening on all interfaces, port 8000 by default),
   `cntrl-pages.service` (published pages, port 8090) and `cntrl-backup.timer`;
4. builds the guest image (downloads a Debian cloud image, about 10 minutes;
   skipped without a usable `/dev/kvm`);
5. runs first-run setup: on a terminal it asks for your login, model provider
   and default security profile; otherwise it prints a one-time `/setup` link
   for a browser;
6. sets up Gitea (skip with `--no-gitea`);
7. when Docker is installed and usable by your user, builds the Docker box
   image and switches Docker boxes on (skip with `--no-docker`);
8. starts the service and checks `/api/health`.

Flags: `--port <n>`, `--name <name>` (a second instance: `cntrl-<name>.service`,
`~/.config/cntrl-<name>`), `--state-dir <dir>`, `--config-dir <dir>`,
`--from <host:path>` (copy state from another install first; `--force`
overwrites local state), `--no-image`, `--no-build`, `--no-gitea`,
`--gitea-dry-run`, `--no-docker`, `--yes` (no questions), `--rename-only`
(see [Upgrading from Jav3](#upgrading-from-jav3)). `bash scripts/install.sh
--help` lists them all.

A coding agent can do the install for you: paste the prompt at the end of
[docs/AGENT-INSTALL.md](docs/AGENT-INSTALL.md). That guide also covers second
instances, state directories, migrating from another machine and putting the
server behind TLS.

After installing:

- Settings live in `~/.config/cntrl/env` (mode 0600) as `CNTRL_<NAME>=value`
  lines (see `backend/config.py`); restart with `systemctl --user restart
  cntrl` after a change. Durable state (projects, memory, agents, skills, the
  database) lives in `~/.local/share/cntrl`.
- `~/cntrl/.venv/bin/python -m backend.cli doctor` says what is still missing.
- To upgrade: `cd ~/cntrl && git pull --ff-only && bash scripts/install.sh`.
  Every step checks before it acts, so a re-run is safe.

## Sandboxes

- **The shared VM.** One KVM guest runs every chat and project by default. It
  is not wiped between turns unless you set `CNTRL_VM_IDLE_SCRUB_SECONDS` (off
  by default); it is rebuilt from the read-only image when it restarts.
- **Boxes of their own.** With `CNTRL_VM_BOXES_ENABLED=true` (off by default;
  the installer's Docker step turns it on when Docker works) a project whose
  security profile or "Runs in" setting says so gets its own VM or container,
  stopped after 10 minutes idle. With boxes off, every project runs in the
  shared VM whatever its setting says. Boxes also enable service boxes,
  package requests, image variants (such as `desktop`, with a screen and a
  browser) and the headless browser tool.
- **Docker.** A container per box with no network interface (`--network
  none`), reaching the host only over two unix sockets
  ([docs/docker-runtime.md](docs/docker-runtime.md)). It is lighter and weaker
  than a VM: it shares the host kernel unless you configure gVisor
  (`CNTRL_DOCKER_OCI_RUNTIME=runsc`). On a host without KVM, chats outside a
  project cannot run, and a project runs only when set to its own container.

## Network access for agents

Egress is off by default, and a fresh install leaves it off: the sandbox has no
network at all. Web search and `web_read` still work, because they run on the
host.

Turning it on is a manual step today. Set `CNTRL_VM_EGRESS=1` in the env file;
the host also needs nftables, dnsmasq and tcpdump, and a sudoers rule that lets
the service user run `sudo -n bash <checkout>/vm/net/net_up.sh …` (and
`vm/net/net_box.sh` when boxes are on) without a password. The installer does
none of this.

With egress on, the sandbox's only route out is a host proxy. nftables drops
the LAN and anything that bypasses the proxy, and DNS goes through a logged
host resolver. The proxy applies the project's security profile: the default
profile allows a short list of package registries and GitHub hosts, and refuses
any other new host and queues it for approval. A separate model call, the
triage reviewer ("Auto review", on by default, no tools and no network), sweeps
the queue every five minutes and can approve hosts it judges routine; you can
switch it off in Security. The proxy meters bytes per host, injects
`{{secret:NAME}}` placeholders the project was granted into plain HTTP
requests, and cuts a host when traffic looks like exfiltration (volume, entropy,
cadence). HTTPS is tunnelled, not inspected. A project can also be given access
to listed LAN addresses (off by default).

## Terminal client

On any computer that can reach the server (Python 3.11 or newer):

```sh
curl -fsSL http://<host>:<port>/cli/install.sh | sh
cntrl
```

It installs into `~/.local/share/cntrl-cli` (with a private venv for httpx and
textual unless the system Python has both) and puts `cntrl` and `jav3`
launchers in `~/.local/bin` (`CNTRL_BIN` changes that). On first run it asks
for the line from Settings > Access > Add computer, which gives a chat-only
device token; `/login password` signs in with your password for full access
(security pages, secrets). It checks the server for a newer client and updates
itself with `/update` (or `cntrl update`). `cntrl --version` prints its
version.

## Computer and browser use

Nothing is on by default. To let an agent use a computer, install `jav3-desk`
on it (from `http://<host>:<port>/cli/jav3-desk`), pair it with the Add
computer line, and grant screen, input or shell in Settings; shell also needs
`jav3-desk allow-shell` at that computer's keyboard. The Chromium extension
(`http://<host>:<port>/cli/cntrl-browser.zip`, or `clients/jav3-browser/`)
pairs the same way and is granted per project. See
[docs/navigation.md](docs/navigation.md) and
[clients/jav3-browser/README.md](clients/jav3-browser/README.md).

## Backups

The `cntrl-backup.timer` runs hourly once a remote is set (Settings, or
`CNTRL_BACKUP_REMOTE` in the env file). It uploads memory, projects, agents,
skills and a snapshot of the database with rclone. Secrets go up only if you
turn that on, and only through an rclone crypt remote. Restore with
`python -m backend.cli restore`.

## Upgrading from Jav3

Update the old checkout and run its installer (`git pull --ff-only && bash
scripts/install.sh`), or run the bootstrap, which picks up `~/jarvis`. The
installer then:

- stops, disables and removes the old systemd user units (`jarvis.service`,
  `jarvis-pages.service`, `jarvis-backup.*`, `jarvis-image-rebuild.*`, or
  `jarvis-<name>-…` for a named instance), moves their drop-in directories to
  the new names, and installs and starts the `cntrl` ones. An image-rebuild
  timer you had installed comes back as `cntrl-image-rebuild.*`;
- renames Gitea's unit from `jav3-gitea-<port>.service` to
  `cntrl-gitea-<port>.service`, on the same data;
- moves `~/.config/jarvis` to `~/.config/cntrl` and `~/.local/share/jarvis` to
  `~/.local/share/cntrl` (when the install uses those default locations), only
  when the new directory holds none of the server's files. Nothing is
  overwritten; anything that could not move is
  named. The old state path is left as a symlink to the new one, so absolute
  paths inside the state keep working;
- rewrites `JARVIS_` lines in the env file as `CNTRL_` (the old file is kept as
  `env.bak`) and adds `CNTRL_INSTANCE_NAME=jav3`, so the server still answers
  as `jav3.local` on the LAN.

`bash scripts/install.sh --rename-only` does only this move, installs the new
units and restarts, without building anything. A second run finds nothing to
do.

What keeps working: `JARVIS_*` settings are read when the `CNTRL_*` one is
unset; until the move, the app runs from the old directories as they are; the
`jav3` command; the server still serves `/cli/jav3`, so old clients update from
it; the session cookie, the device token prefix, the database file
(`jarvis.db`) and the Gitea bot account keep their names, so logins and paired
devices stay valid. The new terminal client moves `~/.config/jav3` to
`~/.config/cntrl` on its first start, unless `~/.config/cntrl` already exists
(for example the server's own config on the same machine): then the old
directory is left alone and you log in again.

## Security model

The agent is treated as untrusted. A prompt injection that reaches code
execution lands in a sandbox with no key, no secrets and no database. With
egress off it has no network; with egress on its traffic crosses the proxy
described above. Your approvals, the write chokepoint, the diff checks and the
git gates hold in every permission mode; yolo only answers the approvals for
you.

What it does not protect against: anything you approve, or let the triage
reviewer or yolo approve; content the agent reads that steers it within what it
is allowed to do; a flaw in the hypervisor, the host kernel or CNTRL's own host
code. [SECURITY-RESIDUAL-RISK.md](SECURITY-RESIDUAL-RISK.md) lists the residual
risks in detail; read it before giving the agent a new capability.

## Known limitations

- **Writes are live.** Agent file writes land in the project immediately. The
  diff checks are heuristic and only raise events. Review `git diff` before you
  run project code you did not write.
- **The Run panel runs on the host.** A project's Run panel runs one of the
  project's Python files on the host as the server's user, with resource limits
  and a timeout but no sandbox. Only you can start it, but the file may be one
  the agent wrote.
- **Exfiltration through an allowed host.** Anomaly detection limits the rate
  and shape of a leak to a host that is allowed, not its possibility. HTTPS
  payloads are opaque to the proxy, secrets cannot be injected into HTTPS from
  the sandbox, and DNS is a logged side channel.
- **Secret values are not scrubbed in general.** `web_read` after a
  substitution, `/local` tool results and service logs replace secret values
  with placeholders; files the agent reads and `run_code` output reach the
  model as they are.
- **Captured model context.** The exact messages sent on each model call are
  stored on the host (on by default, kept 7 days, adjustable in Logs), are not
  scrubbed, outlive a deleted chat and ride in the unencrypted database backup.
- **No single audit log.** Security events, the egress log, the model call log,
  box history and the computer-use audit are separate views.
- **Plain HTTP.** The server speaks HTTP on the LAN; logins, cookies and device
  tokens travel in the clear unless you put TLS in front.
- **Backups** upload the database (including password hashes) unencrypted.
- **Attribution on the shared VM.** When several projects run turns in the
  shared VM at once, its network traffic cannot be told apart; boxes of their
  own are attributed exactly.
- **Image updates.** The monthly image-rebuild timer
  (`scripts/cntrl-image-rebuild.*`) is not installed by the installer.
- **Docker is weaker isolation** than a VM, and computer and browser use act
  with your own accounts and sessions on the sites and apps you grant.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --reload       # API on :8000
cd frontend && npm ci && npm run dev              # web app with a dev proxy to :8000
```

Tests: `.venv/bin/python -m pytest -q` and `cd frontend && npm test`.
`scripts/test_on_linux.sh user@host` runs the suite on a Linux machine without
touching a live install there; CI runs it on Ubuntu with Python 3.13. Tests
that need KVM, vsock or other host features skip where those are missing.

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

# CNTRL

CNTRL ("Controlled: Network, Tools, Runtimes and Logs") is a self-hosted trusted
host for untrusted AI agents. The agent's reasoning loop runs in a disposable
virtual machine that holds no API key, no database and no secrets; the host
keeps the keys, the network policy, the tool broker and the logs.

Every capability the agent has (model calls, tools, file writes, network
access) goes through the host, which can gate, record and cut it. CNTRL was
called Jav3 before v0.1.0.

## What it does today

- Runs the agent loop in a KVM guest reachable only over vsock, or in a
  hardened Docker container on hosts without KVM.
- Model gateway: the API key stays on the host; the guest reaches the model only
  through it.
- Network egress is off by default. When on, the guest's only route out is a
  host proxy with per-project policy, an approval queue for new hosts, secrets
  injected on the wire for plain HTTP, byte metering and an anomaly cut.
- Tools are folders (`tools/<name>/TOOL.md` and a handler), as are agents and
  skills; all go through a broker on the host.
- File writes cross one host chokepoint that refuses path escapes and writes
  containing a real secret value, and runs diff checks that raise security
  events. Projects are git repos; commits, pushes and remotes need approval.
- File-backed memory; notes the agent writes wait for your approval.
- Multi-agent runs (sub-agents, a research pipeline, schedules).
- A web app and a terminal client, with permission modes (safe-yolo, auto, ask,
  yolo), published pages, web search and reading, Gitea for agent pull
  requests, backups through rclone, and an optional voice sidecar.
- Computer and browser use: pair a computer (`clients/jav3-desk`) or a
  Chromium extension (`clients/jav3-browser`). Nothing is on by default; see
  [docs/navigation.md](docs/navigation.md).

## Install

Requirements: Linux (Debian/Ubuntu, Arch or Fedora), x86_64 or arm64, about
4 GB of RAM, and KVM or the hardened Docker runtime. A Raspberry Pi 4 works.
Run it as the user the server will run as, not root:

```
curl -fsSL https://raw.githubusercontent.com/Grindlewalt/cntrl/main/scripts/bootstrap.sh | sh
```

It clones to `~/cntrl` (`CNTRL_DIR=…` changes that) and hands over to
`scripts/install.sh`; installer flags pass through
(`| sh -s -- --port 8780`). Or by hand:

```
git clone https://github.com/Grindlewalt/cntrl.git ~/cntrl
cd ~/cntrl && bash scripts/install.sh
```

The steps that need root are collected into one phase:
`bash scripts/install.sh --check` reports what is missing and changes nothing,
`sudo bash scripts/install.sh --root-phase` does the root part. If the CPU's
virtualization extension is off in firmware and you have no Docker, a person
has to turn it on.

The installer ends at first-run setup: on a terminal it asks for your login and
model provider; otherwise it prints a one-time link to do the same in a
browser. A coding agent can do the install for you with the prompt at the end of
[docs/AGENT-INSTALL.md](docs/AGENT-INSTALL.md). Re-running `scripts/install.sh`
is the upgrade path. More (second instance, state dir, backups, moving hosts,
reverse proxy): `docs/AGENT-INSTALL.md` and `docs/SELF.md`.

## Terminal client

On any computer that can reach the server:

```
curl -fsSL http://<host>:<port>/cli/install.sh | sh
cntrl
```

It asks for the line from Settings > Add computer. It updates itself from the
server (`/update` in the client, or `cntrl update`).

## Upgrading from Jav3

Old names keep working. `JARVIS_*` settings are read when the `CNTRL_*` one is
unset, and `jav3` stays as a second launcher. The installer moves
`~/.config/jarvis` and `~/.local/share/jarvis` to their `cntrl` names, stops and
disables the old `jarvis*` systemd user units and enables the `cntrl*` ones. An
existing checkout stays where it is. Re-run `scripts/install.sh` from it.

## Security model

- The agent is untrusted. A prompt injection that reaches code execution lands
  in a guest with no secrets, no LAN access and a watched, policy-gated network
  path (when egress is on).
- Netless by default: egress is off until you turn it on.
- What it does not protect against: anything you approve, and anything you grant
  by turning on yolo (which approves every request) or computer use; content
  the agent reads from the web that steers it within what it is allowed to do;
  HTTPS traffic is tunneled, not inspected; a flaw in the hypervisor or in the
  host code. Read [SECURITY-RESIDUAL-RISK.md](SECURITY-RESIDUAL-RISK.md) before
  trusting the agent with a new capability.

## Development

Backend: `uvicorn backend.main:app --reload`. Frontend: `cd frontend && npm run
dev`. Tests: `pytest`. Settings come from the environment or
`~/.config/cntrl/env`, prefix `CNTRL_` (see `backend/config.py`).

## Third-party

rclone (MIT) runs as a separate program for backups; the provider catalogue is
data from [models.dev](https://models.dev) (MIT); the web app ships Schibsted
Grotesk and Martian Mono (SIL OFL 1.1). See [NOTICE](NOTICE).

## License

Apache License 2.0. See [LICENSE](LICENSE).

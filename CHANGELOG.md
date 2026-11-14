# Changelog

## Unreleased (dev)

UI v2 of the web app (plan in `docs/ui-v2-plan.md`).

- Components: every dropdown is one glass, searchable `Select` (recents on 1 to 9, full keyboard, a bottom sheet on touch screens); new `HelpTip`, `SidePanel`, row menus where right-click opens the row's ⋯ menu, hairline-divided lists, and `Modal` as a form dialog.
- Work sidebar: pinned projects at the top (stored on the server), right-click menus on every row, a title search with a deep search over user and assistant message text (4 second limit, paged) whose hits open the chat at the message, and project names that can't start with a number (refused by the server on create and rename).
- Project home (`/project/<slug>`, shown inside Work with the chat sidebar kept, or `/project <name>` in a composer): a composer that starts a chat in the project, recent chats, instructions saved as `instructions.md` and loaded into every prompt, uploaded context files, memory and schedules; `/project pin <name>` now pins the chat to a project.
- Chat top bar: plain buttons (Project, Temporary chat, Mode, Runs) that open a side panel with the project's security settings, its files (a viewer for Markdown, HTML, PDF, images and code, an editor, downloads and a zip) and runs.
- File saves from the web app go through the write chokepoint, so protected paths and files holding a secret's value are refused.
- Agents: the roster follows the Work sidebar, clicking an agent opens a chat with it, Edit moved to the row menu, and skills import from a link (a GitHub repo or folder, a raw SKILL.md, a zip) or a dropped folder or zip, arriving off until granted.
- Security: the Queue is a dashboard of what waits for you, each request led by a summary the agent must write (at most two sentences, checked before anything is filed), with Deny, Inspect (the chat step that asked, and the full diff for git) and Approve, plus the running boxes at a glance; the Security history moved to Logs.
- Network and Profiles: the last 30 decisions show their profile, with an All page with search and filters; profiles are edited in a Modal, and secrets get one field per site.
- Network approvals go on the profile's always-allow list (the operator's decision): approving a site for one project lets every project on that profile reach it, Auto review's approvals included. A project's own entries moved onto its profile at upgrade, nothing dropped. Packages and services may be requested by default (each request still waits for approval).
- Setup: agents may request packages and background services by default; each request still waits for approval.
- Research for Settings > Connectors (BETA) over MCP (`docs/connectors-research.md`); no code yet.

## 0.1.0 (2026-11-10)

First public release, under the Apache License 2.0. The project was called
Jav3 (internally Jarvis) until this version; see
[Upgrading from Jav3](#upgrading-from-jav3). The version is in
`backend/version.py`; `/api/health`, `doctor`, the installer and
`cntrl --version` report it.

### Runtime and VMs

- Every agent turn runs its loop inside a disposable sandbox with no model
  key, no database and no secrets. There is no host-side agent loop.
- The default sandbox is one shared KVM guest, reachable from the host only
  over vsock, booted from a read-only golden image with a fresh overlay on each
  start. `vm/build_base.sh` builds the image (Debian, with Python, Node, npm,
  git and pytest); a versioned rebuild and a monthly timer unit are included (the installer
  does not install the timer).
- Boxes (`CNTRL_VM_BOXES_ENABLED`, off by default): a project can run in a VM
  or a Docker container of its own, chosen per security profile or per project
  ("Runs in"), or join another project's box. Idle project boxes stop after 10
  minutes; a RAM budget caps how many run at once, and an idle box gives way to
  a turn that needs the room.
- A hardened Docker runtime for boxes: no network interface, rootless or
  user-namespace remapping supported, all capabilities dropped, read-only root,
  memory and process limits, and the host reached only over two unix sockets.
  `backend.cli docker-setup` (run by the installer when Docker works) builds the
  image and switches it on. On a host without KVM, projects set to their own
  container can still run turns.
- Service boxes for background services an agent asks for and you approve,
  with an optional relay to loopback or a dedicated LAN address.
- Image variants: `main` and `desktop` (a screen and a browser), extra packages
  through approved package requests, and environment requests that move a
  project onto an image or propose a new one.
- A live view of a desktop box in the web app (noVNC); clicking the screen
  takes control from the agent until you hand it back.
- A memory guard: code and screenshots run in a capped memory group, so an
  out-of-memory browser no longer kills the turn, and a lost guest's cause is
  reported.
- An operator shell into the shared guest (a Terminal panel and
  `backend.cli guest-shell`).
- The VMs page and `/vms`: each box's state, projects, RAM, idle timer and
  history, with restart, stop and a cleanup of leftover processes.
- Process and connection telemetry from each box, checked against the host's
  own view; unexpected processes raise alerts.

### Network and egress

- Egress is off by default; a fresh install leaves the sandbox with no network.
- With egress on, the only route out is a host proxy behind an nftables floor
  that drops the LAN and anything bypassing the proxy, with DNS through a
  logged host resolver.
- Security profiles carry the network rule (ask about each new site, allow new
  sites, or no network), allow and deny lists, secret grants and where projects
  run; each project keeps its own lists on top. One profile is the default and
  is set at first run.
- New hosts wait in an approval queue: allow always, allow for an hour, or
  deny, onto the project's own list.
- A triage reviewer ("Auto review", on by default): an isolated model call with
  no tools that clears routine queue items and flags the rest. A separate
  egress auto mode (off by default) can allow hosts for a limited time.
- Byte metering, a live network feed, and an automatic cut of a host on
  exfiltration-shaped traffic (volume, entropy, cadence); a cut can be undone.
- `{{secret:NAME}}` placeholders injected on the wire for plain HTTP, only for
  secrets the project is granted. HTTPS is tunnelled, not inspected.
- Per-project LAN access to listed addresses, off by default.

### Tools, approvals and permission modes

- Tools are folders (`tools/<name>/TOOL.md` and a handler) that reload on edit.
  The model sees a core of ten tools plus a `tools` loader; the rest load by
  section on demand, and old tool names still work.
- Tool arguments are checked against the schema and the handler, and a
  misspelled key is named back to the model.
- Permission modes per chat: safe-yolo (the default), auto (a context-free
  judge reviews each write or command and risky ones ask), ask (every write or
  command asks), and yolo (every request is approved as it is made and logged).
  Yolo needs a password login and a confirmation; shift+tab never reaches it.
  "Always allow" rules are per project and revocable.
- `ask_user`: the agent asks you one or more questions and waits.
- Approval queue for packages, environments and images, skill installs,
  services, git requests, published pages, memory notes and held journal lines.
  Deciding a request clears its alert.
- Skills: write them in the web app, import one from a folder, a .zip or a
  GitHub link (pinned, ungranted), or approve one an agent asks for.
- `report_harness_fault`: the agent reports a tool that misbehaves; reports
  get their own list in the queue.

### Security events and logs

- Security events record the run, conversation and box that caused them, are
  grouped one card per run, and have tiers, coalesced repeats, rate limits and
  per-kind notification settings. Rules can file normal work quietly.
- Do not disturb in the web app and the terminal (`/dnd`).
- Actions on an alert: revert a file, un-cut a host, allow a program.
- Logs: transcripts, the model calls log with gateway refusals, token and cost
  totals, and captured model context (on by default, compressed, kept 7 days,
  adjustable, with a storage warning). Incognito chats capture nothing.
- Box history for each box's starts, stops and actions.

### Memory

- File-backed memory: a persona, standing notes, user and environment files,
  assembled into each turn's context with your rules restated last.
- Notes the agent writes are pending until you approve them on the Memory
  page; a write onto an approved note becomes a proposal with a diff; deleted
  notes go to a trash you can restore from.
- Taint: a note written in a turn that read outside content (web, screen,
  files, another agent's report, network bytes) carries a warning naming the
  source, and taint passes from child agents to their parent.
- Always-loaded files: you choose which project files ride every prompt, and a
  tainted turn's change to one is held for approval.
- Notes that would weaken a guard are refused.

### Projects, git, Gitea and orchestrator PRs

- Every project is a git repo from creation. Agent writes land at once through
  one host chokepoint that refuses path escapes and stored secret values, with
  advisory diff checks that raise security events.
- Commits, remotes and pushes wait for approval; a new commit request replaces
  the pending one. Harness files stay out of commits.
- Edit history: every batch of agent writes is kept on a hidden ref and handed
  home about every minute during a turn; `/history` and `/undo` bring files
  back.
- Committed work is served read-only over HTTP with your login
  (`git clone http://<host>:<port>/git/<slug>`); `/pull` prints the commands.
- GitHub remotes with a `GITHUB_TOKEN` secret.
- Gitea on the host (`backend.cli gitea-setup`, run by the installer): agents
  file pull requests from `agent/*` branches with `git_push_request`, you merge
  them on the Git page; other accounts get read access to every repo.
- Orchestrator plans can run items on branches and end in one pull request you
  approve; approving merges it into main.
- Projects have a trash with restore.

### Agents, plans and schedules

- Agents are folders with a prompt, tools and model; sub-agents, throwaway
  copies (`spawn_temp_agent`) and agent presets (`/persona`).
- The orchestrator turns a request into a plan of items run by agents that can
  message each other, with soft dependencies, per-item round caps, retries and
  a Plan panel.
- Operator messages into a running turn; a turn that died can be resumed.
- A tree of every running chat, plan, schedule and sub-agent, in the web app
  and the terminal.
- Schedules the agent proposes start paused until you approve them; deleted
  schedules go to a trash.
- Providers from the models.dev catalogue (OpenAI-compatible APIs, local
  servers, Anthropic and Google adapters), keys kept on the host, model choice
  per chat, agent and schedule, and a plain warning when a provider's balance
  runs out.

### Research, search and web reading

- `web_read` fetches pages host-side behind an SSRF guard, with no silent cut
  (`#part=N`), fallbacks for thin pages, and PDFs a slice at a time; a scanned
  PDF page comes back as an image for a model that takes images.
- Web search through a SearXNG instance you run (not installed by the
  installer). It reports degraded engines, leaves out off-topic results and
  asks again without the engines that gave junk; `doctor` checks it.
- A research pipeline (scout, readers, synthesis) that stops on junk results.
- `headless_browser` (with boxes on) for pages that need JavaScript, and
  `screenshot` to render a page or a project file.

### Published pages

- An agent can ask to publish a static page from its project. After you
  approve it, `cntrl-pages.service` (port 8090) serves the snapshot to anyone
  with the link, under a sandbox CSP that keeps it away from your session.

### Web app

- Work: the chat with its project's windows beside it (up to four), slash
  commands, inline approvals and questions, a running view of tool steps, and a
  stop menu for this project or everything.
- Pages for Agents, Security (queue, network, profiles, secrets, logs,
  persistent processes), VMs, Tools, Settings (Models, Alerts, Access, System),
  Memory, Schedules, Git and a terminal-style Shell.
- One shared event stream per browser, whatever the number of tabs.
- Phone layouts.
- A restyle in the look of the project's website, with self-hosted fonts.

### Terminal client

- `cntrl` (formerly `jav3`): a full-screen client with tabs and tiles, an
  agents drawer and a sessions drawer, a sidebar, the model's thinking under
  ctrl+o, and `/security` and `/vms` pages.
- Logs in with a chat-only device code or a password session.
- `/local` chats run file and shell tools on the client's own machine, asking
  before every write and command.
- Updates itself from its server (`/update`, `cntrl update`), and `/restart`
  keeps open tabs and chats.
- Themes (built-in and your own), `/screenshot`, `/export`, `/transcript`,
  `/pull`, `/helper`, `/history`, `/undo`, `/dnd`, `/resume`.
- Says when the connection or the model has gone quiet, and re-attaches to a
  turn after a dropped stream.

### Computer and browser use

- `jav3-desk` pairs a macOS or Linux computer: screenshots with a list of
  clickable elements, clicks by element, description or coordinates, typing,
  keys and opening apps. Server-side grants sit under a ceiling set at the
  computer; shell stays off until allowed at its keyboard; `jav3-desk panic`
  stops everything. Locked or sleeping screens are refused.
- A Chromium extension pairs a browser with per-project Read and Act grants,
  its own window, per-site consent and real mouse clicks.
- A grounding model finder (Settings > Access) tests which enabled image
  model clicks best on labelled test screens; a screenshot sent to it is logged as a security event.
- Nothing is on until you pair and grant.

### Voice

- An experimental `/voice` page: wake word, interruption, a local fast model
  tier, music and a projector. It needs the separate `voicebox` speech sidecar
  on a GPU machine, set up by hand (docs/voice.md), and HTTPS for the
  microphone.

### Install, upgrade and the rename

- A one-line bootstrap (`scripts/bootstrap.sh`) and a re-runnable installer
  with a read-only check (`--check`, `--json`, `--target` for another
  machine), one root phase that installs only missing packages and never
  upgrades installed ones, named second instances (`--name`), a chosen port
  and state directory, and state migration from another host (`--from`). A new
  named instance gets its own state directory and its own pages and Gitea
  ports; one that already exists keeps what it had.
- First-run setup in the terminal or a browser (a one-time link), and
  `backend.cli doctor` (with `--json`) for what is still missing.
- `docs/AGENT-INSTALL.md`: a guide and a prompt for a coding agent to do the
  install for you.
- Device login by a single-use code; device tokens expire after 90 days, or 30
  unused, and revoking one stops its running turns. Failed logins are
  throttled.
- Backups of the state directory with rclone, hourly once a remote is set;
  secrets only through an rclone crypt remote.
- The server advertises itself on the LAN over mDNS.
- The rename to CNTRL, with the old names still working (below).

### Upgrading from Jav3

Update the old checkout and run `scripts/install.sh` (or the bootstrap, which
picks up `~/jarvis`). `bash scripts/install.sh --rename-only` does only the
move below, installs the new units and restarts.

- The old user units (`jarvis.service`, `jarvis-pages.service`,
  `jarvis-backup.*`, `jarvis-image-rebuild.*`, or `jarvis-<name>-…`) are
  stopped, disabled and removed, their drop-ins moved, and the `cntrl` units
  installed. An installed image-rebuild timer comes back as
  `cntrl-image-rebuild.*`.
- Gitea's `jav3-gitea-<port>.service` becomes `cntrl-gitea-<port>.service` on
  the same data.
- `~/.config/jarvis` moves to `~/.config/cntrl` and `~/.local/share/jarvis` to
  `~/.local/share/cntrl` when the install uses those default locations and the
  new directory holds none of the server's files. Nothing is overwritten;
  anything left behind is named. The old state path stays as a symlink to the
  new one.
- `JARVIS_` lines in the env file become `CNTRL_` (the old file is kept as
  `env.bak`), and `CNTRL_INSTANCE_NAME=jav3` keeps the server answering as
  `jav3.local`.
- `JARVIS_*` settings are still read when the `CNTRL_*` one is unset, and the
  app runs from the old directories until they are moved.
- The `jav3` command stays as a second launcher, and the server still serves
  `/cli/jav3` and `/cli/jav3-browser.zip`. The new terminal client installs
  into `~/.local/share/cntrl-cli` and moves `~/.config/jav3` to
  `~/.config/cntrl` on first start; into the server's own `~/.config/cntrl`
  on the same machine its files move one by one, a name already there winning,
  so you stay logged in. The computer-use desktop client's `desk*` files follow.
- The computer-use client is `cntrl-desk` (`/cli/cntrl-desk`). A saved
  `jav3-desk` file and `/cli/jav3-desk` keep working, and its service keeps the
  name `jav3-desk.service` (`local.jav3.desk` on macOS) so an installed one is
  replaced, not doubled.
- The session cookie, the device token prefix, the database file
  (`jarvis.db`) and the Gitea bot account keep their names, so logins and
  paired devices stay valid.

# Dev install

The dev install is for developers and testers of CNTRL. It installs the `dev`
branch, where changes are tested before they reach `main`. `dev` can break and
can change under you, so don't run it for your own everyday work: for that,
install the stable `main` as the [README](../README.md#install) describes.

A dev install is a normal install plus one project, `dev-agent`, in which a
CNTRL agent uses every feature from the inside and reports what to improve.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/Grindlewalt/cntrl/dev/scripts/bootstrap.sh | sh -s -- --dev
```

With `--dev` the bootstrap:

1. clones the `dev` branch (set `CNTRL_REF` to use another branch) into
   `~/cntrl-dev` (set `CNTRL_DIR` to use another directory), so a stable
   checkout at `~/cntrl` is never touched. A checkout already at `~/cntrl-dev`
   is switched to `dev` and pulled; git refuses if that would lose local
   changes.
2. hands over to `scripts/install.sh --dev`, which does everything a normal
   install does (the root step, the venv, the web app, the guest image,
   first-run setup, Gitea, Docker boxes) and then runs
   `python -m backend.cli dev-agent-setup`.

Every other installer flag works as usual, for example
`sh -s -- --dev --no-image`. From a checkout of your own, switch it to `dev`
and run `bash scripts/install.sh --dev` in it.

## Beside a stable install

```sh
curl -fsSL https://raw.githubusercontent.com/Grindlewalt/cntrl/dev/scripts/bootstrap.sh | sh -s -- --dev --name dev --port 8781
```

`--name dev` makes it a second instance with its own checkout
(`~/cntrl-dev`), unit (`cntrl-dev.service`), config (`~/.config/cntrl-dev`),
state (`~/.local/share/cntrl-dev`) and web port (8781). Its published pages and
its Gitea take the first free ports from 8091 and 3001, written once to
`~/.config/cntrl-dev/env`, so it never takes over the stable instance's 8090,
3000 or Gitea unit. (Without `--dev`, a named instance shares the default
state dir unless you pass `--state-dir`.)

What is not separated yet, on one host:

- **VMs.** The host listens for VM guests on one fixed vsock port, so only
  the instance that starts first can run VM turns. The other one still serves
  the web app, projects, settings and Gitea, and its log says
  `vsock gateway disabled`.
- **Docker boxes** work in both instances, but a container is named after its
  box (`jav3-p-<project>`), not the instance, so don't give both instances a
  project with the same name.

To test agent turns in VMs, use a dev install on a machine of its own.

## The dev-agent project

`dev-agent-setup` makes sure of these, and says what it found for each. It is
safe to run again; it changes only what is missing.

- **Project `dev-agent`.** Its files are CNTRL's own code: a copy of the
  install's checkout made on the host with git from a local path, so no
  network is needed. It follows the checkout's `dev` branch when there is one,
  else the branch the checkout is on. Changing the project's files never
  changes the running server.
- **Instructions.** The project's `project.md` says what the project is and how
  to work: use every feature like a user, read and run the code in the sandbox,
  never say something works without trying it, keep reports in
  `reports/YYYY-MM-DD-<topic>.md`, and file each concrete improvement as an
  issue. An existing `project.md` is kept (the agent's journal grows in it).
- **The skill `cntrl-try-everything`.** A checklist of what to try, what working
  looks like and what to write down, for chats and permission modes, files and
  undo, git commits and pull requests, memory, web reading and research,
  published pages, schedules, agents, the Security queue, what the sandbox can
  and cannot reach, and running CNTRL's code. Skills in CNTRL are not per
  project: it is enabled for every chat on this instance.
- **Gitea.** When Gitea is set up, the project's repo there, which is where
  the agent's issues go. Without Gitea the agent keeps its issues in its
  reports; run `python -m backend.cli gitea-setup`, then `dev-agent-setup`
  again.
- **A box of its own.** When boxes are on (`CNTRL_VM_BOXES_ENABLED=true`, which
  the installer's Docker step sets when Docker works), the project runs in its
  own box, unless you already chose its "Runs in". When boxes are off it runs
  in the shared VM, like every project. The step never turns boxes or egress
  on.

The agent opens issues with the `gitea_issue` tool (in the git section, action
`issue`). It opens them on the current project's repo only, as the `jav3-agent`
bot, through the host: the Gitea token never enters the sandbox. Each issue is
recorded as a security event. In ask mode you are asked first and in auto mode
the judge reviews it first; in safe-yolo and yolo it is opened at once.

## Give it a strong model

The checklist is long and needs a model that follows instructions over many
steps. Add the provider and its API key yourself, in the web app under
Settings, Models tab, API providers (first-run setup asks for one too), and
pick the model there. Never put a key in a file, in the env file by hand, in a
chat or in the project: the key stays with the host's model gateway, and the
agent never sees it.

## Run it

1. Open the dev-agent project: the Work page in the web app, or `/project` in
   the terminal client.
2. Ask: "Run the cntrl-try-everything skill." To do one part, name it, for
   example "Run the git part of the cntrl-try-everything skill."
3. Stay nearby. Many steps need you: approving or rejecting a request in the
   Security queue, switching the permission mode, running `/history` or
   `/undo`, telling the agent what a page or card showed.

## Where the results land

- **Reports** are files in the project, `reports/YYYY-MM-DD-<topic>.md`. Read
  them in the web app's editor, or on the host under the state dir:
  `~/.local/share/cntrl-dev/projects/dev-agent/reports/` beside a stable
  install, `~/.local/share/cntrl/projects/dev-agent/reports/` otherwise
  (`python -m backend.cli paths projects_dir` prints it). When a report is
  done the agent asks for a commit, which you approve like any other.
- **Issues** are on the project's Gitea repo,
  `http://<host>:<gitea port>/<your login>/dev-agent/issues`, opened by
  `jav3-agent`. Each one is also a `gitea_issue_opened` event on the Security
  page.

## Keep it current

Upgrade the install, then bring the project's copy of the code up to it:

```sh
cd ~/cntrl-dev && git pull --ff-only && bash scripts/install.sh --dev
.venv/bin/python -m backend.cli dev-agent-setup --refresh
```

`--refresh` fast-forwards the project to the checkout's `dev` when the
project's tree is clean. When the project has commits of its own (reports you
approved), the checkout's new commits are merged in instead. It refuses, and
says why, when the project has uncommitted changes (approve or reject the
agent's commit request, or put the files back) or when the merge would
conflict (nothing is changed then). With Gitea on it first takes in pull
requests merged there and pushes the result afterwards. `--refresh` also puts
the shipped `cntrl-try-everything` skill back if you edited your copy.

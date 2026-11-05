# nov4: published pages, and yolo split in two (contract)

Base: branch `nov4` at the commit that adds this file. Every agent starts with
`git reset --hard <that sha>` and touches ONLY the files it owns.

## The operator's decisions (2026-11-04, asked; do not re-ask)

- **Publish:** a file or folder of static documents (HTML, PDF, images, text, video)
  served at its own unguessable link on the Pi, on a separate port (8090) from the
  Jav3 app. **Anyone with the link** can open it (LAN now; a Cloudflare route later is
  the operator's business). **Scripts are allowed.** Each publish **waits for the
  operator's approval, except in yolo**.
- **Modes:** `ask` / `auto` / `safe-yolo` (exactly what yolo does today: free in the
  VM, the bigger requests wait in the Queue; still the default) / a new `yolo` where
  **nothing ever waits**: packages, services, new network hosts, publishing,
  memory/journal holds, git commits and pushes anywhere, turning a project's network
  on, using a secret: all approved as they are made.
- Kept (not questions, guards): the secret-VALUE leak refusal on writes and on
  publish; the VM and the write chokepoint; /local tools and computer use (they act
  on the operator's own machines, not the VM) keep their own approvals in every mode.

## Mode names

| shown to the operator | stored and sent | meaning |
|---|---|---|
| ask | `ask` | every write/run asks |
| auto | `auto` | a judge reviews writes/runs; risky ones ask |
| safe-yolo | `safe-yolo` (and the legacy `yolo`, forever) | free in the VM; requests wait in the Queue |
| yolo | `yolo-full` (`permissions.FULL`) | nothing waits |

The stored value `yolo` keeps its old meaning for good: installed terminal clients
and stale web tabs send `yolo` as their default for every new chat, and must never
get the new mode by accident. Only a new client sends `yolo-full`, and only after a
deliberate choice (never one stray shift+tab). Default for new chats: `safe-yolo`.

## The shared hook (already in backend/permissions.py)

`await permissions.auto_approve(kind, cid=None, project=None, summary="", detail=None)`
returns True when the request's conversation (default: the running turn's) is in
`yolo-full`, and records a `yolo_auto_approved` security event. The caller then
approves the request on the spot, as `decided_by="yolo"`, through its normal approve
path. `permissions.never_asks(cid)` is the bare check. Until Y1 adds `yolo-full` to
`MODES`, both are always False.

Security event kinds are registered already (backend/security.py): `page_publish_requested`
(warn), `page_published`, `page_rejected`, `page_unpublished` (info), `page_refused`
(warn), `yolo_auto_approved` (info).

## Publish: the shape PB builds and PU consumes

- Config (backend/config.py): `pages_enabled: bool = True`, `pages_port: int = 8090`,
  `pages_bind: str = "0.0.0.0"`, `pages_base_url: str = ""` (derived when empty: the
  LAN address the services already use, else the host, with the port).
- A published page is a SNAPSHOT copied at approval time into
  `settings.data_dir / "pages" / <id>/` (regular files only, no symlinks, caps on
  size and count). The pages server never reads project folders. `<id>` is
  `secrets.token_urlsafe(12)`. Link: `<pages_base_url>/p/<id>/`.
- DB table `pages`: id, project, name, path (in the project), files (JSON
  `[{path, size}]`), bytes, status (`pending` | `live` | `rejected` | `unpublished` |
  `refused`), conversation_id, created_at, decided_at, decided_by.
- Operator API (router on `require_user`, the global same-origin check applies):
  - `GET /api/pages` -> `{"items": [page]}` (live ones, newest first)
  - `GET /api/pages/requests` -> `{"items": [page]}` (pending ones)
  - `POST /api/pages/requests/{id}/approve` -> `{"ok": true, "url": ...}`
  - `POST /api/pages/requests/{id}/reject` -> `{"ok": true}`
  - `POST /api/pages/{id}/unpublish` -> `{"ok": true}`
  - page = `{"id", "project", "name", "path", "files": [{"path", "size"}], "bytes",
    "status", "url" (live only, else null), "created_at", "decided_at",
    "conversation_id"}`
  - A request raises `page_publish_requested` (detail: id, project, name, path,
    bytes, files count); deciding it settles that event (`security.settle`).
- The pages server: its own process (`python -m backend.pages_server`, its own
  systemd user unit beside jarvis.service). GET and HEAD only, `/p/<id>/<path>`,
  `index.html` for a folder (a plain generated listing when there is none), 404 the
  same for an unknown id and an unknown file. Headers on every response:
  `Content-Security-Policy: sandbox allow-scripts allow-popups
  allow-popups-to-escape-sandbox allow-downloads; frame-ancestors 'none'` (no
  allow-same-origin: cookies are not port-scoped, so :8090 is same-site with the
  app on :8000; the sandbox gives each page an opaque origin that cannot use the
  operator's login), `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`, `X-Robots-Tag: noindex, nofollow`. Never sets a cookie; ignores and
  never logs the Cookie header. Content types from a fixed map by extension;
  anything else downloads as `application/octet-stream`.
- The agent tool: action `publish` in the `project` section (tools/publish/):
  `path` (a file or folder in the project), `name`. Waits in the Queue, or in yolo
  goes live at once; the result says which, with the link once live.

## Ownership

| Agent | Owns | Must not touch |
|---|---|---|
| Y1 modes, backend | backend/permissions.py, backend/permissions_api.py, and the request-making code in every approval path (packages, vm/services, environments, skillrequests, egress / lanaccess, gitgate + gitea merge/push, alwaysloaded, journalhold, vm/persist, secrets grants, a project's network switch) for the yolo auto-approval; tests `tests/test_nov4_yolo_*.py`; the existing permission tests it must update | publish code, any UI |
| Y2 modes, UI | the mode chip/picker in the web app (frontend/src/AskUser.jsx and where the composer shows the mode), the terminal client's mode code (PERM_MODES, PERM_HINT, the chip, shift+tab, `/permissions`), docs/SELF.md mode text; tests `tests/test_nov4_modes_ui.py`, existing client permission tests | Review page, request cards, Queue rows, the `"pages"` command |
| PB publish, backend | backend/pages.py, backend/pages_api.py (+ registering the router in main.py), backend/pages_server.py, the `pages_*` config, the DB table/migration, tools/publish/, scripts/jarvis-pages.service + its install step in scripts/install.sh, the doctor stage, the install docs (README, docs/AGENT-INSTALL.md); tests `tests/test_nov4_pages*.py` | permissions, UI |
| PU publish, UI | the web Queue cards and a published-pages list (frontend/src/pages/Review.jsx, frontend/src/boxes/RequestCards.jsx, a new frontend/src/boxes/api/pages.js), the terminal client's Queue rows for publish requests and a `/pages` command; tests `tests/test_nov4_pages_ui.py` | permissions code, the mode chip, backend |

In `clients/jav3cli/jav3`: PU adds its `"pages": Command(...)` entry immediately
before the `"exit": Command(` entry and its Queue rows beside the journal rows; Y2
edits only the `"permissions"` entry and the mode code. Neither reformats the rest.

## Rules every agent keeps

- Tests never touch the network. Python: `~/.cache/jav3-test-venv/bin/python -m pytest
  -q -p no:cacheprovider <files>`; lint: the same python `-m pyflakes <files>` (for
  the client file, copy it to a .py path in /tmp first). Run your own and the
  directly related test files only, never the whole suite.
- Tests that touch the DB use the `tmp_env` fixture (tests/conftest.py) and
  `init_db()`: without it they write into the real state dir.
- Keep tool calls and outputs small (a step silent for 600 s kills you). Match the
  surrounding code: comment density, naming, plain-English comments.
- Commit on your branch; report what changed, the tests you ran with results, and
  what you could not verify.

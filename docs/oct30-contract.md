# 2026-10-30 batch: headless browser, desktop requests, folded live view, tabs + tiles (contract)

Operator decisions, asked with sketches on 2026-10-30. Do not re-ask them.
Four agents build against this file at the same time, each on its own
worktree from the commit that added it. Each owns the files listed for it
and touches nothing else. Run `python -m pyflakes` on the Python you
changed, plus the named tests, before each commit. Commit once per step.

## Why (what the operator saw)

- On the Pi, project `fp-shooter` is on the Default profile: the shared
  box, the `main` image, package requests off. `screenshot` failed three
  times with "needs the `desktop` image variant" and the agent had no way
  to fix it. (a) `environment_request` refuses when the profile's
  `allow_package_requests` is off. (b) It needs a NEW image name and at
  least one package, so there is no "move me onto `desktop`" request.
  (c) Approving one copies the profile with the new `box_image` but keeps
  `separate_box = 0`, and `placement.resolve` ignores `box_image` for the
  shared box. So an approved environment changes nothing for a project on
  the shared box.
- The operator wants a headless browser the agent can drive (open, click,
  type, scroll, run JS) that sends back an image, in EVERY box.
- In the terminal client the operator wants (1) tool steps folded out of
  sight with a live line and ↑/↓ tokens + tok/s, ctrl+o to see them, and
  (2) tabs plus tiles with simpler keys.

## H. Headless browser (agent H)

OWNS: `vm/images/main.recipe`, `vm/build_base.sh`, `vm/docker/Dockerfile`
(only to keep main's package list in step), new `backend/headless.py`, new
`tools/headless_browser/` (TOOL.md + handler.py),
`backend/agent/tools/inguest.py`, `backend/vm/guest_pkg.py` (`_COPY_MODULES`
entry only), `backend/agent/tools/toolsections.py` (ONE new SECTIONS entry
only), `tools/screenshot/` (TOOL.md + handler.py), new
`tests/test_headless_browser.py`, and any test that pins the main recipe or
the in-guest tool list.

1. Main image: add `apt chromium-headless-shell fonts-dejavu-core python3-pil`
   to `main.recipe`, plus whatever `build_base.sh` and the Dockerfile need so
   all three agree (read the "main IS the golden base" comment). Debian 13
   ships `chromium-headless-shell` (150.x on arm64, about 195 MB). Find its
   binary name with `apt-get download` + `dpkg -c` on the Pi
   (`ssh grindlewalt@10.0.0.82`, read only, nothing installed there). The
   desktop recipe keeps full `chromium`.
2. `backend/headless.py`, copied verbatim into the guest the way
   `backend/memguard.py` is. It holds a CDP client over
   `--remote-debugging-pipe` (fd 3 = commands in, fd 4 = events out,
   NUL-terminated JSON, stdlib only, no websocket library, no Playwright).
   There is ONE page session per box. It closes after 5 idle minutes, on
   `close`, and when chromium dies. It prefers the headless-shell binary and
   falls back to `chromium --headless=new`. Reuse the flags in
   `screenshot.chromium_argv` (proxy through the box's egress proxy,
   background Google traffic off, `--no-sandbox` only as root). Run it in the
   work cgroup via `memguard.setup/confine`, as screenshot does.
   **WebGL must work.** The agent's projects are WebGL games. Use SwiftShader
   (`--use-angle=swiftshader --enable-unsafe-swiftshader`, or whatever your
   test shows chromium 150/154 needs) and prove it with a WebGL2 page that
   draws a coloured triangle.
3. Tool `headless_browser` (in-guest, box-only like `screenshot`), with a
   new section `headless` in SECTIONS. Its `about` line: "a headless browser
   inside the sandbox box: open a page, click, type, scroll, run JS, get a
   screenshot back". Its triggers cover headless / render / screenshot of a
   page / canvas / webgl / localhost. The TOOL.md has NO `action:` line; the
   tool has its own `action` argument:
   - `open` (url, width default 1280 max 1600, height default 800 max 1200,
     wait_ms default 1500 max 15000)
   - `click` (element N from the list, or `selector`, or `text`, or `x`,`y`)
   - `type` (text, optional element/selector to focus first; never a
     password: refuse a `type=password` field)
   - `key` (Enter, Tab, Escape, arrows, Space, single characters; `hold_ms`
     for a held key, because games need held keys)
   - `scroll` (dy, or `to` = element)
   - `eval` (JS expression; returns its JSON value, capped at 4 KB)
   - `wait` (ms, max 15000)
   - `read` (the element list only, no image)
   - `shot` (optional `full_page`)
   - `close`

   Every action except `read`/`close` returns one image (JPEG q80 or PNG,
   at most 1280 px wide; use CDP `Page.captureScreenshot` with
   format/quality/clip so nothing needs Pillow), the page URL and title, a
   `changed:` line (did the URL, the DOM size or the pixels change), the new
   console lines (uncaught errors first, `page_console`-style, at most 8),
   and after `open`/`read` up to 60 interactive elements as
   `[N] tag/role "label" (x,y)`. Return it with
   `imageresult.with_image(text, path, caption=...)`. A stale element number
   (from before a navigation) is refused with "read again".
4. Taint, as in screenshot. Opening a non-loopback URL, or ANY request to a
   non-loopback host while the session lives (watch CDP `Network` events),
   means the turn is remote content. Call screenshot's `taint_note` before
   returning anything. If it fails, withhold the result. Note the taint
   again on the first result of each new turn (`turnctx.op_id` changed)
   while the session has seen remote content. Page text is untrusted data:
   label it so in the result, as screenshot does.
5. Failure text like screenshot's: OOM (memguard kill counter), timeout,
   chromium exited. Don't retry unchanged. Tell the model what to change.
6. `screenshot`: url mode also finds the headless-shell binary, so it works
   in `main` boxes. NEEDS_DESKTOP becomes app-mode only, and its text says:
   "app mode needs the `desktop` image. Ask for it with
   environment_request(use="desktop", reason=...) (the operator approves),
   or use headless_browser for web pages." Update its TOOL.md
   (`when_to_use`) to match.
7. Tests (host, no box). The CDP pipe framing and element-list parsing are
   pure, so test them pure. Run one real session against the Mac's Chrome
   (`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`) behind a
   skip-if-missing: open a local `http.server` page, click a button, the
   counter changes, eval returns it, the WebGL triangle is not blank. The
   section test (tests/test_tool_sections.py) must pass.
   Not in scope: the operator's real browser (`browser_*` tools), desk.

## E. Desktop / environment requests (agent E)

OWNS: `backend/environments.py`, `tools/environment_request/`,
`backend/db.py` (one migration only), the environment-request API module
(find it from `environments.approve` callers), the web Review card for
environment requests (`frontend/src/…`, find it), in
`clients/jav3cli/jav3` ONLY the function(s) that draw an environment
request row in the /security Queue, and tests
`tests/test_environment_requests*.py` (or the existing file's name).
Contract base: `docs/agent-requests-contract.md` §1.

1. A "use" request: `environment_request(use="desktop", reason=...)` asks
   to move this project onto an EXISTING image (no build, no packages).
   `name`, `from` and `packages` must not be given with `use`. Store it as a
   row with a new column `kind TEXT NOT NULL DEFAULT 'build'` (`'build'` or
   `'use'`). Use `name` = the existing image and `from_variant` = the same
   image. The image must exist. Refuse a request for the image the project
   already runs, with a message that says so. The pending cap of 3 still
   applies.
2. A `use` request is allowed even when the profile's
   `allow_package_requests` is off: nothing is installed, and the operator
   approves it. A `build` request keeps that gate. The refusal text now says
   what to do: "this project's profile does not allow package requests; ask
   the operator (ask_user) to turn on Packages for it, or ask for an
   existing image with use=".
3. Approve (both kinds) moves the project with the existing own-profile /
   copy rule. If the profile it moves from has `separate_box = 0`, the
   new/updated profile gets `separate_box = 1` (runtime unchanged, kvm by
   default), because an image only applies to a project's own box. Raise
   `box_mem_mb` to the image's `min_mem_mb` when it is lower or unset, if
   the placement code does not already do that (check
   `backend/vm/placement.py`). The approve event summary says the project
   now runs in its own box. Approving a `use` request skips
   `create_variant` and the package checks.
4. The tool's result and the web/TUI cards make the kind obvious. For
   example: "Move fp-shooter onto image `desktop` (its own VM, ≥1280 MB)".
   Show no empty package list.
5. Tests: the shared-box → own-box flip, a `use` request with package
   requests off, a refused `use` for the current image, and approve/reject
   for `use`. Existing environment tests still pass. `npm run build` passes
   (link the main checkout's `frontend/node_modules` if yours has none, and
   remove the link after).

## L. Folded live view + tokens (agent L)

OWNS: `backend/agent/loop.py` (the usage event only),
`backend/vm/guest_turn.py` (pass it through), the chat SSE route that
forwards turn events (find it), and in `clients/jav3cli/jav3`: `TurnState`,
`ToolView`, `ChatLog`, the turn footer (`▣`), the `ctrl+o` action
(`action_toggle_tools`), and their help lines. Also `tests/tui_fake_http.py`
(add usage events to its turns), new `tests/test_cli_fold.py`, and the
snapshot files your change moves (`TUI_SNAPSHOT_UPDATE=1`, then read every
changed snapshot).

1. Server: after each model call the loop yields
   `{"type": "usage", "in": <prompt tokens>, "out": <completion tokens>,
   "cached": <cached prompt tokens or null>, "ms": <call wall time>,
   "model": <name>}`. Find where usage arrives (backend/agent/model.py,
   adapters.py, budget.py). A guest turn passes it through to the host
   stream unchanged, unlike turn_stats. The web SPA must ignore it: check
   `frontend/src` handles unknown event types, and change nothing there if
   it does. An older server sends no usage events, and the client then
   shows no token numbers.
2. Client, folded by default:
   - Assistant messages (the in-between notes and the final answer) show as
     today.
   - Consecutive tool steps between two messages fold into ONE line. While
     running it reads `⠋ <current step title> · step N · 12s · ↑ 18.2k ↓ 640
     · 41 tok/s`. ↑ is this turn's prompt tokens so far, ↓ the output tokens
     so far. tok/s is ↓ over the model time; while text streams, estimate ↓
     live from the streamed text (chars/4) until the next usage event
     corrects it. Once done: `▸ 4 steps · grep, read, edit, run   ctrl+o`
     (the verbs from tool_title's icons/titles, deduplicated, at most 5,
     then "…"). A failed step shows in the summary: `▸ 4 steps (1 failed)`.
   - ctrl+o toggles every fold in the chat open. Open shows today's rows
     exactly; ctrl+o again re-folds. tab still picks a row in open mode.
   - Permission asks, ask_user questions, errors and the turn footer are
     never folded.
   - Footer: `▣ Jav3 · deepseek-flash · 34s · ↑ 52.1k ↓ 1.9k · 38 tok/s`.
     It keeps today's fields when no usage events came.
   - History (a chat opened later) folds the same way. Saved transcripts
     have no usage, so they show no token numbers.
3. Tests: drive the fake (`scripts/tui_drive.py --fake`): a turn shows one
   folded line, ctrl+o opens the 4 rows, the footer shows ↑↓. Also unit
   tests for the summary text. The existing test_cli_turns /
   test_cli_reattach / test_cli_resume still pass. Many assert on tool rows
   being visible: make them open the fold (ctrl+o), don't delete them.

## T. Tabs + tiles (agent T)

OWNS: in `clients/jav3cli/jav3`: `PanelSplit`, `panel_split_dir`,
`Panel`, `PanelArea`, the layout save/restore, the App `BINDINGS` lines
for the new keys, `action_leader` and the leader key map, the leader hint
lines, `/help` key lines for panels and tabs, and the `/themes` and `/web`
leader letters. Also `tests/test_cli_panels.py`, a new
`tests/test_cli_tabs.py`, and the snapshot files your change moves.

Key map, exactly (Terminal.app passes no cmd keys and no shift+arrows):

```
TILES (inside the current tab)
  ctrl+t        new tile: splits the focused tile; the new tile is a new chat
  ctrl+w        close the focused tile (the last tile in a tab closes the tab;
                the last tab is never closed: it becomes one new chat)
  ctrl+x ←↑↓→   move between tiles
  ctrl+x z      zoom / unzoom a tile
TABS
  ctrl+x t      new tab (one new-chat tile)
  ctrl+x w      close tab
  ctrl+x 1-9    jump to tab
  ctrl+x [ ]    previous / next tab
  mouse         click a tab to switch, its × to close, + for a new tab
```

- A tab bar sits on the top row: ` 1 chat ×   2 /vms ×   3 /agents ×   + `.
  The current tab is in the accent colour; it gets ellipses and scrolls
  when too many. A tab is named after its focused tile. Each tab has its
  own tile tree (today's split engine, max 4 tiles per tab), and switching
  tabs keeps each tab's tiles alive (chats keep streaming).
- `/page … panel` and the drawers' `p` open in a new TILE in the current tab
  (as today). `ctrl+x 0` and `ctrl+x 1-4` (old) are replaced by the above.
  Keep old routes only where they don't clash.
- ctrl+w in the prompt no longer deletes a word. Delete-word stays on
  alt+backspace and ctrl+backspace (TextArea's own). Say so in /help.
  ctrl+t is unused today.
- `ctrl+x t` (themes) and `ctrl+x w` (web) leave the leader. /themes and
  /web keep working. Update the leader hint lines.
- Layout save/restore (per project) stores tabs too. An old saved layout
  (one tile tree) loads as one tab.
- Closing a tile or tab never deletes a chat. It only closes the view, and
  the chat stays in → sessions.
- Tests: open 3 tiles with ctrl+t, close one with ctrl+w; ctrl+x t makes
  tab 2, ctrl+x [ / ] / 1 switch, ctrl+x w closes; a streaming chat in tab 1
  keeps receiving while tab 2 shows; mouse click on a tab; layout round
  trip; the prompt's ctrl+w closes the tile rather than deleting a word.
  test_cli_panels / test_cli_drawers / test_cli_pages still pass (update
  their keys where the map changed).

## Orchestrator (not an agent)

The ← agents drawer: RUNNING lists only roots with something running. A
root whose only flag is an old "needs you" goes under a NEEDS YOU section.
An expanded root shows its running / needs-you / failed descendants and
folds the finished ones into "N finished". After that comes the merge, the
full suite, `npm run build`, and the Pi deploy plus main image rebuild.

# UI v2: the operator's list, as work for agents

Branch `ui-v2`, cut from `dev` at `ccaf626`. Everything here lands on `dev` (for
developers and testing) through one PR, never straight on `main`. Written
2026-11-13 from the operator's request; their words are quoted where the intent
matters.

## Ground rules for every agent

- **Start right.** Begin with `git reset --hard <the sha you were given>`;
  worktrees start on the wrong commit. Work only in the files your section
  OWNS. If you need a change in a file someone else owns, write it in your
  report instead of making it.
- **Design.** Read `frontend/DESIGN.md` first. The look is the public website:
  light paper `#faf7ef` and ink, one green accent, warm charcoal in dark mode,
  hairline dividers, Schibsted Grotesk, and Martian Mono for data only. Use
  only tokens from `frontend/src/styles.css`, with no new hex colors. Never
  use purple/pink gradients, emoji as icons, or ALL-CAPS labels.
- **Less text.** "Get rid of all of this setting explanation text, it's
  cluttered." Explanations go behind a `?` (the `HelpTip` component): one
  short line visible at most, and the rest on hover or click.
- **Dropdowns.** Every dropdown is the glass, searchable `Select` from the
  foundation, never a native `<select>`.
- **Right-click.** Right-clicking any sidebar row opens exactly the menu its
  `⋯` button opens.
- **Truthful copy.** Every label and summary must be true to the code. Plain
  sentences, sentence case, American spelling, no middle-dot lists, no
  slogans.
- **Security rules hold.** Nothing here weakens a security rule. `yolo` still
  needs a password login. Approvals still go through the same server
  endpoints, and the UI only changes how they look.
- **Verify like a user.**
  - `cd frontend && npm run build && npm test` must pass.
  - Run the Python tests for any backend file you touch
    (`~/.cache/jav3-test-venv/bin/python -m pytest -q -p no:cacheprovider <files>`).
  - Then look at it. Run the backend on a throwaway state and config dir
    (`CNTRL_STATE_DIR=$(mktemp -d) CNTRL_CONFIG_DIR=$(mktemp -d)`) and
    screenshot your pages in headless Chrome. Do light and dark, at 1280x800
    and 390x844.
  - Stop every server you start: kill it by port, never with `pkill -f`.
- **Process.** Keep tool calls and outputs small, because a 600 s stream
  watchdog kills silent agents. Commit in small steps with plain messages
  that say what changed and why. Don't push.
- **Report:** what you built, what you verified and saw, what you didn't, and
  decisions the operator might want to change.

## Phase 0: the foundation (one agent; everything else waits for it)

OWNS: `frontend/src/components/*`, `frontend/src/styles.css`,
`frontend/DESIGN.md`, plus converting raw `<select>`s anywhere to `Select`.

1. **`Select`, rebuilt.** Keep the props every call site uses today (`label`,
   `hint`, `options` as strings or `{value,label}`, `placeholder`,
   `<option>` children, `value`, `onChange` called with a change-like
   event, `disabled`, `className`), so no call site breaks. It becomes a
   glassy dropdown that is easy to read:
   - It is translucent, with a backdrop blur and strong text contrast.
   - A search field shows when there are more than 6 options, and typing
     filters them.
   - Number keys 1 to 9 pick the 9 most recent choices. Recents are per
     `recentKey` (a new optional prop; it defaults to the label), are
     stored in localStorage inside try/catch, and are shown first with
     their number.
   - Arrow keys, Enter and Escape work, with `role="listbox"` and
     `aria-activedescendant`.
   - On touch or narrow screens it opens as a bottom sheet with the same
     search.
   - Convert the raw `<select>`s that remain, in `pages/Artifacts.jsx` and
     `pages/Schedules.jsx`.
2. **`HelpTip`.** `<HelpTip>text</HelpTip>` is a small `?` that reveals a
   popover on hover or click. It must be keyboard-focusable and close on
   Escape.
3. **`SidePanel`.** `<SidePanel open onClose title tabs?>` slides in from the
   right over or beside the page, and is full screen on a phone. Esc and
   the close button close it, and it keeps focus inside while open.
4. **Row menus.** `useRowMenu(items)` returns props to spread on a row and a
   `<RowMenu/>` element. Right-click opens the menu at the cursor, and the
   `⋯` button opens the same menu at the button. Build it on the existing
   `components/Menu.jsx`.
5. **Divided lists.** A `.list-divided` style puts a small, easy-to-see
   hairline between rows (`var(--line)`, full row width, no heavy borders).
   It is used by the Work and Agents sidebars and by any list in the UI.
6. **Modal check.** Make sure `Modal` handles a form dialog: a title, a
   scrolling body, and a footer with refuse-then-approve buttons. Profiles,
   secrets and instructions editors use it.
7. **Docs and tests.** Document all of the above in DESIGN.md with one usage
   example each. Add node tests for the Select logic (filtering, recents
   order, number keys) in the style of `src/**/__tests__`.

## Phase 1: six agents in parallel, after Phase 0 is merged

### A. Work sidebar, search, project names (Sonnet)
OWNS: `frontend/src/work/*` (the sidebar part), `pages/work.css`, and in the
backend the project list/pin endpoints and a new search endpoint.
- **Dividers:** small, easy-to-see lines between projects, in a clean,
  minimal way (`.list-divided`).
- **Pinned projects.** Pin and unpin from the row menu. Pinned projects show
  in their own section at the top. Store the pins server-side, so they
  follow the operator across browsers.
- **Right-click** on any project or chat row opens its `⋯` menu (`useRowMenu`).
- **Smart search.** Typing gives a quick search over chat and project titles
  first, instantly. Below the results, a "Deep search (takes longer, reads and
  checks all text)" button searches message text on the server, through a new
  endpoint with a time limit and paging. Results show the matching line and
  open the chat at that message.
- **Project names can't start with a number.** Enforce it in the backend's
  project create and rename, and say so in the UI where you type a name. Add
  a test.

### B. Project home page (Opus)
OWNS: a new `frontend/src/pages/ProjectHome.jsx` (+ its CSS in `work.css`
under a `ph-` prefix), the route line in `App.jsx` (ADD one line only), and
the backend endpoints it needs for instructions and context files.
- **Clicking a project opens its home**, laid out like the operator's
  screenshot:
  - The project name, big.
  - A composer, "New chat in <project>"; sending creates a new chat in that
    project and opens it.
  - A model and mode line under the composer.
  - Recents: the project's chats, with hairlines and relative dates
    ("9 hours ago", "Yesterday", "Oct 30").
- **The right column** has four rows, each with an action:
  - **Instructions**, Edit: the project's instructions in a Modal editor,
    saved through the existing project-file write path.
  - **Context**, Add: upload documents into the project's context files by
    drag and drop or file picker. They are listed, removable, and marked as
    loaded into every prompt. Reuse the existing always-loaded context
    mechanism.
  - **Memory**, View: the project's memory notes.
  - **Scheduled**, Add: a schedule scoped to the project.
- **Slash command.** The same page opens from a slash command, `/project
  <name>`, in any composer. The operator said "it can also be used as a /".
  If the slash system suggests a better reading, say so in the report.

### C. Chat top bar and the project side panel (Fable)
OWNS: the chat page's top bar (`pages/Chat.jsx` header only, `shell/ScopeChip.jsx`,
`shell/RunsTab.jsx`), a new `frontend/src/panels/` folder, and `FileViewer`.
- **Plain buttons.** The top bar items (the project chip, temporary chat,
  Mode, Runs, model, +) become plain, clearly labeled buttons, with no
  toggle-with-tooltip and no nested dropdowns. Each opens the right
  `SidePanel` at its tab.
- **The Security tab** shows:
  - permission mode (ask / auto / safe-yolo / yolo, same rules as now);
  - where the chat runs (shared VM or own box, its image and status);
  - network (the profile in effect, on/off, the last few decisions for
    this project, a link to the profile).
  Short labels, with a HelpTip for the rest.
- **The Files tab** gives a quick overview of everything the project has:
  files, artifacts and published pages. Each item has:
  - **Open:** a viewer that renders Markdown, HTML (sandboxed iframe, no
    scripts reaching the app), PDF (the browser's viewer), images, and
    code or text with syntax-tinted plain view.
  - **Edit:** text files open in a basic editor that saves through the
    normal write chokepoint endpoint, so its gates still apply.
  - **Download:** each item on its own, or everything as a zip.
  - **Run:** for things that can run, hook the existing Run panel.
- **Phone width:** the panel works full screen.

### D. Agents page (Sonnet)
OWNS: `pages/Agents.jsx`, `pages/manage.css` (agents part), the Skills tab
files (`SkillsPanel.jsx`), and backend skill import if needed.
- **The agents sidebar follows the Work sidebar's scheme:** dividers, and
  right-click opening the same `⋯` menu.
- **Each agent is another chat.** Clicking an agent opens a chat with that
  agent. Editing the definition moves to the menu: Edit opens today's
  editor (prompt, project, model, memory).
- **Dropdowns** become the new glass `Select`. Phase 0 does most of this;
  check the project and model pickers.
- **Skills by link or file.** Drop a link (a GitHub repo or folder, a raw
  SKILL.md, a zip URL) or upload a file (SKILL.md or zip). Research the agent
  skill formats in use (Anthropic Agent Skills' SKILL.md, and others you
  find, such as AGENTS.md conventions). Support the common ones and say
  which in the report. Imports keep today's rule: an imported skill arrives
  off and the operator grants it.

### E. Security as a dashboard (Opus)
OWNS: `SecurityRuns.jsx`, `securityParts.jsx`, `securityRuns.js`,
`securityCopy.js`, `Posture.jsx`, `pages/security.css`, `pages/Logs.jsx` (for
History), and in the backend the request tools' new `summary` field.
- **Approvals only.** The Queue becomes a quiet dashboard: things that need
  the operator. Noise (info alerts, auto-handled items) leaves the front page.
- **A summary on every request.** Each request carries a summary of at most
  two sentences, written by the agent. Add a required `summary` argument to
  the request tools that file approvals:
  - git commit and push, package, service, environment, skill and publish
    requests;
  - and whatever else files a queue item.

  Enforce the two-sentence cap on the server and keep the tool-description
  budget tests green. For queue items no agent wrote (a host the proxy
  refused), show the best existing short reason.
- **Detail on click:** the summary, then Approve / Deny / Inspect.
  - Inspect opens the chat at the step that asked.
  - For git, it shows the diffs.
  - It works the same for the other kinds.
- **History** moves to Logs.
- **At a glance:**
  - the boxes that are running, their images, and which projects and agents
    are working in them;
  - an Edit link to Profiles.

### F. Network and Profiles (Sonnet)
OWNS: `pages/Network.jsx`, `pages/Profiles.jsx` and their CSS (`nw-`, `pf-`
prefixes in `security.css`), and the route line for the full decisions page
in `App.jsx` (ADD one line only). In the backend: decision paging and search,
and profile network lists.
- **Network, recent decisions:** the last 30. "See all" opens a page with
  Back, search and filters. Clicking a row shows who allowed it, when, for
  which project and profile, and why.
- **Allow and always-block lists per profile, not per project.** "Projects
  share profiles... all project based stuff is irrelevant." Remove the
  per-project lists, and make recent decisions show the profile.
- **Profiles:**
  - Strip the explanation text into HelpTips.
  - Edit opens a Modal, not an inline form, with these fields:
    - **Name.**
    - **Network: On / Off.** When it's Off, the allow and block lists
      disappear. Keep the one-line outcome under it ("a site on neither
      list waits for you").
    - **Box:** clear words for VM versus container, with a HelpTip.
    - **Packages and services:** the agent may request them, default Yes.
    - **Services:** "LAN access" on or off, instead of the proxy-or-LAN
      choice.
    - **Secrets:** keep today's handling. Editing one opens its own Modal,
      and hosts are separate fields (name, URL), not one comma-joined
      string.
  - **Changing the packages and services defaults to Yes is a default
    change.** Update `backend/setup_api.py` SAFE_CHOICES, plus README /
    SELF.md / AGENT-INSTALL.md wherever they state the old default, and the
    tests. The requests still wait for approval, so that doesn't change.

### G. Research: connectors over MCP (Sonnet, starts now, report only)
- **Goal.** Everything that has MCP should be reachable through MCP: email,
  calendar, anything Google, Notion, and Microsoft Teams if it exists. The
  operator wants "the easiest, click through menu set up possible", clearly
  marked BETA.
- **Find out:**
  1. Does CNTRL already speak MCP anywhere? Grep the code.
  2. Which MCP servers exist for Gmail, Google Calendar, Google Drive,
     Notion, and Microsoft Teams/Outlook (official or reliable), and how
     each one authenticates (OAuth, token, hosted versus local)?
  3. How agent apps with click-through connector setup do it. Look at the
     ones the operator named ("the dot agents and muse"; identify what
     these are) and at similar ones. Take the flow, not their branding.
  4. How connectors fit CNTRL's security model: tools run on the host
     behind the broker, keys never enter the sandbox, a connector's output
     taints the turn, and sending anything outward waits for approval.
- **Deliver:** a design for Settings > Connectors (BETA) and a build plan.
  No code.

## Phase 2: after Phase 1 is merged
- Build Connectors (BETA) from G's design, once the operator has seen it.
- One integration pass: run every page in light and dark, at desktop and
  phone width; check every dropdown is the new Select and every row has a
  right-click menu; run the full test suite; open the PR `ui-v2` → `dev`.

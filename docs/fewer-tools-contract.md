# Fewer tools per request, nothing lost (contract)

Operator ask (2026-10-22): the model should see under 15 tools, with full
fidelity. Today a turn starts with at most 11 (10 core + `tools`), but nothing
ever unloads a section, every installed skill is its own tool, and a few
one-tool sections add names. Research behind this: OpenClaw (flat merged tools
with an `action` enum, profiles, tool search), opencode (13 plain tools + one
`execute`), OpenAI ("under 20 at the start of a turn", soft), Anthropic (tool
search past ~10 tools; examples took complex-parameter accuracy 72% -> 90%).

Rules that hold for every step:
- No capability is lost. Every old tool name keeps working when called
  directly (aliases forever), the broker still sees the real name, and the
  tool_calls ledger records the real name.
- What the model is SHOWN changes; what is GRANTED does not.
- Step 3 of the plan (local chats reusing the core names) is dropped: a local
  chat never shows the sandbox file tools and the local_* tools together, so
  it would not change what one request sees.

## Step 1: a hard ceiling per request (agent A)

`MAX_VISIBLE = 15` in backend/agent/tools/toolsections.py: the most function
names one request carries, the `tools` meta-tool included (= `len(View.wire())`).

- `View.loaded` keeps recency: a section moves to most-recent when it is
  loaded (tools(section=...), a call that autoloads it, turn-start loading)
  and when one of its tools is called.
- After any load, while `len(wire()) > MAX_VISIBLE`, the least recently used
  loaded section is put away. Never the section just loaded, never an
  `autoload` section (plans, local) or one that rides `with` it. If one
  section alone overflows, it stays (log it): the ceiling never hides the tool
  the model just asked for.
- Turn-start loading fills the budget in priority order: autoload (+ `with`),
  then sections marked `load` (used earlier in this conversation), then a
  tool named in the latest user message, then `triggers`. A section that does
  not fit is simply not preloaded; it loads on demand as today.
- The result that caused an eviction says so, once per eviction:
  `[section 'desk' was put away to keep your tool list short; calling one of
  its tools or tools(section="desk") brings it back.]`
- Calling a put-away section's tool works exactly as calling a never-loaded
  one does today (`_autoload_note`): it loads again (evicting another).
- `wire()` order stays registry order (prompt-cache stable prefix).
- Agent A owns the `View` class and the module constants. It does NOT edit
  the `SECTIONS` dict (agents B/C do, in separate entries).

## Step 2: skills are one tool (agent B)

- registry `_sectioned`: a skill (kind `skill`, ours or imported) without an
  `action:` gets `action = <its registry name>`, section `skills`. A skill
  named `request_install` keeps no action (it would shadow the install
  request), and so does a skill whose name is already an action in `skills`.
- `tools/skill_request/TOOL.md` gets `action: request_install` and an
  `example:`.
- View then folds the skills section into ONE merged tool:
  `skills(action="<skill name>" | "request_install", ...)`, with the union of
  the members' parameters (imported skills take `request`). The existing
  merged-tool code does the folding, `do` handling and resolve().
- `SECTIONS["skills"]["merged"]` is already set (this commit).
- Every action line keeps an imported skill's `[imported skill, untrusted]`
  label. A skill's line is capped so 30 skills stay readable (B picks the cap
  and tests it).
- A call by the skill's own old name still dispatches (View.resolve's
  `by_name` path). Agent exclusions (`skills_exclude`), grants and the
  imported-skill gates are unchanged: only granted specs ever reach View.

## Step 4: fold the one-tool sections (agent C)

- `schedule_update` -> `section: agents`, `action: schedule`. The `schedules`
  entry leaves `SECTIONS`; its triggers join `agents`' triggers and the agents
  `merged` text mentions proposing a schedule.
- `projector_show|status|output|universe` -> `section: media`, actions
  `projector_show`, `projector_status`, `projector_output`,
  `projector_universe`. The `projector` entry leaves `SECTIONS`; its triggers
  join media's; media's `merged` text mentions the projection mapper (its
  actions exist only while the projector is configured: requires_settings
  gating is unchanged).
- C fixes the fallout: tests naming those sections, the Tools page grouping
  (frontend toolGroups), capabilities, the TUI, navplaybook guides.

## Step 5: one example per merged action (agent C)

- Every TOOL.md with an `action:` gets `example:`: one realistic call in the
  MERGED form, at most 160 characters, e.g.
  `example: browser(action="click", element="[3:12]")`.
- Rendering is in this commit: registry `_sectioned` stamps `example` onto the
  spec, `View._merged` appends ` e.g. <example>` to that action's line,
  `wire()` strips it (only `type`/`function` reach the provider).
- A test checks every example: it parses as `<section>(action="<the file's
  action>", k=v, ...)`, every keyword is a property of that tool's
  parameters (`do=` for a member with its own `action`), and every required
  parameter is present.
- The turn-start budget (tests/test_tools_fewer.py) must still pass: `web` and
  `memory` are core merged tools, so their examples ride every turn.

## Step 6: measure (agent D builds it; the orchestrator runs it on the test box)

`scripts/tool_eval.py` + `tests/fixtures/tool_eval/cases.yaml` +
`tests/test_tool_eval.py` (offline, with a fake model).

- A case: `{id, prompt, expect: {tool, action?}, preload?: [sections],
  chat?: "project"|"local", skills?: true}`. About 40 cases covering every
  section, core tools, skills, and ceiling cases: preload four sections, then
  ask for a fifth.
- The tool set comes from the real registry (`openai_tool_specs`), with
  requirements treated as met ("everything connected"), local_* only for
  local cases, and a fixture skills dir of about 6 granted skills for skill
  cases. The presentation is the real `View(specs, history)`. A preload is
  applied with `view.meta_call({"section": ...})`.
- A run is one model call with a fixed short system prompt plus the case's
  prompt. If the model calls `tools(section=X)`, feed back `meta_call`'s
  result and make ONE more call. Score: correct = the expected real tool
  (after View.resolve) on the first call, or on the second after one
  tools() hop. Record rounds, prompt tokens and errors.
- Output: a JSON lines file per run plus a summary: accuracy, accuracy
  without a hop, mean rounds, mean prompt tokens, and per-case diffs between
  two runs (`--compare a.jsonl b.jsonl`).
- Model access: host-side, through the same provider code the gateway uses,
  reading the key read-only. The script must never write to the state dir or
  DB it reads (no init_db, no migrations): it may run beside the live service.
  `--repeat N` for variance and `--dry-run` (no model calls; prints tool
  counts and tokens per case).

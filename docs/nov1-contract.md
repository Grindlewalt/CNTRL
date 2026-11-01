# nov1 contract: thinking, freeze/error detection, sidebar

Branch `nov1` off `oct30` (3b47478). Three agents build against this at once:
**K** (server: thinking events + pulse), **L** (terminal live line: thinking rows,
freeze and error detection), **S** (terminal sidebar + security glance endpoint).

Operator decisions (2026-11-01, AskUserQuestion with sketches; don't re-ask):
- The model's thinking streams live. Under ctrl+o it shows in full as a row in the
  steps (`▾ thought for 14s` + the text). The live line shows a tail of it so you
  can see it moving: `⠋ thinking 14s · 320 words · …should dent, not detonate`.
- Never leave the operator guessing whether it is frozen. Redundant checks; say
  whose fault a failure is (your network / the server / the model provider).
- Sidebar: full sections (sketch at the bottom), every number live and the same
  as the status line, × and ctrl+b hide it, ctrl+x v opens or jumps to a Security
  TAB so the chat stays in view.

## 1. The `think` event (K produces, L renders)

```
{"type": "think", "text": str}     # a delta of the model's reasoning
```

- Sources: OpenAI-compatible `delta.reasoning_content` (DeepSeek, Kimi, vLLM/Qwen)
  and `delta.reasoning` (OpenRouter, ollama); Anthropic `thinking_delta`;
  Gemini thought parts if the adapter already sees them (optional).
- It flows wherever `token` flows, in order with tokens and tool calls:
  ModelClient._stream_once / .complete, adapters (retrying, anthropic, google),
  ModelGateway.complete, loop.py (both `("token", "retry")` sites), the host
  gateway (already relays every event), the guest `VsockModelClient`
  (guest/backend/agent/model.py), the guest server -> guest_turn -> chat.py bus
  -> SSE (already relay every event).
- IMPORTANT: today several layers treat any event that is not token/retry as the
  final message. Every such site must pass `think` through like `token`.
- Never part of `message.content`, never persisted, never sent back to the model,
  not recorded by narration. The request payload sent to the provider does NOT
  change (no new "enable thinking" flag).
- `retry` voids the thinking text streamed so far in that round, like tokens.
- ModelGateway's `streamed` estimate counts think text too (it is billed output).
- Old clients and the web app ignore unknown event types (checked: turnEvents.js
  is an if/else chain; the TUI's handle_event falls through).

## 2. `GET /api/chat/{cid}/pulse` (K produces, L polls)

On the chat router (require_actor, as the rest). In memory only, no DB, cheap.

```
200 {"running": bool,            # a turn task for this chat is alive
     "phase": "starting" | "model" | "tool" | null,
     "tool": str | null,         # the tool running when phase == "tool"
     "phase_s": float | null,    # seconds in this phase
     "quiet_s": float | null}    # seconds since the turn last yielded any event
```

- Unknown or finished chat: `{"running": false, ...nulls}` (never 404).
- Phase tracking lives in chat.py's event loop over `source`: `start` ->
  "starting", token/think/usage/tool_result -> "model", tool -> "tool" (+name).
  Cleared when the turn ends (any path: final, error, cancel).
- `running` must be true only while the turn task is alive (use whatever
  `_running_loops()` / `_active_turns` already use).

## 3. `GET /api/security/glance?project=<slug>` (S produces and reads)

New file `backend/security_glance.py` (router prefix `/api/security`, the same
dependency as the security router in egress_api.py), included in backend/main.py.
Cheap COUNT queries only.

```
200 {"waiting": {"egress": n, "packages": n, "environments": n, "skills": n,
                 "git": n, "alerts": n},
     "total": n,                              # sum of waiting
     "net24h": {"allowed": n, "denied": n}}   # same meaning as /api/egress/summary
```

`project` narrows what can be narrowed; omitted = everything.

## 4. Client: `Link` (stub committed; L implements, S reads)

`class Link` sits above `TurnState` in clients/jav3cli/jav3; the app has
`self.link = Link()`. Fields: `state` (idle|live|quiet|reconnecting|down), `why`
(one line in words), `where` (client|server|provider), `last_byte`, `tries`;
`beat()`, `beat_age()`. L may add methods and constants; must not rename these.

## 5. Client ownership (one file: clients/jav3cli/jav3)

L owns: TurnState, `handle_event` (the `think` branch, error handling), FoldLine,
`fold_live_text`, `fold_summary`, a new ThinkView widget, `_consume`, `_post`,
`_lost_connection`, `_reattach*`, `net_error_text`, Link's methods, the
status-row connection text (`_status_parts*`), stream timeouts.
S owns: `_sidebar`, the `#sidebar` compose block and its CSS, `action_toggle_sidebar`,
the ctrl+x v / `security` leader behaviour, new sidebar pollers and widgets.

Shared anchors (each adds ONE line, at its own anchor, nothing else in these):
- `_tick`: L adds `self._watch_link()` right after the `live_folds` spin loop;
  S adds `self._sidebar_tick()` right after `self._refresh_new_hint()`.
- tests/tui_fake_http.py: L edits the stream/event generation for its scenarios;
  S appends its fake routes in one block at the END of the file.
- New state: L adds fields to TurnState and Link; S adds fields at the END of
  `ChatState.__init__` and at the end of the app `__init__`.

Nobody regenerates TUI snapshots (tests/test_tui_snapshots.py); the orchestrator
does that once after the merge. Nobody touches the other agent's regions.

## 6. Sidebar sketch (approved)

```
Chat #709                    × ctrl+b
⌂ polycrash-v2 · deepseek-flash

This turn  6m 07s · 22 tools · $0.04
  ↑ 914.8k  ↓ 56.8k  189 tok/s
Chat       $0.06 · 1.2M tok · 4% ctx

Agents  1 running · 1 done
  ◇ physics-tuner   running 1m
  ◇ track-builder   done

Git  3 changed, not committed
  M index.html
  A GAME_DESIGN.md
  ? smoke.js

Todo  1/7
  ✓ Research PolyTrack mechanics
  ☐ Write GAME_DESIGN.md spec

Box  polycrash-v2 · main · up 6m

Security  ! 1 host waiting
  ctrl+x v  watch in a new tab

Notifications
  12:22 ✓ created project polycrash-v2

● 10.0.0.82:8000 · live (beat 2s ago)
  grindlewalt · full access
```

## 7. Live line sketch (approved)

```
live line while it thinks:
 ⠋ thinking 14s · 320 words · …should dent, not detonate, below 60 m/s

ctrl+o open:
 ▾ thought for 14s
   The crash should only trigger above a speed
   threshold, so first check how velocity is …
 ✓ read  game.js
 ▾ thought for 3s

when nothing arrives:
 ⠋ waiting for the model · 35s          (yellow)
 ! nothing for 2m · server up (beat 3s ago) · esc stops
 ✗ lost connection: your network is down · retrying 3
 ✗ 10.0.0.82 refuses connections: server down · retrying
 ✗ server error: <its message>
```

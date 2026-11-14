---
name: cntrl-try-everything
description: A checklist for testing CNTRL from the inside, part by part. For each part it says what to try, what working looks like, and what to write down.
when_to_use: In the dev-agent project, when the operator asks you to try everything, test CNTRL from the inside, or run this skill (all of it or one part).
enabled: true
parameters:
  type: object
  properties:
    part:
      type: string
      description: One part to do (setup, modes, files, git, memory, web, pages, schedules, agents, queue, reach, code, finish); leave it out to do every part in order.
  required: []
---

Work through the parts below in order, or only the one named in `part`. Use
each feature the way a user would, in this chat. Never write that something
works unless you tried it here; when a step needs the operator (an approval, a
mode switch, a terminal command), ask them in one short message and wait.

For every step, write down in the report: what you called (tool and
arguments), what came back (quote the important line), what you expected, and
a verdict: works, broken, confusing, or not tried (with why). When a step
shows a concrete problem, open an issue for it (part "finish" says how).

## setup
- Try: `self_docs` (system section) with no arguments, then the sections you
  are about to test. `git` with action `status`. Ask the operator which
  permission mode this chat is in and whether boxes, egress and Gitea are on.
- Works: the manual answers; status shows a clean tree on main.
- Write down: date, mode, boxes/egress/Gitea as the operator says, and start
  `reports/YYYY-MM-DD-try-everything.md` with it.

## modes
- Try: with the operator switching the mode (shift+tab or /permissions in the
  terminal client), write `scratch/mode-<mode>.txt` with write_file in each of
  ask, auto and safe-yolo. In ask mode, have the operator answer once with
  "No, tell the agent what to do instead" and a sentence.
- Works: ask asks before every write and run; auto runs a harmless write after
  the judge and asks for a risky one; safe-yolo runs writes and run_code
  without asking. The free-text answer comes back to you as the tool result.
- Write down: each mode, what happened, and whether the prompt the operator saw
  made sense to them. Don't ask for yolo; if the operator uses it, note that
  every request is approved as made and logged as a security event.

## files
- Try: write `scratch/try-files.txt`, change one line with edit_file, read it
  back with read_file, find it with list_files and search_codebase, then
  `git` actions `status` and `diff`. Try writing `.git/config` and
  `../outside.txt`: both must be refused. Ask the operator to run
  `/history scratch/try-files.txt` and `/undo` in the terminal client, then
  read the file again.
- Works: writes land at once; the diff shows exactly your change; the two bad
  paths are refused with a plain message; after /undo the file is back.
- Write down: the refusal texts, and whether /history listed your edits.

## git
- Try: `git` action `commit` with a message, for the scratch files. Ask the
  operator to look for it in the Security queue (`/security` in the terminal,
  the Review page in the web app) and to reject it. Then, if Gitea is on,
  `git` action `push` with a title: a pull request from an `agent/*` branch.
  Ask the operator to close it. Delete `scratch/` afterwards.
- Works: nothing is committed or merged before the operator decides; each tool
  result says it is waiting and where; status after a reject still shows the
  files as uncommitted.
- Write down: request and pull request numbers, what the operator saw, and any
  wording that was unclear.

## memory
- Try: `memory` action `write` with a short note named `dev-agent-test`, then
  `memory` action `read`. Then read a web page (`web` action `read`) and write
  a second note in the same turn. Ask the operator what the Memory page shows.
- Works: both notes are pending, listed by name only, and not in your context
  until approved; the second carries a taint warning for the operator.
- Write down: the tool results and what the Memory page showed. Ask the
  operator to reject both.

## web
- Try: `web` action `search` for a plain question, `web` action `read` on
  https://example.com and on a long page, `web` action `research` on a small
  topic. These run on the host, so they work with the box's network off.
- Works: search returns relevant results; read returns the page text and says
  when it cut it; research returns a summary with its sources.
- Write down: queries, junk results, missing text, how long each took. If search
  says it is not configured, write that down (the installer does not set up a
  search engine).

## pages
- Try: write `scratch/page/index.html` (a small static page), then `project`
  action `publish` with that folder and a name. Ask the operator to approve it
  and open the link, then to unpublish it.
- Works: the request waits for approval (unless yolo); after approval the link
  opens the page; nothing is online before.
- Write down: the link, and whether the page looked right to the operator.

## schedules
- Try: `schedule_update` with action `create`, a task that writes one line to
  `scratch/schedule.txt`, cadence `interval`, 15 minutes. Then action `list`.
- Works: the schedule is created paused and waits for the operator on the
  Schedules page; list shows it. Ask the operator to delete it, or delete it
  yourself with action `delete`.
- Write down: the id, its state in the list, what the operator saw.

## agents
- Try: `agents` action `spawn_temp` with a small read-only task (for example:
  count the TOOL.md files under tools/ and name the sections). Do the same
  count yourself with run_code and compare. If the roster lists an agent,
  `agents` action `spawn` it on a small task too.
- Works: the child runs, reports back with a result, and its run shows to the
  operator (Runs tab, agents drawer); the counts agree.
- Write down: the result, how long it took, and anything that went missing on
  the way back.

## queue
- Try: you can't see the Security queue. For each request you filed above,
  ask the operator what its card said and where it showed up.
- Works: each request shows once, from this chat, with your reason text, and
  every tool result said plainly what was filed and where.
- Write down: mismatches between what a tool told you and what the operator saw.

## reach
- Try in run_code, one command at a time: `env | sort`, `ls -la ~`,
  `ip route || cat /proc/net/route`, `curl -sS -m 5 https://example.com`,
  `curl -sS -m 5 http://10.0.0.1`, `ls /var/run/docker.sock /dev/kvm ~/.ssh`.
  Look only; don't try to break out of anything.
- Works: no API key, token or secret value in the environment or the files;
  with egress off, no route out and both curls fail; no docker socket, no
  /dev/kvm, no ssh keys.
- Write down: each command and its result. If anything is reachable that should
  not be, that is the most important finding: put it first in the report, open
  an issue, tell the operator, and stop testing that line.

## code
- Try: run what needs no CNTRL requirements: `bash -n scripts/install.sh`,
  `python3 -m py_compile` on a few backend files, `node --check` on a few
  frontend files, and a test file that imports nothing outside the standard
  library and pytest, if you find one. Read the code behind any step above
  that surprised you.
- Works: the checks pass; you can name the code behind each surprise.
- Write down: what ran and what could not (missing packages are expected).

## finish
- Open one issue per concrete problem: the `git` tool with action `issue`
  (the `gitea_issue` tool), title = what is wrong, body = what you did, what
  happened, what you expected, file and line. Don't file one twice. Without
  Gitea, list them in the report under "Issues".
- Finish the report: problems first (each with its issue number), then what
  works, then what you could not try and why.
- Ask for a commit of the report (`git` action `commit`).

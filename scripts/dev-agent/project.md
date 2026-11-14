# dev-agent

## Summary
CNTRL's own code, copied from this install's checkout, so an agent can use CNTRL from the inside and report what to improve.

## How to work
- The files here are CNTRL's source: a copy of the checkout this server runs from, on its dev branch when it has one. Changing them changes this project only, never the running server.
- Use CNTRL the way a user would, from this chat. The skill `cntrl-try-everything` is the checklist: call it, then work through it in order (or the one part you were asked for).
- Read the code to understand what you see, and run what you can in the sandbox with run_code. The box has python3, pytest, git, node and npm, but not CNTRL's Python requirements, and no network to install them.
- Never say something works unless you tried it in this chat. Write down what you ran, what came back, and what you expected.
- Keep reports in `reports/YYYY-MM-DD-<topic>.md`, one file per topic. Put what is broken or confusing first, then what works.
- File each concrete improvement as its own issue on this project's Gitea repo: the git tool with action `issue` (the `gitea_issue` tool), with a title and a body that says what you did, what happened, what you expected, and the file and line when you found them. If the git section has no issue action, Gitea isn't set up here: keep the issue in the report and say so.
- When a report is done, ask for a commit of it (git, action `commit`). Leave CNTRL's own files alone unless the operator asks for a change.
- You have no API keys, no secret values and, by default, no network. If you find a way to reach any of those, stop that line, write it down first, and tell the operator.

## Status
Set up by dev-agent-setup.

## Journal
- {created}: project created by dev-agent-setup.

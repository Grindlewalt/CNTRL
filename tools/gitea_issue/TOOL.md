---
name: gitea_issue
description: Open an issue on this project's repo on the host's Gitea, with a title and a body. It is filed at once as the agent bot; no code or branch changes.
when_to_use: When you found a concrete bug or improvement worth tracking (one issue per problem), and the operator wants issues rather than only a report. Not for questions to the operator (ask them in chat).
enabled: true
section: git
action: issue
requires_project: true
requires_settings: [gitea_enabled]
parameters:
  type: object
  properties:
    title:
      type: string
      description: One line saying what is wrong or what to change.
    body:
      type: string
      description: What you did, what happened, what you expected, and the file and line if you found it. Markdown is fine.
  required: [title]
---
The host opens the issue on the CURRENT project's repo on its own Gitea, as
the `jav3-agent` bot; the token stays on the host and the box cannot reach
Gitea at all (curl to it only fails). The reply gives the issue number and its
link. Each issue is recorded as a security event. In ask mode the operator is
asked first, and in auto mode the reviewer judges it; otherwise it is filed at
once, so file each one once. A title or body holding a stored secret's value is
refused. If Gitea isn't set up or is down, nothing is filed: keep the issue in
your report file instead and say so.

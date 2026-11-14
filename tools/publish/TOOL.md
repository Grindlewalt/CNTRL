---
name: publish
description: Publish a file or folder of the project (HTML, PDF, images, text, video) as a static page at its own link. Files are copied as they are now; the operator approves first.
when_to_use: When the operator asks to share, publish or put something online as a page they can open or send to others. Not for a live app or server.
enabled: true
section: project
action: publish
requires_project: true
parameters:
  type: object
  properties:
    path:
      type: string
      description: A file or folder in the project (e.g. "report/" or "brief.pdf"). A folder needs an index.html to open at its link.
    name:
      type: string
      description: A short name the operator sees in the approval.
    summary:
      type: string
      description: What this request does and why, in at most two sentences. The operator reads it first.
  required: [path, name, summary]
---
Static files only; scripts in a page run sandboxed with no login. A snapshot
is copied at approval, so later edits are not live until published again.
The link works for anyone who has it. A file holding a secret's value is refused.

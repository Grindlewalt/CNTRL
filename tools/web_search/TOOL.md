---
name: web_search
description: Search the web (via a private SearXNG instance) and get back a text list of results — titles, URLs, and snippets. Results already fetched this session are flagged.
when_to_use: To find sources on a topic before reading them, or to answer something you don't know.
enabled: true
section: web
core: true
action: search
read_only: true
parameters:
  type: object
  properties:
    query:
      type: string
  required: [query]
---
This is inert text only — no pages are opened. Use web_read on a specific URL
to get its contents. Prefer sources not already flagged as fetched, so the
knowledge gathered stays diverse.

If the result says search is degraded or that results do not match the
query, the engines are failing: do not retry the same search over and over,
tell the operator search needs fixing, and read URLs you already know with
web_read instead. Never cite results the note says it left out.

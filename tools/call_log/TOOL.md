---
name: call_log
description: Your own recent tool calls in this conversation, one line each (time, tool, arguments, ok with result size or the error first line). Never the result text.
when_to_use: When asked what you called, what failed, or how a turn went, and old tool results are no longer in your context. Read it before summarising failures instead of rebuilding them from files on disk.
enabled: true
section: system
action: call_log
read_only: true
parameters:
  type: object
  properties:
    limit:
      type: integer
      description: How many calls, newest last (default 30, max 200).
    turns:
      type: integer
      description: Only calls since the Nth-last operator message (1 = this turn). Omit for the last `limit` calls whatever the turn.
  required: []
---
Only this conversation's calls (a sub-agent sees its own). A line is
`time tool args -> ok, N chars` or `-> error: first line`. The log keeps at most
10,000 chars of a result, so `10000+` means at least that. Result bodies are
never returned, so web content is not fed back in.

A call can succeed and still return useless content (a search whose results are
unrelated, a page that is only its menu). The log shows sizes and errors, not
quality: a small ok result is a hint to look again, nothing more.

# Connectors over MCP: research for Settings > Connectors (BETA)

Researched 2026-11-13 for UI v2 section G. [V] means the source page was read.
[S] means a search snippet only: verify it before building on it.

## What CNTRL has today
- **One minimal MCP client,** `backend/mcp.py`, for the projector server:
  - streamable HTTP, one POST per call, a bearer token from env settings
    (`mcp_projector_url` and `mcp_projector_token`);
  - no SSE, no sessions, no OAuth, no stdio, no multiple servers, no UI and
    no per-project grants.
- **Pinned tools.** The tool list is a hard-coded manifest
  (`PROJECTOR_MANIFEST`); the server's `tools/list` is only checked against
  it, and anything extra raises a security event.
- **Taint.** Results taint the turn through `vm.broker._UNTRUSTED_PREFIXES`,
  the same ledger `web_read` uses.
- **No server-initiated requests.** It never accepts sampling, roots or
  elicitation.
- **CNTRL is not an MCP server itself.**

## Servers per service

| Service | Server | Auth | Setup friction |
|---|---|---|---|
| Notion | Vendor-hosted `https://mcp.notion.com/mcp` (streamable HTTP, SSE fallback) [S] | OAuth 2.0 + PKCE, discovery via `/.well-known/oauth-protected-resource`, dynamic client registration reported [S] | Click only |
| Gmail, Calendar, Drive, Docs, Sheets, Chat | Google-hosted, Developer Preview: `https://<product>mcp.googleapis.com/mcp/v1` [V] | User-delegated OAuth; the user creates their own Web OAuth client, adds a redirect URI and ticks "Use this client for an AI-powered agent" [V] | High: a Google Cloud project and an OAuth client |
| Outlook, Calendar, Teams (Microsoft 365) | Microsoft "Work IQ" / Agent 365 hosted servers in preview, which may need a Copilot license [S], or community Graph servers | Entra app registration, client secret, Graph permissions; work tenants often need admin consent [S] | Highest |

Google's notes [V]:
- Calendar lists only read and free/busy scopes, so it is read-only for now.
- Gmail has `gmail.readonly`, plus `gmail.compose` for drafts and sending.
- Refresh tokens need `access_type=offline`.
- Apps left in Testing status commonly expire refresh tokens after 7 days
  (not verified on that page).
- Google warns about indirect prompt injection from emails and documents.

## The apps the operator named
- **DotAgents** [V]: a voice-first agent app built on the ".agents Protocol"
  (layered `.agents` config files), with an MCP Tools page. Its exact
  connector flow has not been read yet.
- **Muse** [S]: probably Meta's Muse personal agent. It connects apps from
  Settings, then Connectors, then Connect, each app authorized separately.
  Whether it takes custom MCP URLs is disputed. **Ask the operator which Muse
  they mean.**
- **Claude connectors** [S]: pick from a directory, or "Add custom connector"
  and paste a URL. Advanced client ID and secret only when needed. The app
  runs the OAuth hop itself.

The flow to copy (the flow, not anyone's branding):
1. A catalogue card with a Connect button.
2. A browser sign-in and consent page.
3. Back in the app: "Connected as x@y".
4. A tool list with toggles.
5. "Add by URL" as a fallback.
6. Advanced client ID and secret only when a server needs them.

## How it fits CNTRL's security model
- **The MCP client runs in the host broker, never the sandbox.** The guest
  asks over vsock like every non-code tool. Tokens and connector URLs never
  reach it.
- **Tokens live in the host secrets store.** That covers access and refresh
  tokens, and the operator's OAuth client secrets. Refresh happens on the
  host, and projects get per-project grants like today's secrets.
- **Every result is tainted.** Results are wrapped as data, capped, and
  logged on the untrusted ledger with an `mcp:<connector>` prefix. Email and
  document text is the worst case for prompt injection. Consider this rule:
  once a turn has read connector content, its outward connector writes always
  ask, even in yolo.
- **Outward actions wait for approval by default:** send mail, create or edit
  events, post to Teams, share, delete. A CNTRL-owned table classifies each
  tool as read or write, and an unknown tool counts as a write that asks. The
  approval shows the broker's own arguments: recipient, subject, body,
  attendees.
- **Pin tools as data, not code.**
  - On connect, show the server's tools.
  - Store the approved names, plus a hash of each description and schema.
  - Send the model our own short description, never the server's, which
    defeats tool poisoning.
  - A changed or new tool is disabled and raises a security event, which
    defeats a rug pull.
  - Namespace tools `connector__tool`.
- **Narrow scopes.** Gmail read-only by default, with "allow sending" as its
  own switch. Say plainly that Google and Microsoft grants cover the whole
  account, and suggest a dedicated account. Disconnect revokes the token.
- **Transport.** Add `Mcp-Session-Id`, JSON or SSE bodies on a POST, and size
  and time limits. Still no standing server channel. No stdio in the first
  version; if stdio comes later, it runs in its own hardened box.
- **Egress.** Connector calls leave from the host, outside the guest's egress
  policy, so they need a per-connector host allowlist, logged.
- **A new row in the risk register** before the first third-party server.

## Design: Settings > Connectors (BETA)
1. **Catalogue.** A BETA banner reads: "Connectors are new. Anything they
   read is treated as untrusted; anything they send waits for you." Cards:
   Notion, Gmail, Google Calendar, Google Drive/Docs/Sheets, Microsoft
   Outlook and Calendar, Teams, and "Add by URL". Each card has a "one click"
   or "needs setup" badge.
2. **Connect.**
   - Hosted OAuth with dynamic registration (Notion): a popup, consent, and
     back.
   - The operator's own OAuth client (Google, Microsoft): a 3-step wizard
     with the exact redirect URI to copy, the exact setup link, and fields
     for the client ID and secret.
3. **Connector page:**
   - the account, and the scopes granted;
   - tools labeled read or write, each with a toggle;
   - the approval mode for each write tool;
   - project grants;
   - Test, Reconnect and Disconnect;
   - a "tools changed" notice.
4. **Approvals.** Outward actions go to the existing Security queue.

Backend:
- **Multi-server client.** Generalise `backend/mcp.py` into a multi-server
  client with a `connectors` table (kind, url, account, scopes, status, pins,
  grants).
- **OAuth.**
  - Discovery (RFC 9728 and RFC 8414), dynamic registration, and PKCE.
  - A state-checked callback at `/api/connectors/oauth/callback`. Behind
    Cloudflare Access it needs a Bypass rule, as pairing did.
  - Refresh under a lock, with a "needs reconnect" state.
- **Per-project grants** checked in the broker, and security events for
  connect, disconnect, tool changes and every write.

Build order:
1. **Notion,** which proves the whole path with no app creation.
2. **Google:** Gmail read, then Calendar and Drive, plus the own-client
   wizard.
3. **Microsoft 365 / Teams,** last: preview, licensing and admin consent are
   all uncertain.
4. **Add by URL,** once pinning is proven.

## Honest limits
- **Redirect URIs.** Google and Microsoft web clients need an HTTPS redirect
  on a domain you control. A LAN IP over plain http won't work. Two ways
  round it:
  - the server's public hostname, such as the Cloudflare tunnel, with a
    Bypass rule for the callback;
  - a loopback flow: the sign-in runs at `localhost` on the operator's
    machine, then the code is pasted back to the server.
- **Google may need verification.** Without dynamic registration, Google
  means the operator's own client, and going to production may need
  verification for Gmail scopes.
- **Previews change.** All hosted servers are preview or beta and will change,
  hence the BETA badge and disabling a tool when it changes.
- **Approval is the real control.** Nothing stops an approved send that an
  injected email talked the agent into, so the approval must show the full
  content.

## Sources
- https://developers.google.com/workspace/guides/configure-mcp-servers
- https://pypi.org/project/workspace-mcp/
- https://fast.io/resources/google-workspace-mcp-server/
- https://developers.notion.com/guides/mcp/build-mcp-client
- https://www.notion.com/blog/notions-hosted-mcp-server-an-inside-look
- https://toqan.ai/knowledge/how-to-add-the-microsoft-365-mcp-to-toqan/
- https://docs.litellm.ai/docs/mcp_servers/microsoft_365
- https://www.mintmcp.com/docs/microsoft-365-mcp-setup
- https://dotagents-docs.pages.dev/intro
- https://zapier.com/blog/muse-ai/
- https://anythingmcp.com/guides/connect-gmail-to-muse
- https://www.withone.ai/connect/muse-code/gmail
- https://tldv.io/blog/how-to-add-a-custom-connector-in-claude/

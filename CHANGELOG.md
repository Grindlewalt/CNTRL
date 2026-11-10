# Changelog

## 0.1.0 (2026-11-10)

First public release. The project was called Jav3 until this version.

### Runtime and VMs
- The agent loop runs in a disposable KVM guest over vsock, with no key, no
  database and no secrets; a hardened Docker runtime is the option for hosts
  without KVM.
- Per-project boxes, images with a versioned rebuild and an image-rebuild timer,
  and a guest memory guard so an out-of-memory browser no longer kills a turn.

### Network and egress
- Off by default. When on: a host proxy with per-project policy, an approval
  queue for new hosts, secret injection on the wire, metering, an anomaly cut
  and a live feed.

### Tools and approvals
- Tools, agents and skills are folders, hot-reloaded. Requests for packages,
  services, environments and skills go to a review queue. Security events
  record which run caused them.

### Memory
- File-backed memory with taint stamps on web-derived notes; agent-written
  notes wait for approval.

### Projects and git
- Every project is a git repo; writes pass one chokepoint with diff checks;
  commits, pushes and remotes need approval. Agent pull requests, with an
  approval page, and a Gitea mirror. Backups through rclone.

### Web app
- Streaming chat, project workspace windows, run trees, Review pages, Git page,
  Tools page, Settings.

### Terminal client
- The `cntrl` client (formerly `jav3`): chat, agents view, security pages,
  `/update` and `/restart`, local tools with approval.

### Publishing pages
- The agent can publish a page; you approve it; a separate read-only server
  (port 8090) serves it sandboxed to anyone with the link.

### Permission modes
- safe-yolo (the default), auto, ask and yolo. Yolo approves every request and
  can be switched on only with a password login and a confirmation.

### Search and web reading
- Search reports degraded engines and drops off-topic results; research stops
  on junk. `web_read` reads PDFs a slice at a time.

### Install
- One-line bootstrap, a re-runnable installer with a root phase and a check
  mode, `doctor --json`, and a guide for coding agents.

### Upgrading from Jav3
- Old `JARVIS_*` settings and the `jav3` command keep working.
- The installer moves `~/.config/jarvis` and `~/.local/share/jarvis` to the
  `cntrl` names and replaces the `jarvis*` systemd user units with `cntrl*`.
- The session cookie, device token prefix and database file keep their names.

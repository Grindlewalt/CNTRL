// ---- window registry: add a capability = one entry here + one case in windows.jsx ----
// These are the old project board's cards (and the backend's workspace_panel
// tool names them the same way: backend/gui.py PANEL_SIZES). `title` is the
// window header and the + menu row; `desc` is the row's second line; `label`
// is the two joined, for anything that wants one string.
export const WINDOW_TYPES = {
  plan: { title: 'Plan', desc: 'dump, checklist, agents' },
  board: { title: 'Task board', desc: 'goal, plan, runs' },
  git: { title: 'Git', desc: 'review, approve, push' },
  terminal: { title: 'Terminal', desc: 'a shell in the guest VM' },
  desktop: { title: 'Desktop', desc: 'watch the box’s screen' },
  journal: { title: 'Journal', desc: 'project.md' },
  editor: { title: 'Editor', desc: 'text and markdown' },
  renderer: { title: 'Renderer', desc: 'html, pdf, images' },
  organizer: { title: 'Files', desc: 'the project’s files' },
  run: { title: 'Run', desc: 'python sandbox' },
  todos: { title: 'To-dos', desc: '' },
  context: { title: 'Context', desc: 'files loaded into CNTRL' },
  agent: { title: 'Agent', desc: 'run an agent' },
  research: { title: 'Research', desc: 'research bots, live' },
  chat: { title: 'Chat', desc: 'any conversation, any project' },
  review: { title: 'Security', desc: 'approvals and alerts' },
  network: { title: 'Network', desc: 'egress and host approvals' },
  secrets: { title: 'Secrets', desc: 'key grants for this project' },
}
for (const v of Object.values(WINDOW_TYPES)) v.label = v.desc ? `${v.title}: ${v.desc}` : v.title

export const isWindowType = (t) => Object.prototype.hasOwnProperty.call(WINDOW_TYPES, t)

// Other spellings of the same cards (the slash commands' names), so either
// works in openWindow.
export const ALIASES = { todo: 'todos', taskboard: 'board', grants: 'secrets' }

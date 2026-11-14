import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from './api.js'
import { useAsk } from './ask.jsx'
import { notifyError } from './notify.js'
import Input from './components/Input.jsx'
import Button from './components/Button.jsx'
import { useRowMenu } from './components/RowMenu.jsx'
import { UiGlyph } from './shell/glyphs.jsx'
import { activeFrom } from './chatActive.js'
import { projectsChanged } from './projectsChanged.js'
import { NAME_RULE, nameProblem } from './projectName.js'

// The chat sidebar's list, grouped: Pinned (projects the operator pinned),
// Active (something running right now), Projects (click opens the project's
// home, ＋ makes a new one), then Starred, each folder, then Recent (everything
// unfiled). Each chat shows exactly once — an active chat lives under Active
// while it runs, a starred one under Starred even if it is filed, and each
// returns to its usual group when that stops. Each project shows once too:
// pinned ones only under Pinned.
//
// Every row's ⋯ and its right-click are the same menu (useRowMenu).
//
// Search sits on top. Typing filters the chat and project titles already
// loaded, instantly. Below those, "Deep search" asks the server to read the
// message text (GET /api/search/messages, paged, time-limited); a hit opens its
// chat through onOpen(id, { messageId }). The chat page does not scroll to a
// message yet, so it only opens the chat.
//
// Active is polled, not pushed (useActive below): GET /api/chat/running (every
// loop in flight, no DB read) for a plain chat's own streaming turn, and GET
// /api/chat/agents?scope=active for roots with agents working or a node
// waiting on the operator. Only chats in `conversations` show; an agent job
// root that is not a chat stays on the Agents page.
//
// Self-contained over the folder/star API (GET/POST/PATCH/DELETE
// /api/chat/folders, PATCH /api/conversations/{id} {starred, folder_id}) so a
// different chat shell can mount it as-is: the caller owns the conversation
// list and how a chat opens, renames and deletes; this owns folders, stars and
// which groups are folded.
//
// Two optional seams for the shell (/shell): `folders` hands in a folder list
// the caller already fetched (its one-round-trip GET /api/sidebar), after which
// every folder change is reported through `onChanged` instead of refetched
// here; `projectHref` points the project rows somewhere other than the
// project home.

const GROUPS_KEY = 'jarvis.chat.groups'
const ACTIVE_POLL_MS = 5000
const DEEP_PAGE = 20

function useActive(enabled) {
  const [active, setActive] = useState(() => new Map())
  useEffect(() => {
    if (!enabled) return undefined
    let alive = true
    let busy = false
    const poll = async () => {
      if (busy || document.hidden) return
      busy = true
      try {
        const [r, t] = await Promise.all([
          api('/api/chat/running'), api('/api/chat/agents?scope=active')])
        if (alive) setActive(activeFrom(r.running, t.nodes))
      } catch { /* keep the last answer; the next poll retries */ }
      busy = false
    }
    poll()
    const timer = setInterval(poll, ACTIVE_POLL_MS)
    const onVis = () => { if (!document.hidden) poll() }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      alive = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [enabled])
  return active
}

function readFolded() {
  try { return JSON.parse(localStorage.getItem(GROUPS_KEY)) || {} } catch { return {} }
}

// A chat's line in the sidebar. Untitled chats are summarised from the first
// prompt, so a pasted brief arrives as "# AGENT BRIEF…" or "[startup — …":
// the markdown heading mark and the bracket are noise in a list, and they
// are stripped here only — the toolbar and the tooltip keep the real title.
export function listTitle(c) {
  if (!c.summary) return `#${c.id}, ${c.started_at?.slice(5, 16) || ''}`
  return c.summary.replace(/^[#[\s]+/, '') || c.summary
}

function GroupHead({ id, label, count, folded, onToggle, children, rowProps }) {
  return (
    <div className="convo-group-head" {...rowProps}>
      <button type="button" className="convo-group-toggle" aria-expanded={!folded}
              aria-controls={`convo-group-${id}`} onClick={() => onToggle(id)}>
        <span className={folded ? 'chev' : 'chev open'} aria-hidden="true">›</span>
        <span className="ellipsis">{label}</span>
        {count > 0 && <span className="convo-group-count">{count}</span>}
      </button>
      {children}
    </div>
  )
}

const boardHref = (p) => `/projects/${encodeURIComponent(p.slug)}`
const homeHref = (p) => `/project/${encodeURIComponent(p.slug)}`

function ChatRow({ c, act, activeId, folders, known, onOpen, onRename, onDelete,
  patchChat, moveToNewFolder }) {
  const { rowProps, menu, open } = useRowMenu(() => [
    { label: c.starred ? 'Unstar' : 'Star', onSelect: () => patchChat(c.id, { starred: !c.starred }) },
    { label: 'Rename', onSelect: () => onRename(c) },
    '-',
    { caption: 'Move to folder' },
    { label: 'No folder', checked: c.folder_id == null || !known.has(c.folder_id),
      onSelect: () => patchChat(c.id, { folder_id: null }) },
    ...folders.map((f) => ({ label: f.name, checked: c.folder_id === f.id,
      onSelect: () => patchChat(c.id, { folder_id: f.id }) })),
    { label: 'New folder…', onSelect: () => moveToNewFolder(c) },
    '-',
    { label: 'Delete', danger: true, onSelect: () => onDelete(c) },
  ], { label: 'chat actions', width: 220, align: 'left' })
  return (
    <li className={[c.id === activeId ? 'active' : '', open ? 'menu-open' : '']
          .filter(Boolean).join(' ') || undefined}
        onClick={() => onOpen(c.id)} {...rowProps}>
      {/* title owns the row; the project slug sits under it so a long slug
          can never crush the title into two letters */}
      <div className="convo-main">
        {act ? (
          <span className="convo-active-line">
            <span className={act.needs ? 'convo-active-dot needs' : 'convo-active-dot'}
                  aria-hidden="true" />
            <span className="convo-title ellipsis" title={c.summary || `#${c.id}`}>
              {listTitle(c)}</span>
            {act.agents > 0 && (
              <span className="convo-active-agents"
                    title={`${act.agents} agent${act.agents === 1 ? '' : 's'} working`}>
                {act.agents}</span>
            )}
            {act.needs && (
              <span className="convo-active-needs" title={act.needs}>needs you</span>
            )}
          </span>
        ) : (
          <span className="convo-title ellipsis" title={c.summary || `#${c.id}`}>
            {listTitle(c)}</span>
        )}
        {/* an agent's thread is still a chat and still belongs in this list —
            it just isn't CNTRL speaking, so say so */}
        {(c.agent_slug || c.project_slug) && (
          <span className="convo-proj ellipsis">
            {c.agent_slug && <span className="convo-agent">{c.agent_slug}</span>}
            {c.project_slug}
          </span>
        )}
      </div>
      {/* overlaid on hover rather than reserving width; the menu is portalled
          (the list scrolls and the sidebar clips) */}
      <span className="convo-actions">{menu}</span>
    </li>
  )
}

function ProjectRow({ p, href, workspace, onPin, onRename, onDelete }) {
  const pinned = !!p.pinned_at
  const { rowProps, menu, open } = useRowMenu(() => [
    { label: pinned ? 'Unpin' : 'Pin to top', onSelect: () => onPin(p) },
    workspace && { label: 'Open workspace', onSelect: () => workspace(p) },
    { label: 'Rename', onSelect: () => onRename(p) },
    '-',
    { label: 'Delete', danger: true, onSelect: () => onDelete(p) },
  ], { label: `${p.name} actions`, width: 200, align: 'left' })
  return (
    <li className={open ? 'menu-open' : undefined} {...rowProps}>
      <Link to={href} title={p.slug}>
        <span className="proj-dot" aria-hidden="true" />
        <span className="convo-title ellipsis">{p.name}</span>
      </Link>
      <span className="convo-actions">{menu}</span>
    </li>
  )
}

function FolderHead({ f, count, folded, onToggle, onRename, onDelete }) {
  const { rowProps, menu } = useRowMenu(() => [
    { label: 'Rename', onSelect: () => onRename(f) },
    { label: 'Delete folder', danger: true, onSelect: () => onDelete(f) },
  ], { label: 'folder actions', width: 180 })
  return (
    <GroupHead id={`f:${f.id}`} label={f.name} count={count} folded={folded}
               onToggle={onToggle} rowProps={rowProps}>
      <span className="convo-group-menu">{menu}</span>
    </GroupHead>
  )
}

export default function ChatGroups({
  conversations, activeId, projects = [], onOpen, onRename, onDelete, onChanged,
  folders: givenFolders, projectHref, showActive = true,
}) {
  const ask = useAsk()
  const live = useActive(showActive)
  const [ownFolders, setFolders] = useState([])
  const controlled = givenFolders !== undefined
  const folders = controlled ? givenFolders : ownFolders
  const [folded, setFolded] = useState(readFolded)
  const [draft, setDraft] = useState(null)      // new-folder name while typing
  const [query, setQuery] = useState('')
  const [deep, setDeep] = useState(null)        // null, or the deep search's state
  const deepSeq = useRef(0)
  // the project rows open the project's home; a shell that has its own place
  // for a project passes projectHref, and then has no separate "workspace" item
  const hrefOf = projectHref || homeHref
  const navigate = useNavigate()
  const workspace = projectHref ? null : (p) => navigate(boardHref(p))

  // through a ref: callers pass a fresh onChanged each render, and as a
  // dependency it would refetch the folders on every one of them
  const changedRef = useRef(onChanged)
  changedRef.current = onChanged
  const loadFolders = useCallback(
    () => (controlled
      ? Promise.resolve(changedRef.current?.())
      : api('/api/chat/folders').then((r) => setFolders(r.folders)).catch(() => {})),
    [controlled])
  useEffect(() => { if (!controlled) loadFolders() }, [controlled, loadFolders])

  useEffect(() => {
    try { localStorage.setItem(GROUPS_KEY, JSON.stringify(folded)) } catch { /* private mode */ }
  }, [folded])
  const toggle = (id) => setFolded((f) => ({ ...f, [id]: !f[id] }))

  // a new query invalidates a deep search made for the old one
  useEffect(() => { deepSeq.current += 1; setDeep(null) }, [query])

  async function patchChat(id, body) {
    try {
      await api(`/api/conversations/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
    } catch (err) { notifyError(err) }
    onChanged?.()
    if (!controlled) loadFolders()
  }

  async function createFolder(name, thenFile = null) {
    const clean = name.trim()
    if (!clean) return null
    try {
      const r = await api('/api/chat/folders', {
        method: 'POST', body: JSON.stringify({ name: clean }) })
      await loadFolders()
      if (thenFile != null) await patchChat(thenFile, { folder_id: r.folder.id })
      return r.folder
    } catch (err) {
      notifyError(err)
      return null
    }
  }

  async function renameFolder(f) {
    const next = await ask.prompt('Rename folder', f.name, { confirmLabel: 'Rename' })
    if (!next?.trim()) return
    try {
      await api(`/api/chat/folders/${f.id}`, {
        method: 'PATCH', body: JSON.stringify({ name: next.trim() }) })
    } catch (err) { notifyError(err) }
    loadFolders()
  }

  async function deleteFolder(f) {
    if (!await ask.confirm(`Delete the folder “${f.name}”? Its chats move to Recent.`,
                           { confirmLabel: 'Delete', danger: true })) return
    try {
      await api(`/api/chat/folders/${f.id}`, { method: 'DELETE' })
    } catch (err) { notifyError(err) }
    loadFolders()
    if (!controlled) onChanged?.()
  }

  async function moveToNewFolder(c) {
    const name = await ask.prompt('New folder', '', { confirmLabel: 'Create' })
    if (name?.trim()) await createFolder(name, c.id)
  }

  async function pinProject(p) {
    try {
      await api(`/api/projects/${encodeURIComponent(p.slug)}/pin`, {
        method: 'PUT', body: JSON.stringify({ pinned: !p.pinned_at }) })
      projectsChanged()
    } catch (err) { notifyError(err) }
  }

  // a project row's ⋯ (2026-10-02: the operator found no way to delete a
  // project; Delete lived only on the Projects sheet's cards). Same calls as
  // that sheet: delete moves it to Recently deleted, where it can be restored.
  async function renameProject(p) {
    const next = await ask.prompt('Rename project', p.name, {
      confirmLabel: 'Rename', body: NAME_RULE })
    if (!next?.trim()) return
    const bad = nameProblem(next)
    if (bad) { notifyError(new Error(bad)); return }
    try {
      await api(`/api/projects/${encodeURIComponent(p.slug)}/name`, {
        method: 'PUT', body: JSON.stringify({ name: next.trim() }) })
      projectsChanged()
    } catch (err) { notifyError(err) }
  }

  async function deleteProject(p) {
    if (!await ask.confirm(`Move the project “${p.name}” to Recently deleted?`, {
      body: 'Its files and chats stay until you delete it forever from the Projects sheet '
        + '(＋ on Projects), where it can also be restored.',
      confirmLabel: 'Move to bin', danger: true })) return
    try {
      await api(`/api/projects/${encodeURIComponent(p.slug)}`, { method: 'DELETE' })
      projectsChanged()
    } catch (err) { notifyError(err) }
  }

  async function runDeep(more = false) {
    const seq = ++deepSeq.current
    const offset = more ? deep.offset + DEEP_PAGE : 0
    setDeep((d) => ({ ...(more ? d : { results: [] }), loading: true, error: '' }))
    try {
      const r = await api(`/api/search/messages?q=${encodeURIComponent(query.trim())}`
        + `&limit=${DEEP_PAGE}&offset=${offset}`)
      if (seq !== deepSeq.current) return
      setDeep((d) => ({
        results: [...(more ? d.results : []), ...r.results], more: r.more,
        timedOut: r.timed_out, offset, loading: false, error: '' }))
    } catch (err) {
      if (seq !== deepSeq.current) return
      setDeep((d) => ({ ...d, loading: false, error: err.message || 'Search failed' }))
    }
  }

  const known = new Set(folders.map((f) => f.id))
  // waiting on the operator first, then the list's own order (newest first)
  const activeRows = conversations.filter((c) => live.has(c.id))
    .sort((a, b) => !!live.get(b.id).needs - !!live.get(a.id).needs)
  const rest = activeRows.length ? conversations.filter((c) => !live.has(c.id)) : conversations
  const starred = rest.filter((c) => c.starred)
  const inFolder = (fid) => rest.filter((c) => !c.starred && c.folder_id === fid)
  // a folder id the list doesn't know yet (created elsewhere) reads as unfiled
  // rather than vanishing until the next folder refresh
  const recent = rest.filter(
    (c) => !c.starred && (c.folder_id == null || !known.has(c.folder_id)))
  const pinned = projects.filter((p) => p.pinned_at)
    .sort((a, b) => String(b.pinned_at).localeCompare(String(a.pinned_at)))
  const unpinned = projects.filter((p) => !p.pinned_at)

  const row = (c) => (
    <ChatRow key={c.id} c={c} act={live.get(c.id)} activeId={activeId} folders={folders}
             known={known} onOpen={onOpen} onRename={onRename} onDelete={onDelete}
             patchChat={patchChat} moveToNewFolder={moveToNewFolder} />
  )
  const projRow = (p) => (
    <ProjectRow key={p.slug} p={p} href={hrefOf(p)} workspace={workspace}
                onPin={pinProject} onRename={renameProject} onDelete={deleteProject} />
  )

  const group = (id, label, rows, { head = null, empty = null } = {}) => (
    <section key={id} className="convo-group">
      <GroupHead id={id} label={label} count={rows.length} folded={!!folded[id]}
                 onToggle={toggle}>{head}</GroupHead>
      {!folded[id] && (rows.length
        ? <ul id={`convo-group-${id}`} className="convo-rows list-divided">{rows.map(row)}</ul>
        : empty && <p id={`convo-group-${id}`} className="convo-group-empty">{empty}</p>)}
    </section>
  )

  const q = query.trim().toLowerCase()
  const searching = q.length > 0
  const hit = (...parts) => parts.some((s) => (s || '').toLowerCase().includes(q))
  const matchProjects = searching
    ? projects.filter((p) => hit(p.name, p.slug)) : []
  const matchChats = searching
    ? conversations.filter((c) => hit(listTitle(c), c.summary, c.project_slug, c.agent_slug))
    : []

  const searchBox = (
    <div className="convo-search">
      <Input type="search" value={query} placeholder="Search chats and projects"
             aria-label="search chats and projects" autoComplete="off"
             onChange={(e) => setQuery(e.target.value)}
             onKeyDown={(e) => { if (e.key === 'Escape') setQuery('') }} />
    </div>
  )

  if (searching) {
    return (
      <div className="convo-list">
        {searchBox}
        {matchProjects.length > 0 && (
          <section className="convo-group">
            <div className="convo-group-head"><span className="convo-group-label">Projects</span></div>
            <ul className="convo-rows list-divided">{matchProjects.map(projRow)}</ul>
          </section>
        )}
        {matchChats.length > 0 && (
          <section className="convo-group">
            <div className="convo-group-head"><span className="convo-group-label">Chats</span></div>
            <ul className="convo-rows list-divided">{matchChats.map(row)}</ul>
          </section>
        )}
        {!matchProjects.length && !matchChats.length && (
          <p className="convo-group-empty">No title matches.</p>
        )}
        <section className="convo-group convo-deep">
          {deep === null && query.trim().length >= 2 && (
            <Button variant="ghost" onClick={() => runDeep(false)}>
              Deep search (takes longer, reads and checks all text)</Button>
          )}
          {deep !== null && (
            <>
              <div className="convo-group-head"><span className="convo-group-label">In message text</span></div>
              {deep.results.length > 0 && (
                <ul className="convo-rows list-divided">
                  {deep.results.map((r) => (
                    <li key={r.message_id} className="convo-hit"
                        onClick={() => onOpen(r.conversation_id, { messageId: r.message_id })}>
                      <div className="convo-main">
                        <span className="convo-title ellipsis">
                          {r.title ? listTitle({ id: r.conversation_id, summary: r.title })
                            : `Chat #${r.conversation_id}`}</span>
                        <span className="convo-snippet">{r.snippet}</span>
                        {r.project_slug && <span className="convo-proj ellipsis">{r.project_slug}</span>}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {deep.loading && <p className="convo-group-empty">Searching message text…</p>}
              {deep.error && <p className="convo-group-empty">{deep.error}</p>}
              {!deep.loading && !deep.error && deep.timedOut && (
                <p className="convo-group-empty">The search ran out of time. Try a longer phrase.</p>
              )}
              {!deep.loading && !deep.error && !deep.timedOut && !deep.results.length && (
                <p className="convo-group-empty">Nothing in any message.</p>
              )}
              {!deep.loading && deep.more && (
                <Button variant="ghost" onClick={() => runDeep(true)}>Show more</Button>
              )}
            </>
          )}
        </section>
      </div>
    )
  }

  return (
    <div className="convo-list">
      {searchBox}
      {pinned.length > 0 && (
        <section className="convo-group">
          <GroupHead id="pinned" label="Pinned" count={pinned.length}
                     folded={!!folded.pinned} onToggle={toggle} />
          {!folded.pinned && (
            <ul id="convo-group-pinned" className="convo-rows proj-rows list-divided">
              {pinned.map(projRow)}</ul>
          )}
        </section>
      )}
      {activeRows.length > 0 && group('active', 'Active', activeRows)}
      {/* shown with no projects too: its ＋ is the way to make the first one.
          /projects opens the Projects sheet (create, import, rename, delete),
          which used to be reachable only from the bottom of Work's ＋ picker. */}
      <section className="convo-group">
        <GroupHead id="projects" label="Projects" count={unpinned.length}
                   folded={!!folded.projects} onToggle={toggle}>
          <Link to="/projects" className="win-btn convo-group-add" title="new project"
                aria-label="new project"><UiGlyph name="plus" size={15} /></Link>
        </GroupHead>
        {!folded.projects && (unpinned.length ? (
          <ul id="convo-group-projects" className="convo-rows proj-rows list-divided">
            {unpinned.map(projRow)}
          </ul>
        ) : (
          <p id="convo-group-projects" className="convo-group-empty">
            {projects.length ? 'Every project is pinned.' : 'No projects yet. The plus above makes one.'}
          </p>
        ))}
      </section>
      {starred.length > 0 && group('starred', 'Starred', starred)}
      {folders.map((f) => (
        <section key={`f:${f.id}`} className="convo-group">
          <FolderHead f={f} count={inFolder(f.id).length} folded={!!folded[`f:${f.id}`]}
                      onToggle={toggle} onRename={renameFolder} onDelete={deleteFolder} />
          {!folded[`f:${f.id}`] && (inFolder(f.id).length
            ? <ul id={`convo-group-f:${f.id}`} className="convo-rows list-divided">
                {inFolder(f.id).map(row)}</ul>
            : <p id={`convo-group-f:${f.id}`} className="convo-group-empty">
                Empty. File a chat here from its menu.</p>)}
        </section>
      ))}
      {draft === null
        ? <Button variant="ghost" className="convo-new-folder"
                  onClick={() => setDraft('')}>New folder</Button>
        : (
          <form className="convo-new-folder-form"
                onSubmit={async (e) => {
                  e.preventDefault()
                  if (await createFolder(draft)) setDraft(null)
                }}>
            <Input autoFocus value={draft} placeholder="Folder name" maxLength={60}
                   aria-label="new folder name"
                   onChange={(e) => setDraft(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Escape') setDraft(null) }}
                   onBlur={() => { if (!draft.trim()) setDraft(null) }} />
          </form>
        )}
      {group('recent', 'Recent', recent, { empty: 'No chats yet.' })}
    </div>
  )
}

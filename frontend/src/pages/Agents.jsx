import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, chatStream } from '../api.js'
import { notifyError } from '../notify.js'
import { useAsk } from '../ask.jsx'
import { ago } from '../format.js'
import { modelGroups, resolveId, useModel } from '../modelInfo.js'
import { useIsPhone } from '../breakpoints.js'
import { UiGlyph } from '../shell/glyphs.jsx'
import Page from '../components/Page.jsx'
import Tabs from '../components/Tabs.jsx'
import Button from '../components/Button.jsx'
import Input from '../components/Input.jsx'
import Modal from '../components/Modal.jsx'
import Select from '../components/Select.jsx'
import Tag from '../components/Tag.jsx'
import Toggle from '../components/Toggle.jsx'
import Menu, { MenuItem } from '../components/Menu.jsx'
import { useRowMenu } from '../components/RowMenu.jsx'
import EmptyState from '../components/EmptyState.jsx'
import Chat from './Chat.jsx'
import { StartHome } from './ProjectHome.jsx'
import './manage.css'

// The Agents page is a layout route: this shell owns the one <h1> and the tab
// strip, and each tab is a real URL (/agents[/:slug[/c/:id]], /agents/skills,
// /agents/outputs[/:slug]) rendered through the Outlet. `split` because two of
// the three tabs are a list + a pane; the Outputs tab is a lone <main>.
// Definitions owns every /agents/... that isn't Skills or Outputs.
const OTHER_TAB = /^\/agents\/(skills|outputs)(\/|$)/

export default function Agents() {
  const { pathname } = useLocation()
  const defs = !OTHER_TAB.test(pathname)
  return (
    <Page variant="split" title="Agents"
          actions={(
            <Tabs label="Agents sections" items={[
              { to: defs ? pathname : '/agents', end: true, label: 'Definitions' },
              { to: '/agents/skills', label: 'Skills' },
              { to: '/agents/outputs', label: 'Outputs' },
            ]} />
          )}>
      <Outlet />
    </Page>
  )
}

// The definitions tab: the roster on the left, and the chat with the picked
// agent on the right. That chat is Chat itself (pages/Chat.jsx, the one Work
// renders), with its bar, its side panel (Security, Files with the agent's
// outputs, Runs) and its composer; a new chat there runs as the agent. Its
// URL is /agents/<slug> (a new chat: the agent's home) or /agents/<slug>/c/<id>.
// The definition is edited in a dialog (Edit, from the row menu or the bar).
const NEW_ID = 'agent-new-name'

// The agent's model pin: "inherit" or one of the enabled models, written as
// its `provider/model` id, grouped by provider like the composer's picker. A
// pin the catalogue no longer offers (a disabled model, a hand-edited
// AGENT.md) stays selectable under its raw id rather than silently reading as
// something else. Base URLs belong to providers now, so there is no field.
function AgentModel({ models, value, onChange }) {
  const current = resolveId(models, value)
  const groups = modelGroups(models)
  const offered = groups.some((g) => g.models.some((x) => x.id === current))
  return (
    <Select label="Model" value={current} onChange={(e) => onChange(e.target.value)}
            options={[{ value: '', label: 'inherit (default)' },
                      ...(value && !offered ? [{ value: current, label: `${value} (not enabled)` }] : [])]}>
      {groups.map((g) => (g.provider
        ? (
          <optgroup key={g.provider} label={g.label}>
            {g.models.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
          </optgroup>
        )
        : g.models.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)))}
    </Select>
  )
}

// One roster row: the name opens a new chat with the agent; right-click or ⋯
// opens the same menu (chat, edit, run, rename, delete).
function AgentRow({ a, active, project, on }) {
  const { rowProps, menu, open } = useRowMenu([
    { label: 'Chat', onSelect: () => on.chat(a.slug) },
    { label: 'Edit', onSelect: () => on.edit(a.slug) },
    { label: 'Run a task', onSelect: () => on.run(a.slug) },
    '-',
    { label: 'Rename', onSelect: () => on.rename(a) },
    { label: 'Delete', danger: true, onSelect: () => on.del(a) },
  ], { label: `${a.name} actions` })
  return (
    <li {...rowProps} className={`agent-li${active ? ' active' : ''}${open ? ' menu-open' : ''}`}>
      <button type="button" aria-current={active || undefined} className="agent-row inline"
              onClick={() => on.chat(a.slug)}>
        <span className="agent-row-name">{a.name}</span>
        {project && <Tag>{project}</Tag>}
      </button>
      {menu}
    </li>
  )
}

// The picked agent's chats, under its row, as the Work sidebar lists chats:
// the open one marked, each with the same menu on ⋯ and right-click.
const ROSTER_CHATS = 8

function AgentChatRow({ c, active, on }) {
  const { rowProps, menu, open } = useRowMenu([
    { label: 'Open', onSelect: () => on.open(c.id) },
    { label: 'Rename', onSelect: () => on.renameChat(c) },
    '-',
    { label: 'Delete', danger: true, onSelect: () => on.deleteChat(c) },
  ], { label: 'Chat actions' })
  return (
    <li {...rowProps} className={`agent-chat-li${active ? ' active' : ''}${open ? ' menu-open' : ''}`}>
      <button type="button" className="agent-chat-row" aria-current={active || undefined}
              onClick={() => on.open(c.id)} title={c.summary || `Chat #${c.id}`}>
        <span className="agent-chat-title ellipsis">{c.summary || `Chat #${c.id}`}</span>
        <span className="agent-chat-when">{c.running ? 'now' : ago(c.started_at)}</span>
      </button>
      {menu}
    </li>
  )
}

function AgentChats({ chats, openId, on }) {
  const [all, setAll] = useState(false)
  const shown = all ? chats : chats.slice(0, ROSTER_CHATS)
  return (
    <li className="agent-chats-li">
      <ul className="agent-chats list-divided" aria-label="Chats">
        {shown.map((c) => <AgentChatRow key={c.id} c={c} active={c.id === openId} on={on} />)}
      </ul>
      {shown.length < chats.length && (
        <button type="button" className="agent-chats-more" onClick={() => setAll(true)}>
          Show all {chats.length}</button>
      )}
    </li>
  )
}

export function AgentDefinitions() {
  const { slug: selected = null, id } = useParams()
  const openId = /^\d+$/.test(id || '') ? Number(id) : null
  const navigate = useNavigate()
  const phone = useIsPhone()
  const ask = useAsk()
  const [agents, setAgents] = useState(null)   // null until the roster loads
  const [trash, setTrash] = useState([])
  const [projects, setProjects] = useState([])
  const [newName, setNewName] = useState('')
  const [editing, setEditing] = useState(null) // the slug open in the editor
  const [run, setRun] = useState(null)         // {slug, id} while a run streams
  const [chats, setChats] = useState([])       // the picked agent's chats
  const chatCtl = useRef(null)

  const refresh = () => {
    api('/api/agents').then((r) => setAgents(r.agents)).catch(() => setAgents([]))
    api('/api/agents/trash').then((r) => setTrash(r.agents)).catch(() => {})
  }
  useEffect(() => {
    refresh()
    api('/api/projects').then((r) => setProjects(r.projects || [])).catch(() => {})
  }, [])

  const loadChats = useCallback(() => {
    if (!selected) { setChats([]); return }
    api('/api/conversations').then((r) => setChats(
      (r.conversations || []).filter((c) => c.agent_slug === selected)))
      .catch(() => setChats([]))
  }, [selected])
  useEffect(() => { loadChats() }, [loadChats])

  // n = new agent, when not typing
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return
      if (e.key.toLowerCase() === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault()
        document.getElementById(NEW_ID)?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  async function create(e) {
    e.preventDefault()
    let name = newName.trim()
    if (!name) {
      // Add with an empty field asks, rather than silently doing nothing
      name = (await ask.prompt('Name the new agent', '',
                               { confirmLabel: 'Create' }) || '').trim()
      if (!name) return
    }
    try {
      const r = await api('/api/agents', {
        method: 'POST', body: JSON.stringify({ name }) })
      setNewName('')
      refresh()
      navigate(`/agents/${r.slug}`)
      setEditing(r.slug)    // a new agent needs its prompt first
    } catch (err) { notifyError(err) }
  }

  async function rename(a) {
    const name = (await ask.prompt('Rename agent', a.name,
                                   { confirmLabel: 'Rename' }) || '').trim()
    if (!name || name === a.name) return
    try {
      // PUT is the whole definition: every omitted field resets server-side
      const def = await api(`/api/agents/${a.slug}`)
      await api(`/api/agents/${a.slug}`, {
        method: 'PUT', body: JSON.stringify({ ...def, name }) })
      refresh()
    } catch (err) { notifyError(err) }
  }

  async function del(a) {
    if (!await ask.confirm(`Move agent "${a.slug}" to trash?`,
                           { confirmLabel: 'Move to trash' })) return
    try {
      await api(`/api/agents/${a.slug}`, { method: 'DELETE' })
    } catch (err) { notifyError(err); return }
    if (a.slug === editing) setEditing(null)
    if (a.slug === selected) navigate('/agents')
    refresh()
  }

  async function restore(slug) {
    try {
      await api(`/api/agents/${slug}/restore`, { method: 'POST' })
      refresh()
    } catch (err) { notifyError(err) }
  }
  async function purge(slug) {
    if (!await ask.confirm(`Permanently delete "${slug}"?`,
                           { body: "This can't be undone.",
                             confirmLabel: 'Delete forever', danger: true })) return
    await api(`/api/agents/${slug}/purge`, { method: 'DELETE' })
    refresh()
  }

  // A run is detached server-side: this only starts it and holds its id for
  // Stop. It is deliberately not watchRun()-registered, so the completion
  // notice toasts; the transcript is on the Outputs tab.
  async function start(slug) {
    if (run) return
    const task = (await ask.prompt('Task', '', { confirmLabel: 'Run' }) || '').trim()
    if (!task) return
    setRun({ slug, id: null })
    try {
      await chatStream({ task }, (ev) => {
        if (ev.type === 'start') setRun({ slug, id: ev.conversation_id })
        if (ev.type === 'error') notifyError(new Error(ev.message))
      }, `/api/agents/${slug}/run`)
    } catch (err) { notifyError(err) }
    setRun(null)
  }

  async function stop() {
    if (!run?.id) return
    try { await api(`/api/agents/runs/${run.id}/stop`, { method: 'POST' }) } catch { /* already done */ }
  }

  async function renameChat(c) {
    const next = await ask.prompt('Rename this chat', c.summary || '', { confirmLabel: 'Rename' })
    if (next === null || !next.trim()) return
    try {
      await api(`/api/conversations/${c.id}`, {
        method: 'PATCH', body: JSON.stringify({ title: next.trim() }) })
    } catch (err) { notifyError(err) }
    loadChats()
    chatCtl.current?.refresh?.()
  }
  async function deleteChat(c) {
    // the same words as the chat page: a chat has no bin
    if (!await ask.confirm(`Permanently delete chat #${c.id}?`,
                           { body: 'Its messages and tool history are removed. This cannot be undone.',
                             confirmLabel: 'Delete forever', danger: true })) return
    try { await api(`/api/conversations/${c.id}`, { method: 'DELETE' }) } catch (err) { notifyError(err) }
    if (c.id === openId) navigate(`/agents/${selected}`)
    loadChats()
  }

  const on = {
    chat: (slug) => navigate(`/agents/${slug}`),    // a new chat: the agent's home
    edit: (slug) => setEditing(slug),
    run: (slug) => start(slug),
    rename, del,
    open: (cid) => navigate(`/agents/${selected}/c/${cid}`),
    renameChat, deleteChat,
  }
  const projectName = (slug) => projects.find((p) => p.slug === slug)?.name || slug
  const agent = agents?.find((a) => a.slug === selected) || null

  return (
    <>
      <aside className={selected ? 'agents-aside picked' : 'agents-aside'}>
        <form className="agent-new" onSubmit={create}>
          <Input id={NEW_ID} placeholder="Agent name" aria-label="New agent name"
                 value={newName} onChange={(e) => setNewName(e.target.value)} />
          <Button type="submit" aria-label="Create agent">Add</Button>
        </form>
        <ul className="agent-list list-divided">
          {(agents || []).map((a) => (
            <Fragment key={a.slug}>
              <AgentRow a={a} active={selected === a.slug}
                        project={a.project ? projectName(a.project) : ''} on={on} />
              {selected === a.slug && chats.length > 0 && (
                <AgentChats chats={chats} openId={openId} on={on} />
              )}
            </Fragment>
          ))}
          {agents && agents.length === 0 && (
            <EmptyState as="li">No agents yet. Name one above and press Add.</EmptyState>
          )}
        </ul>
        {trash.length > 0 && (
          <details className="deleted-fold">
            <summary>
              Recently deleted ({trash.length})
              <span className="chev" aria-hidden="true">›</span>
            </summary>
            <ul className="trash-list">
              {trash.map((a) => (
                <li key={a.slug}>
                  <span className="grow ellipsis">{a.name}</span>
                  <button className="win-btn" aria-label={`Restore ${a.name}`}
                          onClick={() => restore(a.slug)}>↺</button>
                  <button className="win-btn" aria-label={`Delete ${a.name} forever`}
                          onClick={() => purge(a.slug)}>×</button>
                </li>
              ))}
            </ul>
          </details>
        )}
      </aside>
      {agent ? (
        <main className="agent-chat-pane">
          <Chat key={agent.slug} controlRef={chatCtl} sidebar={false} remember={false}
                agent={{ slug: agent.slug, name: agent.name, project: agent.project }}
                openId={openId}
                onConversation={(cid) => {
                  loadChats()
                  navigate(cid ? `/agents/${agent.slug}/c/${cid}` : `/agents/${agent.slug}`,
                           { replace: true })
                }}
                toolbarStart={phone && (
                  <Button variant="ghost" className="agent-back" onClick={() => navigate('/agents')}>
                    ‹ Agents</Button>
                )}
                toolbarExtra={(
                  <>
                    <button type="button" className="pp-bar-btn" aria-haspopup="dialog"
                            onClick={() => setEditing(agent.slug)}>
                      <span className="pp-v">Edit</span>
                    </button>
                    <button type="button" className="icon-btn" title="New chat"
                            aria-label={`New chat with ${agent.name}`}
                            onClick={() => navigate(`/agents/${agent.slug}`)}>
                      <UiGlyph name="plus" /></button>
                  </>
                )}
                freshHome={(ctl) => (
                  <StartHome title={agent.name} lede={agent.description}
                             placeholder={`Message ${agent.name}`} ctl={ctl}
                             chats={ctl.conversations.filter((c) => c.agent_slug === agent.slug)}
                             empty={`No chats with ${agent.name} yet. Start one above.`} />
                )} />
        </main>
      ) : (
        <main className="editor-pane split-idle">
          <EmptyState pad>
            {selected && agents
              ? <>No agent called “{selected}”. It may have been deleted or renamed.</>
              : <>Pick an agent on the left to chat with it, or press <kbd>n</kbd> to name a new one.</>}
          </EmptyState>
        </main>
      )}
      {editing && (
        <AgentEditor key={editing} slug={editing} projects={projects}
                     onClose={() => setEditing(null)} onSaved={refresh}
                     run={run && run.slug === editing ? run : null} busy={!!run}
                     onRun={() => start(editing)} onStop={stop} />
      )}
    </>
  )
}

// The definition, in a dialog over the chat: the prompt, then the settings.
// Save PUTs the whole definition back (every omitted field resets
// server-side), so the fields this view doesn't show (base_url, the legacy
// *_exclude lists) round-trip untouched.
function AgentEditor({ slug, projects, onClose, onSaved, run, busy, onRun, onStop }) {
  const ask = useAsk()
  const modelInfo = useModel()
  const [agent, setAgent] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [drafting, setDrafting] = useState(false)
  const [last, setLast] = useState(null)
  const [skills, setSkills] = useState([])
  useEffect(() => {
    api(`/api/agents/${slug}`).then(setAgent).catch((err) => { notifyError(err); onClose() })
    api(`/api/agents/${slug}/outputs?limit=1`).then((r) => setLast(r.outputs?.[0] || null))
      .catch(() => {})
    api('/api/skills').then((r) => setSkills((r.skills || []).map((s) => s.name)))
      .catch(() => {})
  }, [slug])   // eslint-disable-line react-hooks/exhaustive-deps

  const patch = (p) => { setAgent((a) => ({ ...a, ...p })); setDirty(true) }
  async function save() {
    setSaving(true)
    try {
      await api(`/api/agents/${slug}`, { method: 'PUT', body: JSON.stringify(agent) })
      setDirty(false)
      onSaved()
      return true
    } catch (err) { notifyError(err); return false }   // e.g. 400: missing project
    finally { setSaving(false) }
  }
  async function close() {
    if (dirty && !await ask.confirm('Discard unsaved changes?',
                                    { confirmLabel: 'Discard', danger: true })) return
    onClose()
  }
  async function runTask() {
    if (dirty && !await save()) return
    onRun()
  }
  // one call, no quiz: the description in, a whole prompt out
  async function draft() {
    const description = (await ask.prompt('What should this agent do?',
      agent.description || '', { confirmLabel: 'Draft' }) || '').trim()
    if (!description) return
    setDrafting(true)
    try {
      const r = await api('/api/agents/prompt-generate', {
        method: 'POST', body: JSON.stringify({ description, answers: [] }) })
      patch({ description, prompt: r.prompt })
    } catch (err) { notifyError(err) }
    setDrafting(false)
  }

  // a binding to a since-deleted project still shows as itself, or Save would
  // quietly unbind it
  const projectOptions = [
    { value: '', label: 'any' },
    ...projects.map((p) => ({ value: p.slug, label: p.name })),
    ...(agent?.project && !projects.some((p) => p.slug === agent.project)
      ? [{ value: agent.project, label: `${agent.project} (missing)` }] : []),
  ]
  const off = agent?.skills_exclude || []
  const toggleSkill = (name) => patch({
    skills_exclude: off.includes(name) ? off.filter((n) => n !== name) : [...off, name] })

  return (
    <Modal title={agent ? `Edit ${agent.name}` : 'Edit agent'} width={720} className="agent-editor"
           onClose={close} closeOnScrim={false}
           onSubmit={async () => { if (await save()) onClose() }}
           submitLabel="Save" submitDisabled={!agent || !dirty} busy={saving}
           footerStart={agent && (run
             ? <Button variant="ghost" disabled={!run.id} onClick={onStop}>Stop the run</Button>
             : <Button variant="ghost" disabled={busy} onClick={runTask}>Run a task</Button>)}>
      {!agent ? <EmptyState>Loading…</EmptyState> : (
        <>
          {last && (
            <p className="agent-last">
              Last run: {ago(last.last_at || last.started_at)}, {last.running ? 'running' : 'done'}
            </p>
          )}
          <div className="agent-prompt-head">
            <label htmlFor="agent-prompt">Prompt</label>
            <Button variant="ghost" disabled={drafting} onClick={draft}>
              {drafting ? 'Drafting…' : 'Draft prompt'}
            </Button>
          </div>
          <textarea id="agent-prompt" className="md-editor agent-prompt" spellCheck={false}
                    rows={12} value={agent.prompt}
                    onChange={(e) => patch({ prompt: e.target.value })} />
          <div className="agent-grid">
            <Select label="Works in" value={agent.project || ''} options={projectOptions}
                    recentKey="project"
                    onChange={(e) => patch({ project: e.target.value })} />
            <AgentModel models={modelInfo} value={agent.model || ''}
                        onChange={(model) => patch({ model })} />
            <Input label="Max rounds" type="number" min={0} max={200}
                   value={agent.max_iterations || ''} placeholder="default"
                   onChange={(e) => patch({
                     max_iterations: Math.max(0, parseInt(e.target.value, 10) || 0) })} />
            <div className="field">
              <span>Own memory</span>
              <div className="agent-inline">
                <Toggle label="Own memory" checked={!!agent.own_memory}
                        onChange={(v) => patch({ own_memory: v })} />
                {agent.own_memory && <PrivateNotes key={agent.slug} slug={agent.slug} />}
              </div>
            </div>
            {skills.length > 0 && (
              <div className="field agent-wide">
                <span>Skills off</span>
                <SkillsOff skills={skills} off={off} onToggle={toggleSkill} />
              </div>
            )}
          </div>
        </>
      )}
    </Modal>
  )
}

// The skills this agent does without: each a removable Tag, plus a "+" that
// opens a multi-pick of every skill (the menu stays open between picks).
function SkillsOff({ skills, off, onToggle }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="agent-inline">
      {off.map((name) => (
        <Tag key={name}>
          {name}
          <button type="button" className="tag-x" aria-label={`Give back ${name}`}
                  onClick={() => onToggle(name)}>×</button>
        </Tag>
      ))}
      <Menu open={open} onClose={() => setOpen(false)} align="left" floating
            label="Skills off"
            trigger={(
              <Button variant="ghost" aria-label="Pick skills to turn off"
                      aria-haspopup="menu" aria-expanded={open}
                      onClick={() => setOpen((v) => !v)}>+</Button>
            )}>
        {skills.map((name) => (
          <MenuItem key={name} checked={off.includes(name)} onClick={() => onToggle(name)}>
            {name}
          </MenuItem>
        ))}
      </Menu>
    </div>
  )
}

// own_memory redirects the agent's memory_read/write to agents/<slug>/memory/;
// the notes load on first open and are read-only here. Keyed by slug, so
// another agent's notes start closed and unloaded.
function PrivateNotes({ slug }) {
  const [notes, setNotes] = useState(null)
  const load = (e) => {
    if (!e.currentTarget.open || notes) return
    api(`/api/agents/${slug}/memory`).then((r) => setNotes(r.notes || []))
      .catch(() => setNotes([]))
  }
  return (
    <details className="agent-notes" onToggle={load}>
      <summary>Notes <span className="chev" aria-hidden="true">›</span></summary>
      {notes === null ? <EmptyState>Loading…</EmptyState>
        : notes.length === 0 ? <EmptyState>This agent has no private notes yet.</EmptyState>
          : notes.map((n) => (
            <details key={n.name} className="agent-note">
              <summary><code>{n.name}</code></summary>
              <pre>{n.body}</pre>
            </details>
          ))}
    </details>
  )
}

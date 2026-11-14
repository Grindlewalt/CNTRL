import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api.js'
import { TAB_ID } from '../tab'
import { notify, notifyError } from '../notify.js'
import { useAsk } from '../ask.jsx'
import { useIsPhone } from '../breakpoints.js'
import Md from '../Md.jsx'
import ModelPicker from '../ModelPicker.jsx'
import { PermissionModeSelect, usePermissionMode } from '../AskUser.jsx'
import { wireMode } from '../askUser.js'
import { onProjectsChanged } from '../projectsChanged.js'
import { SendGlyph, UiGlyph } from '../shell/glyphs.jsx'
import {
  Button, EmptyState, HelpTip, Input, Modal, Select, SidePanel, Tag, useRowMenu,
} from '../components/index.js'
import {
  cadenceLabel, chatTitle, estimateTokens, firstLine, fmtTokens, relativeDay, summaryOf,
} from './projectHome.js'
import './work.css'

// /project/:slug, a project's home: the name, a composer that starts a chat
// in the project, the project's chats, and a column with what rides along
// with every chat here (instructions, context files), its memory and its
// schedules. One read, GET /api/projects/<slug>/home (backend/project_home.py);
// every write goes through an endpoint that already guards it: instructions
// and uploads through the write chokepoint onto the always-loaded list,
// schedules through /api/schedules, chats through /api/chat.
//
// ProjectHomeBody is the page without the route: Work hosts it in its main
// area for /project/:slug and keeps the chat sidebar beside it.

const enc = encodeURIComponent

export default function ProjectHome() {
  const { slug } = useParams()
  return <ProjectHomeBody key={slug} slug={slug} />
}

export function ProjectHomeBody({ slug }) {
  const [home, setHome] = useState(null)   // GET /home; false = no such project
  const refresh = useCallback(() => api(`/api/projects/${enc(slug)}/home`)
    .then(setHome)
    .catch((err) => { if (err.status === 404) setHome(false); else notifyError(err) }), [slug])
  useEffect(() => {
    refresh()
    const h = () => refresh()
    const seen = () => { if (document.visibilityState === 'visible') refresh() }
    const off = onProjectsChanged(h)
    window.addEventListener('jarvis-files-changed', h)
    document.addEventListener('visibilitychange', seen)
    return () => {
      off()
      window.removeEventListener('jarvis-files-changed', h)
      document.removeEventListener('visibilitychange', seen)
    }
  }, [refresh])

  if (home === false) {
    return (
      <div className="ph-page">
        <div className="ph-missing">
          <h1 className="ph-title">No project called “{slug}”</h1>
          <p className="ph-lede">It may have been deleted or renamed. <Link to="/">Back to Work</Link></p>
        </div>
      </div>
    )
  }
  if (!home) return <div className="ph-page" aria-busy="true" />
  const patch = (p) => setHome((h) => ({ ...h, ...p }))
  const summary = summaryOf(home.project_md)
  return (
    <div className="ph-page">
      <div className="ph-grid">
        <div className="ph-head">
          <h1 className="ph-title">{home.name}</h1>
          {summary && <p className="ph-lede">{summary}</p>}
          <Composer slug={slug} name={home.name} />
        </div>
        <aside className="ph-side" aria-label={`About ${home.name}`}>
          <ul className="ph-card list-divided">
            <InstructionsRow slug={slug} name={home.name} ins={home.instructions}
                             onSaved={(instructions) => { patch({ instructions }); refresh() }} />
            <ContextRow slug={slug} ctx={home.context} onChange={(context) => patch({ context })} />
            <MemoryRow slug={slug} name={home.name} mem={home.memory} />
            <ScheduledRow slug={slug} name={home.name} list={home.schedules} onChanged={refresh} />
          </ul>
        </aside>
        <Recents chats={home.chats} name={home.name} onChanged={refresh} />
      </div>
    </div>
  )
}

// ---- the composer: sending starts a chat pinned to this project ------------

// POST /api/chat and read only as far as `start`, which names the new chat.
// The turn runs server-side as a detached task (chat.py start_turn: "it
// outlives this HTTP connection"), so dropping the stream here loses nothing:
// the chat page re-attaches to the running turn when it opens.
async function startChat(body) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tab: TAB_ID, ...body }),
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* not json */ }
    throw new ApiError(res.status, detail, res)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let i
      while ((i = buf.indexOf('\n\n')) !== -1) {
        const chunk = buf.slice(0, i)
        buf = buf.slice(i + 2)
        if (!chunk.startsWith('data: ')) continue
        const ev = JSON.parse(chunk.slice(6))
        if (ev.type === 'start' && ev.conversation_id) return ev.conversation_id
        if (ev.type === 'error') throw new Error(ev.message || 'the chat did not start')
      }
    }
  } finally {
    reader.cancel().catch(() => {})
  }
  throw new Error('the chat did not start')
}

function openChat(navigate, id) {
  // the chat page resumes this id on mount (routes.jsx OpenChat does the same)
  try { localStorage.setItem('jarvis.chat.last', String(id)) } catch { /* private mode */ }
  navigate(`/c/${id}`)
}

function Composer({ slug, name }) {
  const navigate = useNavigate()
  const phone = useIsPhone()
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)
  const [multi, setMulti] = useState(false)
  const [mode, setMode] = usePermissionMode(null)
  const box = useRef(null)

  // the draft grows to the CSS max-height; past one line the pill relaxes
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
    setMulti(el.scrollHeight > 48 || text.includes('\n'))
  }, [text])

  async function send() {
    const message = text.trim()
    if (!message || sending) return
    setSending(true)
    setError(null)
    try {
      const id = await startChat({
        message, conversation_id: null, project: slug, project_mode: 'pin',
        permission_mode: wireMode(mode),
      })
      openChat(navigate, id)
    } catch (err) {
      setError(err.detail || err.message || String(err))
      setSending(false)
    }
  }

  const label = `New chat in ${name}`
  return (
    <div className="ph-start">
      <form className="ph-composer" onSubmit={(e) => { e.preventDefault(); send() }}>
        <div className={`composer-inner${multi ? ' multi' : ''}`}>
          <textarea ref={box} rows={1} value={text} placeholder={label} aria-label={label}
                    autoFocus={!phone} disabled={sending}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                        e.preventDefault()
                        send()
                      }
                    }} />
          <button type="submit" className="send-btn" aria-label="Start the chat"
                  title="Start the chat" disabled={!text.trim() || sending}>
            <SendGlyph />
          </button>
        </div>
      </form>
      <div className="ph-chips">
        <ModelPicker chip="ph-model-chip" wrap="ph-model" floating />
        <PermissionModeSelect cid={null} value={mode} onChange={setMode} />
        {sending && <span className="ph-status" aria-live="polite">Starting…</span>}
      </div>
      {error && <p className="ph-error" role="alert">{error}</p>}
    </div>
  )
}

// ---- Recents --------------------------------------------------------------

// On a phone the list comes before the card, so it starts short: the card
// stays a few rows away instead of under every chat the project has.
const PHONE_RECENTS = 8

function Recents({ chats, name, onChanged }) {
  const phone = useIsPhone()
  const [all, setAll] = useState(false)
  const shown = phone && !all ? chats.slice(0, PHONE_RECENTS) : chats
  return (
    <section className="ph-recents" aria-labelledby="ph-recents-h">
      <h2 id="ph-recents-h" className="ph-h">Recents</h2>
      {chats.length === 0
        ? <EmptyState className="ph-empty">No chats in {name} yet. Start one above.</EmptyState>
        : (
          <ul className="list-divided ph-chats">
            {shown.map((c) => <ChatRow key={c.id} c={c} onChanged={onChanged} />)}
          </ul>
        )}
      {shown.length < chats.length && (
        <button type="button" className="ph-more-chats" onClick={() => setAll(true)}>
          Show all {chats.length} chats</button>
      )}
    </section>
  )
}

function ChatRow({ c, onChanged }) {
  const navigate = useNavigate()
  const ask = useAsk()
  const open = () => openChat(navigate, c.id)
  async function rename() {
    const next = await ask.prompt('Rename this chat', c.summary || '', { confirmLabel: 'Rename' })
    if (next === null || !next.trim()) return
    try {
      await api(`/api/conversations/${c.id}`, {
        method: 'PATCH', body: JSON.stringify({ title: next.trim() }) })
    } catch (err) { notifyError(err) }
    onChanged()
  }
  async function remove() {
    // the same words as the chat page: a chat has no bin
    if (!await ask.confirm(`Permanently delete chat #${c.id}?`,
                           { body: 'Its messages and tool history are removed. This cannot be undone.',
                             confirmLabel: 'Delete forever', danger: true })) return
    try { await api(`/api/conversations/${c.id}`, { method: 'DELETE' }) } catch (err) { notifyError(err) }
    onChanged()
  }
  const { rowProps, menu, open: menuOpen } = useRowMenu([
    { label: 'Open', onSelect: open },
    { label: 'Rename', onSelect: rename },
    '-',
    { label: 'Delete', danger: true, onSelect: remove },
  ], { label: 'Chat actions', buttonClassName: 'win-btn ph-more' })
  const when = c.last_at || c.started_at
  return (
    <li {...rowProps} className={`ph-chat${menuOpen ? ' menu-open' : ''}`}>
      <button type="button" className="ph-chat-open" onClick={open}>
        <span className="ph-chat-title ellipsis">{chatTitle(c)}</span>
        <span className={`ph-chat-when${c.running ? ' running' : ''}`}
              title={c.last_at ? `Last message ${c.last_at} UTC`
                : (c.started_at ? `Started ${c.started_at} UTC` : undefined)}>
          {c.running ? 'Working now' : relativeDay(when)}
        </span>
      </button>
      {menu}
    </li>
  )
}

// ---- the right column -----------------------------------------------------

const ICONS = {
  // lines of text with a pen: words you write for every chat
  instructions: <><path d="M5 6h10M5 10h8M5 14h5" /><path d="m14.5 19.5 1-3.5 5-5a1.4 1.4 0 0 1 2 2l-5 5Z" /></>,
  // a stack of pages: documents that ride along
  context: <><rect x="7" y="3.5" width="12" height="15" rx="2" /><path d="M4.5 7v11.5a2 2 0 0 0 2 2H15" /></>,
  // the nav's memory book and schedules calendar (nav.jsx)
  memory: <path d="M12 6.6C10.4 5.3 8.4 4.7 5 4.7v12.9c3.4 0 5.4.6 7 1.9 1.6-1.3 3.6-1.9 7-1.9V4.7c-3.4 0-5.4.6-7 1.9Zm0 0v12.9" />,
  schedules: <><rect x="3.5" y="5.2" width="17" height="15.3" rx="2.2" /><path d="M3.5 10h17M8 3.5v3.4M16 3.5v3.4M8.4 14.3h3" /></>,
}

function PhIcon({ name }) {
  return (
    <svg className="ph-icon" width="18" height="18" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true">
      {ICONS[name]}
    </svg>
  )
}

function SideRow({ icon, label, action, actionLabel, onAction, className = '', children, ...rest }) {
  return (
    <li className={`ph-row${className ? ` ${className}` : ''}`} {...rest}>
      <div className="ph-row-head">
        <PhIcon name={icon} />
        <h2 className="ph-row-label">{label}</h2>
        {action && (
          <Button variant="ghost" className="ph-act" onClick={onAction}
                  aria-label={actionLabel || `${action} ${label.toLowerCase()}`}>{action}</Button>
        )}
      </div>
      {children}
    </li>
  )
}

// Instructions: instructions.md, first on the always-loaded list.
function InstructionsRow({ slug, name, ins, onSaved }) {
  const [editing, setEditing] = useState(false)
  // the start of the text, two lines at most (CSS clamps it)
  const preview = firstLine(String(ins.text || '').replace(/\s+/g, ' '), 240)
  return (
    <SideRow icon="instructions" label="Instructions" action="Edit" onAction={() => setEditing(true)}>
      {preview
        ? <p className="ph-sub ph-clamp">{preview}</p>
        : <p className="ph-sub ph-faint">None yet</p>}
      {ins.text && !ins.inlined && (
        <p className="ph-sub"><Tag tone="pending">over budget</Tag> only its name reaches the model</p>
      )}
      {ins.held && (
        <p className="ph-sub ph-warn">A change waits for you on the <Link to="/memory">Memory page</Link></p>
      )}
      {/* portalled: a Modal is not, and this row sits inside the card */}
      {editing && createPortal(
        <InstructionsEditor slug={slug} name={name} ins={ins}
                            onClose={() => setEditing(false)} onSaved={onSaved} />,
        document.body)}
    </SideRow>
  )
}

function InstructionsEditor({ slug, name, ins, onClose, onSaved }) {
  const [text, setText] = useState(ins.text)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  async function save() {
    setBusy(true)
    setError(null)
    try {
      const r = await api(`/api/projects/${enc(slug)}/instructions`, {
        method: 'PUT', body: JSON.stringify({ content: text, base_sha256: ins.sha256 }) })
      onSaved(r.instructions)
      onClose()
      notify(text.trim() ? 'Instructions saved' : 'Instructions removed')
    } catch (err) {
      setError(err.detail || err.message)
      setBusy(false)
    }
  }
  return (
    <Modal title="Instructions" width={640} onClose={onClose} onSubmit={save} submitLabel="Save"
           submitDisabled={text === ins.text} busy={busy} closeOnScrim={false}
           help={`Every chat in ${name} gets these in its prompt, after the project's journal. `
             + 'They are saved as instructions.md in the project. Empty them to remove the file.'}
           footerStart={error
             ? <span className="ph-error" role="alert">{error}</span>
             : <span className="ph-meta">{fmtTokens(estimateTokens(text))} tokens</span>}>
      {ins.held && (
        <p className="ph-note">CNTRL proposed a change to these after reading outside content.
          It waits on the <Link to="/memory">Memory page</Link>; saving here replaces the file
          it would change.</p>
      )}
      <Input textarea rows={14} className="ph-ins-text" aria-label="Instructions" autoFocus
             placeholder="How CNTRL should work in this project" value={text}
             onChange={(e) => setText(e.target.value)} />
    </Modal>
  )
}

// Context: documents on the always-loaded list. Uploads land in context/.
const shortPath = (f) => (f.uploaded ? f.path.replace(/^context\//, '') : f.path)

function ContextRow({ slug, ctx, onChange }) {
  const ask = useAsk()
  const picker = useRef(null)
  const depth = useRef(0)
  const [drag, setDrag] = useState(false)
  const [adding, setAdding] = useState(null)

  async function add(files) {
    if (!files.length) return
    for (const f of files) {
      setAdding(f.name)
      const fd = new FormData()
      fd.append('file', f)
      try {
        const res = await fetch(`/api/projects/${enc(slug)}/context-files`, { method: 'POST', body: fd })
        if (!res.ok) {
          let detail = res.statusText
          try { detail = (await res.json()).detail || detail } catch { /* not json */ }
          notify(`${f.name} was not added: ${detail}`, { sev: 'warn', life: 12 })
        } else {
          onChange((await res.json()).context)
        }
      } catch (err) { notifyError(err) }
    }
    setAdding(null)
    window.dispatchEvent(new Event('jarvis-files-changed'))   // the board's file views
  }

  async function remove(f) {
    const name = shortPath(f)
    const ok = await ask.confirm(f.uploaded ? `Remove ${name}?` : `Stop loading ${name}?`, {
      body: f.uploaded
        ? 'It is deleted from the project and no longer loaded into prompts.'
        : 'The file stays in the project; it is just no longer loaded into every prompt.',
      confirmLabel: f.uploaded ? 'Remove' : 'Stop loading', danger: f.uploaded })
    if (!ok) return
    try {
      const r = await api(`/api/projects/${enc(slug)}/context-files?path=${enc(f.path)}`,
                          { method: 'DELETE' })
      onChange(r.context)
      window.dispatchEvent(new Event('jarvis-files-changed'))
    } catch (err) { notifyError(err) }
  }

  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files')
  const dropProps = {
    onDragEnter: (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth.current += 1; setDrag(true) },
    onDragOver: (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy' },
    onDragLeave: () => { depth.current = Math.max(0, depth.current - 1); if (!depth.current) setDrag(false) },
    onDrop: (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth.current = 0
      setDrag(false)
      add(Array.from(e.dataTransfer.files || []))
    },
  }

  const files = ctx.files || []
  return (
    <SideRow icon="context" label="Context" action="Add" actionLabel="Add context files"
             onAction={() => picker.current?.click()}
             className={drag ? 'ph-drop' : ''} {...dropProps}>
      <input ref={picker} type="file" multiple hidden
             onChange={(e) => { add(Array.from(e.target.files || [])); e.target.value = '' }} />
      <p className="ph-sub ph-sub-row">
        <span>Loaded into every prompt</span>
        <HelpTip label="About context files" align="right">
          These files ride every prompt in this project, after the journal and the instructions,
          until {fmtTokens(ctx.budget)} tokens are used (the total counts all three). A file past
          that is named to the model to read when it needs it. Text files and PDFs (as their
          text). Only you can change this list.
        </HelpTip>
      </p>
      {files.length > 0 && (
        <ul className="list-divided ph-files">
          {files.map((f) => (
            <li key={f.path} className="ph-file">
              <span className="ph-file-name ellipsis" title={f.path}>{shortPath(f)}</span>
              {!f.inlined && (
                <Tag tone="pending">{f.missing ? 'missing' : f.binary ? 'not text' : 'over budget'}</Tag>
              )}
              <span className="ph-meta">{fmtTokens(f.tokens)}</span>
              <button type="button" className="icon-btn ph-x" onClick={() => remove(f)}
                      aria-label={f.uploaded ? `Remove ${shortPath(f)}` : `Stop loading ${f.path}`}
                      title={f.uploaded ? 'Remove' : 'Stop loading'}>
                <UiGlyph name="close" size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding
        ? <p className="ph-sub" aria-live="polite">Adding {adding}…</p>
        : files.length === 0 && (
          <button type="button" className="ph-dropzone" onClick={() => picker.current?.click()}>
            {drag ? 'Drop to add' : 'Drop files here or choose them'}
          </button>
        )}
      {drag && files.length > 0 && <p className="ph-sub ph-drop-hint">Drop to add</p>}
      <p className="ph-total">
        <span className="ph-meta">{fmtTokens(ctx.used)} of {fmtTokens(ctx.budget)} tokens</span>
      </p>
    </SideRow>
  )
}

// Memory: the journal (project.md) and the notes that mention the project.
function MemoryRow({ slug, name, mem }) {
  const [open, setOpen] = useState(false)
  const n = mem?.notes || 0
  return (
    <SideRow icon="memory" label="Memory" action="View" onAction={() => setOpen(true)}>
      <p className="ph-sub">
        {n === 0 ? 'The journal' : n === 1 ? 'The journal, and 1 note that mentions it'
          : `The journal, and ${n} notes that mention it`}
      </p>
      {mem?.held > 0 && (
        <p className="ph-sub ph-warn">
          {mem.held === 1 ? '1 change waits' : `${mem.held} changes wait`} for you on
          the <Link to="/memory">Memory page</Link>
        </p>
      )}
      {open && <MemoryPanel slug={slug} name={name} onClose={() => setOpen(false)} />}
    </SideRow>
  )
}

function MemoryPanel({ slug, name, onClose }) {
  const [mem, setMem] = useState(null)
  useEffect(() => {
    api(`/api/projects/${enc(slug)}/memory`).then(setMem).catch((err) => { notifyError(err); setMem(false) })
  }, [slug])
  const notes = mem?.notes || []
  return (
    <SidePanel open onClose={onClose} title={`Memory: ${name}`}
               tabs={[{ id: 'journal', label: 'Journal' }, { id: 'notes', label: `Notes (${notes.length})` }]}
               footer={<Link to="/memory">Open the Memory page</Link>}>
      {(t) => {
        if (!mem) return <p className="ph-sub" aria-busy={mem === null}>{mem === null ? 'Loading…' : 'Could not load memory.'}</p>
        if (t === 'journal') {
          return (
            <div className="ph-journal">
              <p className="ph-sub ph-sub-row">
                <span>project.md, loaded into every prompt</span>
                <HelpTip label="About the journal" align="right">
                  CNTRL keeps this as it works: the summary, status, issues and dated entries. Entries
                  from turns that read outside content are held back from the prompt until you clear
                  them.
                </HelpTip>
                <span className="ph-meta ph-push">{fmtTokens(mem.journal_tokens)} tokens</span>
              </p>
              {mem.journal ? <Md text={mem.journal} /> : <EmptyState>The journal is empty.</EmptyState>}
            </div>
          )
        }
        return notes.length === 0
          ? <EmptyState>No memory notes mention {name}.</EmptyState>
          : (
            <>
              <p className="ph-sub ph-sub-row">
                <span>Notes that mention {name}</span>
                <HelpTip label="About memory notes" align="right">
                  Memory notes are not kept per project. These are the notes whose name, description or
                  text mention this project. Approve or edit them on the Memory page.
                </HelpTip>
              </p>
              <ul className="list-divided ph-notes">
                {notes.map((x) => (
                  <li key={x.name} className="ph-note-row">
                    <span className="ph-note-name">{x.name}</span>
                    {x.pending && <Tag tone="pending">waiting for approval</Tag>}
                    {x.description && <span className="ph-sub">{x.description}</span>}
                  </li>
                ))}
              </ul>
            </>
          )
      }}
    </SidePanel>
  )
}

// Scheduled: schedules that run in this project (/api/schedules, project_slug).
function ScheduledRow({ slug, name, list, onChanged }) {
  const [adding, setAdding] = useState(false)
  return (
    <SideRow icon="schedules" label="Scheduled" action="Add" actionLabel="Add a schedule"
             onAction={() => setAdding(true)}>
      {list.length > 0
        ? (
          <ul className="list-divided ph-scheds">
            {list.map((s) => <ScheduleRow key={s.id} s={s} onChanged={onChanged} />)}
          </ul>
        )
        : <p className="ph-sub ph-faint">Nothing scheduled</p>}
      {adding && createPortal(
        <ScheduleForm slug={slug} name={name} onClose={() => setAdding(false)}
                      onCreated={() => { setAdding(false); onChanged() }} />,
        document.body)}
    </SideRow>
  )
}

function ScheduleRow({ s, onChanged }) {
  const navigate = useNavigate()
  const ask = useAsk()
  async function act(fn) {
    try { await fn() } catch (err) { notifyError(err) }
    onChanged()
  }
  const toggle = () => act(() => api(`/api/schedules/${s.id}?enabled=${!s.enabled}`, { method: 'PATCH' }))
  async function remove() {
    if (!await ask.confirm(`Move "${s.name}" to recently deleted?`, { confirmLabel: 'Move to bin' })) return
    act(() => api(`/api/schedules/${s.id}`, { method: 'DELETE' }))
  }
  const { rowProps, menu, open } = useRowMenu([
    { label: s.enabled ? 'Pause' : (s.pending_approval ? 'Approve' : 'Resume'), onSelect: toggle },
    { label: 'Edit on the Schedules page', onSelect: () => navigate('/schedules') },
    '-',
    { label: 'Delete', danger: true, onSelect: remove },
  ], { label: 'Schedule actions', buttonClassName: 'win-btn ph-more' })
  return (
    <li {...rowProps} className={`ph-sched${open ? ' menu-open' : ''}`}>
      <span className="ph-sched-main">
        <span className="ph-sched-name ellipsis">{s.name}</span>
        <span className="ph-sub">{cadenceLabel(s)}</span>
      </span>
      {!s.enabled && (
        <Tag tone={s.pending_approval ? 'pending' : undefined}>
          {s.pending_approval ? 'waiting for you' : 'paused'}</Tag>
      )}
      {menu}
    </li>
  )
}

const CADENCES = [
  { value: 'daily', label: 'Every day' },
  { value: 'interval', label: 'Every few minutes or hours' },
]

function ScheduleForm({ slug, name, onClose, onCreated }) {
  const [f, setF] = useState({ name: '', task: '', cadence_kind: 'daily', daily_at: '09:00',
                               interval_minutes: 360 })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const set = (p) => setF((x) => ({ ...x, ...p }))
  async function create() {
    setBusy(true)
    setError(null)
    try {
      await api('/api/schedules', {
        method: 'POST',
        body: JSON.stringify({
          name: f.name.trim(), kind: 'jarvis', project_slug: slug, task: f.task.trim(),
          cadence_kind: f.cadence_kind, daily_at: f.daily_at || '09:00',
          interval_minutes: Math.max(15, Number(f.interval_minutes) || 360),
        }),
      })
      notify(`Scheduled "${f.name.trim()}"`)
      onCreated()
    } catch (err) {
      setError(err.detail || err.message)
      setBusy(false)
    }
  }
  return (
    <Modal title={`New schedule in ${name}`} onClose={onClose} onSubmit={create} submitLabel="Create"
           submitDisabled={!f.name.trim() || !f.task.trim()} busy={busy} closeOnScrim={false}
           help="CNTRL runs the task on its own, in this project, at the times you set, by the server's clock. It starts running once created; pause it from its menu."
           footerStart={error ? <span className="ph-error" role="alert">{error}</span> : null}>
      <Input label="Name" value={f.name} autoFocus placeholder="Nightly summary"
             onChange={(e) => set({ name: e.target.value })} />
      <Input label="Task" textarea rows={4} value={f.task}
             placeholder="What CNTRL should do each time"
             onChange={(e) => set({ task: e.target.value })} />
      <Select label="Repeats" value={f.cadence_kind} options={CADENCES}
              onChange={(e) => set({ cadence_kind: e.target.value })} />
      {f.cadence_kind === 'daily'
        ? <Input label="At" type="time" value={f.daily_at}
                 onChange={(e) => set({ daily_at: e.target.value })} />
        : <Input label="Every (minutes, at least 15)" type="number" min={15} step={15}
                 value={f.interval_minutes}
                 onChange={(e) => set({ interval_minutes: e.target.value })} />}
    </Modal>
  )
}

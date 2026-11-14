import {
  Fragment, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState,
} from 'react'
import { api, chatStream, tailStream } from '../api.js'
import { NavSlotContext } from '../nav.jsx'
import { onProjectsChanged } from '../projectsChanged.js'
import { isPhone, useIsPhone } from '../breakpoints.js'
import { MessageBody } from '../ToolActivity.jsx'
import { activityMark, makeTurnFolder, newTurn, seedParts } from '../turnEvents.js'
import { useDockHeight, useFollow } from '../useFollow.js'
import TurnStatus from '../TurnStatus.jsx'
import ResumeBar from '../ResumeBar.jsx'
import { RESUME_TEXT, resumable, resumeUrl } from '../resume.js'
import { useAsk } from '../ask.jsx'
import ModelPicker from '../ModelPicker.jsx'
import { AskPanel, useOperatorAsks, usePermissionMode } from '../AskUser.jsx'
import { wireMode } from '../askUser.js'
import ChatGroups from '../ChatGroups.jsx'
import { useSlash } from '../slash/useSlash.jsx'
import { subscribe } from '../events.js'
import { useApprovals, placeApprovals } from '../shell/approvals.js'
import ApprovalRow from '../shell/ApprovalRow.jsx'
import ProjectPanel from '../panels/project/ProjectPanel.jsx'
import { SendGlyph, UiGlyph } from '../shell/glyphs.jsx'
import { MODE_HINT } from '../askUser.js'
import { MODE_SHORT } from '../AskUser.jsx'
import './work.css'

// A new chat's empty state: what this chat is scoped to, how it will ask,
// and a few first messages that fill the composer. Plain words, no greeting.
const STARTERS = {
  project: [
    'What does this project do, and what is unfinished?',
    'What changed in the last few commits?',
    'Plan the next step before writing anything',
  ],
  none: [
    'What can you do here, and what needs my approval?',
    'Help me start a new project',
    'Write a note I can keep in this chat',
  ],
}

function ChatHome({ temporary, projectName, mode, onStart, children }) {
  const starters = projectName ? STARTERS.project : STARTERS.none
  return (
    <div className="chat-home">
      <h1>{temporary ? 'Temporary chat' : projectName ? projectName : 'New chat'}</h1>
      <p className="chat-home-lede">
        {temporary
          ? 'Nothing from this chat is written to disk. It is gone once you leave it.'
          : projectName
            ? `Files this chat writes go to ${projectName}. Commits, pushes and new network hosts wait for your approval.`
            : 'Not tied to a project. Files this chat writes stay with the chat; pick a project to work in one.'}
      </p>
      <dl className="chat-home-facts">
        <dt>Mode</dt>
        <dd><b>{MODE_SHORT[mode] || mode}</b> <span className="dim">{MODE_HINT[mode]}</span></dd>
      </dl>
      {children}
      <div className="chat-home-h">Start with</div>
      <ul className="chat-home-starters">
        {starters.map((s) => (
          <li key={s}>
            <button type="button" onClick={() => onStart(s)}>{s}</button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// Work (work/Work.jsx) hosts this page and adds its windows beside it. Every
// prop is optional, so Chat on its own renders exactly as it always has:
//   toolbarExtra     rendered at the end of the chat toolbar (Work's +)
//   beside           rendered after <main>, inside .chat-layout (the windows)
//   onProjectChange  called with the slug this chat's work goes to, or null
//   controlRef       filled with { pickProject(slug), openProject(slug) }
const NEEDS_POLL_MS = 15000

export default function Chat({
  toolbarExtra = null, beside = null, onProjectChange, controlRef,
} = {}) {
  const [conversations, setConversations] = useState([])
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [active, setActive] = useState(null)
  const [projects, setProjects] = useState([])
  const [multiline, setMultiline] = useState(false)   // composer past one line
  const ask = useAsk()
  // project chosen for a chat that doesn't exist yet; sent with the first turn
  const [pendingProject, setPendingProject] = useState('')
  const [pendingMode, setPendingMode] = useState('follow')
  // a temporary chat persists nothing: no conversation row, no transcript.
  // Doesn't repaint the GUI — the toolbar switch and the composer placeholder
  // carry it.
  const [temporary, setTemporary] = useState(false)
  // the multi-agent jobs this chat launched (/messages `jobs`)
  const [chatJobs, setChatJobs] = useState([])
  // the project side panel (panels/project): null, or the tab it is open at
  const [panel, setPanel] = useState(null)
  // bumped when something may have joined the approval queue (see below)
  const [needsPoke, setNeedsPoke] = useState(0)
  const asks = useOperatorAsks(conversationId, busy)   // ask_user / permission asks
  const [permMode, setPermMode] = usePermissionMode(conversationId)
  // on a phone the list is an overlay, so it starts closed unless the operator
  // has explicitly opened it before; on desktop it stays open by default
  const [sideOpen, setSideOpen] = useState(() => {
    const saved = localStorage.getItem('jarvis.chat.side')
    if (saved) return saved !== 'closed'
    return !isPhone()
  })
  // Collapsed on a desktop, the sidebar stops being a chat list and becomes the
  // app's nav rail: it publishes a mount point and App portals the destinations
  // into it. On a phone the sidebar is off-canvas, so it can't hold the nav —
  // no slot there, and the top bar stays.
  const phone = useIsPhone()
  const setNavSlot = useContext(NavSlotContext)
  const slotRef = useCallback((el) => setNavSlot(el), [setNavSlot])
  // Hand the slot back when leaving Chat, or the bar never comes home. This is
  // a layout effect on purpose: as a passive one the release landed after the
  // next route had already painted, so there was a frame with the nav portaled
  // into a node that was no longer in the document.
  useLayoutEffect(() => () => setNavSlot(null), [setNavSlot])

  const scrollRef = useRef(null)   // the .messages scroll container
  const inputRef = useRef(null)
  const tailAbort = useRef(null)   // cancels a resume-tail when switching chats
  const liveId = useRef(null)      // id of the turn in flight
  // Which chat this view is showing, as a counter: opening or leaving a chat
  // bumps it, and a stream that started under an earlier value has been
  // superseded. Without it a turn still streaming from chat A wrote its tokens,
  // its final reply and its busy flag into whichever chat had been opened since.
  const gen = useRef(0)
  const dockRef = useRef(null)     // the composer's dock: status, asks, the bar
  const folder = useRef(null)
  if (!folder.current) folder.current = makeTurnFolder((fn) => setMessages(fn))
  // Read during render, before any effect: the persistence effect below fires
  // on mount with a null id and would clear the key before a restore effect
  // could read it.
  const resumeId = useRef(Number(localStorage.getItem('jarvis.chat.last')) || null)

  const refreshConvos = () =>
    api('/api/conversations').then((r) => setConversations(r.conversations))

  useEffect(() => {
    api('/api/conversations').then((r) => {
      setConversations(r.conversations)
      // Resume only a chat that is still in the list. The messages endpoint
      // answers for any id — a deleted one comes back as an empty transcript,
      // which looks exactly like a new chat but is bound to a conversation the
      // backend 404s on the first send.
      const id = resumeId.current
      // A resume is not a pick: the phone sheet may be open because the
      // drawer's "Chat history" row just asked for it, and closing it here
      // shut the list the operator had opened a frame earlier.
      if (id && r.conversations.some((c) => c.id === id)) openConversation(id, { resume: true })
      else localStorage.removeItem('jarvis.chat.last')
    }).catch(() => {})
    api('/api/projects').then((r) => { setActive(r.active); setProjects(r.projects) })
    // the Projects sheet made a change: the sidebar's Projects list is this copy
    const offProjects = onProjectsChanged(() => {
      api('/api/projects').then((r) => { setActive(r.active); setProjects(r.projects) })
        .catch(() => {})
    })
    return () => { offProjects(); tailAbort.current?.abort() }
  }, [])

  // "Chat" in the nav is a destination, not a reset. It used to mount a blank
  // conversation every time, so coming back from Projects silently dropped the
  // chat you were in — and it did the same job as ＋, which is the control that
  // actually means "new". Resuming makes the two distinct: ＋ is now the only
  // way to start one.
  useEffect(() => {
    if (conversationId) localStorage.setItem('jarvis.chat.last', String(conversationId))
    else localStorage.removeItem('jarvis.chat.last')
  }, [conversationId])


  useEffect(() => {
    localStorage.setItem('jarvis.chat.side', sideOpen ? 'open' : 'closed')
  }, [sideOpen])

  // the phone drawer's "Chat history" row (App.jsx) when Chat is already open
  useEffect(() => {
    const open = () => setSideOpen(true)
    window.addEventListener('jarvis-open-chats', open)
    return () => window.removeEventListener('jarvis-open-chats', open)
  }, [])

  // Follow the stream to the bottom unless the reader has scrolled up (then a
  // "N new" pill brings them back). Only the message list scrolls, never the
  // page (scrollIntoView walks every scrollable ancestor).
  const follow = useFollow(scrollRef, { mark: activityMark(messages), resetKey: conversationId })
  const followRun = follow.follow
  useLayoutEffect(() => { followRun() }, [messages, followRun])
  useDockHeight(dockRef)

  // A security card's "Open chat at step" (/c/<id>?step=<n>, kept by routes.jsx):
  // once the transcript has rendered, scroll the message list to that step and
  // flash it. Direct scrollTop, like the follow logic: never the page.
  useEffect(() => {
    if (!conversationId || !messages.length) return
    let want = null
    try { want = sessionStorage.getItem('jarvis.chat.step') } catch { /* private mode */ }
    if (!want) return
    const [cid, step] = want.split(':')
    if (Number(cid) !== conversationId) return
    const el = document.getElementById(`step-${step}`)
    const box = scrollRef.current
    if (!el || !box) return          // not drawn yet: the next messages change tries again
    try { sessionStorage.removeItem('jarvis.chat.step') } catch { /* private mode */ }
    const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop
    box.scrollTop = Math.max(0, top - box.clientHeight / 3)
    el.classList.add('step-hit')
    setTimeout(() => el.classList.remove('step-hit'), 3600)
  }, [conversationId, messages])

  // the composer grows with the draft, up to the CSS max-height. Past one line
  // the pill relaxes into a rounded box — .multi is that threshold.
  function autoGrow() {
    const ta = inputRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${ta.scrollHeight}px`
    setMultiline(ta.scrollHeight > 48)
  }
  useEffect(autoGrow, [input])

  // a starting point from the empty state: into the composer, not sent
  function startWith(text) {
    setInput(text)
    inputRef.current?.focus({ preventScroll: true })
  }

  // One handler for both paths: the live POST stream and a resumed tail.
  // token/tool/tool_result/job fold into the streaming message's parts (text
  // tokens batched: turnEvents.makeTurnFolder); final swaps in the reply with
  // the activity collapsed above it; an error keeps the run and appends itself.
  // `mine` is the counter value the stream started under: once the view has
  // moved to another chat (gen bumped) the stream is stale and says nothing.
  function handleTurnEvent(ev, mine = gen.current) {
    if (mine !== gen.current) return
    slash.onEvent(ev)
    asks.onEvent(ev)
    if (ev.type === 'start') {
      liveId.current = ev.conversation_id
      // temporary: never adopt the id, or the chat becomes a saved one
      if (!temporary) {
        setConversationId(ev.conversation_id)
        adoptConversation(ev.conversation_id)
      }
    }
    folder.current.handle(ev)
  }

  // A chat this turn just created is not in the list until the list is
  // fetched, and until then everything that reads the chat's project from its
  // row saw a chat that follows the loaded project: the windows beside the chat
  // swapped to the WRONG project's board for the whole turn, and back when it
  // ended. Give the row what the send asked for now, then fetch the real one.
  function adoptConversation(id) {
    setConversations((cs) => (cs.some((c) => c.id === id) ? cs : [{
      id, summary: '', running: true,
      project_slug: pendingMode === 'pin' ? (pendingProject || null) : null,
      project_locked: pendingMode === 'follow' ? 0 : 1,
    }, ...cs]))
    refreshConvos()
  }

  // the phone list overlays the thread, so picking a chat should reveal it
  const closeSideOnPhone = () => {
    if (isPhone()) setSideOpen(false)
  }

  async function openConversation(id, { resume = false, attempt = 0 } = {}) {
    tailAbort.current?.abort()
    folder.current.cancel()
    const mine = ++gen.current
    // the id answers and stops go to: it stayed on the chat that last STARTED a
    // turn, so a question from the chat opened since was answered into the old one
    liveId.current = id
    setTemporary(false)   // saved chats always persist
    if (!resume) closeSideOnPhone()
    setConversationId(id)
    setChatJobs([])
    setBusy(false)        // an abandoned send or tail no longer speaks for this view
    const r = await api(`/api/conversations/${id}/messages`)
    // a slow transcript for a chat the reader already left must not land over
    // the one they went to
    if (mine !== gen.current) return
    setMessages(r.messages)
    setChatJobs(r.jobs || [])
    if (!r.running) return
    // a turn is still executing server-side — re-attach and watch it finish,
    // seeding the placeholder with the tool calls it already made
    setBusy(true)
    const seed = seedParts(r)
    setMessages((m) => [...m, { role: 'assistant', content: '', streaming: true,
                                parts: seed, t0: Date.now() }])
    const ctl = new AbortController()
    tailAbort.current = ctl
    let settled = false
    try {
      await tailStream(`/api/chat/${id}/stream`, (ev) => {
        if (ev.type === 'idle') {
          // turn ended between the messages fetch and the tail — reload
          settled = true
          api(`/api/conversations/${id}/messages`).then((r2) => {
            if (mine === gen.current) setMessages(r2.messages)
          })
          return
        }
        if (ev.type === 'final' || ev.type === 'error') settled = true
        handleTurnEvent(ev, mine)
      }, ctl.signal)
    } catch { /* tail aborted or dropped */ }
    if (mine !== gen.current) return
    folder.current.flush()
    if (!settled && attempt < 3) {
      // the stream ended without an ending, so the turn may well still be
      // going (a dropped connection): look again rather than leave a spinner
      // that never resolves
      await new Promise((ok) => { setTimeout(ok, 800 * (attempt + 1)) })
      if (mine === gen.current) openConversation(id, { resume: true, attempt: attempt + 1 })
      return
    }
    if (!settled) {
      // out of patience: show what is saved instead of a placeholder that
      // would spin forever
      try {
        const r2 = await api(`/api/conversations/${id}/messages`)
        if (mine === gen.current) setMessages(r2.messages)
      } catch { /* offline: the placeholder stays until the next open */ }
    }
    setBusy(false)
    refreshConvos()
  }

  function newConversation() {
    tailAbort.current?.abort()
    folder.current.cancel()
    gen.current += 1
    liveId.current = null
    setBusy(false)
    closeSideOnPhone()
    setConversationId(null)
    setMessages([])
    setChatJobs([])
    setPendingProject('')
    setPendingMode('follow')
    setTemporary(false)
  }

  async function renameConversation(id, current) {
    const next = await ask.prompt('Rename this chat', current || '',
                                  { confirmLabel: 'Rename' })
    if (next === null) return
    if (!next.trim()) return
    await api(`/api/conversations/${id}`, {
      method: 'PATCH', body: JSON.stringify({ title: next.trim() }) })
    refreshConvos()
  }

  async function deleteConversation(id) {
    // unlike projects, agents and schedules, a chat has no bin: it is gone
    if (!await ask.confirm(`Permanently delete chat #${id}?`,
                           { body: 'Its messages and tool history are removed. This cannot be undone.',
                             confirmLabel: 'Delete forever', danger: true })) return
    await api(`/api/conversations/${id}`, { method: 'DELETE' })
    if (id === conversationId) newConversation()
    refreshConvos()
  }

  // the row the toolbar is describing, and the picker state it implies:
  // a slug means pinned, a locked row with no slug means deliberately none,
  // anything else is still following whatever project is loaded
  const openConvo = conversations.find((c) => c.id === conversationId)
  const convoMode = (c) =>
    c?.project_slug ? 'pin' : (c?.project_locked ? 'none' : 'follow')

  async function assignProject(mode, slug) {
    await api(`/api/conversations/${conversationId}`, {
      method: 'PATCH',
      body: JSON.stringify({ project: slug || null, mode }) })
    refreshConvos()
  }

  // The project this chat's work goes to right now: a saved chat's pin (or
  // the loaded project while it follows), a new chat's pending choice.
  const effectiveProject = conversationId
    ? (openConvo?.project_slug
       || (convoMode(openConvo) === 'follow' ? active : null) || null)
    : (pendingMode === 'pin' ? (pendingProject || null)
       : pendingMode === 'follow' ? (active || null) : null)
  useEffect(() => { onProjectChange?.(effectiveProject) },
            [effectiveProject, onProjectChange])
  if (controlRef) {
    controlRef.current = {
      // the pill's "pin to this project", from outside
      pickProject: (slug) => {
        if (conversationId) return assignProject('pin', slug)
        setPendingMode('pin')
        setPendingProject(slug)
      },
      // a /projects/:slug link: a new chat pinned to it, unless this chat is
      // already there (or still empty, when it just takes the pin)
      openProject: (slug) => {
        resumeId.current = null   // a cold load: don't let the resume win
        if (slug === effectiveProject) return
        if (conversationId || messages.length) newConversation()
        setPendingMode('pin')
        setPendingProject(slug)
      },
    }
  }

  async function stop() {
    // the turn ends server-side and every tail gets a final "[Request
    // interrupted]" event — the normal finish path settles the UI
    const id = conversationId ?? liveId.current
    if (!id) return
    try { await api(`/api/chat/${id}/stop`, { method: 'POST' }) } catch { /* already done */ }
    asks.settle()   // the turn's open question is void now; do not leave a dead card
  }

  // `url` sends somewhere other than /api/chat: the resume endpoint, which
  // refuses (409) unless the chat's last turn really died
  async function send(resend = null, url = undefined) {
    const text = (resend ?? input).trim()
    if (!text || busy) return
    const mine = gen.current
    let started = false   // the turn began (a `start` arrived)
    let settled = false   // it ended (a `final` or `error` arrived)
    setBusy(true)
    // clear the bar NOW — the message visibly left; it comes back on failure
    if (!resend) setInput('')
    setMessages((m) => [...m, ...newTurn(text)])
    follow.jump()   // sending is a request to see the answer
    try {
      await chatStream(
        { message: text, conversation_id: conversationId,
          ephemeral: temporary,
          // only meaningful when the conversation is being created by this turn
          ...(conversationId ? {} : { project: pendingProject || null,
                                      project_mode: pendingMode, ...slash.newChatFields,
                                      permission_mode: wireMode(permMode) }) },
        (ev) => {
          if (ev.type === 'start') started = true
          if (ev.type === 'final' || ev.type === 'error') settled = true
          handleTurnEvent(ev, mine)
        },
        url,
      )
    } catch (err) {
      if (mine !== gen.current) return   // the reader moved on; the turn runs regardless
      folder.current.flush()
      if (started && !err.status) {
        // the connection dropped mid-turn: the turn is still running server-side
        settled = false
      } else {
        // refused before anything streamed: drop the two optimistic messages
        setMessages((m) => m.slice(0, -2))
        if (!url) setInput(text)   // a resume has no draft to give back
        setMessages((m) => [...m, { role: 'error',
          content: err.status === 409 && err.detail === 'turn_in_progress'
            ? 'a turn is still running in this chat — wait for it to finish'
            : (err.detail || String(err)) }])
        setBusy(false)
        return
      }
    }
    if (mine !== gen.current) { refreshConvos(); return }
    folder.current.flush()
    if (!settled) {
      // The stream ended without an ending (a dropped connection, a restart).
      // Rather than leave a placeholder spinning, pick the turn back up by
      // re-attaching; a temporary chat has nothing saved to re-attach to.
      const id = conversationId ?? liveId.current
      if (id && !temporary) { openConversation(id, { resume: true }); return }
      folder.current.handle({ type: 'error', message: 'the connection dropped mid-turn' })
    }
    setBusy(false)
    api('/api/conversations').then((r) => setConversations(r.conversations))
    // a turn may have launched research, a team or a plan run: refresh the
    // Runs view's "this chat" list (a temporary chat has nothing to fetch)
    const done = conversationId ?? liveId.current
    if (done && !temporary) {
      api(`/api/conversations/${done}/messages`)
        .then((r) => setChatJobs(r.jobs || [])).catch(() => {})
    }
  }

  // the last turn died and the server saved it: send it on from its last step
  function resume() {
    if (conversationId && !busy && !temporary)
      send(RESUME_TEXT, resumeUrl(conversationId))
  }

  // Swipe in from the left edge to open the chat list on a phone — the quick
  // path; the visible one is the drawer's "Chat history" row. The
  // start must be at the screen's edge so horizontal scrolls inside code
  // blocks and tables never trigger it.
  const edgeTouch = useRef(null)
  const onEdgeTouchStart = (e) => {
    if (!phone || sideOpen) { edgeTouch.current = null; return }
    const t = e.touches[0]
    edgeTouch.current = t.clientX <= 28 ? { x: t.clientX, y: t.clientY } : null
  }
  const onEdgeTouchMove = (e) => {
    const s = edgeTouch.current
    if (!s) return
    const t = e.touches[0]
    if (t.clientX - s.x > 42 && Math.abs(t.clientY - s.y) < 40) {
      setSideOpen(true)
      edgeTouch.current = null
    } else if (Math.abs(t.clientY - s.y) >= 40) edgeTouch.current = null
  }

  // a brand-new chat: no saved conversation, nothing sent yet. On a phone the
  // toolbar disappears and its controls move under the orb instead.
  const fresh = !conversationId && messages.length === 0
  // The project scope this chat's work goes to, as the panel edits it: a saved
  // chat's row, or a new chat's pending choice.
  const scopeMode = conversationId ? convoMode(openConvo) : pendingMode
  const scopeValue = conversationId ? (openConvo?.project_slug || '') : pendingProject
  const pickScope = (mode, slug) => {
    if (conversationId) return assignProject(mode, slug)
    setPendingMode(mode)
    setPendingProject(slug || '')
  }
  const projectName = (slug) => projects.find((p) => p.slug === slug)?.name || slug
  const scopeLabel = scopeMode === 'pin' ? projectName(scopeValue)
    : scopeMode === 'none' ? 'No project'
    : (active ? projectName(active) : 'No project')

  // What is waiting on the operator in this chat's scope, inline in the
  // transcript as /shell shows it (shell/approvals.js). The scope is the
  // project this chat's work goes to, else a saved chat's own `chat-<id>`
  // store. The queue reloads on open, every few seconds while a turn runs,
  // and on `poke`: an egress/notices event on the ONE shared stream
  // (events.js), plus a slow poll while the tab is visible for git and plan
  // requests, which have no topic of their own. Never a new EventSource.
  const approvalScope = effectiveProject
    || (conversationId && !temporary ? `chat-${conversationId}` : null)
  useEffect(() => {
    if (!approvalScope) return undefined
    const bump = () => setNeedsPoke((n) => n + 1)
    const offs = [subscribe('egress', bump), subscribe('notices', bump)]
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') bump()
    }, NEEDS_POLL_MS)
    return () => { offs.forEach((off) => off()); clearInterval(t) }
  }, [approvalScope])
  const approvals = useApprovals({
    scope: approvalScope, isProject: !!effectiveProject, busy, poke: needsPoke,
  })
  const { after: approvalsAfter, tail: approvalsTail } =
    placeApprovals(messages, approvals.items)
  const approvalRows = (list) => list.map((a) => (
    <ApprovalRow key={a.key} a={a} acting={approvals.acting} onDecide={approvals.decide} />
  ))
  // Runs is offered wherever there can be runs: a saved chat, or a project
  const runsOffered = !!(conversationId || effectiveProject) && !temporary
  const runningJobs = chatJobs.filter((j) => j.running).length
  // the panel's store: the project, else a saved chat's own `chat-<id>` files
  const panelSlug = effectiveProject || (conversationId && !temporary ? `chat-${conversationId}` : null)

  // The bar's controls: plain buttons. Each names what it is and its value,
  // and opens the project panel at the tab that changes it (Project: Files,
  // Mode: Security, Runs: Runs). Temporary is the one toggle, pressed or not.
  const barControls = (
    <>
      <button type="button" className={`pp-bar-btn${scopeMode === 'pin' ? ' pinned' : ''}`}
              aria-haspopup="dialog" aria-expanded={panel === 'files'}
              onClick={() => setPanel(panel === 'files' ? null : 'files')}>
        <span className="pp-k">Project</span>
        <span className="pp-v ellipsis">{scopeLabel}</span>
      </button>
      {!conversationId && (
        <button type="button" className={`pp-bar-btn pp-toggle${temporary ? ' on' : ''}`}
                aria-pressed={temporary} onClick={() => setTemporary((t) => !t)}>
          {temporary && <UiGlyph name="check" size={14} />}
          <span className="pp-v">Temporary chat</span>
        </button>
      )}
      <button type="button" className={`pp-bar-btn mode-${permMode}`}
              aria-haspopup="dialog" aria-expanded={panel === 'security'}
              onClick={() => setPanel(panel === 'security' ? null : 'security')}>
        <span className="pp-k">Mode</span>
        <span className="pp-v">{MODE_SHORT[permMode] || permMode}</span>
      </button>
      {runsOffered && (
        <button type="button" className="pp-bar-btn"
                aria-haspopup="dialog" aria-expanded={panel === 'runs'}
                onClick={() => setPanel(panel === 'runs' ? null : 'runs')}>
          <span className="pp-v">Runs</span>
          {runningJobs > 0 && <span className="chat-runs-count">{runningJobs}</span>}
        </button>
      )}
    </>
  )

  const slash = useSlash({
    input, setInput, busy, conversationId, turnId: () => conversationId ?? liveId.current,
    conversations, projects, active, pendingProject, messages, setMessages,
    temporary, setTemporary, send: (t) => send(t), newChat: newConversation,
    openChat: openConversation, openList: () => setSideOpen(true), stop,
    pickProject: (mode, slug) => (conversationId ? assignProject(mode, slug)
      : (setPendingMode(mode), setPendingProject(slug || ''))),
    rename: () => renameConversation(conversationId, openConvo?.summary),
    deleteChat: () => deleteConversation(conversationId), refresh: refreshConvos,
    refreshProjects: () => api('/api/projects').then((r) => { setActive(r.active); setProjects(r.projects) }),
  })

  return (
    <div className="chat-layout"
         onTouchStart={onEdgeTouchStart} onTouchMove={onEdgeTouchMove}>
      <aside className={sideOpen ? '' : 'collapsed'}>
        <div className="side-head">
          <button type="button" className="icon-btn"
                  title={sideOpen ? 'collapse sidebar' : 'expand sidebar'}
                  aria-label={sideOpen ? 'collapse sidebar' : 'expand sidebar'}
                  onClick={() => setSideOpen((o) => !o)}>
            <UiGlyph name={sideOpen ? 'left' : 'right'} /></button>
          <span className="side-title">Chats</span>
          <button type="button" className="icon-btn" title="new chat" aria-label="new chat"
                  onClick={newConversation}><UiGlyph name="plus" /></button>
        </div>
        {!sideOpen && !phone && <div className="side-nav" ref={slotRef} />}
        <ChatGroups conversations={conversations} activeId={conversationId}
                    projects={projects} onOpen={openConversation}
                    onRename={(c) => renameConversation(c.id, c.summary)}
                    onDelete={(c) => deleteConversation(c.id)}
                    onChanged={refreshConvos} />
      </aside>
      {sideOpen && (
        <div className="chat-scrim" onClick={() => setSideOpen(false)} />
      )}
      <main>
        {/* the one home for the project state — except a fresh chat on a
            phone, where the controls sit in the empty state and the bar
            itself would just be an empty strip */}
        {(!phone || !fresh) && (
          <div className="chat-toolbar">
            {conversationId
              ? <span className="chat-title ellipsis">
                  {openConvo?.summary || `Chat #${conversationId}`}</span>
              : <span className="grow" />}
            {/* the number and the project travel as one compact group, so the
                title is what gives way on a narrow screen */}
            <div className="chat-meta">
              {conversationId && <span className="tag">#{conversationId}</span>}
              {barControls}
              {toolbarExtra}
            </div>
          </div>
        )}
        <div className="messages" ref={scrollRef}>
          {messages.length === 0 ? (
            <div className="chat-empty">
              <ChatHome temporary={temporary} mode={permMode} onStart={startWith}
                        projectName={effectiveProject
                          ? (projects.find((p) => p.slug === effectiveProject)?.name || effectiveProject)
                          : null}>
                {phone && fresh && (
                  <div className="empty-controls">{barControls}</div>
                )}
                {approvalsTail.length > 0 && (
                  <div className="chat-approvals">{approvalRows(approvalsTail)}</div>
                )}
              </ChatHome>
            </div>
          ) : (
            <div className="thread">
              {messages.map((m, i) => (
                <div key={i} className={`msg ${m.role}${m.midturn ? ` midturn ${m.midturn}` : ''}`}>
                  {m.role === 'assistant' ? <MessageBody m={m} />
                    : <pre>{m.content || (m.streaming ? '…' : '')}</pre>}
                </div>
              )).map((row, i) => (approvalsAfter.has(i)
                ? <Fragment key={i}>{row}<div className="chat-approvals">
                    {approvalRows(approvalsAfter.get(i))}</div></Fragment>
                : row))}
              {conversationId && !busy && !temporary && resumable(messages) && (
                <ResumeBar onResume={resume} />
              )}
              {approvalsTail.length > 0 && (
                <div className="chat-approvals">{approvalRows(approvalsTail)}</div>
              )}
            </div>
          )}
        </div>
        {/* The dock: the composer floats over the bottom of the thread, and
            what has to be seen while a turn runs rides with it: the steady
            status line, an open question, the way back to the newest.
            (Between the list and the composer, an open question sat UNDER the
            composer's overlay, its buttons out of reach.) */}
        <form className="composer" ref={dockRef}
              onSubmit={(e) => { e.preventDefault(); slash.submit() || send() }}>
          {slash.popup}
          {!follow.pinned && (follow.away > 0 || busy) && messages.length > 0 && (
            <button type="button" className="follow-pill" onClick={follow.jump}>
              {follow.away > 0 ? `${follow.away} new` : 'latest'} <span aria-hidden="true">↓</span>
            </button>
          )}
          <TurnStatus busy={busy} messages={messages} cid={liveId.current ?? conversationId}
                      waiting={asks.asks.length > 0} />
          <AskPanel asks={asks} cid={liveId.current ?? conversationId} />
          <div className={`composer-inner${multiline ? ' multi' : ''}`}>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (slash.onKeyDown(e)) return
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
              }}
              // a phone's bar is ~230px of text: the long form wrapped onto a
              // second line the one-row box then clipped
              placeholder={temporary
                ? (phone ? 'Temporary message' : 'Message CNTRL (temporary chat)')
                : 'Message CNTRL'}
              rows={1}
            />
            <ModelPicker chip="model-chip" wrap="composer-model" />
            {busy
              ? <button type="button" className="send-btn stop" title="stop this turn"
                        aria-label="stop" onClick={stop}><SendGlyph stop /></button>
              : <button type="submit" className="send-btn" title="send"
                        aria-label="send" disabled={!input.trim()}><SendGlyph /></button>}
          </div>
        </form>
      </main>
      {beside}
      <ProjectPanel open={panel !== null} tab={panel || 'files'} onTab={setPanel}
                    onClose={() => setPanel(null)}
                    cid={conversationId} temporary={temporary}
                    slug={panelSlug} isProject={!!effectiveProject}
                    projects={projects} loaded={active}
                    scope={{ mode: scopeMode, value: scopeValue, onPick: pickScope }}
                    permMode={permMode} onPermMode={setPermMode}
                    runs={runsOffered} chatJobs={chatJobs} />
    </div>
  )
}

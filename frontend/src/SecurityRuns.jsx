import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api.js'
import { useAsk } from './ask.jsx'
import { ts } from './format.js'
import { notify, notifyError } from './notify.js'
import { FAULTS_LEDE, baselineAsk, faultText } from './securityCopy.js'
import {
  MUTE_MODES, UNTRUSTED, cardTitle, chatHref, clock, countsParts, eventActions, eventState,
  kindsToMute, lineTitle, muteAsk, programOf, resolveAsk, runState, stopRunAsk, subjectsText,
} from './securityRuns.js'
import { Button } from './components/index.js'
import { Group, State, Ticks } from './securityParts.jsx'

// The Security Queue, one card per RUN (backend/secruns.py, GET /api/security/runs).
// A card is a run's whole story: what waits on you, how many rows rules filed as
// normal work, the agent's own reports, and for each kind the step the agent was
// at, in its words, with the buttons that deal with it.
//
// "What the agent was doing" is the agent's own text. It is UNTRUSTED and always
// labelled so; every string here is a plain text node, nothing goes through <Md>.

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) })

export default function RunCards({ runs, onChanged, onOpenBoard, slug }) {
  return (
    <div className="sq-queue">
      {runs.map((r) => (
        <RunCard key={r.key} run={r} onChanged={onChanged} onOpenBoard={onOpenBoard}
                 inProject={!!slug} />
      ))}
    </div>
  )
}

function RunCard({ run, onChanged, onOpenBoard, inProject }) {
  const ask = useAsk()
  const [busy, setBusy] = useState(false)
  const [filtered, setFiltered] = useState(null)     // the rows rules filed, once shown
  const [showFiltered, setShowFiltered] = useState(false)
  // the card carries each kind's newest event; "show more" asks for all of them
  const [full, setFull] = useState(null)
  const state = runState(run)
  const events = run.events || []
  const key = encodeURIComponent(run.key)

  async function guard(fn) {
    setBusy(true)
    try { await fn() } catch (e) { notifyError(e) }
    setBusy(false)
  }
  const changed = () => {
    window.dispatchEvent(new Event('jarvis-files-changed'))
    onChanged()
  }

  async function loadAll() {
    try { setFull((await api(`/api/security/runs/${key}`)).events || []) }
    catch { setFull(events) }
  }
  // the card changed while its events were open: read them again
  useEffect(() => { if (full !== null) loadAll() }, [run.newest_id, run.counts.need]) // eslint-disable-line

  async function toggleFiltered() {
    const next = !showFiltered
    setShowFiltered(next)
    if (next && filtered === null) {
      try { setFiltered((await api(`/api/security/runs/${key}`)).filtered || []) }
      catch { setFiltered([]) }
    }
  }

  const stopRun = () => guard(async () => {
    const first = events[0]
    if (!first) return
    const dry = await post(`/api/security/events/${first.id}/stop`, { scope: 'run', dry_run: true })
    if (!dry.agents?.length && !dry.plans?.length) { notify(dry.message || 'Nothing is running.'); return }
    const q = stopRunAsk(dry, run.title)
    if (!await ask.confirm(q.title, { body: q.body, confirmLabel: q.confirm, danger: true })) return
    const r = await post(`/api/security/events/${first.id}/stop`, { scope: 'run', confirm: true })
    notify(r.message || 'Stopped.', { life: 8 })
    changed()
  })

  const resolve = () => guard(async () => {
    if (run.counts.need > 0) {
      const q = resolveAsk(run)
      if (!await ask.confirm(q.title, { body: q.body, confirmLabel: q.confirm })) return
    }
    await post(`/api/security/runs/${key}/ack`)
    changed()
  })

  const muteAll = () => guard(async () => {
    const kinds = kindsToMute(run)
    if (!kinds.length) return
    const q = muteAsk(kinds)
    if (!await ask.confirm(q.title, { body: q.body, confirmLabel: q.confirm })) return
    await api('/api/notifications/settings', {
      method: 'PUT',
      body: JSON.stringify({ kinds: Object.fromEntries(kinds.map((k) => [k, 'record'])) }) })
    notify(`${kinds.join(', ')}: record only from now on.`, { life: 8 })
    changed()
  })

  const lines = [...(run.kinds || []), ...(run.report_kinds || [])]
  const counts = countsParts(run.counts)
  const many = (run.counts?.need || 0) + (run.counts?.reports || 0) > 1 || (run.counts?.filtered || 0) > 0
  return (
    <Group className="sq-run">
      <header className="sq-run-head">
        <div className="sq-meta">
          <b>{run.project || 'No project'}</b>
          {state && <>, {state}</>}
          {run.last_at && <span title={`last ${ts(run.last_at)} UTC`}>, last at {clock(run.last_at)}</span>}
        </div>
        <div className="sq-title">{cardTitle(run)}</div>
        {many && counts.length > 0 && (
          <div className="sq-run-counts">
            {counts.map((p, i) => (
              <span key={p.key}>
                {i > 0 && ', '}
                {p.text}
                {p.show && (
                  <> (<button type="button" className="sq-fold" onClick={toggleFiltered}
                              aria-expanded={showFiltered}>
                    {showFiltered ? 'hide' : 'show'}</button>)</>)}
              </span>
            ))}
          </div>)}
      </header>
      {showFiltered && <FilteredRows rows={filtered} onOpen={onOpenBoard} />}
      {lines.map((line) => (
        <KindLine key={line.kind} line={line} run={run}
                  events={(full || events).filter((e) => e.kind === line.kind)}
                  onMore={() => { if (full === null) loadAll() }}
                  busy={busy} guard={guard} changed={changed} onOpenBoard={onOpenBoard}
                  inProject={inProject} />
      ))}
      {(many || run.running === true) && <footer className="sq-run-foot">
        {run.running === true && (
          <Button variant="ghost" danger disabled={busy} onClick={stopRun}
                  title="stop every agent in this run (asks first)">Stop whole run</Button>)}
        {kindsToMute(run).length > 1 && (
          <Button variant="ghost" disabled={busy} onClick={muteAll}
                  title="file these kinds in the History only">Mute these kinds</Button>)}
        <span className="sq-spacer" />
        <Button variant="ghost" disabled={busy} onClick={resolve}
                title="mark everything waiting in this run as seen">Resolve group</Button>
      </footer>}
    </Group>
  )
}

// The rows rules filed as normal work: one line each, with the reason
function FilteredRows({ rows, onOpen }) {
  if (rows === null) return <p className="sq-run-filtered">Loading…</p>
  if (!rows.length) return <p className="sq-run-filtered">Nothing recorded.</p>
  return (
    <ul className="sq-run-filtered">
      {rows.map((r) => (
        <li key={r.id}>
          <button type="button" className="sq-fold" onClick={() => onOpen(r)}
                  title="open the evidence">{r.summary}</button>
          {r.count > 1 && <> ({r.count} times)</>}
          {r.rule && <>: {r.rule}</>}
        </li>
      ))}
    </ul>
  )
}

// One kind in a card: its newest event in full, the rest behind "show N more"
function KindLine({ line, run, events, onMore, busy, guard, changed, onOpenBoard, inProject }) {
  const [more, setMore] = useState(false)
  if (!events.length) return null
  const [first, ...rest] = events
  const toggle = () => { setMore(!more); if (!more && line.n > events.length) onMore() }
  return (
    <>
      <EventBlock ev={first} line={line} run={run} busy={busy} guard={guard} changed={changed}
                  onOpenBoard={onOpenBoard} head inProject={inProject} />
      {more && rest.map((ev) => (
        <EventBlock key={ev.id} ev={ev} line={line} run={run} busy={busy} guard={guard}
                    changed={changed} onOpenBoard={onOpenBoard} inProject={inProject} />))}
      {more && line.n > events.length && <p className="sq-ev-more sq-change">Loading…</p>}
      {line.n > 1 && (
        <div className="sq-ev-more">
          <button type="button" className="sq-fold" onClick={toggle} aria-expanded={more}>
            {more ? 'Show fewer' : `Show ${line.n - 1} more of this kind`}</button>
        </div>)}
    </>
  )
}

function EventBlock({ ev, line, run, busy, guard, changed, onOpenBoard, head }) {
  const ask = useAsk()
  const st = eventState(ev)
  const cid = ev.doing?.conversation?.id || ev.conversation_id || ev.detail?.conversation_id || null
  const actions = eventActions(ev, { running: run.running, hasChat: !!cid })
  const d = ev.detail && typeof ev.detail === 'object' ? ev.detail : {}
  const title = ev.kind === 'harness_fault' ? faultText(ev.summary, d.tool) : lineTitle(ev)
  const sub = head ? subjectsText(line) : ''
  const proc = programOf(ev)

  const run1 = {
    allow: () => guard(async () => {
      const q = baselineAsk({ ...d, exe: proc.exe, unit: proc.unit }, 'program')
      if (!await ask.confirm(q.title, { body: q.body, confirmLabel: q.confirm })) return
      const r = await post(`/api/security/events/${ev.id}/baseline`, { scope: 'program' })
      notify(`Allowed. ${r.acknowledged} alert${r.acknowledged === 1 ? '' : 's'} cleared.`, { life: 8 })
      changed()
    }),
    board: () => onOpenBoard(ev),
    revert: () => guard(async () => {
      if (!await ask.confirm(`Put ${d.path} back?`, {
        body: 'It goes back to the copy git HEAD holds (or is deleted if the agent created it). '
          + 'Edits made since the last commit are lost. It is refused if the file changed '
          + 'after this alert.', confirmLabel: 'Revert file', danger: true })) return
      const r = await post(`/api/security/events/${ev.id}/revert`)
      notify(`${r.path}: ${r.action}.`, { life: 8 })
      changed()
    }),
    uncut: () => guard(async () => {
      if (!await ask.confirm(`Un-cut ${d.host}?`, {
        body: 'The block is lifted and the host is not re-judged for an hour. '
          + 'Look at the evidence first: an anomaly cut is a sign something may be wrong.',
        confirmLabel: 'Un-cut host' })) return
      await post(`/api/security/events/${ev.id}/uncut`)
      notify(`${d.host} is let through again.`, { life: 8 })
      changed()
    }),
    stop: () => guard(async () => {
      if (!await ask.confirm('Stop this agent?', {
        body: 'Just the agent that did this, not the rest of its run.',
        confirmLabel: 'Stop agent', danger: true })) return
      const r = await post(`/api/security/events/${ev.id}/stop`, { scope: 'agent' })
      notify(r.message || 'Stopped.', { life: 8 })
      changed()
    }),
    kill: () => guard(async () => {
      if (!await ask.confirm(`Stop ${proc?.name || 'this process'} (pid ${d.pid})?`, {
        body: 'It is only stopped if it still is the process the box reported. '
          + 'A pid that was reused is left alone.',
        confirmLabel: 'Kill process', danger: true })) return
      await post(`/api/security/events/${ev.id}/kill`)
      notify('Process stopped.', { life: 8 })
      changed()
    }),
    ack: () => guard(async () => {
      await post(`/api/security/events/${ev.id}/ack`)
      changed()
    }),
  }
  const mute = async (mode) => guard(async () => {
    await api('/api/notifications/settings', {
      method: 'PUT', body: JSON.stringify({ kinds: { [ev.kind]: mode } }) })
    notify(`${ev.kind}: ${mode === 'record' ? 'record only' : 'badge only'} from now on.`, { life: 8 })
    changed()
  })

  return (
    <div className="sq-ev">
      <div className="sq-ev-top">
        <State tone={st.tone} why={st.why || undefined}>{st.word}</State>
        {ev.actor === 'operator' && <span className="sq-ev-n">by you</span>}
      </div>
      <div className="sq-ev-top">
        <span className="sq-ev-title"><Ticks text={title} /></span>
        {head && line.n > 1 && <span className="sq-ev-n">{line.n} of this kind</span>}
        {ev.count > 1 && <span className="sq-ev-n" title="repeated while it waited">{ev.count} times</span>}
      </div>
      {sub && <div className="mono sq-ev-subject">{sub}</div>}
      {head && ev.kind === 'harness_fault' && <p className="sq-change">{FAULTS_LEDE}</p>}
      {!head && d.path && <div className="mono sq-ev-subject">{d.path}</div>}
      {ev.doing && <Doing doing={ev.doing} />}
      <div className="sq-ev-actions">
        {actions.map((a) => {
          if (a.id === 'chat') {
            return (
              <Link key="chat" className="ghost-link" to={chatHref(cid, ev.doing?.step?.id)}
                    title={a.tip}>{a.label}</Link>)
          }
          if (a.id === 'mute') {
            return (
              <details key="mute" className="sq-mute">
                <summary className="ghost-link" title={a.tip}>{a.label}</summary>
                <div className="sq-mute-menu">
                  {MUTE_MODES.map((m) => (
                    <button key={m.mode} type="button" className="ghost" disabled={busy}
                            title={m.tip} onClick={() => mute(m.mode)}>{m.label}</button>))}
                </div>
              </details>)
          }
          return (
            <Button key={a.id} variant="ghost" className={a.id === 'ack' ? 'sq-ack' : ''}
                    disabled={busy} title={a.tip} onClick={run1[a.id]}>{a.label}</Button>)
        })}
      </div>
    </div>
  )
}

// The step and what the agent said it was doing, labelled as the agent's
function Doing({ doing }) {
  const step = doing.step
  const says = doing.says
  if (!step && !says) return null
  return (
    <div className="sq-doing">
      <div className="sq-doing-h">What the agent was doing <em>({UNTRUSTED})</em></div>
      {step && (
        <div className="mono">
          step #{step.id} {step.tool}{step.command ? `: ${step.command}` : ''}
          {step.match === 'nearest' && (
            <span className="sq-doing-h"> (nearest earlier step: the event names none)</span>)}
        </div>)}
      {says && (
        <div>
          {says.source === 'narration' ? 'It said' : says.source === 'todo' ? 'Its todo list says'
            : 'Its chat is titled'}: “{says.text}”</div>)}
    </div>
  )
}
